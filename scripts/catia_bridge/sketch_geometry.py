"""The 2D maths behind the sketch-editing tools, with no COM in sight.

CATIA's `Factory2D` can *create* geometry — lines, circles, splines — and that
is all it can do. Corner, chamfer, trim, offset, mirror, translate, rotate,
scale and pattern are Sketcher commands with no automation entry point at all;
the interactive user gets them, a script does not. So the bridge computes them
and writes the result back through the create-and-move calls that do exist.

Keeping that arithmetic here, away from COM, buys the one thing the rest of the
COM layer cannot have: tests. A fillet tangency or a mirrored arc's sweep is
either right or wrong on any machine, and `test_sketch_geometry.py` checks it on
Linux. What stays unverified until Windows is only the binding — which element
got moved — not whether the numbers are correct.

Angles are radians throughout, because that is what `Factory2D` takes; the
degrees in the tool schemas are converted at the boundary in `com/sketch_edit`.
Lengths are millimetres, like everything else in this system.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

#: Below this, two lengths or coordinates are the same number. Sketches are in
#: millimetres and CATIA itself resolves to about 1e-6 mm, so this is a little
#: looser than the kernel rather than an arbitrary epsilon.
TOLERANCE = 1e-9

Point = tuple[float, float]


class SketchGeometryError(ValueError):
    """A construction that has no answer, phrased for whoever asked for it.

    Separate from `CatiaOperationError` so this module stays free of the bridge's
    imports; `com/sketch_edit` translates it at the boundary. The message is
    written for the model that called the tool, not for a log.
    """


@dataclass(frozen=True)
class Segment:
    """A straight sketch element, oriented start → end."""

    start: Point
    end: Point

    @property
    def direction(self) -> Point:
        return (self.end[0] - self.start[0], self.end[1] - self.start[1])

    @property
    def length(self) -> float:
        return math.hypot(*self.direction)


@dataclass(frozen=True)
class Arc:
    """A circular sketch element, swept anticlockwise from start to end.

    A full circle is the case where the two angles are equal; CATIA models it
    that way too, which is why this is one type rather than two.
    """

    centre: Point
    radius: float
    start_angle: float = 0.0
    end_angle: float = 2 * math.pi

    @property
    def closed(self) -> bool:
        return abs((self.end_angle - self.start_angle) % (2 * math.pi)) < TOLERANCE


Element = Segment | Arc


# -- intersections -------------------------------------------------------------


def line_intersection(first: Segment, second: Segment) -> Point:
    """Where the two *infinite* lines cross.

    Infinite on purpose: trimming and filleting both routinely work on segments
    drawn short of their corner, and extending them to meet is the whole point
    of the operation. Refusing because the drawn segments happen not to overlap
    would reject the commonest real case.
    """
    (x1, y1), (x2, y2) = first.start, first.end
    (x3, y3), (x4, y4) = second.start, second.end

    denominator = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(denominator) < TOLERANCE:
        raise SketchGeometryError(
            "Those two elements are parallel, so they never meet. This operation "
            "needs a corner between them."
        )
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / denominator
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def _unit_away_from(corner: Point, segment: Segment) -> tuple[Point, bool]:
    """The unit vector from `corner` along `segment`, and whether that is start→end.

    A corner operation keeps the arm of each element that runs *away* from the
    corner and discards the stub on the other side. Which endpoint is the stub
    is not something the caller can be asked for — it depends on where the two
    elements happen to cross — so it is measured: the endpoint further from the
    corner is the one that survives.
    """
    to_start = math.dist(corner, segment.start)
    to_end = math.dist(corner, segment.end)
    far = segment.end if to_end >= to_start else segment.start
    length = math.dist(corner, far)
    if length < TOLERANCE:
        raise SketchGeometryError(
            "That element has zero length at the corner, so it has no direction "
            "to work from."
        )
    unit = ((far[0] - corner[0]) / length, (far[1] - corner[1]) / length)
    return unit, to_end >= to_start


def _corner_frame(first: Segment, second: Segment) -> tuple[Point, Point, Point, float]:
    """The corner point, both outward unit vectors, and the angle between them."""
    corner = line_intersection(first, second)
    u, _ = _unit_away_from(corner, first)
    v, _ = _unit_away_from(corner, second)
    cosine = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
    angle = math.acos(cosine)
    if angle < 1e-6 or abs(angle - math.pi) < 1e-6:
        raise SketchGeometryError(
            "Those two elements are in line with each other, so there is no corner "
            "between them to work on."
        )
    return corner, u, v, angle


def _retrim(segment: Segment, corner: Point, tangent: Point) -> Segment:
    """`segment` with the endpoint nearest the corner pulled back to `tangent`.

    Orientation is preserved rather than normalised. A sketch element's start
    and end are what its constraints and its dimensions are attached to, so
    quietly reversing one detaches them.
    """
    if math.dist(corner, segment.end) >= math.dist(corner, segment.start):
        return replace(segment, start=tangent)
    return replace(segment, end=tangent)


# -- corner and chamfer --------------------------------------------------------


@dataclass(frozen=True)
class CornerResult:
    """A fillet arc and the two elements trimmed back to touch it."""

    arc: Arc
    first: Segment
    second: Segment
    corner: Point


def corner(first: Segment, second: Segment, radius: float) -> CornerResult:
    """Round the corner between two straight elements with a tangent arc.

    The centre sits on the bisector at `radius / sin(θ/2)` from the corner and
    the tangent points at `radius / tan(θ/2)` along each arm — the standard
    construction, and exactly tangent rather than approximately so, which
    matters because a sketch that is only nearly tangent produces a visible
    facet on the padded solid.
    """
    if radius <= 0:
        raise SketchGeometryError("A corner radius must be greater than zero.")

    point, u, v, angle = _corner_frame(first, second)
    half = angle / 2.0
    trim_distance = radius / math.tan(half)
    centre_distance = radius / math.sin(half)

    bisector_x, bisector_y = u[0] + v[0], u[1] + v[1]
    bisector_length = math.hypot(bisector_x, bisector_y)
    bisector = (bisector_x / bisector_length, bisector_y / bisector_length)

    # Each element must reach at least `trim_distance` past the corner on the
    # arm that survives. Measured from the corner rather than from the drawn
    # length, because an element drawn short of the corner gets extended to it
    # and one drawn past it has a stub that is about to be cut off; neither
    # length is the one the fillet actually consumes.
    for element, name in ((first, "first"), (second, "second")):
        arm = max(math.dist(point, element.start), math.dist(point, element.end))
        if trim_distance > arm + TOLERANCE:
            raise SketchGeometryError(
                f"A radius of {radius:g} mm rounds {trim_distance:.3g} mm back along "
                f"each element, and the {name} one only runs {arm:.3g} mm from the "
                "corner. Use a smaller radius."
            )

    centre = (
        point[0] + bisector[0] * centre_distance,
        point[1] + bisector[1] * centre_distance,
    )
    first_tangent = (point[0] + u[0] * trim_distance, point[1] + u[1] * trim_distance)
    second_tangent = (point[0] + v[0] * trim_distance, point[1] + v[1] * trim_distance)

    start_angle = math.atan2(first_tangent[1] - centre[1], first_tangent[0] - centre[0])
    end_angle = math.atan2(second_tangent[1] - centre[1], second_tangent[0] - centre[0])
    # CATIA sweeps anticlockwise. Taking the tangent points in the order the
    # caller named the elements would draw the complementary arc — the long way
    # round the circle — whenever that order happens to be clockwise.
    if (end_angle - start_angle) % (2 * math.pi) > math.pi:
        start_angle, end_angle = end_angle, start_angle

    return CornerResult(
        arc=Arc(centre=centre, radius=radius, start_angle=start_angle, end_angle=end_angle),
        first=_retrim(first, point, first_tangent),
        second=_retrim(second, point, second_tangent),
        corner=point,
    )


@dataclass(frozen=True)
class ChamferResult:
    """A chamfer line and the two elements trimmed back to its ends."""

    line: Segment
    first: Segment
    second: Segment
    corner: Point
    first_length: float
    second_length: float


def chamfer(
    first: Segment,
    second: Segment,
    length: float,
    *,
    angle_deg: float | None = None,
    second_length: float | None = None,
) -> ChamferResult:
    """Cut the corner off with a straight line.

    Two ways to say the same thing, because drawings specify it both ways: a
    length on each element, or a length and the angle the chamfer makes with the
    first. The second form is solved with the sine rule rather than approximated
    — a chamfer dimensioned by angle and mis-set by a degree is a part that
    fails inspection while looking right on screen.
    """
    if length <= 0:
        raise SketchGeometryError("A chamfer length must be greater than zero.")

    point, u, v, corner_angle = _corner_frame(first, second)

    if second_length is not None:
        if second_length <= 0:
            raise SketchGeometryError("The second chamfer length must be greater than zero.")
        other = float(second_length)
    else:
        alpha = math.radians(45.0 if angle_deg is None else float(angle_deg))
        remaining = math.pi - corner_angle - alpha
        if remaining <= 1e-6:
            limit = math.degrees(math.pi - corner_angle)
            raise SketchGeometryError(
                f"A {math.degrees(alpha):g}° chamfer does not close on a corner of "
                f"{math.degrees(corner_angle):g}°. The angle must stay below {limit:g}°."
            )
        other = length * math.sin(alpha) / math.sin(remaining)

    first_tangent = (point[0] + u[0] * length, point[1] + u[1] * length)
    second_tangent = (point[0] + v[0] * other, point[1] + v[1] * other)

    for element, needed in ((first, length), (second, other)):
        if needed > element.length + TOLERANCE:
            raise SketchGeometryError(
                f"The chamfer needs {needed:.3g} mm of an element that is only "
                f"{element.length:.3g} mm long. Use a shorter chamfer."
            )

    return ChamferResult(
        line=Segment(first_tangent, second_tangent),
        first=_retrim(first, point, first_tangent),
        second=_retrim(second, point, second_tangent),
        corner=point,
        first_length=length,
        second_length=other,
    )


# -- trim ----------------------------------------------------------------------


def trim(first: Segment, second: Segment, keep: str = "both") -> tuple[Segment, Segment]:
    """Pull both elements to where they cross — or extend them until they do.

    One operation for trimming and extending because the arithmetic is the same
    and the user's intent is the same: make these two meet. Which of the two it
    turns out to be depends only on whether the crossing falls inside the drawn
    span, and asking the caller to know that in advance would be asking them to
    do the calculation this function exists to do.

    When the crossing falls *inside* an element, one of its two halves has to
    go, and the interactive Sketcher settles that by asking which side you
    clicked. There is no click here, so **the longer portion survives** — the
    stub sticking out past a corner is what someone drawing an over-long
    contour meant to lose.
    """
    if keep not in {"both", "first", "second"}:
        raise SketchGeometryError(
            f"{keep!r} is not a trim mode. Use 'both', 'first' or 'second'."
        )
    point = line_intersection(first, second)
    trimmed_first = _retrim(first, point, point) if keep in {"both", "first"} else first
    trimmed_second = _retrim(second, point, point) if keep in {"both", "second"} else second
    return trimmed_first, trimmed_second


# -- offset --------------------------------------------------------------------


def offset(element: Element, distance: float, *, reverse: bool = False) -> Element:
    """A parallel copy at `distance`.

    A line moves along its own normal; a circle changes radius. The sign
    convention for a line is the left-hand normal of its start→end direction,
    which is arbitrary but stable — `reverse` is there precisely because no
    convention matches what every caller pictures.
    """
    signed = -float(distance) if reverse else float(distance)

    if isinstance(element, Arc):
        radius = element.radius + signed
        if radius <= TOLERANCE:
            raise SketchGeometryError(
                f"Offsetting a radius of {element.radius:g} mm inwards by "
                f"{abs(signed):g} mm collapses it to nothing. Offset outwards, or by "
                "less than the radius."
            )
        return replace(element, radius=radius)

    length = element.length
    if length < TOLERANCE:
        raise SketchGeometryError("A zero-length element has no direction to offset along.")
    dx, dy = element.direction
    normal = (-dy / length, dx / length)
    return Segment(
        (element.start[0] + normal[0] * signed, element.start[1] + normal[1] * signed),
        (element.end[0] + normal[0] * signed, element.end[1] + normal[1] * signed),
    )


# -- transforms ----------------------------------------------------------------


@dataclass(frozen=True)
class Transform:
    """A 2D affine map, as the six numbers that define it.

        x' = a·x + c·y + e
        y' = b·x + d·y + f

    Kept as one type so translate, rotate, scale and mirror all reach the
    element-mapping code by the same path; a pattern is then simply a list of
    these, and every one of the five tools shares a single tested implementation.
    """

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0
    #: Whether the map reverses orientation. A mirrored arc is swept the other
    #: way round, so this is not cosmetic — it decides which of two arcs is drawn.
    flips: bool = False

    def apply(self, point: Point) -> Point:
        x, y = point
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)

    @property
    def scale(self) -> float:
        """The uniform scale factor. Every transform built here is conformal."""
        return math.hypot(self.a, self.b)

    @property
    def rotation(self) -> float:
        """The angle the map turns a direction through."""
        return math.atan2(self.b, self.a)


def translation(offset_uv: Point) -> Transform:
    return Transform(e=float(offset_uv[0]), f=float(offset_uv[1]))


def rotation(centre: Point, angle: float) -> Transform:
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    cx, cy = float(centre[0]), float(centre[1])
    return Transform(
        a=cos_a,
        b=sin_a,
        c=-sin_a,
        d=cos_a,
        e=cx - cx * cos_a + cy * sin_a,
        f=cy - cx * sin_a - cy * cos_a,
    )


def scaling(centre: Point, factor: float) -> Transform:
    if factor <= 0:
        raise SketchGeometryError("A scale factor must be greater than zero.")
    cx, cy = float(centre[0]), float(centre[1])
    return Transform(
        a=factor, d=factor, e=cx * (1.0 - factor), f=cy * (1.0 - factor)
    )


def reflection(axis: Segment) -> Transform:
    """Mirror about the infinite line through `axis`."""
    length = axis.length
    if length < TOLERANCE:
        raise SketchGeometryError(
            "The mirror axis has zero length, so it does not define a line."
        )
    dx, dy = axis.direction[0] / length, axis.direction[1] / length
    ax, ay = axis.start
    # Reflection about a line through (ax, ay) with unit direction (dx, dy):
    # translate to the origin, reflect about the direction, translate back.
    a = dx * dx - dy * dy
    b = 2 * dx * dy
    return Transform(
        a=a,
        b=b,
        c=b,
        d=-a,
        e=ax - a * ax - b * ay,
        f=ay - b * ax + a * ay,
        flips=True,
    )


def apply(element: Element, transform: Transform) -> Element:
    """Map an element through a transform, keeping arcs swept the right way.

    The arc case is the one worth reading. Rotating an arc turns its two angles
    with it, but *mirroring* one also swaps them: reflection reverses the
    direction of travel, and an arc whose angles are left in the original order
    after a mirror is the complementary arc — the piece of the circle that was
    not there. That is the bug this function exists to not have.
    """
    if isinstance(element, Segment):
        return Segment(transform.apply(element.start), transform.apply(element.end))

    centre = transform.apply(element.centre)
    radius = element.radius * transform.scale
    if element.closed:
        return Arc(centre=centre, radius=radius, start_angle=0.0, end_angle=2 * math.pi)

    if transform.flips:
        # A reflection maps the direction at angle θ to 2φ − θ, where φ is the
        # axis angle; `rotation` on a flipping transform returns exactly 2φ.
        mirrored_start = transform.rotation - element.start_angle
        mirrored_end = transform.rotation - element.end_angle
        return Arc(
            centre=centre,
            radius=radius,
            start_angle=mirrored_end,
            end_angle=mirrored_start,
        )

    turn = transform.rotation
    return Arc(
        centre=centre,
        radius=radius,
        start_angle=element.start_angle + turn,
        end_angle=element.end_angle + turn,
    )


# -- patterns ------------------------------------------------------------------


def rectangular_pattern(
    count: int,
    spacing: float,
    *,
    second_count: int = 1,
    second_spacing: float | None = None,
    direction: Point = (1.0, 0.0),
    second_direction: Point = (0.0, 1.0),
) -> list[Transform]:
    """Grid placements, excluding the original's own position.

    The original is left out because the caller already drew it: returning a
    transform for it would duplicate every element on top of itself, and a
    sketch with two coincident lines is one that pads with a visible seam and
    reports an ambiguous profile.
    """
    if count < 1 or second_count < 1:
        raise SketchGeometryError("A pattern needs at least one instance in each direction.")
    if spacing <= 0:
        raise SketchGeometryError("Pattern spacing must be greater than zero.")

    gap = float(spacing if second_spacing is None else second_spacing)
    if gap <= 0:
        raise SketchGeometryError("Pattern spacing must be greater than zero.")

    placements: list[Transform] = []
    for row in range(int(second_count)):
        for column in range(int(count)):
            if row == 0 and column == 0:
                continue
            placements.append(
                translation(
                    (
                        direction[0] * spacing * column + second_direction[0] * gap * row,
                        direction[1] * spacing * column + second_direction[1] * gap * row,
                    )
                )
            )
    return placements


def circular_pattern(
    count: int, centre: Point, *, total_angle: float = 2 * math.pi
) -> list[Transform]:
    """Placements around `centre`, excluding the original.

    A full circle divides by `count`, a partial arc by `count - 1`, so that both
    read the way a drawing does: eight holes on a full bolt circle are 45° apart,
    and eight holes across 90° put one at each end rather than leaving a gap the
    size of one spacing at the finish.
    """
    if count < 2:
        raise SketchGeometryError("A circular pattern needs at least two instances.")

    full = abs(total_angle % (2 * math.pi)) < TOLERANCE and abs(total_angle) > TOLERANCE
    step = total_angle / (count if full else count - 1)
    return [rotation(centre, step * index) for index in range(1, count)]
