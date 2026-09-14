"""Multi-objective trade-offs — `app/optimise/pareto.py`, master plan 10.3.

Checked against a front known in closed form. For f1 = x² and f2 = (x − 2)² over
x ∈ [−5, 5] the Pareto set is x ∈ [0, 2], so every point on the front satisfies
f2 = (√f1 − 2)², the extremes are (0, 4) and (4, 0), and nothing outside that
curve may appear on it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pytest

from app.design.assertions import Assertion
from app.optimise import AnalyticModel, DesignVariable, Objective, Sense, Stop, VariableError
from app.optimise.doe import full_factorial, run
from app.optimise.errors import ObjectiveError
from app.optimise.evaluate import Evaluation
from app.optimise.pareto import (
    OPTIMISED_STATEMENT,
    SAMPLED_STATEMENT,
    FrontPoint,
    ParetoFront,
    TradeOff,
    non_dominated,
    pareto_front,
    sampled_front,
)


def _two_bowls(response: Any = None, *, lower: float = -5.0) -> tuple[TradeOff, AnalyticModel]:
    model = AnalyticModel(
        response=response
        or (lambda v: {"f1": v["x"] ** 2, "f2": (v["x"] - 2.0) ** 2}),
        start={"x": 4.0},
    )
    trade = TradeOff(
        name="two bowls",
        variables=(DesignVariable(name="x", lower=lower, upper=5.0),),
        objectives=(Objective(measurement="f1"), Objective(measurement="f2")),
    )
    return trade, model


class TestTheFrontIsTheClosedFormCurve:
    def test_every_point_lies_on_it(self) -> None:
        trade, model = _two_bowls()
        front = pareto_front(trade, model, points=7)
        assert front.complete
        assert len(front.points) == 7
        for point in front.points:
            f1, f2 = point.objectives["f1"], point.objectives["f2"]
            assert f2 == pytest.approx((math.sqrt(f1) - 2.0) ** 2, abs=1e-4)
            assert -1e-4 <= point.values["x"] <= 2.0 + 1e-4

    def test_the_extremes_are_the_two_anchors(self) -> None:
        trade, model = _two_bowls()
        front = pareto_front(trade, model, points=5)
        first, last = front.points[0], front.points[-1]
        assert first.objectives["f1"] == pytest.approx(0.0, abs=1e-5)
        assert first.objectives["f2"] == pytest.approx(4.0, abs=1e-3)
        assert last.objectives["f1"] == pytest.approx(4.0, abs=1e-3)
        assert last.objectives["f2"] == pytest.approx(0.0, abs=1e-5)

    def test_points_are_ordered_along_the_first_objective(self) -> None:
        trade, model = _two_bowls()
        front = pareto_front(trade, model, points=6)
        f1 = [one.objectives["f1"] for one in front.points]
        assert f1 == sorted(f1)

    def test_a_maximised_objective_is_stepped_the_right_way(self) -> None:
        """Maximise g = −(x − 2)²: the same front with f2's sign flipped."""
        trade = TradeOff(
            name="mixed",
            variables=(DesignVariable(name="x", lower=-5.0, upper=5.0),),
            objectives=(
                Objective(measurement="f1"),
                Objective(measurement="g", sense=Sense.MAXIMISE),
            ),
        )
        model = AnalyticModel(
            response=lambda v: {"f1": v["x"] ** 2, "g": -((v["x"] - 2.0) ** 2)},
            start={"x": 4.0},
        )
        front = pareto_front(trade, model, points=5)
        assert front.complete
        # A bound written the wrong way round (g <= level) is met by f1's own
        # anchor, so every interior run collapses onto it and is dropped as a
        # duplicate: the curve check alone would still pass on the two anchors.
        assert len(front.points) == 5
        for point in front.points:
            assert -point.objectives["g"] == pytest.approx(
                (math.sqrt(point.objectives["f1"]) - 2.0) ** 2, abs=1e-4
            )

    def test_every_point_came_from_a_converged_rebuilt_run(self) -> None:
        trade, model = _two_bowls()
        front = pareto_front(trade, model, points=4)
        assert len(front.runs) == 4
        assert all(one.stop is Stop.CONVERGED for one in front.runs)
        assert front.evaluations == sum(len(one.log) for one in front.runs)
        assert front.statement == OPTIMISED_STATEMENT
        assert not front.sampled


class TestASubProblemThatFailsIsAGap:
    def test_a_region_that_will_not_build_leaves_a_gap_not_a_point(self) -> None:
        """Nothing builds for x in (0.5, 1.5): the epsilon runs aimed there fail."""

        def holed(values: Mapping[str, float]) -> Mapping[str, Any]:
            x = values["x"]
            if 0.5 < x < 1.5:
                raise ValueError("the rib and the pocket collide here")
            return {"f1": x**2, "f2": (x - 2.0) ** 2}

        trade, model = _two_bowls(holed)
        front = pareto_front(trade, model, points=9)
        for point in front.points:
            assert not 0.5 < point.values["x"] < 1.5
        # Whatever did not converge is reported, never drawn.
        assert len(front.points) + len(front.gaps) + front.dominated == len(front.runs)
        for gap in front.gaps:
            assert gap.stop is not Stop.CONVERGED
            assert gap.message

    def test_a_missing_anchor_stops_before_any_interior_run(self) -> None:
        def no_f2(values: Mapping[str, float]) -> Mapping[str, Any]:
            return {"f1": values["x"] ** 2}

        trade, model = _two_bowls(no_f2)
        front = pareto_front(trade, model, points=5)
        assert not front.complete
        assert len(front.runs) == 2
        assert any("no interior points were attempted" in note for note in front.notes)


class TestDominatedPointsAreDroppedAndCounted:
    @staticmethod
    def _point(f1: float, f2: float) -> FrontPoint:
        evaluation = Evaluation(index=0, values={"x": 0.0}, built=True, objective=f1)
        return FrontPoint(evaluation=evaluation, objectives={"f1": f1, "f2": f2}, source="test")

    def test_the_filter(self) -> None:
        objectives = (Objective(measurement="f1"), Objective(measurement="f2"))
        points = [
            self._point(1.0, 5.0),
            self._point(2.0, 3.0),
            self._point(2.5, 3.5),  # beaten by (2, 3)
            self._point(4.0, 1.0),
            self._point(2.0, 3.0 + 1e-12),  # the same point twice, within tolerance
        ]
        kept, dropped = non_dominated(points, objectives)
        assert [(one.objectives["f1"], one.objectives["f2"]) for one in kept] == [
            (1.0, 5.0),
            (2.0, 3.0),
            (4.0, 1.0),
        ]
        assert dropped == 2

    def test_sense_is_respected(self) -> None:
        objectives = (Objective(measurement="f1"), Objective(measurement="f2", sense=Sense.MAXIMISE))
        kept, dropped = non_dominated(
            [self._point(1.0, 5.0), self._point(2.0, 3.0)], objectives
        )
        assert len(kept) == 1 and kept[0].objectives == {"f1": 1.0, "f2": 5.0}
        assert dropped == 1


class TestATradeOffIsRefusedWhenItIsNotOne:
    def test_one_objective(self) -> None:
        with pytest.raises(ObjectiveError, match="at least two objectives"):
            TradeOff(
                name="t",
                variables=(DesignVariable(name="x", lower=0.0, upper=1.0),),
                objectives=(Objective(measurement="f1"),),
            )

    def test_four_objectives(self) -> None:
        with pytest.raises(ObjectiveError, match="at most 3"):
            TradeOff(
                name="t",
                variables=(DesignVariable(name="x", lower=0.0, upper=1.0),),
                objectives=tuple(Objective(measurement=f"f{i}") for i in range(4)),
            )

    def test_one_measurement_twice(self) -> None:
        with pytest.raises(ObjectiveError, match="named twice"):
            TradeOff(
                name="t",
                variables=(DesignVariable(name="x", lower=0.0, upper=1.0),),
                objectives=(Objective(measurement="f1"), Objective(measurement="f1", sense=Sense.MAXIMISE)),
            )

    def test_a_front_of_only_its_extremes(self) -> None:
        trade, model = _two_bowls()
        with pytest.raises(VariableError, match="at least 3"):
            pareto_front(trade, model, points=2)

    def test_the_result_has_no_single_answer(self) -> None:
        for forbidden in ("best", "optimum", "solution", "winner", "weights"):
            assert not hasattr(ParetoFront, forbidden)


class TestAThreeWayFront:
    def test_every_point_is_non_dominated_and_feasible(self) -> None:
        """Three bowls centred at three corners of a triangle; the front is its interior."""
        centres = {"a": (0.0, 0.0), "b": (2.0, 0.0), "c": (0.0, 2.0)}

        def bowls(values: Mapping[str, float]) -> Mapping[str, Any]:
            return {
                name: (values["x"] - cx) ** 2 + (values["y"] - cy) ** 2
                for name, (cx, cy) in centres.items()
            }

        trade = TradeOff(
            name="three",
            variables=(
                DesignVariable(name="x", lower=-3.0, upper=3.0),
                DesignVariable(name="y", lower=-3.0, upper=3.0),
            ),
            objectives=tuple(Objective(measurement=name) for name in centres),
        )
        front = pareto_front(trade, AnalyticModel(response=bowls, start={"x": 1.0, "y": 1.0}), points=4)
        assert len(front.runs) == 3 + 2 * 2
        assert front.points
        for point in front.points:
            x, y = point.values["x"], point.values["y"]
            # The Pareto set of three isotropic bowls is their centres' convex hull.
            assert x >= -1e-3 and y >= -1e-3 and x + y <= 2.0 + 1e-3


class TestASampledFrontSaysItWasNotOptimised:
    def test_the_survey_front_is_non_dominated_and_labelled(self) -> None:
        trade, model = _two_bowls()
        problem = trade.problem_for(trade.objectives[0], ())
        experiment = run(full_factorial(problem, 21), model)
        front = sampled_front(experiment, trade.objectives)
        assert front.sampled
        assert front.statement == SAMPLED_STATEMENT
        xs = sorted(one.values["x"] for one in front.points)
        assert xs == pytest.approx([0.0, 0.5, 1.0, 1.5, 2.0])
        assert front.dominated == 21 - 5

    def test_an_infeasible_survey_point_is_not_on_it(self) -> None:
        trade, model = _two_bowls()
        problem = trade.problem_for(
            trade.objectives[0],
            (Assertion(name="keep x small", measure="f1", comparison="<=", bound=1.0),),
        )
        experiment = run(full_factorial(problem, 21), model)
        front = sampled_front(experiment, trade.objectives)
        assert all(one.objectives["f1"] <= 1.0 for one in front.points)
