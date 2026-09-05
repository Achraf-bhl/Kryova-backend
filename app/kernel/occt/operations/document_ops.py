"""Operations that establish or describe the document rather than its geometry.

`catia_new_part`, `catia_set_material`, `catia_feature_rename`, `catia_list_features`.
Named `document_ops` rather than `document` so it cannot be confused with
`app.kernel.occt.document`, which is the thing these act upon.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.kernel.errors import GeometryError
from app.kernel.occt.document import PartDocument
from app.kernel.occt.operations.context import BuildContext
from app.solve.materials import MATERIALS


def new_part(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Open the document a design builds into."""
    context.document = PartDocument(name=str(arguments["name"]))
    return {"document": context.document.name, "features": []}


def set_material(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Choose the material, and with it the density every later mass depends on.

    Densities come from `app.solve.materials` — the same table the FEA solver uses —
    rather than a copy kept here. Two tables of the same physical constants drift, and
    the symptom is a part that weighs one thing in the geometry report and another in
    the simulation.

    The server normally supplies `density_kg_m3` alongside the slug (it is a
    server-supplied field on the operation), so that is honoured when present; the
    lookup is the fallback for a direct call.
    """
    document = context.require_document()
    slug = str(arguments["material"])

    supplied = arguments.get("density_kg_m3")
    if supplied is not None:
        density = float(supplied)
    else:
        material = MATERIALS.get(slug)
        if material is None:
            known = ", ".join(sorted(MATERIALS))
            raise GeometryError(
                f"No density is known for material {slug!r}, so nothing weighed against "
                f"it would be true. Known materials: {known}."
            )
        density = float(material.density_kg_m3)

    document.material = slug
    document.density_kg_m3 = density
    return {"material": slug, "density_kg_m3": density}


def feature_rename(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Give a feature the design's own name.

    The compiler emits this after every operation that cannot be named on creation
    (Layer B2). Here it is bookkeeping rather than geometry — OCCT has no feature tree
    to rename — but it must still report full post-state, because a compiled plan very
    often *ends* on a rename and `BuildReport.last_result()` is what the assertion
    engine and the self-correction loop measure by default. A bare acknowledgement here
    makes every assertion on such a design come back UNMEASURED: honest, and useless.
    """
    document = context.require_document()
    target = str(arguments["feature"])
    new_name = str(arguments["name"])

    for feature in document:
        if feature.catia_style_name == target:
            feature.catia_style_name = new_name
            return context.result_for(feature)

    known = ", ".join(document.feature_names()) or "nothing"
    raise GeometryError(
        f"Cannot rename {target!r}: no feature by that name. Built so far: {known}."
    )


def list_features(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """What has been built, in build order."""
    document = context.require_document()
    return {
        "features": document.feature_names(),
        "detail": [feature.to_dict() for feature in document],
    }


__all__ = ["feature_rename", "list_features", "new_part", "set_material"]
