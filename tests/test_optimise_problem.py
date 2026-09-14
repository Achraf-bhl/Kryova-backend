"""What an optimisation *is* — `app/optimise/problem.py`, master plan 10.3.

The vocabulary a driver never sees: bounded design variables, one objective read
by a measurement path, and constraints that are `Assertion`s. Every refusal here
is a statement that is wrong before anything is built, so each one is tested by
posing the wrong statement and reading the sentence that comes back.
"""

from __future__ import annotations

import math

import pytest

from app.design.assertions import Assertion, AssertionResult, Outcome
from app.optimise import (
    DEFAULT_MAX_EVALUATIONS,
    DesignVariable,
    Objective,
    ObjectiveError,
    OptimisationProblem,
    Sense,
    VariableError,
    margin,
)


def _problem(**overrides: object) -> OptimisationProblem:
    fields: dict[str, object] = {
        "name": "plate",
        "variables": (
            DesignVariable(name="width_mm", lower=10.0, upper=50.0),
            DesignVariable(name="thickness_mm", lower=1.0, upper=5.0),
        ),
        "objective": Objective(measurement="mass_kg"),
    }
    fields.update(overrides)
    return OptimisationProblem(**fields)  # type: ignore[arg-type]


class TestADesignVariableIsABox:
    @pytest.mark.parametrize("bound", [math.inf, -math.inf, math.nan])
    def test_an_infinite_bound_is_refused(self, bound: float) -> None:
        with pytest.raises(VariableError, match="Bounds must be finite"):
            DesignVariable(name="r", lower=bound, upper=10.0)
        with pytest.raises(VariableError, match="Bounds must be finite"):
            DesignVariable(name="r", lower=0.0, upper=bound)

    @pytest.mark.parametrize(("lower", "upper"), [(5.0, 5.0), (6.0, 5.0)])
    def test_a_box_with_nothing_inside_is_refused(self, lower: float, upper: float) -> None:
        with pytest.raises(VariableError, match="nothing between them"):
            DesignVariable(name="r", lower=lower, upper=upper)

    def test_a_start_outside_its_own_box_is_refused(self) -> None:
        with pytest.raises(VariableError, match="outside its own"):
            DesignVariable(name="r", lower=0.0, upper=1.0, initial=1.5)

    def test_clamp_lands_on_the_nearest_bound(self) -> None:
        variable = DesignVariable(name="r", lower=2.0, upper=4.0)
        assert variable.clamp(1.0) == 2.0
        assert variable.clamp(9.0) == 4.0
        assert variable.clamp(3.0) == 3.0
        assert variable.span == 2.0

    def test_a_declared_start_wins_over_the_model(self) -> None:
        variable = DesignVariable(name="r", lower=0.0, upper=10.0, initial=7.0)
        assert variable.start({"r": 2.0}) == 7.0

    def test_the_models_value_is_the_start_when_none_is_declared(self) -> None:
        variable = DesignVariable(name="r", lower=0.0, upper=10.0)
        assert variable.start({"r": 2.0}) == 2.0

    def test_no_value_anywhere_starts_in_the_middle(self) -> None:
        variable = DesignVariable(name="r", lower=2.0, upper=10.0)
        assert variable.start({}) == 6.0

    def test_a_model_value_outside_the_box_is_clamped(self) -> None:
        variable = DesignVariable(name="r", lower=2.0, upper=10.0)
        assert variable.start({"r": 50.0}) == 10.0


class TestTheObjectiveKnowsWhichWayIsBetter:
    def test_signum(self) -> None:
        assert Sense.MINIMISE.signum == 1.0
        assert Sense.MAXIMISE.signum == -1.0

    def test_a_maximisation_is_handed_to_the_driver_negated(self) -> None:
        assert Objective(measurement="stiffness", sense=Sense.MAXIMISE).driver_value(3.0) == -3.0
        assert Objective(measurement="mass_kg").driver_value(3.0) == 3.0

    @pytest.mark.parametrize("path", ["", "   "])
    def test_an_objective_with_nothing_to_measure_is_refused(self, path: str) -> None:
        with pytest.raises(ObjectiveError, match="needs something to measure"):
            Objective(measurement=path)

    def test_it_reads_as_a_sentence(self) -> None:
        assert str(Objective(measurement="mass_kg")) == "minimise mass_kg"


def _result(comparison: str, measured: float | None, expected: float | None, *,
            tolerance: float = 0.0, outcome: Outcome = Outcome.PASSED) -> AssertionResult:
    # An exact `==` on a measured number is refused without a tolerance, so the
    # equality rows carry one too small to move the margin.
    if comparison == "==" and tolerance == 0.0:
        tolerance = 1e-12
    assertion = Assertion(
        name="c", measure="m", comparison=comparison, bound=expected or 0.0, tolerance=tolerance
    )
    return AssertionResult(
        assertion=assertion, outcome=outcome, measured=measured, expected=expected
    )


class TestAMarginIsSignedRoom:
    @pytest.mark.parametrize(
        ("comparison", "measured", "expected", "room"),
        [
            ("<=", 8.0, 10.0, 2.0),
            ("<", 12.0, 10.0, -2.0),
            (">=", 12.0, 10.0, 2.0),
            (">", 8.0, 10.0, -2.0),
            ("==", 10.5, 10.0, -0.5),
        ],
    )
    def test_positive_means_satisfied_and_negative_means_violated(
        self, comparison: str, measured: float, expected: float, room: float
    ) -> None:
        assert margin(_result(comparison, measured, expected)) == pytest.approx(room)

    def test_a_tolerance_is_room(self) -> None:
        assert margin(_result("<=", 10.5, 10.0, tolerance=1.0)) == pytest.approx(0.5)
        assert margin(_result("==", 10.5, 10.0, tolerance=1.0)) == pytest.approx(0.5)

    def test_not_equal_is_satisfied_or_not_with_no_slope(self) -> None:
        assert margin(_result("!=", 11.0, 10.0)) == 1.0
        assert margin(_result("!=", 10.0, 10.0)) == -1.0

    def test_an_unmeasured_constraint_has_no_margin_not_a_zero_one(self) -> None:
        assert margin(_result("<=", 8.0, 10.0, outcome=Outcome.UNMEASURED)) is None
        assert margin(_result("<=", None, 10.0)) is None


class TestAProblemIsRefusedBeforeAnythingIsBuilt:
    def test_a_problem_needs_a_name(self) -> None:
        with pytest.raises(VariableError, match="needs a name"):
            _problem(name="  ")

    def test_a_problem_needs_something_to_change(self) -> None:
        with pytest.raises(VariableError, match="nothing to"):
            _problem(variables=())

    def test_one_dimension_declared_twice_is_refused(self) -> None:
        twice = (
            DesignVariable(name="w", lower=0.0, upper=1.0),
            DesignVariable(name="w", lower=0.0, upper=2.0),
        )
        with pytest.raises(VariableError, match="declared twice"):
            _problem(variables=twice)

    def test_a_budget_of_nothing_is_refused(self) -> None:
        with pytest.raises(VariableError, match="nothing\\s+would ever be built"):
            _problem(max_evaluations=0)

    @pytest.mark.parametrize("tolerance", [0.0, -1e-6])
    def test_an_unreachable_tolerance_is_refused(self, tolerance: float) -> None:
        with pytest.raises(VariableError, match="cannot be reached"):
            _problem(tolerance=tolerance)

    def test_of_builds_the_same_problem(self) -> None:
        built = OptimisationProblem.of(
            "plate",
            variables=[DesignVariable(name="w", lower=0.0, upper=1.0)],
            objective=Objective(measurement="mass_kg"),
        )
        assert built.variables[0].name == "w"
        assert built.max_evaluations == DEFAULT_MAX_EVALUATIONS


class TestTheStartSaysHowItWasChosen:
    def test_a_part_inside_its_bounds_starts_where_it_is_with_no_note(self) -> None:
        values, notes = _problem().start({"width_mm": 20.0, "thickness_mm": 2.0})
        assert values == {"width_mm": 20.0, "thickness_mm": 2.0}
        assert notes == ()

    def test_a_part_outside_its_bounds_is_moved_and_the_move_is_said(self) -> None:
        values, notes = _problem().start({"width_mm": 80.0, "thickness_mm": 2.0})
        assert values["width_mm"] == 50.0
        assert len(notes) == 1
        assert "width_mm" in notes[0] and "outside the declared bounds" in notes[0]

    def test_a_model_that_reports_nothing_starts_in_the_middle_and_says_so(self) -> None:
        values, notes = _problem().start({})
        assert values == {"width_mm": 30.0, "thickness_mm": 3.0}
        assert len(notes) == 2
        assert all("middle of its bounds" in note for note in notes)


class TestTheVectorOrderIsTheDeclaredOrder:
    def test_vector_and_named_are_inverses(self) -> None:
        problem = _problem()
        values = {"thickness_mm": 2.0, "width_mm": 20.0}
        assert problem.names == ("width_mm", "thickness_mm")
        assert problem.vector(values) == [20.0, 2.0]
        assert problem.named([20.0, 2.0]) == {"width_mm": 20.0, "thickness_mm": 2.0}
        assert problem.bounds == ((10.0, 50.0), (1.0, 5.0))

    def test_a_driver_that_steps_a_hair_outside_is_clamped_back(self) -> None:
        assert _problem().named([10.0 - 1e-16, 5.0 + 1e-12]) == {
            "width_mm": 10.0,
            "thickness_mm": 5.0,
        }

    def test_the_statement_reads_back_with_its_constraints(self) -> None:
        problem = _problem(
            constraints=(Assertion(name="stiff", measure="deflection_mm", comparison="<=", bound=0.8),)
        )
        text = str(problem)
        assert text.startswith("plate: minimise mass_kg over width_mm in [10, 50]")
        assert "subject to stiff: deflection_mm <= 0.8" in text
