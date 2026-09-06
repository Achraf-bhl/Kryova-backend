"""The sheet: one thickness, one material, and the radius below which it cracks.

Phase 17.3. A sheet-metal part is *one* material at *one* thickness — that is
what makes it sheet metal rather than a machined part — so the thickness lives
here and not on the bend. A bend does not have a thickness; the sheet it is in
does, and putting it on the bend is how two bends in one part end up with
different ones.

**The minimum bend radius is a material property with a source, on exactly the
same terms as the K-factor.** Bend a sheet tighter than the grade allows and the
outer fibre cracks; the limit is a function of the grade, its temper, and which
way the grain runs, and no formula in this package can derive it. So
`MinimumBendRadius` carries a `Source` and a `Status` (`app.solve.materials`,
reused rather than restated, as `app/parts/` does), and a material that has none
produces an **`UNMEASURED` finding naming what is missing**, never a pass.

**The shipped table is deliberately small, and is a screening table.** Five
grades, every value `Status.TYPICAL`, every one carrying the same caveat: they
are figures for a bend **across the grain** in the as-supplied condition, and
bending along the rolling direction needs more. They are here so that the checks
have something to run against and so that the shape of a sourced value is
visible; they are not a materials database, and `sheet_material()` exists beside
`SheetMaterial(...)` precisely so that a caller with a supplier's datasheet can
pass their own number and never touch the table. Decision 5 (honest scope): the
mechanism is the deliverable, the table is an illustration that says so.

**Outer-fibre strain is exact given K, and is reported.** With the neutral axis
at `r + K*t` and the outer surface at `r + t`, engineering strain on the outside
of the bend is `(1-K)*t / (r + K*t)`. It is the number that decides whether the
material cracks, it needs no table, and it is far more informative than
"below minimum radius" on its own — a grade with 30% elongation and a bend
asking 29% of it is a part somebody should look at even though it passes.

Units are millimetres and nothing converts (project rule). `factor` is a
multiple of thickness because that is how every published minimum-bend-radius
figure is written, and multiplying by `thickness_mm` is the only place it
becomes a length.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

from app.sheetmetal.errors import SheetMetalError
from app.sheetmetal.kfactor import MaterialFamily
from app.solve.materials import Source, SourceKind, Status


@dataclass(frozen=True)
class MinimumBendRadius:
    """The tightest inside radius a grade survives, as a multiple of thickness.

    Deliberately not an `app.solve.materials.Property`: `Property` looks its
    unit up in `PROPERTY_UNITS` and refuses a name that is not there, and a
    dimensionless multiple of thickness is not one of the mm-N-MPa quantities
    that table declares. Adding it there would put a sheet-metal process figure
    into the materials vocabulary the FEA layer reads. The provenance record —
    `Source`, `SourceKind`, `Status` — *is* reused, which is the part that
    matters.
    """

    #: Multiple of material thickness. 1.0 means "inside radius at least 1t".
    factor: float
    status: Status
    source: Source
    note: str = ""

    def __post_init__(self) -> None:
        if not math.isfinite(self.factor) or self.factor < 0.0:
            raise SheetMetalError(
                f"A minimum bend radius factor of {self.factor!r} is meaningless. It is a "
                f"multiple of material thickness and cannot be negative; 0.0 means the "
                f"grade bends flat on itself."
            )
        if self.status is Status.ESTIMATED and not self.note.strip():
            raise SheetMetalError(
                "An estimated minimum bend radius must say how it was estimated. "
                "An unexplained estimate of the radius at which a part cracks is not a "
                "limit, it is a guess wearing one's clothes."
            )

    def radius_mm(self, thickness_mm: float) -> float:
        """The limit as a length, for a given sheet."""
        return self.factor * thickness_mm

    def __str__(self) -> str:
        return f"{self.factor:g}t ({self.status}, {self.source.citation})"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "factor": self.factor,
            "status": str(self.status),
            "source": self.source.to_dict(),
        }
        if self.note:
            out["note"] = self.note
        return out


@dataclass(frozen=True)
class SheetMaterial:
    """What the blank is made of and how thick it is.

    `minimum_bend_radius` may be `None`, and that is the honest state for a
    material somebody named without looking anything up. It makes the minimum
    radius check `UNMEASURED` with the reason attached — never a pass, which is
    the rule `app/design/assertions.py` applies and this package does not
    re-decide.
    """

    name: str
    family: MaterialFamily
    thickness_mm: float
    minimum_bend_radius: MinimumBendRadius | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise SheetMetalError(
                "A sheet material needs a name — it appears on the flat pattern and on "
                "every finding about the part. 'DC01 1.5 mm' is a name; '' is not."
            )
        if not math.isfinite(self.thickness_mm) or self.thickness_mm <= 0.0:
            raise SheetMetalError(
                f"A sheet thickness of {self.thickness_mm!r} mm is not a sheet. Give the "
                f"material thickness in millimetres — it is the one dimension every bend "
                f"in the part shares."
            )

    def minimum_bend_radius_mm(self) -> float | None:
        """The tightest inside radius for this sheet, or `None` when unstated."""
        if self.minimum_bend_radius is None:
            return None
        return self.minimum_bend_radius.radius_mm(self.thickness_mm)

    def outer_fibre_strain(self, *, inside_radius_mm: float, k: float) -> float:
        """Engineering strain on the outside of a bend of this sheet.

        `(1-K)*t / (r + K*t)` — exact given K, and independent of bend angle,
        because the strain is a property of how far the outer fibre sits from
        the neutral axis and not of how far round it goes.
        """
        neutral_radius = inside_radius_mm + k * self.thickness_mm
        return (1.0 - k) * self.thickness_mm / neutral_radius

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "family": str(self.family),
            "thickness_mm": self.thickness_mm,
            "minimum_bend_radius": (
                None if self.minimum_bend_radius is None else self.minimum_bend_radius.to_dict()
            ),
        }
        if self.note:
            out["note"] = self.note
        return out


#: The caveat every shipped figure carries. Written once so that a value cannot
#: quietly ship without it, and so that a reader sees the same sentence on all
#: five rather than five paraphrases of it.
_SCREENING_NOTE: Final = (
    "Screening figure for a 90 degree bend across the grain in the as-supplied condition. "
    "Bending along the rolling direction needs more; a production part takes the "
    "supplier's figure for the grade, temper and orientation actually ordered."
)

_HANDBOOK: Final = Source(
    citation=(
        "Common sheet-metal forming practice, minimum inside bend radius as a multiple "
        "of thickness (handbook figures; see the supplier datasheet for the ordered grade)"
    ),
    kind=SourceKind.TEXTBOOK,
)

#: Grade key -> (family, minimum bend radius factor). Five entries, screening
#: values, and the module docstring says why it is not more.
_SHIPPED: Final[dict[str, tuple[MaterialFamily, float]]] = {
    "steel_mild_cr": (MaterialFamily.STEEL, 1.0),
    "stainless_304_annealed": (MaterialFamily.STAINLESS, 1.0),
    "aluminium_1100_o": (MaterialFamily.ALUMINIUM, 0.5),
    "aluminium_5052_h32": (MaterialFamily.ALUMINIUM, 1.0),
    "aluminium_6061_t6": (MaterialFamily.ALUMINIUM, 3.0),
}

#: The grade keys `sheet_material()` knows, for a caller that wants to list them.
SHIPPED_GRADES: Final = tuple(sorted(_SHIPPED))


def sheet_material(grade: str, *, thickness_mm: float) -> SheetMaterial:
    """A sheet of one of the shipped grades at a stated thickness.

    Refuses an unknown grade rather than inventing a material, and names what it
    does have — the alternative is a part built on a made-up minimum radius,
    which is the sheet-metal version of `set_material` silently falling back to
    steel.
    """
    try:
        family, factor = _SHIPPED[grade]
    except KeyError:
        known = ", ".join(SHIPPED_GRADES)
        raise SheetMetalError(
            f"{grade!r} is not one of the grades shipped with this package. It has "
            f"{known}. This table is a screening set, not a materials database — for "
            f"anything else build a SheetMaterial with your supplier's minimum bend "
            f"radius and its source."
        ) from None
    return SheetMaterial(
        name=grade,
        family=family,
        thickness_mm=thickness_mm,
        minimum_bend_radius=MinimumBendRadius(
            factor=factor,
            status=Status.TYPICAL,
            source=_HANDBOOK,
            note=_SCREENING_NOTE,
        ),
    )


__all__ = [
    "SHIPPED_GRADES",
    "MaterialFamily",
    "MinimumBendRadius",
    "SheetMaterial",
    "Source",
    "SourceKind",
    "Status",
    "sheet_material",
]
