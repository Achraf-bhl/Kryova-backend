"""Phase 8 verified against closed-form and published answers, never recorded output.

The same standard `tests/test_solver.py` holds the FEA to: a bar in tension reproduces
σ = F/A because that is what a bar in tension does, not because somebody ran it once
and pasted the number. Fatigue is unusually well suited to it — every step of a
constant-amplitude assessment is arithmetic with an exact answer:

* a constant-amplitude history closes an **exactly predictable** number of cycles, and
  leaves an exactly predictable residue;
* Basquin's law N = ND·(σa/SD)^−k is a power law, so N at any amplitude is arithmetic;
* Miner's rule on a two-block history is a sum of two fractions;
* the FKM-Goodman transformation is Δσ_eq = Δσ + 2·M·σm;
* Eurocode 3's fatigue curve geometry follows from continuity through three fixed
  points, so ΔσD/Δσc and ΔσL/ΔσD are closed-form ratios that can be checked against
  the published 0.737 and 0.549.

These run offline in well under a second and touch no database, no seat and no
geometry kernel.
"""

from __future__ import annotations

import math

import pytest

from app.design.assertions import Outcome
from app.fatigue import (
    Assessment,
    Collective,
    Factor,
    FactorSet,
    LoadHistory,
    SignConvention,
    SNCurve,
    StressBasis,
    WeldDetail,
    available,
    default_backend,
)
from app.fatigue.material import (
    EC3_CUTOFF_CYCLES,
    EC3_KNEE_CYCLES,
    EC3_REFERENCE_CYCLES,
)

pytestmark = pytest.mark.skipif(
    not available(), reason="pyLife is not installed; the arithmetic under test is federated to it"
)

SOURCE = "test fixture, not a real material"


def _unity_factors() -> FactorSet:
    """Factors of exactly 1.0, so the arithmetic under test is not perturbed.

    Named rather than omitted: an empty `FactorSet` would be *refused* by the
    assessment (that is `test_fatigue.py`'s subject), and these tests are about
    the numbers, not about the guard.
    """
    return FactorSet.of(
        [
            Factor("surface", 1.0, source=SOURCE),
            Factor("size", 1.0, source=SOURCE),
        ]
    )


def _curve(**overrides: object) -> SNCurve:
    """SD = 100 MPa at ND = 1e6, k1 = 5. Round numbers so every N is exact."""
    kwargs: dict[str, object] = {
        "slope_k1": 5.0,
        "knee_cycles": 1.0e6,
        "knee_amplitude_mpa": 100.0,
        "source": SOURCE,
    }
    kwargs.update(overrides)
    return SNCurve(**kwargs)  # type: ignore[arg-type]


class TestConstantAmplitudeCycleCount:
    """A constant-amplitude history has an exactly predictable count. Pin it."""

    def test_n_reversal_pairs_close_n_minus_one_cycles(self) -> None:
        # 0, then (100, -100) ten times: 21 samples, 21 turning points.
        # Three-point rainflow closes 9 cycles and leaves a 3-point residue —
        # the opening excursion never finds a partner inside one block.
        history = LoadHistory.from_values(
            "ten fully reversed cycles",
            [0.0] + [100.0, -100.0] * 10,
            basis=StressBasis.NOTCH_ROOT,
            sign=SignConvention.SIGNED,
            source=SOURCE,
        )
        collective = default_backend().count(history)

        assert len(collective.blocks) == 9
        assert collective.total_cycles == pytest.approx(9.0)
        assert collective.residue_mpa == (0.0, 100.0, -100.0)
        for block in collective.blocks:
            assert block.amplitude_mpa == pytest.approx(100.0)
            assert block.mean_mpa == pytest.approx(0.0)
            assert block.cycles == pytest.approx(1.0)

    def test_amplitude_and_mean_of_a_pulsating_history(self) -> None:
        """0 → 100 → 0 is amplitude 50 about a mean of 50, not amplitude 100."""
        history = LoadHistory.from_values(
            "pulsating",
            [0.0] + [100.0, 0.0] * 5,
            basis=StressBasis.NOTCH_ROOT,
            sign=SignConvention.SIGNED,
            source=SOURCE,
        )
        collective = default_backend().count(history)

        assert len(collective.blocks) == 4
        first = collective.blocks[0]
        assert first.amplitude_mpa == pytest.approx(50.0)
        assert first.mean_mpa == pytest.approx(50.0)
        assert first.range_mpa == pytest.approx(100.0)
        assert first.stress_ratio == pytest.approx(0.0)

    def test_a_second_block_closes_the_residue_of_the_first(self) -> None:
        """Counting two consecutive blocks gives 2n−1, not 2(n−1).

        This is the arithmetic behind the residue assumption the assessment
        records: the residue is not lost damage, it is damage that closes when
        the block repeats, and the difference between one block and two is the
        per-block figure a repeating duty cycle actually sees.
        """
        one = [0.0] + [100.0, -100.0] * 10
        two = one + [100.0, -100.0] * 10

        backend = default_backend()
        counted_once = backend.count(
            LoadHistory.from_values(
                "one", one, basis=StressBasis.NOTCH_ROOT, sign=SignConvention.SIGNED, source=SOURCE
            )
        )
        counted_twice = backend.count(
            LoadHistory.from_values(
                "two", two, basis=StressBasis.NOTCH_ROOT, sign=SignConvention.SIGNED, source=SOURCE
            )
        )

        assert len(counted_once.blocks) == 9
        assert len(counted_twice.blocks) == 19
        assert len(counted_twice.blocks) - len(counted_once.blocks) == 10


class TestBasquinLaw:
    """N = ND · (σa/SD)^−k1, checked at points where the arithmetic is exact."""

    def test_cycles_at_twice_the_endurance_amplitude(self) -> None:
        # 1e6 * 2^-5 = 1e6/32 = 31250
        assert default_backend().cycles_to_failure(_curve(), 200.0) == pytest.approx(31_250.0)

    def test_cycles_at_the_knee_is_the_knee(self) -> None:
        assert default_backend().cycles_to_failure(_curve(), 100.0) == pytest.approx(1.0e6)

    def test_below_the_knee_with_no_second_slope_is_infinite_life(self) -> None:
        assert math.isinf(default_backend().cycles_to_failure(_curve(), 50.0))

    def test_haibach_second_slope_is_two_k_minus_one(self) -> None:
        curve = _curve().with_haibach_slope(source="Haibach, chosen for this spectrum")
        assert curve.slope_k2 == pytest.approx(9.0)
        # 1e6 * (50/100)^-9 = 1e6 * 512 = 5.12e8
        assert default_backend().cycles_to_failure(curve, 50.0) == pytest.approx(5.12e8)


class TestMinerArithmetic:
    """Miner's rule on a two-block history has an arithmetic answer. This is it."""

    def test_two_block_damage_equals_the_hand_sum(self) -> None:
        collective = Collective.from_blocks(
            [(200.0, 0.0, 1_000.0), (150.0, 0.0, 2_000.0)],
            basis=StressBasis.NOTCH_ROOT,
            source="a two-block duty cycle stated by hand",
        )
        expected_first = 1_000.0 / (1.0e6 * (200.0 / 100.0) ** -5.0)
        expected_second = 2_000.0 / (1.0e6 * (150.0 / 100.0) ** -5.0)
        assert expected_first == pytest.approx(0.032)

        result = Assessment(
            loading=collective, curve=_curve(), factors=_unity_factors()
        ).run()

        assert result.outcome is Outcome.PASSED
        assert result.damage == pytest.approx(expected_first + expected_second)
        assert result.damage == pytest.approx(0.0471875)
        assert [b.damage for b in result.blocks] == pytest.approx(
            [expected_first, expected_second]
        )

    def test_damage_scales_linearly_with_the_duty_cycle(self) -> None:
        collective = Collective.from_blocks(
            [(200.0, 0.0, 1.0)], basis=StressBasis.NOTCH_ROOT, source=SOURCE
        )
        result = Assessment(
            loading=collective,
            curve=_curve(),
            factors=_unity_factors(),
            design_life_blocks=31_250.0,
        ).run()

        # Exactly one lifetime: 31250 cycles at 200 MPa against N = 31250.
        assert result.damage == pytest.approx(1.0)
        assert result.outcome is Outcome.PASSED  # D == limit is not yet a failure
        assert result.life_blocks == pytest.approx(31_250.0)

    def test_one_more_cycle_than_the_life_fails(self) -> None:
        collective = Collective.from_blocks(
            [(200.0, 0.0, 1.0)], basis=StressBasis.NOTCH_ROOT, source=SOURCE
        )
        result = Assessment(
            loading=collective,
            curve=_curve(),
            factors=_unity_factors(),
            design_life_blocks=31_251.0,
        ).run()
        assert result.outcome is Outcome.FAILED
        assert result.damage is not None and result.damage > 1.0

    def test_a_damage_limit_below_one_shortens_the_life_proportionally(self) -> None:
        collective = Collective.from_blocks(
            [(200.0, 0.0, 1.0)], basis=StressBasis.NOTCH_ROOT, source=SOURCE
        )
        result = Assessment(
            loading=collective,
            curve=_curve(),
            factors=_unity_factors(),
            design_life_blocks=31_250.0,
            damage_limit=0.5,
        ).run()
        assert result.damage == pytest.approx(1.0)
        assert result.outcome is Outcome.FAILED
        assert result.life_blocks == pytest.approx(15_625.0)

    def test_everything_below_the_knee_is_zero_damage_and_unbounded_life(self) -> None:
        collective = Collective.from_blocks(
            [(50.0, 0.0, 1.0e9)], basis=StressBasis.NOTCH_ROOT, source=SOURCE
        )
        result = Assessment(
            loading=collective, curve=_curve(), factors=_unity_factors()
        ).run()
        assert result.damage == pytest.approx(0.0)
        assert result.life_blocks is not None and math.isinf(result.life_blocks)
        # …and zero damage is a *result*, not a refusal. The distinction matters:
        # the assumption that made it zero is recorded rather than hidden.
        assert result.outcome is Outcome.PASSED
        assert any("Miner original" in a for a in result.assumptions)

    def test_the_same_spectrum_under_haibach_is_no_longer_harmless(self) -> None:
        """Breaking the assumption the previous test records, to show it is load-bearing."""
        collective = Collective.from_blocks(
            [(50.0, 0.0, 1.0e9)], basis=StressBasis.NOTCH_ROOT, source=SOURCE
        )
        curve = _curve().with_haibach_slope(source="Haibach, spectrum contains overloads")
        result = Assessment(
            loading=collective, curve=curve, factors=_unity_factors()
        ).run()
        # 1e9 / (1e6 * 512) = 1.953125
        assert result.damage == pytest.approx(1.953125)
        assert result.outcome is Outcome.FAILED


class TestStrengthFactors:
    """Factors multiply the endurance amplitude; the life follows the power law."""

    def test_a_factor_of_one_half_costs_a_factor_of_thirty_two_in_life(self) -> None:
        collective = Collective.from_blocks(
            [(200.0, 0.0, 1.0)], basis=StressBasis.NOTCH_ROOT, source=SOURCE
        )
        halved = FactorSet.of(
            [
                Factor("surface", 0.5, source=SOURCE),
                Factor("size", 1.0, source=SOURCE),
            ]
        )
        full = Assessment(loading=collective, curve=_curve(), factors=_unity_factors()).run()
        reduced = Assessment(loading=collective, curve=_curve(), factors=halved).run()

        assert full.damage is not None and reduced.damage is not None
        # SD halved, k1 = 5: N falls by 2^5 = 32, so damage rises by 32.
        assert reduced.damage / full.damage == pytest.approx(32.0)

    def test_factors_multiply_and_the_product_is_what_is_applied(self) -> None:
        collective = Collective.from_blocks(
            [(200.0, 0.0, 1.0)], basis=StressBasis.NOTCH_ROOT, source=SOURCE
        )
        two = FactorSet.of(
            [
                Factor("surface", 0.8, source=SOURCE),
                Factor("size", 0.9, source=SOURCE),
            ]
        )
        one = FactorSet.of(
            [
                Factor("surface", 0.72, source=SOURCE),
                Factor("size", 1.0, source=SOURCE),
            ]
        )
        assert two.value == pytest.approx(0.72)
        a = Assessment(loading=collective, curve=_curve(), factors=two).run()
        b = Assessment(loading=collective, curve=_curve(), factors=one).run()
        assert a.damage == pytest.approx(b.damage)


class TestStressConcentrationArithmetic:
    def test_kf_scales_amplitude_and_mean_together(self) -> None:
        collective = Collective.from_blocks(
            [(100.0, 40.0, 1.0)], basis=StressBasis.NOMINAL, source=SOURCE
        )
        scaled = collective.scaled(2.5)
        assert scaled.blocks[0].amplitude_mpa == pytest.approx(250.0)
        # The mean concentrates too. Scaling the amplitude alone would understate
        # the mean and therefore the mean-stress correction that follows.
        assert scaled.blocks[0].mean_mpa == pytest.approx(100.0)

    def test_a_notch_costs_kf_to_the_power_of_the_slope(self) -> None:
        from app.fatigue import StressConcentration

        collective = Collective.from_blocks(
            [(50.0, 0.0, 1.0)], basis=StressBasis.NOMINAL, source=SOURCE
        )
        curve = _curve().with_haibach_slope(source="so the low-amplitude case still damages")
        plain = Assessment(
            loading=Collective.from_blocks(
                [(50.0, 0.0, 1.0)], basis=StressBasis.NOTCH_ROOT, source=SOURCE
            ),
            curve=curve,
            factors=_unity_factors(),
        ).run()
        notched = Assessment(
            loading=collective,
            curve=curve,
            factors=_unity_factors(),
            concentration=StressConcentration(kt=2.0, source=SOURCE, location="fillet"),
        ).run()

        assert plain.damage is not None and notched.damage is not None
        # Kf = 2 doubles the amplitude; 100 MPa is the knee, so N goes from
        # 1e6·(50/100)^-9 = 5.12e8 to 1e6 exactly: a factor of 512 = 2^9.
        assert notched.damage / plain.damage == pytest.approx(512.0)

    def test_peterson_relation_for_notch_sensitivity(self) -> None:
        from app.fatigue import StressConcentration

        full = StressConcentration(kt=3.0, source=SOURCE)
        assert full.kf == pytest.approx(3.0)
        assert full.assumed_full_sensitivity is True

        partial = StressConcentration(kt=3.0, notch_sensitivity=0.8, source=SOURCE)
        # Kf = 1 + q(Kt − 1) = 1 + 0.8·2 = 2.6
        assert partial.kf == pytest.approx(2.6)
        assert partial.assumed_full_sensitivity is False


class TestFkmGoodmanMeanStress:
    """Δσ_eq = Δσ + 2·M·σm for R ≤ 0 — the published FKM-Goodman construction."""

    def test_the_transformation_is_the_published_formula(self) -> None:
        collective = Collective.from_blocks(
            [(300.0, 100.0, 1.0), (150.0, 50.0, 10.0)],
            basis=StressBasis.NOTCH_ROOT,
            source=SOURCE,
        )
        corrected = default_backend().correct_mean_stress(collective, m=0.5, m2=None)

        # (600 + 2·0.5·100)/2 = 350 ; (300 + 2·0.5·50)/2 = 175
        assert corrected.blocks[0].amplitude_mpa == pytest.approx(350.0)
        assert corrected.blocks[1].amplitude_mpa == pytest.approx(175.0)
        assert all(b.mean_mpa == pytest.approx(0.0) for b in corrected.blocks)
        assert [b.cycles for b in corrected.blocks] == pytest.approx([1.0, 10.0])

    def test_a_tensile_mean_shortens_the_life_through_the_assessment(self) -> None:
        curve = _curve(mean_stress_sensitivity=0.3)
        reversed_ = Collective.from_blocks(
            [(200.0, 0.0, 1.0)], basis=StressBasis.NOTCH_ROOT, source=SOURCE
        )
        tensile = Collective.from_blocks(
            [(200.0, 100.0, 1.0)], basis=StressBasis.NOTCH_ROOT, source=SOURCE
        )
        a = Assessment(loading=reversed_, curve=curve, factors=_unity_factors()).run()
        b = Assessment(loading=tensile, curve=curve, factors=_unity_factors()).run()

        assert a.damage is not None and b.damage is not None
        # Equivalent amplitude 200 + 0.3·100 = 230; damage ratio (230/200)^5.
        assert b.damage / a.damage == pytest.approx((230.0 / 200.0) ** 5.0)
        assert "FKM-Goodman" in b.method.mean_stress_correction


class TestEurocode3WeldCurve:
    """The EC3 fatigue curve geometry, against the ratios the standard publishes."""

    def test_the_constant_amplitude_limit_is_the_published_ratio(self) -> None:
        detail = WeldDetail(detail_category_mpa=90.0, source="chosen by the responsible engineer")
        # ΔσD/Δσc = (2/5)^(1/3) = 0.7368…, the standard's 0.737.
        assert detail.constant_amplitude_limit_mpa / 90.0 == pytest.approx(
            (EC3_REFERENCE_CYCLES / EC3_KNEE_CYCLES) ** (1.0 / 3.0)
        )
        assert detail.constant_amplitude_limit_mpa == pytest.approx(66.3126, abs=1e-3)

    def test_the_cutoff_is_the_published_ratio(self) -> None:
        detail = WeldDetail(detail_category_mpa=90.0, source="chosen by the responsible engineer")
        # ΔσL/ΔσD = (5e6/1e8)^(1/5) = 0.5493…
        assert detail.cutoff_limit_mpa / detail.constant_amplitude_limit_mpa == pytest.approx(
            (EC3_KNEE_CYCLES / EC3_CUTOFF_CYCLES) ** (1.0 / 5.0)
        )
        assert detail.cutoff_limit_mpa == pytest.approx(36.4242, abs=1e-3)

    def test_the_detail_category_is_the_range_at_two_million_cycles(self) -> None:
        """The defining property of a detail category, and the range↔amplitude check.

        Detail 90 means: 2×10⁶ cycles at a stress *range* of 90 MPa. Our curve is
        in amplitude, so that is 45 MPa — and if the halving were done twice, or
        not at all, this is the assertion that would catch it.
        """
        curve = WeldDetail(detail_category_mpa=90.0, source="ME").sn_curve()
        assert default_backend().cycles_to_failure(curve, 45.0) == pytest.approx(
            EC3_REFERENCE_CYCLES, rel=1e-9
        )

    def test_the_knee_sits_at_five_million_cycles(self) -> None:
        detail = WeldDetail(detail_category_mpa=90.0, source="ME")
        curve = detail.sn_curve()
        assert curve.knee_cycles == pytest.approx(EC3_KNEE_CYCLES)
        assert default_backend().cycles_to_failure(
            curve, detail.constant_amplitude_limit_mpa / 2.0
        ) == pytest.approx(EC3_KNEE_CYCLES, rel=1e-9)

    def test_the_slopes_are_three_then_five(self) -> None:
        curve = WeldDetail(detail_category_mpa=71.0, source="ME").sn_curve()
        assert curve.slope_k1 == pytest.approx(3.0)
        assert curve.slope_k2 == pytest.approx(5.0)
        # And the standard's categories are 95% survival curves, not medians.
        assert curve.failure_probability == pytest.approx(0.025)


class TestFailureProbabilityTransform:
    """A design curve from a median curve — only where the scatter is declared."""

    def test_the_endurance_amplitude_follows_the_log_normal_scatter_definition(self) -> None:
        from scipy import stats

        curve = _curve(scatter_tn=3.0)
        transformed = default_backend().at_failure_probability(curve, 0.1)

        # TS = TN^(1/k1); the scatter range T is the ratio of the 90% to the 10%
        # value of a log-normal, so its standard deviation in decades is
        # log10(T)/(z_0.9 − z_0.1). SD(p) = SD(0.5)·10^(z_p·σ_log).
        scatter_ts = 3.0 ** (1.0 / 5.0)
        sigma_log = math.log10(scatter_ts) / (stats.norm.ppf(0.9) - stats.norm.ppf(0.1))
        expected = 100.0 * 10.0 ** (stats.norm.ppf(0.1) * sigma_log)

        assert transformed.scatter_ts == pytest.approx(scatter_ts)
        assert transformed.knee_amplitude_mpa == pytest.approx(expected)
        assert transformed.failure_probability == pytest.approx(0.1)

    def test_a_lower_failure_probability_is_a_weaker_curve(self) -> None:
        backend = default_backend()
        curve = _curve(scatter_tn=3.0)
        weaker = backend.at_failure_probability(curve, 0.025)
        assert weaker.knee_amplitude_mpa < curve.knee_amplitude_mpa
        assert backend.cycles_to_failure(weaker, 200.0) < backend.cycles_to_failure(curve, 200.0)

    def test_round_tripping_a_probability_returns_the_original_curve(self) -> None:
        backend = default_backend()
        curve = _curve(scatter_tn=3.0)
        there = backend.at_failure_probability(curve, 0.025)
        back = backend.at_failure_probability(there, 0.5)
        assert back.knee_amplitude_mpa == pytest.approx(curve.knee_amplitude_mpa)
        assert back.knee_cycles == pytest.approx(curve.knee_cycles)


class TestEndToEndFromAHistory:
    """A history counted, corrected, factored and summed — every step at once."""

    def test_a_counted_pulsating_history_matches_the_hand_calculation(self) -> None:
        # 0 → 100 → 0, five times: four closed cycles at amplitude 50 about mean 50.
        history = LoadHistory.from_values(
            "pulsating duty cycle",
            [0.0] + [100.0, 0.0] * 5,
            basis=StressBasis.NOTCH_ROOT,
            sign=SignConvention.SIGNED,
            source="stated duty cycle for the test",
        )
        curve = _curve(mean_stress_sensitivity=0.4).with_haibach_slope(source="test")
        result = Assessment(
            loading=history,
            curve=curve,
            factors=_unity_factors(),
            design_life_blocks=1_000.0,
        ).run()

        # Equivalent fully reversed amplitude: 50 + 0.4·50 = 70 MPa.
        # Below SD = 100 with Haibach k2 = 9: N = 1e6·(70/100)^-9.
        expected_n = 1.0e6 * (70.0 / 100.0) ** -9.0
        expected_damage = 4.0 * 1_000.0 / expected_n

        assert result.outcome is Outcome.PASSED
        assert result.damage == pytest.approx(expected_damage)
        assert [b.cycles_to_failure for b in result.blocks] == pytest.approx([expected_n] * 4)
