"""Turn a raw CATIA COM failure into a sentence the agent can act on.

Every operation that reaches CATIA can fail inside `ShapeFactory`, and 83 of
them create a feature without a handler of their own -- the surface is far too
large to write bespoke advice at each call site, and a rule that has to be
remembered 83 times is one that will not be. What they all share is the shape of
what comes back:

    com_error: (-2147352567, "Une exception s'est produite.",
                (0, 'CATIAShapeFactory', 'La methode AddNewSolidEdgeFillet-
                 WithConstantRadius a echoue', None, 0, -2147467259), None)

Measured on ladder prompt H3, 2026-09-06. What the agent does with that is
nothing useful: it retried the identical call, then abandoned the modelling
tools and started driving CATIA's menus by hand, and the round budget ran out
with a block and no fillets.

**The CATIA method name inside is not localised.** The wrapper text is --
`La methode X a echoue` here, `The method X failed` on an English seat -- but
`X` is the automation API name and is identical on every install, which is the
same property `api.localisation` relies on for the whole bridge. So the name is
the key, and the surrounding sentence is ignored.

Advice is grouped by what actually goes wrong, not written per method: a fillet,
a chamfer and a draft fail for one reason (the value is too large for the
neighbouring faces), every sketch-driven solid for another (the profile is not
one clean closed loop), every surface operation for a third. A generic fallback
covers the rest and still says more than the COM error does -- it at least names
the operation that failed and says nothing was changed.

Nothing here invents a cause. Each line says what *usually* produces that
failure and what to try; the COM error itself is always kept, because it is the
only thing that distinguishes one instance from another.
"""

from __future__ import annotations

import re
from typing import Final

#: `La methode X a echoue` / `The method X failed` / `Die Methode X ...`.
#: Anchored on the API name, which is the same on every seat, rather than on
#: any of the wordings around it.
_METHOD_RE: Final = re.compile(r"\b(AddNew[A-Za-z0-9_]+|[A-Z][A-Za-z0-9_]{3,})\b")

_PROFILE: Final = (
    "The profile is usually the cause: it has to be ONE closed loop, or loops "
    "nested cleanly inside one another. An open profile, two shapes that "
    "overlap or cross, or a sketch still being edited are all refused. Read the "
    "sketch with catia_list_features -- the element count tells you whether it "
    "holds more than you drew -- and keep one profile per sketch."
)

_TOO_BIG: Final = (
    "The value is usually too large for the faces next to the edge -- a fillet "
    "or chamfer cannot be wider than the narrowest face it runs onto, and the "
    "limit follows the whole tangent chain, not just the edge you named. Try a "
    "smaller value, or apply it to fewer edges at once."
)

_NO_MATERIAL: Final = (
    "The feature has nothing to act on where it was placed. Check it against the "
    "part's bounding box with catia_measure: a sketch on an origin plane sits at "
    "the origin, not on the face you are looking at."
)

_SURFACE: Final = (
    "Surface operations need their inputs to meet: curves that do not touch "
    "cannot be joined, a sweep needs a profile and a guide that intersect, and a "
    "fill needs a closed boundary. Check the inputs exist and are connected with "
    "catia_list_features."
)

_REFERENCE: Final = (
    "The construction geometry could not be placed -- usually the reference it is "
    "measured from does not exist, or the two inputs are parallel or coincident "
    "when the construction needs them not to be."
)

#: Prefix of the CATIA method name -> the advice for that family.
#:
#: Ordered longest-first at lookup so `AddNewShellFace` cannot be answered by
#: `AddNewShell`'s entry.
_ADVICE: Final[dict[str, str]] = {
    "AddNewPad": _PROFILE,
    "AddNewPocket": _PROFILE,
    "AddNewShaft": _PROFILE,
    "AddNewGroove": _PROFILE,
    "AddNewStiffener": _PROFILE,
    "AddNewRib": _PROFILE,
    "AddNewSlot": _PROFILE,
    "AddNewLoft": _PROFILE,
    "AddNewRemovedLoft": _PROFILE,
    "AddNewMultiSection": _PROFILE,
    "AddNewSolidEdgeFillet": _TOO_BIG,
    "AddNewSolidFaceFillet": _TOO_BIG,
    "AddNewSolidTritangentFillet": _TOO_BIG,
    "AddNewChamfer": _TOO_BIG,
    "AddNewDraft": _TOO_BIG,
    "AddNewShell": _TOO_BIG,
    "AddNewThickness": _TOO_BIG,
    "AddNewHole": _NO_MATERIAL,
    "AddNewThread": _NO_MATERIAL,
    "AddNewMirror": _NO_MATERIAL,
    "AddNewRemoveFace": _NO_MATERIAL,
    "AddNewReplaceFace": _NO_MATERIAL,
    "AddNewSolidCombine": _NO_MATERIAL,
    "AddNewRectPattern": _NO_MATERIAL,
    "AddNewCircPattern": _NO_MATERIAL,
    "AddNewUserPattern": _NO_MATERIAL,
    "AddNewExtrude": _SURFACE,
    "AddNewRevol": _SURFACE,
    "AddNewOffset": _SURFACE,
    "AddNewFill": _SURFACE,
    "AddNewSweep": _SURFACE,
    "AddNewBlend": _SURFACE,
    "AddNewJoin": _SURFACE,
    "AddNewHybridSplit": _SURFACE,
    "AddNewHybridTrim": _SURFACE,
    "AddNewExtract": _SURFACE,
    "AddNewBoundary": _SURFACE,
    "AddNewExtrapol": _SURFACE,
    "AddNewHealing": _SURFACE,
    "AddNewUnTrim": _SURFACE,
    "AddNewCloseSurface": _SURFACE,
    "AddNewThickSurface": _SURFACE,
    "AddNewSewSurface": _SURFACE,
    "AddNewSphere": _SURFACE,
    "AddNewCylinder": _SURFACE,
    "AddNewPlane": _REFERENCE,
    "AddNewPoint": _REFERENCE,
    "AddNewLine": _REFERENCE,
    "AddNewCircle": _REFERENCE,
    "AddNewSpline": _REFERENCE,
    "AddNewHelix": _REFERENCE,
    "AddNewSpiral": _REFERENCE,
    "AddNewPolyline": _REFERENCE,
    "AddNewCorner": _REFERENCE,
    "AddNewConnect": _REFERENCE,
    "AddNewProject": _REFERENCE,
    "AddNewIntersection": _REFERENCE,
    "AddNewCombine": _REFERENCE,
    "AddNewCurvePar": _REFERENCE,
    "AddNew3DCurveOffset": _REFERENCE,
    "AddNewExtremum": _REFERENCE,
    "AddNewReflectLine": _REFERENCE,
    "AddNewAffinity": _REFERENCE,
}


def failed_method(text: str) -> str | None:
    """The CATIA API method named in a COM error, if one is."""
    for candidate in _METHOD_RE.findall(text):
        if candidate.startswith("AddNew"):
            return candidate
    return None


def advice_for(text: str) -> str | None:
    """What to try instead, given a raw COM error, or None if we cannot say.

    None rather than a vague sentence: an invented cause is worse than the COM
    error, because the agent will act on it.
    """
    method = failed_method(text)
    if method is None:
        return None
    for prefix in sorted(_ADVICE, key=len, reverse=True):
        if method.startswith(prefix):
            return _ADVICE[prefix]
    return None


def explain(tool: str, exception: BaseException) -> str:
    """The message an unhandled COM failure should reach the agent as.

    Always keeps CATIA's own words -- they are the only thing distinguishing one
    instance from another -- and adds what to do about it when the method is one
    we recognise. When it is not, it still says which tool failed and that
    nothing was changed, which is more than the bare error carries.

    The exception's *type* is kept too, as the message it replaced did: a COM
    tuple says nothing on its own, and `com_error` versus `TimeoutError` versus
    `AttributeError` is frequently the only thing that separates "CATIA refused
    this" from "the bridge called a method that does not exist on this release".
    """
    raw = str(exception)
    advice = advice_for(raw)
    method = failed_method(raw)

    what = f"CATIA could not complete {tool}"
    if method:
        what += f" -- {method} failed"
    tail = f" {advice}" if advice else (
        " Nothing was changed. Check the feature's inputs exist and are named "
        "exactly as catia_list_features reports them."
    )
    return f"{what}. CATIA reported: {type(exception).__name__}: {raw}.{tail}"
