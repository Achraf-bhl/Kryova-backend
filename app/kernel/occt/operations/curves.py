"""Wireframe: curves that live in 3D space rather than on a sketch plane.

The line between this module and `sketcher.py` is the one the registry draws, and it
decides where a new curve belongs: a sketch curve is drawn in 2D on a support and edited
in the Sketcher; a wireframe curve is built in 3D from references. **A helix cannot be
sketched** — it does not lie in a plane — which is exactly why this module exists, and
until it did, every curve in a design had to come from a planar sketch or a surface
boundary. A genuinely 3D path was not expressible at all.

The derived curves — section, intersection, extremum, projection, parallel, offset,
combine — are the associative ones: they are defined by other geometry rather than drawn,
so they follow it when it moves. That is what makes them worth more than drawing the same
shape by hand, and it is the same argument `feature#selector` makes about faces.

Four things here are decisions rather than convenience.

**A helix is a straight line in a cylinder's own coordinates**, and the parameterisation
is where it goes wrong. `Geom2d_Line` normalises the direction it is given, so travelling
`t` along the pcurve advances the angle by `t/√(1+k²)`, not by `t`. Sweeping `0 → 2πn`
therefore builds a helix of the right shape and the **wrong length** — measured 265.5 mm
where the closed form says 314.8, a 16% error that looks entirely plausible in a
screenshot. `_helix_span` is that factor, and the test checks the length against
`n·√(pitch² + (2πr)²)` rather than against a recorded number for exactly this reason.

**A point argument accepts a name or a literal.** The registry types these as name lists,
which is CATIA's model — a point is constructed first and then referred to. That is
honoured, and `[x, y, z]` is accepted beside it, because requiring four `catia_point_at`
calls before a four-point polyline adds ceremony without adding meaning. `as_point`
already accepts both spellings everywhere else in the kernel.

**A section and an intersection are the same algorithm asked two questions.** Both are
`BRepAlgoAPI_Section`; the difference is only what the second operand is — a plane for a
section, another element for an intersection — so they share an implementation rather
than drifting apart over what "extend" or an empty result means.

**An offset has a side, and the side is stated here rather than inherited.** OCCT decides
it from the wire's own winding for a planar offset and from a formula its documentation
describes with the opposite sign for a 3D one — neither of which is something the design
ever said. Both operations therefore build, measure which way the result went, and mirror
the distance when it went the other way, so `catia_curve_parallel` and
`catia_curve_offset_3d` mean what their docstrings say on any curve.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Final

from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt.binding import symbol
from app.kernel.occt.elements import axis_for, plane_frame
from app.kernel.occt.operations.context import (
    BuildContext,
    as_direction,
    as_point,
    as_positive_length,
    build_or_raise,
    feature_name,
)
from app.kernel.occt.reference import ReferencePoint
from app.kernel.occt.topology import EDGE, count, explore

HELIX = "catia_curve_helix"
CIRCLE = "catia_curve_circle"
POLYLINE = "catia_curve_polyline"
SPLINE = "catia_curve_spline"
SECTION = "catia_curve_section"
INTERSECT = "catia_curve_intersect"
EXTREMUM = "catia_curve_extremum"
LINE_BETWEEN = "catia_line_between"
LINE_DIRECTION = "catia_line_direction"
LINE_NORMAL = "catia_line_normal"
LINE_TANGENT = "catia_line_tangent"
PROJECT = "catia_curve_project"
PARALLEL = "catia_curve_parallel"
OFFSET_3D = "catia_curve_offset_3d"
COMBINE = "catia_curve_combine"

#: Below this a direction, a chord or a radius is treated as zero rather than normalised
#: into a division by zero.
MIN_LENGTH_MM: Final = 1e-9

#: How the circle kinds are spelled in the registry, and which this backend builds.
_CIRCLE_KINDS: Final[frozenset[str]] = frozenset({"centre_radius", "three_points"})

#: Below this ratio of least to greatest moment, a curve or a point set is treated as
#: lying on one line, which has no plane. Relative for the reason `least_spread_axis`
#: gives: an absolute threshold is wrong at both ends of the scale a machine spans.
_COPLANAR_DEFINITION: Final = 1e-9

#: How far off its support a curve may sit and still be offset within it, in mm. Loose
#: enough for a fitted curve's own approximation error, tight enough that a curve on the
#: wrong plane is caught rather than silently flattened onto this one.
_IN_PLANE_TOLERANCE_MM: Final = 1e-6


# -- curves drawn in space ----------------------------------------------------


def curve_helix(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A helix winding around an axis — the path of a thread, a spring, a spiral stair.

    The radius is not an argument: it is the distance from `start_point` to the axis,
    because a helix that began somewhere other than the point naming its start would be a
    different curve from the one asked for. `taper_deg` opens it into a cone, and the end
    radius is then `r + height·tan(taper)` exactly.
    """
    document = context.require_document()

    axis = axis_for(document, arguments.get("axis"), tool=HELIX)
    start = _point_named(document, arguments.get("start_point"), tool=HELIX, argument="start_point")
    pitch = as_positive_length(arguments.get("pitch_mm"), argument="pitch_mm", tool=HELIX)
    height = as_positive_length(arguments.get("height_mm"), argument="height_mm", tool=HELIX)

    taper = float(arguments.get("taper_deg") or 0.0)
    if not -89.0 < taper < 89.0:
        raise GeometryError(
            f"{HELIX} takes taper_deg between -89 and 89 degrees; got {taper}. At 90° the "
            "cone closes on itself and the helix has nowhere to wind."
        )

    frame, radius = _helix_frame(axis, start, tool=HELIX)
    if arguments.get("start_angle_deg"):
        frame = _turned_frame(frame, float(arguments["start_angle_deg"]))

    clockwise = arguments.get("clockwise") is not False
    edge = _helical_edge(frame, radius, pitch, height, taper, clockwise=clockwise)
    return _record(context, document, arguments, HELIX, edge, "helix")


def curve_circle(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A 3D circle or arc, from a centre and radius on a support, or through three points.

    **A 3D circle needs a support**, and that is not this backend being strict: through
    one centre at one radius there are infinitely many circles, one per plane. The
    three-point kind carries its own plane, which is why it is the only kind that does not
    ask for one.
    """
    document = context.require_document()

    kind = str(arguments.get("kind") or "centre_radius").lower()
    if kind not in _CIRCLE_KINDS:
        raise OperationNotSupported(
            f"{CIRCLE} of kind {kind!r}",
            "bitangent and tritangent circles are solved against curves they must touch, "
            "which is a constraint problem rather than a construction. "
            f"Available: {', '.join(sorted(_CIRCLE_KINDS))}",
        )

    if kind == "three_points":
        points = [
            _point_named(document, reference, tool=CIRCLE, argument="points")
            for reference in _references(arguments.get("points"), tool=CIRCLE, argument="points", minimum=3)
        ]
        circle = _circle_through(points[:3])
    else:
        support = arguments.get("support")
        if not support:
            raise GeometryError(
                f"{CIRCLE} of kind 'centre_radius' needs a support — the plane the circle "
                "lies on. Through one centre at one radius there is a circle in every "
                "plane, so without one there is no single answer."
            )
        frame = plane_frame(document, support, tool=CIRCLE)
        centre = _point_named(document, arguments.get("centre"), tool=CIRCLE, argument="centre")
        radius = as_positive_length(arguments.get("radius_mm"), argument="radius_mm", tool=CIRCLE)
        normal = frame.Direction()
        circle = symbol("gp_Circ")(
            symbol("gp_Ax2")(symbol("gp_Pnt")(*centre), normal), radius
        )

    start_angle = arguments.get("start_angle_deg")
    end_angle = arguments.get("end_angle_deg")
    if start_angle is None and end_angle is None:
        maker = symbol("BRepBuilderAPI_MakeEdge")(circle)
    else:
        first = math.radians(float(start_angle or 0.0))
        last = math.radians(float(end_angle if end_angle is not None else 360.0))
        if abs(last - first) < MIN_LENGTH_MM:
            raise GeometryError(
                f"{CIRCLE} was given a start and end angle that are the same, which is an "
                "arc of no length. Leave both out for a full circle."
            )
        maker = symbol("BRepBuilderAPI_MakeEdge")(circle, first, last)

    edge = build_or_raise(
        maker,
        tool=CIRCLE,
        detail="The circle could not be built as an edge; check the radius and the "
        "support plane.",
    )
    return _record(context, document, arguments, CIRCLE, edge, "circle")


def curve_polyline(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A 3D polyline through a list of points — straight segments, chained into one curve."""
    document = context.require_document()
    points = _point_list(document, arguments.get("points"), tool=POLYLINE, minimum=2)

    if arguments.get("radius_mm"):
        raise OperationNotSupported(
            f"{POLYLINE} with radius_mm",
            "rounding every corner of a 3D polyline is a fillet between successive "
            "segments, which needs the corner arc solved in the plane of each pair. Build "
            "the polyline and round the corners that matter with catia_curve_corner once "
            "it lands",
        )

    if arguments.get("closed"):
        points = [*points, points[0]]

    wire = symbol("BRepBuilderAPI_MakeWire")()
    for start, end in zip(points, points[1:], strict=False):
        if _distance_between(start, end) < MIN_LENGTH_MM:
            raise GeometryError(
                f"{POLYLINE} was given the same point twice in a row at "
                f"{[round(value, 4) for value in start]}, which is a segment of no "
                "length. Remove the repeat."
            )
        wire.Add(
            symbol("BRepBuilderAPI_MakeEdge")(
                symbol("gp_Pnt")(*start), symbol("gp_Pnt")(*end)
            ).Edge()
        )

    built = build_or_raise(
        wire,
        tool=POLYLINE,
        detail="The segments could not be chained into one curve; check that the points "
        "are given in order.",
    )
    return _record(context, document, arguments, POLYLINE, built, "polyline")


def curve_spline(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A 3D spline through a list of points, interpolating rather than approximating.

    **Through the points, not near them.** `GeomAPI_Interpolate` passes through every
    point given; `GeomAPI_PointsToBSpline` fits a curve that may miss them by up to its
    tolerance. For a design where the points were chosen deliberately — a section through
    a scanned surface, a set of clearance limits — a curve that misses them is a different
    curve, and no message would say so.
    """
    document = context.require_document()
    points = _point_list(document, arguments.get("points"), tool=SPLINE, minimum=2)
    closed = bool(arguments.get("closed"))

    if arguments.get("support"):
        raise OperationNotSupported(
            f"{SPLINE} with support",
            "holding a spline onto a surface constrains every interior point to lie on "
            "it, which is a fit rather than an interpolation. Interpolate the points "
            "here, then lay the result onto the surface with catia_curve_project",
        )

    array = symbol("TColgp_HArray1OfPnt")(1, len(points))
    for index, point in enumerate(points, start=1):
        array.SetValue(index, symbol("gp_Pnt")(*point))

    interpolator = symbol("GeomAPI_Interpolate")(array, closed, MIN_LENGTH_MM)
    start_tangent, end_tangent = arguments.get("start_tangent"), arguments.get("end_tangent")
    if start_tangent is not None or end_tangent is not None:
        if closed:
            raise GeometryError(
                f"{SPLINE} cannot take end tangents on a closed curve — a loop has no "
                "ends. Drop `closed`, or drop the tangents."
            )
        interpolator.Load(
            symbol("gp_Vec")(*as_direction(start_tangent, argument="start_tangent")),
            symbol("gp_Vec")(*as_direction(end_tangent, argument="end_tangent")),
            True,
        )

    try:
        interpolator.Perform()
        done = interpolator.IsDone()
    except Exception as exc:  # noqa: BLE001 - OCCT's Standard_Failure hierarchy
        raise GeometryError(
            f"{SPLINE} could not interpolate the points: {exc}. Two points at the same "
            "place, or a closed curve given fewer than three, are the usual causes."
        ) from exc
    if not done:
        raise GeometryError(
            f"{SPLINE} could not interpolate the {len(points)} points given. Check that "
            "no two of them coincide."
        )

    edge = build_or_raise(
        symbol("BRepBuilderAPI_MakeEdge")(interpolator.Curve()),
        tool=SPLINE,
        detail="The interpolated curve could not be made into an edge.",
    )
    return _record(context, document, arguments, SPLINE, edge, "spline")


# -- lines ---------------------------------------------------------------------
#
# A line is a curve, so these build edges and file them in the construction store beside
# the helices and the sections. That is not a filing decision: `catia_line_direction`
# exists to make a rotation axis or a sweep spine an actual object, and an object that
# lives somewhere the rest of the vocabulary cannot name would not do that job.


def line_between(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A line between two points, optionally pushed past either end.

    The extensions are what make this more than a segment: a rotation axis or a cutting
    line has to reach the geometry it acts on, and the two points that *define* it are
    usually inside the part rather than clear of it.
    """
    document = context.require_document()
    points = _point_list(document, arguments.get("points"), tool=LINE_BETWEEN, minimum=2)
    if len(points) != 2:
        raise GeometryError(
            f"{LINE_BETWEEN} joins exactly two points and was given {len(points)}. Use "
            "catia_curve_polyline for a run of them."
        )

    start, end = points
    span = _distance_between(start, end)
    if span < MIN_LENGTH_MM:
        raise GeometryError(
            f"{LINE_BETWEEN} was given the same point twice, so the line has no length "
            "and no direction. Name two different points."
        )

    along = (
        (end[0] - start[0]) / span,
        (end[1] - start[1]) / span,
        (end[2] - start[2]) / span,
    )
    back = float(arguments.get("extend_start_mm") or 0.0)
    forward = float(arguments.get("extend_end_mm") or 0.0)
    first = _stepped(start, along, -back)
    last = _stepped(end, along, forward)

    return _record(context, document, arguments, LINE_BETWEEN, _segment(first, last), "line")


def line_direction(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A line from a point, along a direction, for a length.

    `both_sides` runs the same length backwards as well, so the line is `2 × length_mm`
    long — the same reading `catia_pad`'s `symmetric` does not take, and deliberately: the
    registry's own wording is "extend the same length backwards too", which is an
    addition rather than a division.
    """
    document = context.require_document()
    origin = _point_named(document, arguments.get("point"), tool=LINE_DIRECTION, argument="point")
    along = _unit(as_direction(arguments.get("direction"), argument="direction"), tool=LINE_DIRECTION)
    length = as_positive_length(
        arguments.get("length_mm"), argument="length_mm", tool=LINE_DIRECTION
    )

    back = length if arguments.get("both_sides") else 0.0
    first = _stepped(origin, along, -back)
    last = _stepped(origin, along, length)
    return _record(context, document, arguments, LINE_DIRECTION, _segment(first, last), "line")


def line_normal(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A line standing off a surface at a point on it — where a hole would be drilled.

    **The normal is taken at the point, not at the face's centre.** On a flat wall the two
    are the same and the difference never shows; on a curved one, using the centre points
    the fastener somewhere nobody asked for, and the line still looks plausible.
    """
    from app.kernel.occt.operations.surfaces import named_geometry

    document = context.require_document()
    surface = named_geometry(document, arguments.get("surface"), tool=LINE_NORMAL, argument="surface")
    at = _point_named(document, arguments.get("point"), tool=LINE_NORMAL, argument="point")
    length = as_positive_length(arguments.get("length_mm"), argument="length_mm", tool=LINE_NORMAL)

    face, foot, normal = closest_face_normal(surface, at, tool=LINE_NORMAL)
    del face
    tip = _stepped(foot, normal, length)
    return _record(context, document, arguments, LINE_NORMAL, _segment(foot, tip), "normal")


def line_tangent(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A line tangent to a curve at a point on it — the local direction of travel."""
    from app.kernel.occt.operations.surfaces import named_geometry

    document = context.require_document()
    curve = named_geometry(document, arguments.get("curve"), tool=LINE_TANGENT, argument="curve")
    at = _point_named(document, arguments.get("point"), tool=LINE_TANGENT, argument="point")
    length = as_positive_length(arguments.get("length_mm"), argument="length_mm", tool=LINE_TANGENT)

    foot, tangent = closest_curve_tangent(curve, at, tool=LINE_TANGENT)
    tip = _stepped(foot, tangent, length)
    return _record(context, document, arguments, LINE_TANGENT, _segment(foot, tip), "tangent")


# -- curves derived from other geometry ---------------------------------------


def curve_section(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """The curve where a plane cuts through a surface or a solid.

    The first step when reverse-engineering a shape or fitting a mating part: it takes the
    *real* profile off the geometry rather than a profile someone drew to look like it.
    """
    from app.kernel.occt.operations.surfaces import named_geometry

    document = context.require_document()
    element = named_geometry(document, arguments.get("element"), tool=SECTION, argument="element")
    frame = plane_frame(document, arguments.get("plane"), tool=SECTION)

    cut = _section_of(element, symbol("gp_Pln")(frame), tool=SECTION)
    if not count(cut, EDGE):
        raise GeometryError(
            f"{SECTION} found nothing where {arguments.get('plane')!r} crosses "
            f"{arguments.get('element')!r} — the plane misses it entirely. Check the "
            "plane's offset against where the geometry actually sits."
        )
    return _record(context, document, arguments, SECTION, cut, "section")


def curve_intersect(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Where two elements cross: a curve between two surfaces, a point between curve and
    surface.

    The reliable way to find where two shapes actually meet rather than where they look
    like they meet. An empty result is **refused**, because "they do not touch" and "the
    intersection is empty" are the same sentence and only one of them is what the caller
    expected to hear.
    """
    from app.kernel.occt.operations.surfaces import named_geometry

    document = context.require_document()
    references = _references(
        arguments.get("elements"), tool=INTERSECT, argument="elements", minimum=2
    )
    if len(references) != 2:
        raise GeometryError(
            f"{INTERSECT} crosses exactly two elements and was given {len(references)}. "
            "Intersect them a pair at a time."
        )

    if arguments.get("extend"):
        raise OperationNotSupported(
            f"{INTERSECT} with extend",
            "extending two elements until they meet changes both of them, and by an "
            "amount nobody stated. Extend them deliberately with catia_extrapolate once "
            "it lands, or make them large enough to cross",
        )

    first, second = (
        named_geometry(document, reference, tool=INTERSECT, argument="elements")
        for reference in references
    )
    crossing = _section_of(first, second, tool=INTERSECT)
    if not count(crossing, EDGE) and not explore(crossing, "VERTEX"):
        raise GeometryError(
            f"{INTERSECT} found nothing where {references[0]!r} and {references[1]!r} "
            "cross: they do not meet. Check that they overlap, and that each is large "
            "enough to reach the other."
        )
    return _record(context, document, arguments, INTERSECT, crossing, "intersect")


def curve_extremum(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """The extreme point of an element along a direction — its highest, lowest, leftmost.

    An associative way to say "the top of this shape" that stays correct when the shape
    changes, where a hand-placed point does not.

    **A point, not a curve**, so it goes into the document's point store where
    `catia_measure_item` and every operation that takes a point can already find it. The
    registry files it under the curve toolbar because CATIA does; that is where the
    engineer looks for it, and it is no reason to invent a second place to keep points.
    """
    from app.kernel.occt.operations.surfaces import named_geometry

    document = context.require_document()
    element = named_geometry(document, arguments.get("element"), tool=EXTREMUM, argument="element")
    direction = as_direction(arguments.get("direction"), argument="direction")
    if arguments.get("second_direction"):
        raise OperationNotSupported(
            f"{EXTREMUM} with second_direction",
            "a tie-break direction only matters when the first leaves several points "
            "equally extreme, and this reports one of them rather than pretending to "
            "choose. Name a direction that does not tie",
        )

    maximum = arguments.get("maximum") is not False
    found = _extreme_vertex(element, direction, maximum=maximum)
    if found is None:
        raise GeometryError(
            f"{EXTREMUM} found no vertex on {arguments.get('element')!r} to measure. A "
            "full circle or a closed surface of revolution may have none — take a "
            "section of it first."
        )

    name = feature_name(arguments, "extremum")
    document.add_point(
        ReferencePoint(
            name=name,
            position=found,
            derived_from=str(arguments.get("element")),
            description=(
                f"the {'furthest' if maximum else 'nearest'} point of "
                f"{arguments.get('element')} along "
                f"[{', '.join(f'{value:g}' for value in direction)}]"
            ),
        )
    )
    return {"point": name, "position_mm": list(found), "of": arguments.get("element")}


def curve_project(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Lay a curve or a point onto a surface — how a trim line gets onto a shaped panel.

    Two projections, and which one runs is decided by whether a `direction` was given.
    Without one it is the **normal** projection: every point goes to its own nearest place
    on the surface, so the result hugs the shape. With one it is a *shadow* cast along that
    direction, which is what a moulding line or a drilled pattern actually is.

    **A projected curve is a fit, not a transcription.** OCCT approximates the result as a
    B-spline within its own tolerance — measured about 1 part in 10⁷ on a circle wrapped
    round a cylinder. It is the right answer to within manufacturing tolerance and the
    wrong thing to compare for exact equality; the tests check it to a relative tolerance
    for that reason.

    **Missing is refused, not returned empty.** A projection that lands nowhere produces a
    shape with no edges in it and `IsDone()` still true, so a caller that trusted the flag
    would file an empty curve under a name and find out three operations later.
    """
    from app.kernel.occt.operations.surfaces import named_geometry

    document = context.require_document()
    named = arguments.get("element")
    onto = named_geometry(document, arguments.get("support"), tool=PROJECT, argument="support")
    direction = (
        _unit(as_direction(arguments.get("direction"), argument="direction"), tool=PROJECT)
        if arguments.get("direction") is not None
        else None
    )
    nearest = arguments.get("nearest") is not False

    if _is_a_point(document, named):
        at = _point_named(document, named, tool=PROJECT, argument="element")
        return _project_point(context, arguments, at, onto, direction, nearest=nearest)

    curve = named_geometry(document, named, tool=PROJECT, argument="element")
    pieces = _projected_pieces(curve, onto, direction, tool=PROJECT)
    if not pieces:
        raise GeometryError(
            f"{PROJECT} landed nothing on {arguments.get('support')!r}: no part of "
            f"{named!r} projects onto it. "
            + (
                "Check that the direction points at the surface."
                if direction
                else "A normal projection only lands where the curve is actually over the "
                "surface — check that it is, or give a direction to cast along."
            )
        )

    from app.kernel.occt.topology import compound

    kept = _nearest_piece(pieces, curve) if nearest and len(pieces) > 1 else pieces
    shape = kept[0] if len(kept) == 1 else compound(kept)
    return _record(context, document, arguments, PROJECT, shape, "projection")


def curve_parallel(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A curve offset from another *within* a flat support — a border, a seam allowance.

    **Which side is ours to define, not OCCT's**, and the difference is visible in one
    place: OCCT takes the offset plane from the *wire* and never sees the support that was
    named, so on its own it gives the same answer whichever way the support faces. The
    rule here is stated, measured on the built result, and rebuilt mirrored when it went
    the other way:

    * a **closed** curve — positive grows the region it encloses, negative shrinks it;
    * an **open** curve — positive goes to the side `tangent × normal` points, at the
      curve's start, with the normal from the support the caller named.

    For a closed wire OCCT already normalises the side the same way, so the check passes
    rather than corrects; for an open one on a support facing the other way it corrects,
    which is the case `test_the_support_decides_an_open_curve_s_side_and_not_the_wire`
    pins. The check is what makes both a contract instead of a coincidence.

    `reversed` flips whichever of those applies, exactly as CATIA's own reverse-direction
    button does.

    A non-planar support is **refused**. The true offset inside a curved surface is a
    geodesic one in its parameter space, which is a different algorithm and a different
    answer; producing the planar one and calling it the same would be wrong by however
    much the surface curves.
    """
    from app.kernel.occt.operations.surfaces import named_geometry

    document = context.require_document()
    named = arguments.get("curve")
    curve = named_geometry(document, named, tool=PARALLEL, argument="curve")
    frame = _flat_support(document, arguments.get("support"), tool=PARALLEL)

    raw = arguments.get("distance_mm")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        raise GeometryError(
            f"{PARALLEL} needs distance_mm as a number of millimetres, got {raw!r}."
        )
    distance = -float(raw) if arguments.get("reversed") else float(raw)
    if abs(distance) < MIN_LENGTH_MM:
        raise GeometryError(
            f"{PARALLEL} was asked for an offset of zero, which is the curve it was given. "
            "Name a distance."
        )

    wire, closed = _as_wire(curve, tool=PARALLEL)
    _lies_in_plane(wire, frame, tool=PARALLEL, named=str(named))
    offset = _offset_in_plane(wire, distance, closed=closed, frame=frame, tool=PARALLEL)
    return _record(context, document, arguments, PARALLEL, offset, "parallel")


def curve_offset_3d(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A curve offset from another in space, with a direction instead of a support.

    Not a translation, which is what "offset by a vector" would mean and what a first
    implementation reaches for. The offset is perpendicular to the curve at every point:
    a circle offset this way is a *concentric* circle, where a translated one is the same
    circle somewhere else. `direction` only says which of the two perpendiculars to take —
    positive goes the way `tangent × direction` points at the curve's start.

    That side is **measured on the built curve rather than inferred**, because OCCT's own
    documentation and OCCT's own behaviour disagree about it: `Geom_OffsetCurve` is
    documented as offsetting along `V ^ T`, which for a circle in XY offset with `V = +Z`
    would shrink it, and it grows. Reading the displacement at the first parameter costs
    one evaluation and means the rule above holds whichever of the two OCCT meant.

    A curve whose tangent runs parallel to `direction` anywhere is **refused**: the two
    perpendiculars collapse into a whole circle of them there, and OCCT's own message for
    it names a class nobody outside this file has heard of.
    """
    from app.kernel.occt.operations.surfaces import named_geometry

    document = context.require_document()
    named = arguments.get("curve")
    curve = named_geometry(document, named, tool=OFFSET_3D, argument="curve")
    along = _unit(as_direction(arguments.get("direction"), argument="direction"), tool=OFFSET_3D)

    raw = arguments.get("distance_mm")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        raise GeometryError(
            f"{OFFSET_3D} needs distance_mm as a number of millimetres, got {raw!r}."
        )

    from app.kernel.occt.topology import edges as edge_list

    found = edge_list(curve)
    if len(found) != 1:
        raise OperationNotSupported(
            f"{OFFSET_3D} of a curve of {len(found)} edges",
            "each edge offsets on its own, so the pieces come apart at every corner and "
            "the result is a broken curve that measures as a whole one. Offset a single "
            "edge, or use catia_curve_parallel, which rounds the corners on a flat support",
        )

    return _record(
        context,
        document,
        arguments,
        OFFSET_3D,
        _offset_edge(found[0], float(raw), along, tool=OFFSET_3D),
        "offset",
    )


def curve_combine(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """The 3D curve two drawing views agree on, each extruded and the two intersected.

    How a shape defined as a plan and an elevation becomes real geometry — the wireframe
    counterpart of `catia_solid_combine`, and the reason a scanned or hand-drafted pair of
    views is worth anything in 3D.

    **Each curve is extruded far enough to cross the other**, symmetrically about itself,
    from the two curves' shared bounding box rather than from a constant. A fixed extrusion
    length works on a bracket and silently misses on a chassis.

    A direction defaults to the normal of the plane its curve lies in, which is what "the
    view it was drawn in" means. A **straight** curve lies in infinitely many planes, so
    there it is refused rather than guessed at — one arbitrary choice out of a pencil of
    planes would put the combined curve somewhere nobody asked for.
    """
    from app.kernel.occt.operations.surfaces import named_geometry

    document = context.require_document()
    names = (arguments.get("first_curve"), arguments.get("second_curve"))
    curves = [
        named_geometry(document, name, tool=COMBINE, argument=argument)
        for name, argument in zip(names, ("first_curve", "second_curve"), strict=True)
    ]
    directions = [
        _unit(as_direction(given, argument=argument), tool=COMBINE)
        if given is not None
        else _plane_of_curve(curve, tool=COMBINE, named=str(name))
        for curve, given, name, argument in zip(
            curves,
            (arguments.get("first_direction"), arguments.get("second_direction")),
            names,
            ("first_direction", "second_direction"),
            strict=True,
        )
    ]

    span = _spanning_length(curves)
    walls = [
        _swept_wall(curve, direction, span) for curve, direction in zip(curves, directions, strict=True)
    ]
    combined = _section_of(walls[0], walls[1], tool=COMBINE)
    if not count(combined, EDGE):
        raise GeometryError(
            f"{COMBINE} found nothing where {names[0]!r} and {names[1]!r} agree: extruded "
            "along their own views they never cross. Two views of the same shape must "
            "overlap where they are drawn — check that the curves cover the same region, "
            "and that each direction is the normal of the view it was drawn in."
        )
    return _record(context, document, arguments, COMBINE, combined, "combine")


# -- construction -------------------------------------------------------------


def _helix_frame(
    axis: Any, start: tuple[float, float, float], *, tool: str
) -> tuple[Any, float]:
    """The cylinder the helix winds on: its axis, and X pointing at the start point.

    X is aimed at `start_point` rather than left to OCCT so that the curve begins exactly
    where the design said. Letting `gp_Ax3` choose its own X from the axis alone puts the
    start somewhere nobody named, and the helix is then correct in shape and wrong in
    phase — which shows up as a thread that does not line up with its own runout.
    """
    location = axis.Location()
    origin = (location.X(), location.Y(), location.Z())
    normal = axis.Direction()
    along = (normal.X(), normal.Y(), normal.Z())

    offset = tuple(s - o for s, o in zip(start, origin, strict=True))
    height_along = sum(o * a for o, a in zip(offset, along, strict=True))
    radial = tuple(o - height_along * a for o, a in zip(offset, along, strict=True))
    radius = math.sqrt(sum(value * value for value in radial))
    if radius < MIN_LENGTH_MM:
        raise GeometryError(
            f"{tool} was given a start point on the axis itself, so the helix has a "
            "radius of zero and never leaves the line. Move the start point off the axis."
        )

    base = tuple(s - r for s, r in zip(start, radial, strict=True))
    frame = symbol("gp_Ax3")(
        symbol("gp_Pnt")(*base),
        symbol("gp_Dir")(*along),
        symbol("gp_Dir")(*radial),
    )
    return frame, radius


def _turned_frame(frame: Any, degrees: float) -> Any:
    """The same frame, spun about its own axis — how `start_angle_deg` moves the start."""
    turned = symbol("gp_Ax3")(frame.Location(), frame.Direction(), frame.XDirection())
    turned.Rotate(symbol("gp_Ax1")(frame.Location(), frame.Direction()), math.radians(degrees))
    return turned


def _helical_edge(
    frame: Any,
    radius: float,
    pitch: float,
    height: float,
    taper_deg: float,
    *,
    clockwise: bool,
) -> Any:
    """A helix as a straight pcurve on a cylinder, or on a cone when it tapers.

    See the module docstring on why the parameter span carries the `√(1+k²)` factor: it is
    the whole difference between the right length and a 16% error that looks right.
    """
    taper = math.radians(taper_deg)
    turns = height / pitch

    if taper_deg:
        # A cone's v runs along the slant, so climbing `pitch` per turn in *height* means
        # climbing pitch/cos(taper) in v. Using the height directly would build a helix
        # that rises too slowly, by exactly the cosine.
        surface = symbol("Geom_ConicalSurface")(frame, taper, radius)
        rise = (pitch / (2.0 * math.pi)) / math.cos(taper)
    else:
        surface = symbol("Geom_CylindricalSurface")(frame, radius)
        rise = pitch / (2.0 * math.pi)

    direction = symbol("gp_Dir2d")(1.0 if clockwise else -1.0, rise)
    line = symbol("Geom2d_Line")(symbol("gp_Pnt2d")(0.0, 0.0), direction)
    span = turns * 2.0 * math.pi * math.sqrt(1.0 + rise * rise)

    edge = build_or_raise(
        symbol("BRepBuilderAPI_MakeEdge")(line, surface, 0.0, span),
        tool=HELIX,
        detail="The helix could not be built on its cylinder; check the pitch and height.",
    )
    # Without this the edge carries only its 2D curve on the surface, and every 3D
    # question asked of it — its length, its bounding box, sweeping along it — answers
    # about nothing.
    symbol("BRepLib").BuildCurves3d_s(edge)
    return edge


def _circle_through(points: Sequence[tuple[float, float, float]]) -> Any:
    """The circle through three points, refusing three that are in a line.

    `GC_MakeCircle`, not `GC_MakeArcOfCircle`: the arc builder answers a different
    question — the arc *from* the first point *through* the second *to* the third — and
    its result is a trimmed curve, so the full circle this operation promises would come
    back as half of one.
    """
    maker = symbol("GC_MakeCircle")(
        symbol("gp_Pnt")(*points[0]),
        symbol("gp_Pnt")(*points[1]),
        symbol("gp_Pnt")(*points[2]),
    )
    if not maker.IsDone():
        raise GeometryError(
            f"{CIRCLE} could not fit a circle through those three points. Three points in "
            "a straight line lie on no circle — move one off the line."
        )
    return maker.Value().Circ()


def _section_of(first: Any, second: Any, *, tool: str) -> Any:
    """Where two shapes cross, as edges and vertices.

    `BRepAlgoAPI_Section` with `ComputePCurveOn` left off and approximation on: the result
    is wanted as 3D geometry that later operations can sweep along, not as a pcurve on
    either parent.
    """
    maker = symbol("BRepAlgoAPI_Section")(first, second, False)
    maker.Approximation(True)
    return build_or_raise(
        maker,
        tool=tool,
        detail="The two elements could not be intersected. One of them may be an "
        "unbounded construction plane rather than real geometry.",
    )


def _is_a_point(document: Any, reference: Any) -> bool:
    """Whether `element` names a place rather than geometry.

    Asked before resolving, because the answer decides which of two different operations
    runs — a point projects to a point, a curve to a curve — and because a literal
    `[x, y, z]` is accepted wherever a point name is.
    """
    if isinstance(reference, (list, tuple, Mapping)):
        return True
    return bool(reference) and document.has_point(str(reference).strip())


def _project_point(
    context: BuildContext,
    arguments: Mapping[str, Any],
    at: tuple[float, float, float],
    onto: Any,
    direction: tuple[float, float, float] | None,
    *,
    nearest: bool,
) -> Mapping[str, Any]:
    """A point laid onto a surface: its nearest place, or where a line through it crosses.

    The directional case reads **both** halves of the line. A projection direction says
    which way to look, not which way to walk, and a surface behind the point is still
    something the point projects onto — refusing it because of a sign would be a miss
    nothing downstream could tell from a real one.
    """
    document = context.require_document()
    named = arguments.get("element")

    if direction is None:
        _, position, _ = closest_on_surface(onto, at, tool=PROJECT)
        how = f"the nearest place on {arguments.get('support')}"
    else:
        from app.kernel.occt.interrogate.raycast import signed_hit_distances

        hits = signed_hit_distances(onto, at, direction)
        if not hits:
            raise GeometryError(
                f"{PROJECT} cast a line through {named!r} along "
                f"[{', '.join(f'{value:g}' for value in direction)}] and it never meets "
                f"{arguments.get('support')!r}. Check the direction, or drop it to project "
                "to the nearest place instead."
            )
        if len(hits) > 1 and not nearest:
            raise GeometryError(
                f"{PROJECT} found {len(hits)} places where the line through {named!r} "
                "crosses the surface, and a point has one place. Leave nearest on, or "
                "project a curve, which can carry every solution at once."
            )
        position = _stepped(at, direction, hits[0])
        how = f"cast onto {arguments.get('support')}"

    name = feature_name(arguments, "projection")
    document.add_point(
        ReferencePoint(
            name=name,
            position=position,
            derived_from=str(named),
            description=f"{named} projected to {how}",
        )
    )
    return {"feature": name, "point": name, "position_mm": list(position), "of": named}


def _projected_pieces(
    curve: Any, onto: Any, direction: tuple[float, float, float] | None, *, tool: str
) -> list[Any]:
    """Every separate piece a curve lands in, however the projection was cast."""
    from app.kernel.occt.topology import domains

    wire, _ = _as_wire(curve, tool=tool)

    if direction is not None:
        projector = symbol("BRepProj_Projection")(wire, onto, symbol("gp_Dir")(*direction))
        found = []
        while projector.More():
            found.append(projector.Current())
            projector.Next()
        return [piece for piece in found if count(piece, EDGE)]

    maker = symbol("BRepOffsetAPI_NormalProjection")(onto)
    maker.Add(wire)
    landed = build_or_raise(
        maker,
        tool=tool,
        detail="The curve could not be projected onto that surface. A surface with no "
        "face in it — an unbounded construction plane, say — is the usual cause.",
    )
    pieces = [piece for piece in domains(landed) if count(piece, EDGE)]
    return pieces or ([landed] if count(landed, EDGE) else [])


def _nearest_piece(pieces: list[Any], source: Any) -> list[Any]:
    """The one solution closest to what was projected, of several.

    A surface can be in the way twice — a cylinder is, from any direction across it — and
    both crossings are real. Which one was meant is the near one, and `nearest` says so.
    """
    def distance(piece: Any) -> float:
        measure = symbol("BRepExtrema_DistShapeShape")(piece, source)
        measure.Perform()
        return float(measure.Value()) if measure.IsDone() else math.inf

    return [min(pieces, key=distance)]


def _flat_support(document: Any, reference: Any, *, tool: str) -> Any:
    """The support a curve is offset within, as a frame — and only a flat one."""
    from app.kernel.occt.elements import plane_frame as frame_of_plane

    if reference is None:
        raise GeometryError(
            f"{tool} needs a support — the flat face or plane the curve lies on. The "
            "offset is taken within it, so which one it is changes the answer."
        )
    return frame_of_plane(document, reference, tool=tool)


def _as_wire(curve: Any, *, tool: str) -> tuple[Any, bool]:
    """A curve as one wire, and whether it closes on itself.

    Closure is read from the wire's own flag rather than by comparing endpoints: OCCT sets
    it when the wire actually closes, and a coordinate comparison would need a tolerance
    that is either too tight for a fitted curve or loose enough to close a real gap.
    """
    from app.kernel.occt.topology import WIRE, explore
    from app.kernel.occt.topology import edges as edge_list

    wires = explore(curve, WIRE)
    if len(wires) == 1:
        wire = symbol("TopoDS").Wire_s(wires[0])
        return wire, bool(wire.Closed())

    found = edge_list(curve)
    if not found:
        raise GeometryError(f"{tool} was given something with no curve in it.")

    maker = symbol("BRepBuilderAPI_MakeWire")()
    for edge in found:
        maker.Add(edge)
    build_or_raise(
        maker,
        tool=tool,
        detail=f"Its {len(found)} edges do not join into one curve. Join them with "
        "catia_join first, or name a single one.",
    )
    wire = maker.Wire()
    return wire, bool(wire.Closed())


def _lies_in_plane(wire: Any, frame: Any, *, tool: str, named: str) -> None:
    """Refuse a curve that does not lie in its support, before offsetting it in it."""
    from app.kernel.occt.topology import point_of, vertices

    plane = symbol("gp_Pln")(frame)
    worst = max(
        (plane.Distance(symbol("gp_Pnt")(*point_of(vertex))) for vertex in vertices(wire)),
        default=0.0,
    )
    if worst > _IN_PLANE_TOLERANCE_MM:
        raise GeometryError(
            f"{tool} offsets a curve within its support, and {named!r} stands "
            f"{worst:.4g} mm off the one named. Name the plane the curve is actually in, "
            "or project it onto this one first with catia_curve_project."
        )


def _offset_in_plane(
    wire: Any, distance: float, *, closed: bool, frame: Any, tool: str
) -> Any:
    """Offset a planar wire, on the side this operation says rather than the one OCCT picks.

    The rebuild is not a retry: OCCT's side comes from the wire's winding, which is a
    property of how the curve was built and is invisible to whoever named it. Building
    once, measuring which way it went, and mirroring the distance if it went the wrong way
    makes the stated rule the contract on any wire.
    """
    def built(offset: float) -> Any:
        maker = symbol("BRepOffsetAPI_MakeOffset")(
            wire, symbol("GeomAbs_JoinType").GeomAbs_Arc, not closed
        )
        maker.Perform(offset)
        return build_or_raise(
            maker,
            tool=tool,
            detail=f"An offset of {offset:g} mm could not be built. An offset larger than "
            "the curve's own tightest turn collapses it — try a smaller one.",
        )

    first = built(distance)
    if _went_the_right_way(first, wire, distance, closed=closed, frame=frame, tool=tool):
        return first
    return built(-distance)


def _went_the_right_way(
    result: Any, source: Any, distance: float, *, closed: bool, frame: Any, tool: str
) -> bool:
    """Whether an offset landed on the side this operation promised.

    Two rules, because a closed curve and an open one have different sides to speak of.
    A closed one has an inside, so positive grows it — measured as area, which no winding
    can flip. An open one has none, so the side is `tangent × normal` at its start, which
    is stated in terms the caller can see: the first point they named and the support they
    named.
    """
    if closed:
        return (_enclosed_area(result, tool=tool) > _enclosed_area(source, tool=tool)) == (
            distance > 0
        )

    normal = frame.Direction()
    start, tangent = start_of_curve(source, tool=tool)
    side = _cross(tangent, (normal.X(), normal.Y(), normal.Z()))
    _, landed = closest_on_curve_point(result, start, tool=tool)
    moved = (landed[0] - start[0], landed[1] - start[1], landed[2] - start[2])
    return (sum(a * b for a, b in zip(moved, side, strict=True)) > 0.0) == (distance > 0)


def _enclosed_area(wire: Any, *, tool: str) -> float:
    face = symbol("BRepBuilderAPI_MakeFace")(symbol("TopoDS").Wire_s(wire), True)
    made = build_or_raise(
        face,
        tool=tool,
        detail="A closed curve that encloses no area cannot be offset to a stated side — "
        "check that it does not cross itself.",
    )
    properties = symbol("GProp_GProps")()
    symbol("BRepGProp").SurfaceProperties_s(made, properties)
    return float(properties.Mass())


def start_of_curve(curve: Any, *, tool: str) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """The first point of a curve in its own running order, and the tangent there."""
    chain = _chain_of(curve, tool=tool)
    adaptor = chain[0][0]
    place, derivative = symbol("gp_Pnt")(), symbol("gp_Vec")()
    adaptor.D1(adaptor.FirstParameter(), place, derivative)
    return (place.X(), place.Y(), place.Z()), _unit(
        (derivative.X(), derivative.Y(), derivative.Z()), tool=tool
    )


def closest_on_curve_point(
    curve: Any, at: tuple[float, float, float], *, tool: str
) -> tuple[Any, tuple[float, float, float]]:
    """The nearest edge of a curve to a point, and the nearest place on it."""
    edge, parameter = closest_on_curve(curve, at, tool=tool)
    place = symbol("BRepAdaptor_Curve")(edge).Value(parameter)
    return edge, (place.X(), place.Y(), place.Z())


def _offset_edge(
    edge: Any, distance: float, along: tuple[float, float, float], *, tool: str
) -> Any:
    """One edge offset perpendicular to itself, on the side this operation states."""
    adaptor = symbol("BRepAdaptor_Curve")(edge)
    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    basis = symbol("BRep_Tool").Curve_s(edge, first, last)

    place, derivative = symbol("gp_Pnt")(), symbol("gp_Vec")()
    adaptor.D1(first, place, derivative)
    tangent = _unit((derivative.X(), derivative.Y(), derivative.Z()), tool=tool)
    side = _cross(tangent, along)
    if math.sqrt(sum(value * value for value in side)) < 1e-9:
        raise GeometryError(
            f"{tool} was given a direction the curve runs along, so there is no single "
            "perpendicular to offset towards — every direction around the curve is one. "
            "Give a direction across the curve rather than along it."
        )

    def built(offset: float) -> tuple[Any, tuple[float, float, float]]:
        try:
            curve = symbol("Geom_OffsetCurve")(basis, offset, symbol("gp_Dir")(*along))
            moved = curve.Value(first)
        except Exception as exc:  # noqa: BLE001 - OCCT's Standard_Failure hierarchy
            raise GeometryError(
                f"{tool} could not offset the curve: {exc}. This happens where the curve "
                "runs parallel to the direction given — give one across the curve, not "
                "along it."
            ) from exc
        return curve, (moved.X() - place.X(), moved.Y() - place.Y(), moved.Z() - place.Z())

    curve, moved = built(distance)
    wanted = sum(a * b for a, b in zip(moved, side, strict=True)) * (1.0 if distance > 0 else -1.0)
    if wanted < 0.0:
        curve, _ = built(-distance)

    return build_or_raise(
        symbol("BRepBuilderAPI_MakeEdge")(curve, first, last),
        tool=tool,
        detail="The offset curve could not be made into an edge.",
    )


def _plane_of_curve(curve: Any, *, tool: str, named: str) -> tuple[float, float, float]:
    """The normal of the plane a drawn curve lies in — the view it was drawn in."""
    from app.kernel.occt.reference import least_spread_axis

    properties = symbol("GProp_GProps")()
    symbol("BRepGProp").LinearProperties_s(curve, properties)
    normal, definition = least_spread_axis(properties)
    if definition < _COPLANAR_DEFINITION:
        raise GeometryError(
            f"{tool} needs to know which view {named!r} was drawn in, and a straight curve "
            "lies in infinitely many planes — every plane through it fits equally well. "
            "Give the direction explicitly."
        )
    return _unit(normal, tool=tool)


def _spanning_length(shapes: Sequence[Any]) -> float:
    """A length certain to carry either curve clear across the other.

    Twice the diagonal of the two together, so the extrusions cross whatever the curves'
    relative size — a constant that works on a bracket silently misses on a chassis.
    """
    box = symbol("Bnd_Box")()
    for shape in shapes:
        symbol("BRepBndLib").Add_s(shape, box, True)
    if box.IsVoid():  # pragma: no cover - both shapes are curves with real extent
        raise GeometryError("These curves have no extent to combine.")
    return 2.0 * math.sqrt(box.SquareExtent()) + 1.0


def _swept_wall(curve: Any, direction: tuple[float, float, float], span: float) -> Any:
    """A curve extruded symmetrically about itself, far enough to cross anything nearby."""
    shift = symbol("gp_Trsf")()
    shift.SetTranslation(symbol("gp_Vec")(*(value * -span / 2.0 for value in direction)))
    moved = symbol("BRepBuilderAPI_Transform")(curve, shift, True).Shape()
    return symbol("BRepPrimAPI_MakePrism")(
        moved, symbol("gp_Vec")(*(value * span for value in direction))
    ).Shape()


def _cross(
    first: tuple[float, float, float], second: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _extreme_vertex(
    element: Any, direction: tuple[float, float, float], *, maximum: bool
) -> tuple[float, float, float] | None:
    """The vertex furthest along a direction, measured on the element's own vertices.

    Vertices rather than a sampled search over the faces: they are exact, they are what a
    machined part's extremes actually sit on, and an approximated answer would have to say
    so in a payload whose whole point is to be referred to later as a place.
    """
    from app.kernel.occt.topology import point_of, vertices

    found = vertices(element)
    if not found:
        return None

    def along(vertex: Any) -> float:
        position = point_of(vertex)
        return sum(p * d for p, d in zip(position, direction, strict=True))

    chosen = max(found, key=along) if maximum else min(found, key=along)
    return point_of(chosen)


# -- points on curves and surfaces --------------------------------------------
#
# Shared with `reference_ops`, which builds the *points*. The evaluation lives here
# because it is a curve question and the two must agree: a point placed on a curve by one
# rule and a tangent read at it by another describe two different places on the same
# curve, and nothing in either payload would say so.


def closest_on_curve(curve: Any, at: tuple[float, float, float], *, tool: str) -> tuple[Any, float]:
    """The edge of a curve nearest a point, and the parameter of the nearest point on it.

    A named curve may be several edges — a section around a cylinder, a polyline — so the
    edge is chosen rather than assumed. Refusing a multi-edge curve would make the point
    operations useless on exactly the curves that are worth putting a point on.
    """
    from app.kernel.occt.topology import edges as edge_list

    found = edge_list(curve)
    if not found:
        raise GeometryError(f"{tool} was given something with no curve in it to sit on.")

    target = symbol("gp_Pnt")(*at)
    best_edge, best_parameter, best_distance = None, 0.0, math.inf
    for edge in found:
        adaptor = symbol("BRepAdaptor_Curve")(edge)
        projector = symbol("GeomAPI_ProjectPointOnCurve")(
            target, adaptor.Curve().Curve(), adaptor.FirstParameter(), adaptor.LastParameter()
        )
        if projector.NbPoints() < 1:
            continue
        if projector.LowerDistance() < best_distance:
            best_edge = edge
            best_parameter = projector.LowerDistanceParameter()
            best_distance = projector.LowerDistance()

    if best_edge is None:
        raise GeometryError(
            f"{tool} could not find a point on the curve nearest "
            f"{[round(value, 4) for value in at]}. The curve may be degenerate."
        )
    return best_edge, best_parameter


def point_along_curve(
    curve: Any, *, ratio: float | None, distance_mm: float | None, from_end: bool, tool: str
) -> tuple[float, float, float]:
    """A place on a curve, by proportion of its length or by absolute length along it.

    **Both are measured as arc length**, not as parameter. A B-spline's parameter is not
    proportional to its length, so `ratio: 0.5` read as a parameter lands somewhere that
    is the midpoint of nothing — plausible on a line, wrong on every curve worth the name.
    """
    chain = _chain_of(curve, tool=tool)
    lengths = [symbol("GCPnts_AbscissaPoint").Length_s(adaptor) for adaptor, _ in chain]
    total = sum(lengths)
    if total < MIN_LENGTH_MM:
        raise GeometryError(f"{tool} was given a curve of no length to place a point on.")

    if ratio is not None and distance_mm is not None:
        raise GeometryError(
            f"{tool} takes ratio or distance_mm, not both — they are two ways of saying "
            "the same thing and giving both leaves which one wins to chance."
        )
    if ratio is None and distance_mm is None:
        travelled = total / 2.0
    elif ratio is not None:
        if not 0.0 <= ratio <= 1.0:
            raise GeometryError(
                f"{tool} takes ratio between 0 and 1 as a proportion along the curve; got "
                f"{ratio}. Use distance_mm to go past the end."
            )
        travelled = ratio * total
    else:
        travelled = float(distance_mm or 0.0)

    if from_end:
        travelled = total - travelled

    # Walk the chain segment by segment, spending each one's length before moving on.
    # Anything else would need one adaptor over the whole wire, which OCCT does not offer
    # — and taking the first edge instead would put "halfway along" a quarter of the way
    # along an L, which is the number nobody would question.
    remaining = travelled
    for (adaptor, _), length in zip(chain, lengths, strict=True):
        if remaining <= length or (adaptor, length) == (chain[-1][0], lengths[-1]):
            walker = symbol("GCPnts_AbscissaPoint")(adaptor, remaining, adaptor.FirstParameter())
            if not walker.IsDone():
                raise GeometryError(
                    f"{tool} could not walk {travelled:.4g} mm along a curve {total:.4g} mm "
                    "long. Check the distance against the curve's own length."
                )
            place = adaptor.Value(walker.Parameter())
            return (place.X(), place.Y(), place.Z())
        remaining -= length

    raise GeometryError(  # pragma: no cover - the loop always returns on its last segment
        f"{tool} could not place a point {travelled:.4g} mm along a curve {total:.4g} mm long."
    )


def _chain_of(curve: Any, *, tool: str) -> list[tuple[Any, Any]]:
    """A curve's edges **in the order they connect**, each with its own adaptor.

    `topology.edges` returns them in map order, which is the order they were built rather
    than the order they run — fine for "every edge of this", useless for "40 mm along
    this". `BRepTools_WireExplorer` walks a wire end to end, which is the only ordering
    that makes arc length along a chain mean anything.
    """
    from app.kernel.occt.topology import WIRE, explore
    from app.kernel.occt.topology import edges as edge_list

    wires = explore(curve, WIRE)
    if len(wires) == 1:
        walker = symbol("BRepTools_WireExplorer")(symbol("TopoDS").Wire_s(wires[0]))
        ordered = []
        while walker.More():
            edge = walker.Current()
            ordered.append((symbol("BRepAdaptor_Curve")(edge), edge))
            walker.Next()
        if ordered:
            return ordered

    found = edge_list(curve)
    if len(found) == 1:
        return [(symbol("BRepAdaptor_Curve")(found[0]), found[0])]
    if not found:
        raise GeometryError(f"{tool} was given something with no curve in it.")
    raise GeometryError(
        f"{tool} places a point by length along one connected curve, and this one is "
        f"{len(found)} edges that do not form a single chain. Join them with catia_join, "
        "or name one of them."
    )


def closest_on_surface(
    surface: Any, at: tuple[float, float, float], *, tool: str
) -> tuple[Any, tuple[float, float, float], tuple[float, float]]:
    """The face of a surface nearest a point, the nearest point **on the face**, and its (u, v).

    **A face is a trimmed piece of an unbounded surface, and the difference is not
    academic.** `GeomAPI_ProjectPointOnSurf` projects onto the surface a face was cut out
    of, so the top disc of a cylinder answers for the whole infinite plane it lies in: a
    point out beside the wall projects to a place 20 mm outside the disc's rim, which is
    then nearer than the wall and wins. Every caller then agrees — the plane, the point,
    the normal — on a place that is not on the part. `BRepExtrema_DistShapeShape` measures
    against the face's real boundary, so the answer is somewhere the part actually is.

    The (u, v) still comes from the projector, because it is exact for a point already
    known to lie on the surface, and `ParOnFaceS2` has nothing to say when the nearest
    place is on the face's own edge — which is precisely the case this exists to handle.
    """
    from app.kernel.occt.topology import faces as face_list

    found = face_list(surface)
    if not found:
        raise GeometryError(f"{tool} was given something with no face in it to sit on.")

    measure = symbol("BRepExtrema_DistShapeShape")(
        symbol("BRepBuilderAPI_MakeVertex")(symbol("gp_Pnt")(*at)).Vertex(),
        surface if len(found) > 1 else found[0],
    )
    measure.Perform()
    if not measure.IsDone() or measure.NbSolution() < 1:
        raise GeometryError(
            f"{tool} could not find a point on the surface nearest "
            f"{[round(value, 4) for value in at]}."
        )

    place = measure.PointOnShape2(1)
    position = (place.X(), place.Y(), place.Z())

    support = measure.SupportOnShape2(1)
    face = (
        symbol("TopoDS").Face_s(support)
        if support.ShapeType() == symbol("TopAbs_ShapeEnum").TopAbs_FACE
        else _face_holding(found, place)
    )
    projector = symbol("GeomAPI_ProjectPointOnSurf")(place, symbol("BRep_Tool").Surface_s(face))
    if projector.NbPoints() < 1:  # pragma: no cover - the point is on this very surface
        raise GeometryError(
            f"{tool} found a place on the surface but could not read its parameters there."
        )
    u, v = projector.LowerDistanceParameters()
    return face, position, (u, v)


def _face_holding(faces: Sequence[Any], place: Any) -> Any:
    """Which face a point on a shared boundary belongs to.

    When the nearest place is on an edge, that edge borders two faces and OCCT names the
    edge rather than either. Both are correct answers to "which surface is this on"; the
    nearest one is chosen so the choice is at least deterministic and the normal that
    comes back is a normal of a face the point is genuinely on.
    """
    def distance(face: Any) -> float:
        projector = symbol("GeomAPI_ProjectPointOnSurf")(
            place, symbol("BRep_Tool").Surface_s(face)
        )
        return float(projector.LowerDistance()) if projector.NbPoints() else math.inf

    return min(faces, key=distance)


def closest_face_normal(
    surface: Any, at: tuple[float, float, float], *, tool: str
) -> tuple[Any, tuple[float, float, float], tuple[float, float, float]]:
    """The nearest face, the foot of the perpendicular, and the outward normal there."""
    from app.kernel.occt import classify

    face, foot, (u, v) = closest_on_surface(surface, at, tool=tool)
    normal = classify.face_normal_at(face, u, v)
    if normal is None:
        raise GeometryError(
            f"{tool} could not read the surface's normal at "
            f"{[round(value, 4) for value in foot]}. The surface may be degenerate there."
        )
    return face, foot, normal


def closest_curve_tangent(
    curve: Any, at: tuple[float, float, float], *, tool: str
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """The nearest point on a curve, and the unit tangent there."""
    edge, parameter = closest_on_curve(curve, at, tool=tool)
    adaptor = symbol("BRepAdaptor_Curve")(edge)
    place, derivative = symbol("gp_Pnt")(), symbol("gp_Vec")()
    adaptor.D1(parameter, place, derivative)

    tangent = (derivative.X(), derivative.Y(), derivative.Z())
    return (place.X(), place.Y(), place.Z()), _unit(tangent, tool=tool)


def _stepped(
    origin: tuple[float, float, float],
    along: tuple[float, float, float],
    distance: float,
) -> tuple[float, float, float]:
    """`origin + along · distance`, spelled out rather than zipped.

    A comprehension over `zip` produces `tuple[float, ...]`, which loses the one fact
    every consumer here depends on — that a place has three components.
    """
    return (
        origin[0] + along[0] * distance,
        origin[1] + along[1] * distance,
        origin[2] + along[2] * distance,
    )


def _segment(start: tuple[float, float, float], end: tuple[float, float, float]) -> Any:
    return symbol("BRepBuilderAPI_MakeEdge")(
        symbol("gp_Pnt")(*start), symbol("gp_Pnt")(*end)
    ).Edge()


def _unit(vector: tuple[float, float, float], *, tool: str) -> tuple[float, float, float]:
    span = math.sqrt(sum(value * value for value in vector))
    if span < MIN_LENGTH_MM:
        raise GeometryError(f"{tool} was given a direction of zero length, which points nowhere.")
    return (vector[0] / span, vector[1] / span, vector[2] / span)


def _record(
    context: BuildContext,
    document: Any,
    arguments: Mapping[str, Any],
    tool: str,
    shape: Any,
    fallback: str,
) -> Mapping[str, Any]:
    """File a constructed curve under the design's own name for it.

    Curves and surfaces share one construction store — see `PartDocument._construction` —
    so this is `surfaces._record` with the kind fixed. Kept as its own three lines rather
    than imported so this module does not depend on that one for its own bookkeeping.
    """
    from app.kernel.occt.naming import record_primitive
    from app.kernel.occt.operations.surfaces import CURVE

    name = feature_name(arguments, fallback)
    feature = document.add_feature(name, tool)
    document.set_construction(feature, shape, name=name, kind=CURVE)
    record_primitive(feature.labels, shape)
    return context.result_for(feature)


# -- resolving points ---------------------------------------------------------


def _point_named(
    document: Any, reference: Any, *, tool: str, argument: str
) -> tuple[float, float, float]:
    """A named reference point, or literal `[x, y, z]` millimetres.

    Both spellings, for the reason the module docstring gives: the registry types these as
    names because CATIA builds a point before referring to it, and requiring four
    `catia_point_at` calls before a four-point polyline is ceremony without meaning.
    """
    if reference is None:
        raise GeometryError(f"{tool} needs {argument} and none was given.")
    if isinstance(reference, (list, tuple)) or isinstance(reference, Mapping):
        return as_point(reference, argument=argument)

    name = str(reference).strip()
    if document.has_point(name):
        return document.point(name).position

    from app.kernel.occt.elements import resolve_element

    element = resolve_element(document, name, tool=tool)
    if element.position is None:
        raise GeometryError(
            f"{tool} needs {argument} to be a point, and {name!r} is "
            f"{element.description}. Build a point with catia_point_at or "
            "catia_curve_extremum, or give [x, y, z]."
        )
    return element.position


def _point_list(
    document: Any, value: Any, *, tool: str, minimum: int
) -> list[tuple[float, float, float]]:
    """A list of point references, each resolved the same way one is."""
    references = _references(value, tool=tool, argument="points", minimum=minimum)
    return [
        _point_named(document, reference, tool=tool, argument="points")
        for reference in references
    ]


def _references(value: Any, *, tool: str, argument: str, minimum: int) -> list[Any]:
    """A list-of-names argument, refused clearly when it is one name or none."""
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise GeometryError(
            f"{tool} needs {argument} as a list, for example "
            f'["start", "end"] or [[0, 0, 0], [10, 0, 5]]; got {value!r}.'
        )
    listed = list(value)
    if len(listed) < minimum:
        raise GeometryError(
            f"{tool} needs at least {minimum} entries in {argument} and was given "
            f"{len(listed)}."
        )
    return listed


def _distance_between(
    first: tuple[float, float, float], second: tuple[float, float, float]
) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second, strict=True)))


__all__ = [
    "CIRCLE",
    "COMBINE",
    "EXTREMUM",
    "HELIX",
    "INTERSECT",
    "LINE_BETWEEN",
    "LINE_DIRECTION",
    "LINE_NORMAL",
    "LINE_TANGENT",
    "MIN_LENGTH_MM",
    "OFFSET_3D",
    "PARALLEL",
    "POLYLINE",
    "PROJECT",
    "SECTION",
    "SPLINE",
    "closest_curve_tangent",
    "closest_face_normal",
    "closest_on_curve",
    "closest_on_curve_point",
    "closest_on_surface",
    "curve_circle",
    "curve_combine",
    "curve_extremum",
    "curve_helix",
    "curve_intersect",
    "curve_offset_3d",
    "curve_parallel",
    "curve_polyline",
    "curve_project",
    "curve_section",
    "curve_spline",
    "line_between",
    "line_direction",
    "line_normal",
    "line_tangent",
    "point_along_curve",
    "start_of_curve",
]