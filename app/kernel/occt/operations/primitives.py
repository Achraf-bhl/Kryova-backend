"""Solid primitives: geometry created from numbers rather than from a profile.

These are the operations that need no sketcher, which is why they are the first ones
mapped. Everything that starts from a 2D profile waits on PlaneGCS (master plan 1.3);
these do not, and they are enough to exercise the naming layer, the measurement contract
and the end-to-end path from a compiled spec to a checked assertion.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.kernel.errors import OperationNotSupported
from app.kernel.occt.binding import symbol
from app.kernel.occt.naming import record_primitive
from app.kernel.occt.operations.context import (
    BuildContext,
    as_direction,
    as_point,
    as_positive_length,
    build_or_raise,
    feature_name,
    frame,
    point,
)
from app.kernel.occt.topology import edges, faces

TOOL = "catia_surface_primitive"


def surface_primitive(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A sphere, cylinder or box, positioned and oriented.

    The registry gives every primitive kind one numeric parameter (`radius_mm`) plus an
    optional `length_mm`, so what `radius_mm` means depends on the kind. That is the
    schema's shape, not this module's choice, and each branch says what it read it as.
    """
    document = context.require_document()
    kind = str(arguments["kind"]).lower()
    radius = as_positive_length(arguments.get("radius_mm"), argument="radius_mm", tool=TOOL)
    centre = as_point(arguments.get("centre"), argument="centre")
    axis = as_direction(arguments.get("axis"))

    if kind == "sphere":
        maker = symbol("BRepPrimAPI_MakeSphere")(point(centre), radius)
        advice = "A sphere needs only a positive radius."
    elif kind == "cylinder":
        length = as_positive_length(arguments.get("length_mm"), argument="length_mm", tool=TOOL)
        maker = symbol("BRepPrimAPI_MakeCylinder")(frame(centre, axis), radius, length)
        advice = "Check that radius_mm and length_mm are both positive."
    elif kind == "box":
        # `radius_mm` is the half-width here: one numeric parameter covers every kind,
        # so a box is the square cross-section of side 2*radius extruded by length.
        length = as_positive_length(arguments.get("length_mm"), argument="length_mm", tool=TOOL)
        corner = (centre[0] - radius, centre[1] - radius, centre[2])
        maker = symbol("BRepPrimAPI_MakeBox")(point(corner), 2 * radius, 2 * radius, length)
        advice = "A box is built from radius_mm as a half-width and length_mm as height."
    else:
        raise OperationNotSupported(f"{TOOL}(kind={kind!r})")

    shape = build_or_raise(maker, tool=f"{TOOL} ({kind})", detail=advice)

    feature = document.add_feature(feature_name(arguments, kind), TOOL)
    document.set_result(feature, shape, contributed=(faces(shape), edges(shape)))
    record_primitive(feature.labels, shape)
    return context.result_for(feature)


__all__ = ["TOOL", "surface_primitive"]
