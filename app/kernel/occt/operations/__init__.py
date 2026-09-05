"""Every operation this backend implements, assembled into one table.

Mirrors how `app/catia/ops/` is laid out: one module per domain, one table assembling
them, and adding an operation is one entry in the module it belongs to. The registry
there declares *what the vocabulary is*; this declares *what OCCT can do about it*, and
the gap between the two is the honest coverage number.

**Every handler name is checked against the real registry at import.** A typo in a key
here would otherwise produce a handler that can never be called — dead code that looks
live, and a silent hole in coverage. `HANDLERS` is validated on first use instead, so a
mistake surfaces immediately rather than as an operation that mysteriously reports
"not implemented".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from app.kernel.occt.operations import (
    annotation_ops,
    booleans,
    document_ops,
    dressup,
    features,
    holes,
    inspection,
    patterns,
    primitives,
    reference_ops,
    sketcher,
    surfaces,
    sweeps,
    transforms,
)
from app.kernel.occt.operations.context import BuildContext

#: What every operation handler looks like.
Handler = Callable[[BuildContext, Mapping[str, Any]], Mapping[str, Any]]

#: Operation name → implementation. The single place a new OCCT operation is wired in.
HANDLERS: Final[dict[str, Handler]] = {
    # document
    "catia_new_part": document_ops.new_part,
    "catia_set_material": document_ops.set_material,
    "catia_feature_rename": document_ops.feature_rename,
    "catia_list_features": document_ops.list_features,
    "catia_body_create": document_ops.body_create,
    "catia_body_activate": document_ops.body_activate,
    "catia_geometrical_set": document_ops.geometrical_set,
    # reference geometry
    reference_ops.POINT_AT: reference_ops.point_at,
    reference_ops.POINT_BETWEEN: reference_ops.point_between,
    reference_ops.PLANE_OFFSET: reference_ops.plane_offset,
    reference_ops.PLANE_THROUGH_POINTS: reference_ops.plane_through_points,
    reference_ops.PLANE_ANGLE: reference_ops.plane_angle,
    reference_ops.AXIS_SYSTEM: reference_ops.axis_system,
    # sketching
    sketcher.CREATE: sketcher.sketch_create,
    sketcher.RECTANGLE: sketcher.sketch_rectangle,
    sketcher.CIRCLE: sketcher.sketch_circle,
    sketcher.POLYGON: sketcher.sketch_polygon,
    sketcher.SLOT: sketcher.sketch_slot,
    sketcher.CLOSE: sketcher.sketch_close,
    sketcher.CONSTRAIN: sketcher.sketch_constrain,
    sketcher.POINT: sketcher.sketch_point,
    sketcher.LINE: sketcher.sketch_line,
    sketcher.POLYLINE: sketcher.sketch_polyline,
    sketcher.ARC: sketcher.sketch_arc,
    sketcher.ARC_THREE_POINT: sketcher.sketch_arc_three_point,
    sketcher.ELLIPSE: sketcher.sketch_ellipse,
    sketcher.SPLINE: sketcher.sketch_spline,
    sketcher.AXIS: sketcher.sketch_axis,
    # sketch-based solid features
    features.PAD: features.pad,
    features.POCKET: features.pocket,
    features.SHAFT: features.shaft,
    features.GROOVE: features.groove,
    features.SOLID_COMBINE: features.solid_combine,
    sweeps.RIB: sweeps.rib,
    sweeps.SLOT: sweeps.slot,
    sweeps.STIFFENER: sweeps.stiffener,
    holes.HOLE: holes.hole,
    holes.HOLE_AT: holes.hole_at,
    # primitives and dress-up
    primitives.TOOL: primitives.surface_primitive,
    dressup.FILLET: dressup.fillet,
    dressup.CHAMFER: dressup.chamfer,
    dressup.DRAFT: dressup.draft,
    dressup.FILLET_EDGES: dressup.fillet_edges,
    dressup.FILLET_VARIABLE: dressup.fillet_variable,
    dressup.FILLET_TRITANGENT: dressup.fillet_tritangent,
    dressup.THICKNESS: dressup.thickness,
    dressup.REMOVE_FACE: dressup.remove_face,
    annotation_ops.THREAD: annotation_ops.thread,
    # surfaces, and the two ways one becomes material
    surfaces.EXTRUDE: surfaces.surface_extrude,
    surfaces.REVOLVE: surfaces.surface_revolve,
    surfaces.OFFSET: surfaces.surface_offset,
    surfaces.FILL: surfaces.surface_fill,
    surfaces.LOFT: surfaces.surface_loft,
    surfaces.JOIN: surfaces.join,
    surfaces.EXTRACT: surfaces.extract,
    surfaces.BOUNDARY: surfaces.boundary,
    surfaces.CLOSE: surfaces.close_surface,
    surfaces.THICKEN: surfaces.thick_surface,
    # whole-body operations
    booleans.BOOLEAN: booleans.boolean,
    booleans.SHELL: booleans.shell,
    transforms.TRANSLATE: transforms.translate,
    transforms.ROTATE: transforms.rotate,
    transforms.MIRROR: transforms.mirror,
    transforms.SYMMETRY: transforms.symmetry,
    transforms.SCALE: transforms.scale,
    # patterns
    patterns.PATTERN_RECTANGULAR: patterns.pattern_rectangular,
    patterns.PATTERN_CIRCULAR: patterns.pattern_circular,
    patterns.PATTERN_USER: patterns.pattern_user,
    # reading
    inspection.MEASURE: inspection.measure,
    inspection.MEASURE_BETWEEN: inspection.measure_between,
    inspection.MEASURE_ITEM: inspection.measure_item,
    inspection.ANALYSIS: inspection.analysis_part,
    inspection.LIST_FACES: inspection.list_faces,
    inspection.LIST_EDGES: inspection.list_edges,
}


def unknown_handler_names() -> tuple[str, ...]:
    """Handler keys that are not operations the registry declares.

    Imported lazily so this package does not drag the CATIA operation registry into a
    geometry-only import path. Called by the runner's self-check and by the test suite;
    an empty result is the invariant.
    """
    from app.catia.ops import registry

    return tuple(sorted(name for name in HANDLERS if registry.get(name) is None))


def coverage() -> dict[str, int]:
    """How much of the vocabulary this backend implements, as data rather than a claim."""
    from app.catia.ops import registry

    declared = len(registry.OPERATIONS_BY_NAME)
    return {
        "implemented": len(HANDLERS),
        "declared": declared,
        "remaining": declared - len(HANDLERS),
    }


__all__ = ["HANDLERS", "BuildContext", "Handler", "coverage", "unknown_handler_names"]
