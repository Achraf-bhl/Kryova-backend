"""Designs of experiments — `app/optimise/doe.py`, master plan 10.3.

A survey chooses its points before it sees an answer. What is pinned: the
factorial is every combination with the bounds included, the hypercube really
is one point per stratum in every projection, the seed reproduces the plan, and
a survey larger than its budget is refused rather than quietly shortened.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pytest

from app.optimise import (
    AnalyticModel,
    DesignVariable,
    Objective,
    OptimisationProblem,
    VariableError,
)
from app.optimise.doe import PURPOSE, Sampling, full_factorial, latin_hypercube, run


def _problem(*, budget: int = 200, variables: int = 2) -> OptimisationProblem:
    bounds = [(0.0, 10.0), (-2.0, 2.0), (100.0, 400.0)]
    return OptimisationProblem(
        name="survey",
        variables=tuple(
            DesignVariable(name=f"v{index}", lower=low, upper=high)
            for index, (low, high) in enumerate(bounds[:variables])
        ),
        objective=Objective(measurement="y"),
        max_evaluations=budget,
    )


class TestAFullFactorialIsEveryCombination:
    def test_the_count_and_the_bounds(self) -> None:
        plan = full_factorial(_problem(), 3)
        assert plan.method is Sampling.FULL_FACTORIAL
        assert len(plan) == 9
        assert {one["v0"] for one in plan.points} == {0.0, 5.0, 10.0}
        assert {one["v1"] for one in plan.points} == {-2.0, 0.0, 2.0}
        assert len({(one["v0"], one["v1"]) for one in plan.points}) == 9

    def test_levels_may_differ_per_variable(self) -> None:
        plan = full_factorial(_problem(), {"v0": 2, "v1": 4})
        assert len(plan) == 8
        assert plan.describe() == "full factorial over v0, v1: 2 × 4 = 8 points"

    def test_one_level_is_refused(self) -> None:
        with pytest.raises(VariableError, match="at least 2"):
            full_factorial(_problem(), {"v0": 1, "v1": 3})

    def test_an_unknown_or_missing_variable_is_refused(self) -> None:
        with pytest.raises(VariableError, match="not design"):
            full_factorial(_problem(), {"v0": 2, "v1": 2, "width": 3})
        with pytest.raises(VariableError, match="no level count"):
            full_factorial(_problem(), {"v0": 2})

    def test_a_survey_over_budget_is_refused_not_truncated(self) -> None:
        with pytest.raises(VariableError, match="never truncated") as refused:
            full_factorial(_problem(budget=20), 5)
        assert "25 rebuilds" in str(refused.value)


class TestALatinHypercubeFillsEveryStratum:
    @pytest.mark.parametrize("samples", [2, 7, 40])
    def test_each_projection_has_one_point_per_stratum(self, samples: int) -> None:
        problem = _problem(variables=3)
        plan = latin_hypercube(problem, samples, seed=11)
        assert plan.method is Sampling.LATIN_HYPERCUBE
        assert len(plan) == samples
        for variable in problem.variables:
            unit = np.array([(one[variable.name] - variable.lower) / variable.span for one in plan.points])
            strata = np.minimum((unit * samples).astype(int), samples - 1)
            assert sorted(strata.tolist()) == list(range(samples))
            assert np.all((unit >= 0.0) & (unit <= 1.0))

    def test_the_seed_reproduces_the_plan_and_another_seed_does_not(self) -> None:
        problem = _problem()
        first = latin_hypercube(problem, 12, seed=3)
        assert first.points == latin_hypercube(problem, 12, seed=3).points
        assert first.points != latin_hypercube(problem, 12, seed=4).points
        assert first.seed == 3
        assert first.to_dict()["seed"] == 3

    def test_the_seed_is_not_optional(self) -> None:
        with pytest.raises(TypeError):
            latin_hypercube(_problem(), 5)  # type: ignore[call-arg]

    def test_a_single_point_is_refused(self) -> None:
        with pytest.raises(VariableError, match="at least 2"):
            latin_hypercube(_problem(), 1, seed=0)

    def test_over_budget_is_refused(self) -> None:
        with pytest.raises(VariableError, match="never truncated"):
            latin_hypercube(_problem(budget=10), 11, seed=0)


class TestEveryPointIsBuiltAndRecorded:
    def test_failures_are_in_the_record_with_their_reason(self) -> None:
        def response(values: Mapping[str, float]) -> Mapping[str, Any]:
            if values["v0"] > 7.0:
                raise ValueError("the wall breaks through above 7 mm")
            return {"y": values["v0"] + values["v1"]}

        plan = full_factorial(_problem(), 3)
        experiment = run(plan, AnalyticModel(response=response))
        assert len(experiment.log) == 9
        assert len(experiment.failures()) == 3
        assert len(experiment.measured()) == 6
        assert all(one.purpose == PURPOSE for one in experiment.log.evaluations)
        assert all("breaks through" in one.reason for one in experiment.failures())
        assert experiment.summary().endswith("6 of 9 built, 6 feasible.")
