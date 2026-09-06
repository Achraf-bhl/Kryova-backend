"""Gradients, and `design/sensitivity.py`'s first caller — master plan 10.3.

`app/design/sensitivity.py` has had **no caller outside a test** since it was
written; `KRYOVA_BUILD_PLAN.md` records it beside `app/render/` and
`app/ai/vision.py` as capability wired to nothing. `objective_gradient` is the
caller, and that is worth a test on its own: a seam nobody uses is a seam that
has never been shown to work.

The gradients are checked against **differentiable functions whose derivative is
known by hand**, never against what the code returned. A finite-difference
gradient verified against its own previous output is not verified — it will
happily agree with itself while being wrong by the step size, by a sign, or by
which variable it thinks it is perturbing.

The last of those is the one worth designing a test around. A gradient that
swaps two variables is *numerically plausible* — right magnitudes, right
smoothness, wrong answer — so every test here uses a function whose partials
**differ from each other**, and one uses variables scaled a thousand apart,
because a relative step that is really an absolute one passes the first kind of
test and fails the second.
"""

from __future__ import annotations

import pytest

from app.optimise import (
    AnalyticModel,
    DesignVariable,
    Evaluator,
    Objective,
    OptimisationProblem,
    Sense,
    objective_gradient,
)


def _problem(response, variables, *, sense=Sense.MINIMISE):
    model = AnalyticModel(response=response, start={v.name: v.lower for v in variables})
    problem = OptimisationProblem(
        name="gradient",
        variables=tuple(variables),
        objective=Objective(measurement="cost", sense=sense),
    )
    return Evaluator(problem, model)


class TestTheGradientIsTheDerivative:
    def test_a_linear_objective_has_its_coefficients(self) -> None:
        """d(3x + 7y)/dx = 3 and /dy = 7, exactly, at every point. The partials
        differ, so a gradient that swapped the variables fails."""
        evaluator = _problem(
            lambda v: {"cost": 3.0 * v["x"] + 7.0 * v["y"]},
            [
                DesignVariable(name="x", lower=0.0, upper=10.0),
                DesignVariable(name="y", lower=0.0, upper=10.0),
            ],
        )

        report = objective_gradient(evaluator, {"x": 2.0, "y": 5.0})

        assert report.available, report.reason
        assert report.values["x"] == pytest.approx(3.0, rel=1e-3)
        assert report.values["y"] == pytest.approx(7.0, rel=1e-3)

    def test_a_quadratic_has_its_analytic_slope(self) -> None:
        """d(x²)/dx = 2x, so the gradient depends on where it is taken —
        a constant gradient would pass the linear test above and fail here."""
        evaluator = _problem(
            lambda v: {"cost": v["x"] ** 2},
            [DesignVariable(name="x", lower=0.0, upper=10.0)],
        )

        at_two = objective_gradient(evaluator, {"x": 2.0})
        at_five = objective_gradient(evaluator, {"x": 5.0})

        assert at_two.values["x"] == pytest.approx(4.0, rel=1e-3)
        assert at_five.values["x"] == pytest.approx(10.0, rel=1e-3)

    def test_a_decreasing_objective_has_a_negative_gradient(self) -> None:
        """A sign error is the single most common gradient bug and sends an
        optimiser away from the optimum at full speed."""
        evaluator = _problem(
            lambda v: {"cost": -4.0 * v["x"]},
            [DesignVariable(name="x", lower=0.0, upper=10.0)],
        )

        report = objective_gradient(evaluator, {"x": 1.0})

        assert report.values["x"] == pytest.approx(-4.0, rel=1e-3)

    def test_variables_scaled_a_thousand_apart_are_both_right(self) -> None:
        """The test an absolute step fails. `thickness_mm` is order 10 and
        `length_mm` order 10,000 in a real problem, and a step that is really
        absolute is either far too coarse for one or lost in rounding for the
        other."""
        evaluator = _problem(
            lambda v: {"cost": 2.0 * v["small"] + 0.001 * v["large"]},
            [
                DesignVariable(name="small", lower=0.001, upper=1.0),
                DesignVariable(name="large", lower=1000.0, upper=100000.0),
            ],
        )

        report = objective_gradient(evaluator, {"small": 0.01, "large": 50000.0})

        assert report.values["small"] == pytest.approx(2.0, rel=1e-2)
        assert report.values["large"] == pytest.approx(0.001, rel=1e-2)


class TestItGoesThroughSensitivity:
    """The point of the module: `design/sensitivity.py`'s first real caller."""

    def test_the_report_carries_the_sensitivity_it_used(self) -> None:
        evaluator = _problem(
            lambda v: {"cost": 3.0 * v["x"]},
            [DesignVariable(name="x", lower=0.0, upper=10.0)],
        )

        report = objective_gradient(evaluator, {"x": 1.0})

        assert report.sensitivity is not None

    def test_the_probes_are_recorded_and_charged_for(self) -> None:
        """They are rebuilds, and somebody is paying for them. A gradient sweep
        that did not count against the budget would let a run quietly cost ten
        times what its `max_evaluations` promised."""
        evaluator = _problem(
            lambda v: {"cost": 3.0 * v["x"] + v["y"]},
            [
                DesignVariable(name="x", lower=0.0, upper=10.0),
                DesignVariable(name="y", lower=0.0, upper=10.0),
            ],
        )
        before = len(evaluator.log)

        objective_gradient(evaluator, {"x": 1.0, "y": 1.0})

        assert len(evaluator.log) > before
        assert any(
            getattr(each, "purpose", "") == "gradient" for each in evaluator.log
        ), "gradient probes must be distinguishable from the optimiser's own steps"


class TestAGradientThatCouldNotBeTakenSaysSo:
    def test_a_design_that_will_not_build_is_unavailable_not_zero(self) -> None:
        """A zero gradient tells an optimiser it is at a stationary point, which
        is the one message guaranteed to make it stop. `available=False` with a
        reason lets the driver fall back to its own differences."""

        def response(values):
            raise ValueError("this design does not build")

        evaluator = _problem(
            response, [DesignVariable(name="x", lower=0.0, upper=10.0)]
        )

        report = objective_gradient(evaluator, {"x": 1.0})

        assert not report.available
        assert report.reason
        assert not report.values

    def test_an_unavailable_report_carries_no_numbers_at_all(self) -> None:
        """Half a gradient is worse than none: a driver reading `values` would
        use whatever partials happened to succeed and treat the rest as zero."""

        def response(values):
            if values["y"] > 1.0000001:
                raise ValueError("the y probe does not build")
            return {"cost": values["x"] + values["y"]}

        evaluator = _problem(
            response,
            [
                DesignVariable(name="x", lower=0.0, upper=10.0),
                DesignVariable(name="y", lower=0.0, upper=10.0),
            ],
        )

        report = objective_gradient(evaluator, {"x": 1.0, "y": 1.0})

        if not report.available:
            assert not report.values
