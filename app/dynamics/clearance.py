"""Does it clash anywhere in its travel? -- master plan 9.3, and gate G3's whole subject.

The single most valuable thing this phase delivers early, because it is what turns "two
parts that fit" into "a mechanism that works". Two parts that clear each other in the
pose they were drawn in tell you nothing: the question is whether they clear at every
pose the machine reaches, and that question needs a motion path and a distance query and
nothing else -- no dynamics engine, no solver, no seat.

**The distance query is not reimplemented here.** `app.kernel.interrogation` already
declares `ClearanceReport` -- minimum distance, interference volume, the closest point on
each shape, and an honest account of when the overlap question is not a question --
and `app/kernel/occt/interrogate/proximity.py` answers it exactly with
`BRepExtrema_DistShapeShape`. This module sweeps poses and consumes those reports. It
imports the kernel lazily and only for the report type, so a caller with no OCCT
installed can still run the sweep against any measurer they supply.

**The measurer is injected.** Exactly the discipline `app/design/execute.py` keeps: the
only thing in this package that touches the outside world does so through a callable the
caller passes in. That is why the tests here run offline in milliseconds against an
analytic stand-in whose true minimum is known in closed form -- which is the only way to
verify a sweep at all, since a sweep over real geometry can only be compared against
itself.

**A minimum over samples is an upper bound on the true minimum, never the true
minimum.** Between two sampled poses the parts may come closer, and no sample density
removes that. The same honesty `ThicknessReport` keeps about ray casting, for the same
reason, and the payload records it as APPROXIMATED with the sample count in the method
string so a reviewer can judge whether 37 poses was enough for a mechanism that snaps
through a toggle.

**An interference found at any pose is reported as interference, and that half is not a
bound.** A clash that was seen was seen: sampling can miss a clash, it cannot invent one.
So the sweep's "they collide" is a fact and its "they clear by 2.1 mm" is a bound, and
the report keeps them apart.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.dynamics.errors import MechanismError
from app.dynamics.pose import Frame
from app.dynamics.types import MotionPath

#: Called once per sampled pose with the two bodies' names and world frames. Returns
#: anything shaped like `app.kernel.interrogation.ClearanceReport` -- `distance_mm`,
#: `interference_mm3`, `failure`. Typed loosely on purpose: the caller may be driving
#: OCCT, a CATIA seat through `catia_analysis_part`, or a closed-form stand-in, and this
#: module must not be able to tell which.
PoseMeasurer = Callable[[str, Frame, str, Frame], Any]


@dataclass(frozen=True)
class ClearanceSweep:
    """How close two bodies come across a whole motion range.

    `minimum_mm` is `None` rather than zero when nothing could be measured, and the two
    are opposite claims: an unmeasured pair and a touching pair look identical in a
    number and completely different to anyone deciding whether to build the machine.
    """

    body_a: str
    body_b: str
    samples: int

    #: The smallest distance seen, in mm. An **upper bound** on the true minimum over
    #: the continuous motion -- see the module docstring.
    minimum_mm: float | None = None

    #: Index and time of the closest sampled pose. What a viewer would jump to.
    at_index: int | None = None
    at_time_s: float | None = None

    #: The largest overlap volume seen, mm3. Greater than zero means they collide, and
    #: unlike the minimum this is not a bound: sampling can miss a clash, never invent one.
    interference_mm3: float = 0.0
    interference_index: int | None = None

    #: Poses where the measurer could not answer, with its reason. Counted rather than
    #: dropped: a sweep of 37 poses that measured four of them is not a sweep.
    failures: tuple[str, ...] = ()

    #: Why the whole sweep produced nothing, when it produced nothing.
    unavailable_reason: str = ""

    #: How many poses the measurer was actually asked about. Equal to `samples` for a
    #: complete sweep and **smaller when `stop_on_collision` short-circuited**, which is
    #: the whole reason it is a field rather than being assumed. It shipped assumed:
    #: `measured_poses` was `samples - len(failures)`, so a sweep that stopped at pose 9
    #: of 21 published `measured_pose_count: 21` -- twelve poses nobody looked at,
    #: counted as measured, in the payload an assertion reads.
    attempted: int = 0

    #: Whether the sweep stopped at the first collision instead of finishing. When true
    #: `interference_mm3` is the **first** overlap found, not the worst, and
    #: `minimum_mm` covers only the poses before it.
    stopped_early: bool = False

    @property
    def collides(self) -> bool:
        return self.interference_mm3 > 0.0

    @property
    def measured_poses(self) -> int:
        """Poses that were asked about *and* returned a distance."""
        return self.attempted - len(self.failures)

    def to_payload(self) -> dict[str, Any]:
        """The sweep as a measurement payload `app.design.assertions` can read.

        Uses `app.kernel.interrogation`'s own path constants rather than new spellings,
        so an assertion written against a single-pose clearance check reads a swept one
        unchanged -- which is the point of those constants being constants.
        """
        from app.kernel import interrogation, provenance

        payload: dict[str, Any] = {
            "sample_count": self.samples,
            "measured_pose_count": self.measured_poses,
        }
        path = interrogation.MINIMUM_CLEARANCE_MM
        payload["attempted_pose_count"] = self.attempted
        payload["stopped_early"] = self.stopped_early
        early = (
            " The sweep stopped at the first collision, so poses after it were never "
            "looked at."
            if self.stopped_early
            else ""
        )

        if self.minimum_mm is None:
            provenance.attach(
                payload,
                path,
                provenance.unavailable(
                    self.unavailable_reason
                    or (
                        f"none of the {self.samples} sampled poses could be measured: "
                        + ("; ".join(self.failures[:3]) or "no reason was given.")
                    )
                ),
            )
        else:
            payload[path] = self.minimum_mm
            payload["closest_pose_index"] = self.at_index
            if self.at_time_s is not None:
                payload["closest_pose_time_s"] = self.at_time_s
            provenance.attach(
                payload,
                path,
                provenance.approximated(
                    f"minimum over {self.measured_poses} measured poses of the "
                    f"{self.attempted} asked about, in a {self.samples}-sample motion "
                    "range; an upper bound on the true minimum, because the parts may "
                    "come closer between two samples." + early
                ),
            )

        payload[interrogation.INTERFERENCE_VOLUME_MM3] = self.interference_mm3
        payload["interferes"] = self.collides
        provenance.attach(
            payload,
            interrogation.INTERFERENCE_VOLUME_MM3,
            provenance.measured(
                (
                    "first boolean-common volume found; the sweep stopped there, so "
                    "this is not the worst overlap in the travel"
                    if self.stopped_early
                    else "largest boolean-common volume over the sampled poses"
                )
                + "; a clash that was seen was seen, though a clash between two samples "
                "can still be missed"
            ),
        )
        return payload

    def describe(self) -> str:
        if self.minimum_mm is None and not self.collides:
            return (
                f"{self.body_a} vs {self.body_b}: not checked -- "
                f"{self.unavailable_reason or 'no pose could be measured'}"
            )
        if self.collides:
            which = "first" if self.stopped_early else "worst"
            return (
                f"{self.body_a} vs {self.body_b}: COLLIDE, {which} overlap "
                f"{self.interference_mm3:.4g} mm3 at sample {self.interference_index}."
            )
        return (
            f"{self.body_a} vs {self.body_b}: closest {self.minimum_mm:.4g} mm at "
            f"sample {self.at_index} (t={self.at_time_s:.4g}s), over "
            f"{self.measured_poses} of {self.samples} poses. Upper bound."
        )


def sweep(
    path: MotionPath,
    body_a: str,
    body_b: str,
    measure: PoseMeasurer,
    *,
    stop_on_collision: bool = False,
) -> ClearanceSweep:
    """Measure the clearance between two bodies at every pose of the motion path.

    `stop_on_collision` short-circuits at the first overlap. Off by default because the
    *worst* overlap is what tells a correction loop how far to move something, and the
    first one only tells it that something is wrong; on when a caller is asking the
    cheap yes/no question over a long sweep. When it fires, the result says so
    (`stopped_early`) and `attempted` records how far the sweep actually got -- an
    unlooked-at pose is never counted as a measured one, and the payload's provenance
    says the interference volume is the first found rather than the largest.

    The measurer is called once per pose and **is allowed to fail**: a pose it could not
    answer is counted in `failures` and the sweep continues, because the answer over the
    other thirty-six poses is still worth having and the count is what says whether to
    believe it. An exception from the measurer is caught for the same reason and recorded
    with its text -- this function does not raise on a measurement, only on being asked
    about a body that is not in the path.
    """
    motion_a = path.motion(body_a)
    motion_b = path.motion(body_b)
    if body_a == body_b:
        raise MechanismError(
            f"Clearance between {body_a!r} and itself is not a question. Name the two "
            "different bodies that must not touch."
        )

    best: float | None = None
    best_index: int | None = None
    worst_overlap = 0.0
    worst_index: int | None = None
    failures: list[str] = []
    attempted = 0
    stopped_early = False

    for index in range(len(path.times_s)):
        attempted += 1
        try:
            report = measure(
                body_a, motion_a.frames[index], body_b, motion_b.frames[index]
            )
        except Exception as exc:  # noqa: BLE001 - a measurer's failure is data, not a crash
            failures.append(f"sample {index}: {type(exc).__name__}: {exc}")
            continue

        failure = getattr(report, "failure", "") or ""
        distance = getattr(report, "distance_mm", None)
        overlap = float(getattr(report, "interference_mm3", 0.0) or 0.0)

        if overlap > worst_overlap:
            worst_overlap = overlap
            worst_index = index

        if failure or distance is None:
            failures.append(
                f"sample {index}: {failure or 'the measurer reported no distance'}"
            )
        else:
            value = float(distance)
            if best is None or value < best:
                best, best_index = value, index

        if stop_on_collision and overlap > 0.0:
            stopped_early = index < len(path.times_s) - 1
            break

    times = path.times_s
    unavailable = ""
    if best is None:
        unavailable = (
            f"none of the {attempted} sampled poses returned a distance"
            + (f": {failures[0]}" if failures else "")
        )

    return ClearanceSweep(
        body_a=body_a,
        body_b=body_b,
        samples=len(times),
        minimum_mm=best,
        at_index=best_index,
        at_time_s=None if best_index is None else times[best_index],
        interference_mm3=worst_overlap,
        interference_index=worst_index,
        failures=tuple(failures),
        unavailable_reason=unavailable,
        attempted=attempted,
        stopped_early=stopped_early,
    )


def sweep_pairs(
    path: MotionPath,
    pairs: Sequence[tuple[str, str]],
    measure: PoseMeasurer,
) -> tuple[ClearanceSweep, ...]:
    """Several pairs over the same path, in the order given.

    Pairs are given explicitly rather than every combination of bodies, and that is not
    laziness: two links joined by a pin touch by design, so an all-pairs check reports a
    clash for every joint in the machine and the real one is lost among them. Naming the
    pairs is how the question stays answerable.
    """
    return tuple(sweep(path, a, b, measure) for a, b in pairs)


def worst(sweeps: Sequence[ClearanceSweep]) -> ClearanceSweep | None:
    """The pair that comes closest, collisions first. None when there are no sweeps.

    Collisions sort ahead of near misses regardless of magnitude, because a 0.01 mm3
    overlap and a 0.5 mm clearance are not comparable quantities and ranking them
    together by number would bury the clash.
    """
    if not sweeps:
        return None
    colliding = [s for s in sweeps if s.collides]
    if colliding:
        return max(colliding, key=lambda s: s.interference_mm3)
    measured = [s for s in sweeps if s.minimum_mm is not None]
    if not measured:
        return sweeps[0]
    return min(measured, key=lambda s: s.minimum_mm or 0.0)


__all__ = ["ClearanceSweep", "PoseMeasurer", "sweep", "sweep_pairs", "worst"]
