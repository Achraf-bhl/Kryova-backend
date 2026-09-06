"""The two planar closed chains that will not go away, solved exactly.

`kinematics.py` refuses a closed loop, and correctly: a loop's joint coordinates are not
independent, so no forward recursion reaches them. The general answer is a constrained
solver -- an engine's job. But two loops account for most of the mechanisms anyone
actually writes down, both have a closed-form solution, and both have derivatives that
can be obtained analytically rather than by differencing. So they are here, exactly,
with no engine.

* **Slider-crank** -- every reciprocating machine: a press, a compressor, a piston pump,
  a shear. `x = r*cos(theta) + sqrt(l^2 - r^2*sin^2(theta))` is exact, and both
  derivatives are the chain rule applied to it.
* **Four-bar** -- every linkage that swings: a suspension, a wiper, a toggle clamp, a
  quick-return. Solved by the vector loop, with velocity and acceleration from the
  differentiated loop equations, which are two 2x2 linear systems with the same matrix.

**Both refuse rather than approximate when the loop cannot close.** A slider-crank with
a rod shorter than its crank has no assembly at some angles; a four-bar can jam at a
dead centre where the Jacobian is singular. Returning a nearest-fit pose there would be
a mechanism that appears to work and physically locks -- which is one of the failures
master plan 9.3's "travel and lock checks" exists to find, so it is reported, not
smoothed over.

Angles are radians, lengths mm, per the package convention. The plane is xy with the
loop rotating about +z, which is the plane `Joint(axis=(0, 0, 1))` uses, so a closure
result drops into a mechanism definition without a frame change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.dynamics.errors import MechanismError


@dataclass(frozen=True)
class SliderState:
    """Where the slider is, and how fast, for one crank angle.

    `position_mm` is measured from the crank centre along the slide axis, so it is the
    distance from the main bearing to the wrist pin -- the number on the drawing.
    """

    angle_rad: float
    position_mm: float
    velocity_mm_s: float
    acceleration_mm_s2: float

    #: The connecting-rod angle from the slide axis, radians. What decides side thrust.
    rod_angle_rad: float


def slider_crank(
    *,
    crank_mm: float,
    rod_mm: float,
    angle_rad: float,
    rate_rad_s: float = 0.0,
    accel_rad_s2: float = 0.0,
    offset_mm: float = 0.0,
) -> SliderState:
    """Exact slider-crank kinematics, including the offset (desaxe) case.

    With `offset_mm = 0` this reduces to the textbook centred slider-crank

        x = r*cos(t) + sqrt(l^2 - r^2*sin^2(t))

    whose closed form is what `tests/test_dynamics_closures.py` checks against. The
    offset case is the same loop with the slide axis displaced, which is what a real
    press crank usually is -- it trades a symmetric stroke for a lower side thrust on
    the working stroke.

    Derivatives are analytic, not differenced. `rate_rad_s` and `accel_rad_s2` are the
    crank's, and the chain rule carries them to the slider, so a crank at constant speed
    still gives a slider with non-zero acceleration -- which is the entire reason a
    reciprocating machine has inertia loads at all.
    """
    if crank_mm <= 0.0 or rod_mm <= 0.0:
        raise MechanismError(
            f"A slider-crank needs positive links, got crank={crank_mm} mm and "
            f"rod={rod_mm} mm. The crank is half the stroke; the rod is the connecting "
            "rod's centre distance."
        )

    perpendicular = crank_mm * math.sin(angle_rad) - offset_mm
    discriminant = rod_mm * rod_mm - perpendicular * perpendicular
    if discriminant <= 0.0:
        raise MechanismError(
            f"The loop does not close at crank angle {math.degrees(angle_rad):.1f} deg: "
            f"the rod would have to span {abs(perpendicular):.3f} mm across the slide "
            f"but is only {rod_mm:.3f} mm long. The rod must exceed the crank plus the "
            f"offset -- here at least {crank_mm + abs(offset_mm):.3f} mm."
        )
    root = math.sqrt(discriminant)

    position = crank_mm * math.cos(angle_rad) + root

    # d/dtheta of the two terms, then the chain rule for time.
    d_perp = crank_mm * math.cos(angle_rad)
    d_root = -perpendicular * d_perp / root
    dx_dtheta = -crank_mm * math.sin(angle_rad) + d_root

    d2_perp = -crank_mm * math.sin(angle_rad)
    d2_root = (
        -(d_perp * d_perp + perpendicular * d2_perp) / root
        - (perpendicular * d_perp) * (perpendicular * d_perp) / (root**3)
    )
    d2x_dtheta2 = -crank_mm * math.cos(angle_rad) + d2_root

    velocity = dx_dtheta * rate_rad_s
    acceleration = d2x_dtheta2 * rate_rad_s * rate_rad_s + dx_dtheta * accel_rad_s2

    return SliderState(
        angle_rad=angle_rad,
        position_mm=position,
        velocity_mm_s=velocity,
        acceleration_mm_s2=acceleration,
        rod_angle_rad=math.asin(max(-1.0, min(1.0, perpendicular / rod_mm))),
    )


@dataclass(frozen=True)
class FourBarState:
    """The coupler and output angles of a four-bar, with both derivatives.

    Angles are absolute, measured from the ground link (+x), positive about +z.
    """

    input_rad: float
    coupler_rad: float
    output_rad: float
    coupler_rate_rad_s: float
    output_rate_rad_s: float
    coupler_accel_rad_s2: float
    output_accel_rad_s2: float

    #: Whether the assembly taken is the crossed branch. A four-bar has two solutions
    #: for every input angle and they are different machines; which one a linkage is in
    #: is decided at assembly and never changes, so it is reported rather than chosen
    #: silently each step.
    crossed: bool


def four_bar(
    *,
    ground_mm: float,
    input_mm: float,
    coupler_mm: float,
    output_mm: float,
    angle_rad: float,
    rate_rad_s: float = 0.0,
    accel_rad_s2: float = 0.0,
    crossed: bool = False,
) -> FourBarState:
    """Exact four-bar kinematics by the vector loop.

    The loop is `input + coupler = ground + output`, with the ground link along +x from
    the input pivot at the origin. Position comes from intersecting two circles;
    velocity and acceleration come from differentiating the loop equation once and
    twice, each a 2x2 linear solve sharing one matrix -- which is why the singular case
    (a dead centre, where coupler and output are collinear) is detected once, on the
    determinant, rather than three times.
    """
    for name, value in (
        ("ground", ground_mm),
        ("input", input_mm),
        ("coupler", coupler_mm),
        ("output", output_mm),
    ):
        if value <= 0.0:
            raise MechanismError(
                f"A four-bar needs four positive links; {name} is {value} mm. Give the "
                "pin-to-pin centre distance of each link."
            )

    # Position of the moving end of the input crank, and of the fixed output pivot.
    ax = input_mm * math.cos(angle_rad)
    ay = input_mm * math.sin(angle_rad)
    bx, by = ground_mm, 0.0

    dx, dy = bx - ax, by - ay
    span = math.hypot(dx, dy)
    if span > coupler_mm + output_mm or span < abs(coupler_mm - output_mm) or span == 0.0:
        raise MechanismError(
            f"The four-bar cannot be assembled at input angle "
            f"{math.degrees(angle_rad):.1f} deg: the coupler and output pivots are "
            f"{span:.3f} mm apart, and links of {coupler_mm:.3f} and {output_mm:.3f} mm "
            f"span between {abs(coupler_mm - output_mm):.3f} and "
            f"{coupler_mm + output_mm:.3f} mm. The linkage locks before this angle -- "
            "restrict the range, or change a link length."
        )

    # Circle-circle intersection: the coupler pin lies on both.
    a = (coupler_mm * coupler_mm - output_mm * output_mm + span * span) / (2.0 * span)
    height_sq = coupler_mm * coupler_mm - a * a
    height = math.sqrt(max(0.0, height_sq))
    mid_x = ax + a * dx / span
    mid_y = ay + a * dy / span
    sign = -1.0 if crossed else 1.0
    px = mid_x + sign * height * (dy / span)
    py = mid_y - sign * height * (dx / span)

    coupler_angle = math.atan2(py - ay, px - ax)
    output_angle = math.atan2(py - by, px - bx)

    # Loop:  input*e(i*t2) + coupler*e(i*t3) - output*e(i*t4) - ground = 0
    # d/dt:  coupler*w3*(-sin t3, cos t3) - output*w4*(-sin t4, cos t4)
    #          = -input*w2*(-sin t2, cos t2)
    s3, c3 = math.sin(coupler_angle), math.cos(coupler_angle)
    s4, c4 = math.sin(output_angle), math.cos(output_angle)
    s2, c2 = math.sin(angle_rad), math.cos(angle_rad)

    m11, m12 = -coupler_mm * s3, output_mm * s4
    m21, m22 = coupler_mm * c3, -output_mm * c4
    det = m11 * m22 - m12 * m21
    if abs(det) < 1e-12 * coupler_mm * output_mm:
        raise MechanismError(
            f"The four-bar is at a dead centre at input angle "
            f"{math.degrees(angle_rad):.1f} deg -- coupler and output links are "
            "collinear, so the output rate is not determined by the input rate. This is "
            "a lock, and it is a real property of these link lengths, not a numerical "
            "problem. Stop the range short of it, or change a link length."
        )

    rhs1 = input_mm * s2 * rate_rad_s
    rhs2 = -input_mm * c2 * rate_rad_s
    coupler_rate = (rhs1 * m22 - m12 * rhs2) / det
    output_rate = (m11 * rhs2 - rhs1 * m21) / det

    # Second derivative: same matrix, right-hand side carries the centripetal terms.
    rhs1 = (
        input_mm * s2 * accel_rad_s2
        + input_mm * c2 * rate_rad_s * rate_rad_s
        + coupler_mm * c3 * coupler_rate * coupler_rate
        - output_mm * c4 * output_rate * output_rate
    )
    rhs2 = (
        -input_mm * c2 * accel_rad_s2
        + input_mm * s2 * rate_rad_s * rate_rad_s
        + coupler_mm * s3 * coupler_rate * coupler_rate
        - output_mm * s4 * output_rate * output_rate
    )
    coupler_accel = (rhs1 * m22 - m12 * rhs2) / det
    output_accel = (m11 * rhs2 - rhs1 * m21) / det

    return FourBarState(
        input_rad=angle_rad,
        coupler_rad=coupler_angle,
        output_rad=output_angle,
        coupler_rate_rad_s=coupler_rate,
        output_rate_rad_s=output_rate,
        coupler_accel_rad_s2=coupler_accel,
        output_accel_rad_s2=output_accel,
        crossed=crossed,
    )


def grashof(
    *, ground_mm: float, input_mm: float, coupler_mm: float, output_mm: float
) -> str:
    """Which four-bar this is, in the words a mechanism designer uses.

    Grashof's condition -- shortest + longest <= the other two -- decides whether any
    link can fully rotate, which decides whether the linkage can be motor-driven at all.
    Returned as a phrase rather than a boolean because "crank-rocker" and
    "double-rocker" are different machines and the distinction is the useful half.
    """
    lengths = sorted([ground_mm, input_mm, coupler_mm, output_mm])
    shortest, second, third, longest = lengths
    if shortest + longest > second + third:
        return "non-Grashof: no link fully rotates, so every link is a rocker"
    if shortest == input_mm:
        return "Grashof crank-rocker: the input link fully rotates, the output rocks"
    if shortest == ground_mm:
        return "Grashof double-crank: both input and output fully rotate"
    if shortest == coupler_mm:
        return "Grashof double-rocker: the coupler fully rotates, neither pivot does"
    return "Grashof crank-rocker: the output link fully rotates, the input rocks"


__all__ = ["FourBarState", "SliderState", "four_bar", "grashof", "slider_crank"]
