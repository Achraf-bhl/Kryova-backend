"""The surrogate flywheel — `app/optimise/flywheel.py` and `screening.py`, master plan E10.4.

The surrogate is trained on **real solves**: bars pulled in tension through
`LinearStaticSolver` on exact box meshes, whose extension is δ = FL/(E·b·h) in
closed form. A power law is exact for that family, so the fitted exponents must
come back as the physics — +1 on force and length, −1 on modulus, width and
height — and the leave-one-out band must be round-off. Then the rule is pinned:
it may rank, the decision spends real solves, and nothing it returns is a verdict.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pytest

from app.mesh.primitives import box_mesh
from app.optimise import (
    AnalyticModel,
    DesignVariable,
    Objective,
    OptimisationProblem,
    VariableError,
)
from app.optimise.flywheel import Datapoint, PowerLawSurrogate, SurrogateError
from app.optimise.screening import MAY_RANK_NEVER_DECIDE, VERIFY_PURPOSE, Estimate, Ranking, screen
from app.solve.linear_static import LinearStaticSolver
from app.solve.materials import MATERIALS
from tests.test_solver import uniaxial_case

FEATURES = ("applied_force_n", "bounding_box_z_mm", "youngs_modulus_mpa", "bounding_box_x_mm", "bounding_box_y_mm")
EXPECTED = {
    "applied_force_n": 1.0,
    "bounding_box_z_mm": 1.0,
    "youngs_modulus_mpa": -1.0,
    "bounding_box_x_mm": -1.0,
    "bounding_box_y_mm": -1.0,
}


def _solve_bar(force: float, length: float, modulus: float, width: float, height: float) -> float:
    """Tip extension of a bar solved by the in-house solver, pulled along z."""
    material = MATERIALS["steel-1018"].model_copy(update={"youngs_modulus_mpa": modulus})
    mesh = box_mesh((width, height, length), divisions=(1, 1, 3))
    output = LinearStaticSolver().solve(mesh, uniaxial_case(material, force))
    return float(output.result.max_displacement_mm)


def _features(force: float, length: float, modulus: float, width: float, height: float) -> dict[str, float]:
    return {
        "applied_force_n": force,
        "bounding_box_z_mm": length,
        "youngs_modulus_mpa": modulus,
        "bounding_box_x_mm": width,
        "bounding_box_y_mm": height,
    }


@pytest.fixture(scope="module")
def solved() -> list[Datapoint]:
    """32 real solves over a 2⁵ grid of force, length, modulus, width and height."""
    points = []
    grid = itertools.product((500.0, 2000.0), (60.0, 150.0), (70_000.0, 205_000.0), (6.0, 14.0), (5.0, 11.0))
    for index, (force, length, modulus, width, height) in enumerate(grid):
        points.append(
            Datapoint(
                source=f"bar-{index}",
                features=_features(force, length, modulus, width, height),
                response=_solve_bar(force, length, modulus, width, height),
                solver="internal",
                solver_version="linear-static",
            )
        )
    return points


class TestTheSolvesAreTheClosedForm:
    def test_a_bar_extends_by_fl_over_ea(self) -> None:
        # max |u| includes the Poisson contraction's lateral component, which is
        # second order here; the axial term dominates to well under a percent.
        assert _solve_bar(1000.0, 100.0, 200_000.0, 10.0, 10.0) == pytest.approx(
            1000.0 * 100.0 / (200_000.0 * 100.0), rel=5e-3
        )


class TestTheSurrogateLearnsThePhysics:
    def test_the_exponents_are_the_closed_forms(self, solved: list[Datapoint]) -> None:
        surrogate = PowerLawSurrogate.train(solved, FEATURES, response="max_displacement_mm")
        for name, exponent in EXPECTED.items():
            assert surrogate.exponents[name] == pytest.approx(exponent, abs=0.02), name

    def test_its_prediction_at_an_unsolved_bar_is_within_its_own_band(self, solved: list[Datapoint]) -> None:
        surrogate = PowerLawSurrogate.train(solved, FEATURES, response="max_displacement_mm")
        features = _features(1200.0, 100.0, 120_000.0, 9.0, 8.0)
        estimate = surrogate.predict(features)
        truth = _solve_bar(1200.0, 100.0, 120_000.0, 9.0, 8.0)
        assert not estimate.extrapolated
        assert estimate.provenance == "approximated"
        assert estimate.low / 1.02 <= truth <= estimate.high * 1.02
        assert surrogate.error_factor < 1.05

    def test_it_says_what_it_was_trained_on(self, solved: list[Datapoint]) -> None:
        surrogate = PowerLawSurrogate.train(solved, FEATURES, response="max_displacement_mm")
        assert surrogate.samples == 32
        assert surrogate.single_grid == 32
        assert surrogate.solvers == ("internal linear-static",)
        text = surrogate.summary()
        assert "32 single-grid" not in text or "32 of the runs are single-grid" in text
        assert "32 of the runs are single-grid" in text
        assert "32 solved runs" in surrogate.predict(_features(1000.0, 100.0, 100_000.0, 8.0, 8.0)).basis

    def test_a_point_outside_the_trained_range_is_flagged(self, solved: list[Datapoint]) -> None:
        surrogate = PowerLawSurrogate.train(solved, FEATURES)
        estimate = surrogate.predict(_features(1000.0, 400.0, 100_000.0, 8.0, 8.0))
        assert estimate.extrapolated
        assert "bounding_box_z_mm" in estimate.note


class TestTheErrorBasisIsHonest:
    def test_leave_one_out_matches_refitting_without_each_point(self) -> None:
        """On data a power law does not fit exactly, the hat-matrix shortcut must
        equal brute-force refits — the band is only as honest as this number."""
        rng = np.random.default_rng(7)
        points = []
        for index in range(14):
            a, b = rng.uniform(1.0, 10.0, size=2)
            response = a**1.5 / b * (1.0 + 0.2 * math.sin(3.0 * a + b))
            points.append(Datapoint(source=str(index), features={"a": a, "b": b}, response=response))
        surrogate = PowerLawSurrogate.train(points, ("a", "b"))
        errors = []
        for index, left_out in enumerate(points):
            refit = PowerLawSurrogate.train(points[:index] + points[index + 1 :], ("a", "b"))
            errors.append(math.log(left_out.response) - math.log(refit.predict(left_out.features).value))
        assert surrogate.loo_rms_log_error == pytest.approx(float(np.sqrt(np.mean(np.square(errors)))), rel=1e-8)
        assert surrogate.loo_max_log_error == pytest.approx(float(np.max(np.abs(errors))), rel=1e-8)
        assert surrogate.error_factor > 1.01, "the data were chosen so that the fit is not exact"


class TestItRefusesToTrainOnWhatWouldMislead:
    def test_too_few_datapoints(self, solved: list[Datapoint]) -> None:
        with pytest.raises(SurrogateError, match="need at least 8 datapoints"):
            PowerLawSurrogate.train(solved[:7], FEATURES)

    def test_a_feature_that_never_varies(self, solved: list[Datapoint]) -> None:
        same_force = [one for one in solved if one.features["applied_force_n"] == 500.0]
        with pytest.raises(SurrogateError, match="same applied_force_n"):
            PowerLawSurrogate.train(same_force, FEATURES)

    def test_features_that_move_together(self) -> None:
        points = [
            Datapoint(source=str(i), features={"a": float(i + 1), "b": float(i + 1) ** 2}, response=float(i + 3))
            for i in range(8)
        ]
        with pytest.raises(SurrogateError, match="move together"):
            PowerLawSurrogate.train(points, ("a", "b"))

    def test_a_zero_response_is_not_a_datapoint(self, solved: list[Datapoint]) -> None:
        broken = [*solved[:-1], Datapoint(source="stuck", features=solved[-1].features, response=0.0)]
        with pytest.raises(SurrogateError, match="stuck.*fixture or load problem"):
            PowerLawSurrogate.train(broken, FEATURES)

    def test_a_non_positive_feature(self, solved: list[Datapoint]) -> None:
        features = dict(solved[-1].features) | {"bounding_box_x_mm": 0.0}
        broken = [*solved[:-1], Datapoint(source="flat", features=features, response=1.0)]
        with pytest.raises(SurrogateError, match="flat has bounding_box_x_mm = 0"):
            PowerLawSurrogate.train(broken, FEATURES)

    def test_a_prediction_missing_a_feature(self, solved: list[Datapoint]) -> None:
        surrogate = PowerLawSurrogate.train(solved, FEATURES)
        with pytest.raises(VariableError, match="needs"):
            surrogate.predict({"applied_force_n": 1.0})


class _BarModel:
    """The surrogate's features as design variables, answered by the real solver."""

    backend = "internal linear-static"

    def __init__(self) -> None:
        self.solves = 0

    def baseline(self) -> Mapping[str, float]:
        return {}

    def evaluate(self, values: Mapping[str, float]) -> Mapping[str, Any]:
        self.solves += 1
        extension = _solve_bar(1000.0, 100.0, 205_000.0, values["bounding_box_x_mm"], values["bounding_box_y_mm"])
        return {
            "max_displacement_mm": extension,
            "area_mm2": values["bounding_box_x_mm"] * values["bounding_box_y_mm"],
        }


class _FixedFeatures:
    """Adapts the surrogate to candidates that set only the section."""

    def __init__(self, surrogate: PowerLawSurrogate) -> None:
        self.surrogate = surrogate

    def predict(self, values: Mapping[str, float]) -> Estimate:
        return self.surrogate.predict(
            _features(1000.0, 100.0, 205_000.0, values["bounding_box_x_mm"], values["bounding_box_y_mm"])
        )


class TestTheDecisionSpendsARealSolve:
    def test_screening_rebuilds_the_top_candidates_and_judges_on_measurements(
        self, solved: list[Datapoint]
    ) -> None:
        from app.design.assertions import Assertion

        surrogate = PowerLawSurrogate.train(solved, FEATURES)
        problem = OptimisationProblem(
            name="lightest stiff section",
            variables=(
                DesignVariable(name="bounding_box_x_mm", lower=6.0, upper=14.0),
                DesignVariable(name="bounding_box_y_mm", lower=5.0, upper=11.0),
            ),
            objective=Objective(measurement="max_displacement_mm"),
            constraints=(Assertion(name="material", measure="area_mm2", comparison="<=", bound=90.0),),
        )
        candidates = [
            {"bounding_box_x_mm": float(w), "bounding_box_y_mm": float(h)}
            for w, h in itertools.product(np.linspace(6.0, 14.0, 9), np.linspace(5.0, 11.0, 7))
        ]
        model = _BarModel()
        screening = screen(problem, model, _FixedFeatures(surrogate), candidates, verify_top=4)

        assert model.solves == 4, "only the top candidates are solved"
        assert all(one.purpose == VERIFY_PURPOSE for one in screening.log.evaluations)
        best = screening.best_measured
        # The stiffest sections by estimate break the area limit; the measured,
        # constraint-checked best is what is returned, never the top of the ranking.
        assert best is None or best.feasible
        assert screening.ranking.statement == MAY_RANK_NEVER_DECIDE
        assert MAY_RANK_NEVER_DECIDE in screening.summary()

    def test_a_ranking_has_no_way_to_decide(self) -> None:
        for forbidden in ("best", "winner", "optimum", "accepted", "passed"):
            assert not hasattr(Ranking, forbidden)
        for forbidden in ("measured", "passed", "feasible"):
            assert not hasattr(Estimate, forbidden)

    def test_the_best_measured_comes_from_the_rebuild_not_the_estimate(self) -> None:
        """An estimator that is confidently wrong about the order: the measurement wins."""

        class Backwards:
            def predict(self, values: Mapping[str, float]) -> Estimate:
                value = -values["x"]
                return Estimate(value=value, low=value, high=value, basis="a test double", extrapolated=False)

        problem = OptimisationProblem(
            name="closest to 3",
            variables=(DesignVariable(name="x", lower=0.0, upper=10.0),),
            objective=Objective(measurement="miss"),
        )
        model = AnalyticModel(response=lambda v: {"miss": abs(v["x"] - 3.0)})
        screening = screen(problem, model, Backwards(), [{"x": 3.0}, {"x": 9.0}, {"x": 10.0}], verify_top=3)
        assert screening.ranking.order[0].candidate == {"x": 10.0}
        best = screening.best_measured
        assert best is not None and best.values == {"x": 3.0}
