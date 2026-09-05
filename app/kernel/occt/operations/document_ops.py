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


def body_create(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Create a new body and, by default, make it the one features go into.

    Multi-body is what makes a boolean between two shapes expressible: `catia_boolean`
    takes a `tool_body`, and before this there was only ever one body to name. The
    pattern a design uses is *create a body, build the tool inside it, activate the
    first again, subtract* — which is exactly how it reads in CATIA.
    """
    document = context.require_document()
    name = str(arguments.get("name") or f"Body.{len(document.body_names()) + 1}")
    activate = arguments.get("activate")
    document.add_body(name, activate=True if activate is None else bool(activate))
    return {
        "body": name,
        "bodies": document.body_names(),
        "active_body": document.active_body,
    }


def body_activate(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Choose which body new features are added to — CATIA's Define In Work Object."""
    document = context.require_document()
    document.activate_body(str(arguments["body"]))
    return {
        "active_body": document.active_body,
        "bodies": document.body_names(),
        **document.measure(),
    }


def geometrical_set(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Create a geometrical set — a folder for construction geometry.

    **A geometrical set is organisation, not geometry**, and this backend says so rather
    than pretending otherwise. In CATIA it is a tree node that construction elements are
    filed under; here, planes, points and axis systems already live in their own
    namespaces on the document and are addressed by name, so a set changes where a thing
    appears in a tree and nothing about what it is or how it resolves.

    It is implemented rather than refused because a design that files its datums tidily
    must not fail on a backend that has no tree to file them in — and because the CATIA
    backend, which does, will make the same call mean something visible there.
    """
    document = context.require_document()
    name = str(arguments.get("name") or f"Geometrical Set.{len(document.sets) + 1}")
    document.add_set(name, ordered=bool(arguments.get("ordered")))
    return {
        "geometrical_set": name,
        "geometrical_sets": document.set_names(),
        "note": (
            "Construction geometry in this backend is addressed by name, not by tree "
            "position, so the set records the grouping and does not change how anything "
            "resolves."
        ),
    }


__all__ = [
    "body_activate",
    "body_create",
    "feature_rename",
    "geometrical_set",
    "list_features",
    "new_part",
    "set_material",
]
