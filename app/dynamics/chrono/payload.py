"""The wire between this process and the container: a `Mechanism` in, a result out.

Plain JSON, because the boundary is a file and the other side is a different Python with
a different interpreter, different packages and no access to `app`. Nothing pickled:
a pickle across that boundary would couple the container's Python version to this one and
would execute whatever the file said.

**Units cross here and convert exactly once.** The whole codebase is mm-N-MPa
(`CLAUDE.md`, non-negotiable), and Chrono is metres-newtons-kilograms. So the payload
carries **millimetres** — the units this side speaks — and `_entrypoint.py` divides by
1000 on the way in and multiplies on the way out, in one place each. Converting here as
well would convert twice; converting in neither would put metres in a `Mechanism` and
every downstream load would be 1000× wrong while looking entirely plausible.

**A body with no inertia tensor is a point mass and says so.** `Body.inertia_kg_mm2` is
optional and is **sent when the caller gave one** — an early draft of this module hard-
coded `has_inertia_tensor: False` and silently discarded a tensor an engineer had typed,
which is the same class of defect as an argument the schema advertises and nothing reads.
What has no tensor is `app.assembly.mass.roll_up` (E9.2's status records it), so a
mechanism assembled from a product structure arrives here as point masses. A rigid body
with *zero* inertia is not a point mass to an integrator — it is a singular mass matrix —
so the entry point substitutes a negligible isotropic tensor and the result carries the
caveat through to `MechanismResult.warnings`. Inventing a *plausible* tensor is the
failure mode this package spends its docstrings avoiding; a negligible one that is
declared in the warnings is not the same thing.

**Inertia converts here, and it is the one quantity that does.** Kryova states a tensor in
**kg·mm²** (`types.Body`) and Chrono wants **kg·m²**, a factor of 1e-6 — while lengths
convert in `_entrypoint.py`. That looks like a violation of "convert exactly once at a
boundary" and is not: there is one conversion per quantity, and this is inertia's. It is
here rather than there because `INERTIA_KG_MM2_TO_KG_M2` is the kind of constant that
wants to sit beside the type that names its unit.
"""

from __future__ import annotations

from typing import Any

from app.dynamics.errors import MechanismError
from app.dynamics.pose import Frame
from app.dynamics.types import (
    Body,
    BodyMotion,
    JointReaction,
    Mechanism,
    MechanismResult,
    MotionPath,
    MotionRange,
)

#: The shape version. Bumped when the payload or the result changes meaning, so an image
#: built against an older entry point refuses rather than mis-reading a field.
WIRE_VERSION = 1

#: kg.mm2 (Kryova, `types.Body.inertia_kg_mm2`) to kg.m2 (Chrono). See the module
#: docstring for why this one conversion lives here and the lengths live in the entry
#: point: one conversion per quantity, each beside the type that names its unit.
INERTIA_KG_MM2_TO_KG_M2 = 1e-6

#: Chrono's own joint names for the kinds this vocabulary has. `spherical` is absent
#: deliberately, exactly as it is absent from the kinematic evaluator: a three-degree-of-
#: freedom joint cannot be prescribed by one driver, and it is refused by name rather than
#: silently treated as something else.
JOINT_KINDS: dict[str, str] = {
    "revolute": "revolute",
    "prismatic": "prismatic",
    "fixed": "lock",
}


def _body_payload(body: Body) -> dict[str, Any]:
    """One body on the wire, with its tensor if it has one.

    `has_inertia_tensor` is sent as its own field rather than left to be inferred from
    the presence of `inertia_kg_m2`, because the container has to *say* which it did in
    the warnings, and a reader that infers it would go quiet the day the key is spelled
    differently.
    """
    written: dict[str, Any] = {
        "name": body.name,
        "mass_kg": body.mass_kg,
        "centre_of_mass_mm": list(body.centre_of_mass_mm),
        "has_inertia_tensor": body.inertia_kg_mm2 is not None,
    }
    if body.inertia_kg_mm2 is not None:
        written["inertia_kg_m2"] = [
            value * INERTIA_KG_MM2_TO_KG_M2 for value in body.inertia_kg_mm2
        ]
    return written


def to_payload(mechanism: Mechanism, motion: MotionRange) -> dict[str, Any]:
    """A `Mechanism` and a range as the JSON the container reads."""
    unsupported = sorted(
        {joint.kind for joint in mechanism.joints if joint.kind not in JOINT_KINDS}
    )
    if unsupported:
        raise MechanismError(
            f"This mechanism uses {', '.join(unsupported)} joint(s), which Kryova's "
            "Chrono translation does not send. A spherical joint has three degrees of "
            "freedom and cannot be prescribed by one driver — split it into revolutes, "
            "or state the motion you mean."
        )

    return {
        "wire_version": WIRE_VERSION,
        "name": mechanism.name,
        "gravity_mm_s2": list(mechanism.gravity_mm_s2),
        "bodies": [_body_payload(body) for body in mechanism.bodies],
        "joints": [
            {
                "name": joint.name,
                "kind": JOINT_KINDS[joint.kind],
                "body": joint.body,
                "parent": joint.parent,
                "origin_mm": list(joint.origin_mm),
                "axis": list(joint.axis),
            }
            for joint in mechanism.joints
        ],
        "drivers": [
            {
                "joint": driver.joint,
                "kind": driver.kind,
                "offset": driver.offset,
                "rate": driver.rate,
                "amplitude": driver.amplitude,
                "frequency_hz": driver.frequency_hz,
                "phase_rad": driver.phase_rad,
                "times_s": list(driver.times_s),
                "values": list(driver.values),
            }
            for driver in mechanism.drivers
        ],
        "motion": {"duration_s": motion.duration_s, "samples": motion.samples},
    }


def _vec_list(raw: Any, field: str, expected: int) -> tuple[tuple[float, float, float], ...]:
    if not isinstance(raw, list) or len(raw) != expected:
        raise MechanismError(
            f"Chrono's result has {field} of the wrong length: expected {expected} "
            f"samples, got {len(raw) if isinstance(raw, list) else type(raw).__name__}."
        )
    out: list[tuple[float, float, float]] = []
    for item in raw:
        if not isinstance(item, list) or len(item) != 3:
            raise MechanismError(f"Chrono's result has a malformed vector in {field}.")
        out.append((float(item[0]), float(item[1]), float(item[2])))
    return tuple(out)


def _frames(raw: dict[str, Any], name: str, count: int) -> tuple[Frame, ...]:
    """A body's poses, with the identity rotation where the engine sent none.

    `Frame.rotation` is a row-major 9-tuple (`pose.Rot3`), not a quaternion, so the
    entry point converts on its side — one conversion, in the place that knows Chrono's
    convention, rather than a half-conversion here and another there.
    """
    origins = _vec_list(raw.get("frame_origin_mm"), f"{name}.frame_origin_mm", count)
    rotations = raw.get("frame_rotation")
    if not rotations:
        return tuple(Frame(origin_mm=origin) for origin in origins)
    if not isinstance(rotations, list) or len(rotations) != count:
        raise MechanismError(
            f"Chrono's result has {name}.frame_rotation of the wrong length: expected "
            f"{count}."
        )
    frames: list[Frame] = []
    for origin, rotation in zip(origins, rotations, strict=True):
        if not isinstance(rotation, list) or len(rotation) != 9:
            raise MechanismError(
                f"Chrono's result has a malformed rotation in {name}.frame_rotation: a "
                "rotation is nine numbers, row-major."
            )
        frames.append(
            Frame(
                rotation=tuple(float(v) for v in rotation),  # type: ignore[arg-type]
                origin_mm=origin,
            )
        )
    return tuple(frames)


def from_result(
    mechanism: Mechanism, written: dict[str, Any], *, engine: str
) -> MechanismResult:
    """Read the container's `output.json` into the vocabulary the rest of the app speaks.

    **Validated rather than trusted.** The file was written by another process that may be
    an older image, and a short array silently zipped against a long one would produce a
    motion path with the wrong times against the right-looking positions.
    """
    if written.get("wire_version") != WIRE_VERSION:
        raise MechanismError(
            f"This Chrono image speaks wire version {written.get('wire_version')!r} and "
            f"this build speaks {WIRE_VERSION}. Rebuild the image with "
            "`scripts/chrono_image.sh` so the entry point matches."
        )

    times = tuple(float(t) for t in written.get("times_s", []))
    if not times:
        raise MechanismError("Chrono's result carries no time samples.")

    bodies: dict[str, BodyMotion] = {}
    for name, raw in (written.get("bodies") or {}).items():
        count = len(times)
        bodies[name] = BodyMotion(
            body=name,
            times_s=times,
            frames=_frames(raw, name, count),
            position_mm=_vec_list(raw.get("position_mm"), f"{name}.position_mm", count),
            velocity_mm_s=_vec_list(raw.get("velocity_mm_s"), f"{name}.velocity_mm_s", count),
            acceleration_mm_s2=_vec_list(
                raw.get("acceleration_mm_s2"), f"{name}.acceleration_mm_s2", count
            ),
            angular_velocity_rad_s=_vec_list(
                raw.get("angular_velocity_rad_s"), f"{name}.angular_velocity_rad_s", count
            ),
            angular_acceleration_rad_s2=_vec_list(
                raw.get("angular_acceleration_rad_s2"),
                f"{name}.angular_acceleration_rad_s2",
                count,
            ),
        )

    reactions: dict[str, JointReaction] = {}
    for name, raw in (written.get("reactions") or {}).items():
        count = len(times)
        reactions[name] = JointReaction(
            joint=name,
            times_s=times,
            location_mm=_vec_list(raw.get("location_mm"), f"{name}.location_mm", count),
            force_n=_vec_list(raw.get("force_n"), f"{name}.force_n", count),
            moment_n_mm=_vec_list(raw.get("moment_n_mm"), f"{name}.moment_n_mm", count),
            moment_caveat=str(raw.get("moment_caveat", "")),
        )

    warnings = tuple(str(w) for w in written.get("warnings", ()))
    return MechanismResult(
        mechanism=mechanism.name,
        engine=engine,
        path=MotionPath(
            mechanism=mechanism.name,
            times_s=times,
            bodies=bodies,
            method=str(written.get("method", "chrono time integration")),
            warnings=warnings,
        ),
        reactions=reactions,
        warnings=warnings,
    )


__all__ = [
    "INERTIA_KG_MM2_TO_KG_M2",
    "JOINT_KINDS",
    "WIRE_VERSION",
    "from_result",
    "to_payload",
]
