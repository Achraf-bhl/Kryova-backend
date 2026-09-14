"""The material's fatigue strength: the S-N curve, its scatter, and where it came from.

This is the input a static analysis never needed. `app/solve/materials.py` carries
modulus, Poisson's ratio, yield and density — handbook figures that vary by a few
percent between sources and change an answer by a few percent. Fatigue data does not
behave like that. Two heats of the same nominal steel, machined the same way, differ
in life by a factor that a factor-of-two scatter band barely covers, and the curve is
a *power law*: at a slope of 5, a 10% error in the endurance strength is a 60% error
in life. So no S-N curve is shipped as a default here, and there is no material table
in this module. A curve is an input, it names its source, and an assessment that does
not have one cannot be made.

**What the code does and what an engineer does.** The code does arithmetic on a
declared curve: the Basquin power law, the transformation to another failure
probability given a declared scatter, the Eurocode 3 segment geometry. The engineer
chooses the curve, the detail category, the slope below the knee, and the failure
probability the design is to be checked at. Those four choices decide the answer, and
all four are constructor arguments with a `source`.

**The curve is in stress amplitude.** Unwelded metal S-N data is usually published
that way (σa at R = −1). Weld standards are written in stress *range* Δσ. The halving
happens exactly once, inside `WeldDetail.eurocode_3`, at the point the standard is
read — the same rule the rest of the codebase keeps for units: convert at the
boundary, never deeper in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from app.fatigue.sources import require_source

#: Eurocode 3 Part 1-9 fixes the reference life of a detail category at 2e6 cycles,
#: the constant-amplitude fatigue limit at 5e6, and the cut-off at 1e8, with slope
#: m = 3 up to the first and m = 5 between the first and second. Everything
#: `WeldDetail.sn_curve` computes follows from those five numbers by continuity, so
#: they are named rather than inlined and the derivations are checkable arithmetic.
EC3_REFERENCE_CYCLES: float = 2.0e6
EC3_KNEE_CYCLES: float = 5.0e6
EC3_CUTOFF_CYCLES: float = 1.0e8
EC3_SLOPE_BELOW_REFERENCE: float = 3.0
EC3_SLOPE_ABOVE_KNEE: float = 5.0
#: EN 1993-1-9:2005 §7.1, NOTE 1 after Figure 7.2: Δσc at 2 million cycles was calculated "for a
#: 75% confidence level of 95% probability of survival for log N". Read from the standard's text.
EC3_FAILURE_PROBABILITY: float = 0.05
#: EN 1993-1-9:2005 §7.1(2): shear ranges follow Δτ_R^m·N_R = Δτ_C^m·2·10⁶ with m = 5 for
#: N ≤ 10⁸, and Δτ_L = (2/100)^(1/5)·Δτ_C is the cut-off (p. 14–15). There is no knee.
EC3_SHEAR_SLOPE: float = 5.0


@dataclass(frozen=True)
class SNCurve:
    """A Wöhler curve as a two-slope Basquin law, in stress amplitude and cycles.

        N(σa) = ND · (σa / SD) ** −k1        for σa ≥ SD
        N(σa) = ND · (σa / SD) ** −k2        for σa < SD, when k2 is given
        N(σa) = ∞                            for σa < SD, when k2 is None

    Four fields carry judgements a qualified engineer makes, and each is called out
    here because the docstring is where the reviewer will look for them:

    * **`slope_k2` — what happens below the knee.** `None` means amplitudes below
      the endurance limit do no damage at all (Miner *original*). Under
      variable-amplitude loading that is **non-conservative**: the large cycles blunt
      the notch and drag the limit down, so small cycles that would be harmless alone
      start to count. Haibach's convention, k2 = 2·k1 − 1, is the usual answer and is
      available as `with_haibach_slope()` — but it is *applied only when asked for*,
      because "which of Miner original, elementary or modified applies to this duty
      cycle" is exactly the judgement Phase 8 is marked *needs an ME* for. The
      assessment records whichever was used as a stated assumption.
    * **`scatter_tn` / `scatter_ts`** — the measured scatter, N90/N10 and S90/S10.
      Absent means *unknown*, not *none*. An assessment asked for a survival
      probability other than 50% against a curve with no scatter comes back
      `UNMEASURED`: there is no defensible way to invent a scatter band, and doing so
      would put a reliability number on a design that has none.
    * **`failure_probability`** — the probability the curve *as given* describes.
      Published median curves are 0.5; a design curve from a standard may already be
      at 0.025 or 0.05, and using one as if it were the median double-counts the
      safety twice over.
    * **`mean_stress_sensitivity` (FKM's M)** — the slope of the Haigh diagram. There
      is no universal value; it rises with tensile strength and is different for
      welded and unwelded material. Absent means the assessment cannot correct for
      mean stress and will say so rather than assume M = 0, which is the
      non-conservative assumption.
    """

    slope_k1: float
    knee_cycles: float
    knee_amplitude_mpa: float
    source: str
    slope_k2: float | None = None
    scatter_tn: float | None = None
    scatter_ts: float | None = None
    failure_probability: float = 0.5
    mean_stress_sensitivity: float | None = None
    #: The stress ratio the curve was measured at. Carried, not used to correct:
    #: the correction is the Haigh diagram's job and needs M, which is separate.
    stress_ratio: float = -1.0
    #: Free text naming the standard, if the curve came from one. Printed in the
    #: result's method so the reviewer sees which document was applied.
    standard: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", require_source(self.source, "An S-N curve"))
        if not (self.slope_k1 > 0.0 and math.isfinite(self.slope_k1)):
            raise ValueError(
                "The Wöhler slope k1 is the positive exponent in N ∝ σ**−k1 — typically "
                "3 for welds, 5 to 15 for polished unwelded metal. A non-positive slope "
                "says life rises with stress."
            )
        if self.slope_k2 is not None and not (self.slope_k2 > 0.0 and math.isfinite(self.slope_k2)):
            raise ValueError("The second Wöhler slope k2 must be positive and finite when given.")
        if not (self.knee_cycles > 0.0 and math.isfinite(self.knee_cycles)):
            raise ValueError("The knee (endurance) cycle count must be positive and finite.")
        if not (self.knee_amplitude_mpa > 0.0 and math.isfinite(self.knee_amplitude_mpa)):
            raise ValueError("The endurance stress amplitude must be positive and finite, in MPa.")
        if not 0.0 < self.failure_probability < 1.0:
            raise ValueError(
                "A failure probability is strictly between 0 and 1. A curve claimed to "
                "describe zero or certain failure describes nothing."
            )
        for scatter, name in ((self.scatter_tn, "TN"), (self.scatter_ts, "TS")):
            if scatter is not None and not scatter >= 1.0:
                raise ValueError(
                    f"The scatter {name} is a ratio of the 90% to the 10% value and is "
                    "at least 1.0; 1.0 means no scatter was observed, which is itself a "
                    "claim about the test series."
                )

    @property
    def has_scatter(self) -> bool:
        """Whether a survival probability other than the curve's own can be derived."""
        return self.scatter_tn is not None or self.scatter_ts is not None

    def with_haibach_slope(self, source: str) -> SNCurve:
        """The same curve with Haibach's k2 = 2·k1 − 1 below the knee.

        A separate call, and it takes its own `source`, because adopting Haibach
        is a *decision about the duty cycle* — it is right when the spectrum
        contains amplitudes above the knee that damage the endurance limit, and
        unnecessary when it does not. Making it the default would silence the
        decision; making it unavailable would force everybody to compute 2k−1 by
        hand and get it wrong once.
        """
        return replace(
            self,
            slope_k2=2.0 * self.slope_k1 - 1.0,
            source=f"{self.source}; k2 per Haibach (2·k1−1): {require_source(source, 'A Haibach slope')}",
        )

    def scaled(self, factor: float, why: str) -> SNCurve:
        """The curve with its endurance amplitude multiplied and its knee cycle count held.

        **This is a convention and it is stated because it is not the only one.**
        Scaling SD with ND fixed slides the whole finite-life line down parallel
        to itself (same k1, new knee point). The alternative — Shigley's — anchors
        the line at 10³ cycles to 0.9·Sut and rotates it, so the finite-life
        region is penalised less than the endurance region. That needs the
        ultimate strength, which is not a fatigue property and is not on this
        curve, and the parallel shift is the more conservative of the two in the
        finite-life range where most machine duty cycles sit.

        A qualified engineer who wants the rotating construction should build the
        second curve explicitly and pass it in; this function will not guess.
        """
        if not (factor > 0.0 and math.isfinite(factor)):
            raise ValueError(
                "A strength-modifying factor is a positive multiplier on the endurance "
                "amplitude. Zero or negative describes a material with no strength."
            )
        return replace(
            self,
            knee_amplitude_mpa=self.knee_amplitude_mpa * factor,
            source=f"{self.source}; endurance amplitude × {factor:.6g} ({why})",
        )


@dataclass(frozen=True)
class WeldDetail:
    """A welded joint's fatigue class — the input Phase 8.3 is marked *needs an ME* for.

    **The category is not computed and never will be.** Choosing between, say,
    EC3 detail 80 and detail 71 for a transverse butt weld turns on whether the
    weld is ground flush, whether it is welded from both sides, whether the
    backing strip stays in, the misalignment tolerance actually achieved on the
    shop floor, and the inspection regime that will be applied — none of which
    is in a CAD model, and all of which decide the answer by a factor of two in
    stress and eight in life. So `detail_category_mpa` is an argument with a
    source, and this class does only the arithmetic the standard specifies once
    the category is known.

    What the code does supply is the *segment geometry* of the EC3 curve, which
    is fixed arithmetic: m = 3 to 5·10⁶ cycles, m = 5 beyond, the
    constant-amplitude limit ΔσD and the cut-off ΔσL following from continuity.
    """

    detail_category_mpa: float
    source: str
    #: What the category was chosen for, in words — "transverse butt weld, ground
    #: flush, weld toe, load transverse to the weld". Printed in the result.
    description: str = ""
    standard: str = "EN 1993-1-9"

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", require_source(self.source, "A weld detail category"))
        if not (self.detail_category_mpa > 0.0 and math.isfinite(self.detail_category_mpa)):
            raise ValueError(
                "A detail category is the stress range Δσc in MPa at 2×10⁶ cycles — "
                "36, 40, 45 … 160 in EN 1993-1-9 — and must be positive."
            )

    @property
    def constant_amplitude_limit_mpa(self) -> float:
        """ΔσD, the constant-amplitude fatigue limit at 5×10⁶ cycles, in stress *range*.

        From continuity of the m = 3 branch through (2×10⁶, Δσc):
        ΔσD = Δσc · (2/5)^(1/3) ≈ 0.737 · Δσc.
        """
        ratio = EC3_REFERENCE_CYCLES / EC3_KNEE_CYCLES
        return self.detail_category_mpa * ratio ** (1.0 / EC3_SLOPE_BELOW_REFERENCE)

    @property
    def cutoff_limit_mpa(self) -> float:
        """ΔσL, the cut-off at 10⁸ cycles, in stress *range*.

        From continuity of the m = 5 branch through (5×10⁶, ΔσD):
        ΔσL = ΔσD · (5×10⁶/10⁸)^(1/5) ≈ 0.549 · ΔσD.

        Reported for the reviewer; **not applied** by `sn_curve()`. See its
        docstring for why continuing the m = 5 line below the cut-off is the
        conservative choice and how to override it.
        """
        ratio = EC3_KNEE_CYCLES / EC3_CUTOFF_CYCLES
        return self.constant_amplitude_limit_mpa * ratio ** (1.0 / EC3_SLOPE_ABOVE_KNEE)

    def sn_curve(self, *, failure_probability: float = EC3_FAILURE_PROBABILITY) -> SNCurve:
        """The EC3 design curve for this detail, converted to stress amplitude.

        Three things a reviewer must know, all of them consequences of the
        standard rather than of this code:

        * **Range to amplitude.** EC3 is written in Δσ; this codebase's
          collectives are in σa = Δσ/2. The halving happens here, once.
        * **The cut-off is not applied.** EC3 says cycles below ΔσL do no damage.
          This curve continues the m = 5 line indefinitely instead, which
          predicts damage where EC3 predicts none — conservative, and it removes
          a cliff edge from an optimiser's search space. An engineer who wants
          the standard's cut-off exactly should truncate the collective at
          `cutoff_limit_mpa` before assessing, and say so.
        * **Mean stress is already in the category.** EC3 detail curves are for
          as-welded joints, where the residual stress field is at yield and the
          applied mean adds nothing. That is why `mean_stress_sensitivity` is
          left `None` here and the assessment must be run with
          `MeanStressPolicy.DECLARED_IRRELEVANT` and that justification written
          down — rather than with a mean-stress correction of zero, which would
          look identical and mean something different.

        `failure_probability` defaults to 0.05 because EN 1993-1-9 §7.1 (NOTE 1
        after Figure 7.2) says Δσc was calculated "for a 75% confidence level of
        95% probability of survival for log N" — a 5% failure probability, read
        to that level of confidence. It is not a median curve: passing 0.5 would claim one and
        understate the design margin by roughly a factor of two in life. The 75%
        confidence level has no field on `SNCurve` and is carried only in words,
        in the curve's source. Until 2026-09-14 this default was 0.025, the
        mean-minus-two-standard-deviations reading, which the standard's text
        does not say.
        """
        return SNCurve(
            slope_k1=EC3_SLOPE_BELOW_REFERENCE,
            slope_k2=EC3_SLOPE_ABOVE_KNEE,
            knee_cycles=EC3_KNEE_CYCLES,
            knee_amplitude_mpa=self.constant_amplitude_limit_mpa / 2.0,
            failure_probability=failure_probability,
            source=(
                f"{self.standard} detail category {self.detail_category_mpa:g} MPa"
                + (f" ({self.description})" if self.description else "")
                + f"; category from: {self.source}"
                + "; Δσc is the 75% confidence level of 95% survival for log N (§7.1, NOTE 1)"
            ),
            standard=self.standard,
        )


@dataclass(frozen=True)
class ShearDetail:
    """A detail's shear category Δτ_C, and the single-slope curve EN 1993-1-9 draws for it.

    The sibling of `WeldDetail` for shear stress ranges (Figure 7.2): m = 5 all the way to the
    cut-off at 10⁸ cycles, with no constant-amplitude knee. Like `WeldDetail`, the category is an
    argument with a source and any positive value is accepted, because the National Annex may give
    categories for details the tables do not cover (§7.1(5) NOTE). `weld_catalogue.py` is where a
    category is checked against the two Figure 7.2 draws.
    """

    detail_category_mpa: float
    source: str
    description: str = ""
    standard: str = "EN 1993-1-9"

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", require_source(self.source, "A shear detail category"))
        if not (self.detail_category_mpa > 0.0 and math.isfinite(self.detail_category_mpa)):
            raise ValueError(
                "A shear detail category is the shear stress range Δτc in MPa at 2×10⁶ cycles — "
                "100 or 80 in EN 1993-1-9 Figure 7.2 — and must be positive."
            )

    @property
    def cutoff_limit_mpa(self) -> float:
        """Δτ_L = (2·10⁶/10⁸)^(1/5)·Δτ_C ≈ 0.457·Δτ_C, in shear stress *range* (§7.1(2)).

        Reported, and **not applied** by `sn_curve()`, for the reason `WeldDetail.cutoff_limit_mpa`
        gives.
        """
        ratio = EC3_REFERENCE_CYCLES / EC3_CUTOFF_CYCLES
        return self.detail_category_mpa * ratio ** (1.0 / EC3_SHEAR_SLOPE)

    def sn_curve(self, *, failure_probability: float = EC3_FAILURE_PROBABILITY) -> SNCurve:
        """The shear curve in stress amplitude: slope 5 through (2·10⁶, Δτ_C), continued past 10⁸.

        The curve is anchored at the cut-off (10⁸ cycles, Δτ_L/2) with k1 = k2 = 5, which is the
        same line as anchoring it at Δτ_C. Continuing it below Δτ_L predicts damage the standard
        does not, as `WeldDetail.sn_curve` does.

        **The failure probability is a reading.** §7.1 NOTE 1 is written of Δσc, and says nothing
        of Δτc. It is applied here to shear as well, because the shear categories are given in the
        same tables and nothing in the standard says they were derived differently. The source says
        so.
        """
        return SNCurve(
            slope_k1=EC3_SHEAR_SLOPE,
            slope_k2=EC3_SHEAR_SLOPE,
            knee_cycles=EC3_CUTOFF_CYCLES,
            knee_amplitude_mpa=self.cutoff_limit_mpa / 2.0,
            failure_probability=failure_probability,
            source=(
                f"{self.standard} shear detail category {self.detail_category_mpa:g} MPa"
                + (f" ({self.description})" if self.description else "")
                + f"; category from: {self.source}"
                + "; m = 5 to 10⁸ cycles, cut-off not applied (§7.1(2), Figure 7.2)"
                + "; §7.1 NOTE 1's 95% survival, written of Δσc, read as applying to Δτc"
            ),
            standard=self.standard,
        )


__all__ = [
    "EC3_CUTOFF_CYCLES",
    "EC3_FAILURE_PROBABILITY",
    "EC3_KNEE_CYCLES",
    "EC3_REFERENCE_CYCLES",
    "EC3_SHEAR_SLOPE",
    "EC3_SLOPE_ABOVE_KNEE",
    "EC3_SLOPE_BELOW_REFERENCE",
    "SNCurve",
    "ShearDetail",
    "WeldDetail",
]
