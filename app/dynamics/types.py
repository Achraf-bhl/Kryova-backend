"""The vocabulary of a mechanism, and of what a mechanism does.

Master plan Phase 9. **This module is the part of the phase that is ours and is not
delegated to any library.** Project Chrono, MBDyn, CATIA DMU and the closed-form
evaluator in `kinematics.py` are four ways of integrating the same statement; the
statement itself -- *these bodies, joined like this, driven like that, over this range*
-- is the asset, exactly as `app.solve.loads` and `app.solve.selection` are the asset on
the FEA side (Decision 2). Swapping the engine underneath must not touch a caller.

**A mechanism is bodies, joints, drivers and a motion range. A result is reaction
forces and accelerations over time, at named locations.** Those two sentences are the
whole design. Everything here serves one of them.

Units, per the repository's mm-N-MPa system, with the one honest wrinkle spelled out:

* length **mm**, time **s**, force **N**, moment **N.mm**, angle **rad**, rate **rad/s**
* acceleration **mm/s2** -- `app.solve.types.STANDARD_GRAVITY_MM_S2` is imported rather
  than restated, because two spellings of g is how a sign error hides
* mass **kg**, and rotational inertia **kg.mm2**

Mass in kilograms is the same decision `app.solve` already made: density is carried in
kg/m3 because that is how a datasheet quotes it, and the conversion to the consistent
mass unit (the tonne, in an mm-N-s system) happens **once, named, at the point where a
force is formed** -- `_DENSITY_KG_M3_TO_TONNE_MM3` in `app/solve/loads.py`, and
`MASS_KG_TO_TONNE` here. Doing it anywhere else, or more than once, is how a factor of
a thousand gets into a bracket.

**Names may not contain a dot or a square bracket.** Results are published as a
measurement payload that `app.design.assertions` reads with dotted paths, so a joint
called `pivot.left` would produce a path nobody can address and an assertion that is
silently unmeasurable. Refused at construction, where it can still be fixed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from app.dynamics.errors import MechanismError
from app.dynamics.pose import Frame, Rot3, Vec3, norm
from app.solve.types import STANDARD_GRAVITY_MM_S2

#: The one mass conversion in this package: kg to the consistent mm-N-s mass unit
#: (the tonne). F[N] = m[t] * a[mm/s2], so F[N] = m[kg]*1e-3 * a[mm/s2].
#: Named here and used only in `reactions.py`, mirroring `app/solve/loads.py`.
MASS_KG_TO_TONNE: Final = 1e-3

#: Gravity as a vector, in the usual "down is -z" convention. Re-exported so a caller
#: never has to write the number, and never has to guess the sign.
GRAVITY_DOWN_MM_S2: Final[Vec3] = (0.0, 0.0, -STANDARD_GRAVITY_MM_S2)

#: Characters that would break a measurement path. See the module docstring.
_FORBIDDEN_IN_NAME: Final = (".", "[", "]")

JointKind = Literal["revolute", "prismatic", "fixed", "spherical"]
DriverKind = Literal["constant", "harmonic", "table"]


def _check_name(name: str, what: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise MechanismError(
            f"A {what} needs a name -- it is what a reaction and a load case are "
            "reported under. Give it one, e.g. 'crank_pivot'."
        )
    bad = [ch for ch in _FORBIDDEN_IN_NAME if ch in cleaned]
    if bad:
        suggestion = cleaned.replace(".", "_").replace("[", "_").replace("]", "")
        raise MechanismError(
            f"The {what} name {cleaned!r} contains {bad[0]!r}, which is how a "
            "measurement path is punctuated -- an assertion on it could never be "
            f"addressed. Use underscores instead, e.g. {suggestion!r}."
        )
    return cleaned


# -- what moves ---------------------------------------------------------------


@dataclass(frozen=True)
class Body:
    """One rigid link, with the properties that decide what force it needs.

    `centre_of_mass_mm` is in the body's **own** frame, whose origin sits at the joint
    that attaches it to its parent. That is not an arbitrary choice: it is what makes a
    link definable before anyone has drawn it, and it is what a CAD part's centre of
    gravity converts into with a single offset.

    `inertia_kg_mm2` is the diagonal of the inertia tensor about the centre of mass, in
    the body frame. **Optional, and the consequence is recorded rather than hidden**: a
    body with no inertia tensor is a point mass, so its joint *forces* are still exact
    (F = m*a does not involve inertia) while its joint *moments* omit the I*alpha and
    omega x I*omega terms. `reactions.py` marks the moment approximated and names the
    body. A tensor invented for the caller would be a number nobody could defend.
    """

    name: str
    mass_kg: float
    centre_of_mass_mm: Vec3 = (0.0, 0.0, 0.0)
    inertia_kg_mm2: Vec3 | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _check_name(self.name, "body"))
        if self.mass_kg < 0.0:
            raise MechanismError(
                f"Body {self.name!r} has mass {self.mass_kg} kg. Mass is not negative; "
                "a massless guide or a datum link is 0."
            )
        if self.inertia_kg_mm2 is not None and any(i < 0.0 for i in self.inertia_kg_mm2):
            raise MechanismError(
                f"Body {self.name!r} has a negative principal inertia "
                f"{self.inertia_kg_mm2}. Give the diagonal of the tensor about the "
                "centre of mass, all three entries positive, in kg.mm2."
            )

    @property
    def is_point_mass(self) -> bool:
        return self.inertia_kg_mm2 is None


@dataclass(frozen=True)
class Joint:
    """How one body is held by another, and which coordinate is free.

    `parent=None` means ground -- the frame the whole mechanism is measured in. Exactly
    one body must reach ground, or the chain is floating and its accelerations are not
    determined by the drivers.

    `origin_mm` and `axis` are given in the **parent's** frame, which is what makes a
    chain assemblable from link drawings alone: the shoulder is 300 mm along the upper
    arm, whatever the upper arm is currently doing.

    Four kinds, and `spherical` is here to be *refused by name* rather than omitted. A
    ball joint is a real thing an engineer will write down; a three-degree-of-freedom
    joint cannot be prescribed by one driver, so the kinematic evaluator says so and
    names the alternative instead of failing on an unknown string.
    """

    name: str
    kind: JointKind
    body: str
    parent: str | None = None
    origin_mm: Vec3 = (0.0, 0.0, 0.0)
    axis: Vec3 = (0.0, 0.0, 1.0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _check_name(self.name, "joint"))
        if self.kind in ("revolute", "prismatic") and norm(self.axis) <= 0.0:
            raise MechanismError(
                f"Joint {self.name!r} is {self.kind} but its axis is zero length. A "
                f"{self.kind} joint moves along or about a direction -- give it one, "
                "e.g. axis=(0, 0, 1)."
            )

    @property
    def is_driven_coordinate(self) -> bool:
        """Whether this joint has exactly one coordinate a driver can prescribe."""
        return self.kind in ("revolute", "prismatic")


# -- what drives it -----------------------------------------------------------


@dataclass(frozen=True)
class Driver:
    """The motion imposed on one joint coordinate, as a function of time.

    Three kinds, and the split is about *how the derivatives are obtained*, which is the
    only thing that matters downstream: an acceleration is what becomes a load, so where
    it came from is not a detail.

    * `constant` -- q = offset + rate*t. Derivatives exact and trivial.
    * `harmonic` -- q = offset + amplitude*sin(2*pi*frequency_hz*t + phase_rad).
      Derivatives exact, analytically differentiated. This is the useful one: a
      reciprocating machine's peak inertia load is at the end of the stroke and this is
      what puts it there.
    * `table` -- samples of q at given times, interpolated linearly. **Its derivatives
      are finite differences**, so they are approximated and say so: a linear
      interpolant has zero second derivative inside a span and a step at each knot,
      which is not an acceleration anybody means. Provided because a measured duty cycle
      is a real input; `kinematics.evaluate` refuses to take accelerations from one
      unless the caller passes `allow_tabulated_acceleration=True` and accepts the
      caveat that comes back with it.
    """

    joint: str
    kind: DriverKind = "constant"
    offset: float = 0.0
    rate: float = 0.0
    amplitude: float = 0.0
    frequency_hz: float = 0.0
    phase_rad: float = 0.0
    times_s: tuple[float, ...] = ()
    values: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "joint", _check_name(self.joint, "driver's joint"))
        if self.kind == "harmonic" and self.frequency_hz <= 0.0:
            raise MechanismError(
                f"The harmonic driver on {self.joint!r} has frequency "
                f"{self.frequency_hz} Hz. A harmonic driver oscillates; give it a "
                "positive frequency, or use kind='constant' to hold it still."
            )
        if self.kind == "table":
            object.__setattr__(self, "times_s", tuple(self.times_s))
            object.__setattr__(self, "values", tuple(self.values))
            if len(self.times_s) < 2 or len(self.times_s) != len(self.values):
                raise MechanismError(
                    f"The tabulated driver on {self.joint!r} has "
                    f"{len(self.times_s)} times and {len(self.values)} values. It needs "
                    "at least two of each, paired one to one."
                )
            if any(b <= a for a, b in zip(self.times_s, self.times_s[1:], strict=False)):
                raise MechanismError(
                    f"The tabulated driver on {self.joint!r} has times that do not "
                    "increase. Sort the samples by time before handing them over -- "
                    "interpolating an unsorted table gives a motion nobody wrote."
                )

    @property
    def derivatives_are_exact(self) -> bool:
        """Whether `at()` differentiates analytically. False only for a table."""
        return self.kind != "table"

    def at(self, t: float) -> tuple[float, float, float]:
        """(q, qdot, qddot) at time `t`, in the joint's own unit (rad or mm)."""
        if self.kind == "constant":
            return (self.offset + self.rate * t, self.rate, 0.0)
        if self.kind == "harmonic":
            omega = 2.0 * math.pi * self.frequency_hz
            angle = omega * t + self.phase_rad
            return (
                self.offset + self.amplitude * math.sin(angle),
                self.amplitude * omega * math.cos(angle),
                -self.amplitude * omega * omega * math.sin(angle),
            )
        return self._table_at(t)

    def _table_at(self, t: float) -> tuple[float, float, float]:
        """Linear interpolation, with backward-difference second derivative.

        Clamped outside the table rather than extrapolated: a duty cycle that was
        measured for two seconds says nothing about the third, and a linear
        extrapolation would invent a load.
        """
        times, values = self.times_s, self.values
        if t <= times[0]:
            return (values[0], 0.0, 0.0)
        if t >= times[-1]:
            return (values[-1], 0.0, 0.0)
        index = 0
        for index in range(len(times) - 1):
            if times[index] <= t <= times[index + 1]:
                break
        t0, t1 = times[index], times[index + 1]
        v0, v1 = values[index], values[index + 1]
        span = t1 - t0
        q = v0 + (v1 - v0) * (t - t0) / span
        rate = (v1 - v0) / span
        if index > 0:
            previous = (values[index] - values[index - 1]) / (times[index] - times[index - 1])
        else:
            previous = rate
        accel = (rate - previous) / span
        return (q, rate, accel)


@dataclass(frozen=True)
class MotionRange:
    """The span of time the mechanism is looked at, and how finely.

    Time rather than joint angle, deliberately, and it is not a stylistic choice: an
    acceleration is a second derivative *with respect to time*, so a range expressed in
    crank degrees has no accelerations in it at all. A driver converts the one into the
    other, which is exactly what a driver is for.

    `samples` is the number of instants, endpoints included, so `samples=2` is start and
    end. Everything sampled -- the clearance sweep, the peak reaction -- is a minimum or
    maximum **over these instants**, never over the continuum, and every report that
    depends on it says so.
    """

    duration_s: float
    samples: int = 37

    def __post_init__(self) -> None:
        if self.duration_s <= 0.0:
            raise MechanismError(
                f"A motion range of {self.duration_s} s covers no motion. Give the "
                "duration of one cycle, or of the manoeuvre being checked."
            )
        if self.samples < 2:
            raise MechanismError(
                f"A motion range needs at least 2 samples (start and end), got "
                f"{self.samples}. 37 samples is one revolution every 10 degrees."
            )

    def times(self) -> tuple[float, ...]:
        last = self.samples - 1
        return tuple(self.duration_s * i / last for i in range(self.samples))


# -- the mechanism ------------------------------------------------------------


@dataclass(frozen=True)
class Mechanism:
    """Bodies, joints, drivers and gravity: everything but the range and the engine.

    Validated at construction for everything that is *local* -- duplicate names, a joint
    naming a body that does not exist, two drivers on one joint. Topology (does it reach
    ground, is there a closed loop) is checked by `kinematics.topological_order`, not
    here, because a closed loop is a perfectly valid mechanism that this evaluator
    cannot pose and an engine can -- so it must not be refused at construction.
    """

    name: str
    bodies: tuple[Body, ...] = ()
    joints: tuple[Joint, ...] = ()
    drivers: tuple[Driver, ...] = ()
    gravity_mm_s2: Vec3 = GRAVITY_DOWN_MM_S2

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _check_name(self.name, "mechanism"))
        object.__setattr__(self, "bodies", tuple(self.bodies))
        object.__setattr__(self, "joints", tuple(self.joints))
        object.__setattr__(self, "drivers", tuple(self.drivers))

        seen: set[str] = set()
        for body in self.bodies:
            if body.name in seen:
                raise MechanismError(
                    f"Two bodies are both called {body.name!r}. Every reaction and "
                    "load case is reported under a name, so they have to be unique."
                )
            seen.add(body.name)

        joint_names: set[str] = set()
        for joint in self.joints:
            if joint.name in joint_names:
                raise MechanismError(
                    f"Two joints are both called {joint.name!r}. Rename one -- a "
                    "reaction is looked up by joint name."
                )
            joint_names.add(joint.name)
            if joint.body not in seen:
                raise MechanismError(
                    f"Joint {joint.name!r} moves body {joint.body!r}, which is not one "
                    f"of this mechanism's bodies ({', '.join(sorted(seen)) or 'none'}). "
                    "Add the body, or fix the name."
                )
            if joint.parent is not None and joint.parent not in seen:
                raise MechanismError(
                    f"Joint {joint.name!r} hangs off {joint.parent!r}, which is not one "
                    "of this mechanism's bodies. Use parent=None for a joint to ground."
                )

        driven: set[str] = set()
        for driver in self.drivers:
            if driver.joint not in joint_names:
                raise MechanismError(
                    f"A driver prescribes joint {driver.joint!r}, which does not exist "
                    f"in {self.name!r}. Its joints are: "
                    f"{', '.join(sorted(joint_names)) or 'none'}."
                )
            if driver.joint in driven:
                raise MechanismError(
                    f"Joint {driver.joint!r} has two drivers. Two prescribed motions "
                    "for one coordinate is not an over-constraint the solver resolves; "
                    "it is a contradiction. Keep one."
                )
            driven.add(driver.joint)

    def body(self, name: str) -> Body:
        for candidate in self.bodies:
            if candidate.name == name:
                return candidate
        raise MechanismError(
            f"{self.name!r} has no body {name!r}. Its bodies are: "
            f"{', '.join(b.name for b in self.bodies) or 'none'}."
        )

    def joint(self, name: str) -> Joint:
        for candidate in self.joints:
            if candidate.name == name:
                return candidate
        raise MechanismError(
            f"{self.name!r} has no joint {name!r}. Its joints are: "
            f"{', '.join(j.name for j in self.joints) or 'none'}."
        )

    def driver_for(self, joint_name: str) -> Driver | None:
        for driver in self.drivers:
            if driver.joint == joint_name:
                return driver
        return None

    @property
    def total_mass_kg(self) -> float:
        return sum(body.mass_kg for body in self.bodies)


# -- what comes back ----------------------------------------------------------


@dataclass(frozen=True)
class BodyMotion:
    """One body's motion through the range, at its centre of mass.

    Position, velocity and acceleration are of the **centre of mass**, because that is
    the point Newton's second law is written about and therefore the only one that turns
    into a load without a further step. `frames` carries the body's pose so a clearance
    sweep can place its geometry; the two are separate because a pose is about the body
    origin and a load is about the centre of mass, and conflating them puts a moment arm
    in the wrong place.
    """

    body: str
    times_s: tuple[float, ...]
    frames: tuple[Frame, ...]
    position_mm: tuple[Vec3, ...]
    velocity_mm_s: tuple[Vec3, ...]
    acceleration_mm_s2: tuple[Vec3, ...]
    angular_velocity_rad_s: tuple[Vec3, ...]
    angular_acceleration_rad_s2: tuple[Vec3, ...]

    @property
    def peak_acceleration_mm_s2(self) -> float:
        return max((norm(a) for a in self.acceleration_mm_s2), default=0.0)

    @property
    def travel_mm(self) -> float:
        """How far the centre of mass moves, summed along the sampled path.

        A path length over the samples, not a straight-line displacement: a crank pin
        returns to where it started and its displacement is zero, which is not a
        statement anybody wants about how far it travelled.
        """
        total = 0.0
        for a, b in zip(self.position_mm, self.position_mm[1:], strict=False):
            total += norm((b[0] - a[0], b[1] - a[1], b[2] - a[2]))
        return total

    def rotation_at(self, index: int) -> Rot3:
        return self.frames[index].rotation


@dataclass(frozen=True)
class JointReaction:
    """What one joint carries, through the range -- the phase's deliverable.

    Sign convention, stated because there are two and both are defended in textbooks:
    **the force and moment reported are those applied by the parent side onto the child
    side**, at the joint's own location. That is the load the bracket holding the pin
    actually feels, which is the number this whole phase exists to produce, and it is
    the one that goes straight into a `LoadCase` with no sign to remember.

    **A reaction that could not be computed carries a reason and no numbers.** It is
    never a zero: a joint whose load is unknown and a joint carrying nothing are the
    opposite of each other, and `to_measurement_payload` renders the first as
    UNAVAILABLE so an assertion on it comes back UNMEASURED.
    """

    joint: str
    times_s: tuple[float, ...] = ()
    location_mm: tuple[Vec3, ...] = ()
    force_n: tuple[Vec3, ...] = ()
    moment_n_mm: tuple[Vec3, ...] = ()

    #: Why there are no numbers, when there are none. Empty when the reaction was found.
    unavailable_reason: str = ""

    #: Why the *moment* is approximate, when it is -- a point-mass idealisation, most
    #: often. Empty when the moment is exact. The force is exact whenever it is present.
    moment_caveat: str = ""

    @property
    def available(self) -> bool:
        return not self.unavailable_reason and bool(self.force_n)

    @property
    def peak_force_n(self) -> float | None:
        if not self.available:
            return None
        return max(norm(f) for f in self.force_n)

    @property
    def peak_moment_n_mm(self) -> float | None:
        if not self.available or not self.moment_n_mm:
            return None
        return max(norm(m) for m in self.moment_n_mm)

    def peak_index(self) -> int | None:
        """The sample where the force magnitude is largest -- the sizing instant."""
        if not self.available:
            return None
        return max(range(len(self.force_n)), key=lambda i: norm(self.force_n[i]))


@dataclass(frozen=True)
class MotionPath:
    """Every body's motion through one range. The kinematic answer, complete.

    Useful entirely on its own, and that is worth saying plainly: swept volume,
    interference through travel, lock and travel checks (master plan 9.3) are questions
    about *positions*, and none of them needs a dynamics engine. `clearance.py` consumes
    exactly this.
    """

    mechanism: str
    times_s: tuple[float, ...]
    bodies: Mapping[str, BodyMotion]
    joint_paths: Mapping[str, tuple[Vec3, ...]] = field(default_factory=dict)

    #: How the derivatives were obtained, in words, for the provenance record.
    method: str = ""

    #: Anything the caller should know that is not a failure.
    warnings: tuple[str, ...] = ()

    def motion(self, body: str) -> BodyMotion:
        try:
            return self.bodies[body]
        except KeyError:
            raise MechanismError(
                f"No motion was computed for body {body!r}. This path covers: "
                f"{', '.join(sorted(self.bodies)) or 'nothing'}."
            ) from None


@dataclass(frozen=True)
class MechanismResult:
    """A mechanism, run: where everything went and what every joint carried.

    `engine` names what produced it. That is not decoration -- Decision 3 binds a result
    to what computed it, and a reaction from the closed-form serial-chain recursion and
    one from a contact-resolving dynamics engine are not interchangeable evidence.
    """

    mechanism: str
    engine: str
    path: MotionPath
    reactions: Mapping[str, JointReaction] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def reaction(self, joint: str) -> JointReaction:
        try:
            return self.reactions[joint]
        except KeyError:
            raise MechanismError(
                f"No reaction was computed for joint {joint!r}. This result covers: "
                f"{', '.join(sorted(self.reactions)) or 'nothing'}."
            ) from None

    @property
    def peak_reaction_force_n(self) -> float | None:
        """The largest force any joint carries at any sampled instant.

        None when no joint's reaction could be computed -- which is not zero, and is
        rendered as UNAVAILABLE in the payload for exactly that reason.
        """
        peaks = [
            peak
            for reaction in self.reactions.values()
            if (peak := reaction.peak_force_n) is not None
        ]
        return max(peaks) if peaks else None

    def to_measurement_payload(self) -> dict[str, Any]:
        """The result as something `app.design.assertions` can check claims against.

        Nested under joints.<name> and bodies.<name> so an assertion reads
        "joints.crank_pin.peak_force_n <= 4200", which is a sentence an engineer would
        say. Provenance is attached per path, so a moment computed from a point-mass
        idealisation is approximated while the force beside it stays measured.

        `app.kernel.provenance` is imported lazily for the reason
        `app.design.assertions` gives for the same import: `app.kernel.__init__` pulls
        in OCP, and this package's tests are worth keeping offline and instant.
        """
        from app.kernel import provenance

        payload: dict[str, Any] = {
            "duration_s": self.path.times_s[-1] if self.path.times_s else 0.0,
            "sample_count": len(self.path.times_s),
            "engine": self.engine,
        }

        peak = self.peak_reaction_force_n
        if peak is None:
            reasons = sorted(
                {r.unavailable_reason for r in self.reactions.values() if r.unavailable_reason}
            )
            provenance.attach(
                payload,
                "peak_reaction_force_n",
                provenance.unavailable(
                    "no joint reaction in this mechanism could be computed; "
                    + ("; ".join(reasons) or "no reactions were requested at all.")
                ),
            )
        else:
            payload["peak_reaction_force_n"] = peak
            provenance.attach(
                payload,
                "peak_reaction_force_n",
                provenance.measured(
                    f"largest joint force over {len(self.path.times_s)} sampled instants"
                ),
            )

        method = self.path.method or self.engine
        joints: dict[str, Any] = {}
        for name, reaction in self.reactions.items():
            entry: dict[str, Any] = {}
            force_path = f"joints.{name}.peak_force_n"
            moment_path = f"joints.{name}.peak_moment_n_mm"
            if not reaction.available:
                why = (
                    reaction.unavailable_reason
                    or "the reaction at this joint was not computed."
                )
                provenance.attach(payload, force_path, provenance.unavailable(why))
                provenance.attach(payload, moment_path, provenance.unavailable(why))
                joints[name] = entry
                continue
            entry["peak_force_n"] = reaction.peak_force_n
            provenance.attach(payload, force_path, provenance.measured(method))
            moment = reaction.peak_moment_n_mm
            if moment is not None:
                entry["peak_moment_n_mm"] = moment
                if reaction.moment_caveat:
                    provenance.attach(
                        payload, moment_path, provenance.approximated(reaction.moment_caveat)
                    )
                else:
                    provenance.attach(payload, moment_path, provenance.measured(method))
            joints[name] = entry
        payload["joints"] = joints

        bodies: dict[str, Any] = {}
        for name, motion in self.path.bodies.items():
            bodies[name] = {
                "peak_acceleration_mm_s2": motion.peak_acceleration_mm_s2,
                "travel_mm": motion.travel_mm,
            }
            provenance.attach(
                payload,
                f"bodies.{name}.peak_acceleration_mm_s2",
                provenance.measured(method),
            )
            provenance.attach(
                payload,
                f"bodies.{name}.travel_mm",
                provenance.approximated(
                    f"path length summed over {len(self.path.times_s)} sampled poses; a "
                    "coarser sweep chords the corners and under-reports"
                ),
            )
        payload["bodies"] = bodies
        return payload


def sequence_to_vec3(values: Sequence[float]) -> Vec3:
    """A length-3 sequence as a `Vec3`, refused otherwise.

    A boundary helper: JSON and Pydantic hand over lists, and silently accepting a
    length-2 one would make a planar mechanism look like it worked.
    """
    if len(values) != 3:
        raise MechanismError(
            f"A point or direction needs three components, got {len(values)}: "
            f"{list(values)!r}. This layer is 3D even when the mechanism is planar -- "
            "use 0 for the out-of-plane component."
        )
    return (float(values[0]), float(values[1]), float(values[2]))


__all__ = [
    "GRAVITY_DOWN_MM_S2",
    "MASS_KG_TO_TONNE",
    "STANDARD_GRAVITY_MM_S2",
    "Body",
    "BodyMotion",
    "Driver",
    "DriverKind",
    "Joint",
    "JointKind",
    "JointReaction",
    "Mechanism",
    "MechanismResult",
    "MotionPath",
    "MotionRange",
    "sequence_to_vec3",
]
