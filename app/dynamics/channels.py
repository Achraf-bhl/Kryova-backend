"""A machine's cycle as fatigue load channels: manoeuvre to damage -- master plan 9.4.

`loadcases.build` turns one instant of a cycle into one `LoadCase`, and `envelope` turns
every instant into one case each: N solves for an N-sample cycle. Fatigue needs the whole
cycle, and it does not need N solves. A linear solve's stress is linear in its loads, so
a load that varies as `F(t)` can be solved **once per independent direction** and scaled.
This module splits a mechanism's cycle into those channels. Each is a `LoadCase` to solve
once and a signal of multiples, which is exactly what `app.fatigue.field.LoadChannel`
takes. So the chain the task names closes: manoeuvre, `reactions.compute`, `plan`, one
solve per channel, `LoadChannel.from_solve`, `history_at`, `Assessment`.

**Which split is exact depends on the load, and each is chosen for that reason:**

* **A plain force mount** (`bearing=False`) is spread by tributary area, which is linear
  in the force vector. Three channels, one per world axis, reconstruct `F(t)` exactly at
  every instant, whatever the direction does.
* **A bearing mount is not linear in the force vector.** Its cosine pressure sits on the
  side of the bore the pin pushes, so the distribution depends on the direction, and
  x/y/z channels would put pressure on three sides of the bore at once. A bearing mount
  is split along its one line of action instead: a channel pushing one way and a channel
  pushing the other, each carrying the positive part of the signal. At any instant only
  one of them is non-zero, so the sum is the bearing load of that instant exactly. **This
  is exact only when the reaction keeps its line.** A reaction that turns is refused, with
  the angle it turned by and the two ways forward (mount it as a plain force, or solve
  `envelope`'s instants).
* **The body's own inertia** is `g − a(t)`, a uniform field that `GravityLoad` applies
  linearly. Three channels, one per world axis.

A channel whose signal is zero at every instant is dropped and named, because a solve that
contributes nothing is a solve somebody waits for.

**What the plan does not claim.** The history is the sampled motion range, taken as one
block of the duty cycle. A peak between two samples is not in it, so the cycle's range is
a lower bound on the true one. A spinning body's `omega² r` field across its own extent is
not in the uniform field, and the note says so as `build` does. Joint moments are not
applied, as in `build`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.dynamics.errors import MechanismError
from app.dynamics.loadcases import JointMount, _available_reaction
from app.dynamics.pose import Vec3, dot, norm, sub
from app.dynamics.types import MechanismResult
from app.solve.types import BearingLoad, Fixture, ForceLoad, GravityLoad, LoadCase, Material

if TYPE_CHECKING:
    from app.fatigue.field import LoadChannel

#: A bearing reaction may turn by this much and still be one line of action, in degrees.
#: It is a rounding allowance for a reaction computed along a fixed line, not an
#: engineering tolerance: a reaction that genuinely turns is refused at any angle.
LINE_OF_ACTION_TOLERANCE_DEG = 1e-6

_AXES: tuple[tuple[str, Vec3], ...] = (
    ("x", (1.0, 0.0, 0.0)),
    ("y", (0.0, 1.0, 0.0)),
    ("z", (0.0, 0.0, 1.0)),
)


@dataclass(frozen=True)
class ChannelPlan:
    """One case to solve once, and the multiples of it the cycle applies."""

    name: str
    case: LoadCase
    signal: tuple[float, ...]
    source: str


@dataclass(frozen=True)
class CyclePlan:
    mechanism: str
    engine: str
    times_s: tuple[float, ...]
    channels: tuple[ChannelPlan, ...]
    #: Channels whose signal is zero throughout, by name. Not solved.
    dropped: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def load_channels(
        self, stresses: Mapping[str, Any], *, solver: str = ""
    ) -> list[LoadChannel]:
        """`LoadChannel`s from each channel's solved `SolveOutput`, keyed by channel name."""
        from app.fatigue.field import LoadChannel

        missing = [c.name for c in self.channels if c.name not in stresses]
        if missing:
            raise MechanismError(
                "No solve was given for channel(s) " + ", ".join(missing) + ". Every channel "
                "is part of the load; assessing without one leaves that part out."
            )
        return [
            LoadChannel.from_solve(
                channel.name, stresses[channel.name], channel.signal, source=channel.source,
                solver=solver,
            )
            for channel in self.channels
        ]


def _case(name: str, material: Material, fixtures: Sequence[Fixture], load: Any) -> LoadCase:
    return LoadCase(name=name, material=material, fixtures=list(fixtures), loads=[load])


def plan(
    result: MechanismResult,
    *,
    mounts: Sequence[JointMount],
    material: Material,
    fixtures: Sequence[Fixture],
    gravity_mm_s2: Vec3,
    inertia_of: str | None = None,
) -> CyclePlan:
    """Split the sampled cycle into channels whose superposition is each instant's load."""
    if not fixtures:
        raise MechanismError(
            "A load case needs at least one fixture, or the static solve is singular. Hold "
            "the part where the machine holds it."
        )
    if not mounts and inertia_of is None:
        raise MechanismError(
            "Nothing loads this part: no mount and no inertia. Name the joints whose "
            "reactions land on it, or pass inertia_of."
        )
    times = result.path.times_s
    if len(times) < 3:
        raise MechanismError(
            f"The run has {len(times)} samples; a fatigue history needs at least 3. Sample "
            "the motion range more finely."
        )
    source = (
        f"{result.mechanism}, {len(times)} samples over {times[-1] - times[0]:.4g} s from "
        f"{result.engine}, taken as one block of the duty cycle"
    )

    channels: list[ChannelPlan] = []
    dropped: list[str] = []
    notes: list[str] = [
        "The history is sampled: a peak between two samples is not in it, so the cycle's "
        "range is a lower bound on the true one."
    ]

    def add(name: str, load: Any, signal: list[float]) -> None:
        if all(value == 0.0 for value in signal):
            dropped.append(name)
            return
        channels.append(
            ChannelPlan(
                name=name,
                case=_case(f"{result.mechanism}: {name}", material, fixtures, load),
                signal=tuple(signal),
                source=source,
            )
        )

    for mount in mounts:
        reaction = _available_reaction(result, mount)
        forces = reaction.force_n
        label = mount.name or f"{mount.joint} reaction"
        if reaction.moment_caveat:
            notes.append(f"{mount.joint}: {reaction.moment_caveat}")
        if not mount.bearing:
            for k, (axis, unit_vector) in enumerate(_AXES):
                component = [f[k] for f in forces]
                peak = max(abs(c) for c in component)
                name = f"{label} {axis}"
                if peak == 0.0:
                    dropped.append(name)
                    continue
                add(
                    name,
                    ForceLoad(
                        where=mount.where,
                        force_n=(unit_vector[0] * peak, unit_vector[1] * peak, unit_vector[2] * peak),
                        name=name,
                    ),
                    [c / peak for c in component],
                )
            continue

        peak_index = max(range(len(forces)), key=lambda i: norm(forces[i]))
        peak_force = norm(forces[peak_index])
        if peak_force == 0.0:
            dropped.append(f"{label} +")
            dropped.append(f"{label} -")
            continue
        line = (
            forces[peak_index][0] / peak_force,
            forces[peak_index][1] / peak_force,
            forces[peak_index][2] / peak_force,
        )
        along = [dot(f, line) for f in forces]
        worst_turn = 0.0
        worst_at = 0
        for i, f in enumerate(forces):
            magnitude = norm(f)
            # An instant carrying rounding (a harmonic's zero crossing) has no direction
            # worth measuring; its component along the line is what reaches the part.
            if magnitude <= 1e-9 * peak_force:
                continue
            across = norm(sub(f, (line[0] * along[i], line[1] * along[i], line[2] * along[i])))
            turn = math.degrees(math.asin(min(1.0, across / magnitude)))
            if turn > worst_turn:
                worst_turn, worst_at = turn, i
        if worst_turn > LINE_OF_ACTION_TOLERANCE_DEG:
            raise MechanismError(
                f"The reaction at {mount.joint!r} turns by {worst_turn:.4g} degrees off its "
                f"line of action (at t = {times[worst_at]:.4g} s). A bearing pressure sits "
                "on the side of the bore the pin pushes, so a turning bearing load is not a "
                "scaled copy of one solve. Mount it with bearing=False for three exact "
                "force channels, or solve loadcases.envelope's instants one by one."
            )
        push = max(along)
        pull = max(-a for a in along)
        if push > 0.0:
            add(
                f"{label} +",
                BearingLoad(
                    where=mount.where,
                    force_n=(line[0] * push, line[1] * push, line[2] * push),
                    name=f"{label} +",
                ),
                [max(a, 0.0) / push for a in along],
            )
        else:
            dropped.append(f"{label} +")
        if pull > 0.0:
            add(
                f"{label} -",
                BearingLoad(
                    where=mount.where,
                    force_n=(-line[0] * pull, -line[1] * pull, -line[2] * pull),
                    name=f"{label} -",
                ),
                [max(-a, 0.0) / pull for a in along],
            )
        else:
            dropped.append(f"{label} -")

    if inertia_of is not None:
        motion = result.path.motion(inertia_of)
        fields = [sub(gravity_mm_s2, a) for a in motion.acceleration_mm_s2]
        for k, (axis, unit_vector) in enumerate(_AXES):
            component = [f[k] for f in fields]
            peak = max(abs(c) for c in component)
            name = f"{inertia_of} inertia + weight {axis}"
            if peak == 0.0:
                dropped.append(name)
                continue
            add(
                name,
                GravityLoad(direction=unit_vector, magnitude_mm_s2=peak, name=name),
                [c / peak for c in component],
            )
        spin = max(norm(w) for w in motion.angular_velocity_rad_s)
        if spin > 0.0:
            notes.append(
                f"{inertia_of} rotates at up to {spin:.4g} rad/s. Its inertia is applied as "
                "the centre-of-mass acceleration, uniform over the part; the omega^2*r "
                "field across the part is not in these channels."
            )

    if not channels:
        raise MechanismError(
            "Every channel is zero at every instant, so nothing loads this part over the "
            "cycle. Check the mounts name the right joints."
        )
    return CyclePlan(
        mechanism=result.mechanism,
        engine=result.engine,
        times_s=tuple(times),
        channels=tuple(channels),
        dropped=tuple(dropped),
        notes=tuple(notes),
    )


def instant(cycle: CyclePlan, index: int) -> dict[str, Any]:
    """The superposed load of every channel at one instant, per load name.

    Forces sum per mount selector label (without the channel suffix) and fields sum as a
    vector. What a test compares against `loadcases.build(at=index)`: the two must agree
    at every instant, or the split is not exact.
    """
    forces: dict[str, list[float]] = {}
    field = [0.0, 0.0, 0.0]
    for channel in cycle.channels:
        scale_by = channel.signal[index]
        [load] = channel.case.loads
        if isinstance(load, GravityLoad):
            for k in range(3):
                field[k] += scale_by * load.magnitude_mm_s2 * load.direction[k]
            continue
        key = channel.name.rsplit(" ", 1)[0]
        total = forces.setdefault(key, [0.0, 0.0, 0.0])
        for k in range(3):
            total[k] += scale_by * load.force_n[k]
    return {"forces_n": {k: tuple(v) for k, v in forces.items()}, "field_mm_s2": tuple(field)}


__all__ = [
    "LINE_OF_ACTION_TOLERANCE_DEG",
    "ChannelPlan",
    "CyclePlan",
    "instant",
    "plan",
]
