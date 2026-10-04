"""Sheet Metal Design, as far as CATIA's automation API actually reaches — THE QUEUE E1.

**The headline is a refusal, and it is measured rather than assumed.** THE QUEUE E1 asked
a seat session to settle "whether `AddNewWall`/`AddNewFlange` behave as the COM
documentation says on this V5-R33 install". They do not behave any way at all: **they do
not exist**. The sheet-metal automation library — `CATShfInterfaces`,
`{AEDE231A-8E0E-11D3-827B-006094EB7FE4}` — declares exactly four classes and not one
creation method among them (read off the generated type library on 2026-09-17, before
anything was called, which is the discipline the `AddJoint` crash bought):

    SheetMetalFactory     CreateSheetMetalParameters, GetItem
    SheetMetalParameters  GetThickness                    (a getter; there is no setter)
    SheetMetalPart        CreateManufacturingFace, SaveAsDXF, SaveAsDWG
    Bend                  GetBendAngle, GetBendRadius, GetBreakAxis

So **there is no `catia_sheetmetal_wall` in this module and there cannot be one over
COM.** The registry is the list of things the bridge can be told to do, and a wall
operation would be a promise the bridge cannot keep — which is exactly why E1 says it was
"deliberately not declared" rather than written blind. Creating walls needs the Win32 UI
bridge (`app/catia_kb/ui.py`, which exists for commands COM cannot reach) and is its own
task.

**What COM does reach is worth having**, and it is the half that matters for Decision 1's
"the result lands in CATIA, where the customer works":

* **the parameters** — thickness, bend radius and the K-factor, set and read;
* **the flat pattern** — `CreateManufacturingFace` then `SaveAsDXF`, which is CATIA's own
  unfold and therefore the independent second arithmetic `app/sheetmetal/unfold.py` can
  be checked against;
* **the bends** — each one's angle and radius, read back from the part.

Four things measured on this seat on 2026-09-17, each of which would otherwise be a guess
-----------------------------------------------------------------------------------------
1. **`CreateSheetMetalParameters()` works on an empty part.** So the parameters *can* be
   set before the first wall, which is E1's second question. The part becomes a
   sheet-metal part at that call.
2. **The thickness has no setter on the COM object and is settable through knowledge-ware.**
   `params.Thickness = x` and `params.SetThickness(x)` are both refused; the same quantity
   as `part.Parameters` → `…\\Epaisseur` takes the write, and `GetThickness()` then returns
   it. Set 3.5, read 3.5 — the round trip is proved, not assumed.
3. **The parameter names are LOCALISED, and this is the trap.** On this French seat they
   are `Epaisseur`, `Rayon pli`, `Facteur perte au pli`. CLAUDE.md's rule that "the COM
   automation API is not localised" is about *method* names and holds; a parameter's name
   is user-visible data and translates. A table keyed on `"Thickness"` works in English and
   silently finds nothing here — so every lookup below is by **suffix across a language
   table**, and a miss is refused by name rather than defaulted.
4. **The K-factor is computed by CATIA, not chosen, until the DIN formula is switched
   off.** Its default is `0.40051499783199057` — not a number anybody typed — and writing
   to it is refused while `…\\Formule norme DIN\\Activity` is true. Deactivate that and the
   write takes. The formula depends on `r/t` alone and was solved exactly against three
   measured points:

       K = (0.5 + 0.5 * log10(2 * r / t)) / 2

   which is **DIN 6935's bend-allowance factor halved, with the unrounded constant**: the
   printed standard says `k = 0.65 + 0.5 log10(r/t)`, and CATIA uses
   `0.5 + 0.5 log10 2 = 0.650515…`. Using the printed 0.65 instead is wrong by a constant
   2.575e-4 at every ratio — small, plausible, and exactly the size of disagreement between
   two unfold implementations that nobody can explain. `app/sheetmetal/` takes K as a free
   input, so a comparison must either hand CATIA Kryova's K (deactivating the formula) or
   read CATIA's and use it; **it may not let each use its own.**
"""

from __future__ import annotations

from typing import Final

from app.catia.ops.spec import (
    Operation,
    Tier,
    Workbench,
    distance,
    flag,
    length,
    optional,
    ratio,
    required,
)

_WB: Final = Workbench.SHEET_METAL

#: The sheet-metal parameter names, per interface language, keyed by the name this
#: product uses. **Localised on purpose**: a parameter's name is user-visible data and
#: translates, unlike a COM method name. Measured on a French R33 seat; the English
#: spellings are CATIA's published ones and are marked as unverified here rather than
#: presented as measured, because no English seat has been seen.
PARAMETER_NAMES: Final[dict[str, tuple[str, ...]]] = {
    # (French — measured on this seat, English — published, not verified here)
    "thickness": ("Epaisseur", "Thickness"),
    "bend_radius": ("Rayon pli", "Bend radius", "BendRadius"),
    "k_factor": ("Facteur perte au pli", "Bend Allowance", "KFactor"),
    "minimum_bend_radius": ("Rayon de pli minimal", "Minimum bend radius"),
    "din_formula_active": (
        "Formule norme DIN\\Activity",
        "DIN standard formula\\Activity",
    ),
}

#: The type library the four sheet-metal classes live in. Recorded because late binding
#: does not see them — the same trap DMU Kinematics has, and the reason THE QUEUE E6 spent
#: a session concluding a licence was missing when it was not.
TYPE_LIBRARY: Final = "{AEDE231A-8E0E-11D3-827B-006094EB7FE4}"

#: CATIA's own K-factor when its DIN formula drives it, as a function of `r / t`.
#: Solved exactly against three measured points on 2026-09-17; see the module docstring.
#: Exposed so `app/sheetmetal/` can be handed the *same* K rather than each using its own.
DIN_CONSTANT: Final = 0.5


def din_k_factor(bend_radius_mm: float, thickness_mm: float) -> float:
    """CATIA's DIN K-factor for this radius and thickness.

    `K = (0.5 + 0.5 log10(2 r / t)) / 2`, which reproduces the seat to the last bit at
    r/t = 1, 2 and 4. **Not the printed DIN 6935 constant**: that reads 0.65 and CATIA uses
    `0.5 + 0.5 log10 2 = 0.650515…`, a difference of 2.575e-4 in K at every ratio.

    Raises on a non-positive thickness rather than returning a number, because `log10` of
    a non-positive ratio is not a K-factor and a NaN here would travel into a blank length.
    """
    import math

    if thickness_mm <= 0.0 or bend_radius_mm <= 0.0:
        raise ValueError(
            f"A bend radius of {bend_radius_mm} mm and a thickness of {thickness_mm} mm "
            "do not describe a bend. Both must be positive for the DIN formula."
        )
    return (DIN_CONSTANT + 0.5 * math.log10(2.0 * bend_radius_mm / thickness_mm)) / 2.0


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="catia_sheetmetal_start",
        summary=(
            "Make the open part a sheet-metal part and set its parameters.\n"
            "Works on an empty part. Omit k_factor to let CATIA compute it from the "
            "DIN formula; give one and the formula is switched off first."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("thickness_mm", length("Sheet thickness.")),
            optional("bend_radius_mm", length("Default inner bend radius.")),
            optional("k_factor", ratio("Neutral-axis position, 0 to 1.")),
        ),
    ),
    Operation(
        name="catia_sheetmetal_parameters",
        summary=(
            "Read the part's thickness, bend radius and K-factor.\n"
            "Also says whether CATIA is computing that K itself, which is the "
            "commonest reason two unfolds disagree."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(),
    ),
    Operation(
        name="catia_sheetmetal_bends",
        summary=(
            "List the part's bends with each one's angle and radius.\n"
            "Read from the part, so a bend the workbench adjusted reports what it "
            "became."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(),
    ),
    Operation(
        name="catia_sheetmetal_export_flat",
        summary=(
            "Export CATIA's own flat pattern and attach it to the conversation.\n"
            "CATIA's unfold, not Kryova's, so a blank can be checked against it."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            optional("tolerance_mm", distance("Chord tolerance. Default 0.1 mm.")),
            optional("as_dwg", flag("Write DWG instead of DXF. Default false.")),
        ),
        server_fields=("max_inline_bytes",),
        long_running=True,
    ),
)


#: Operations this workbench has and **COM cannot reach**, with the reason.
#:
#: Declared as data rather than left out, because "the registry has no wall operation" is
#: indistinguishable from "nobody got to it yet" when it is simply absent — and the first
#: is a measured fact about V5's automation API while the second invites somebody to write
#: one blind. `tests/test_catia_sheet_metal.py` asserts none of these is in `OPERATIONS`.
UNREACHABLE_OVER_COM: Final[dict[str, str]] = {
    "wall": (
        "CATShfInterfaces declares no wall creation of any kind. The `AddNewWall` the "
        "documentation describes is not in the automation API on V5-R33 — measured "
        "2026-09-17 by reading the type library. It needs the Win32 UI bridge."
    ),
    "flange": (
        "Same: no `AddNewFlange`, and no flange class at all. The four sheet-metal "
        "classes are SheetMetalFactory, SheetMetalParameters, SheetMetalPart and Bend."
    ),
    "unfold_in_place": (
        "There is no unfold command over COM. `SheetMetalPart.CreateManufacturingFace` "
        "builds the flat pattern as an exportable face — which is what "
        "`catia_sheetmetal_export_flat` uses — but it does not switch the part into the "
        "unfolded view a user would see."
    ),
}


__all__ = [
    "DIN_CONSTANT",
    "OPERATIONS",
    "PARAMETER_NAMES",
    "TYPE_LIBRARY",
    "UNREACHABLE_OVER_COM",
    "din_k_factor",
]
