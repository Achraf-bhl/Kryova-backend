"""Does it stay inside its travel, does it lock, and what space does it sweep -- master plan 9.3.

`clearance.py` answers the fourth question of task 3 (interference through motion). This
module answers the other three, and each is held to the standard of the rest of the
package: an exact answer where one exists, a bound that says it is a bound where it does
not.

**Travel is exact, not sampled.** A joint's coordinate comes from its driver, and every
driver kind has extremes that can be found in closed form over an interval. A constant
rate is monotone, so the extremes are the endpoints. A harmonic driver reaches
`offset ± amplitude` at the instants where its phase crosses π/2 + kπ, which are
computed, not searched for. A table is linearly interpolated and clamped, so its extremes
are among its knots and the two endpoints. So `travel` reports the true minimum and
maximum over the whole range, and a limit violated between two samples is still found.
The limits are the caller's, with a source: an end stop's position is on a drawing, not
in the mechanism.

**A lock is a four-bar that cannot be assembled or cannot be driven.** `closures.four_bar`
refuses at both. `lock_sweep` walks an input range, reports the first angle it refuses at
with the refusal's words, and the smallest **transmission angle** it saw. The transmission
angle is the angle between coupler and output: at 0 or 180 degrees the input can push the
output nowhere. The acceptable minimum is a design criterion, and it is the caller's to
state with its source. None is defaulted here, because a textbook number typed into a
default is a number nobody checked against this machine. **The sweep is sampled**, so the
minimum is an upper bound on the true minimum, and the report says so.

**Swept volume is a lower bound, by construction.** The union of the part placed at every
sampled pose is contained in the true swept volume, so its volume can only be smaller.
`swept_volume` takes the union measurer by injection, as `clearance.sweep` takes its
distance measurer, so it runs offline against an analytic stand-in. `occt_union_volume`
is the real measurer: it places the shape at each pose with `BRepBuilderAPI_Transform`,
fuses the copies, and integrates. `swept_envelope` is the cheap companion: the
axis-aligned box of a set of body points over every pose, which is also sampled and is
reported as such.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.design.assertions import Outcome
from app.dynamics.closures import four_bar
from app.dynamics.errors import MechanismError
from app.dynamics.pose import Frame, Vec3
from app.dynamics.types import Driver, MotionPath, MotionRange

# -- travel ---------------------------------------------------------------------


@dataclass(frozen=True)
class TravelLimit:
    """A joint's end stops, in the joint's own unit (rad or mm), with where they came from."""

    joint: str
    lower: float
    upper: float
    source: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise MechanismError(
                f"The travel limit on {self.joint!r} has no source. An end stop is on a "
                "drawing or a catalogue page; name it."
            )
        if not (math.isfinite(self.lower) and math.isfinite(self.upper)) or self.lower >= self.upper:
            raise MechanismError(
                f"The travel limit on {self.joint!r} is [{self.lower}, {self.upper}]. Give "
                "a finite lower stop below a finite upper stop."
            )


@dataclass(frozen=True)
class TravelReport:
    joint: str
    minimum: float
    maximum: float
    at_minimum_s: float
    at_maximum_s: float
    limit: TravelLimit
    outcome: Outcome
    #: Distance inside the nearer stop; negative when a stop is passed.
    margin: float
    summary: str


def _harmonic_extremes(driver: Driver, start: float, end: float) -> list[float]:
    omega = 2.0 * math.pi * driver.frequency_hz
    # Phase ωt + φ = π/2 + kπ; solve for t and keep those inside the range.
    first_k = math.ceil(((omega * start + driver.phase_rad) - math.pi / 2.0) / math.pi)
    times: list[float] = []
    k = first_k
    while True:
        t = (math.pi / 2.0 + k * math.pi - driver.phase_rad) / omega
        if t > end:
            break
        if t >= start:
            times.append(t)
        k += 1
    return times


def coordinate_extremes(driver: Driver, duration_s: float) -> tuple[float, float, float, float]:
    """(minimum, time of minimum, maximum, time of maximum) of the driver over [0, duration].

    Exact for every driver kind; see the module docstring for why.
    """
    if duration_s <= 0.0:
        raise MechanismError(f"A duration of {duration_s} s covers no motion.")
    candidates = [0.0, duration_s]
    if driver.kind == "harmonic":
        candidates.extend(_harmonic_extremes(driver, 0.0, duration_s))
    elif driver.kind == "table":
        candidates.extend(t for t in driver.times_s if 0.0 < t < duration_s)
    values = [(driver.at(t)[0], t) for t in candidates]
    low = min(values, key=lambda item: (item[0], item[1]))
    high = max(values, key=lambda item: (item[0], -item[1]))
    return low[0], low[1], high[0], high[1]


def travel(driver: Driver, limit: TravelLimit, motion: MotionRange) -> TravelReport:
    """Whether the driven joint stays between its stops over the whole range."""
    if driver.joint != limit.joint:
        raise MechanismError(
            f"The driver moves {driver.joint!r} and the limit is on {limit.joint!r}. Pair "
            "each limit with the driver of the same joint."
        )
    low, t_low, high, t_high = coordinate_extremes(driver, motion.duration_s)
    margin = min(low - limit.lower, limit.upper - high)
    passed = margin >= 0.0
    if passed:
        summary = (
            f"{limit.joint} travels {low:.6g} to {high:.6g}, inside [{limit.lower:.6g}, "
            f"{limit.upper:.6g}] by {margin:.6g} ({limit.source})."
        )
    else:
        which = (
            f"the lower stop at t = {t_low:.4g} s"
            if low - limit.lower < limit.upper - high
            else f"the upper stop at t = {t_high:.4g} s"
        )
        summary = (
            f"{limit.joint} passes {which}: it travels {low:.6g} to {high:.6g} against "
            f"[{limit.lower:.6g}, {limit.upper:.6g}], by {-margin:.6g} ({limit.source})."
        )
    return TravelReport(
        joint=limit.joint,
        minimum=low,
        maximum=high,
        at_minimum_s=t_low,
        at_maximum_s=t_high,
        limit=limit,
        outcome=Outcome.PASSED if passed else Outcome.FAILED,
        margin=margin,
        summary=summary,
    )


# -- lock -----------------------------------------------------------------------


@dataclass(frozen=True)
class LockReport:
    samples: int
    #: The first input angle `four_bar` refused at, in degrees, with its words. None when
    #: every sample assembled.
    locked_at_deg: float | None
    lock_reason: str
    #: Smallest transmission angle seen, degrees in [0, 90]. An upper bound on the true
    #: minimum, because it is sampled. None when not one sample assembled.
    minimum_transmission_deg: float | None
    at_input_deg: float | None
    required_deg: float
    source: str
    outcome: Outcome
    summary: str


def transmission_angle_deg(coupler_rad: float, output_rad: float) -> float:
    """The acute angle between coupler and output, degrees in [0, 90].

    Folded to the acute side because 30 and 150 degrees transmit equally badly: what
    matters is how far the two links are from collinear.
    """
    angle = abs(math.degrees(coupler_rad - output_rad)) % 180.0
    return min(angle, 180.0 - angle)


def lock_sweep(
    *,
    ground_mm: float,
    input_mm: float,
    coupler_mm: float,
    output_mm: float,
    start_deg: float,
    end_deg: float,
    samples: int,
    minimum_transmission_deg: float,
    source: str,
    crossed: bool = False,
) -> LockReport:
    """Walk a four-bar's input through a range and report whether it locks or binds."""
    if samples < 2:
        raise MechanismError(f"A lock sweep needs at least 2 samples, got {samples}.")
    if not source.strip():
        raise MechanismError(
            "The minimum transmission angle has no source. It is a design criterion; name "
            "the document or the engineer it came from."
        )
    if not 0.0 < minimum_transmission_deg < 90.0:
        raise MechanismError(
            f"A minimum transmission angle of {minimum_transmission_deg} degrees is not a "
            "criterion. Give an angle strictly between 0 and 90."
        )
    worst: float | None = None
    worst_at: float | None = None
    locked_at: float | None = None
    reason = ""
    for i in range(samples):
        degrees = start_deg + (end_deg - start_deg) * i / (samples - 1)
        try:
            state = four_bar(
                ground_mm=ground_mm,
                input_mm=input_mm,
                coupler_mm=coupler_mm,
                output_mm=output_mm,
                angle_rad=math.radians(degrees),
                crossed=crossed,
            )
        except MechanismError as exc:
            locked_at, reason = degrees, str(exc)
            break
        mu = transmission_angle_deg(state.coupler_rad, state.output_rad)
        if worst is None or mu < worst:
            worst, worst_at = mu, degrees

    if locked_at is not None:
        outcome = Outcome.FAILED
        summary = f"The linkage locks at input {locked_at:.4g} degrees: {reason}"
    elif worst is not None and worst < minimum_transmission_deg:
        outcome = Outcome.FAILED
        summary = (
            f"The transmission angle falls to {worst:.3g} degrees at input {worst_at:.4g} "
            f"degrees, below the {minimum_transmission_deg:.3g} required ({source})."
        )
    else:
        outcome = Outcome.PASSED
        summary = (
            f"No lock over {start_deg:.4g} to {end_deg:.4g} degrees; the smallest "
            f"transmission angle seen is {worst:.3g} degrees at {samples} samples, an upper "
            f"bound on the true minimum, against {minimum_transmission_deg:.3g} ({source})."
        )
    return LockReport(
        samples=samples,
        locked_at_deg=locked_at,
        lock_reason=reason,
        minimum_transmission_deg=worst,
        at_input_deg=worst_at,
        required_deg=minimum_transmission_deg,
        source=source,
        outcome=outcome,
        summary=summary,
    )


# -- swept space ----------------------------------------------------------------

#: Given the poses a body takes, the volume of the union of its shape at those poses.
UnionMeasurer = Callable[[Sequence[Frame]], float]


@dataclass(frozen=True)
class SweptVolume:
    body: str
    samples: int
    #: A lower bound on the true swept volume, mm3; see the module docstring.
    lower_bound_mm3: float
    method: str


def swept_volume(path: MotionPath, body: str, union: UnionMeasurer) -> SweptVolume:
    """The volume of the union of a body's sampled poses: a lower bound on what it sweeps."""
    frames = path.motion(body).frames
    volume = float(union(frames))
    if not math.isfinite(volume) or volume < 0.0:
        raise MechanismError(
            f"The union measurer returned {volume!r} mm3 for {body!r}. A volume is finite "
            "and not negative."
        )
    return SweptVolume(
        body=body,
        samples=len(frames),
        lower_bound_mm3=volume,
        method=(
            f"union of the body placed at {len(frames)} sampled poses; the space swept "
            "between two samples is not in it, so this is a lower bound"
        ),
    )


@dataclass(frozen=True)
class SweptEnvelope:
    body: str
    samples: int
    minimum_mm: Vec3
    maximum_mm: Vec3
    method: str


def swept_envelope(path: MotionPath, body: str, points_mm: Sequence[Vec3]) -> SweptEnvelope:
    """The axis-aligned box of `points_mm` (body frame) over every sampled pose."""
    if not points_mm:
        raise MechanismError(
            f"No points were given for {body!r}. Pass the corners of the part's own "
            "bounding box, in the body frame."
        )
    frames = path.motion(body).frames
    placed = [frame.point(p) for frame in frames for p in points_mm]
    low = tuple(min(p[k] for p in placed) for k in range(3))
    high = tuple(max(p[k] for p in placed) for k in range(3))
    return SweptEnvelope(
        body=body,
        samples=len(frames),
        minimum_mm=(low[0], low[1], low[2]),
        maximum_mm=(high[0], high[1], high[2]),
        method=(
            f"{len(points_mm)} body points placed at {len(frames)} sampled poses; a point "
            "between two samples can reach outside this box"
        ),
    )


def occt_union_volume(shape: Any) -> UnionMeasurer:
    """A `UnionMeasurer` for one OCCT shape, given in the body frame.

    Each pose is a `gp_Trsf` built from the frame's row-major rotation and origin, the
    shape `app.assembly.clash` places an occurrence with. The copies are fused one at a
    time and the result integrated.
    """

    def measure(frames: Sequence[Frame]) -> float:
        from app.kernel.occt.binding import symbol
        from app.kernel.occt.metrology import volume_mm3

        if not frames:
            raise MechanismError("A swept volume needs at least one pose.")
        union: Any = None
        for frame in frames:
            r, o = frame.rotation, frame.origin_mm
            transform = symbol("gp_Trsf")()
            transform.SetValues(
                r[0], r[1], r[2], o[0],
                r[3], r[4], r[5], o[1],
                r[6], r[7], r[8], o[2],
            )
            placed = symbol("BRepBuilderAPI_Transform")(shape, transform, True).Shape()
            union = placed if union is None else symbol("BRepAlgoAPI_Fuse")(union, placed).Shape()
        return volume_mm3(union)

    return measure


__all__ = [
    "LockReport",
    "SweptEnvelope",
    "SweptVolume",
    "TravelLimit",
    "TravelReport",
    "UnionMeasurer",
    "coordinate_extremes",
    "lock_sweep",
    "occt_union_volume",
    "swept_envelope",
    "swept_volume",
    "transmission_angle_deg",
    "travel",
]
