"""Response surfaces — `app/optimise/surface.py`, master plan 10.3.

Checked against functions whose coefficients are known: a quadratic surface fitted
to an exact quadratic must reproduce it to round-off with a leave-one-out error of
round-off, and one fitted to a function it cannot represent must *say* so in its
error basis. The refusals are the fits that would succeed and mislead.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pytest

from app.optimise import (
    AnalyticModel,
    DesignVariable,
    Objective,
    OptimisationProblem,
    Sense,
    VariableError,
)
from app.optimise.doe import full_factorial, latin_hypercube, run
from app.optimise.screening import APPROXIMATED, MAY_RANK_NEVER_DECIDE, Ranking, rank
from app.optimise.surface import ResponseSurface, SurfaceError


def _problem(budget: int = 400) -> OptimisationProblem:
    return OptimisationProblem(
        name="bowl",
        variables=(
            DesignVariable(name="x", lower=-2.0, upper=4.0),
            DesignVariable(name="y", lower=10.0, upper=30.0),
        ),
        objective=Objective(measurement="f"),
        max_evaluations=budget,
    )


def _quadratic(values: Mapping[str, float]) -> Mapping[str, Any]:
    x, y = values["x"], values["y"]
    return {"f": 3.0 + 2.0 * x - 0.5 * y + 1.5 * x * x + 0.02 * x * y + 0.01 * y * y}


def _survey(response: Any, *, levels: int = 4) -> Any:
    problem = _problem()
    return problem, run(full_factorial(problem, levels), AnalyticModel(response=response))


class TestAQuadraticIsReproducedExactly:
    def test_predictions_at_unseen_points_match_the_function(self) -> None:
        problem, experiment = _survey(_quadratic)
        surface = ResponseSurface.fit(problem, experiment.log.evaluations)
        for x, y in [(-1.3, 12.2), (0.7, 25.0), (3.9, 29.1)]:
            estimate = surface.predict({"x": x, "y": y})
            assert estimate.value == pytest.approx(_quadratic({"x": x, "y": y})["f"], rel=1e-10)
            assert estimate.provenance == APPROXIMATED
            assert not estimate.extrapolated

    def test_its_error_basis_is_round_off(self) -> None:
        problem, experiment = _survey(_quadratic)
        surface = ResponseSurface.fit(problem, experiment.log.evaluations)
        assert surface.r_squared == pytest.approx(1.0, abs=1e-12)
        assert surface.loo_rms_error < 1e-9
        assert surface.samples == 16
        assert len(surface.terms) == 6

    def test_a_linear_surface_on_a_linear_function(self) -> None:
        problem, experiment = _survey(lambda v: {"f": 7.0 - 3.0 * v["x"] + 0.25 * v["y"]}, levels=3)
        surface = ResponseSurface.fit(problem, experiment.log.evaluations, degree=1)
        assert surface.predict({"x": 1.0, "y": 20.0}).value == pytest.approx(9.0, rel=1e-12)


class TestTheErrorBasisIsHonestWhereTheFitIsNot:
    def test_leave_one_out_matches_refitting_without_each_point(self) -> None:
        """The hat-matrix shortcut is exact; check it against brute force."""
        problem = _problem()

        def wavy(values: Mapping[str, float]) -> Mapping[str, Any]:
            return {"f": math.sin(values["x"]) * values["y"]}

        experiment = run(latin_hypercube(problem, 25, seed=5), AnalyticModel(response=wavy))
        surface = ResponseSurface.fit(problem, experiment.log.evaluations)
        errors = []
        evaluations = list(experiment.log.evaluations)
        for index, left_out in enumerate(evaluations):
            rest = evaluations[:index] + evaluations[index + 1 :]
            refit = ResponseSurface.fit(problem, rest)
            predicted = refit.predict(left_out.values).value
            errors.append(float(left_out.objective or 0.0) - predicted)
        assert surface.loo_rms_error == pytest.approx(float(np.sqrt(np.mean(np.square(errors)))), rel=1e-8)
        assert surface.loo_max_error == pytest.approx(float(np.max(np.abs(errors))), rel=1e-8)

    def test_a_function_the_polynomial_cannot_follow_reports_a_large_error(self) -> None:
        problem = _problem()

        def wavy(values: Mapping[str, float]) -> Mapping[str, Any]:
            return {"f": math.sin(3.0 * values["x"]) * values["y"]}

        experiment = run(latin_hypercube(problem, 30, seed=2), AnalyticModel(response=wavy))
        surface = ResponseSurface.fit(problem, experiment.log.evaluations)
        spread = float(np.std([one.objective for one in experiment.log.evaluations]))
        # R² on the fitted points flatters the fit; the error basis does not.
        assert surface.loo_rms_error > 0.5 * spread


class TestFitsThatWouldMisleadAreRefused:
    def test_too_few_points_to_estimate_its_own_error(self) -> None:
        problem, experiment = _survey(_quadratic, levels=2)
        with pytest.raises(SurfaceError, match="needs at least 8 measured points"):
            ResponseSurface.fit(problem, experiment.log.evaluations)

    def test_a_design_that_cannot_see_curvature(self) -> None:
        """Enough points (32 for 21 terms), but two levels: every squared column is constant."""
        problem = OptimisationProblem(
            name="flat",
            variables=tuple(
                DesignVariable(name=name, lower=0.0, upper=1.0) for name in ("x", "y", "z", "w", "v")
            ),
            objective=Objective(measurement="f"),
        )
        experiment = run(
            full_factorial(problem, 2),
            AnalyticModel(response=lambda v: {"f": v["x"] + v["y"] * v["z"] - v["w"] + v["v"]}),
        )
        with pytest.raises(SurfaceError, match="cannot tell the 21 terms"):
            ResponseSurface.fit(problem, experiment.log.evaluations)

    def test_an_unsupported_degree(self) -> None:
        problem, experiment = _survey(_quadratic)
        with pytest.raises(SurfaceError, match="degree-3"):
            ResponseSurface.fit(problem, experiment.log.evaluations, degree=3)

    def test_a_point_outside_the_design_bounds_is_refused(self) -> None:
        problem, experiment = _survey(_quadratic)
        surface = ResponseSurface.fit(problem, experiment.log.evaluations)
        with pytest.raises(VariableError, match="outside its design bounds"):
            surface.predict({"x": 5.0, "y": 20.0})
        with pytest.raises(VariableError, match="needs a value"):
            surface.predict({"x": 1.0})


class TestWhatWasLeftOutIsSaid:
    def test_unbuilt_and_unmeasured_points_are_excluded_by_name(self) -> None:
        def patchy(values: Mapping[str, float]) -> Mapping[str, Any]:
            if values["x"] == 4.0 and values["y"] == 30.0:
                raise ValueError("the fillet cannot be carried here")
            if values["x"] == -2.0 and values["y"] == 10.0:
                return {"other": 1.0}
            return _quadratic(values)

        problem, experiment = _survey(patchy)
        surface = ResponseSurface.fit(problem, experiment.log.evaluations)
        assert surface.samples == 14
        assert len(surface.excluded) == 2
        assert any("did not build" in one for one in surface.excluded)
        assert any("reported no finite 'f'" in one for one in surface.excluded)
        assert "2 surveyed point(s) left out" in surface.summary()

    def test_a_point_outside_the_sampled_range_is_flagged_extrapolated(self) -> None:
        problem = _problem()
        experiment = run(latin_hypercube(problem, 20, seed=9), AnalyticModel(response=_quadratic))
        surface = ResponseSurface.fit(problem, experiment.log.evaluations)
        low_x = surface.sampled[0][0]
        assert low_x > -2.0
        estimate = surface.predict({"x": -2.0, "y": 20.0})
        assert estimate.extrapolated
        assert "outside the surveyed range in x" in estimate.note


class TestARankingMayRankAndNeverDecide:
    def test_it_orders_by_the_estimate(self) -> None:
        problem, experiment = _survey(_quadratic)
        surface = ResponseSurface.fit(problem, experiment.log.evaluations)
        candidates = [{"x": 3.0, "y": 20.0}, {"x": -0.5, "y": 20.0}, {"x": 1.0, "y": 20.0}]
        ranking = rank(surface, candidates, verify_top=2)
        order = [one.candidate["x"] for one in ranking.order]
        truth = sorted(candidates, key=lambda c: _quadratic(c)["f"])
        assert order == [one["x"] for one in truth]
        assert ranking.to_verify == tuple(truth[:2])
        assert ranking.statement == MAY_RANK_NEVER_DECIDE

        maximised = rank(surface, candidates, sense=Sense.MAXIMISE)
        assert [one.candidate["x"] for one in maximised.order] == list(reversed(order))

    def test_it_has_no_way_to_name_a_winner(self) -> None:
        for forbidden in ("best", "winner", "optimum", "decision", "accepted"):
            assert not hasattr(Ranking, forbidden)

    def test_it_always_sends_something_to_be_rebuilt(self) -> None:
        problem, experiment = _survey(_quadratic)
        surface = ResponseSurface.fit(problem, experiment.log.evaluations)
        with pytest.raises(VariableError, match="at least 1"):
            rank(surface, [{"x": 0.0, "y": 20.0}], verify_top=0)

    def test_two_candidates_closer_than_the_error_basis_are_indistinguishable(self) -> None:
        problem = _problem()

        def noisy(values: Mapping[str, float]) -> Mapping[str, Any]:
            return {"f": math.sin(3.0 * values["x"]) * values["y"]}

        experiment = run(latin_hypercube(problem, 30, seed=2), AnalyticModel(response=noisy))
        surface = ResponseSurface.fit(problem, experiment.log.evaluations)
        close = rank(surface, [{"x": 1.0, "y": 20.0}, {"x": 1.001, "y": 20.0}])
        assert close.indistinguishable

    def test_well_separated_candidates_on_an_exact_fit_are_distinguishable(self) -> None:
        problem, experiment = _survey(_quadratic)
        surface = ResponseSurface.fit(problem, experiment.log.evaluations)
        apart = rank(surface, [{"x": 3.0, "y": 20.0}, {"x": -0.5, "y": 20.0}])
        assert not apart.indistinguishable

    def test_the_band_is_the_leave_one_out_error(self) -> None:
        problem = _problem()
        experiment = run(
            latin_hypercube(problem, 30, seed=2),
            AnalyticModel(response=lambda v: {"f": math.sin(3.0 * v["x"]) * v["y"]}),
        )
        surface = ResponseSurface.fit(problem, experiment.log.evaluations)
        estimate = surface.predict({"x": 1.0, "y": 20.0})
        assert estimate.high - estimate.value == pytest.approx(surface.loo_rms_error)
        assert estimate.value - estimate.low == pytest.approx(surface.loo_rms_error)
        assert "leave-one-out" in estimate.basis
