"""Constructing reference geometry — master plan Phase 2.4.

The operations that make the *second* feature of a real part possible. Before these, a
sketch could only sit on `XY`, `YZ` or `ZX`, so every profile in a design passed through
the world origin: enough for a first pad and nothing after it. A boss on top of a pad
needs a plane at the top of the pad, and there was no way to say so.

**A constructed plane adds nothing to the part.** It is a place to draw, held on the
document under the design's own name, exactly as in CATIA where a plane is a construction
element rather than a body. So these operations do not touch the part's shape, do not
record a naming history, and cannot make a build fail later the way a real surface can —
which is also why they return the plane's own description rather than a measurement.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from app.catia.ops import vocabulary
from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt.binding import symbol
from app.kernel.occt.operations.context import BuildContext, feature_name
from app.kernel.occt.reference import (
    AxisSystem,
    ReferencePlane,
    ReferencePoint,
    axis_frame,
    frame_from_normal,
    frame_from_points,
    least_spread_axis,
    offset_frame,
    rotated_frame,
)

PLANE_OFFSET = "catia_plane_offset"
PLANE_THROUGH_POINTS = "catia_plane_through_points"
PLANE_ANGLE = "catia_plane_angle"
PLANE_NORMAL_TO_CURVE = "catia_plane_normal_to_curve"
PLANE_TANGENT_TO_SURFACE = "catia_plane_tangent_to_surface"
PLANE_MEAN = "catia_plane_mean"
PLANES_BETWEEN = "catia_planes_between"
POINT_AT = "catia_point_at"
POINT_BETWEEN = "catia_point_between"
POINT_ON_CURVE = "catia_point_on_curve"
POINT_ON_SURFACE = "catia_point_on_surface"
POINT_CENTRE = "catia_point_centre"
AXIS_SYSTEM = "catia_axis_system"

#: The world axes, accepted wherever a hinge line is wanted. They are always present in
#: CATIA's tree too (the origin axis system), so naming one is not a special case a
#: design has to construct first — the same reasoning that lets `XY` be written directly
#: rather than built.
_WORLD_AXES: dict[str, tuple[float, float, float]] = {
    "X": (1.0, 0.0, 0.0),
    "Y": (0.0, 1.0, 0.0),
    "Z": (0.0, 0.0, 1.0),
}

#: Below this ratio of least to greatest moment, a point set is treated as lying on one
#: line. Relative rather than absolute — see `reference.least_spread_axis`.
_COLLINEAR_DEFINITION = 1e-9

#: How far from parallel two planes may face and still have one spacing between them, in
#: radians. About 6 arc-seconds: tight enough that a real angle is caught, loose enough
#: that a plane built by an offset chain is not refused for its own rounding.
_PARALLEL_TOLERANCE_RAD = 3e-5

#: Below this the two planes are the same plane and there is nothing between them, in mm.
_COINCIDENT_TOLERANCE_MM = 1e-9


def plane_offset(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A plane parallel to an existing one, at a distance.

    **The local X axis is inherited from the reference**, which is what makes offsets
    compose: a rectangle placed `at (10, 5)` on a plane offset from `XY` lands directly
    above the same rectangle on `XY`. Deriving a fresh X from the normal — which is what
    happens if you hand `gp_Ax3` a point and a direction and let it choose — silently
    rotates the sketch frame, and the boss comes out square to nothing in particular.

    `distance_mm` is signed along the reference plane's normal and `reversed` flips it.
    Both spellings exist in the operation's own schema because a CATIA user reaches for
    either, and honouring only one would make the other silently do nothing.
    """
    document = context.require_document()

    reference = arguments.get("reference")
    if reference is None:
        raise GeometryError(
            f"{PLANE_OFFSET} needs a reference plane to offset from — an origin plane "
            f"({', '.join(vocabulary.ORIGIN_PLANES)}) or one this design already built."
        )

    raw_distance = arguments.get("distance_mm")
    if not isinstance(raw_distance, (int, float)) or isinstance(raw_distance, bool):
        raise GeometryError(
            f"{PLANE_OFFSET} needs distance_mm as a number of millimetres, got "
            f"{raw_distance!r}. Negative offsets to the other side."
        )

    distance = float(raw_distance)
    if arguments.get("reversed"):
        distance = -distance

    base = _reference_frame(document, str(reference))
    name = feature_name(arguments, "plane")
    plane = ReferencePlane(
        name=name,
        frame=offset_frame(base, distance),
        derived_from=str(reference),
        description=f"{reference} offset by {distance} mm",
    )
    document.add_plane(plane)

    return {
        "feature": plane.name,
        **plane.to_dict(),
        "planes": document.plane_names(),
    }


def _reference_frame(document: Any, reference: Any, *, tool: str = PLANE_OFFSET) -> Any:
    """The frame of an origin plane, a constructed plane, or a named planar face.

    The third of those used to be refused with a message blaming Phase 2.2. That phase
    shipped, so `catia_plane_offset(reference="slab#top", distance_mm=5)` now means what
    it reads as — a plane 5 mm above the slab's top face — and goes through the one
    resolver in `app.kernel.occt.elements` that every "which plane" argument uses.

    `tool` is passed through so the refusal names the operation the caller actually ran.
    A message blaming `catia_plane_offset` for an argument of `catia_planes_between` sends
    the reader to a call that is not in their design.
    """
    from app.kernel.occt.elements import plane_frame

    return plane_frame(document, reference, tool=tool)


# -- points -------------------------------------------------------------------


def point_at(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A point at explicit coordinates, optionally measured from another point."""
    document = context.require_document()
    at = _as_vector(arguments.get("at"), argument="at", tool=POINT_AT)

    reference = arguments.get("reference")
    origin = (0.0, 0.0, 0.0)
    derived = ""
    if reference is not None:
        origin = document.point(str(reference)).position
        derived = str(reference)

    point = ReferencePoint(
        name=feature_name(arguments, "point"),
        position=tuple(origin[i] + at[i] for i in range(3)),  # type: ignore[arg-type]
        derived_from=derived,
        description=f"at {list(at)}" + (f" from {reference}" if reference else ""),
    )
    document.add_point(point)
    return {"feature": point.name, **point.to_dict(), "points": document.point_names()}


def point_between(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A point a proportion of the way along the line joining two others.

    `ratio` is unbounded on purpose: 0.5 is the midpoint, 0 and 1 are the ends, and a
    value outside [0, 1] extrapolates beyond them. CATIA allows that and it is genuinely
    useful — "one diameter past the flange" is an extrapolation — so it is not clamped.
    """
    document = context.require_document()
    names = arguments.get("points")
    if not isinstance(names, (list, tuple)) or len(names) != 2:
        raise GeometryError(
            f"{POINT_BETWEEN} needs exactly two point names, got {names!r}."
        )

    first = document.point(str(names[0])).position
    second = document.point(str(names[1])).position
    ratio = arguments.get("ratio")
    ratio = 0.5 if ratio is None else float(ratio)

    point = ReferencePoint(
        name=feature_name(arguments, "point"),
        position=tuple(  # type: ignore[arg-type]
            first[i] + (second[i] - first[i]) * ratio for i in range(3)
        ),
        derived_from=f"{names[0]}, {names[1]}",
        description=f"{ratio} of the way from {names[0]} to {names[1]}",
    )
    document.add_point(point)
    return {"feature": point.name, **point.to_dict(), "points": document.point_names()}


# -- planes from points and angles ---------------------------------------------


def plane_through_points(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """A plane through three named points."""
    document = context.require_document()
    names = arguments.get("points")
    if not isinstance(names, (list, tuple)) or len(names) != 3:
        raise GeometryError(
            f"{PLANE_THROUGH_POINTS} needs exactly three point names, got {names!r}. "
            "Two points define a line, not a plane."
        )

    positions = [document.point(str(name)).position for name in names]
    plane = ReferencePlane(
        name=feature_name(arguments, "plane"),
        frame=frame_from_points(*positions),
        derived_from=", ".join(str(name) for name in names),
        description=f"through {', '.join(str(n) for n in names)}",
    )
    document.add_plane(plane)
    return {"feature": plane.name, **plane.to_dict(), "planes": document.plane_names()}


def plane_angle(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A plane at an angle to another, hinged about an axis.

    The whole frame is rotated, not just the normal, so the result keeps the reference's
    local X turned with it — a sketch on the angled plane is then oriented the way the
    reference was, rather than square to nothing in particular.
    """
    document = context.require_document()

    reference = arguments.get("reference")
    if reference is None:
        raise GeometryError(f"{PLANE_ANGLE} needs a reference plane to measure from.")

    raw_angle = arguments.get("angle_deg")
    if not isinstance(raw_angle, (int, float)) or isinstance(raw_angle, bool):
        raise GeometryError(
            f"{PLANE_ANGLE} needs angle_deg as a number of degrees, got {raw_angle!r}."
        )

    base = _reference_frame(document, str(reference))
    hinge_origin, hinge_direction = _hinge_axis(document, arguments.get("axis"))

    plane = ReferencePlane(
        name=feature_name(arguments, "plane"),
        frame=rotated_frame(base, hinge_origin, hinge_direction, float(raw_angle)),
        derived_from=str(reference),
        description=f"{reference} rotated {raw_angle}° about {arguments.get('axis')}",
    )
    document.add_plane(plane)
    return {"feature": plane.name, **plane.to_dict(), "planes": document.plane_names()}


def _hinge_axis(
    document: Any, axis: Any
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """The line a plane rotates about: a world axis, or one of an axis system's.

    `"X"` is the world X through the origin. `"frame.x"` is the X of an axis system the
    design built, through *its* origin — which is the form that matters, because hinging
    about a world axis only ever tilts a plane through the world origin.
    """
    if axis is None:
        raise GeometryError(
            f"{PLANE_ANGLE} needs an axis to hinge about. Use a world axis "
            f"({', '.join(sorted(_WORLD_AXES))}), or 'name.x' naming an axis of an axis "
            "system this design built."
        )

    text = str(axis)
    if text.upper() in _WORLD_AXES:
        return ((0.0, 0.0, 0.0), _WORLD_AXES[text.upper()])

    if "." in text:
        system_name, letter = text.rsplit(".", 1)
        if document.has_axis_system(system_name):
            system = document.axis_system(system_name)
            return (system.origin_mm(), system.axis(letter))

    known = ", ".join(document.axis_system_names()) or "none yet"
    raise OperationNotSupported(
        f"{PLANE_ANGLE} about {axis!r}",
        f"the world axes are {', '.join(sorted(_WORLD_AXES))} and this design has built "
        f"these axis systems: {known} (name an axis of one as 'system.x'). Hinging about "
        "an edge of the part needs the feature#selector syntax (Phase 2.2)",
    )


# -- planes derived from geometry ------------------------------------------------
#
# The associative planes: each is defined by a curve or a surface rather than typed as an
# offset, so it follows what it was taken from. `plane_normal_to_curve` is the one that
# unlocks the most — it is what places a sweep profile square to its path, which is the
# first step of a pipe, a cable run or a swept rib, and it is why the helix that landed
# earlier is now something a section can actually be swept along.


def plane_normal_to_curve(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """A plane perpendicular to a curve, at a point on it or at its start.

    **The normal is the tangent at that point**, so the plane cuts the curve square. Given
    no point it stands at the curve's start — the end that runs first in the curve's own
    order, which for a drawn curve is the first point the design named.
    """
    from app.kernel.occt.operations.curves import (
        closest_curve_tangent,
        start_of_curve,
    )
    from app.kernel.occt.operations.surfaces import named_geometry

    document = context.require_document()
    named = arguments.get("curve")
    curve = named_geometry(document, named, tool=PLANE_NORMAL_TO_CURVE, argument="curve")

    at = arguments.get("point")
    if at is None:
        origin, tangent = start_of_curve(curve, tool=PLANE_NORMAL_TO_CURVE)
        where = f"the start of {named}"
    else:
        from app.kernel.occt.operations.curves import _point_named

        wanted = _point_named(document, at, tool=PLANE_NORMAL_TO_CURVE, argument="point")
        origin, tangent = closest_curve_tangent(curve, wanted, tool=PLANE_NORMAL_TO_CURVE)
        where = f"{at} on {named}"

    plane = ReferencePlane(
        name=feature_name(arguments, "plane"),
        frame=frame_from_normal(origin, tangent),
        derived_from=str(named),
        description=f"perpendicular to {named} at {where}",
    )
    document.add_plane(plane)
    return {"feature": plane.name, **plane.to_dict(), "planes": document.plane_names()}


def plane_tangent_to_surface(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """A plane touching a surface at a point on it — somewhere to sketch on a curved wall.

    The plane sits at the point **projected onto the surface**, not at the point as given:
    an anchor a millimetre off the wall would otherwise put the plane a millimetre off it
    too, and a boss built there would float. Same rule as `catia_point_on_surface`, and it
    matters more here because a plane hides the gap.
    """
    from app.kernel.occt.operations.curves import _point_named, closest_face_normal
    from app.kernel.occt.operations.surfaces import named_geometry

    document = context.require_document()
    named = arguments.get("surface")
    surface = named_geometry(
        document, named, tool=PLANE_TANGENT_TO_SURFACE, argument="surface"
    )
    at = _point_named(
        document, arguments.get("point"), tool=PLANE_TANGENT_TO_SURFACE, argument="point"
    )

    _, foot, normal = closest_face_normal(surface, at, tool=PLANE_TANGENT_TO_SURFACE)
    plane = ReferencePlane(
        name=feature_name(arguments, "plane"),
        frame=frame_from_normal(foot, normal),
        derived_from=str(named),
        description=f"tangent to {named} at {arguments.get('point')}",
    )
    document.add_plane(plane)
    return {"feature": plane.name, **plane.to_dict(), "planes": document.plane_names()}


def plane_mean(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """The best-fit plane through a set of points — a working plane out of measured data.

    Least squares, computed exactly by OCCT rather than fitted iteratively: see
    `reference.least_spread_axis` for why a principal axis of inertia answers a covariance
    question. Through exactly three points it reproduces `catia_plane_through_points`,
    because three points have a plane and a fit through them has no residual to trade.

    **The payload says how far off the worst point is.** A mean plane through points that
    are not coplanar is an average, and an average presented without its spread reads as a
    fact — an engineer who cannot see that a point stands 4 mm off it will build to it as
    if nothing did.
    """
    document = context.require_document()
    names = arguments.get("points")
    if not isinstance(names, (list, tuple)) or len(names) < 3:
        raise GeometryError(
            f"{PLANE_MEAN} fits a plane through at least three points, got {names!r}. "
            "Two points define a line, not a plane."
        )

    positions = [document.point(str(name)).position for name in names]
    properties = symbol("GProp_PGProps")()
    for position in positions:
        properties.AddPoint(symbol("gp_Pnt")(*position))

    normal, definition = least_spread_axis(properties)
    if definition < _COLLINEAR_DEFINITION:
        raise GeometryError(
            f"{PLANE_MEAN} was given {len(positions)} points that lie on one line, and a "
            "line has no plane — every plane through it fits equally well. Move one of "
            "them off the line through the others."
        )

    centre = properties.CentreOfMass()
    origin = (centre.X(), centre.Y(), centre.Z())
    frame = frame_from_normal(origin, normal)

    deviation = max(
        abs(sum((position[i] - origin[i]) * normal[i] for i in range(3)))
        for position in positions
    )
    plane = ReferencePlane(
        name=feature_name(arguments, "plane"),
        frame=frame,
        derived_from=", ".join(str(name) for name in names),
        description=(
            f"best fit through {len(positions)} points, worst of them {deviation:.4g} mm off"
        ),
    )
    document.add_plane(plane)
    return {
        "feature": plane.name,
        **plane.to_dict(),
        "deviation_mm": deviation,
        "planes": document.plane_names(),
    }


def planes_between(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A run of equally spaced planes between two others — the setup for a sectioning study.

    **`count` planes strictly between the two**, evenly spaced, neither end included: the
    two ends already exist and naming them again would give the design two names for one
    plane. So `count: 3` between planes 0 mm and 40 mm apart lands them at 10, 20 and 30.

    Non-parallel planes are **refused**. Two planes that meet have no single spacing
    between them — the gap depends on where you measure it — so the run would be evenly
    spaced only along the line the implementation happened to walk.
    """
    document = context.require_document()
    first = _reference_frame(document, arguments.get("first"), tool=PLANES_BETWEEN)
    second = _reference_frame(document, arguments.get("second"), tool=PLANES_BETWEEN)

    raw = arguments.get("count")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise GeometryError(
            f"{PLANES_BETWEEN} needs count as a whole number of planes to create, at "
            f"least 1; got {raw!r}."
        )

    normal = first.Direction()
    other = second.Direction()
    if not normal.IsParallel(other, _PARALLEL_TOLERANCE_RAD):
        raise GeometryError(
            f"{PLANES_BETWEEN} needs two parallel planes, and "
            f"{arguments.get('first')!r} and {arguments.get('second')!r} meet at an "
            "angle. Two planes that cross have no single distance between them."
        )

    here, there = first.Location(), second.Location()
    gap = (
        (there.X() - here.X()) * normal.X()
        + (there.Y() - here.Y()) * normal.Y()
        + (there.Z() - here.Z()) * normal.Z()
    )
    if abs(gap) < _COINCIDENT_TOLERANCE_MM:
        raise GeometryError(
            f"{PLANES_BETWEEN} was given two planes in the same place, so there is no "
            "space between them to fill. Offset one of them first."
        )

    step = gap / (raw + 1)
    made = []
    for index in range(1, raw + 1):
        plane = ReferencePlane(
            name=f"{feature_name(arguments, 'between')}.{index}",
            frame=offset_frame(first, step * index),
            derived_from=f"{arguments.get('first')}, {arguments.get('second')}",
            description=(
                f"{index} of {raw} between {arguments.get('first')} and "
                f"{arguments.get('second')}, {step * index:.4g} mm from the first"
            ),
        )
        document.add_plane(plane)
        made.append(plane)

    return {
        "feature": made[0].name,
        "planes_created": [plane.name for plane in made],
        "spacing_mm": abs(step),
        "planes": document.plane_names(),
    }


# -- axis systems ---------------------------------------------------------------


def axis_system(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A local coordinate system at a named point.

    `set_current` is accepted and refused rather than ignored. Making an axis system
    "current" changes the frame every *later* operation's coordinates are read in, which
    is a large, invisible change to everything downstream — and the design IR has no way
    to express it, so a plan carrying one would mean different things on the two
    backends. That is exactly the divergence the two-backend design exists to prevent.
    """
    document = context.require_document()

    if arguments.get("set_current"):
        raise OperationNotSupported(
            f"{AXIS_SYSTEM} with set_current",
            "making an axis system current silently changes the frame every later "
            "operation is read in, and the design IR has no way to record that — so the "
            "same plan would mean different things on the two backends. State "
            "coordinates in the part's own frame instead",
        )

    origin_name = arguments.get("origin")
    if origin_name is None:
        raise GeometryError(
            f"{AXIS_SYSTEM} needs an origin — the name of a point it sits at. Build one "
            "with catia_point_at."
        )
    origin = document.point(str(origin_name)).position

    system = AxisSystem(
        name=feature_name(arguments, "axis_system"),
        frame=axis_frame(
            origin,
            _optional_vector(arguments.get("x_direction"), "x_direction", AXIS_SYSTEM),
            _optional_vector(arguments.get("y_direction"), "y_direction", AXIS_SYSTEM),
        ),
        derived_from=str(origin_name),
    )
    document.add_axis_system(system)
    return {
        "feature": system.name,
        **system.to_dict(),
        "axis_systems": document.axis_system_names(),
    }


# -- shared argument reading ----------------------------------------------------


def _as_vector(value: Any, *, argument: str, tool: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise GeometryError(
            f"{tool} needs {argument} as [x, y, z] in millimetres, got {value!r}."
        )
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError) as exc:
        raise GeometryError(
            f"{tool}'s {argument} must be three numbers, got {value!r}."
        ) from exc


# -- points derived from geometry ---------------------------------------------
#
# These three are what make a point *associative*: instead of measuring a place and
# typing the coordinate — which goes stale the moment the geometry changes — the point is
# defined by the geometry and follows it. The evaluation lives in
# `operations/curves.py`, shared with the line operations, so a point placed on a curve
# and a tangent read at it cannot end up describing two different places on it.


def point_on_curve(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A point on a curve, by proportion of its length or by length along it.

    Both are **arc length**, never parameter. A B-spline's parameter is not proportional
    to its length, so `ratio: 0.5` read as a parameter lands at the midpoint of nothing —
    a mistake that is invisible on a straight line and wrong on every curve worth putting
    a point on.
    """
    from app.kernel.occt.operations.curves import point_along_curve
    from app.kernel.occt.operations.surfaces import named_geometry

    document = context.require_document()
    named = arguments.get("curve")
    curve = named_geometry(document, named, tool=POINT_ON_CURVE, argument="curve")

    ratio = arguments.get("ratio")
    distance = arguments.get("distance_mm")
    position = point_along_curve(
        curve,
        ratio=None if ratio is None else float(ratio),
        distance_mm=None if distance is None else float(distance),
        from_end=bool(arguments.get("from_end")),
        tool=POINT_ON_CURVE,
    )

    where = (
        f"{float(ratio):g} of the way along"
        if ratio is not None
        else (f"{float(distance):g} mm along" if distance is not None else "halfway along")
    )
    return _derived_point(
        document,
        arguments,
        position,
        derived_from=str(named),
        description=f"{where} {named}" + (" from its far end" if arguments.get("from_end") else ""),
    )


def point_on_surface(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A point on a surface, offset from a reference point along a direction.

    **The offset point is projected back onto the surface**, which is the whole operation:
    moving along a direction leaves a curved face, and a point that has drifted off it is
    not an anchor for anything. On a flat wall the projection changes nothing; on a
    curved one it is the difference between a fastener on the face and one in the air.
    """
    from app.kernel.occt.operations.curves import closest_on_surface
    from app.kernel.occt.operations.surfaces import named_geometry

    document = context.require_document()
    named = arguments.get("surface")
    surface = named_geometry(document, named, tool=POINT_ON_SURFACE, argument="surface")

    reference = arguments.get("reference")
    if reference is None:
        # No anchor named: the surface's own centre of area, which is a place derived
        # from the surface rather than a corner of its bounding box.
        start = _area_centre_of(surface)
    else:
        from app.kernel.occt.operations.curves import _point_named

        start = _point_named(document, reference, tool=POINT_ON_SURFACE, argument="reference")

    distance = float(arguments.get("distance_mm") or 0.0)
    if distance:
        along = _as_vector(
            arguments.get("direction"), argument="direction", tool=POINT_ON_SURFACE
        )
        span = math.sqrt(sum(value * value for value in along))
        if span < 1e-12:
            raise GeometryError(
                f"{POINT_ON_SURFACE} was given a distance to move and a direction of zero "
                "length, which points nowhere."
            )
        start = (
            start[0] + along[0] / span * distance,
            start[1] + along[1] / span * distance,
            start[2] + along[2] / span * distance,
        )

    _, position, _ = closest_on_surface(surface, start, tool=POINT_ON_SURFACE)
    return _derived_point(
        document,
        arguments,
        position,
        derived_from=str(named),
        description=f"on {named}" + (f", {distance:g} mm from {reference}" if distance else ""),
    )


def point_centre(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """The centre of a circle, arc, sphere or planar face.

    The reliable way to reference the middle of an existing feature, rather than measuring
    it once and typing a coordinate that then goes stale. Each kind has an **exact**
    centre — a circle's own, a sphere's own, a planar face's centroid — so nothing here is
    a fit or an average of samples.
    """
    from app.kernel.occt import classify
    from app.kernel.occt.operations.surfaces import named_geometry
    from app.kernel.occt.topology import edges, faces

    document = context.require_document()
    named = arguments.get("element")
    element = named_geometry(document, named, tool=POINT_CENTRE, argument="element")

    found_edges, found_faces = edges(element), faces(element)
    if len(found_edges) == 1 and not found_faces:
        edge = found_edges[0]
        if classify.edge_curve_type(edge) != "Circle":
            raise GeometryError(
                f"{POINT_CENTRE} was given {named!r}, which is a "
                f"{classify.edge_curve_type(edge).lower()} rather than a circle or an arc. "
                "A curve with no centre has none to report."
            )
        location = symbol("BRepAdaptor_Curve")(edge).Circle().Location()
        kind = "circle"
    elif len(found_faces) == 1:
        face = found_faces[0]
        surface = classify.face_surface_type(face)
        if surface == "Sphere":
            location = symbol("BRepAdaptor_Surface")(face).Sphere().Location()
            kind = "sphere"
        elif surface == "Plane":
            properties = symbol("GProp_GProps")()
            symbol("BRepGProp").SurfaceProperties_s(face, properties)
            location = properties.CentreOfMass()
            kind = "planar face"
        else:
            raise GeometryError(
                f"{POINT_CENTRE} was given a {surface.lower()} face. A circle, an arc, a "
                "sphere or a flat face has a centre; a general surface does not."
            )
    else:
        raise GeometryError(
            f"{POINT_CENTRE} takes one circle, arc, sphere or planar face, and {named!r} "
            f"holds {len(found_edges)} edges and {len(found_faces)} faces. Name one of "
            "them — catia_list_faces and catia_list_edges report what is there."
        )

    return _derived_point(
        document,
        arguments,
        (location.X(), location.Y(), location.Z()),
        derived_from=str(named),
        description=f"the centre of the {kind} {named}",
    )


def _derived_point(
    document: Any,
    arguments: Mapping[str, Any],
    position: tuple[float, float, float],
    *,
    derived_from: str,
    description: str,
) -> Mapping[str, Any]:
    """File a computed point under the design's own name, in the one point store."""
    point = ReferencePoint(
        name=feature_name(arguments, "point"),
        position=position,
        derived_from=derived_from,
        description=description,
    )
    document.add_point(point)
    return {"feature": point.name, **point.to_dict(), "points": document.point_names()}


def _area_centre_of(shape: Any) -> tuple[float, float, float]:
    properties = symbol("GProp_GProps")()
    symbol("BRepGProp").SurfaceProperties_s(shape, properties)
    point = properties.CentreOfMass()
    return (point.X(), point.Y(), point.Z())


def _optional_vector(
    value: Any, argument: str, tool: str
) -> tuple[float, float, float] | None:
    return None if value is None else _as_vector(value, argument=argument, tool=tool)


__all__ = [
    "AXIS_SYSTEM",
    "PLANES_BETWEEN",
    "PLANE_ANGLE",
    "PLANE_MEAN",
    "PLANE_NORMAL_TO_CURVE",
    "PLANE_OFFSET",
    "PLANE_TANGENT_TO_SURFACE",
    "PLANE_THROUGH_POINTS",
    "POINT_AT",
    "POINT_BETWEEN",
    "POINT_CENTRE",
    "POINT_ON_CURVE",
    "POINT_ON_SURFACE",
    "axis_system",
    "plane_angle",
    "plane_mean",
    "plane_normal_to_curve",
    "plane_offset",
    "plane_tangent_to_surface",
    "plane_through_points",
    "planes_between",
    "point_at",
    "point_between",
    "point_centre",
    "point_on_curve",
    "point_on_surface",
]
