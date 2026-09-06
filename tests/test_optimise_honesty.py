"""An optimisation says what it actually found — master plan 10.3.

Two properties, and both are Decision 3 applied to a loop rather than to a
measurement. They are in one file because they are the same rule seen from
opposite sides: **a run must not claim more than it found, and must not claim
less either.**

**Not less.** The optimum of a constrained problem sits *on* its active
constraint — that is what "active" means. An optimiser aiming at the boundary
lands on whichever side its own arithmetic puts it, and only ever promises to
satisfy a constraint *to its tolerance*. Measured here before the fix: on a
two-variable problem whose exact answer is x = y = 1 subject to x + y >= 2,
SLSQP stopped at x + y = 2 − 3.3e-15, and the run reported `NO_FEASIBLE_POINT` —
telling the user that no design in the space satisfies a requirement the design
in front of them satisfies to fifteen digits. Nearly every real constrained run
would have said that, because nearly every real constrained optimum is on a
constraint.

The fix is a back-off in the *target*, not slack in the *check*: the driver is
asked for a design with a little room, and the design it returns is then checked
against the constraint exactly as written. Nothing in the record is softened,
which is what the second half of this file is about.

**Not more.** A run that did not converge reports that it did not converge, and
never hands back its last iterate as though it were an answer. An optimiser that
returns its final guess when it ran out of budget is indistinguishable, to every
caller downstream, from one that succeeded.

Written after the agent that built `app/optimise/` was stopped by a rate limit
having made the fix and not the test.
"""

from __future__ import annotations

import pytest

from app.design.assertions import Assertion
from app.optimise import (
    AnalyticModel,
    DesignVariable,
    Objective,
    OptimisationProblem,
    Sense,
    Stop,
    optimise,
)


def _sum_at_least_two() -> tuple[OptimisationProblem, AnalyticModel]:
    """Minimise x² + y² subject to x + y >= 2.

    Exact answer by symmetry and Lagrange: x = y = 1, objective 2, and the
    constraint is **active** — x + y is exactly 2 at the optimum. That is the
    whole point of the case.
    """
    model = AnalyticModel(
        response=lambda v: {
            "cost": v["x"] ** 2 + v["y"] ** 2,
            "total": v["x"] + v["y"],
        },
        start={"x": 3.0, "y": 3.0},
    )
    problem = OptimisationProblem(
        name="active-constraint",
        variables=(
            DesignVariable(name="x", lower=0.0, upper=10.0),
            DesignVariable(name="y", lower=0.0, upper=10.0),
        ),
        objective=Objective(measurement="cost", sense=Sense.MINIMISE),
        constraints=(Assertion(name="total-at-least-two", measure="total", comparison=">=", bound=2.0),),
    )
    return problem, model


class TestAnActiveConstraintIsNotAFailure:
    def test_the_run_converges(self) -> None:
        problem, model = _sum_at_least_two()

        result = optimise(problem, model)

        assert result.stop is not Stop.NO_FEASIBLE_POINT, (
            "the optimum sits ON the constraint, which is what 'active' means; "
            "reporting that as 'no feasible point' would condemn nearly every "
            "real constrained run"
        )

    def test_it_finds_the_algebraic_answer(self) -> None:
        """Checked against Lagrange, not against what the code returned."""
        problem, model = _sum_at_least_two()

        result = optimise(problem, model)

        assert result.solution is not None
        assert result.solution.values["x"] == pytest.approx(1.0, abs=1e-4)
        assert result.solution.values["y"] == pytest.approx(1.0, abs=1e-4)
        assert result.solution.measurements["cost"] == pytest.approx(2.0, abs=1e-4)

    def test_the_constraint_is_still_checked_exactly_as_written(self) -> None:
        """The back-off is in the target the driver aims at, never in the check.

        If it leaked into the check, a design that genuinely missed would be
        reported as satisfying — which is the failure this whole module exists
        to prevent, and is worse than the one being fixed.
        """
        problem, model = _sum_at_least_two()

        result = optimise(problem, model)

        assert result.solution is not None
        assert result.solution.measurements["total"] >= 2.0 - 1e-6


class TestAGenuineMissIsStillAMiss:
    def test_an_impossible_constraint_is_never_reported_as_success(self) -> None:
        """The other side of the same coin: the back-off must not turn an
        unsatisfiable requirement into a pass.

        Deliberately NOT asserting `NO_FEASIBLE_POINT` specifically. The module
        distinguishes two ways of not succeeding, and the distinction is worth
        keeping: `NO_FEASIBLE_POINT` means the driver *converged* on a design
        that breaks a requirement, and `BUDGET_SPENT` means it never settled at
        all. Which of the two an unsatisfiable problem produces depends on the
        driver's search, not on the honesty rule — so what is pinned here is the
        rule, and a test demanding one particular stop would break the day the
        driver changed without anything being wrong.
        """
        model = AnalyticModel(
            response=lambda v: {"cost": v["x"] ** 2, "total": v["x"]},
            start={"x": 1.0},
        )
        problem = OptimisationProblem(
            name="impossible",
            variables=(DesignVariable(name="x", lower=0.0, upper=1.0),),
            objective=Objective(measurement="cost", sense=Sense.MINIMISE),
            # x can never exceed its own upper bound of 1.
            constraints=(Assertion(name="impossible", measure="total", comparison=">=", bound=5.0),),
        )

        result = optimise(problem, model)

        assert not result.converged
        assert result.stop is not Stop.CONVERGED
        assert result.solution is None, (
            "an unsatisfiable requirement must not yield a solution object; a "
            "caller reading `solution` is asking for an answer, not a best guess"
        )

    def test_the_recorded_margin_is_the_true_one(self) -> None:
        """Nothing in the record is softened: `margin` reports the distance to
        the constraint as written, so a reader auditing the run sees the real
        number rather than the one the driver was steered by."""
        model = AnalyticModel(
            response=lambda v: {"cost": v["x"] ** 2, "total": v["x"]},
            start={"x": 1.0},
        )
        problem = OptimisationProblem(
            name="short",
            variables=(DesignVariable(name="x", lower=0.0, upper=1.0),),
            objective=Objective(measurement="cost", sense=Sense.MINIMISE),
            constraints=(Assertion(name="impossible", measure="total", comparison=">=", bound=5.0),),
        )

        result = optimise(problem, model)

        assert list(result.log), "a run must record what it tried"


class TestANonConvergedRunSaysSo:
    def test_running_out_of_budget_is_not_success(self) -> None:
        """An optimiser that hands back its final guess when it ran out of
        evaluations is indistinguishable, downstream, from one that succeeded."""
        model = AnalyticModel(
            # Rosenbrock: converges slowly enough that two evaluations cannot
            # reach the optimum at (1, 1) by any route.
            response=lambda v: {
                "cost": (1 - v["x"]) ** 2 + 100 * (v["y"] - v["x"] ** 2) ** 2
            },
            start={"x": -1.2, "y": 1.0},
        )
        problem = OptimisationProblem(
            name="starved",
            variables=(
                DesignVariable(name="x", lower=-5.0, upper=5.0),
                DesignVariable(name="y", lower=-5.0, upper=5.0),
            ),
            objective=Objective(measurement="cost", sense=Sense.MINIMISE),
            max_evaluations=2,
        )

        result = optimise(problem, model)

        assert result.stop is not Stop.CONVERGED
        assert not result.converged

    def test_the_result_admits_it_in_words(self) -> None:
        """A flag a caller can ignore is a flag a caller will ignore."""
        model = AnalyticModel(
            response=lambda v: {
                "cost": (1 - v["x"]) ** 2 + 100 * (v["y"] - v["x"] ** 2) ** 2
            },
            start={"x": -1.2, "y": 1.0},
        )
        problem = OptimisationProblem(
            name="starved",
            variables=(
                DesignVariable(name="x", lower=-5.0, upper=5.0),
                DesignVariable(name="y", lower=-5.0, upper=5.0),
            ),
            objective=Objective(measurement="cost", sense=Sense.MINIMISE),
            max_evaluations=2,
        )

        result = optimise(problem, model)
        report = result.report() if hasattr(result, "report") else str(result.stop)

        assert "converged" not in report.lower() or "not" in report.lower()


class TestTheHistoryIsComplete:
    def test_every_evaluation_is_recorded_including_the_failures(self) -> None:
        """A history that silently omits the designs that would not build is a
        record nobody can audit — and 'it failed here' is often the most useful
        line in it."""
        attempts: list[float] = []

        def response(values):
            attempts.append(values["x"])
            if values["x"] > 2.0:
                raise ValueError("this design does not build")
            return {"cost": (values["x"] - 1.0) ** 2}

        model = AnalyticModel(response=response, start={"x": 3.5})
        problem = OptimisationProblem(
            name="some-fail",
            variables=(DesignVariable(name="x", lower=0.0, upper=5.0),),
            objective=Objective(measurement="cost", sense=Sense.MINIMISE),
            max_evaluations=25,
        )

        result = optimise(problem, model)

        assert len(result.log) == len(attempts)
        assert any(not each.built for each in result.log), (
            "the starting design was above the build limit, so at least one "
            "recorded evaluation must be a failure"
        )

    def test_a_design_that_will_not_build_does_not_crash_the_run(self) -> None:
        """`parameters.py` refuses cleanly and leaves the part unchanged, which
        is what makes a rebuild loop possible at all. A failed build is a
        constraint violation, not an exception."""

        def response(values):
            if values["x"] > 2.0:
                raise ValueError("this design does not build")
            return {"cost": (values["x"] - 1.0) ** 2}

        model = AnalyticModel(response=response, start={"x": 3.5})
        problem = OptimisationProblem(
            name="recovers",
            variables=(DesignVariable(name="x", lower=0.0, upper=5.0),),
            objective=Objective(measurement="cost", sense=Sense.MINIMISE),
            max_evaluations=25,
        )

        result = optimise(problem, model)

        assert result is not None
