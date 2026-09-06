"""The front door: `optimise(problem, model)`.

Assembles the evaluator, the driver and the result, and — the part that is not
plumbing — **decides what the run is allowed to claim**. The driver's opinion of
its own convergence arrives here as one input among several:

1. the driver said it converged, and
2. the point it stopped at was rebuilt and measured *here*, not taken from the
   driver's own memory of it, and
3. every declared constraint at that point was measured, and
4. every one of them passed.

All four, or the result is a non-convergence naming which of them failed. Step 2
is the one that looks redundant and is not: a driver reports the vector it
believes it ended on, and between its last evaluation and that vector there can
be a projection onto the bounds, a numerical nudge, or in OpenMDAO's case a
value read back out of its own data structures. Re-evaluating is one more
rebuild and it is the difference between a reported optimum that was measured
and one that was inferred.

The final evaluation is looked up in the log first, so the common case — the
driver ended on a point it had just evaluated — costs nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from app.optimise.drivers import Driver, DriverOutcome, driver_named
from app.optimise.errors import DriverUnavailable
from app.optimise.evaluate import Evaluation, Evaluator, Model
from app.optimise.problem import OptimisationProblem
from app.optimise.result import OptimisationResult, Stop

#: How close two parameter vectors must be to count as the same point, relative
#: to each variable's own range. A driver's reported endpoint differs from its
#: last evaluation by rounding; one part in a million of the search range is far
#: below anything a rebuild could resolve and far above that rounding.
SAME_POINT: Final = 1e-9


def optimise(
    problem: OptimisationProblem,
    model: Model,
    *,
    driver: str | Driver | None = None,
) -> OptimisationResult:
    """Run one optimisation and report honestly on what came of it.

    Raises only for a problem that is wrong or a driver that is missing —
    everything that can be learned by building is a `OptimisationResult`,
    including "it did not converge" and "nothing built anywhere".
    """
    chosen = driver_named(driver)
    installed, why = chosen.available()
    if not installed:
        raise DriverUnavailable(why)

    evaluator = Evaluator(problem, model)
    start, notes = problem.start(model.baseline())

    first = evaluator.at(start, purpose="baseline")
    if not first.built:
        notes = (
            *notes,
            f"the starting design did not build: {first.reason}",
        )

    outcome = chosen.run(evaluator, start)
    return _conclude(problem, evaluator, chosen, outcome, notes)


def _conclude(
    problem: OptimisationProblem,
    evaluator: Evaluator,
    driver: Driver,
    outcome: DriverOutcome,
    notes: tuple[str, ...],
) -> OptimisationResult:
    """Turn a driver's report into a result that claims only what was measured."""
    log = evaluator.log
    name = driver.name
    gradients = outcome.gradients

    if not log.built():
        return OptimisationResult.stopped(
            problem,
            stop=Stop.NOTHING_BUILT,
            log=log,
            driver=name,
            message=(
                "No design in this search built at all, so nothing was measured and "
                "there is nothing to report. The usual cause is bounds that describe a "
                "region no geometry occupies — the first failure was: "
                + (log.evaluations[0].reason if log.evaluations else "no evaluation ran.")
            ),
            gradients=gradients,
            notes=notes,
        )

    if not outcome.converged or evaluator.exhausted:
        if outcome.failed:
            stop = Stop.DRIVER_FAILED
        elif outcome.budget_spent or evaluator.exhausted:
            stop = Stop.BUDGET_SPENT
        else:
            stop = Stop.DRIVER_STOPPED
        return OptimisationResult.stopped(
            problem,
            stop=stop,
            log=log,
            driver=name,
            message=_did_not_converge(problem, evaluator, outcome, stop),
            gradients=gradients,
            notes=notes,
        )

    if outcome.values is None:  # pragma: no cover - a driver that claims success with no point
        return OptimisationResult.stopped(
            problem,
            stop=Stop.DRIVER_FAILED,
            log=log,
            driver=name,
            message=(
                f"{name} reported convergence but returned no design variables, so "
                "there is no design to report."
            ),
            gradients=gradients,
            notes=notes,
        )

    final = _final_evaluation(problem, evaluator, outcome.values)
    return OptimisationResult.converged_at(
        problem,
        solution=final,
        log=log,
        driver=name,
        message=(
            f"{name} converged in {len(log)} evaluations "
            f"({len(log.failures())} of which did not build). {outcome.message}"
        ),
        gradients=gradients,
        notes=notes,
    )


def _final_evaluation(
    problem: OptimisationProblem,
    evaluator: Evaluator,
    values: Mapping[str, float],
) -> Evaluation:
    """The measured state at the point the driver stopped at.

    Reused from the log where the driver ended on a point it had just evaluated,
    which is the common case; rebuilt otherwise. Never taken on trust from the
    driver, which is the whole reason this function exists.
    """
    for evaluation in reversed(evaluator.log.evaluations):
        if _same_point(problem, evaluation.values, values):
            return evaluation
    return evaluator.at(values, purpose="objective")


def _same_point(
    problem: OptimisationProblem,
    left: Mapping[str, float],
    right: Mapping[str, float],
) -> bool:
    for variable in problem.variables:
        there = left.get(variable.name)
        here = right.get(variable.name)
        if there is None or here is None:
            return False
        if abs(there - here) > SAME_POINT * max(variable.span, 1.0):
            return False
    return True


def _did_not_converge(
    problem: OptimisationProblem,
    evaluator: Evaluator,
    outcome: DriverOutcome,
    stop: Stop,
) -> str:
    """Why it stopped, and what to do — never a number that looks like an answer."""
    log = evaluator.log
    counts = log.counts()
    if stop is Stop.BUDGET_SPENT and outcome.converged:
        # The driver believes it converged and the budget says it cannot have
        # measured what it converged on. Its opinion is reported in quotes and
        # explicitly not adopted, because past the budget every point it asked
        # for came back as the failed-build penalty rather than as a rebuild —
        # so what it converged on was the shape of the penalty.
        head = (
            f"The evaluation budget of {problem.max_evaluations} rebuilds ran out during "
            f"the search. {driver_opinion(outcome)} is not being adopted: past the budget "
            "every point it asked for was answered with the unbuildable penalty rather "
            "than with a rebuild, so it converged on that and not on the design."
        )
        advice = (
            "Raise max_evaluations, narrow the bounds, or reduce the number of design "
            "variables — each one costs two more rebuilds per gradient."
        )
    elif stop is Stop.BUDGET_SPENT:
        head = (
            f"The evaluation budget of {problem.max_evaluations} rebuilds ran out before "
            f"the optimiser converged ({outcome.message.rstrip('.')})."
        )
        advice = (
            "Raise max_evaluations, narrow the bounds around where the search was "
            "heading, or reduce the number of design variables — each one costs two "
            "more rebuilds per gradient."
        )
    else:
        head = f"{outcome.message.rstrip('.')}."
        advice = (
            "The design is not being reported as optimised. Check whether the objective "
            "is measurable everywhere in the bounds, and whether the constraints leave "
            "any feasible region at all."
        )

    # The best point visited is deliberately *not* repeated here. `summary()`
    # prints it on its own line, under a label that says it was visited and not
    # verified; putting it in the message too gives a number two chances to be
    # read as an answer.
    return f"{head} {counts['built']} of {counts['evaluations']} evaluations built. {advice}"


def driver_opinion(outcome: DriverOutcome) -> str:
    """The driver's own words about why it stopped, quoted rather than restated."""
    said = outcome.message.strip().rstrip(".")
    return f"The optimiser's report ({said!r})" if said else "The optimiser's report"


__all__ = ["SAME_POINT", "optimise"]
