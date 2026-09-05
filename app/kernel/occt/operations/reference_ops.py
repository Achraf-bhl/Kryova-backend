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

from collections.abc import Mapping
from typing import Any

from app.catia.ops import vocabulary
from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt.operations.context import BuildContext, feature_name
from app.kernel.occt.reference import (
    AxisSystem,
    ReferencePlane,
    ReferencePoint,
    axis_frame,
    frame_from_points,
    offset_frame,
    rotated_frame,
)
from app.kernel.occt.sketching import frame_of

PLANE_OFFSET = "catia_plane_offset"
PLANE_THROUGH_POINTS = "catia_plane_through_points"
PLANE_ANGLE = "catia_plane_angle"
POINT_AT = "catia_point_at"
POINT_BETWEEN = "catia_point_between"
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


def _reference_frame(document: Any, reference: str) -> Any:
    """The frame of an origin plane or a plane the design constructed.

    Offsetting from a *planar face* is the third thing the operation's schema allows and
    it is refused, not approximated: naming a face needs `feature#selector` (Phase 2.2),
    and picking the nearest origin plane instead would build the boss at a height nobody
    chose — the kind of wrong that looks right until it is measured.
    """
    if reference.upper() in vocabulary.ORIGIN_PLANES:
        return frame_of(reference)
    if document.has_plane(reference):
        return document.plane(reference).frame

    known = ", ".join(document.plane_names()) or "none yet"
    raise OperationNotSupported(
        f"{PLANE_OFFSET} from {reference!r}",
        f"the origin planes are {', '.join(vocabulary.ORIGIN_PLANES)} and this design "
        f"has constructed: {known}. Offsetting from a face of the part needs the "
        "feature#selector syntax (Phase 2.2)",
    )


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


def _optional_vector(
    value: Any, argument: str, tool: str
) -> tuple[float, float, float] | None:
    return None if value is None else _as_vector(value, argument=argument, tool=tool)


__all__ = [
    "AXIS_SYSTEM",
    "PLANE_ANGLE",
    "PLANE_OFFSET",
    "PLANE_THROUGH_POINTS",
    "POINT_AT",
    "POINT_BETWEEN",
    "axis_system",
    "plane_angle",
    "plane_offset",
    "plane_through_points",
    "point_at",
    "point_between",
]
