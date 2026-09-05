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
    circle_wire,
    closed_wire,
    frame_of,
    polygon_corners,
    rectangle_corners,
    slot_wire,
)

CREATE = "catia_sketch_create"
RECTANGLE = "catia_sketch_rectangle"
CIRCLE = "catia_sketch_circle"
POLYGON = "catia_sketch_polygon"
SLOT = "catia_sketch_slot"
CLOSE = "catia_sketch_close"
CONSTRAIN = "catia_sketch_constrain"
POINT = "catia_sketch_point"


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


def sketch_close(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Finish a sketch.

    Nothing to do geometrically — profiles here are closed as they are drawn — but the
    operation exists in the vocabulary and a design that calls it must not fail. It
    reports the state so a caller can see what was drawn.
    """
    sketch = _target_sketch(context, arguments, CLOSE)
    if sketch.is_empty:
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


def _at(arguments: Mapping[str, Any]) -> tuple[float, float]:
    return _uv(arguments.get("at"), "at", default=(0.0, 0.0))


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
    "CIRCLE",
    "CLOSE",
    "CONSTRAIN",
    "CREATE",
    "POINT",
    "POLYGON",
    "RECTANGLE",
    "SLOT",
    "sketch_circle",
    "sketch_close",
    "sketch_constrain",
    "sketch_create",
    "sketch_point",
    "sketch_polygon",
    "sketch_rectangle",
    "sketch_slot",
]
