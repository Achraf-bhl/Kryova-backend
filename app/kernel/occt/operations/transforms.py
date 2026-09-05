"""Rigid transformations: moving geometry without changing its shape.

A transform preserves every measurement except position, which makes it the cheapest
useful check that the naming layer follows geometry rather than coordinates — a name
recorded before a translation must still resolve after it, and the face it finds must
have the same area in a different place.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.kernel.occt.binding import symbol
from app.kernel.occt.naming import evolution_of, record_derived
from app.kernel.occt.operations.context import (
    BuildContext,
    as_point,
    build_or_raise,
    feature_name,
)
from app.kernel.occt.topology import edges, faces

TRANSLATE = "catia_translate"

#: OCCT copies the shape rather than transforming it in place. Required here: the source
#: shape is still referenced by the previous feature's naming history, and moving it
#: underneath that history would invalidate names already recorded against it.
COPY_ON_TRANSFORM = True


def translate(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Move the current shape by a vector, in millimetres."""
    document = context.require_document()
    source = context.require_shape(TRANSLATE)

    vector = as_point(
        arguments.get("vector") if arguments.get("vector") is not None
        else arguments.get("direction"),
        argument="vector",
    )

    transformation = symbol("gp_Trsf")()
    transformation.SetTranslation(symbol("gp_Vec")(*vector))
    maker = symbol("BRepBuilderAPI_Transform")(source, transformation, COPY_ON_TRANSFORM)
    result = build_or_raise(
        maker,
        tool=TRANSLATE,
        detail="A translation cannot fail on valid geometry; the source shape is "
        "probably empty or already invalid.",
    )

    feature = document.add_feature(feature_name(arguments, "translate"), TRANSLATE)
    modified, generated = evolution_of(maker, source)
    document.set_result(feature, result, contributed=(faces(result), edges(result)))
    record_derived(
        feature.labels,
        result=result,
        source=source,
        modified=modified,
        generated=generated,
    )
    return context.result_for(feature)


__all__ = ["COPY_ON_TRANSFORM", "TRANSLATE", "translate"]
