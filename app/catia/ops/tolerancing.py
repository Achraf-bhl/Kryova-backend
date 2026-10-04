"""Functional Tolerancing & Annotation — datums and feature control frames on the part.

The 3D half of what `app/manufacture/drawing.py` already tabulates on a sheet. E17 task 1
carries a part's `Tolerancing` into a drawing; this puts the same statements onto the solid,
where CATIA's downstream tools (and a customer's inspection plan) look for them.

**Every call in the chain was measured on a French V5-R33 seat on 2026-09-19** (THE QUEUE E7),
and four of the findings are the reason this module exists rather than a paragraph of guesses:

1. **`iSurf` is a `UserSurface`, never a `Reference`.** Handing `CreateDatum` a face reference
   answers `Le type ne correspond pas`, and so does handing it a plane. What settled it was a
   *control*: `CreateText` and `CreateFlagNote` — nothing to do with datums — refused
   identically, so the refusal was about the parameter and not the semantics.
   `part.UserSurfaces.Generate(reference)` is the missing step.
2. **An annotation needs a view.** `AnnotationSet.ActiveView` *raises* until
   `TPSViewFactory.CreateView(planeReference, 0)` has made one.
3. **A datum reference frame comes back empty, and an empty one refuses every tolerance.**
   `CreateToleranceWithDRF` failed at all sixteen indices until
   `frame.SetFrame(label, "", "")` put the datum's letter in the first box.
4. **The characteristic index is per family, not global.** `3` is *flatness* without a frame
   and *parallelism* with one. That is why this module's vocabulary is a **name**, and why
   `CHARACTERISTICS` is two tables rather than one — a schema keyed on the number would put the
   wrong symbol on a part with nothing erroring.

**What is deliberately not offered: the tolerance value.** Creating the frame was measured;
*setting its magnitude* was not, so there is no `value_mm` parameter. A tolerance whose value
nobody set is visibly incomplete in CATIA; a tolerance carrying a number the bridge silently
failed to apply is a drawing that says 0.05 and means whatever the default was. The second is
the failure this repository refuses everywhere else, so the argument stays absent until
somebody measures `ToleranceZone`. The summaries say so, because the agent reads them.
"""

from __future__ import annotations

from typing import Final

from app.catia.ops import vocabulary as vocab
from app.catia.ops.spec import (
    Operation,
    Tier,
    Workbench,
    name_list,
    one_of,
    optional,
    required,
)

_WB: Final = Workbench.FTA

#: The characteristics this bridge will create, by family, with CATIA's index for each.
#:
#: **Two tables, because the index is per family** — measured 2026-09-19 on a planar face.
#: Without a datum frame, 3 is flatness; with one, 3 is parallelism. Only what was seen to
#: work is here: every other index answered `CreateTolerance… a échoué` on a plane, and an
#: index nobody has watched succeed is not a capability.
FORM_CHARACTERISTICS: Final[dict[str, int]] = {
    "straightness": 1,
    "flatness": 3,
    "line_profile": 6,
    "surface_profile": 7,
}

#: The ones that take datum references. `position` is the one a bolt pattern needs.
REFERENCED_CHARACTERISTICS: Final[dict[str, int]] = {
    "parallelism": 3,
    "position": 4,
    "line_profile": 7,
    "surface_profile": 8,
}

#: Every name the schema advertises, in a stable order for the enum.
CHARACTERISTICS: Final[tuple[str, ...]] = (
    "straightness",
    "flatness",
    "parallelism",
    "position",
    "line_profile",
    "surface_profile",
)

#: Which family a name belongs to depends on whether datums were given, and two names are in
#: both. `_family_error` is the refusal a caller sees; it names the alternative rather than
#: saying "invalid", because the agent's next move is to pick the other one.
ONLY_WITH_DATUMS: Final[frozenset[str]] = frozenset(
    REFERENCED_CHARACTERISTICS
) - frozenset(FORM_CHARACTERISTICS)
ONLY_WITHOUT_DATUMS: Final[frozenset[str]] = frozenset(
    FORM_CHARACTERISTICS
) - frozenset(REFERENCED_CHARACTERISTICS)

#: The type library the whole chain lives in. Recorded because **late binding sees none of
#: it** — `part.AnnotationSets` is `<COMObject <unknown>>` without it, which reads exactly
#: like an unlicensed seat. The same trap sheet metal and DMU Kinematics have, and the third
#: instance is what turned it into CLAUDE.md's rule. `EnsureModule` on *this* library only:
#: generating `MecModInterfaces` alongside it removes `AddNewPad` from `ShapeFactory`.
TYPE_LIBRARY: Final = "{88D26C84-D8E9-0000-0280-020CC3000000}"

#: The standard an annotation set is created against. A string, not an enum — the integer
#: that was tried first is what made an earlier session record this as a licence failure.
DEFAULT_STANDARD: Final = "ISO"


def characteristic_index(name: str, *, with_datums: bool) -> int:
    """CATIA's index for a characteristic in the family the call belongs to.

    Raises `ValueError` naming the other family when a name exists but not here — `position`
    without datums is a request nobody can satisfy, and "unknown characteristic" would send
    the caller looking for a spelling mistake instead of at the missing datum.
    """
    table = REFERENCED_CHARACTERISTICS if with_datums else FORM_CHARACTERISTICS
    if name in table:
        return table[name]
    if name in ONLY_WITH_DATUMS:
        raise ValueError(
            f"{name!r} is measured against datums, so it needs at least one in `datums`. "
            f"Without datums, use one of: {', '.join(sorted(FORM_CHARACTERISTICS))}."
        )
    if name in ONLY_WITHOUT_DATUMS:
        raise ValueError(
            f"{name!r} is a form tolerance and takes no datums. Drop `datums`, or use one of: "
            f"{', '.join(sorted(REFERENCED_CHARACTERISTICS))}."
        )
    raise ValueError(
        f"{name!r} is not a characteristic this bridge creates. One of: "
        f"{', '.join(CHARACTERISTICS)}."
    )


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="catia_tolerance_datum",
        summary=(
            "Put a datum on a face, and report the letter CATIA gave it.\n"
            "CATIA assigns the letter (A, then B). Create datums first; a frame "
            "references them by letter."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("face", vocab.face_reference("The face the datum is taken on.")),
        ),
    ),
    Operation(
        name="catia_tolerance_frame",
        summary=(
            "Put a feature control frame on a face: flatness, position, parallelism, profiles.\n"
            "`datums` takes letters from catia_tolerance_datum; omit it for a form tolerance. "
            "The frame gets CATIA's default value — setting the magnitude is not implemented, "
            "so edit it on the seat."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("face", vocab.face_reference("The face the tolerance applies to.")),
            required(
                "characteristic",
                one_of(CHARACTERISTICS, "Which characteristic the frame states."),
            ),
            optional(
                "datums",
                name_list(
                    "Datum letters, in order: A, then B, then C. Required for position and "
                    "parallelism; refused for straightness and flatness."
                ),
            ),
        ),
    ),
    Operation(
        name="catia_tolerance_list",
        summary=(
            "List the datums and feature control frames already on the part.\n"
            "Read from the annotation set, so frames added by hand on the seat appear too."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(),
    ),
)


#: What this workbench has and the bridge does **not** offer, with the reason. Declared as
#: data for `catia/ops/sheet_metal.py`'s reason: an absent operation reads as "nobody got to
#: it yet" and invites somebody to write one blind, while a named one is a measured fact.
UNIMPLEMENTED: Final[dict[str, str]] = {
    "tolerance_value": (
        "creating a frame was measured; setting its magnitude was not. `ToleranceZone` is the "
        "class to read off the type library, and until somebody does, a value argument would "
        "be a number the bridge might silently fail to apply."
    ),
    "datum_target": (
        "`CreateDatumTarget(iSurf, iDatum)` exists and takes two dispatches, but no target has "
        "been created on a seat, so its second argument's exact type is unverified."
    ),
    "roughness": (
        "`CreateRoughness(iSurf)` is declared in the same factory and is untried. It belongs "
        "with a surface-finish vocabulary this product does not have yet."
    ),
}


__all__ = [
    "CHARACTERISTICS",
    "DEFAULT_STANDARD",
    "FORM_CHARACTERISTICS",
    "ONLY_WITHOUT_DATUMS",
    "ONLY_WITH_DATUMS",
    "OPERATIONS",
    "REFERENCED_CHARACTERISTICS",
    "TYPE_LIBRARY",
    "UNIMPLEMENTED",
    "characteristic_index",
]
