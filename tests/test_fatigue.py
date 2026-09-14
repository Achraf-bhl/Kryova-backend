"""The fatigue package's refusals — every judgement nobody made comes back with a name on it.

`test_fatigue_verification.py` holds the arithmetic to closed form. This file holds the
other half, which is the part `app/fatigue/assessment.py` calls *ours*: an input a method
needs and was not given is `UNMEASURED` naming the input, never a number. Until
2026-09-14 the verification file's docstring pointed here and there was no such file, so
not one of these guards had ever been seen to fail.

Each class is one refusal. Each was verified by removing the guard it names and
watching the test fail.
"""

from __future__ import annotations

import math

import pytest

from app.design.assertions import Outcome
from app.fatigue import (
    Assessment,
    Collective,
    Confidence,
    DamageResult,
    Factor,
    FactorSet,
    LoadHistory,
    MeanStressPolicy,
    Method,
    SignConvention,
    SNCurve,
    StressBasis,
    StressConcentration,
    WeldDetail,
    available,
)
from app.fatigue import assessment as assessment_module
from app.fatigue.assessment import MINER_RULE

SOURCE = "test fixture, not a real material"

needs_pylife = pytest.mark.skipif(
    not available(), reason="pyLife is not installed; the assessment federates counting to it"
)


def _factors() -> FactorSet:
    return FactorSet.of([Factor("surface", 1.0, source=SOURCE), Factor("size", 1.0, source=SOURCE)])


def _curve(**overrides: object) -> SNCurve:
    kwargs: dict[str, object] = {
        "slope_k1": 5.0,
        "knee_cycles": 1.0e6,
        "knee_amplitude_mpa": 100.0,
        "source": SOURCE,
    }
    kwargs.update(overrides)
    return SNCurve(**kwargs)  # type: ignore[arg-type]


def _reversed(basis: StressBasis = StressBasis.NOTCH_ROOT) -> LoadHistory:
    return LoadHistory.from_values(
        "reversed",
        [0.0, 200.0, -200.0, 200.0, -200.0, 0.0],
        basis=basis,
        sign=SignConvention.SIGNED,
        source=SOURCE,
    )


def _declared(*blocks: tuple[float, float, float], basis=StressBasis.NOTCH_ROOT) -> Collective:
    return Collective.from_blocks(list(blocks), basis=basis, source=SOURCE)


def _missing(result: DamageResult) -> str:
    assert result.outcome is Outcome.UNMEASURED, result.summary()
    assert result.damage is None
    return " ".join(result.missing)


@needs_pylife
class TestAnUnsignedHistoryIsNotCounted:
    def test_a_von_mises_history_is_unmeasured_naming_the_sign(self) -> None:
        history = LoadHistory.from_von_mises("vm", [0.0, 200.0, 0.0, 200.0, 0.0], source=SOURCE)
        result = Assessment(loading=history, curve=_curve(), factors=_factors()).run()
        assert "signed stress history" in _missing(result)

    def test_from_von_mises_is_unsigned_and_local(self) -> None:
        history = LoadHistory.from_von_mises("vm", [0.0, 1.0, 0.0], source=SOURCE)
        assert history.sign is SignConvention.UNSIGNED
        assert history.basis is StressBasis.NOTCH_ROOT


@needs_pylife
class TestAFlatHistoryIsNotANoDamageResult:
    def test_a_constant_history_is_unmeasured(self) -> None:
        history = LoadHistory.from_values(
            "flat", [50.0, 50.0, 50.0], basis=StressBasis.NOTCH_ROOT, sign=SignConvention.SIGNED, source=SOURCE
        )
        result = Assessment(loading=history, curve=_curve(), factors=_factors()).run()
        assert "varying stress history" in _missing(result)


@needs_pylife
class TestTheNotchIsAppliedExactlyOnce:
    def test_a_nominal_history_without_a_concentration_is_unmeasured(self) -> None:
        result = Assessment(loading=_reversed(StressBasis.NOMINAL), curve=_curve(), factors=_factors()).run()
        assert "stress concentration factor" in _missing(result)

    def test_a_concentration_on_a_notch_root_stress_is_refused(self) -> None:
        result = Assessment(
            loading=_reversed(StressBasis.NOTCH_ROOT),
            curve=_curve(),
            factors=_factors(),
            concentration=StressConcentration(kt=2.0, source=SOURCE),
        ).run()
        missing = _missing(result)
        assert "squares it" in missing
        assert "the FE stress" in missing

    def test_a_concentration_on_a_hot_spot_stress_is_refused_naming_the_category(self) -> None:
        result = Assessment(
            loading=_reversed(StressBasis.HOT_SPOT),
            curve=_curve(),
            factors=_factors(),
            concentration=StressConcentration(kt=2.0, source=SOURCE),
            required_factors=frozenset(),
        ).run()
        assert "the detail category" in _missing(result)

    def test_a_nominal_history_with_a_concentration_is_assessed_at_kf(self) -> None:
        concentration = StressConcentration(kt=2.0, source=SOURCE, notch_sensitivity=0.5)
        result = Assessment(
            loading=_declared((100.0, 0.0, 1000.0), basis=StressBasis.NOMINAL),
            curve=_curve(),
            factors=_factors(),
            concentration=concentration,
        ).run()
        assert result.outcome is Outcome.PASSED
        assert result.blocks[0].amplitude_mpa == pytest.approx(150.0)

    def test_kf_taken_as_kt_is_stated_not_implied(self) -> None:
        result = Assessment(
            loading=_declared((100.0, 0.0, 1000.0), basis=StressBasis.NOMINAL),
            curve=_curve(),
            factors=_factors(),
            concentration=StressConcentration(kt=2.0, source=SOURCE),
        ).run()
        assert any("full notch sensitivity" in a for a in result.assumptions)


@needs_pylife
class TestARequiredFactorCannotBeLeftOut:
    def test_an_empty_factor_set_names_both_required_factors(self) -> None:
        result = Assessment(loading=_reversed(), curve=_curve()).run()
        missing = _missing(result)
        assert "the size factor" in missing
        assert "the surface factor" in missing

    def test_a_factor_of_one_is_not_the_same_as_no_factor(self) -> None:
        assessed = Assessment(loading=_reversed(), curve=_curve(), factors=_factors()).run()
        assert assessed.outcome is not Outcome.UNMEASURED

    def test_a_welded_assessment_may_declare_no_factors_required(self) -> None:
        result = Assessment(loading=_reversed(), curve=_curve(), required_factors=frozenset()).run()
        assert result.outcome is not Outcome.UNMEASURED


@needs_pylife
class TestAProbabilityNeedsTheScatter:
    def test_asking_for_a_survival_probability_of_a_median_curve_is_unmeasured(self) -> None:
        result = Assessment(
            loading=_reversed(), curve=_curve(), factors=_factors(), failure_probability=0.025
        ).run()
        assert "scatter" in _missing(result)

    def test_with_scatter_the_result_is_characterised(self) -> None:
        result = Assessment(
            loading=_reversed(),
            curve=_curve(scatter_tn=3.0, scatter_ts=1.25),
            factors=_factors(),
            failure_probability=0.1,
        ).run()
        assert result.confidence is Confidence.CHARACTERISED
        assert result.failure_probability == pytest.approx(0.1)

    def test_without_asking_for_a_probability_it_is_only_screening(self) -> None:
        result = Assessment(loading=_reversed(), curve=_curve(scatter_tn=3.0), factors=_factors()).run()
        assert result.confidence is Confidence.SCREENING


@needs_pylife
class TestMeanStressIsCorrectedOrDeclaredIrrelevantInWords:
    def test_a_tensile_mean_with_no_sensitivity_is_unmeasured(self) -> None:
        result = Assessment(loading=_declared((100.0, 50.0, 1000.0)), curve=_curve(), factors=_factors()).run()
        assert "mean-stress sensitivity M" in _missing(result)

    def test_declaring_it_irrelevant_needs_a_justification(self) -> None:
        result = Assessment(
            loading=_declared((100.0, 50.0, 1000.0)),
            curve=_curve(),
            factors=_factors(),
            mean_stress_policy=MeanStressPolicy.DECLARED_IRRELEVANT,
            mean_stress_justification="   ",
        ).run()
        assert "written justification" in _missing(result)

    def test_a_justification_is_carried_into_the_method_and_the_assumptions(self) -> None:
        why = "as-welded joint to EN 1993-1-9; residual stress at yield"
        result = Assessment(
            loading=_declared((100.0, 50.0, 1000.0)),
            curve=_curve(),
            factors=_factors(),
            mean_stress_policy=MeanStressPolicy.DECLARED_IRRELEVANT,
            mean_stress_justification=why,
        ).run()
        assert result.outcome is not Outcome.UNMEASURED
        assert why in result.method.mean_stress_correction
        assert any(why in a for a in result.assumptions)

    def test_a_fully_reversed_collective_needs_no_sensitivity(self) -> None:
        result = Assessment(loading=_declared((100.0, 0.0, 1000.0)), curve=_curve(), factors=_factors()).run()
        assert result.outcome is not Outcome.UNMEASURED


class TestAMissingBackendIsAFindingNotACrash:
    def test_no_pylife_is_unmeasured_naming_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(assessment_module, "backend_available", lambda: False)
        monkeypatch.setattr(assessment_module, "backend_import_error", lambda: "ModuleNotFoundError: pylife")
        result = Assessment(loading=_reversed(), curve=_curve(), factors=_factors()).run()
        assert "pyLife" in _missing(result)


@needs_pylife
class TestWhatTheResultSaysItAssumed:
    def test_miner_original_is_stated_when_there_is_no_second_slope(self) -> None:
        result = Assessment(loading=_reversed(), curve=_curve(), factors=_factors()).run()
        assert any("Miner original" in a for a in result.assumptions)

    def test_the_residue_of_an_unclosed_history_is_stated(self) -> None:
        result = Assessment(loading=_reversed(), curve=_curve(), factors=_factors()).run()
        assert any("unclosed reversals" in a for a in result.assumptions)

    def test_every_declared_input_is_printed_beside_the_number(self) -> None:
        result = Assessment(
            loading=_reversed(), curve=_curve(), factors=_factors(), location="fillet root R3"
        ).run()
        joined = " | ".join(result.inputs)
        assert SOURCE in joined
        assert "location: fillet root R3" in joined
        assert "damage limit: 1" in joined


class TestAZeroDamageIsNotAnUnmeasuredDamage:
    def test_an_unmeasured_result_must_name_what_is_missing(self) -> None:
        with pytest.raises(ValueError, match="name the input"):
            DamageResult(outcome=Outcome.UNMEASURED, method=Method(damage_rule=MINER_RULE))

    def test_a_verdict_must_carry_its_damage(self) -> None:
        with pytest.raises(ValueError, match="carry the damage"):
            DamageResult(outcome=Outcome.PASSED, method=Method(damage_rule=MINER_RULE))

    def test_a_method_must_name_its_damage_rule(self) -> None:
        with pytest.raises(ValueError, match="damage rule"):
            Method(damage_rule=" ")

    def test_an_unmeasured_payload_has_no_damage_and_says_why(self) -> None:
        result = DamageResult(
            outcome=Outcome.UNMEASURED, method=Method(damage_rule=MINER_RULE), missing=("an S-N curve",)
        )
        payload = result.to_payload()
        assert "damage" not in payload
        assert "an S-N curve" in str(payload)

    @needs_pylife
    def test_everything_below_the_endurance_limit_is_zero_damage_not_none(self) -> None:
        result = Assessment(loading=_declared((50.0, 0.0, 1e9)), curve=_curve(), factors=_factors()).run()
        assert result.outcome is Outcome.PASSED
        assert result.damage == 0.0
        assert result.life_blocks is not None and math.isinf(result.life_blocks)


class TestConstructionRefusals:
    def test_a_design_life_must_be_positive_and_finite(self) -> None:
        for bad in (0.0, -1.0, math.inf):
            with pytest.raises(ValueError, match="design life"):
                Assessment(loading=_reversed(), curve=_curve(), design_life_blocks=bad)

    def test_a_damage_limit_lies_in_zero_to_one(self) -> None:
        for bad in (0.0, 1.5):
            with pytest.raises(ValueError, match="damage limit"):
                Assessment(loading=_reversed(), curve=_curve(), damage_limit=bad)

    def test_a_factor_needs_a_source_a_name_and_a_positive_value(self) -> None:
        with pytest.raises(ValueError, match="source"):
            Factor("surface", 0.8, source="")
        with pytest.raises(ValueError, match="named"):
            Factor(" ", 0.8, source=SOURCE)
        with pytest.raises(ValueError, match="positive, finite"):
            Factor("surface", 0.0, source=SOURCE)

    def test_one_factor_given_twice_is_refused_rather_than_squared(self) -> None:
        with pytest.raises(ValueError, match="given twice"):
            FactorSet.of([Factor("surface", 0.8, source=SOURCE), Factor("Surface", 0.9, source=SOURCE)])

    def test_a_concentration_below_one_and_a_sensitivity_outside_the_unit_interval(self) -> None:
        with pytest.raises(ValueError, match="at least 1.0"):
            StressConcentration(kt=0.9, source=SOURCE)
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            StressConcentration(kt=2.0, source=SOURCE, notch_sensitivity=1.2)

    def test_an_sn_curve_refuses_a_meaningless_slope_probability_or_scatter(self) -> None:
        with pytest.raises(ValueError, match="slope"):
            _curve(slope_k1=0.0)
        with pytest.raises(ValueError, match="strictly between"):
            _curve(failure_probability=1.0)
        with pytest.raises(ValueError, match="at least 1.0"):
            _curve(scatter_tn=0.5)

    def test_haibach_needs_its_own_source(self) -> None:
        with pytest.raises(ValueError, match="source"):
            _curve().with_haibach_slope("")

    def test_a_history_needs_three_finite_points(self) -> None:
        with pytest.raises(ValueError, match="three points"):
            LoadHistory.from_values("h", [0.0, 1.0], basis=StressBasis.NOMINAL, sign=SignConvention.SIGNED, source=SOURCE)
        with pytest.raises(ValueError, match="non-finite"):
            LoadHistory.from_values(
                "h", [0.0, float("nan"), 1.0], basis=StressBasis.NOMINAL, sign=SignConvention.SIGNED, source=SOURCE
            )

    def test_a_weld_detail_needs_a_positive_category_and_a_source(self) -> None:
        with pytest.raises(ValueError, match="source"):
            WeldDetail(detail_category_mpa=71.0, source="")
