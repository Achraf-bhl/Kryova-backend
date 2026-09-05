"""Boolean operations and the thin-wall feature.

`catia_boolean` combines two bodies; `catia_shell` hollows one. They share a module
because both are whole-body operations whose failure modes are the same shape — an
operation that succeeds and leaves nothing, or one that succeeds and leaves the part in
pieces. Both are checked for, because OCCT reports neither as an error.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.kernel.errors import GeometryError
from app.kernel.occt.binding import symbol
from app.kernel.occt.naming import (
    contribution_of,
    edges_bounding,
    evolution_of,
    faces_generated_by,
    faces_modified_by,
    record_derived,
)
from app.kernel.occt.operations.context import (
    BuildContext,
    as_positive_length,
    build_or_raise,
    feature_name,
)
from app.kernel.occt.selectors import select_faces
from app.kernel.occt.topology import has_solid

BOOLEAN = "catia_boolean"
SHELL = "catia_shell"

#: Join tolerance for a shell that removes faces. OCCT's own default for this operation;
#: tightening it makes the offset fail on ordinary parts rather than making it stricter.
SHELL_TOLERANCE_MM = 1e-3

#: The registry's boolean words, mapped to the OCCT algorithm that performs each.
_OPERATIONS: dict[str, str] = {
    "add": "BRepAlgoAPI_Fuse",
    "union": "BRepAlgoAPI_Fuse",
    "remove": "BRepAlgoAPI_Cut",
    "subtract": "BRepAlgoAPI_Cut",
    "intersect": "BRepAlgoAPI_Common",
}


def boolean(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Combine the current part with another body."""
    document = context.require_document()
    target = context.require_shape(BOOLEAN)

    word = str(arguments.get("operation", "")).lower()
    algorithm = _OPERATIONS.get(word)
    if algorithm is None:
        known = ", ".join(sorted(_OPERATIONS))
        raise GeometryError(
            f"{word!r} is not a boolean operation. Use one of: {known}."
        )

    tool_name = arguments.get("tool_body")
    if not tool_name:
        raise GeometryError(f"{BOOLEAN} needs tool_body — the body to combine with.")
    tool_shape = document.body(str(tool_name))

    maker = symbol(algorithm)(target, tool_shape)
    result = build_or_raise(
        maker,
        tool=f"{BOOLEAN} ({word})",
        detail="The two bodies may not touch at all, which makes the result either "
        "unchanged or empty depending on the operation.",
    )
    if not has_solid(result):
        raise GeometryError(
            f"{BOOLEAN} ({word}) left no solid. For a subtraction the tool body probably "
            "covers the target entirely; for an intersection they probably do not overlap."
        )

    feature = document.add_feature(feature_name(arguments, word), BOOLEAN)
    modified, generated = evolution_of(maker, target)
    document.set_result(
        feature,
        result,
        contributed=contribution_of(maker, tool_shape),
        evolved_by=maker,
    )
    record_derived(
        feature.labels, result=result, source=target, modified=modified, generated=generated
    )
    return context.result_for(feature)


def shell(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Hollow the part, leaving walls of the given thickness.

    Naming faces to open is what Phase 2.1's face selection made possible: `faces` takes
    the same words and predicates every other selector does, so *"open the top"* is
    `{"axis": "z", "side": "max"}` and *"open the big flat face"* is
    `{"planar": true, "larger_than_mm2": 400}`. With no faces named the part is hollowed
    completely — a closed shell, which is a real thing to want and the safer default.
    """
    document = context.require_document()
    source = context.require_shape(SHELL)

    thickness = as_positive_length(
        arguments.get("thickness_mm"), argument="thickness_mm", tool=SHELL
    )

    # Outward shells add material beyond the original surface; the sign convention is
    # OCCT's, and getting it backwards silently makes the part bigger rather than hollow.
    offset = thickness if arguments.get("outward") else -thickness

    opening = arguments.get("faces") or arguments.get("open_faces")
    maker = symbol("BRepOffsetAPI_MakeThickSolid")()
    to_remove = symbol("TopTools_ListOfShape")()

    if opening:
        # Named faces are removed, leaving the part open there — the ordinary meaning of
        # a shell. Enabled by Phase 2.1's face selection; before it, this was refused.
        for face in select_faces(source, opening, tool=SHELL, document=document):
            to_remove.Append(face)

    maker.MakeThickSolidByJoin(source, to_remove, offset, SHELL_TOLERANCE_MM)
    result = build_or_raise(
        maker,
        tool=f"{SHELL} at {thickness} mm",
        detail="A wall thicker than the narrowest part of the solid cannot be offset "
        "inwards — reduce it.",
    )

    # **What a shell contributes is its inner surface**, and the two paths reach it
    # differently.
    #
    # With faces opened, the inner walls are `Generated` by the join and the rim is a
    # `Modified` original. Both count as the shell's own, because producing the wall
    # cross-section is this operation's whole purpose — unlike a fillet, whose trimming
    # of its neighbours is a side effect and stays with whatever built them.
    contributed = (
        faces_generated_by(maker, source) + faces_modified_by(maker, source)
        if opening
        else []
    )
    evolved_by: Any = maker

    if not opening:
        # **With no face removed, `MakeThickSolidByJoin` offsets the boundary and returns
        # the shrunken solid — not a hollow one.** On a 40×30×20 box at 2 mm it hands back
        # a plain 36×26×16 block: still six faces, still solid, 14,976 mm³ where the wall
        # is 9,024. Nothing in the result says it is wrong, which is why this is here and
        # not left to whoever reads the volume next.
        #
        # (`MakeThickSolidBySimple`, the API that sounds like the right one, does not
        # complete at all on this input — `IsDone()` false, `Shape()` raising.)
        #
        # So the offset solid is the *inner* boundary, and cutting it from the original
        # leaves the wall: twelve faces, 9,024 mm³, closed. That is what a shell with
        # nothing opened means.
        #
        # The contribution has to be read from **this** algorithm rather than the join:
        # the result is a different shape built by a different maker, so none of the
        # join's faces appear in it and a contribution recorded from the join would name
        # faces that are not in the part.
        inner = result
        cut = symbol("BRepAlgoAPI_Cut")(source, inner)
        result = build_or_raise(
            cut,
            tool=f"{SHELL} at {thickness} mm",
            detail="The hollow could not be cut from the solid.",
        )
        contributed = contribution_of(cut, inner)[0]
        evolved_by = cut
    if not has_solid(result):
        raise GeometryError(
            f"{SHELL} at {thickness} mm consumed the whole part. The wall is thicker "
            "than the material available; reduce it."
        )
    feature = document.add_feature(feature_name(arguments, "shell"), SHELL)
    modified, generated = evolution_of(maker, source)
    document.set_result(
        feature,
        result,
        contributed=(contributed, edges_bounding(contributed)),
        evolved_by=evolved_by,
    )
    record_derived(
        feature.labels, result=result, source=source, modified=modified, generated=generated
    )
    return context.result_for(feature)


__all__ = ["BOOLEAN", "SHELL", "SHELL_TOLERANCE_MM", "boolean", "shell"]
