"""THE QUEUE B1/B2 — build one compiled `Plan` on OCCT and on a real CATIA seat.

`app/kernel/conformance.py::compare_backends` has been backend-neutral since
2026-09-05 and **its right-hand side has never been a real seat**, so Decision 1's
central claim — that a design built on the open kernel and on CATIA is the same
design — has been unverified rather than wrong. `app/catia/runner.py` is the
adapter that was missing. This is the driver, and it exists as a script because
of a constraint that is easy to hit and costs an hour to rediscover:

**The harness has to *be* the server process.** `app.catia.connection.registry`
is documented as *"Which devices are online, in this process"* — a module-level
singleton, deliberately, because the bridge daemon holds a WebSocket to one
worker and only that worker can talk to it. `local_bridge`'s `_process` and
`_process_user_id` are module globals for the same reason. So a plain script that
imports `call_catia` can never see the running server's bridge: it spawns a
*second* daemon, which dies on `bridge.lock`, and reports "the bridge exited
immediately after starting" — which is true, and not the useful truth. Measured
on 2026-09-11 while writing this.

There is no HTTP route that runs an arbitrary CATIA tool, and there should not be
one: that would be a remote-execution surface on somebody's workstation. So the
answer is to run the application *here*, in this process, let the daemon connect
to it, and drive `compare_backends` directly. Nothing is added to the product.

**Stop any other backend first.** One daemon per machine (`bridge.lock`), and one
listener per port.

    venv\\Scripts\\python -m scripts.catia_conformance
    venv\\Scripts\\python -m scripts.catia_conformance --keep-open
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

ADMIN_EMAIL = "admin@admin.com"

#: The quantities `measurement.compare` looks at, so "absent on one side" can be
#: told apart from "present and different" in the report.
_COMPARED = (
    "volume_mm3", "surface_area_mm2", "mass_kg", "face_count", "edge_count",
    "solid_count", "has_solid", "centre_of_mass_mm", "center_of_gravity_mm",
    "bounding_box_mm",
)

#: How long to wait for the daemon to spawn, find CATIA and connect back. CATIA's
#: COM surface takes ~2.5 min from a cold CNEXT; against a running one it is
#: seconds, and this is the running-one case because the operator started it.
BRIDGE_WAIT_S = 180.0

#: The rungs E3's phase proof names ("through M4").
LADDER_RUNGS = ("M1", "M2", "M3", "M4")


def _plans() -> list[tuple[str, Any]]:
    """Plans in increasing order of what they can disagree about.

    Start at sketch/rectangle/pad: if *that* diverges, nothing more elaborate is
    worth running, and if it agrees the next plan adds exactly one operation. A
    conformance run that opens with a mission produces one useless bit.
    """
    from app.design import DesignSpec, FeatureSpec, compile_spec, ref

    plate = DesignSpec.of(
        "Plate",
        material="steel-1018",
        features=[
            FeatureSpec("p.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "p.outline",
                "catia_sketch_rectangle",
                {"sketch": ref("p.profile"), "width_mm": 30.0, "height_mm": 20.0},
            ),
            FeatureSpec("p.body", "catia_pad", {"sketch": ref("p.profile"), "length_mm": 5.0}),
        ],
    )
    bored = DesignSpec.of(
        "BoredPlate",
        material="steel-1018",
        features=[
            FeatureSpec("p.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "p.outline",
                "catia_sketch_rectangle",
                {"sketch": ref("p.profile"), "width_mm": 60.0, "height_mm": 40.0},
            ),
            FeatureSpec("p.body", "catia_pad", {"sketch": ref("p.profile"), "length_mm": 10.0}),
            FeatureSpec("p.bore", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "p.hole", "catia_sketch_circle", {"sketch": ref("p.bore"), "diameter_mm": 12.0}
            ),
            FeatureSpec("p.cut", "catia_pocket", {"sketch": ref("p.bore"), "depth_mm": 10.0}),
        ],
    )
    return [("plate", compile_spec(plate)), ("bored plate", compile_spec(bored))]


def ladder_plans() -> tuple[list[tuple[str, Any]], dict[str, str]]:
    """E3's phase proof: every part the mission ladder builds through M4, and what is skipped.

    "Every assertion in the ladder through M4 is measurable, and each measurement agrees
    between OCCT and CATIA." B2 answered that for two plates. This hands the seat the ladder's
    own parts: M1's bracket and each component of M2's frame, compiled from the same
    `DesignSpec` the offline ladder builds. A rung that cannot reach a seat is returned in the
    second value **with its reason**, never dropped: a skipped rung that nobody names reads as
    a rung that agreed.
    """
    from app.design import compile_spec
    from app.design.missions import LADDER

    plans: list[tuple[str, Any]] = []
    skipped: dict[str, str] = {}
    for mission in LADDER:
        if mission.rung not in LADDER_RUNGS:
            continue
        if mission.spec is not None:
            plans.append((f"{mission.rung} {mission.title}", compile_spec(mission.spec)))
        elif mission.assembly is not None:
            for component, spec in mission.assembly.parts.items():
                plans.append((f"{mission.rung} {component}", compile_spec(spec)))
        elif mission.folded is not None:
            skipped[mission.rung] = (
                "a folded sheet: no sheet-metal operation exists in the CATIA registry, "
                "deliberately, until THE QUEUE E1 writes its COM half on a seat"
            )
        else:
            skipped[mission.rung] = (
                "no geometry yet; the rung needs " + "; ".join(mission.needs)
                if mission.needs
                else "no geometry yet"
            )
    return plans, skipped


def _serve_in_this_process(port: int) -> threading.Thread:
    """Run the app here, so the bridge's socket lands in this process."""
    import uvicorn

    from app.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="kryova-app", daemon=True)
    thread.start()
    return thread


def _wait_for_bridge(db: Any, user_id: str, *, deadline_s: float) -> bool:
    from app.catia import local_bridge

    started = time.monotonic()
    last = ""
    while time.monotonic() - started < deadline_s:
        if local_bridge.ensure_started(db, user_id, wait_s=5.0):
            return True
        why = local_bridge.last_error(user_id) or "(no reason recorded)"
        if why != last:
            print(f"   waiting for the bridge: {why}")
            last = why
        time.sleep(3.0)
    return False



def _measured_divergences(occt: Any, seat: Any) -> dict[str, Any]:
    """Ask both backends to *measure* the part they just built, and compare that.

    **`compare_backends` compares `last_result()`, and that is not enough against
    a real seat.** Measured 2026-09-11, the first time its right-hand side was
    one: OCCT's mutating operations return a full post-state
    (`volume_mm3`, `surface_area_mm2`, `mass_kg`, counts, centre of mass,
    bounding box, provenance) and the bridge's `catia_set_material` returns
    `{feature, was}`. Every compared key was therefore "present on one side only",
    which `measurement.compare` correctly calls a divergence — so the harness
    reported nine disagreements on two parts that may well be identical. The
    assumption was invisible while both sides were `OcctRunner`.

    `catia_measure` is implemented by both backends and is what B2 actually asks
    about ("every interrogated quantity must agree"), so the comparison is made
    on that rather than on whatever the plan's last call happened to return.
    """
    from app.kernel.measurement import SEAT_TOLERANCE_MM3, compare

    def measure(runner: Any) -> dict[str, Any]:
        try:
            got = dict(runner("catia_measure", {}))
        except Exception as exc:  # noqa: BLE001 - a refusal is a result here
            return {"error": f"{type(exc).__name__}: {exc}"}
        # The kernel answers flat; the bridge wraps its numbers in `measurements`.
        inner = got.get("measurements")
        return dict(inner) if isinstance(inner, dict) else got

    left, right = measure(occt), measure(seat)
    if "error" in left or "error" in right:
        return {"occt": left, "catia": right, "divergences": ["could not measure"],
                "deltas": {}, "absent": {}}
    # `SEAT_TOLERANCE_MM3`, not the default: CATIA prints four decimal places, and
    # 1e-6 measures its rounding rather than its modelling. See the constant.
    # **The deltas are reported, not just the verdict.** One `tolerance` covers
    # volume (mm3), area (mm2) and mass (kg) in `compare`, which was harmless at
    # 1e-6 and is not at 1e-3: a mass slack of a gram would swallow the 0.13%
    # difference that CATIA's catalogue density genuinely produces (7860 kg/m3 for
    # Acier against the kernel's 7870 for steel-1018, a deliberate choice recorded
    # in `scripts/catia_bridge/catia_com.py` so Kryova's mass matches the CATPart's
    # own). A pass that hides a known difference is worse than a fail, so every
    # compared number is printed with its delta and the reader can see which is
    # rounding and which is physics.
    deltas: dict[str, Any] = {}
    for key in ("volume_mm3", "surface_area_mm2", "mass_kg"):
        a, b = left.get(key), right.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            deltas[key] = {
                "occt": a,
                "catia": b,
                "abs": abs(a - b),
                "rel": (abs(a - b) / abs(a)) if a else None,
            }
    absent = {
        "not reported by catia": sorted(k for k in left if k not in right and k in _COMPARED),
        "not reported by occt": sorted(k for k in right if k not in left and k in _COMPARED),
    }
    return {
        "occt": left,
        "catia": right,
        "divergences": compare(left, right, tolerance=SEAT_TOLERANCE_MM3),
        "deltas": deltas,
        "absent": absent,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="leave the CATIA documents open afterwards, to look at them",
    )
    parser.add_argument("--out", default="", help="write the result as JSON to this path")
    parser.add_argument(
        "--ladder",
        action="store_true",
        help="also build every part of missions M1-M4 on both backends (E3's phase proof)",
    )
    args = parser.parse_args(argv)

    from app.catia.runner import CatiaSeatRunner
    from app.core.database import SessionLocal
    from app.kernel import OcctRunner, compare_backends
    from app.models.conversation import Conversation
    from app.models.user import User

    print(f"serving the application in this process on :{args.port}")
    _serve_in_this_process(args.port)
    time.sleep(4.0)

    results: list[dict[str, Any]] = []
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == ADMIN_EMAIL).one_or_none()
        if user is None:
            print(f"no {ADMIN_EMAIL}; run scripts/create_admin.py first")
            return 2
        print(f"user: {user.email}")

        if not _wait_for_bridge(db, user.id, deadline_s=BRIDGE_WAIT_S):
            print("the bridge never connected; nothing to compare against")
            return 3
        print("bridge connected\n")

        plans = _plans()
        if args.ladder:
            ladder, skipped = ladder_plans()
            plans = plans + ladder
            for rung, reason in skipped.items():
                print(f"skipped {rung}: {reason}")
                results.append({"label": rung, "skipped": reason})
        for label, plan in plans:
            convo = Conversation(id=str(uuid.uuid4()), owner_id=user.id, title=f"B1 {label}")
            db.add(convo)
            db.commit()

            seat = CatiaSeatRunner(db, user_id=user.id, conversation_id=convo.id)
            occt = OcctRunner()
            print(f"=== {label}: {len(plan)} calls, digest {plan.digest()[:12]}")
            result = compare_backends(plan, occt, seat)
            # B1 is "did both build it"; the summary's geometry verdict is not
            # usable on its own -- see `_measured_divergences`.
            print("   build: " + result.summary())
            for side, report in (("occt", result.left), ("catia", result.right)):
                if report is None:
                    print(f"   {side}: no report")
                    continue
                print(f"   {side}: ok={report.ok} calls={len(report)}")
                if report.ok:
                    last = report.last_result()
                    print("        " + json.dumps(
                        {k: last.get(k) for k in
                         ("volume_mm3", "surface_area_mm2", "mass_kg",
                          "face_count", "edge_count", "solid_count")},
                        default=str,
                    ))
                    # What the seat actually reports matters as much as whether
                    # it agrees: a key absent on one side is a divergence by
                    # `measurement.compare`'s own rule, and telling that apart
                    # from a real geometric difference needs the key list.
                    print(f"        keys: {sorted(last)}")
                elif report.failure is not None:
                    print(f"        failure: {str(report.failure)[:300]}")
            record = result.to_dict()
            if result.left is not None and result.left.ok and result.right is not None                     and result.right.ok:
                measured = _measured_divergences(occt, seat)
                record["measured"] = measured
                if measured["divergences"]:
                    print(f"   B2 MEASURED: they DISAGREE on {measured['divergences']}")
                else:
                    print("   B2 MEASURED: every interrogated quantity agrees")
                for key, d in measured["deltas"].items():
                    rel = f"{d['rel']:.3%}" if d["rel"] is not None else "n/a"
                    print(f"        {key:18s} occt={d['occt']!r:24s} catia={d['catia']!r:16s} "
                          f"abs={d['abs']:.6g} rel={rel}")
                for why, keys in measured["absent"].items():
                    if keys:
                        print(f"        {why}: {keys}")
            results.append(record)
            print()
            sys.stdout.flush()

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.out}")
    if args.keep_open:
        print("leaving CATIA as it is (--keep-open)")
    return 0 if all(r.get("agrees") for r in results if "skipped" not in r) else 1


if __name__ == "__main__":
    raise SystemExit(main())
