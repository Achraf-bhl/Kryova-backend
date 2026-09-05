"""Phase 5.1 — assertions for machines rather than parts.

Offline, like the rest of `app/design/`: the tools are a stub, so nothing here
needs a kernel, a mesh or a solver. That is the property being protected as much
as it is a convenience — a check library that could only be tested with OCCT
installed would be tested rarely.

The rule under nearly every test below is the one the package already lives by:
**an unmeasured claim is never a pass.** A machine with no solver, a partial
tools object, a tool that raises — each has to come back `UNMEASURED` with a
reason somebody can act on, and a report containing one is not green.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from app.design.assertions import Outcome
from app.design.errors import SpecError
from app.design.machine_checks import (
    MINIMUM_SAMPLES,
    BucklingFactor,
    ClearanceThroughMotion,
    CostBudget,
    Envelope,
    FactorOfSafety,
    FirstNaturalFrequency,
    MachineTooling,
    MassBudget,
    MinimumWall,
    StackUp,
    ToolUnavailable,
    evaluate_machine,
    machine_measurements,
)


class _Tools:
    """A machine that can measure everything, with values the test dictates."""

    def __init__(self, **answers: Any) -> None:
        self.answers = answers
        self.poses: list[float] = []

    def mass_kg(self, subject: Any) -> float:
        return float(self.answers.get("mass", 1.0))

    def bounding_box_mm(self, subject: Any) -> tuple[float, float, float]:
        return tuple(self.answers.get("box", (10.0, 20.0, 30.0)))  # type: ignore[return-value]

    def minimum_wall_mm(self, subject: Any) -> tuple[float, bool]:
        return self.answers.get("wall", (3.0, False))

    def posed(self, subject: Any, at: float) -> Any:
        self.poses.append(at)
        return ("posed", at)

    def minimum_distance_mm(self, first: Any, second: Any) -> float:
        gaps = self.answers.get("gaps")
        if gaps is None:
            return float(self.answers.get("gap", 5.0))
        return float(gaps[len(self.poses) - 1])

    def first_natural_frequency_hz(self, subject: Any) -> float:
        return float(self.answers.get("hz", 250.0))

    def factor_of_safety(self, subject: Any, load_case: str) -> float:
        return float(self.answers.get("fos", {}).get(load_case, 2.0))

    def buckling_factor(self, subject: Any, load_case: str) -> float:
        return float(self.answers.get("buckling", 12.0))


def _outcome(report: Any, name_fragment: str) -> Outcome:
    for result in report.results:
        if name_fragment in result.name:
            return result.outcome
    raise AssertionError(f"no result named like {name_fragment!r} in {report}")


def _result(report: Any, name_fragment: str) -> Any:
    for result in report.results:
        if name_fragment in result.name:
            return result
    raise AssertionError(f"no result named like {name_fragment!r}")


class TestAnUnmeasuredClaimIsNeverAPass:
    def test_a_machine_with_no_tools_reports_every_claim_unmeasured(self) -> None:
        report = evaluate_machine([MassBudget(4.2), FirstNaturalFrequency(120.0)])
        assert not report
        assert all(one.outcome is Outcome.UNMEASURED for one in report.results)

    def test_the_reason_says_what_is_missing(self) -> None:
        report = evaluate_machine([MassBudget(4.2)], tools=MachineTooling("no kernel is installed."))
        assert "no kernel is installed" in _result(report, "mass").reason

    def test_a_partial_tools_object_is_supported_not_a_crash(self) -> None:
        """Supplying only the tools one check needs is a normal thing to do."""

        class OnlyMass:
            def mass_kg(self, subject: Any) -> float:
                return 3.0

        report = evaluate_machine(
            [MassBudget(4.2), FirstNaturalFrequency(120.0)], tools=OnlyMass()
        )
        assert _outcome(report, "mass") is Outcome.PASSED
        assert _outcome(report, "first_mode") is Outcome.UNMEASURED

    def test_a_tool_that_raises_is_unmeasured_rather_than_failed(self) -> None:
        """'The solver fell over' is not the same claim as 'the part is unsafe'."""

        class Broken:
            def factor_of_safety(self, subject: Any, load_case: str) -> float:
                raise RuntimeError("the mesh was degenerate")

        report = evaluate_machine([FactorOfSafety("lift", 1.5)], tools=Broken())
        result = _result(report, "fos")
        assert result.outcome is Outcome.UNMEASURED
        assert "degenerate" in result.reason

    def test_tool_unavailable_keeps_its_own_sentence(self) -> None:
        class NoSolver:
            def first_natural_frequency_hz(self, subject: Any) -> float:
                raise ToolUnavailable("no solver is federated yet (Phase 6).")

        report = evaluate_machine([FirstNaturalFrequency(120.0)], tools=NoSolver())
        assert "Phase 6" in _result(report, "first_mode").reason


class TestTheChecksThatMeasureGeometry:
    def test_a_mass_budget_passes_and_fails_on_the_number(self) -> None:
        assert evaluate_machine([MassBudget(4.2)], tools=_Tools(mass=3.9))
        assert not evaluate_machine([MassBudget(4.2)], tools=_Tools(mass=4.9))

    def test_a_failure_carries_how_far_over_it_was(self) -> None:
        """'3.1 kg over' is actionable; 'failed' is not."""
        report = evaluate_machine([MassBudget(4.0)], tools=_Tools(mass=7.1))
        assert _result(report, "mass").gap == pytest.approx(3.1)

    def test_an_envelope_names_the_axis_that_does_not_fit(self) -> None:
        report = evaluate_machine(
            [Envelope(100.0, 100.0, 25.0)], tools=_Tools(box=(10.0, 20.0, 30.0))
        )
        assert _outcome(report, "fits in x") is Outcome.PASSED
        assert _outcome(report, "fits in y") is Outcome.PASSED
        assert _outcome(report, "fits in z") is Outcome.FAILED

    def test_a_sampled_wall_is_reported_as_approximate(self) -> None:
        """A wall that passes by 0.01 mm on a ray cast has not been shown to pass."""
        report = evaluate_machine([MinimumWall(2.0)], tools=_Tools(wall=(2.01, True)))
        assert _result(report, "wall").approximate is True

    def test_an_exact_wall_is_not_marked_approximate(self) -> None:
        report = evaluate_machine([MinimumWall(2.0)], tools=_Tools(wall=(2.01, False)))
        assert _result(report, "wall").approximate is False


class TestClearanceThroughAMotionRange:
    def test_it_reports_the_closest_approach_over_the_whole_sweep(self) -> None:
        tools = _Tools(gaps=[8.0, 6.0, 1.5, 9.0, 7.0])
        report = evaluate_machine(
            [ClearanceThroughMotion(against=object(), floor_mm=2.0, samples=5)], tools=tools
        )
        result = _result(report, "clearance")
        assert result.measured == pytest.approx(1.5)
        assert result.outcome is Outcome.FAILED

    def test_the_sweep_covers_both_ends_of_the_range(self) -> None:
        tools = _Tools(gap=5.0)
        evaluate_machine(
            [ClearanceThroughMotion(against=object(), floor_mm=1.0, samples=5)], tools=tools
        )
        assert tools.poses[0] == 0.0
        assert tools.poses[-1] == 1.0
        assert len(tools.poses) == 5

    def test_the_answer_is_approximate_because_it_is_sampled(self) -> None:
        """A collision between two adjacent poses is invisible to this, and it says so."""
        report = evaluate_machine(
            [ClearanceThroughMotion(against=object(), floor_mm=1.0, samples=8)],
            tools=_Tools(gap=5.0),
        )
        result = _result(report, "clearance")
        assert result.approximate is True
        assert "between two of them" in result.assertion.note

    def test_too_few_samples_is_refused_at_construction(self) -> None:
        with pytest.raises(SpecError, match="not a sweep"):
            ClearanceThroughMotion(against=object(), floor_mm=1.0, samples=MINIMUM_SAMPLES - 1)


class TestStackUpIsArithmeticAndExact:
    CHAIN = [
        ("housing bore", 40.0, 0.05),
        ("bearing outer", -32.0, 0.02),
        ("shim", -4.0, 0.03),
        ("clip", -3.5, 0.04),
    ]

    def test_worst_case_sums_the_tolerances(self) -> None:
        report = evaluate_machine(
            [StackUp(self.CHAIN, limit_mm=0.20, method="worst_case")], tools=MachineTooling()
        )
        assert _result(report, "stack_up").measured == pytest.approx(0.14)
        assert report

    def test_rss_is_the_root_sum_of_squares_and_is_smaller(self) -> None:
        report = evaluate_machine(
            [StackUp(self.CHAIN, limit_mm=0.20, method="rss")], tools=MachineTooling()
        )
        expected = math.sqrt(0.05**2 + 0.02**2 + 0.03**2 + 0.04**2)
        assert _result(report, "stack_up").measured == pytest.approx(expected)
        assert expected < 0.14

    def test_the_method_chosen_is_named_in_the_claim(self) -> None:
        """Neither method is a safe default, so the report must say which ran."""
        worst = evaluate_machine([StackUp(self.CHAIN, 0.2, "worst_case")], tools=MachineTooling())
        rss = evaluate_machine([StackUp(self.CHAIN, 0.2, "rss")], tools=MachineTooling())
        assert "worst_case" in worst.results[0].name
        assert "rss" in rss.results[0].name

    def test_it_needs_no_tools_at_all(self) -> None:
        """The one check here that can never come back unmeasured."""
        report = evaluate_machine([StackUp(self.CHAIN, 0.2, "rss")])
        assert report.results[0].outcome is not Outcome.UNMEASURED

    def test_an_empty_chain_is_refused_rather_than_always_passing(self) -> None:
        with pytest.raises(SpecError, match="no contributors"):
            StackUp([], limit_mm=0.2)

    def test_a_negative_tolerance_is_refused(self) -> None:
        with pytest.raises(SpecError, match="half-width"):
            StackUp([("a", 1.0, -0.1)], limit_mm=0.2)

    def test_an_unknown_method_is_refused_with_both_names(self) -> None:
        with pytest.raises(SpecError, match="worst_case"):
            StackUp(self.CHAIN, limit_mm=0.2, method="average")  # type: ignore[arg-type]


class TestTheSolverBackedChecks:
    def test_a_first_mode_below_the_threshold_fails(self) -> None:
        assert not evaluate_machine([FirstNaturalFrequency(300.0)], tools=_Tools(hz=250.0))
        assert evaluate_machine([FirstNaturalFrequency(200.0)], tools=_Tools(hz=250.0))

    def test_a_factor_of_safety_is_against_a_named_load_case(self) -> None:
        tools = _Tools(fos={"lift": 1.2, "stow": 4.0})
        report = evaluate_machine(
            [FactorOfSafety("lift", 1.5), FactorOfSafety("stow", 1.5, name="fos_stow")],
            tools=tools,
        )
        assert _outcome(report, "under lift") is Outcome.FAILED
        assert _outcome(report, "under stow") is Outcome.PASSED

    def test_an_unnamed_load_case_is_refused(self) -> None:
        """A factor of safety nobody can reproduce is not a check."""
        with pytest.raises(SpecError, match="name of the load case"):
            FactorOfSafety("  ", 1.5)

    def test_two_load_cases_do_not_collide_in_the_payload(self) -> None:
        payload = machine_measurements(
            None,
            [FactorOfSafety("lift", 1.5), FactorOfSafety("stow", 1.5)],
            _Tools(fos={"lift": 1.2, "stow": 4.0}),
        )
        assert payload["machine"]["fos"]["lift"]["factor_of_safety"] == pytest.approx(1.2)
        assert payload["machine"]["fos"]["stow"]["factor_of_safety"] == pytest.approx(4.0)

    def test_buckling_reads_its_own_factor(self) -> None:
        assert evaluate_machine([BucklingFactor("lift", 4.0)], tools=_Tools(buckling=12.0))


class TestCostIsDeclaredAndHonestlyUnavailable:
    def test_a_cost_budget_is_unmeasured_because_there_is_no_cost_model(self) -> None:
        """Declared so the report says so, rather than quietly left out of the library."""
        report = evaluate_machine([CostBudget(1200.0)], tools=_Tools())
        assert _outcome(report, "cost") is Outcome.UNMEASURED

    def test_it_becomes_real_the_day_a_tool_answers(self) -> None:
        class WithCost(_Tools):
            def cost(self, subject: Any, currency: str) -> float:
                return 900.0

        assert evaluate_machine([CostBudget(1200.0)], tools=WithCost())


class TestThePayloadCarriesProvenance:
    def test_a_sampled_number_is_marked_approximated_per_path(self) -> None:
        from app.kernel.provenance import Basis, basis_of

        payload = machine_measurements(
            None,
            [MinimumWall(2.0), MassBudget(4.0)],
            _Tools(wall=(2.5, True), mass=3.0),
        )
        assert basis_of(payload, "machine.wall.minimum_mm") is Basis.APPROXIMATED
        # An exact mass beside a sampled wall is not tainted by it.
        assert basis_of(payload, "machine.mass.kg") is Basis.MEASURED

    def test_an_unavailable_number_records_its_reason_in_the_sidecar(self) -> None:
        from app.kernel.provenance import Basis, basis_of, reason_for

        payload = machine_measurements(None, [FirstNaturalFrequency(120.0)], MachineTooling())
        assert basis_of(payload, "machine.first_mode.hz") is Basis.UNAVAILABLE
        assert reason_for(payload, "machine.first_mode.hz")

    def test_measurements_land_under_their_own_namespace(self) -> None:
        """A produced number must never be mistaken for one the kernel measured."""
        payload = machine_measurements(None, [MassBudget(4.0)], _Tools())
        assert "machine" in payload
        assert "mass_kg" not in payload
