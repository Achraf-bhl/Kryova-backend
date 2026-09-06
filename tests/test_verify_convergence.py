"""Grid convergence (master plan 7.2), verified against closed forms.

Two kinds of evidence here, and both are needed.

**Synthetic power-law sequences**, where the exact answer is known by
construction. If the discretisation error is a pure power law
`f(h) = F + C·h^p`, then every output of this module is predictable in closed
form: the observed order must come back as exactly `p`, the Richardson
extrapolation must come back as exactly `F`, and — the identity the whole
acceptance criterion rests on — the fine-grid GCI must equal
`SAFETY_FACTOR × (the fine grid's true relative error)`. That last one is what
lets the threshold be pinned in engineering terms instead of against the
constant: at a safety factor of 1.25 a 5% GCI band *means* "the finest grid is
within 4% of the converged answer", and the tests below say 4%, not
`DEFAULT_GCI_THRESHOLD ± something`.

**A real solve**, on the one case where the finite-element answer is exact at
every refinement: a bar in uniform tension. tet4 shape functions represent a
linear displacement field exactly, so the study should find the three grids
agreeing to round-off and say so — and it must *not* mistake round-off for a
converging error sequence.

The derivation of the GCI identity, for a constant refinement ratio `r`:

    e21 = f2 - f1 = C·h^p·(r^p - 1)
    GCI = Fs·|e21/f1|/(r^p - 1) = Fs·C·h^p/|f1| = Fs·(f1 - F)/|f1|

so the `(r^p - 1)` cancels and the GCI is the fine grid's own relative error
scaled by the safety factor, whatever `r` and `p` were.
"""

import math

import numpy as np
import pytest

from app.mesh.primitives import box_mesh
from app.mesh.types import MeshError, TetMesh
from app.solve.linear_static import LinearStaticSolver
from app.solve.materials import MATERIALS
from app.solve.types import (
    FaceSelector,
    Fixture,
    ForceLoad,
    LoadCase,
    SolverError,
)
from app.verify.convergence import (
    DEFAULT_GCI_THRESHOLD,
    MAXIMUM_CREDIBLE_ORDER,
    MINIMUM_REFINEMENT_RATIO,
    NEGLIGIBLE_CHANGE,
    RECOMMENDED_REFINEMENT_RATIO,
    SAFETY_FACTOR,
    ConvergenceStudy,
    GridLevel,
    UnconvergedError,
    Verdict,
    assess,
    observed_order,
    run_study,
)
from app.verify.quantities import displacement_component_at

STEEL = MATERIALS["steel-1018"]

#: A fixed notional volume for synthetic levels. Only the ratio of grid sizes
#: matters, and `element_count = V/h^3` makes `representative_size_mm` come out
#: as exactly `h` — so a test that says "these grids are 1, 2 and 4 mm" is
#: describing what the arithmetic actually sees.
_VOLUME_MM3 = 1000.0


def level(h: float, value: float, *, element_size_mm: float | None = None) -> GridLevel:
    """A synthetic grid level whose representative size is exactly `h`."""
    return GridLevel(
        element_size_mm=h if element_size_mm is None else element_size_mm,
        node_count=10,
        element_count=_VOLUME_MM3 / h**3,
        element_type="tet4",
        volume_mm3=_VOLUME_MM3,
        value=value,
    )


def power_law_levels(
    exact: float, order: float, fine_relative_error: float, *, r: float = 2.0, h: float = 1.0
) -> list[GridLevel]:
    """Three grids obeying `f(h) = exact + C·h^order` exactly.

    `fine_relative_error` is `(f1 - exact)/f1` — the finest grid's error as a
    fraction of the finest grid's *own* value, which is the quantity the GCI is
    a band on. Stating the case that way is what makes the acceptance criterion
    readable as engineering ("the finest grid is within 3% of the answer")
    rather than as arithmetic about a constant.
    """
    f1 = exact / (1.0 - fine_relative_error)
    c = (f1 - exact) / h**order
    return [level(hi, exact + c * hi**order) for hi in (h, r * h, r * r * h)]


# --------------------------------------------------------------------------
# The estimator against a known answer
# --------------------------------------------------------------------------


class TestAgainstAKnownAnswer:
    """A power-law sequence has an exact order, an exact limit and an exact GCI."""

    @pytest.mark.parametrize("order", [1.0, 1.5, 2.0, 3.0])
    @pytest.mark.parametrize("r", [1.5, 2.0, 3.0])
    def test_observed_order_recovers_the_order_it_was_built_with(
        self, order: float, r: float
    ) -> None:
        levels = power_law_levels(7.5, order, 0.01, r=r)
        study = assess("q", "mm", levels)
        assert study.observed_order is not None
        assert study.observed_order == pytest.approx(order, rel=1e-9)

    @pytest.mark.parametrize("order", [1.0, 2.0, 3.0])
    def test_richardson_extrapolates_to_the_exact_value(self, order: float) -> None:
        """The limit is 7.5 by construction; the three grids never take that value."""
        levels = power_law_levels(7.5, order, 0.02)
        study = assess("q", "mm", levels)
        assert study.extrapolated_value is not None
        assert study.extrapolated_value == pytest.approx(7.5, rel=1e-9)
        assert all(abs(lvl.value - 7.5) > 1e-3 for lvl in study.levels)

    def test_observed_order_is_solved_not_assumed_on_unequal_ratios(self) -> None:
        """Celik's `q(p)` earns its place only when the ratios differ.

        With `r21 != r32` the textbook `log(e32/e21)/log(r)` formula is wrong;
        the transcendental solve is what makes the answer right. Building the
        levels at 1, 1.7 and 4.42 mm gives r21=1.7, r32=2.6, and the order must
        still come back as the 2.0 it was built with.
        """
        exact, order = 3.0, 2.0
        c = 0.05
        levels = [level(h, exact + c * h**order) for h in (1.0, 1.7, 4.42)]
        study = assess("q", "mm", levels)
        assert study.refinement_ratios == pytest.approx((1.7, 2.6), rel=1e-12)
        assert study.observed_order == pytest.approx(order, rel=1e-9)
        assert study.extrapolated_value == pytest.approx(exact, rel=1e-9)

        naive = math.log((exact + c * 4.42**order) - (exact + c * 1.7**order)) - math.log(
            (exact + c * 1.7**order) - (exact + c * 1.0**order)
        )
        naive_order = naive / math.log(1.7)
        assert abs(naive_order - order) > 0.1, "unequal ratios must make the naive formula wrong"

    def test_gci_is_the_safety_factor_times_the_fine_grid_error(self) -> None:
        """`GCI = Fs × (true relative error of the finest grid)`, exactly.

        This identity is what the acceptance threshold *means*, and pinning it
        is what stops every other test in this file being a restatement of
        whatever the constants happen to be.
        """
        for fine_error in (0.001, 0.01, 0.03, 0.10):
            for order in (1.0, 2.0):
                for r in (1.5, 2.0):
                    study = assess(
                        "q", "mm", power_law_levels(1.0, order, fine_error, r=r), gci_threshold=0.5
                    )
                    assert study.gci_fine is not None
                    assert study.gci_fine == pytest.approx(SAFETY_FACTOR * fine_error, rel=1e-9)


class TestTheAcceptanceCriterionFromBothSides:
    """What "converged" means, stated as engineering rather than as a constant.

    A 5% GCI band with a safety factor of 1.25 is the claim *the finest grid is
    within 4% of the converged answer*. So the boundary is pinned at 4% true
    fine-grid error, from both sides, with the threshold written out as the
    literal 0.05 it is — not read back out of the module, which would make the
    test follow the constant wherever anybody moved it.
    """

    def test_a_grid_within_four_percent_of_the_answer_converges(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.039), gci_threshold=0.05)
        assert study.verdict is Verdict.CONVERGED
        assert study.gci_fine is not None and study.gci_fine < 0.05
        assert study.stated_value is not None

    def test_a_grid_outside_four_percent_does_not_converge(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.041), gci_threshold=0.05)
        assert study.verdict is Verdict.NOT_CONVERGED
        assert study.gci_fine is not None and study.gci_fine > 0.05
        assert study.stated_value is None

    def test_the_two_cases_differ_only_in_the_fine_grid_error(self) -> None:
        """Both sides are the same physics; only the accuracy moved."""
        good = assess("q", "mm", power_law_levels(1.0, 2.0, 0.039), gci_threshold=0.05)
        bad = assess("q", "mm", power_law_levels(1.0, 2.0, 0.041), gci_threshold=0.05)
        assert good.observed_order == pytest.approx(2.0, rel=1e-9)
        assert bad.observed_order == pytest.approx(2.0, rel=1e-9)
        assert good.extrapolated_value == pytest.approx(1.0, rel=1e-9)
        assert bad.extrapolated_value == pytest.approx(1.0, rel=1e-9)

    def test_the_documented_defaults_are_the_ones_being_described(self) -> None:
        """The constants the paragraph above quotes, asserted once and here only.

        Separated from the behaviour tests deliberately: if someone changes the
        safety factor or the default band, exactly this test fails and the
        engineering statement above has to be rewritten, rather than every
        threshold test silently sliding to the new value.
        """
        assert SAFETY_FACTOR == 1.25
        assert DEFAULT_GCI_THRESHOLD == 0.05
        assert MINIMUM_REFINEMENT_RATIO == 1.1
        assert RECOMMENDED_REFINEMENT_RATIO == 1.3
        assert MAXIMUM_CREDIBLE_ORDER == 6.0

    def test_a_tighter_threshold_refuses_a_grid_the_default_accepts(self) -> None:
        levels = power_law_levels(1.0, 2.0, 0.02)  # GCI = 2.5%
        assert assess("q", "mm", levels, gci_threshold=0.05).verdict is Verdict.CONVERGED
        assert assess("q", "mm", levels, gci_threshold=0.01).verdict is Verdict.NOT_CONVERGED

    def test_the_threshold_that_was_applied_is_recorded(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.02), gci_threshold=0.01)
        assert study.gci_threshold == 0.01
        assert study.to_dict()["gci_threshold"] == 0.01

    @pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5])
    def test_a_threshold_outside_zero_to_one_is_refused(self, bad: float) -> None:
        with pytest.raises(ValueError, match="fraction of the fine-grid value"):
            assess("q", "mm", power_law_levels(1.0, 2.0, 0.02), gci_threshold=bad)


# --------------------------------------------------------------------------
# The refusals — the reason the phase exists
# --------------------------------------------------------------------------


class TestAnUnconvergedNumberCannotBeStated:
    """7.2's whole point: no verdict but CONVERGED yields a number."""

    def test_stated_value_is_none_when_not_converged(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.20))
        assert study.verdict is Verdict.NOT_CONVERGED
        assert study.stated_value is None

    def test_stated_value_is_none_when_indeterminate(self) -> None:
        study = assess("q", "mm", [level(1.0, 1.0), level(2.0, 1.1)])
        assert study.verdict is Verdict.INDETERMINATE
        assert study.stated_value is None

    def test_the_fine_grid_answer_is_still_visible_as_a_diagnostic(self) -> None:
        """`fine_value` exists so a diagnostic can show what the model said.

        It must never be confused with a result — which is why the two are
        separate properties and only one of them is `None`.
        """
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.20))
        assert study.fine_value is not None
        assert study.stated_value is None

    def test_value_or_refuse_raises_with_what_to_do_next(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.20))
        with pytest.raises(UnconvergedError) as caught:
            study.value_or_refuse()
        message = str(caught.value)
        assert "No value may be stated" in message
        assert "element_size_mm <=" in message

    def test_value_or_refuse_returns_the_number_when_converged(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.001))
        assert study.value_or_refuse() == study.levels[0].value

    def test_the_next_grid_size_is_finer_than_the_finest_tried(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.20))
        nxt = study.next_element_size_mm()
        assert nxt is not None
        assert nxt < study.levels[0].element_size_mm
        assert nxt == pytest.approx(
            study.levels[0].element_size_mm / RECOMMENDED_REFINEMENT_RATIO
        )

    def test_a_converged_study_asks_for_no_further_grid(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.001))
        assert study.next_element_size_mm() is None

    def test_the_report_of_a_refusal_says_so_in_words(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.20))
        report = study.report()
        assert "not converged" in report
        assert "No value may be stated." in report
        assert "h=" in report

    def test_the_report_of_a_pass_carries_the_band_and_the_order(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.01))
        report = study.report()
        assert "converged" in report
        assert "(GCI)" in report
        assert "observed order 2.00" in report


class TestDivergenceIsNotConvergence:
    """A sequence that gets *worse* under refinement must be refused.

    This is the defect this file was written to find. `observed_order` used to
    take the absolute value of the whole numerator — Celik writes it that way,
    because his equation is posed for a sequence already known to be converging
    — and on a diverging sequence that mirrored a negative order back onto a
    plausible positive one. The measured consequence: the sequence below, whose
    steps *grow* as the mesh refines, came back `CONVERGED` at "observed order
    1.00, ±0.03% (GCI)" and let its value be stated. The order is signed now.
    """

    #: coarse 100.03 -> medium 100.02 -> fine 100.00. The coarse-to-medium step
    #: is 0.01 and the medium-to-fine step is 0.02: refining doubled the change.
    DIVERGING = (100.00, 100.02, 100.03)

    def test_a_diverging_sequence_is_refused(self) -> None:
        f1, f2, f3 = self.DIVERGING
        study = assess("q", "mm", [level(1.0, f1), level(2.0, f2), level(4.0, f3)])
        assert study.verdict is Verdict.NOT_CONVERGED
        assert study.stated_value is None

    def test_the_order_of_a_diverging_sequence_is_negative(self) -> None:
        f1, f2, f3 = self.DIVERGING
        study = assess("q", "mm", [level(1.0, f1), level(2.0, f2), level(4.0, f3)])
        assert study.observed_order is not None
        assert study.observed_order < 0.0
        assert "not positive" in study.reason

    def test_no_extrapolation_is_offered_for_a_diverging_sequence(self) -> None:
        """Richardson on a diverging sequence is a number with no meaning."""
        f1, f2, f3 = self.DIVERGING
        study = assess("q", "mm", [level(1.0, f1), level(2.0, f2), level(4.0, f3)])
        assert study.extrapolated_value is None
        assert study.gci_fine is None

    def test_observed_order_is_signed(self) -> None:
        """Directly, on the estimator: a shrinking error is +, a growing one -.

        `e32` is the coarse-to-medium change and `e21` the medium-to-fine one,
        so `|e32| > |e21|` is convergence and the reverse is divergence, and the
        two must not produce the same number.
        """
        converging = observed_order(e21=0.25, e32=1.0, r21=2.0, r32=2.0)
        diverging = observed_order(e21=1.0, e32=0.25, r21=2.0, r32=2.0)
        assert converging == pytest.approx(2.0, rel=1e-12)
        assert diverging == pytest.approx(-2.0, rel=1e-12)

    def test_a_stalled_sequence_is_refused(self) -> None:
        """Equal steps at every refinement: the error is not shrinking at all."""
        study = assess("q", "mm", [level(1.0, 1.0), level(2.0, 1.1), level(4.0, 1.2)])
        assert study.verdict is Verdict.NOT_CONVERGED
        assert study.stated_value is None
        assert study.gci_fine is None

    def test_a_strongly_diverging_sequence_is_not_reported_as_a_high_order(self) -> None:
        """It must be refused as divergence, not as "the order is implausible"."""
        study = assess("q", "mm", [level(1.0, 100.0), level(2.0, 100.001), level(4.0, 100.00101)])
        assert study.verdict is Verdict.NOT_CONVERGED
        assert study.observed_order is not None and study.observed_order < 0.0
        assert "not positive" in study.reason


class TestTheOtherRefusals:
    """Each branch of `assess` that declines to answer, and why."""

    def test_two_grids_cannot_give_an_order(self) -> None:
        study = assess("q", "mm", [level(1.0, 1.0), level(2.0, 1.1)])
        assert study.verdict is Verdict.INDETERMINATE
        assert "three are needed" in study.reason

    def test_no_grids_at_all(self) -> None:
        study = assess("q", "mm", [])
        assert study.verdict is Verdict.INDETERMINATE
        assert study.fine_value is None
        assert study.next_element_size_mm() is None
        assert "Grids: none" in study.report()

    def test_grids_too_close_together(self) -> None:
        """Below a 1.1 ratio the difference is mesh noise, not discretisation."""
        levels = [level(1.0, 1.0), level(1.05, 1.01), level(1.1025, 1.02)]
        study = assess("q", "mm", levels)
        assert study.verdict is Verdict.INDETERMINATE
        assert "too close together" in study.reason
        assert study.refinement_ratios == pytest.approx((1.05, 1.05))

    def test_a_ratio_between_the_minimum_and_the_recommended_is_a_caution(self) -> None:
        """1.1 ≤ r < 1.3 still reports, with the reliability caveat attached."""
        levels = power_law_levels(1.0, 2.0, 0.01, r=1.2)
        study = assess("q", "mm", levels)
        assert study.verdict is Verdict.CONVERGED
        assert any("below the recommended" in c for c in study.cautions)

    def test_every_grid_returning_zero(self) -> None:
        study = assess("q", "mm", [level(1.0, 0.0), level(2.0, 0.0), level(4.0, 0.0)])
        assert study.verdict is Verdict.INDETERMINATE
        assert "exactly zero" in study.reason

    def test_the_two_finest_agreeing_while_the_coarsest_differs(self) -> None:
        study = assess("q", "mm", [level(1.0, 1.0), level(2.0, 1.0), level(4.0, 1.5)])
        assert study.verdict is Verdict.INDETERMINATE
        assert "same value" in study.reason

    def test_oscillation_is_refused_before_any_order_is_fitted(self) -> None:
        study = assess("q", "mm", [level(1.0, 1.0), level(2.0, 1.2), level(4.0, 1.05)])
        assert study.verdict is Verdict.NOT_CONVERGED
        assert "oscillates" in study.reason
        assert study.observed_order is None
        assert study.extrapolated_value is None

    def test_an_implausible_order_is_refused_rather_than_believed(self) -> None:
        """`r^p - 1` grows fast, so a huge p drives the GCI to false confidence."""
        study = assess("q", "mm", [level(1.0, 1.0), level(2.0, 1.01), level(4.0, 2.01)])
        assert study.verdict is Verdict.NOT_CONVERGED
        assert study.observed_order is not None
        assert study.observed_order > MAXIMUM_CREDIBLE_ORDER
        assert study.gci_fine is None, "no band may be quoted from an incredible order"

    def test_a_level_with_no_elements_has_no_representative_size(self) -> None:
        empty = GridLevel(
            element_size_mm=1.0,
            node_count=0,
            element_count=0,
            element_type="tet4",
            volume_mm3=0.0,
            value=1.0,
        )
        with pytest.raises(ValueError, match="did not build"):
            _ = empty.representative_size_mm

    def test_a_study_containing_an_empty_level_is_indeterminate_not_a_crash(self) -> None:
        empty = GridLevel(
            element_size_mm=1.0,
            node_count=0,
            element_count=0,
            element_type="tet4",
            volume_mm3=0.0,
            value=1.0,
        )
        study = assess("q", "mm", [empty, level(2.0, 1.0), level(4.0, 1.1)])
        assert study.verdict is Verdict.INDETERMINATE
        assert study.levels == ()
        assert study.stated_value is None


class TestGridIndependence:
    """Three grids agreeing to round-off is convergence, and claims no order."""

    def test_identical_values_converge_without_claiming_an_order(self) -> None:
        study = assess("q", "mm", [level(1.0, 2.5), level(2.0, 2.5), level(4.0, 2.5)])
        assert study.verdict is Verdict.CONVERGED
        assert study.observed_order is None
        assert study.gci_fine == 0.0
        assert study.stated_value == 2.5
        assert study.extrapolated_value == 2.5
        assert "none is claimed" in study.reason

    def test_round_off_sized_differences_still_count_as_agreement(self) -> None:
        """The tolerance has to survive real float noise from a sparse solve.

        A relative difference of 1e-14 is what a direct solve on a few thousand
        degrees of freedom actually produces on an exactly-representable answer
        (measured below in `TestARealSolve`). Without this branch such noise
        would fall through to the monotonicity test and be classified on its
        sign, which is meaningless — half the time it would read as an
        oscillation and refuse an exact answer.
        """
        study = assess("q", "mm", [level(1.0, 2.5), level(2.0, 2.5 + 3e-14), level(4.0, 2.5 - 1e-14)])
        assert study.verdict is Verdict.CONVERGED

    def test_differences_above_the_negligible_band_are_assessed_properly(self) -> None:
        """Pinned from the other side, so the band is a real boundary."""
        big = 2.5 * NEGLIGIBLE_CHANGE * 100.0
        study = assess("q", "mm", [level(1.0, 2.5), level(2.0, 2.5 + big), level(4.0, 2.5 - big)])
        assert study.verdict is not Verdict.CONVERGED

    def test_both_differences_must_be_negligible_not_just_one(self) -> None:
        study = assess("q", "mm", [level(1.0, 2.5), level(2.0, 2.5), level(4.0, 3.0)])
        assert study.verdict is Verdict.INDETERMINATE


class TestCautionsNeverChangeTheVerdict:
    def test_a_formal_order_far_from_the_observed_one_only_cautions(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.01), formal_order=4.0)
        assert study.verdict is Verdict.CONVERGED
        assert any("formal order" in c for c in study.cautions)

    def test_a_matching_formal_order_raises_no_caution(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.01), formal_order=2.0)
        assert not any("formal order" in c for c in study.cautions)

    def test_an_out_of_band_asymptotic_indicator_only_cautions(self) -> None:
        """A large fine-grid error puts f1/f2 well away from 1 without failing."""
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.09), gci_threshold=0.5)
        assert study.verdict is Verdict.CONVERGED
        assert study.asymptotic_ratio is not None
        assert any("asymptotic-range indicator" in c for c in study.cautions)

    def test_a_well_resolved_study_is_in_the_asymptotic_range(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.005))
        assert study.asymptotic_ratio == pytest.approx(1.0, abs=0.15)
        assert study.cautions == ()


class TestSorting:
    def test_levels_are_sorted_finest_first_whatever_the_caller_gave(self) -> None:
        levels = power_law_levels(1.0, 2.0, 0.01)
        forward = assess("q", "mm", levels)
        backward = assess("q", "mm", list(reversed(levels)))
        assert [lvl.value for lvl in forward.levels] == [lvl.value for lvl in backward.levels]
        assert forward.verdict is backward.verdict
        sizes = [lvl.representative_size_mm for lvl in forward.levels]
        assert sizes == sorted(sizes)

    def test_the_representative_size_is_measured_not_requested(self) -> None:
        """`h = (V/N)^(1/3)`, from the mesh that was built, not the size asked for.

        A mesher asked for 4 mm and producing 3.7 mm is ordinary; a ratio taken
        from the request would be wrong by exactly that much. Here the request
        is a lie in each level and the maths must ignore it.
        """
        levels = [
            level(1.0, 1.01, element_size_mm=99.0),
            level(2.0, 1.04, element_size_mm=98.0),
            level(4.0, 1.16, element_size_mm=97.0),
        ]
        study = assess("q", "mm", levels)
        assert study.refinement_ratios == pytest.approx((2.0, 2.0))
        assert study.levels[0].representative_size_mm == pytest.approx(1.0)


class TestTheRecord:
    def test_to_dict_carries_everything_a_reviewer_needs_to_redo_it(self) -> None:
        study = assess("q", "mm", power_law_levels(1.0, 2.0, 0.01))
        payload = study.to_dict()
        for key in (
            "quantity",
            "unit",
            "verdict",
            "reason",
            "gci_threshold",
            "gci_fine",
            "observed_order",
            "extrapolated_value",
            "asymptotic_ratio",
            "refinement_ratios",
            "safety_factor",
            "stated_value",
            "fine_value",
            "levels",
        ):
            assert key in payload
        assert payload["safety_factor"] == SAFETY_FACTOR
        assert len(payload["levels"]) == 3
        assert payload["levels"][0]["representative_size_mm"] == pytest.approx(1.0)

    def test_an_unconverged_record_carries_no_stated_value(self) -> None:
        payload = assess("q", "mm", power_law_levels(1.0, 2.0, 0.5)).to_dict()
        assert payload["stated_value"] is None
        assert payload["fine_value"] is not None

    def test_the_verdict_serialises_as_a_word(self) -> None:
        payload = assess("q", "mm", power_law_levels(1.0, 2.0, 0.01)).to_dict()
        assert payload["verdict"] == "converged"


# --------------------------------------------------------------------------
# run_study: failures are recorded, bugs are not swallowed
# --------------------------------------------------------------------------


def _stub_mesh() -> TetMesh:
    return box_mesh((10.0, 10.0, 10.0), divisions=(1, 1, 1))


class TestRunStudy:
    def test_a_sampler_that_fails_to_mesh_is_recorded_not_raised(self) -> None:
        def sample(size: float) -> tuple[TetMesh, float]:
            if size > 3.0:
                raise MeshError("element_size_mm is coarser than the smallest feature")
            return _stub_mesh(), 1.0

        study = run_study("q", "mm", [4.0, 2.0, 1.0, 0.5], sample)
        assert len(study.failures) == 1
        assert "coarser than the smallest feature" in study.failures[0]
        assert study.to_dict()["failures"] == list(study.failures)

    def test_a_solver_failure_is_recorded_too(self) -> None:
        def sample(size: float) -> tuple[TetMesh, float]:
            raise SolverError("the fixtures leave a rigid-body motion")

        study = run_study("q", "mm", [4.0, 2.0, 1.0], sample)
        assert study.verdict is Verdict.INDETERMINATE
        assert len(study.failures) == 3
        assert study.stated_value is None

    def test_three_surviving_levels_are_enough_to_assess(self) -> None:
        values = {4.0: 1.16, 2.0: 1.04, 1.0: 1.01}

        def sample(size: float) -> tuple[TetMesh, float]:
            if size == 8.0:
                raise MeshError("too coarse")
            n = int(round(10.0 / size))
            return box_mesh((10.0, 10.0, 10.0), divisions=(n, n, n)), values[size]

        study = run_study("q", "mm", [8.0, 4.0, 2.0, 1.0], sample)
        assert len(study.levels) == 3
        assert study.failures
        assert study.verdict in (Verdict.CONVERGED, Verdict.NOT_CONVERGED)

    def test_a_bug_in_the_sampler_propagates(self) -> None:
        """A `TypeError` is the caller's bug and must not read as non-convergence."""

        def sample(size: float) -> tuple[TetMesh, float]:
            return None.missing  # type: ignore[attr-defined,return-value]

        with pytest.raises(AttributeError):
            run_study("q", "mm", [4.0, 2.0, 1.0], sample)

    def test_the_level_describes_the_mesh_the_sampler_actually_built(self) -> None:
        """Not the size that was asked for — that is the whole reason the sampler
        hands the mesh back rather than a node count."""

        def sample(size: float) -> tuple[TetMesh, float]:
            mesh = box_mesh((10.0, 10.0, 10.0), divisions=(2, 2, 2))
            return mesh, 1.0

        study = run_study("q", "mm", [4.0, 2.0, 1.0], sample)
        mesh = box_mesh((10.0, 10.0, 10.0), divisions=(2, 2, 2))
        for lvl in study.levels:
            assert lvl.element_count == mesh.tet_count
            assert lvl.volume_mm3 == pytest.approx(1000.0)
            assert lvl.element_type == "tet4"


# --------------------------------------------------------------------------
# A real solve, where the finite-element answer is exact
# --------------------------------------------------------------------------


def uniaxial_case(force_n: float) -> LoadCase:
    """A bar on three roller planes, pulled along +z — `tests/test_solver.py`'s case.

    Rollers rather than a clamp so the stress stays uniform and the closed form
    δ = FL/AE holds exactly; an encastre face would block the Poisson
    contraction and put a concentration next to it.
    """
    return LoadCase(
        name="uniaxial",
        material=STEEL,
        fixtures=[
            Fixture(where=FaceSelector(axis="z", side="min"), dofs=["z"]),
            Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x"]),
            Fixture(where=FaceSelector(axis="y", side="min"), dofs=["y"]),
        ],
        loads=[ForceLoad(where=FaceSelector(axis="z", side="max"), force_n=(0.0, 0.0, force_n))],
    )


class TestARealSolve:
    """A uniform-stress bar: the FE answer is exact at every refinement.

    tet4 shape functions are linear, and the displacement field of a bar in
    uniform tension is linear, so the discretisation error is zero at any
    element size. The study must report that as grid independence — and the
    number it permits must be the closed-form δ = FL/AE, not merely the finest
    grid's own answer.
    """

    WIDTH = 10.0
    DEPTH = 10.0
    LENGTH = 50.0
    FORCE_N = 20_000.0

    @property
    def closed_form_extension_mm(self) -> float:
        area = self.WIDTH * self.DEPTH
        return self.FORCE_N * self.LENGTH / (area * STEEL.youngs_modulus_mpa)

    def _study(self) -> ConvergenceStudy:
        quantity = displacement_component_at((self.WIDTH, self.DEPTH, self.LENGTH), "z")
        solver = LinearStaticSolver()

        def sample(size: float) -> tuple[TetMesh, float]:
            n = int(round(self.LENGTH / size / 3.0))
            mesh = box_mesh((self.WIDTH, self.DEPTH, self.LENGTH), divisions=(n, n, n * 3))
            return mesh, quantity.read(mesh, solver.solve(mesh, uniaxial_case(self.FORCE_N)))

        sizes = [self.LENGTH / 3.0, self.LENGTH / 6.0, self.LENGTH / 12.0]
        return run_study(quantity.name, quantity.unit, sizes, sample)

    def test_the_exact_case_converges_and_states_the_closed_form(self) -> None:
        study = self._study()
        assert study.failures == ()
        assert study.verdict is Verdict.CONVERGED
        assert study.stated_value is not None
        assert study.stated_value == pytest.approx(self.closed_form_extension_mm, rel=1e-9)

    def test_it_is_grid_independence_rather_than_a_fitted_order(self) -> None:
        """No order may be claimed where there is no error to fit."""
        study = self._study()
        assert study.observed_order is None
        assert study.gci_fine == 0.0

    def test_the_grids_really_are_three_different_meshes(self) -> None:
        study = self._study()
        counts = [lvl.element_count for lvl in study.levels]
        assert len(set(counts)) == 3
        assert counts == sorted(counts, reverse=True)
        ratios = study.refinement_ratios
        assert all(r > MINIMUM_REFINEMENT_RATIO for r in ratios)

    def test_the_round_off_between_grids_is_far_below_the_negligible_band(self) -> None:
        """The measurement behind `NEGLIGIBLE_CHANGE`, so it is not a guessed constant.

        If a real direct solve produced differences anywhere near 1e-10, the
        grid-independence branch would be reached by accident on a genuinely
        moving quantity. It does not: the spread here is ~1e-14 relative.
        """
        study = self._study()
        values = np.array([lvl.value for lvl in study.levels])
        spread = float(values.max() - values.min()) / float(abs(values).max())
        assert spread < NEGLIGIBLE_CHANGE / 1000.0
        assert spread > 0.0, "three different meshes should not agree bit for bit"

    def test_the_stress_is_also_exact_and_also_grid_independent(self) -> None:
        """A second quantity, so the pass is not a property of displacement alone."""
        from app.verify.quantities import MAX_VON_MISES

        solver = LinearStaticSolver()

        def sample(size: float) -> tuple[TetMesh, float]:
            n = int(round(self.LENGTH / size / 3.0))
            mesh = box_mesh((self.WIDTH, self.DEPTH, self.LENGTH), divisions=(n, n, n * 3))
            return mesh, MAX_VON_MISES.read(mesh, solver.solve(mesh, uniaxial_case(self.FORCE_N)))

        study = run_study(
            MAX_VON_MISES.name,
            MAX_VON_MISES.unit,
            [self.LENGTH / 3.0, self.LENGTH / 6.0, self.LENGTH / 12.0],
            sample,
        )
        assert study.verdict is Verdict.CONVERGED
        assert study.stated_value == pytest.approx(
            self.FORCE_N / (self.WIDTH * self.DEPTH), rel=1e-9
        )
