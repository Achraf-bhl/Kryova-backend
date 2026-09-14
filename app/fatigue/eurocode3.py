"""EN 1993-1-9:2005 in code: the rules that sit around a detail category.

`material.WeldDetail` and `material.ShearDetail` turn a category into a curve, and
`weld_catalogue.py` says which categories a joint may have. This module holds the rest of what the
standard adds to a category, each rule citing the clause and the page it was read from:

- the category sets drawn in Figures 7.1 and 7.2;
- the size factor k_s of Table 8.3 (§7.2.2, eq. 7.1);
- the partial factor γMf of Table 3.1, recorded as the recommendation it is;
- the reduced effective range of §7.2.1, for non-welded or stress-relieved details only;
- the range limit of §8(1), the verification of §8(2) and the interaction of §8(3).

The pages were read from rendered images, because the PDF's text layer is broken OCR.
`docs/eurocode3-fatigue-reading.md` holds the page map and the notes.

**What is not here.** The damage-equivalent ranges Δσ_E,2 and Δτ_E,2 that §8 verifies are
inputs with a source. Computing them from a collective needs Annex A or the λ factors of an
application part, and neither has been read. BS 7608 has not been read either, and nothing in this
package encodes it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from app.design.assertions import Outcome
from app.fatigue.history import Collective, CycleBlock, SignConvention
from app.fatigue.material import ShearDetail, WeldDetail
from app.fatigue.sources import require_source

#: The document every rule here was read from.
STANDARD: Final = "EN 1993-1-9:2005"
READ_FROM: Final = (
    "BS EN 1993-1-9:2005 incorporating corrigendum AC2, Public.Resource.Org compilation "
    "'Eurocode 3: Design of steel structures', read 2026-09-14 from "
    "https://gaprojekt.com/wp-content/uploads/2021/11/Eurocode-3-Design-Of-Steel-Structures.pdf"
)

#: Figure 7.1 (p. 15), the direct stress detail categories Δσc in N/mm², read off the curve labels.
DIRECT_CATEGORIES_MPA: Final = (
    160.0, 140.0, 125.0, 112.0, 100.0, 90.0, 80.0, 71.0, 63.0, 56.0, 50.0, 45.0, 40.0, 36.0,
)
#: Figure 7.2 (p. 16), the shear stress detail categories Δτc in N/mm².
SHEAR_CATEGORIES_MPA: Final = (100.0, 80.0)

#: Table 8.3's size effect, "size effect for t>25mm: k_s=(25/t)^0.2".
SIZE_EFFECT_THICKNESS_MM: Final = 25.0
SIZE_EFFECT_EXPONENT: Final = 0.2

#: §7.2.1(2): "adding the tensile portion of the stress range and 60% of the magnitude of the
#: compressive portion of the stress range".
COMPRESSIVE_PORTION: Final = 0.6

#: §8(1), eq. (8.1): Δσ ≤ 1,5·f_y and Δτ ≤ 1,5·f_y/√3.
RANGE_LIMIT_FACTOR: Final = 1.5

#: §8(3), eq. (8.3): the exponents on the direct and the shear ratio.
INTERACTION_DIRECT_EXPONENT: Final = 3.0
INTERACTION_SHEAR_EXPONENT: Final = 5.0


class StressKind(StrEnum):
    """Which of the two curve families a range belongs to: Figure 7.1 or Figure 7.2."""

    DIRECT = "direct"
    SHEAR = "shear"


def require_category(category_mpa: float, stress: StressKind) -> float:
    """Return a category the standard's figures draw, or refuse it.

    This checks a category read from EN 1993-1-9's own tables. It is not applied to `WeldDetail`,
    because the National Annex may give categories for details the tables do not cover (§7.1(5)
    NOTE), and a reduced category Δσc,red = k_s·Δσc is not on the figure either.
    """
    drawn = DIRECT_CATEGORIES_MPA if stress is StressKind.DIRECT else SHEAR_CATEGORIES_MPA
    figure = "Figure 7.1" if stress is StressKind.DIRECT else "Figure 7.2"
    if category_mpa not in drawn:
        raise ValueError(
            f"{category_mpa:g} MPa is not a {stress} stress detail category. {STANDARD} {figure} "
            f"draws {', '.join(f'{c:g}' for c in drawn)}. A category between two curves is not "
            "one the standard defines; use the lower curve, or a category the National Annex gives."
        )
    return category_mpa


def _positive(value: float, what: str) -> float:
    if not (value > 0.0 and math.isfinite(value)):
        raise ValueError(f"{what} must be positive and finite, in mm; got {value!r}.")
    return value


def thickness_size_factor(t_mm: float) -> float:
    """k_s = (25/t)^0.2 for t > 25 mm, and 1 otherwise (Table 8.3, pp. 22–23; §7.2.2 eq. 7.1).

    The table says "size effect for t>25mm". A thinner plate is not given a factor above 1, so
    none is invented.
    """
    _positive(t_mm, "The plate thickness t")
    if t_mm <= SIZE_EFFECT_THICKNESS_MM:
        return 1.0
    return (SIZE_EFFECT_THICKNESS_MM / t_mm) ** SIZE_EFFECT_EXPONENT


def eccentric_step_size_factor(t1_mm: float, t2_mm: float, e_mm: float) -> float:
    """k_s of Table 8.3 detail 17 (p. 23), a butt weld between thicknesses with no transition.

        k_s = (25/t₁)^0.2 / (1 + 6e/t₁ · t₁^1.5 / (t₁^1.5 + t₂^1.5)),  t₂ ≥ t₁

    The table labels it "size effect for t>25mm and/or generalization for eccentricity". It is read
    here as the size term applying only above 25 mm, as everywhere else in the table, and the
    eccentricity term applying always. With e = 0 and t₁ ≤ 25 mm the factor is 1.
    """
    _positive(t1_mm, "The thinner plate's thickness t1")
    _positive(t2_mm, "The thicker plate's thickness t2")
    if t2_mm < t1_mm:
        raise ValueError(
            f"Table 8.3 detail 17 draws t2 ≥ t1, and t1 is the thinner plate; got t1 = {t1_mm:g} mm "
            f"and t2 = {t2_mm:g} mm. Swap them: the formula is not symmetric."
        )
    if not (e_mm >= 0.0 and math.isfinite(e_mm)):
        raise ValueError(f"The eccentricity e is a distance between centrelines and is not negative; got {e_mm!r}.")
    share = t1_mm**1.5 / (t1_mm**1.5 + t2_mm**1.5)
    return thickness_size_factor(t1_mm) / (1.0 + 6.0 * e_mm / t1_mm * share)


class AssessmentMethod(StrEnum):
    """§3(7): the two ways EN 1993-1-9 lets the required reliability be achieved."""

    DAMAGE_TOLERANT = "damage tolerant"
    SAFE_LIFE = "safe life"


class Consequence(StrEnum):
    """Table 3.1's two columns, "Consequence of failure"."""

    LOW = "low consequence"
    HIGH = "high consequence"


@dataclass(frozen=True)
class PartialFactor:
    """A partial factor, γFf or γMf, with where its value came from."""

    value: float
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", require_source(self.source, "A partial factor"))
        if not (self.value > 0.0 and math.isfinite(self.value)):
            raise ValueError(f"A partial factor is a positive, finite multiplier; got {self.value!r}.")


#: Table 3.1 (p. 11), "Recommended values for partial factors for fatigue strength".
_TABLE_3_1: Final = {
    (AssessmentMethod.DAMAGE_TOLERANT, Consequence.LOW): 1.00,
    (AssessmentMethod.DAMAGE_TOLERANT, Consequence.HIGH): 1.15,
    (AssessmentMethod.SAFE_LIFE, Consequence.LOW): 1.15,
    (AssessmentMethod.SAFE_LIFE, Consequence.HIGH): 1.35,
}


def recommended_gamma_mf(method: AssessmentMethod, consequence: Consequence) -> PartialFactor:
    """γMf from Table 3.1, as a recommendation that a National Annex may replace.

    The standard's NOTE under §3(7), quoted: "The National Annex may give the choice of the
    assessment method, definitions of classes of consequences and numerical values for γMf". So
    the source says the value is recommended, and names the NOTE. Choosing the method and the
    consequence class is the engineer's.
    """
    value = _TABLE_3_1[(method, consequence)]
    return PartialFactor(
        value=value,
        source=(
            f"{STANDARD} Table 3.1 (p. 11), recommended value for the {method} method with "
            f"{consequence} of failure; the National Annex may give the choice of the assessment "
            "method, definitions of classes of consequences and numerical values for γMf (§3(7) NOTE)"
        ),
    )


class WeldState(StrEnum):
    """What §7.2.1 asks of a detail before its compressive portion may be reduced."""

    NON_WELDED = "non-welded"
    STRESS_RELIEVED = "stress-relieved welded"
    AS_WELDED = "as-welded"


def effective_range_mpa(maximum_mpa: float, minimum_mpa: float, *, state: WeldState) -> float:
    """The reduced effective range of §7.2.1 (p. 17) and Figure 7.4 (p. 18).

    §7.2.1(2): "The effective stress range may be calculated by adding the tensile portion of the
    stress range and 60% of the magnitude of the compressive portion of the stress range". For a
    cycle through zero that is |σmax| + 0.6·|σmin|, Figure 7.4's formula.

    **An as-welded detail is refused.** §7.2.1 is titled "Non-welded or stress-relieved welded
    details in compression". An as-welded joint carries a residual stress near yield, so a cycle
    that is compressive in the applied stress is not compressive at the weld.
    """
    if state is WeldState.AS_WELDED:
        raise ValueError(
            f"{STANDARD} §7.2.1 reduces the compressive portion only for non-welded or "
            "stress-relieved welded details. An as-welded detail counts its whole range, because "
            "its residual stress is at yield and the applied compression does not close the crack."
        )
    for value, what in ((maximum_mpa, "maximum"), (minimum_mpa, "minimum")):
        if not math.isfinite(value):
            raise ValueError(f"A cycle's {what} stress must be finite.")
    if maximum_mpa < minimum_mpa:
        raise ValueError(
            f"A cycle's maximum ({maximum_mpa:g} MPa) is below its minimum ({minimum_mpa:g} MPa)."
        )
    tensile = max(maximum_mpa, 0.0) - max(minimum_mpa, 0.0)
    compressive = min(maximum_mpa, 0.0) - min(minimum_mpa, 0.0)
    return tensile + COMPRESSIVE_PORTION * compressive


def effective_collective(collective: Collective, *, state: WeldState, source: str) -> Collective:
    """A collective with every range reduced per §7.2.1, and its mean stress spent.

    §7.2.1(1) says the reduced range is how "the mean stress influence on the fatigue strength
    may be taken into account". So each returned block carries **zero mean**: a mean-stress
    correction applied afterwards would count the same influence twice. The source says the mean
    was used here.

    The collective must be signed. An unsigned one has no compressive portion to find.
    """
    if collective.sign is not SignConvention.SIGNED:
        raise ValueError(
            "§7.2.1 separates the tensile from the compressive portion of each range, and an "
            "unsigned collective does not say which is which. Count a signed history."
        )
    why = require_source(source, "Applying §7.2.1's reduced range")
    blocks = []
    for block in collective.blocks:
        upper = block.mean_mpa + block.amplitude_mpa
        lower = block.mean_mpa - block.amplitude_mpa
        effective = effective_range_mpa(upper, lower, state=state)
        blocks.append(CycleBlock(amplitude_mpa=effective / 2.0, mean_mpa=0.0, cycles=block.cycles))
    return replace(
        collective,
        blocks=tuple(blocks),
        source=(
            f"{collective.source}; ranges reduced for a {state} detail per {STANDARD} §7.2.1, "
            f"which takes the mean stress into account, so every block carries zero mean: {why}"
        ),
    )


def range_limit_mpa(yield_strength_mpa: float, stress: StressKind) -> float:
    """§8(1), eq. (8.1): 1,5·f_y for a direct range, 1,5·f_y/√3 for a shear range (p. 18)."""
    if not (yield_strength_mpa > 0.0 and math.isfinite(yield_strength_mpa)):
        raise ValueError(f"The yield strength f_y must be positive and finite, in MPa; got {yield_strength_mpa!r}.")
    limit = RANGE_LIMIT_FACTOR * yield_strength_mpa
    return limit if stress is StressKind.DIRECT else limit / math.sqrt(3.0)


@dataclass(frozen=True)
class RangeCheck:
    """The outcome of §8(1) for one range."""

    stress: StressKind
    range_mpa: float
    limit_mpa: float
    outcome: Outcome
    statement: str


def check_range_limit(
    range_mpa: float, *, yield_strength_mpa: float, stress: StressKind, source: str
) -> RangeCheck:
    """Whether a nominal, modified nominal or geometric range stays within §8(1)'s limit.

    §8(1) applies to ranges "due to frequent loads ψ1 Qk (see EN 1990)". This function cannot
    see which load produced the range, so `source` must say, and the statement repeats it.
    """
    why = require_source(source, "A range checked against §8(1)")
    if not (range_mpa >= 0.0 and math.isfinite(range_mpa)):
        raise ValueError(f"A stress range is not negative; got {range_mpa!r}.")
    limit = range_limit_mpa(yield_strength_mpa, stress)
    outcome = Outcome.PASSED if range_mpa <= limit else Outcome.FAILED
    formula = "1,5·f_y" if stress is StressKind.DIRECT else "1,5·f_y/√3"
    return RangeCheck(
        stress=stress,
        range_mpa=range_mpa,
        limit_mpa=limit,
        outcome=outcome,
        statement=(
            f"{STANDARD} §8(1): {stress} range {range_mpa:.4g} MPa against {formula} = "
            f"{limit:.4g} MPa with f_y = {yield_strength_mpa:g} MPa, for ranges due to frequent "
            f"loads ψ1·Qk; the range is from: {why}"
        ),
    )


@dataclass(frozen=True)
class EquivalentRange:
    """Δσ_E,2 or Δτ_E,2: the damage-equivalent constant range at 2 million cycles, with its source.

    Not computed here: see the module docstring.
    """

    stress: StressKind
    range_mpa: float
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", require_source(self.source, "A damage-equivalent range"))
        if not (self.range_mpa >= 0.0 and math.isfinite(self.range_mpa)):
            raise ValueError(f"A damage-equivalent range is not negative; got {self.range_mpa!r}.")


@dataclass(frozen=True)
class Verification:
    """§8(2) for each range given, and §8(3) when both are.

    A ratio is γFf·Δσ_E,2 / (Δσ_C/γMf). `interaction` is None unless both a direct and a shear
    range were given; with both, it is ratio_direct³ + ratio_shear⁵, and the verification fails if
    it exceeds 1 even when each ratio alone does not.
    """

    direct_ratio: float | None
    shear_ratio: float | None
    interaction: float | None
    outcome: Outcome
    statements: tuple[str, ...]


def verify(
    *,
    gamma_ff: PartialFactor,
    gamma_mf: PartialFactor,
    direct: tuple[EquivalentRange, WeldDetail] | None = None,
    shear: tuple[EquivalentRange, ShearDetail] | None = None,
) -> Verification:
    """EN 1993-1-9 §8(2), eq. (8.2), and §8(3), eq. (8.3) (p. 18).

    Neither partial factor has a default. γMf can come from `recommended_gamma_mf`; γFf is not in
    the pages read. §8(3) begins "Unless otherwise stated in the fatigue strength categories in
    Table 8.8 and Table 8.9". Those tables (orthotropic decks) are not in the catalogue, so the
    interaction is applied whenever both ranges are given, and the statement says so.
    """
    if direct is None and shear is None:
        raise ValueError(
            "§8 verifies a direct range, a shear range, or both. Give Δσ_E,2 with its detail, "
            "Δτ_E,2 with its shear detail, or both."
        )
    statements: list[str] = [
        f"γFf = {gamma_ff.value:g} [{gamma_ff.source}]",
        f"γMf = {gamma_mf.value:g} [{gamma_mf.source}]",
    ]

    def ratio(equivalent: EquivalentRange, category_mpa: float, symbol: str) -> float:
        value = gamma_ff.value * equivalent.range_mpa / (category_mpa / gamma_mf.value)
        statements.append(
            f"{STANDARD} §8(2): γFf·{symbol}E,2 / ({symbol}C/γMf) = {gamma_ff.value:g} × "
            f"{equivalent.range_mpa:.4g} / ({category_mpa:.4g}/{gamma_mf.value:g}) = {value:.4g}; "
            f"{symbol}E,2 from: {equivalent.source}"
        )
        return value

    direct_ratio: float | None = None
    shear_ratio: float | None = None
    if direct is not None:
        equivalent, detail = direct
        if equivalent.stress is not StressKind.DIRECT:
            raise ValueError("The direct verification was given a shear range. Pass it as `shear`.")
        direct_ratio = ratio(equivalent, detail.detail_category_mpa, "Δσ")
    if shear is not None:
        equivalent, shear_detail = shear
        if equivalent.stress is not StressKind.SHEAR:
            raise ValueError("The shear verification was given a direct range. Pass it as `direct`.")
        shear_ratio = ratio(equivalent, shear_detail.detail_category_mpa, "Δτ")

    interaction: float | None = None
    if direct_ratio is not None and shear_ratio is not None:
        interaction = (
            direct_ratio**INTERACTION_DIRECT_EXPONENT + shear_ratio**INTERACTION_SHEAR_EXPONENT
        )
        statements.append(
            f"{STANDARD} §8(3): ({direct_ratio:.4g})³ + ({shear_ratio:.4g})⁵ = {interaction:.4g}; "
            "Tables 8.8 and 8.9, which may say otherwise for their details, are not encoded"
        )

    checked = [r for r in (direct_ratio, shear_ratio, interaction) if r is not None]
    outcome = Outcome.PASSED if all(r <= 1.0 for r in checked) else Outcome.FAILED
    return Verification(
        direct_ratio=direct_ratio,
        shear_ratio=shear_ratio,
        interaction=interaction,
        outcome=outcome,
        statements=tuple(statements),
    )


__all__ = [
    "COMPRESSIVE_PORTION",
    "DIRECT_CATEGORIES_MPA",
    "READ_FROM",
    "SHEAR_CATEGORIES_MPA",
    "STANDARD",
    "AssessmentMethod",
    "Consequence",
    "EquivalentRange",
    "PartialFactor",
    "RangeCheck",
    "StressKind",
    "Verification",
    "WeldState",
    "check_range_limit",
    "eccentric_step_size_factor",
    "effective_collective",
    "effective_range_mpa",
    "range_limit_mpa",
    "recommended_gamma_mf",
    "require_category",
    "thickness_size_factor",
    "verify",
]
