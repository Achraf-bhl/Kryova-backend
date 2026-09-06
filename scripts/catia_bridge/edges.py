"""What an edge is, from the three points CATIA will actually give us.

`Measurable` over COM answers `Length` and `Radius` for an edge and refuses
everything else this bridge asked it: `GetCOG` raises E_NOTIMPL on every edge
(measured on V5-R33, 2026-09-06), and `GetDirection`/`GetPointsOnCurve` take
an out-array that pywin32 hands over by value, so they "succeed" and leave the
zeros untouched. `catia_list_edges` was built on those calls and reported
`kind: "unknown"` with no midpoint for every edge of every part -- which made
its `kind` filter a no-op and left the agent nothing to find the vertical
edges with.

What does work is `vba.edge_map`: `GetPointsOnCurve` run *inside* CATIA, one
Evaluate for the whole part, giving (start, middle, end) per edge. `_select_edges`
has classified edges from those three points since the fillet tool was made to
work, so this module is that arithmetic taken out of a closure and shared:
the list tool and the fillet selector must agree on what "vertical" means, or
listing shows one set of edges and rounding touches another.

Everything here is pure -- three points in, a fact out -- so it is tested
offline against edges whose answer is known.
"""

from __future__ import annotations

import math
from typing import Final

Point = tuple[float, float, float]
Triple = tuple[Point, Point, Point]

#: Machine coordinates from CATIA are exact, not measured, so a tight tolerance
#: is right: a "vertical" edge is vertical to the last bit, and one that is
#: 1e-3 off is a genuinely tilted edge that must not be filleted as vertical.
TOLERANCE: Final = 1e-6

#: The orientation words `catia_fillet edges=` takes and `catia_list_edges
#: kind=` lists. `convex`/`concave` are in the shared vocabulary too and are
#: deliberately absent here: they need the solid, not the curve, and nothing
#: in this bridge measures them yet -- a caller asking for them is told so.
ORIENTATIONS: Final = ("vertical", "horizontal", "top", "bottom")


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _norm(v: Point) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _cross(a: Point, b: Point) -> Point:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def orientations(triple: Triple, z_top: float, z_bottom: float) -> frozenset[str]:
    """Which of `ORIENTATIONS` this edge is, given the part's z extent.

    The rules are the ones `_select_edges` has applied since it was verified on
    the seat, moved here with one tightening (below):

    * `top` / `bottom`: all three points on the part's highest / lowest z.
    * `horizontal`: start, middle and end at one z (a horizontal arc counts).
    * `vertical`: start, middle and end share x and y, and start and end differ
      in z -- a straight edge along Z. `_select_edges` tested only the ends,
      which let a vertical semicircle (the edge a bore leaves on a side face)
      into "the vertical edges"; the middle point is the one-line difference.

    An edge can be several at once: the top rim of a block is `top` and
    `horizontal`. A slanted edge is none.
    """
    start, middle, end = triple
    points = (start, middle, end)
    found: set[str] = set()
    if all(abs(p[2] - z_top) < TOLERANCE for p in points):
        found.add("top")
    if all(abs(p[2] - z_bottom) < TOLERANCE for p in points):
        found.add("bottom")
    if abs(start[2] - end[2]) < TOLERANCE and abs(start[2] - middle[2]) < TOLERANCE:
        found.add("horizontal")
    if (
        all(abs(p[0] - start[0]) < TOLERANCE for p in points)
        and all(abs(p[1] - start[1]) < TOLERANCE for p in points)
        and abs(start[2] - end[2]) > TOLERANCE
    ):
        found.add("vertical")
    return frozenset(found)


def z_extent(triples: list[Triple] | tuple[Triple, ...]) -> tuple[float, float]:
    """(z_top, z_bottom) over every point of every edge."""
    z_values = [p[2] for triple in triples for p in triple]
    return max(z_values), min(z_values)


def is_linear(triple: Triple) -> bool:
    """Straight iff the middle point lies on the chord.

    The deviation is the distance from the middle point to the chord, and it
    is tested absolutely, against the same 1e-6 mm as everything else here: the
    points are CATIA's exact machine coordinates, so a straight edge deviates
    by floating-point noise (~1e-13 mm even on a metre-long edge) and a curved
    one deviates by its sagitta. A tolerance scaled by the edge's length was
    tried first and could not be justified -- it loosens the test exactly where
    the noise does not grow, and the shallowest arc worth calling curved (a
    10 m radius over a 50 mm edge) still bulges 0.03 mm, four orders above
    this.
    """
    start, middle, end = triple
    chord = _sub(end, start)
    span = _norm(chord)
    if span < TOLERANCE:
        # Closed curve: start and end coincide, and a straight edge of zero
        # length is not an edge CATIA would report.
        return False
    deviation = _norm(_cross(chord, _sub(middle, start))) / span
    return deviation < TOLERANCE


def circle_through(triple: Triple) -> tuple[float, float] | None:
    """(radius, arc length) of the circular arc through three points, or None.

    `GetPointsOnCurve`'s middle point is at the curve's parametric midpoint,
    which on a circle is the arc's midpoint, so the two halves subtend equal
    angles and the length is four times the radius times half the angle of
    one half. That also makes a *major* arc come out right -- each half is
    below a semicircle even when the whole is not.

    A closed circle arrives with start == end and the middle point diametrically
    opposite; that case is handled first because the general formula divides by
    the chord.

    None when the three points are collinear (a straight edge) -- the caller
    asks `is_linear` first, but a caller that does not must not get a division
    by zero dressed up as a radius.
    """
    start, middle, end = triple
    if _norm(_sub(end, start)) < TOLERANCE:
        radius = _norm(_sub(middle, start)) / 2.0
        return (radius, 2.0 * math.pi * radius) if radius > TOLERANCE else None
    a = _norm(_sub(middle, start))
    b = _norm(_sub(end, middle))
    c = _norm(_sub(end, start))
    twice_area = _norm(_cross(_sub(middle, start), _sub(end, start)))
    if twice_area < TOLERANCE * max(1.0, c):
        return None
    radius = (a * b * c) / (2.0 * twice_area)
    half = math.asin(min(1.0, a / (2.0 * radius)))
    return radius, 4.0 * radius * half


def straight_length(triple: Triple) -> float:
    start, _, end = triple
    return _norm(_sub(end, start))


#: The refusal for `convex` / `concave`, shared by the list tool and the fillet
#: selector so both say the same thing. Both words are in the vocabulary the
#: schemas advertise, and an agent that asks for them deserves the reason and
#: the alternative rather than an empty list that reads as "there are none".
UNMEASURED_CONVEXITY: Final = (
    "Whether an edge is convex or concave is not measured by this bridge yet, so "
    "'convex' and 'concave' cannot be resolved on this seat. Use 'vertical', "
    "'horizontal', 'top' or 'bottom', or name the edges by id with catia_fillet_edges "
    "after reading them with catia_list_edges."
)


def describe(
    triple: Triple, z_top: float, z_bottom: float, *, radius_mm: float | None = None
) -> dict[str, object]:
    """The facts `catia_list_edges` reports for one edge, minus its id.

    `kind` is `linear`, `circular` or `other`. Three points cannot tell a
    circle from a spline -- any three define *some* circle -- so a curved edge
    is `circular` only when the caller passes the radius CATIA itself
    answered for it (`Measurable.Radius`, which does work over COM and refuses
    on anything that is not an arc). Without one a curved edge is `other`, and
    its length is then the chord with `length_is_chord: true` rather than a
    number that looks measured and is not.

    Where CATIA gave a radius the arc length still comes from the three points
    -- they and the radius describe the same circle, and the points also say
    how much of it the edge covers, which the radius alone does not.
    """
    facts: dict[str, object] = {
        "midpoint": [round(v, 4) for v in triple[1]],
        "orientation": sorted(orientations(triple, z_top, z_bottom)),
    }
    if is_linear(triple):
        facts["kind"] = "linear"
        facts["length_mm"] = round(straight_length(triple), 4)
        return facts
    arc = circle_through(triple) if radius_mm is not None else None
    if radius_mm is None or arc is None:
        facts["kind"] = "other"
        facts["length_mm"] = round(straight_length(triple), 4)
        facts["length_is_chord"] = True
        return facts
    _, length = arc
    facts["kind"] = "circular"
    facts["radius_mm"] = round(radius_mm, 4)
    facts["length_mm"] = round(length, 4)
    return facts
