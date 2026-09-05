"""Sketch operations: creating a sketch and drawing dimension-driven profiles on it.

Every profile here is fully determined by its arguments, which is why none of them needs
a constraint solver — see `app.kernel.occt.sketching` for why that is a property of the
registry's vocabulary rather than a shortcut. `catia_sketch_constrain` is the operation
that genuinely needs one, and it refuses with the reason until PlaneGCS lands.

**A sketch is addressed by name, not by position.** `catia_pad(sketch=@plate.profile)`
resolves through the design's semantic name, so the profile a feature consumes is the one
the author named — never "the last sketch drawn", which is how a second sketch silently
steals a pad.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.catia.ops import vocabulary
from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt.operations.context import (
    BuildContext,
    as_point,
    as_positive_length,
    feature_name,
)
from app.kernel.occt.sketching import (
    Sketch,
    arc_edge,
    arc_through_edge,
    circle_wire,
    closed_wire,
    ellipse_wire,
    frame_of,
    line_edge,
    polygon_corners,
    rectangle_corners,
    slot_wire,
    spline_edge,
)

CREATE = "catia_sketch_create"
RECTANGLE = "catia_sketch_rectangle"
CIRCLE = "catia_sketch_circle"
POLYGON = "catia_sketch_polygon"
SLOT = "catia_sketch_slot"
CLOSE = "catia_sketch_close"
CONSTRAIN = "catia_sketch_constrain"
POINT = "catia_sketch_point"
LINE = "catia_sketch_line"
POLYLINE = "catia_sketch_polyline"
ARC = "catia_sketch_arc"
ARC_THREE_POINT = "catia_sketch_arc_three_point"
ELLIPSE = "catia_sketch_ellipse"
SPLINE = "catia_sketch_spline"
AXIS = "catia_sketch_axis"


def sketch_create(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Open a sketch on an origin plane or a plane the design constructed."""
    document = context.require_document()
    support = str(arguments["support"])
    name = feature_name(arguments, "sketch")
    origin = as_point(arguments.get("origin"), argument="origin")

    sketch = Sketch(
        name=name,
        support=support,
        origin=origin,
        frame_ax3=resolve_support(document, support, origin, tool=CREATE),
    )
    document.add_sketch(sketch)
    return {"feature": sketch.name, "sketch": sketch.name, **sketch.to_dict()}


def resolve_support(
    document: Any,
    support: str,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    *,
    tool: str,
) -> Any:
    """Turn a support name into a frame: an origin plane, or one the design built.

    **Origin planes are tried first, and the order is not arbitrary.** `XY`, `YZ` and `ZX`
    are vocabulary, not names — `app.design.names` refuses them as the first segment of a
    semantic name for exactly this reason — so a constructed plane can never shadow one,
    and checking the vocabulary first makes that guarantee local rather than something to
    trust from another module.

    Sketching on a *face* of the part is the remaining case, and it is refused with the
    reason rather than approximated: naming a face needs `feature#selector` (Phase 2.2),
    and quietly falling back to the nearest origin plane would put the profile on a plane
    the author did not choose.
    """
    from app.kernel.occt.reference import translated_frame

    if str(support).upper() in vocabulary.ORIGIN_PLANES:
        return frame_of(support, origin)

    if document.has_plane(support):
        base = document.plane(support).frame
        return translated_frame(base, origin) if any(origin) else base

    known = ", ".join(document.plane_names()) or "none yet"
    raise GeometryError(
        f"{tool} cannot find a plane called {support!r}. The origin planes are "
        f"{', '.join(vocabulary.ORIGIN_PLANES)}; planes this design has constructed: "
        f"{known}. Build one with catia_plane_offset, or sketch on an origin plane. "
        "(Sketching directly on a face of the part needs the feature#selector syntax, "
        "which is Phase 2.2.)"
    )


def sketch_rectangle(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    sketch = _target_sketch(context, arguments, RECTANGLE)
    corners = rectangle_corners(
        as_positive_length(arguments.get("width_mm"), argument="width_mm", tool=RECTANGLE),
        as_positive_length(arguments.get("height_mm"), argument="height_mm", tool=RECTANGLE),
        _at(arguments),
        float(arguments.get("rotation_deg") or 0.0),
    )
    return _add_profile(sketch, closed_wire(sketch, corners), arguments)


def sketch_circle(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    sketch = _target_sketch(context, arguments, CIRCLE)
    diameter = as_positive_length(
        arguments.get("diameter_mm"), argument="diameter_mm", tool=CIRCLE
    )
    return _add_profile(sketch, circle_wire(sketch, _at(arguments), diameter), arguments)


def sketch_polygon(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    sketch = _target_sketch(context, arguments, POLYGON)
    corners = polygon_corners(
        int(arguments["sides"]),
        as_positive_length(arguments.get("diameter_mm"), argument="diameter_mm", tool=POLYGON),
        _at(arguments),
        float(arguments.get("rotation_deg") or 0.0),
    )
    return _add_profile(sketch, closed_wire(sketch, corners), arguments)


def sketch_slot(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    sketch = _target_sketch(context, arguments, SLOT)
    wire = slot_wire(
        sketch,
        _uv(arguments.get("start"), "start"),
        _uv(arguments.get("end"), "end"),
        as_positive_length(arguments.get("width_mm"), argument="width_mm", tool=SLOT),
    )
    return _add_profile(sketch, wire, arguments)


def sketch_point(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Place a point in the sketch, by its own 2D coordinates.

    A point is never a profile: it goes on `Sketch.points`, not `Sketch.profiles`, so a
    pad over this sketch extrudes the outlines and ignores the points. That separation
    is what lets a user pattern draw its positions in the same sketch as the shape being
    positioned, which is how CATIA does it.

    `construction` is accepted and makes no difference here, because a sketch point is
    already reference geometry — there is nothing for the flag to switch off. Accepting
    it rather than refusing it keeps a design that sets it from failing over a no-op.
    """
    sketch = _target_sketch(context, arguments, POINT)
    at = _uv(arguments.get("at"), "at")
    sketch.points.append(at)
    return {
        **sketch.to_dict(),
        "point": list(at),
        "world_mm": list(sketch.to_world(at)),
    }


def sketch_line(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Draw a straight line, chaining it onto the contour being drawn."""
    sketch = _target_sketch(context, arguments, LINE)
    start = _uv(arguments.get("start"), "start")
    end = _uv(arguments.get("end"), "end")
    return _add_segment(sketch, line_edge(sketch, start, end), start, end, arguments)


def sketch_polyline(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Draw a connected run of straight lines through a list of points.

    Each segment goes through the same chaining as a single line, so a polyline can
    continue a contour that lines started and a following line can continue it — the
    two operations are one drawing action, not two kinds of geometry.
    """
    sketch = _target_sketch(context, arguments, POLYLINE)
    corners = _uv_list(arguments.get("points"), "points", minimum=2)
    if arguments.get("closed"):
        corners = corners + [corners[0]]

    outcome = ""
    for start, end in zip(corners, corners[1:], strict=False):
        outcome = _chain(sketch, line_edge(sketch, start, end), start, end, arguments)
    return {"feature": sketch.name, "outcome": outcome, **sketch.to_dict()}


def sketch_arc(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Draw a circular arc from a centre, a radius and two angles."""
    sketch = _target_sketch(context, arguments, ARC)
    edge, start, end = arc_edge(
        sketch,
        _uv(arguments.get("centre"), "centre"),
        as_positive_length(arguments.get("radius_mm"), argument="radius_mm", tool=ARC),
        float(arguments["start_angle_deg"]),
        float(arguments["end_angle_deg"]),
    )
    return _add_segment(sketch, edge, start, end, arguments)


def sketch_arc_three_point(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Draw a circular arc through three points: start, a point on it, end."""
    sketch = _target_sketch(context, arguments, ARC_THREE_POINT)
    start = _uv(arguments.get("start"), "start")
    through = _uv(arguments.get("through"), "through")
    end = _uv(arguments.get("end"), "end")
    edge = arc_through_edge(sketch, start, through, end)
    return _add_segment(sketch, edge, start, end, arguments)


def sketch_spline(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Draw a smooth spline through a list of points."""
    sketch = _target_sketch(context, arguments, SPLINE)
    through = _uv_list(arguments.get("points"), "points", minimum=2)
    closed = bool(arguments.get("closed"))
    edge = spline_edge(sketch, through, closed=closed)
    end = through[0] if closed else through[-1]
    return _add_segment(sketch, edge, through[0], end, arguments)


def sketch_ellipse(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Draw an ellipse — a closed profile, like a circle."""
    sketch = _target_sketch(context, arguments, ELLIPSE)
    wire = ellipse_wire(
        sketch,
        _uv(arguments.get("centre"), "centre"),
        as_positive_length(
            arguments.get("major_radius_mm"), argument="major_radius_mm", tool=ELLIPSE
        ),
        as_positive_length(
            arguments.get("minor_radius_mm"), argument="minor_radius_mm", tool=ELLIPSE
        ),
        float(arguments.get("rotation_deg") or 0.0),
    )
    return _add_profile(sketch, wire, arguments)


def sketch_axis(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Draw the sketch's revolution axis.

    Recorded on the sketch rather than added to its geometry: an axis is a reference a
    shaft or groove revolves about, and letting it into `profiles` would put a stray
    line into the face a pad extrudes. A sketch has exactly one, so drawing a second
    replaces the first — which is what redrawing an axis means in CATIA.
    """
    sketch = _target_sketch(context, arguments, AXIS)
    start = _uv(arguments.get("start"), "start")
    end = _uv(arguments.get("end"), "end")
    if start == end:
        raise GeometryError(
            "The axis begins and ends at the same point, so it has no direction. Give "
            "two different points on the line the profile turns about."
        )
    sketch.axis = (start, end)
    return {"feature": sketch.name, "axis": [list(start), list(end)], **sketch.to_dict()}


def sketch_close(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Finish a sketch.

    Nothing to do geometrically — profiles here are closed as they are drawn — but the
    operation exists in the vocabulary and a design that calls it must not fail. It
    reports the state so a caller can see what was drawn.
    """
    sketch = _target_sketch(context, arguments, CLOSE)
    # An open run counts as something drawn: a rib's spine sketch holds a curve and no
    # profile at all, and refusing to close it would make every swept feature start with
    # an error. What is refused is a sketch with *nothing* on it.
    if sketch.is_empty and not sketch.curves:
        raise GeometryError(
            f"Sketch {sketch.name!r} is being closed with nothing drawn on it. A feature "
            "consuming it would have no profile to build from."
        )
    return {"feature": sketch.name, **sketch.to_dict()}


def sketch_constrain(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Apply a constraint to sketch geometry — the one operation needing a solver."""
    raise OperationNotSupported(
        CONSTRAIN,
        "Constraining free geometry needs a 2D solver (PlaneGCS, master plan 1.3). The "
        "dimension-driven profiles — rectangle, circle, polygon, slot — are fully "
        "determined by their arguments and need no constraints",
    )


# -- helpers -----------------------------------------------------------------


def _target_sketch(context: BuildContext, arguments: Mapping[str, Any], tool: str) -> Sketch:
    """Which sketch this operation draws into.

    Named explicitly where the design says so. Falling back to the most recent sketch
    matches the registry's optional `sketch` argument, but only when exactly one is
    open — guessing between several is how a profile lands on the wrong plane.
    """
    document = context.require_document()
    named = arguments.get("sketch")
    if named:
        return document.sketch(str(named))

    if not document.sketches:
        raise GeometryError(
            f"{tool} needs a sketch to draw into, and none is open. Call "
            "catia_sketch_create first."
        )
    if len(document.sketches) > 1:
        known = ", ".join(document.sketch_names())
        raise GeometryError(
            f"{tool} did not say which sketch to draw into and this part has several: "
            f"{known}. Name one explicitly."
        )
    return next(iter(document.sketches.values()))


def _add_profile(sketch: Sketch, wire: Any, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """File a finished wire as either real profile or construction geometry."""
    if arguments.get("construction"):
        sketch.construction.append(wire)
    else:
        sketch.profiles.append(wire)
    # A profile leaves no tree row of its own -- it is geometry *inside* a sketch. The
    # compiler already knows this (such features are "unaddressable"), so the result
    # reports the sketch rather than inventing a name for something unnameable.
    return {"feature": sketch.name, **sketch.to_dict()}


def _add_segment(
    sketch: Sketch,
    edge: Any,
    start: tuple[float, float],
    end: tuple[float, float],
    arguments: Mapping[str, Any],
) -> Mapping[str, Any]:
    """File one drawn segment and report what it did to the contour."""
    outcome = _chain(sketch, edge, start, end, arguments)
    return {"feature": sketch.name, "outcome": outcome, **sketch.to_dict()}


def _chain(
    sketch: Sketch,
    edge: Any,
    start: tuple[float, float],
    end: tuple[float, float],
    arguments: Mapping[str, Any],
) -> str:
    """Add a segment to the sketch, or file it as construction geometry.

    **Construction segments do not chain.** Construction geometry positions other
    elements — an axis line, a bolt-circle diameter — and is not a contour under
    construction, so joining two of them into a run would only make the eventual
    `profiles` promotion fire on geometry that must never become a face.
    """
    if arguments.get("construction"):
        sketch.construction.append(edge)
        return "construction"
    return sketch.add_segment(edge, start, end)


def _at(arguments: Mapping[str, Any]) -> tuple[float, float]:
    return _uv(arguments.get("at"), "at", default=(0.0, 0.0))


def _uv_list(
    value: Any, argument: str, *, minimum: int
) -> list[tuple[float, float]]:
    """A list of sketch-local points, in order."""
    if not isinstance(value, (list, tuple)) or len(value) < minimum:
        raise GeometryError(
            f"{argument} must be a list of at least {minimum} points, each two numbers "
            f"[u, v] in the sketch's own axes; got {value!r}."
        )
    return [_uv(item, f"{argument}[{index}]") for index, item in enumerate(value)]


def _uv(
    value: Any, argument: str, default: tuple[float, float] | None = None
) -> tuple[float, float]:
    """A sketch-local (u, v) point. Two numbers, in the sketch's own axes."""
    if value is None:
        if default is None:
            raise GeometryError(f"{argument} is required and was not given.")
        return default
    if isinstance(value, Mapping):
        return (float(value.get("u", value.get("x", 0.0))), float(value.get("v", value.get("y", 0.0))))
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return (float(value[0]), float(value[1]))
    raise GeometryError(
        f"{argument} must be two numbers [u, v] in the sketch's own axes, got {value!r}."
    )


__all__ = [
    "ARC",
    "ARC_THREE_POINT",
    "AXIS",
    "CIRCLE",
    "CLOSE",
    "CONSTRAIN",
    "CREATE",
    "ELLIPSE",
    "LINE",
    "POINT",
    "POLYGON",
    "POLYLINE",
    "RECTANGLE",
    "SLOT",
    "SPLINE",
    "sketch_arc",
    "sketch_arc_three_point",
    "sketch_axis",
    "sketch_circle",
    "sketch_close",
    "sketch_constrain",
    "sketch_create",
    "sketch_ellipse",
    "sketch_line",
    "sketch_point",
    "sketch_polygon",
    "sketch_polyline",
    "sketch_rectangle",
    "sketch_slot",
    "sketch_spline",
]
