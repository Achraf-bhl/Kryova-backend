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
    """What has been built, in build order — **and what has been drawn.**

    The operation's own summary promises "the features, sketches and bodies in
    the part", and `include_sketches` is a declared parameter documented as
    defaulting to true. Until 2026-09-11 this returned solid features only and
    read neither argument, so a sketch carrying a finished profile was invisible
    to the one tool the summary calls *"the first call to make on any document
    you did not just build yourself"*.

    **Measured on ladder L2, `qwen3.6:27b`, on the open kernel.** A rectangle had
    been drawn on sketch `'sketch'` (`profiles: 1`, `ok`), a pad on it had just
    been turned back, and the model called this to find out what was really
    there. It answered `{"features": [], "detail": []}` — literally true of solid
    features, and read, correctly, as *"the part is empty"*. The model said so in
    as many words and rebuilt from scratch. An empty answer to a question about a
    document that is not empty is worse than a refusal, because it is believed.

    A sketch is reported as what it is — a drawing, with the number of closed
    profiles on it — rather than as a feature, so nothing downstream can mistake
    one for material. `document.feature` already refuses that confusion by name
    ("... is a sketch, not a feature. A sketch is a drawing until ...") and this
    keeps the same line.
    """
    document = context.require_document()

    body = arguments.get("body")
    if body is not None:
        known = document.body_names()
        if str(body) not in known:
            # Refused rather than ignored, for the mock's reason: silently
            # answering about a different body than the one asked for is a
            # wrong answer wearing a successful one's clothes.
            raise GeometryError(
                f"No body called {str(body)!r} in {document.name}. "
                f"Bodies: {', '.join(known) or 'none'}."
            )

    rows: list[dict[str, Any]] = []
    for feature in document:
        row = feature.to_dict()
        # The type is the stem of the name CATIA would have given it -- `Pad.1`
        # is a Pad -- which is the same thing the mock reports and the same word
        # the user says.
        row["type"] = str(row.get("catia_name") or "").split(".")[0]
        rows.append(row)

    include = arguments.get("include_sketches")
    if include is None or bool(include):
        for name in document.sketch_names():
            sketch = document.sketches[name]
            rows.append(
                {
                    "name": sketch.name,
                    "catia_name": sketch.name,
                    "type": "Sketch",
                    "tool": "catia_sketch_create",
                    "body": None,
                    "support": sketch.support,
                    # The field that answers the question a listing is asked
                    # after a pad has just been refused. A pad needs one closed
                    # profile; `Sketch.1` alone says nothing about whether it has
                    # one, and this says it before the pad fails again.
                    "elements": (
                        len(sketch.profiles)
                        + len(sketch.curves)
                        + len(sketch.points)
                        + len(sketch.construction)
                    ),
                    "profiles": len(sketch.profiles),
                    "can_be_built_from": bool(sketch.profiles),
                }
            )

    if body is not None:
        # A sketch belongs to no body, so restricting to one drops them.
        rows = [one for one in rows if one.get("body") == str(body)]

    kind = arguments.get("kind")
    note = None
    if kind:
        # Case-insensitive, because the model types what the user said rather
        # than what CATIA capitalises.
        present = sorted({str(one["type"]) for one in rows if one["type"]})
        wanted = str(kind).strip().lower()
        rows = [one for one in rows if str(one["type"]).lower() == wanted]
        if not rows:
            # An empty list is a second round trip. Answer the next question now.
            note = (
                f"Nothing of type {str(kind)!r} in {document.name}. "
                f"Present: {', '.join(present) or 'nothing'}."
            )

    out: dict[str, Any] = {
        "features": [str(one.get("catia_name") or one["name"]) for one in rows],
        "detail": rows,
        "bodies": document.body_names(),
        "active_body": document.active_body,
    }
    if note:
        out["note"] = note
    return out


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
