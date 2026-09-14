"""Boolean operations and the thin-wall features.

`catia_boolean` combines two bodies; `catia_shell` and `catia_shell_faces` hollow one. They share a module
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
    _NO_CHANGE_MM3,
    BuildContext,
    as_positive_length,
    build_or_raise,
    given_name,
)
from app.kernel.occt.selectors import select_faces
from app.kernel.occt.topology import has_solid

BOOLEAN = "catia_boolean"
SHELL = "catia_shell"
SHELL_FACES = "catia_shell_faces"

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

    # `target_body` is declared — "The body to combine into. Defaults to the main
    # body." — and is read by nothing in `app/`. Measured 2026-09-11: varying it
    # alone, including to a body that does not exist, changed nothing; the
    # boolean always combined into the *active* body. So a caller who activated
    # one body and named another got the wrong one silently.
    #
    # Checked here rather than in `unsupported.IGNORED` because the value that is
    # harmless is not a constant — it is whatever body happens to be active — and
    # only the document knows that. Naming the active body is honoured because it
    # asks for exactly what it will get; naming any other is refused rather than
    # ignored.
    wanted = arguments.get("target_body")
    if wanted is not None and str(wanted) != document.active_body:
        raise GeometryError(
            f"{BOOLEAN} was told to combine into body {str(wanted)!r}, but it always "
            f"combines into the active body, which is {document.active_body!r}. Call "
            f"catia_body_activate with {str(wanted)!r} first, or drop target_body."
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

    feature = document.add_feature(given_name(arguments), BOOLEAN)
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
    _refuse_a_shell_that_did_not_hollow(source, result, tool=f"{SHELL} at {thickness} mm")
    feature = document.add_feature(given_name(arguments), SHELL)
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


def shell_faces(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Hollow the part open at the named faces, each wall at its own thickness if asked.

    **Why the open kernel has it.** `catia_shell`'s own summary says a shell with no
    face removed is sealed, and sends the agent here "to leave it open — which is
    nearly always what is wanted". On the open kernel this tool did not exist, so
    that summary pointed at an operation the agent was never offered: the
    vocabulary gap `CLAUDE.md` *Testing* item 8 describes. The per-face thickness
    is the one thing it adds over `catia_shell`.

    Built on `BRepOffset_MakeOffset`, not `BRepOffsetAPI_MakeThickSolid`, because
    only the lower-level class takes a thickness per face (`SetOffsetOnFace`). The
    API class initialises and builds in one call, which leaves no moment to set
    one. Everything else is passed as `MakeThickSolidByJoin` passes it, so a call
    with no overrides builds exactly what `catia_shell` builds (pinned by a test).
    Measured on 2026-09-14: a 40x30x20 box open at the top, with 2 mm walls and
    5 mm on the +x side, is 8,556 mm3. By hand, 24,000 less a 33x26x18 cavity.

    `open_faces` is required here although the schema marks it optional. With no
    face removed this is `catia_shell`, and the refusal says so rather than
    building a sealed hollow under a name whose whole point is the opening.
    """
    document = context.require_document()
    source = context.require_shape(SHELL_FACES)

    thickness = as_positive_length(
        arguments.get("thickness_mm"), argument="thickness_mm", tool=SHELL_FACES
    )
    sign = 1.0 if arguments.get("outward") else -1.0

    opening = arguments.get("open_faces")
    if not opening:
        raise GeometryError(
            f"{SHELL_FACES} needs open_faces, the faces to remove. With none removed the "
            "part is hollowed sealed, and that is catia_shell."
        )
    removed = _faces_named(source, opening, tool=SHELL_FACES, document=document)

    overrides: list[tuple[Any, float]] = []
    for override in arguments.get("face_thicknesses") or []:
        wall = as_positive_length(
            override.get("thickness_mm"),
            argument="face_thicknesses[].thickness_mm",
            tool=SHELL_FACES,
        )
        for face in select_faces(
            source, override.get("face"), tool=SHELL_FACES, document=document
        ):
            if any(face.IsSame(gone) for gone in removed):
                raise GeometryError(
                    f"{SHELL_FACES} was asked to remove a face and also to give it a "
                    f"{wall} mm wall. A removed face has no wall; drop it from "
                    "open_faces or from face_thicknesses."
                )
            overrides.append((face, wall))

    maker = symbol("BRepOffset_MakeOffset")()
    maker.Initialize(
        source,
        sign * thickness,
        SHELL_TOLERANCE_MM,
        symbol("BRepOffset_Mode").BRepOffset_Skin,
        False,  # Intersection
        False,  # SelfInter
        symbol("GeomAbs_JoinType").GeomAbs_Arc,
        False,  # Thickening, as MakeThickSolidByJoin passes it; on a solid, True measured the same
    )
    for face in removed:
        maker.AddFace(face)
    for face, wall in overrides:
        maker.SetOffsetOnFace(face, sign * wall)

    walls = f"{SHELL_FACES} at {thickness} mm"
    try:
        maker.MakeThickSolid()
        done = maker.IsDone()
    except Exception as exc:  # noqa: BLE001 - OCCT's Standard_Failure hierarchy
        raise GeometryError(
            f"{walls} could not run: {exc}. A wall thicker than the narrowest part of the "
            "solid cannot be offset inwards; reduce it."
        ) from exc
    if not done:
        raise GeometryError(
            f"{walls} did not produce a shape (OCCT reports {maker.Error()}). A wall "
            "thicker than the narrowest part of the solid cannot be offset inwards; "
            "reduce it, or remove fewer faces."
        )
    result = maker.Shape()
    if result.IsNull() or not has_solid(result):
        raise GeometryError(
            f"{walls} consumed the whole part. A wall is thicker than the material "
            "available; reduce it."
        )
    _refuse_a_shell_that_did_not_hollow(source, result, tool=walls)

    contributed = faces_generated_by(maker, source) + faces_modified_by(maker, source)
    feature = document.add_feature(given_name(arguments), SHELL_FACES)
    modified, generated = evolution_of(maker, source)
    document.set_result(
        feature,
        result,
        contributed=(contributed, edges_bounding(contributed)),
        evolved_by=maker,
    )
    record_derived(
        feature.labels, result=result, source=source, modified=modified, generated=generated
    )
    return context.result_for(feature)


def _refuse_a_shell_that_did_not_hollow(source: Any, result: Any, *, tool: str) -> None:
    """Refuse a shell whose walls met, which OCCT reports as a success.

    **Measured 2026-09-14** on a 40x30x20 box open at the top, both through
    `catia_shell` with faces and through `catia_shell_faces`. Up to 14.99 mm the
    wall volume matches the hand calculation to the last digit. At 15 mm, where
    the two long walls meet in the middle, `IsDone()` is true and the answer is
    20,888.9 mm3, a shape `BRepCheck_Analyzer` calls invalid. From 15.01 mm on,
    `IsDone()` is still true and the "shell" is the original box, 24,000 mm3 and
    six faces, reported as a finished hollow.

    So two checks, each catching one of the two: an invalid shape, and the part
    handed back unchanged. "Unchanged", not "no material removed", because an
    outward shell removes the core too and can come out lighter or heavier than
    the part (2 mm outward on that box is 8,707.9 mm3); only an unchanged volume
    is never a shell.
    """
    from app.kernel.occt.metrology import volume_mm3

    if not symbol("BRepCheck_Analyzer")(result).IsValid():
        raise GeometryError(
            f"{tool} produced a shape the kernel's own check calls invalid. The walls "
            "meet: the thickness is at least half the part's width somewhere, so there "
            "is no cavity left between them. Reduce the thickness."
        )
    before, after = volume_mm3(source), volume_mm3(result)
    if abs(after - before) <= _NO_CHANGE_MM3:
        raise GeometryError(
            f"{tool} returned the part unchanged ({before:.3f} mm3 before and "
            f"{after:.3f} after): the walls are too thick to leave any cavity, and the "
            "kernel reports that as a success. Reduce the thickness to under half the "
            "part's narrowest width."
        )


def _faces_named(source: Any, named: Any, *, tool: str, document: Any) -> list[Any]:
    """Faces from a list of selectors, which is what the schema declares, or from one."""
    selectors = named if isinstance(named, (list, tuple)) else [named]
    found: list[Any] = []
    for selector in selectors:
        for face in select_faces(source, selector, tool=tool, document=document):
            if not any(face.IsSame(seen) for seen in found):
                found.append(face)
    return found


__all__ = [
    "BOOLEAN",
    "SHELL",
    "SHELL_FACES",
    "SHELL_TOLERANCE_MM",
    "boolean",
    "shell",
    "shell_faces",
]
