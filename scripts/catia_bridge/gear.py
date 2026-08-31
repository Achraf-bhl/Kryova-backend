"""The outline of an involute spur gear, as a closed polyline.

Pure geometry, no CATIA: both the real backend and the mock draw or measure
from the same point list, and the offline tests check the math without a
workstation. The involute flanks are approximated with straight segments --
fine for CAD demonstration and FEA meshing, and honest about it in the tool's
description; nobody should hob a gear from this outline.

Conventions are ISO metric: pitch radius m*z/2, addendum m, dedendum 1.25*m.
For fewer than ~41 teeth at 20 degrees the root circle sits below the base
circle, and the flank is completed with a radial segment from root to base --
the standard simplification when the trochoidal root fillet is not modelled.
"""

from __future__ import annotations

import math

#: Straight segments approximating one involute flank.
FLANK_STEPS = 8
#: Straight segments approximating each tip and root arc.
ARC_STEPS = 3


def _involute_point(base_radius: float, t: float) -> tuple[float, float]:
    """The involute of a circle, unrolled by parameter t (radians of roll)."""
    return (
        base_radius * (math.cos(t) + t * math.sin(t)),
        base_radius * (math.sin(t) - t * math.cos(t)),
    )


def _rotate(point: tuple[float, float], angle: float) -> tuple[float, float]:
    c, s = math.cos(angle), math.sin(angle)
    return (point[0] * c - point[1] * s, point[0] * s + point[1] * c)


def outline(
    module_mm: float, teeth: int, pressure_angle_deg: float = 20.0
) -> list[tuple[float, float]]:
    """The gear's closed outline, counter-clockwise, centred on the origin.

    The last point is NOT repeated; the caller closes the loop.
    """
    if teeth < 6:
        raise ValueError("A gear needs at least 6 teeth; fewer undercuts to nothing.")
    module = float(module_mm)
    alpha = math.radians(float(pressure_angle_deg))

    pitch_r = module * teeth / 2.0
    base_r = pitch_r * math.cos(alpha)
    tip_r = pitch_r + module
    root_r = pitch_r - 1.25 * module
    if root_r <= 0:
        raise ValueError(
            f"module {module:g} mm with {teeth} teeth gives a non-positive root radius."
        )

    # The involute reaches the pitch circle after rolling through tan(alpha);
    # by then it has swung inv(alpha) = tan(alpha) - alpha around the centre.
    # Centring the tooth on the +x axis places the flank's pitch point at half
    # the tooth's angular thickness, pi/(2z), each side of it.
    inv_alpha = math.tan(alpha) - alpha
    half_tooth = math.pi / (2.0 * teeth) + inv_alpha

    t_tip = math.sqrt((tip_r / base_r) ** 2 - 1.0)
    t_start = 0.0 if root_r <= base_r else math.sqrt((root_r / base_r) ** 2 - 1.0)

    # One flank, root to tip, in the involute's own frame (starting on +x).
    flank: list[tuple[float, float]] = []
    if root_r < base_r:
        # Radial stub from the root circle up to the involute's start.
        flank.append((root_r, 0.0))
    for step in range(FLANK_STEPS + 1):
        t = t_start + (t_tip - t_start) * step / FLANK_STEPS
        flank.append(_involute_point(base_r, t))

    # Angles: the flank above starts at angle 0 and swings positive as it
    # rises. Rotate it so the tooth is symmetric about the tooth's centreline.
    rising = [_rotate(p, -half_tooth) for p in flank]
    # The falling flank is the mirror image across the centreline.
    falling = [(p[0], -p[1]) for p in reversed(rising)]

    tip_angle = math.atan2(rising[-1][1], rising[-1][0])
    root_angle = math.atan2(rising[0][1], rising[0][0])
    pitch_angle = 2.0 * math.pi / teeth

    # Per tooth, walking counter-clockwise: rising flank (root to tip), arc
    # across the tip, falling flank (tip to root), then the root arc over to
    # the next tooth.
    points: list[tuple[float, float]] = []
    for k in range(teeth):
        centre = k * pitch_angle
        points.extend(_rotate(p, centre) for p in rising)
        a0 = tip_angle + centre
        a1 = -tip_angle + centre  # falling flank's tip end, by mirror symmetry
        for step in range(1, ARC_STEPS):
            a = a0 + (a1 - a0) * step / ARC_STEPS
            points.append((tip_r * math.cos(a), tip_r * math.sin(a)))
        points.extend(_rotate(p, centre) for p in falling)
        b0 = -root_angle + centre
        b1 = root_angle + centre + pitch_angle
        for step in range(1, ARC_STEPS):
            b = b0 + (b1 - b0) * step / ARC_STEPS
            points.append((root_r * math.cos(b), root_r * math.sin(b)))
    return points


def area_mm2(points: list[tuple[float, float]]) -> float:
    """Shoelace area of the closed outline."""
    total = 0.0
    for index, (x0, y0) in enumerate(points):
        x1, y1 = points[(index + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def dimensions(module_mm: float, teeth: int) -> dict[str, float]:
    pitch_r = float(module_mm) * teeth / 2.0
    return {
        "pitch_diameter_mm": 2.0 * pitch_r,
        "tip_diameter_mm": 2.0 * (pitch_r + float(module_mm)),
        "root_diameter_mm": 2.0 * (pitch_r - 1.25 * float(module_mm)),
    }
