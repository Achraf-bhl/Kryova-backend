"""Turning what the mechanism did into a load case the existing FEA vocabulary accepts.

**This is the point of Phase 9.** Today a `LoadCase` is something a person types: 5 kN on
that face. On a real machine the load on a bracket is whatever the mechanism *does* to
it -- peak inertia at the end of a stroke, the reaction at a bearing through a cycle. This
module is the step that produces the second kind, and it produces it as an
`app.solve.types.LoadCase` -- the same object a hand-entered case is -- so everything
downstream (the solver, the job runner, fatigue, the viewer) needs no change and cannot
tell the difference.

**Nothing new was added to the load vocabulary, and that is the design.** Decision 2
names `app/solve/loads.py` and `app/solve/selection.py` as the real asset; a second
vocabulary beside them would fork the system. Every mechanism load turns out to already
have a spelling:

* a **joint reaction** is a force on a bore -- `BearingLoad` on a `CylinderSelector`,
  which already distributes a pin's push as the cosine bearing pressure it really is,
  or `ForceLoad` where the mount is a pad rather than a pin;
* a body's **inertia** is a uniform acceleration field, and `GravityLoad`'s own
  docstring already says it is "self-weight, *or any uniform acceleration of the whole
  body*". d'Alembert says the body force is `g - a`, so the inertial case is one
  `GravityLoad` whose direction and magnitude come from the instant chosen. A part in
  free fall correctly gets no body load at all, because `g - a` is zero;
* a **spin** is `CentrifugalLoad`, which is quadratic in rate exactly as it should be.

So the bridge is a mapping, not an extension. The one thing it must add is the
*geometric* half -- which bore on the part is which joint of the mechanism -- and that is
`JointMount`, because a mechanism knows about links and a mesh knows about cylinders and
somebody has to say which is which.

**The instant is chosen and recorded, never averaged.** A load case is one instant of a
cycle. Averaging a reaction over a revolution produces a number that occurs at no point
in the machine's life; the sizing question is "what is the worst it sees", so the default
is the peak of the summed mount forces and the index used is carried on the result.

**A case whose reaction could not be computed is refused, not zeroed.** A closed-loop
mechanism reports its reactions UNAVAILABLE; building a `LoadCase` from that would put a
zero force on a bracket and pass every check. `DerivedLoadCase` is not returned in that
situation at all -- a `MechanismError` names the joint and quotes the reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.dynamics.errors import MechanismError
from app.dynamics.pose import Vec3, norm, sub
from app.dynamics.types import MechanismResult
from app.solve.types import (
    BearingLoad,
    CylinderSelector,
    Fixture,
    ForceLoad,
    GravityLoad,
    Load,
    LoadCase,
    Material,
    Selector,
)

#: Below this the inertial field is dropped rather than given a made-up direction.
#: In mm/s2, so 1 is about 1e-4 g -- far below anything that sizes a part, and above the
#: rounding of a difference of two numbers of order 1e4.
_NEGLIGIBLE_FIELD_MM_S2 = 1.0


@dataclass(frozen=True)
class JointMount:
    """Which feature of the part carries which joint of the mechanism.

    The one thing the mechanism cannot know and the mesh cannot know: a mechanism has a
    joint called `crank_pin`, a mesh has a cylindrical bore at some radius about some
    axis, and only a person (or an agent that built both) can say they are the same
    thing. Everything else in this module is derivable.

    `where` is an ordinary `app.solve.types.Selector` -- the existing geometric selector
    vocabulary, by geometry and never by face id, exactly as the repository requires,
    so a mount survives a re-export of the part.

    `bearing` defaults to True because a joint is a pin in a hole far more often than it
    is a pad, and because the failure modes differ: a `ForceLoad` spread uniformly over a
    whole bore understates the peak stress at the contact, which is where a lug actually
    fails. It is refused on a selector that is not a cylinder, since a bearing pressure
    on a box has no bore to distribute around.
    """

    joint: str
    where: Selector
    bearing: bool = True
    name: str | None = None

    def __post_init__(self) -> None:
        if self.bearing and not isinstance(self.where, CylinderSelector):
            raise MechanismError(
                f"Mount for joint {self.joint!r} asks for a bearing distribution on a "
                f"{type(self.where).__name__}. A bearing load is a pin pressing round a "
                "bore, so it needs a CylinderSelector. Use a cylinder selector for the "
                "bore, or set bearing=False to apply the reaction as a plain force."
            )


@dataclass(frozen=True)
class DerivedLoadCase:
    """A load case, plus where in the machine's cycle it came from.

    The `LoadCase` alone would be enough to solve, and would be a worse artefact.
    Decision 3 binds a result to what produced it, and "4.2 kN at the crank pin" is a
    number somebody has to be able to argue with six months later -- which means knowing
    it was sample 22 of 37 through a 0.5 s cycle, from the kinematic engine, and that
    the body's own inertia was folded in as an 18 g field.
    """

    case: LoadCase
    mechanism: str
    engine: str
    instant_index: int
    instant_s: float

    #: The joints whose reactions became loads, and the force magnitude of each, in N.
    mount_forces_n: dict[str, float] = field(default_factory=dict)

    #: The uniform inertial field applied, in mm/s2, or None when none was.
    inertial_field_mm_s2: float | None = None

    #: Caveats a reviewer must see. Never empty when a spinning body was reduced to a
    #: uniform field, because that reduction is the one approximation this bridge makes.
    notes: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        return {
            "mechanism": self.mechanism,
            "engine": self.engine,
            "instant_index": self.instant_index,
            "instant_s": self.instant_s,
            "mount_forces_n": dict(self.mount_forces_n),
            "inertial_field_mm_s2": self.inertial_field_mm_s2,
            "notes": list(self.notes),
        }


def sizing_instant(result: MechanismResult, mounts: Sequence[JointMount]) -> int:
    """The sample index where the mounts together carry the most force.

    The *summed magnitude*, not the largest single mount, because two bearings peaking
    at opposite ends of a stroke is a real arrangement and sizing the part at either
    peak alone misses the case where both are loaded at once. Ties go to the earlier
    index so the choice is deterministic.
    """
    if not mounts:
        raise MechanismError(
            "A load case needs at least one mount -- the geometric feature the "
            "mechanism's reaction lands on. Give a JointMount naming the bore or pad."
        )
    reactions = [_available_reaction(result, mount) for mount in mounts]
    count = len(reactions[0].force_n)
    best_index, best_total = 0, -1.0
    for index in range(count):
        total = sum(norm(reaction.force_n[index]) for reaction in reactions)
        if total > best_total:
            best_index, best_total = index, total
    return best_index


def _available_reaction(result: MechanismResult, mount: JointMount) -> Any:
    reaction = result.reaction(mount.joint)
    if not reaction.available:
        raise MechanismError(
            f"The reaction at joint {mount.joint!r} was not computed, so no load case "
            f"can be built from it: {reaction.unavailable_reason} A load case built on "
            "an unknown reaction would put a zero force on the part and pass every "
            "check, which is why this refuses instead."
        )
    return reaction


def inertial_field(
    result: MechanismResult, body: str, index: int, gravity_mm_s2: Vec3
) -> tuple[Vec3, float]:
    """The uniform body-force field (direction, magnitude) at one instant, mm/s2.

    d'Alembert: a body accelerating at `a` in a gravity field `g` feels a uniform body
    force as if gravity were `g - a`. That is exactly what `GravityLoad` applies, so the
    inertial case needs no new load type -- see the module docstring.

    Gravity is an argument rather than something read off the result, and that is
    deliberate: gravity is a property of the *mechanism*, the result carries the motion,
    and defaulting one from the other is how a run done at zero g to isolate the inertia
    term comes back with 1 g quietly re-added.

    Direction is normalised and magnitude returned beside it, so a caller can decide
    what is negligible. A body in free fall gives magnitude zero, which is correct --
    which is why the two are not folded together.
    """
    motion = result.path.motion(body)
    field_vector = sub(gravity_mm_s2, motion.acceleration_mm_s2[index])
    magnitude = norm(field_vector)
    if magnitude <= 0.0:
        return ((0.0, 0.0, -1.0), 0.0)
    return (
        (
            field_vector[0] / magnitude,
            field_vector[1] / magnitude,
            field_vector[2] / magnitude,
        ),
        magnitude,
    )


def build(
    result: MechanismResult,
    *,
    mounts: Sequence[JointMount],
    material: Material,
    fixtures: Sequence[Fixture],
    gravity_mm_s2: Vec3,
    inertia_of: str | None = None,
    at: int | None = None,
    name: str | None = None,
) -> DerivedLoadCase:
    """A `LoadCase` for the part at one instant of the mechanism's cycle.

    `gravity_mm_s2` is passed explicitly rather than read off the result, and that is
    deliberate: the run's gravity is a property of the *mechanism*, the result carries
    the motion, and silently defaulting one from the other is how a case run at zero g
    to isolate inertia comes back with 1 g quietly re-added. Pass
    `mechanism.gravity_mm_s2`.

    `inertia_of` names the body whose own mass is being checked -- usually the part the
    mesh is of. Its acceleration at the chosen instant becomes the uniform d'Alembert
    field. Leave it None when the part is a ground fixture, whose only load is what the
    joints push into it.

    **The one approximation this function makes is named in `notes` when it applies.**
    A uniform field is the acceleration *of the centre of mass*; a body that is also
    rotating has an additional field varying as `omega^2 * r` across its own extent. The
    note says so, quantifies it over the part's own travel, and points at
    `CentrifugalLoad`. It is not silently absorbed, and it is not silently ignored.
    """
    if not fixtures:
        raise MechanismError(
            "A load case needs at least one fixture, or the static solve is singular. "
            "Hold the part where the machine holds it -- the mounting face, or the far "
            "bore -- with an app.solve.types.Fixture."
        )

    index = sizing_instant(result, mounts) if at is None else at
    times = result.path.times_s
    if not 0 <= index < len(times):
        raise MechanismError(
            f"Instant {index} is outside this run, which has {len(times)} samples "
            f"(0 to {len(times) - 1}). Pass at=None to use the peak-force instant."
        )

    loads: list[Load] = []
    mount_forces: dict[str, float] = {}
    notes: list[str] = []

    for mount in mounts:
        reaction = _available_reaction(result, mount)
        force = reaction.force_n[index]
        mount_forces[mount.joint] = norm(force)
        label = mount.name or f"{mount.joint} reaction"
        if mount.bearing:
            loads.append(
                BearingLoad(where=mount.where, force_n=force, name=label)
            )
        else:
            loads.append(ForceLoad(where=mount.where, force_n=force, name=label))
        if reaction.moment_caveat:
            notes.append(f"{mount.joint}: {reaction.moment_caveat}")

    field_magnitude: float | None = None
    if inertia_of is not None:
        motion = result.path.motion(inertia_of)
        vector = sub(gravity_mm_s2, motion.acceleration_mm_s2[index])
        magnitude = norm(vector)
        if magnitude > _NEGLIGIBLE_FIELD_MM_S2:
            field_magnitude = magnitude
            loads.append(
                GravityLoad(
                    direction=(
                        vector[0] / magnitude,
                        vector[1] / magnitude,
                        vector[2] / magnitude,
                    ),
                    magnitude_mm_s2=magnitude,
                    name=f"{inertia_of} inertia + weight (d'Alembert)",
                )
            )
        else:
            notes.append(
                f"{inertia_of} is in free fall at this instant (g - a is "
                f"{magnitude:.3g} mm/s2), so it carries no body load at all. That is "
                "correct, not a missing load."
            )
        spin = norm(motion.angular_velocity_rad_s[index])
        if spin > 0.0:
            notes.append(
                f"{inertia_of} is rotating at {spin:.4g} rad/s at this instant. The "
                "inertial field applied is the acceleration of its centre of mass, "
                "uniform over the part; the true field varies by omega^2*r across it, "
                f"which is {spin * spin:.4g} mm/s2 per mm from the spin axis. Add an "
                "app.solve.types.CentrifugalLoad if the spin term is not small against "
                f"{field_magnitude if field_magnitude is not None else 0.0:.4g} mm/s2."
            )

    if not loads:
        raise MechanismError(
            "Nothing loads this part at the chosen instant: every mount reaction is "
            "zero and no body inertia was requested. Check the mounts name the right "
            "joints, or pass inertia_of to include the part's own weight."
        )

    case = LoadCase(
        name=name
        or f"{result.mechanism} at t={times[index]:.4g}s (sample {index})",
        material=material,
        fixtures=list(fixtures),
        loads=loads,
    )

    return DerivedLoadCase(
        case=case,
        mechanism=result.mechanism,
        engine=result.engine,
        instant_index=index,
        instant_s=times[index],
        mount_forces_n=mount_forces,
        inertial_field_mm_s2=field_magnitude,
        notes=tuple(notes),
    )


def envelope(
    result: MechanismResult,
    *,
    mounts: Sequence[JointMount],
    material: Material,
    fixtures: Sequence[Fixture],
    gravity_mm_s2: Vec3,
    inertia_of: str | None = None,
    instants: Sequence[int] | None = None,
) -> tuple[DerivedLoadCase, ...]:
    """Several instants of one cycle, as several load cases.

    What fatigue needs and what a single peak case cannot give: damage accumulates over
    a cycle, and the worst *stress* is not always at the worst *force* once the load
    changes direction relative to the part. With no `instants`, every sampled instant is
    returned -- one case per sample, which is what a duty cycle is.

    Deliberately a tuple of cases rather than a new multi-instant type. Each one is an
    ordinary `LoadCase` that the ordinary solver solves, so a cycle costs N solves and
    no new machinery, and any of them can be pulled out and looked at on its own.
    """
    chosen = range(len(result.path.times_s)) if instants is None else instants
    return tuple(
        build(
            result,
            mounts=mounts,
            material=material,
            fixtures=fixtures,
            gravity_mm_s2=gravity_mm_s2,
            inertia_of=inertia_of,
            at=index,
        )
        for index in chosen
    )


def cylinder_mount(
    joint: str,
    *,
    axis_point: Vec3,
    axis_direction: Vec3,
    radius_mm: float,
    length_mm: float | None = None,
    bearing: bool = True,
) -> JointMount:
    """A bore, in the selector vocabulary, without spelling out the selector.

    A convenience with a purpose: `CylinderSelector` takes a radius *tolerance* as well
    as a radius, and a caller who does not know that reaches for a `BoxSelector` and
    catches the material around the hole. Nominal radius is what is on the drawing, so
    that is what this takes.
    """
    if radius_mm <= 0.0:
        raise MechanismError(
            f"A bore of radius {radius_mm} mm is not a bore. Give the nominal radius "
            "from the drawing -- 5 for a 10 mm hole."
        )
    return JointMount(
        joint=joint,
        where=CylinderSelector(
            axis_point=axis_point,
            axis_direction=axis_direction,
            radius=radius_mm,
            length=length_mm,
        ),
        bearing=bearing,
    )


def field_in_g(magnitude_mm_s2: float) -> float:
    """A body-force field expressed in g, for a message a person can judge.

    18 g means something to an engineer and 176520 mm/s2 does not, and every note this
    module writes is read by somebody deciding whether to believe the case.
    """
    from app.solve.types import STANDARD_GRAVITY_MM_S2

    return magnitude_mm_s2 / STANDARD_GRAVITY_MM_S2


__all__ = [
    "DerivedLoadCase",
    "JointMount",
    "build",
    "cylinder_mount",
    "envelope",
    "field_in_g",
    "inertial_field",
    "sizing_instant",
]
