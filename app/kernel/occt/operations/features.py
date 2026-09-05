"""Solid features built from a sketch: pad, pocket, shaft, groove.

These are what makes the kernel useful — the operations a real part is actually made of.
Each takes a profile and turns it into material (pad, shaft) or removes it (pocket,
groove).

**Adding and removing are the same geometry with opposite signs.** A pad and a pocket
both prism a profile; the pad fuses the result into the part and the pocket cuts it out.
Writing them as one function with a boolean at the end keeps them from drifting — a bug
fixed in the pad's limit handling would otherwise have to be found again in the pocket's.

**The first solid feature has nothing to fuse into**, and that is not an error: a part
starts empty and its first pad *is* the part. Every later one combines.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt.binding import symbol
from app.kernel.occt.naming import (
    contribution_of,
    evolution_of,
    record_derived,
    record_primitive,
)
from app.kernel.occt.operations.context import (
    BuildContext,
    as_positive_length,
    build_or_raise,
    feature_name,
)
from app.kernel.occt.sketching import Sketch
from app.kernel.occt.topology import edges, faces

PAD = "catia_pad"
POCKET = "catia_pocket"
SHAFT = "catia_shaft"
GROOVE = "catia_groove"

#: Limit modes the registry allows but this backend cannot honour yet, each with what it
#: is actually waiting on.
#:
#: These reasons used to blame Phase 2.1's face selection. That shipped, and the reasons
#: were wrong from that moment — the missing piece was never *naming* the face to stop
#: against but *stopping* against it, which is `BRepFeat_MakePrism` with an until-shape
#: rather than the plain `BRepPrimAPI_MakePrism` used here. Recorded because a stale
#: "blocked on X" outlives X and then sends the next reader to build something that
#: already exists.
_UNSUPPORTED_LIMITS: dict[str, str] = {
    "up_to_next": (
        "stopping the extrusion at the next face needs BRepFeat_MakePrism with an "
        "until-shape, not the plain prism this backend builds (Phase 2.5)"
    ),
    "up_to_last": (
        "stopping the extrusion at the last face needs BRepFeat_MakePrism with an "
        "until-shape (Phase 2.5)"
    ),
    "up_to_plane": "stopping against a plane needs constructed planes (Phase 2.4)",
    "up_to_surface": "stopping against a surface needs constructed surfaces (Phase 2.6)",
}


def pad(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Extrude a profile into material."""
    length = as_positive_length(arguments.get("length_mm"), argument="length_mm", tool=PAD)
    return _prism_feature(context, arguments, PAD, length, adds_material=True)


def pocket(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Extrude a profile and remove it from the part."""
    if arguments.get("through_all"):
        depth = _through_all_depth(context)
    else:
        depth = as_positive_length(arguments.get("depth_mm"), argument="depth_mm", tool=POCKET)
    return _prism_feature(context, arguments, POCKET, depth, adds_material=False)


def shaft(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Revolve a profile into material."""
    return _revolve_feature(context, arguments, SHAFT, adds_material=True)


def groove(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Revolve a profile and remove it from the part."""
    return _revolve_feature(context, arguments, GROOVE, adds_material=False)


# -- shared construction -----------------------------------------------------


def _prism_feature(
    context: BuildContext,
    arguments: Mapping[str, Any],
    tool: str,
    distance: float,
    *,
    adds_material: bool,
) -> Mapping[str, Any]:
    document = context.require_document()
    sketch = _sketch_argument(context, arguments, tool)
    _reject_unsupported_limit(arguments, tool)

    face = sketch.face()
    normal = sketch.frame().Direction()
    reversed_ = bool(arguments.get("reversed"))
    symmetric = bool(arguments.get("symmetric"))

    if symmetric:
        # Half each way, so the *total* is the length the author asked for. Extruding
        # the full length in both directions would make a part twice as thick as the
        # number in the spec, which is the kind of error nobody notices until assembly.
        #
        # The profile is **moved** back half a length and then extruded once. Extruding
        # it backwards and then extruding *that* forwards is the natural way to write
        # this and does not work: the first extrusion returns a solid, and
        # `BRepPrimAPI_MakePrism` refuses a solid with `Standard_NoSuchObject: Solids are
        # not Processed` — a C++ exception out of the constructor, before any `IsDone()`
        # the wrapper could have checked.
        blank = _prism(_translated(face, normal, -distance / 2.0), normal, distance, tool)
    else:
        blank = _prism(face, normal, -distance if reversed_ else distance, tool)

    return _combine(context, document, arguments, tool, blank, adds_material=adds_material)


def _revolve_feature(
    context: BuildContext,
    arguments: Mapping[str, Any],
    tool: str,
    *,
    adds_material: bool,
) -> Mapping[str, Any]:
    document = context.require_document()
    sketch = _sketch_argument(context, arguments, tool)

    angle = float(arguments.get("angle_deg") or 360.0)
    if not 0.0 < angle <= 360.0:
        raise GeometryError(
            f"{tool} needs a revolution angle between 0 and 360 degrees; got {angle}."
        )

    frame = sketch.frame()
    # Revolve about the sketch's local Y axis through its origin — the convention CATIA
    # uses for a shaft with no explicit axis. An explicit `axis` argument is Phase 2.4,
    # when constructed axes exist to name.
    if arguments.get("axis"):
        raise OperationNotSupported(
            f"{tool} with an explicit axis",
            "Naming a revolution axis needs constructed axis systems (Phase 2.4); the "
            "sketch's own vertical axis is used meanwhile",
        )
    axis = symbol("gp_Ax1")(frame.Location(), frame.YDirection())

    maker = symbol("BRepPrimAPI_MakeRevol")(sketch.face(), axis, math.radians(angle))
    blank = build_or_raise(
        maker,
        tool=f"{tool} through {angle}°",
        detail="A profile that crosses its own revolution axis cannot be revolved — "
        "move it clear of the axis.",
    )
    return _combine(context, document, arguments, tool, blank, adds_material=adds_material)


def _translated(shape: Any, normal: Any, distance: float) -> Any:
    """The same shape, moved along a direction. Used to place a symmetric profile."""
    transform = symbol("gp_Trsf")()
    transform.SetTranslation(
        symbol("gp_Vec")(
            normal.X() * distance, normal.Y() * distance, normal.Z() * distance
        )
    )
    return symbol("BRepBuilderAPI_Transform")(shape, transform, True).Shape()


def _prism(face: Any, normal: Any, distance: float, tool: str) -> Any:
    vector = symbol("gp_Vec")(normal.X() * distance, normal.Y() * distance, normal.Z() * distance)
    maker = symbol("BRepPrimAPI_MakePrism")(face, vector)
    return build_or_raise(
        maker,
        tool=tool,
        detail="The profile could not be extruded; check that it is a single closed "
        "outline that does not cross itself.",
    )


def _combine(
    context: BuildContext,
    document: Any,
    arguments: Mapping[str, Any],
    tool: str,
    blank: Any,
    *,
    adds_material: bool,
) -> Mapping[str, Any]:
    """Fuse or cut the new material into the part, recording the naming history."""
    feature = document.add_feature(feature_name(arguments, tool.removeprefix("catia_")), tool)
    existing = document.shape

    if existing is None:
        if not adds_material:
            raise GeometryError(
                f"{tool} removes material, and this part has none yet. Build a pad or a "
                "shaft before cutting into it."
            )
        # The first solid feature *is* the part; there is nothing to fuse into, so
        # everything it has is its own contribution.
        document.set_result(feature, blank, contributed=(faces(blank), edges(blank)))
        record_primitive(feature.labels, blank)
        return context.result_for(feature)

    operation = "BRepAlgoAPI_Fuse" if adds_material else "BRepAlgoAPI_Cut"
    maker = symbol(operation)(existing, blank)
    result = build_or_raise(
        maker,
        tool=f"{tool} ({'fuse' if adds_material else 'cut'})",
        detail="The new material does not meet the existing part, or the cut would "
        "remove all of it.",
    )
    if not _encloses_solid(result):
        raise GeometryError(
            f"{tool} removed everything the part was made of, leaving no solid. Check "
            "its depth and position against the material that is actually there."
        )

    modified, generated = evolution_of(maker, existing)
    document.set_result(
        feature,
        result,
        contributed=contribution_of(maker, blank),
        evolved_by=maker,
    )
    record_derived(
        feature.labels, result=result, source=existing, modified=modified, generated=generated
    )
    return context.result_for(feature)


def _encloses_solid(shape: Any) -> bool:
    from app.kernel.occt.topology import has_solid

    return has_solid(shape)


def _sketch_argument(context: BuildContext, arguments: Mapping[str, Any], tool: str) -> Sketch:
    document = context.require_document()
    named = arguments.get("sketch")
    if not named:
        raise GeometryError(f"{tool} needs a sketch to build from, and none was named.")
    return document.sketch(str(named))


def _reject_unsupported_limit(arguments: Mapping[str, Any], tool: str) -> None:
    limit = arguments.get("limit")
    if not limit:
        return
    reason = _UNSUPPORTED_LIMITS.get(str(limit))
    if reason is not None:
        raise OperationNotSupported(f"{tool} with limit={limit!r}", reason)


def _through_all_depth(context: BuildContext) -> float:
    """Deep enough to pass through whatever exists, from wherever it starts.

    The part's bounding-box diagonal is the shortest length guaranteed to clear it from
    any direction, doubled so a profile starting outside the part still passes through.
    Computed from the geometry rather than being a large constant, because a constant
    that is generous for a bracket is not generous for a chassis.
    """
    from app.kernel.occt.metrology import bounding_box_mm

    document = context.require_document()
    if document.shape is None:
        raise GeometryError(
            "A through-all pocket needs material to cut through, and this part is empty."
        )
    size = bounding_box_mm(document.shape)["size"]
    return 2.0 * math.sqrt(sum(component * component for component in size))


__all__ = ["GROOVE", "PAD", "POCKET", "SHAFT", "groove", "pad", "pocket", "shaft"]
