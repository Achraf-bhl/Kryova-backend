"""The optimisers behind one seam — `app/optimise/drivers.py` and `run.py`, master plan 10.3.

Every closed-form case has an optimum known by algebra, so the driver is checked
against arithmetic rather than against what it printed last time. The two
drivers are held to the *same* answers: a seam whose two sides disagree is two
products.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from app.design.assertions import Assertion
from app.optimise import (
    OPENMDAO,
    SCIPY,
    AnalyticModel,
    DesignVariable,
    DriverOutcome,
    DriverUnavailable,
    Evaluator,
    Objective,
    OpenMdaoDriver,
    OptimisationProblem,
    ScipyDriver,
    Sense,
    Stop,
    driver_named,
    optimise,
)
from app.optimise import drivers as drivers_module
from app.optimise.drivers import _numerical_gradient, is_close
from app.optimise.run import SAME_POINT, _same_point


def _bowl(*, sense: Sense = Sense.MINIMISE, constrained: bool = False) -> tuple[
    OptimisationProblem, AnalyticModel
]:
    """(x − 3)² + (y + 1)², or its negative under MAXIMISE.

    Unconstrained optimum (3, −1). With x + y >= 4 the optimum moves onto the
    constraint: minimise over x + y = 4 by Lagrange gives x − 3 = y + 1, so
    x = 4, y = 0, objective 2.
    """
    sign = 1.0 if sense is Sense.MINIMISE else -1.0
    model = AnalyticModel(
        response=lambda v: {
            "bowl": sign * ((v["x"] - 3.0) ** 2 + (v["y"] + 1.0) ** 2),
            "total": v["x"] + v["y"],
        },
        start={"x": 0.0, "y": 0.0},
    )
    problem = OptimisationProblem(
        name="bowl",
        variables=(
            DesignVariable(name="x", lower=-10.0, upper=10.0),
            DesignVariable(name="y", lower=-10.0, upper=10.0),
        ),
        objective=Objective(measurement="bowl", sense=sense),
        constraints=(
            (Assertion(name="total", measure="total", comparison=">=", bound=4.0),)
            if constrained
            else ()
        ),
    )
    return problem, model


class TestADriverIsChosenByNameOrRefused:
    def test_no_name_is_scipy(self) -> None:
        assert isinstance(driver_named(None), ScipyDriver)

    def test_names_are_case_and_space_insensitive(self) -> None:
        assert isinstance(driver_named("  SciPy "), ScipyDriver)
        assert isinstance(driver_named("OPENMDAO"), OpenMdaoDriver)

    def test_an_unknown_name_is_refused_listing_the_known_ones(self) -> None:
        with pytest.raises(DriverUnavailable, match="not an optimiser this build knows") as refused:
            driver_named("genetic")
        assert SCIPY in str(refused.value) and OPENMDAO in str(refused.value)

    def test_a_driver_object_passes_through(self) -> None:
        driver = ScipyDriver(method="Powell")
        assert driver_named(driver) is driver


class TestScipyPicksAMethodTheProblemCanUse:
    def test_constraints_mean_slsqp_and_bounds_alone_mean_lbfgsb(self) -> None:
        constrained, model = _bowl(constrained=True)
        free, _ = _bowl()
        assert ScipyDriver().method_for(Evaluator(constrained, model)) == "SLSQP"
        assert ScipyDriver().method_for(Evaluator(free, model)) == "L-BFGS-B"

    def test_an_explicit_method_is_used_and_named(self) -> None:
        free, model = _bowl()
        driver = ScipyDriver(method="Powell")
        assert driver.method_for(Evaluator(free, model)) == "Powell"
        assert driver.name == "scipy/Powell"
        assert ScipyDriver().name == "scipy"


class TestScipyFindsTheAlgebraicAnswer:
    def test_the_unconstrained_minimum(self) -> None:
        problem, model = _bowl()
        result = optimise(problem, model, driver="scipy")
        assert result.stop is Stop.CONVERGED
        assert result.values is not None
        assert result.values["x"] == pytest.approx(3.0, abs=1e-4)
        assert result.values["y"] == pytest.approx(-1.0, abs=1e-4)

    def test_a_maximisation_finds_the_same_point_and_records_the_true_sign(self) -> None:
        problem, model = _bowl(sense=Sense.MAXIMISE)
        result = optimise(problem, model)
        assert result.converged
        assert result.values is not None
        assert result.values["x"] == pytest.approx(3.0, abs=1e-4)
        # Recorded as measured: the negative bowl, not the negated number the driver saw.
        assert result.objective is not None and result.objective <= 0.0
        assert result.objective == pytest.approx(0.0, abs=1e-6)

    def test_the_constrained_minimum_sits_on_the_constraint(self) -> None:
        problem, model = _bowl(constrained=True)
        result = optimise(problem, model)
        assert result.converged
        assert result.values is not None
        assert result.values["x"] == pytest.approx(4.0, abs=1e-3)
        assert result.values["y"] == pytest.approx(0.0, abs=1e-3)
        assert result.objective == pytest.approx(2.0, rel=1e-3)

    def test_without_our_gradients_it_still_converges_and_reports_none(self) -> None:
        problem, model = _bowl()
        result = optimise(problem, model, driver=ScipyDriver(gradients=False))
        assert result.converged
        assert result.gradients == ()

    def test_with_our_gradients_the_reports_are_carried(self) -> None:
        problem, model = _bowl()
        result = optimise(problem, model)
        assert result.gradients, "the default driver steers by sensitivity.py"


class TestADriverThatRaisesIsNotAStatementAboutTheDesign:
    def test_an_unknown_scipy_method_is_driver_failed(self) -> None:
        problem, model = _bowl()
        result = optimise(problem, model, driver=ScipyDriver(method="no-such-method"))
        assert result.stop is Stop.DRIVER_FAILED
        assert result.solution is None
        assert "optimiser problem rather than a statement about the design" in result.message


class TestTheNumericalFallbackIsAForwardDifference:
    def test_it_matches_the_analytic_slope(self) -> None:
        def f(v: Any) -> float:
            return 3.0 * v[0] ** 2 + 2.0 * v[1]

        slope = _numerical_gradient(f, [2.0, 5.0])
        assert slope[0] == pytest.approx(12.0, rel=1e-4)
        assert slope[1] == pytest.approx(2.0, rel=1e-4)

    def test_it_is_used_where_our_gradient_is_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.optimise.gradients import GradientReport

        called: list[int] = []
        real = drivers_module._numerical_gradient

        def counting(objective: Any, vector: Any, **kwargs: Any) -> list[float]:
            called.append(1)
            return real(objective, vector, **kwargs)

        def unavailable(evaluator: Any, at: Any, **kwargs: Any) -> GradientReport:
            return GradientReport(at=dict(at), available=False, reason="nothing measured here.")

        monkeypatch.setattr(drivers_module, "_numerical_gradient", counting)
        monkeypatch.setattr(drivers_module, "objective_gradient", unavailable)
        problem, model = _bowl()
        result = optimise(problem, model)
        assert called
        assert result.converged


class TestOpenMdaoIsRefusedByNameWhereItIsMissing:
    def test_a_missing_framework_is_driver_unavailable_naming_scipy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            OpenMdaoDriver, "available", lambda self: (False, "OpenMDAO is not installed here; use driver='scipy'.")
        )
        problem, model = _bowl()
        with pytest.raises(DriverUnavailable, match="driver='scipy'"):
            optimise(problem, model, driver="openmdao")

    def test_the_real_probe_names_the_alternative(self) -> None:
        installed, why = OpenMdaoDriver().available()
        if installed:
            assert why == ""
        else:
            assert "driver='scipy'" in why and "pip install openmdao" in why

    @pytest.mark.parametrize(
        ("name", "tag"),
        [
            ("Pad.1\\length_mm", "Pad_1_length_mm"),
            ("1st_width", "v_1st_width"),
            ("width_mm", "width_mm"),
        ],
    )
    def test_a_journal_name_becomes_a_legal_openmdao_name(self, name: str, tag: str) -> None:
        assert OpenMdaoDriver._tag(name) == tag


class TestOpenMdaoAgreesWithScipy:
    """The seam's two sides held to one answer. Runs only where OpenMDAO is installed."""

    @pytest.fixture(autouse=True)
    def _needs_openmdao(self) -> None:
        pytest.importorskip("openmdao.api")

    def test_the_unconstrained_minimum(self) -> None:
        problem, model = _bowl()
        result = optimise(problem, model, driver="openmdao")
        assert result.stop is Stop.CONVERGED, result.message
        assert result.driver == "openmdao/SLSQP"
        assert result.values is not None
        assert result.values["x"] == pytest.approx(3.0, abs=1e-3)
        assert result.values["y"] == pytest.approx(-1.0, abs=1e-3)

    def test_the_constrained_minimum(self) -> None:
        problem, model = _bowl(constrained=True)
        result = optimise(problem, model, driver="openmdao")
        assert result.stop is Stop.CONVERGED, result.message
        assert result.values is not None
        assert result.values["x"] == pytest.approx(4.0, abs=1e-3)
        assert result.values["y"] == pytest.approx(0.0, abs=1e-3)

    def test_every_rebuild_is_in_the_same_log(self) -> None:
        problem, model = _bowl()
        result = optimise(problem, model, driver="openmdao")
        assert len(result.log) >= 2
        assert result.log.evaluations[0].purpose == "baseline"


class _Stubborn:
    """A driver that claims convergence at a point it never evaluated."""

    name = "stubborn"

    def __init__(self, values: Mapping[str, float], *, converged: bool = True) -> None:
        self._values = values
        self._converged = converged

    def available(self) -> tuple[bool, str]:
        return True, ""

    def run(self, evaluator: Evaluator, start: Mapping[str, float]) -> DriverOutcome:
        return DriverOutcome(converged=self._converged, message="done.", values=self._values)


class TestTheReportedPointIsRebuiltNotBelieved:
    def test_a_point_the_driver_never_evaluated_is_rebuilt_here(self) -> None:
        problem, model = _bowl()
        result = optimise(problem, model, driver=_Stubborn({"x": 3.0, "y": -1.0}))  # type: ignore[arg-type]
        assert result.converged
        assert len(result.log) == 2  # the baseline, and the rebuild of the claimed point
        assert result.log.evaluations[-1].values == {"x": 3.0, "y": -1.0}
        assert result.objective == 0.0

    def test_a_point_already_in_the_log_costs_no_rebuild(self) -> None:
        problem, model = _bowl()
        result = optimise(problem, model, driver=_Stubborn({"x": 0.0, "y": 0.0}))  # type: ignore[arg-type]
        assert len(result.log) == 1
        assert result.objective == pytest.approx(10.0)

    def test_a_driver_that_stopped_short_is_driver_stopped(self) -> None:
        problem, model = _bowl()
        result = optimise(
            problem, model, driver=_Stubborn({"x": 1.0, "y": 1.0}, converged=False)  # type: ignore[arg-type]
        )
        assert result.stop is Stop.DRIVER_STOPPED
        assert result.solution is None

    def test_same_point_is_relative_to_each_variables_range(self) -> None:
        problem, _ = _bowl()
        span = problem.variables[0].span
        assert _same_point(problem, {"x": 1.0, "y": 1.0}, {"x": 1.0 + 0.5 * SAME_POINT * span, "y": 1.0})
        assert not _same_point(problem, {"x": 1.0, "y": 1.0}, {"x": 1.0 + 2.0 * SAME_POINT * span, "y": 1.0})
        assert not _same_point(problem, {"x": 1.0}, {"x": 1.0, "y": 1.0})


class TestCloseIsRelativeWhereItCanBe:
    def test_a_large_objective_and_a_small_one(self) -> None:
        assert is_close(1_000_000.0, 1_000_000.5, tolerance=1e-6)
        assert not is_close(1.0, 1.5, tolerance=1e-6)
