"""Landing a design in CATIA, and saying whether what landed is what was iterated on.

ROAD_TO_10 5.6, and Decision 1 made into a flow: the agent designs on the open kernel because
a design loop needs tens of rebuilds a minute and a seat gives one every few seconds, and the
result **lands** in CATIA, where the customer works. Everything below that sentence existed --
the compiled `Plan`, `OcctRunner`, `CatiaSeatRunner`, the conformance comparator -- and nothing
joined them into something a person could ask for.

**The plan is the source of truth, not the part.** The open kernel's live document is
in-memory state pinned to one worker and is never read back; what is replayed is the compiled
plan of the recorded design, which is what `build_design` builds. So landing needs a recorded
design, and a part built tool by tool with no design behind it has nothing to land (the route
says so rather than guessing a plan from a journal).

**It rebuilds on the open kernel first, in a fresh document of its own.** That is the "before"
of the comparison, taken the same way and at the same moment as the "after", so the two are
never a stale measurement against a fresh one, and a design the kernel cannot build is
refused *before* the seat is touched.

**It lands in a conversation of its own** (`landing_conversation_id`), never the one that was
iterated in. `CatiaDocument` binds a conversation to one active document, and putting a seat
document under a conversation whose part is a kernel session would have the two overwrite each
other's binding. A landed part is a deliverable, and a conversation named for it is where its
checkpoints and its rollback live.

**What is compared, and what deliberately is not.** Volume, surface area, centre of mass, the
bounding box and the face count, each as its own finding with three outcomes -- *agrees*,
*differs*, *unmeasured* (the other side did not report it) -- because "the seat said nothing
about faces" and "the seat counted different faces" are different facts with different fixes.
**Edge counts are never compared**: OCCT carries a seam edge on every closed cylindrical face
and CATIA does not, so the same bored plate is 15 edges against 14, and a number adjusted to
agree would hide a real divergence the day one happened. `solid_count` has no CATIA equivalent
at all. **Mass is reported and does not count**: the two sides hold different densities for
"steel" (measured 2026-09-12: 7860 against 7870 kg/m3, 0.127%), so a mass difference with an
agreeing volume is the material, not the geometry, and says so.

Nothing here runs on a seat that has not been measured: the comparison is exercised against the
real daemon in mock mode, and a real seat is THE QUEUE G8.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy.orm import Session

from app.catia import dispatch
from app.catia.dispatch import CatiaUnavailable
from app.catia.runner import CatiaSeatRunner
from app.design.compile import Plan
from app.design.execute import BuildReport, CallRunner, execute_plan
from app.geometry import backends
from app.kernel.measurement import (
    BOUNDING_BOX_MM,
    CENTRE_OF_MASS_MM,
    MASS_KG,
    SEAT_TOLERANCE_MM3,
    SURFACE_AREA_MM2,
    VOLUME_MM3,
    centre_of_mass,
    compare,
)
from app.models.conversation import Conversation

#: What is read and never compared, each with its reason, so a reader asked "why no edges?"
#: gets the answer from the report and not from this file.
NOT_COMPARED: Final[Mapping[str, str]] = {
    "edge_count": (
        "OCCT carries a seam edge on every closed cylindrical face and CATIA does not, so "
        "the same part counts one edge more per bore on the open kernel. Not reconciled: a "
        "number adjusted to agree would hide a real divergence."
    ),
    "solid_count": "CATIA has no equivalent of a solid count; the bridge does not report one.",
}

AGREES: Final = "agrees"
DIFFERS: Final = "differs"
UNMEASURED: Final = "unmeasured"

_FACE_COUNT: Final = "face_count"
_BOX: Final = "bounding_box_size_mm"


@dataclass(frozen=True)
class Finding:
    """One quantity, as the open kernel and CATIA each reported it."""

    quantity: str
    occt: Any
    catia: Any
    verdict: str
    #: Whether this decides `Landing.agrees`. Mass does not: see the module docstring.
    counts: bool = True
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "quantity": self.quantity,
            "occt": self.occt,
            "catia": self.catia,
            "verdict": self.verdict,
            "counts": self.counts,
        }
        if self.note:
            out["note"] = self.note
        return out


@dataclass(frozen=True)
class Landing:
    """What sending a design to CATIA did."""

    design: str
    plan_digest: str
    #: The whole plan ran on the seat. False means the part there is partial or absent.
    landed: bool
    #: Which side stopped, when one did: "occt", "catia" or "catia-unavailable".
    stopped_on: str | None = None
    reason: str | None = None
    findings: tuple[Finding, ...] = ()
    landing_conversation_id: str | None = None
    calls_on_seat: int = 0
    not_compared: Mapping[str, str] = field(default_factory=lambda: dict(NOT_COMPARED))

    @property
    def differing(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.counts and f.verdict == DIFFERS)

    @property
    def agrees(self) -> bool:
        """Landed, at least one quantity was measured on both sides, and none that counts differs."""
        measured = any(f.counts and f.verdict == AGREES for f in self.findings)
        return self.landed and measured and not self.differing

    def summary(self) -> str:
        if self.stopped_on == "occt":
            return f"{self.design}: nothing was sent — the open kernel could not build it. {self.reason}"
        if self.stopped_on == "catia-unavailable":
            return f"{self.design}: nothing was sent — {self.reason}"
        if not self.landed:
            return f"{self.design}: the build stopped on the seat, so the part there is incomplete. {self.reason}"
        if self.differing:
            names = ", ".join(f.quantity for f in self.differing)
            return f"{self.design} landed in CATIA and the two builds DISAGREE on {names}."
        if not self.agrees:
            return (
                f"{self.design} landed in CATIA, but nothing could be measured on both sides, "
                "so it is not known to match."
            )
        unmeasured = [f.quantity for f in self.findings if f.counts and f.verdict == UNMEASURED]
        tail = f" Not reported by CATIA: {', '.join(unmeasured)}." if unmeasured else ""
        return f"{self.design} landed in CATIA and matches the open-kernel build on what was measured.{tail}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "design": self.design,
            "plan_digest": self.plan_digest,
            "landed": self.landed,
            "agrees": self.agrees,
            "stopped_on": self.stopped_on,
            "reason": self.reason,
            "summary": self.summary(),
            "findings": [finding.to_dict() for finding in self.findings],
            "not_compared": dict(self.not_compared),
            "landing_conversation_id": self.landing_conversation_id,
            "calls_on_seat": self.calls_on_seat,
        }


# -- the comparison -------------------------------------------------------------------------


def _box_size(payload: Mapping[str, Any]) -> Sequence[float] | None:
    box = payload.get(BOUNDING_BOX_MM)
    size = box.get("size") if isinstance(box, Mapping) else None
    return size if isinstance(size, Sequence) and not isinstance(size, str) else None


def _pair(
    quantity: str,
    occt: Any,
    catia: Any,
    differs: bool,
    *,
    counts: bool = True,
    note: str | None = None,
) -> Finding:
    if occt is None or catia is None:
        side = "CATIA" if catia is None else "the open kernel"
        return Finding(quantity, occt, catia, UNMEASURED, counts, note or f"{side} did not report it.")
    return Finding(quantity, occt, catia, DIFFERS if differs else AGREES, counts, note)


def _differs(key: str, a: Any, b: Any, tolerance: float) -> bool:
    """Whether the shared comparator calls `key` a disagreement; False when a side is missing.

    A missing side is the *unmeasured* outcome and is decided before this is asked, so the
    comparator's "absent on one side is a disagreement" never reaches a finding.
    """
    if a is None or b is None:
        return False
    return bool(compare({key: a}, {key: b}, tolerance=tolerance))


def compare_measurements(
    occt: Mapping[str, Any], catia: Mapping[str, Any], *, tolerance: float = SEAT_TOLERANCE_MM3
) -> tuple[Finding, ...]:
    """Each quantity of two `catia_measure` payloads as a finding.

    Built on `measurement.compare` so the tolerance rules, the alias for the centre of mass and
    the vector comparison are the conformance harness's and cannot drift from it; what is added
    is the third outcome, which that function's "absent on one side is a disagreement" cannot
    express.
    """
    findings: list[Finding] = []

    for key in (VOLUME_MM3, SURFACE_AREA_MM2):
        a, b = occt.get(key), catia.get(key)
        findings.append(_pair(key, a, b, _differs(key, a, b, tolerance)))

    a, b = centre_of_mass(occt), centre_of_mass(catia)
    findings.append(_pair(CENTRE_OF_MASS_MM, a, b, _differs(CENTRE_OF_MASS_MM, a, b, tolerance)))

    a, b = _box_size(occt), _box_size(catia)
    differs = a is not None and b is not None and bool(
        compare({BOUNDING_BOX_MM: {"size": a}}, {BOUNDING_BOX_MM: {"size": b}}, tolerance=tolerance)
    )
    findings.append(
        _pair(_BOX, list(a) if a is not None else None, list(b) if b is not None else None, differs)
    )

    a, b = occt.get(_FACE_COUNT), catia.get(_FACE_COUNT)
    findings.append(_pair(_FACE_COUNT, a, b, a is not None and b is not None and a != b))

    findings.append(_mass_finding(occt, catia, findings))
    return tuple(findings)


def _mass_finding(
    occt: Mapping[str, Any], catia: Mapping[str, Any], others: Sequence[Finding]
) -> Finding:
    a, b = occt.get(MASS_KG), catia.get(MASS_KG)
    if a is None or b is None:
        return _pair(MASS_KG, a, b, False, counts=False)
    relative = abs(float(a) - float(b)) / max(abs(float(a)), abs(float(b)), 1e-30)
    if relative <= 1e-6:
        return Finding(MASS_KG, a, b, AGREES, counts=False)
    volume = next((f for f in others if f.quantity == VOLUME_MM3), None)
    if volume is not None and volume.verdict == AGREES:
        note = (
            f"The masses differ by {relative * 100:.3f}% with the volumes agreeing, so the two "
            "sides hold different densities for the material; the geometry is not what differs."
        )
    else:
        note = f"The masses differ by {relative * 100:.3f}%."
    return Finding(MASS_KG, a, b, DIFFERS, counts=False, note=note)


# -- the flow -------------------------------------------------------------------------------


class _WatchesTheSeat:
    """A runner that remembers *why* it stopped when the seat could not be reached.

    `execute_plan` turns every exception into a `BuildFailure` carrying only a message, which
    is right for a build and loses the one distinction this flow needs: "no workstation is
    online" is not "CATIA refused the pad", and the second leaves a partial part behind.
    """

    def __init__(self, runner: CallRunner) -> None:
        self._runner = runner
        self.unavailable: str | None = None

    def __call__(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            return self._runner(tool, arguments)
        except CatiaUnavailable as exc:
            self.unavailable = str(exc)
            raise


def _failure_text(report: BuildReport) -> str:
    return str(report.failure) if report.failure is not None else ""


def land_in_catia(
    db: Session,
    *,
    user_id: str,
    plan: Plan,
    source_conversation: Conversation,
    occt_runner: CallRunner | None = None,
    seat_runner: CallRunner | None = None,
    tolerance: float = SEAT_TOLERANCE_MM3,
) -> Landing:
    """Replay `plan` on the open kernel, then on the seat, and compare what each made.

    `occt_runner` and `seat_runner` are injectable for the reason `compare_backends` takes
    runners: a test can put anything on either side. In production the first is a fresh
    `OcctRunner` and the second is bound to a new landing conversation.
    """
    if occt_runner is None:
        from app.kernel.occt.runner import OcctRunner

        occt_runner = OcctRunner()

    before = execute_plan(plan, occt_runner)
    if not before.ok:
        return Landing(
            design=plan.design,
            plan_digest=plan.digest(),
            landed=False,
            stopped_on="occt",
            reason=_failure_text(before),
        )

    landing_conversation = None
    if seat_runner is None:
        # Asked before a conversation is created, so "no workstation is online" leaves nothing
        # behind. It may start this machine's own daemon, which is what the user asked for.
        try:
            dispatch._resolve_connection(db, user_id, None)
        except CatiaUnavailable as exc:
            return Landing(
                design=plan.design,
                plan_digest=plan.digest(),
                landed=False,
                stopped_on="catia-unavailable",
                reason=str(exc),
            )
        landing_conversation = Conversation(
            owner_id=user_id,
            project_id=source_conversation.project_id,
            title=f"{plan.design} — landed in CATIA"[:255],
        )
        db.add(landing_conversation)
        db.flush()
        seat_runner = CatiaSeatRunner(db, user_id=user_id, conversation_id=landing_conversation.id)
    watched = _WatchesTheSeat(seat_runner)

    # The seat's runner goes through `call_catia`, which chooses a kernel from the deployment's
    # setting. This is the one place that must use the seat whatever that setting says.
    with backends.use_backend("catia"):
        after = execute_plan(plan, watched)
        measured = _measure(watched) if after.ok else None

    landing_id = landing_conversation.id if landing_conversation is not None else None
    calls = len(after)
    if watched.unavailable is not None:
        return Landing(
            design=plan.design,
            plan_digest=plan.digest(),
            landed=False,
            stopped_on="catia-unavailable",
            reason=watched.unavailable,
            landing_conversation_id=landing_id,
            calls_on_seat=calls,
        )
    if not after.ok:
        return Landing(
            design=plan.design,
            plan_digest=plan.digest(),
            landed=False,
            stopped_on="catia",
            reason=_failure_text(after),
            landing_conversation_id=landing_id,
            calls_on_seat=calls,
        )

    occt_measure = _measure(occt_runner)
    findings = compare_measurements(occt_measure or {}, measured or {}, tolerance=tolerance)
    return Landing(
        design=plan.design,
        plan_digest=plan.digest(),
        landed=True,
        findings=findings,
        landing_conversation_id=landing_id,
        calls_on_seat=calls,
    )


def _measure(runner: CallRunner) -> Mapping[str, Any] | None:
    """The finished part's measurement, or None when the backend would not give one.

    An explicit `catia_measure` rather than the last build call's payload: that payload is
    whatever the final feature happened to report, and a comparison should not depend on
    which feature came last.
    """
    try:
        return runner("catia_measure", {})
    except Exception:  # noqa: BLE001 - an unmeasurable part is "unmeasured", never a pass
        return None


__all__ = [
    "AGREES",
    "DIFFERS",
    "NOT_COMPARED",
    "UNMEASURED",
    "Finding",
    "Landing",
    "compare_measurements",
    "land_in_catia",
]
