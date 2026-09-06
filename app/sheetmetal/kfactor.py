"""The K-factor: where the neutral axis sits, and — the whole point — where the number came from.

Phase 17.3. `BA = (pi/180) * theta * (r + K*t)` is exact arithmetic; `K` is the
judgement, and it is the only place in sheet metal where two competent engineers
hand you different numbers for the same part. So this module's design is not
about computing K. It is about **never letting a K reach a flat pattern without
a stated basis**, which is Decision 3 applied to the one input that decides
whether the blank comes out the right length.

**There is no default K anywhere in this package.** `Bend` requires a `KFactor`
object, `KFactor` requires a `Source`, and `Source.__post_init__` (in
`app.solve.materials`, reused here exactly as `app/parts/` reuses it) refuses an
empty citation. A K you did not think about cannot be constructed. The one way
to proceed without a basis is `assumed()`, which is `Status.ESTIMATED`, demands
a written reason, and marks every flat pattern computed from it provisional —
the same treatment `app/rules/engine.py` gives a pass measured off a sampled
bound.

**The ANSI/DIN distinction is real and is not a formatting difference.** Two
traditions, two shipped providers, and they disagree by more than rounding:

* `machinerys_handbook()` — the US/ANSI tradition. Machinery's Handbook states
  bend allowance as `(0.0078*T + 0.01743*R) * theta` for `R < 2T` and
  `(0.0087*T + 0.01743*R) * theta` for `R >= 2T`. Divide the T coefficient by
  `pi/180` (which is what the 0.01743 on R *is*) and those two constants are
  K = 0.4469 and K = 0.4985. The table below carries the quotient rather than a
  transcribed decimal, so the provenance is legible in the source line.
* `din6935()` — the German tradition, and it is a *formula* rather than a
  table: the unfolding factor `k = 0.65 + 0.5*log10(r/t)` below `r/t = 5`, and
  `k = 1` at or above it. DIN's `k` is twice the ANSI K (its compensation value
  is written around `r + k*t/2`), so `K = k/2`.

At `r/t = 1.5` these give K = 0.4469 and K = 0.3690 — a 17% difference in K,
which on a 2 mm sheet at 90 degrees moves the bend allowance by 0.24 mm per
bend. On a six-bend enclosure that is a millimetre and a half of blank. Neither
is wrong; they are different traditions, and which one a shop uses is a fact
about the shop. That is exactly why the answer carries its source.

**K is capped at 0.5 and refused above it.** The neutral axis moves *toward* the
inside surface under bending and cannot move outward past the mid-plane, so
`0 < K <= 0.5` is not a table range, it is the physics. A number above 0.5 is
almost always a Y-factor (`Y = K * pi/2`) entered in the wrong field, and the
refusal says so.

**Continuity is the check that the DIN constants are transcribed right.** At
`r/t = 5` the formula gives `0.65 + 0.5*log10(5) = 0.99948`, and the plateau
above it is exactly 1. The two halves meeting to 5.2e-4 is a property of the
published constants, not of this code, so a test asserting it catches a
mistyped 0.65 or 0.5 in a way no recorded output could.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.sheetmetal.errors import BendError
from app.solve.materials import Source, SourceKind, Status

#: Radians per degree. Named because it is the `0.01743` in the Machinery's
#: Handbook coefficients, and the table below divides by it to recover K.
DEGREE: Final = math.pi / 180.0

#: The neutral axis lies between the inside surface and the mid-plane, so K is
#: bounded above by 0.5 by geometry rather than by convention. Bending never
#: moves it outward.
MAX_K: Final = 0.5

#: Below this ratio the DIN 6935 logarithm stops describing anything: the
#: factor falls through zero at r/t = 0.05, and every common sheet material has
#: a minimum bend radius far above 0.1*t anyway. Refused rather than clamped —
#: a clamped K is a silently wrong blank.
DIN_MINIMUM_R_OVER_T: Final = 0.1

#: Where DIN 6935's unfolding factor stops depending on r/t. Above it the
#: neutral axis has effectively reached the mid-plane (k = 1, K = 0.5).
DIN_PLATEAU_R_OVER_T: Final = 5.0


class MaterialFamily(StrEnum):
    """The coarse material groups a K table or a bend-radius rule is written for.

    Coarse on purpose: a K table keyed on grade would be a table nobody could
    populate, and the honest granularity of a published K figure is "steel" or
    "aluminium". Grade-specific numbers come from a test bend
    (`measured()`), which is the only thing that is actually specific to a
    grade *and* a set of tooling.
    """

    STEEL = "steel"
    STAINLESS = "stainless"
    ALUMINIUM = "aluminium"
    COPPER_ALLOY = "copper alloy"
    TITANIUM = "titanium"
    OTHER = "other"


class Basis(StrEnum):
    """How a K-factor was arrived at. This is the field the whole module exists for.

    `ASSUMED` is not a fifth flavour of source — it is the *absence* of one,
    kept as a named value rather than as `None` so that it prints, serialises
    and propagates into the flat pattern instead of being a missing key nobody
    notices.
    """

    STANDARD_FORMULA = "standard formula"
    TABLE = "table"
    TEST_BEND = "test bend"
    ASSUMED = "assumed"


@dataclass(frozen=True)
class KFactor:
    """One K value, its basis, its source, and the ratio it was looked up at.

    `r_over_t` is carried because K depends on it in both traditions, so a K
    quoted without the ratio it belongs to cannot be checked against the bend
    it was used on. `None` is honest for a test-bend or assumed value that was
    not tied to a ratio.
    """

    value: float
    basis: Basis
    source: Source
    status: Status
    note: str = ""
    r_over_t: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.value) or self.value <= 0.0:
            raise BendError(
                f"A K-factor of {self.value!r} is not a position for the neutral axis. "
                f"K is the fraction of the material thickness from the inside surface to "
                f"the neutral axis, so it must be greater than zero."
            )
        if self.value > MAX_K:
            raise BendError(
                f"K = {self.value:g} is above {MAX_K:g}, which bending cannot produce: the "
                f"neutral axis moves toward the inside surface, never past the mid-plane. "
                f"If this came off a datasheet it is probably a Y-factor — divide by "
                f"pi/2 ({self.value / (math.pi / 2):.4f}) and pass that."
            )
        if self.status is Status.ESTIMATED and not self.note.strip():
            raise BendError(
                "An estimated K-factor must say how it was estimated. An unexplained "
                "estimate cannot be reviewed, only believed — and a believed K is a blank "
                "cut to the wrong length. Give `why`."
            )
        if self.basis is Basis.ASSUMED and self.status is not Status.ESTIMATED:
            raise BendError(
                f"basis={self.basis} claims this K has no source and status={self.status} "
                f"claims it does. An assumed K is an estimated one by definition. The "
                f"converse does not hold and is not checked: a value derived from a "
                f"standard by analogy to another material family is ESTIMATED and still "
                f"has a basis, which is exactly what din6935() returns off steel."
            )

    @property
    def has_stated_basis(self) -> bool:
        """Whether this K came from anywhere a reviewer could go and look.

        The flat pattern reads this per bend and reports `provisional` when any
        answers false, so an assumed K is visible in the result rather than only
        at the call site that invented it.
        """
        return self.basis is not Basis.ASSUMED

    def __str__(self) -> str:
        ratio = f" at r/t={self.r_over_t:g}" if self.r_over_t is not None else ""
        return f"K={self.value:.4f}{ratio} ({self.basis}, {self.source.citation})"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "value": self.value,
            "basis": str(self.basis),
            "status": str(self.status),
            "has_stated_basis": self.has_stated_basis,
            "source": self.source.to_dict(),
        }
        if self.r_over_t is not None:
            out["r_over_t"] = self.r_over_t
        if self.note:
            out["note"] = self.note
        return out


@dataclass(frozen=True)
class Band:
    """One row of a K table: a half-open ratio band and the K in it.

    Half-open `[low, high)` so that a ratio landing exactly on a boundary
    belongs to exactly one band. Machinery's Handbook's split is written
    "R < 2T" and "R >= 2T", which is this convention already.
    """

    low_r_over_t: float
    high_r_over_t: float
    value: float

    def contains(self, r_over_t: float) -> bool:
        return self.low_r_over_t <= r_over_t < self.high_r_over_t


@dataclass(frozen=True)
class KFactorTable:
    """A cited table of K against r/t, for a named set of material families.

    The table carries its own `Source` and its own `Status`, which is the rule
    the brief for this phase states: *where a table is used, the table says
    where it came from*. A lookup outside the families the table was written
    for is refused rather than extrapolated — a steel table applied to titanium
    is not a conservative answer, it is a different answer with no error bar.
    """

    name: str
    source: Source
    status: Status
    bands: tuple[Band, ...]
    families: frozenset[MaterialFamily]
    note: str = ""

    def __post_init__(self) -> None:
        if not self.bands:
            raise BendError(
                f"K table {self.name!r} has no bands, so every lookup would fail. Give it "
                f"at least one r/t band with a K value."
            )
        ordered = sorted(self.bands, key=lambda b: b.low_r_over_t)
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier.high_r_over_t != later.low_r_over_t:
                raise BendError(
                    f"K table {self.name!r} has a gap or an overlap between r/t "
                    f"{earlier.high_r_over_t:g} and {later.low_r_over_t:g}. Bands must abut "
                    f"exactly, or a ratio in the seam silently picks whichever band is "
                    f"listed first."
                )

    def covers(self, family: MaterialFamily) -> bool:
        return family in self.families

    def k_for(
        self,
        *,
        inside_radius_mm: float,
        thickness_mm: float,
        family: MaterialFamily,
    ) -> KFactor:
        """The K this table gives for a bend, or a refusal naming what is missing."""
        ratio = _ratio(inside_radius_mm, thickness_mm)
        if not self.covers(family):
            covered = ", ".join(sorted(str(f) for f in self.families))
            raise BendError(
                f"K table {self.name!r} is written for {covered} and was asked for "
                f"{family}. Extrapolating a K table across material families is not a "
                f"conservative answer, it is an unquantified one. Use a table that covers "
                f"{family}, a test bend, or `assumed()` with the reason written down."
            )
        for band in self.bands:
            if band.contains(ratio):
                return KFactor(
                    value=band.value,
                    basis=Basis.TABLE,
                    source=self.source,
                    status=self.status,
                    note=self.note,
                    r_over_t=ratio,
                )
        raise BendError(
            f"K table {self.name!r} does not cover r/t = {ratio:g} (r={inside_radius_mm:g} mm, "
            f"t={thickness_mm:g} mm). Its bands run from "
            f"{min(b.low_r_over_t for b in self.bands):g} to "
            f"{max(b.high_r_over_t for b in self.bands):g}."
        )


#: The US/ANSI tradition, transcribed from the two coefficients Machinery's
#: Handbook prints in its bend-allowance formula rather than from a K column, so
#: the arithmetic that turns them into K is visible on the line beside them.
#: `0.01743` in the same formula is `pi/180` on the radius term, which is what
#: makes the division below the right one.
MACHINERYS_HANDBOOK: Final = KFactorTable(
    name="Machinery's Handbook bend allowance",
    source=Source(
        citation=(
            "Machinery's Handbook, 'Bending Sheet Metal' — bend allowance "
            "(0.0078 T + 0.01743 R) x angle for R < 2T, (0.0087 T + 0.01743 R) x angle "
            "for R >= 2T"
        ),
        kind=SourceKind.TEXTBOOK,
        note="K recovered as the T coefficient divided by pi/180.",
    ),
    status=Status.TYPICAL,
    bands=(
        Band(0.0, 2.0, 0.0078 / DEGREE),
        Band(2.0, math.inf, 0.0087 / DEGREE),
    ),
    families=frozenset(
        {
            MaterialFamily.STEEL,
            MaterialFamily.STAINLESS,
            MaterialFamily.ALUMINIUM,
            MaterialFamily.COPPER_ALLOY,
        }
    ),
    note=(
        "A general shop table, not a grade figure: it is a screening value for a first "
        "blank, and a production run should replace it with a test bend on the tooling "
        "that will make the part."
    ),
)

#: The source cited by `din6935()`. A module-level constant so the citation is
#: written once and every K produced by the formula carries the identical string.
DIN_6935_SOURCE: Final = Source(
    citation="DIN 6935, cold bending of flat steel products — unfolding factor k",
    kind=SourceKind.STANDARD,
    note=(
        "k = 0.65 + 0.5 log10(r/t) below r/t = 5, k = 1 at and above it. DIN's k is twice "
        "the ANSI K, because DIN writes its compensation value around r + k*t/2."
    ),
)


def _ratio(inside_radius_mm: float, thickness_mm: float) -> float:
    if not math.isfinite(inside_radius_mm) or inside_radius_mm <= 0.0:
        raise BendError(
            f"An inside bend radius of {inside_radius_mm!r} mm is not a bend. A zero or "
            f"negative radius has no neutral axis to place; state the radius the tooling "
            f"actually produces."
        )
    if not math.isfinite(thickness_mm) or thickness_mm <= 0.0:
        raise BendError(
            f"A sheet thickness of {thickness_mm!r} mm is not a sheet. Give the material "
            f"thickness in millimetres."
        )
    return inside_radius_mm / thickness_mm


def din6935(
    *,
    inside_radius_mm: float,
    thickness_mm: float,
    family: MaterialFamily = MaterialFamily.STEEL,
) -> KFactor:
    """K from the DIN 6935 unfolding factor, `K = k/2`.

    Applied to a family other than steel the answer is still returned — shops do
    use DIN's factor on aluminium and stainless — but it comes back as
    `Status.ESTIMATED` with the analogy named, which is exactly what `ESTIMATED`
    means in `app.solve.materials.Status`: derived by correlation or analogy,
    and never a design basis on its own.
    """
    ratio = _ratio(inside_radius_mm, thickness_mm)
    if ratio < DIN_MINIMUM_R_OVER_T:
        raise BendError(
            f"r/t = {ratio:g} is below {DIN_MINIMUM_R_OVER_T:g}, where DIN 6935's "
            f"logarithm stops describing a bend — its factor falls through zero at "
            f"r/t = 0.05. A radius this tight is below the minimum bend radius of every "
            f"common sheet material; either open the radius or state K from a test bend."
        )
    k_din = 1.0 if ratio >= DIN_PLATEAU_R_OVER_T else 0.65 + 0.5 * math.log10(ratio)
    if family is MaterialFamily.STEEL:
        return KFactor(
            value=k_din / 2.0,
            basis=Basis.STANDARD_FORMULA,
            source=DIN_6935_SOURCE,
            status=Status.SPECIFIED,
            r_over_t=ratio,
        )
    return KFactor(
        value=k_din / 2.0,
        basis=Basis.STANDARD_FORMULA,
        source=DIN_6935_SOURCE,
        status=Status.ESTIMATED,
        note=(
            f"DIN 6935 is written for cold-formed flat steel and was applied to {family} "
            f"by analogy. The unfolding factor is a fit to steel forming behaviour; on "
            f"another family it is a screening number, not a specified one."
        ),
        r_over_t=ratio,
    )


def machinerys_handbook(*, inside_radius_mm: float, thickness_mm: float,
                        family: MaterialFamily = MaterialFamily.STEEL) -> KFactor:
    """K from `MACHINERYS_HANDBOOK`. A thin name for the common lookup."""
    return MACHINERYS_HANDBOOK.k_for(
        inside_radius_mm=inside_radius_mm, thickness_mm=thickness_mm, family=family
    )


def measured(
    value: float,
    *,
    source: Source,
    r_over_t: float | None = None,
    note: str = "",
) -> KFactor:
    """K worked back from a test bend on the tooling that will make the part.

    The only K that is specific to a grade *and* a press *and* a die, which is
    why it outranks every table here and why `Status.MEASURED` is a design
    basis (see `app.solve.materials.Status.is_design_basis`).
    """
    return KFactor(
        value=value,
        basis=Basis.TEST_BEND,
        source=source,
        status=Status.MEASURED,
        note=note,
        r_over_t=r_over_t,
    )


def assumed(value: float, *, why: str, r_over_t: float | None = None) -> KFactor:
    """A K with no basis, said out loud.

    This exists so that "we had to pick something" is a recorded decision rather
    than a literal in a call. Everything downstream reads `has_stated_basis` and
    reports the flat pattern as provisional; nothing silently upgrades it.
    """
    if not why.strip():
        raise BendError(
            "An assumed K-factor needs a reason. 'why' is the whole difference between "
            "an assumption on the record and a number somebody typed — write what it is "
            "based on and what would replace it."
        )
    return KFactor(
        value=value,
        basis=Basis.ASSUMED,
        source=Source(
            citation=f"assumed, no published basis: {why.strip()}",
            kind=SourceKind.DERIVED,
        ),
        status=Status.ESTIMATED,
        note=why.strip(),
        r_over_t=r_over_t,
    )


def unstated(k_factors: Iterable[KFactor]) -> tuple[KFactor, ...]:
    """The K-factors in a part that have no basis. Empty is the answer you want."""
    return tuple(k for k in k_factors if not k.has_stated_basis)


__all__ = [
    "DEGREE",
    "DIN_6935_SOURCE",
    "DIN_MINIMUM_R_OVER_T",
    "DIN_PLATEAU_R_OVER_T",
    "MACHINERYS_HANDBOOK",
    "MAX_K",
    "Band",
    "Basis",
    "KFactor",
    "KFactorTable",
    "MaterialFamily",
    "assumed",
    "din6935",
    "machinerys_handbook",
    "measured",
    "unstated",
]
