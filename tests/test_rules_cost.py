"""The cost estimate is arithmetic on stated rates, and an unknown input is never a zero (E13.3).

Every figure below is a test fixture, not a price. The totals are worked by hand in the
test that asserts them, the way `tests/test_solver.py` works σ = F/A.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.design.assertions import Outcome
from app.design.machine_checks import CostBudget, evaluate_machine, machine_measurements
from app.kernel.provenance import Basis, basis_of
from app.rules.cost import CostInputs, CostTools, Rate, estimate
from app.rules.errors import RuleError, SourceError

QUOTE = "test fixture, not a price"


def _rate(value: float) -> Rate:
    return Rate(value=value, source=QUOTE)


def _complete(**overrides: Any) -> CostInputs:
    kwargs: dict[str, Any] = {
        "currency": "EUR",
        "batch_size": 50,
        "material_price_per_kg": _rate(4.0),
        "stock_factor": _rate(3.0),
        "cycle_minutes": _rate(12.0),
        "setup_minutes": _rate(90.0),
        "machine_rate_per_hour": _rate(60.0),
        "tooling_cost": _rate(500.0),
        "tooling_amortised_over_parts": _rate(1000.0),
        "assembly_minutes": _rate(6.0),
        "labour_rate_per_hour": _rate(40.0),
    }
    kwargs.update(overrides)
    return CostInputs(**kwargs)


class TestTheArithmetic:
    def test_the_four_components_are_the_hand_sums(self) -> None:
        answer = estimate(_complete(), mass_kg=0.5, mass_source="measured by the kernel")
        by_name = {line.name: line.value for line in answer.lines}

        # 0.5 kg x 3 x 4.00 = 6.00
        assert by_name["material"] == pytest.approx(6.0)
        # 12/60 x 60 + 90/60 x 60 / 50 = 12.00 + 1.80 = 13.80
        assert by_name["process"] == pytest.approx(13.8)
        # 500 / 1000 = 0.50
        assert by_name["tooling"] == pytest.approx(0.5)
        # 6/60 x 40 = 4.00
        assert by_name["assembly"] == pytest.approx(4.0)
        assert answer.total == pytest.approx(24.3)

    def test_setup_spreads_over_the_batch(self) -> None:
        one = estimate(_complete(batch_size=1), mass_kg=0.5, mass_source="kernel")
        fifty = estimate(_complete(batch_size=50), mass_kg=0.5, mass_source="kernel")
        assert one.total - fifty.total == pytest.approx(90.0 - 1.8)

    def test_an_explicit_zero_with_a_source_is_a_real_zero(self) -> None:
        answer = estimate(
            _complete(
                tooling_cost=Rate(0.0, "machined from stock, no dedicated tooling"),
                assembly_minutes=Rate(0.0, "a single part, nothing to assemble"),
            ),
            mass_kg=0.5,
            mass_source="kernel",
        )
        assert answer.total == pytest.approx(6.0 + 13.8)

    def test_every_line_carries_its_sources(self) -> None:
        answer = estimate(_complete(), mass_kg=0.5, mass_source="kernel")
        material = next(line for line in answer.lines if line.name == "material")
        assert material.sources == (
            f"material price per kg: {QUOTE}",
            f"stock factor: {QUOTE}",
        )


class TestAnUnknownInputIsNotAZero:
    def test_a_missing_rate_leaves_the_total_unavailable_naming_it(self) -> None:
        answer = estimate(
            _complete(labour_rate_per_hour=None), mass_kg=0.5, mass_source="kernel"
        )
        assert answer.total is None
        assert answer.missing == ("assembly: labour rate per hour",)

    def test_the_payload_records_the_total_unavailable_with_the_reason(self) -> None:
        payload = estimate(
            _complete(stock_factor=None), mass_kg=0.5, mass_source="kernel"
        ).to_payload()
        assert "total" not in payload
        assert basis_of(payload, "total") is Basis.UNAVAILABLE

    def test_a_complete_total_is_approximated_never_measured(self) -> None:
        payload = estimate(_complete(), mass_kg=0.5, mass_source="kernel").to_payload()
        assert payload["total"] == pytest.approx(24.3)
        assert basis_of(payload, "total") is Basis.APPROXIMATED


class TestTheCostBudgetIsCheckable:
    def _tools(self, inputs: CostInputs) -> CostTools:
        return CostTools(inputs=inputs, mass_of=lambda subject: (0.5, "kernel"))

    def test_within_budget_passes(self) -> None:
        report = evaluate_machine([CostBudget(30.0)], tools=self._tools(_complete()))  # type: ignore[arg-type]
        assert report.results[0].outcome is Outcome.PASSED

    def test_over_budget_fails(self) -> None:
        report = evaluate_machine([CostBudget(20.0)], tools=self._tools(_complete()))  # type: ignore[arg-type]
        assert report.results[0].outcome is Outcome.FAILED

    def test_a_missing_input_is_unmeasured_and_says_which(self) -> None:
        report = evaluate_machine(
            [CostBudget(30.0)],
            tools=self._tools(_complete(machine_rate_per_hour=None)),  # type: ignore[arg-type]
        )
        [result] = report.results
        assert result.outcome is Outcome.UNMEASURED
        assert "machine rate per hour" in str(result)

    def test_a_budget_in_another_currency_is_unmeasured_not_converted(self) -> None:
        report = evaluate_machine(
            [CostBudget(30.0, currency="USD")], tools=self._tools(_complete())  # type: ignore[arg-type]
        )
        [result] = report.results
        assert result.outcome is Outcome.UNMEASURED
        assert "converts" in str(result)

    def test_the_budget_payload_marks_the_cost_approximated(self) -> None:
        payload = machine_measurements(None, [CostBudget(30.0)], self._tools(_complete()))  # type: ignore[arg-type]
        assert basis_of(payload, "machine.cost.total") is Basis.APPROXIMATED


class TestWhatTheInputsRefuse:
    def test_a_rate_with_no_source(self) -> None:
        with pytest.raises(SourceError):
            Rate(value=1.0, source=" ")

    def test_a_negative_rate(self) -> None:
        with pytest.raises(RuleError, match="not negative"):
            Rate(value=-1.0, source=QUOTE)

    def test_a_stock_factor_below_one(self) -> None:
        with pytest.raises(RuleError, match="at least 1"):
            _complete(stock_factor=_rate(0.8))

    def test_a_batch_of_zero(self) -> None:
        with pytest.raises(RuleError, match="at least 1"):
            _complete(batch_size=0)

    def test_a_mass_with_no_source(self) -> None:
        with pytest.raises(SourceError):
            estimate(_complete(), mass_kg=0.5, mass_source="")
