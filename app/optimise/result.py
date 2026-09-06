"""What an optimisation returns, and the one rule that matters most here.

**An optimisation that did not converge reports that it did not converge, and
never returns its last iterate as though it were an answer.** That is Decision 3
applied to a loop, and everything in this module is arranged around it.

It is arranged around it structurally rather than by convention, because
convention loses. Every optimisation library in existence returns an object with
a `.x` on it and a `success` flag beside it, and the overwhelmingly common bug —
in research code, in industrial code, in code written by people who know better
— is reading the `.x` without reading the flag. The number is right there and it
looks like an answer. So:

* `OptimisationResult` **has no `x`, no `optimum`, no `design` and no `value`.**
* `solution` is the only accessor that yields a design, and it returns `None`
  unless the run converged *and* the point it converged on is feasible. Not
  "unless the driver said success" — the driver's opinion is one input; a
  measured constraint violation at the final point overrides it.
* The point the run actually ended on is reachable, because a caller debugging a
  failed run needs it, but only through `best_seen` and
  `closest_to_feasible` — names that cannot be misread as "the answer", on a
  result whose `summary()` leads with why it did not converge.

The parallel is deliberate: `app/ai/vision.py`'s `VisualReview` has no
`approved` property for the same reason, and `app/design/assertions.py` has
three outcomes rather than two. A thing that could not be established is never
quietly turned into a pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.optimise.evaluate import Evaluation, EvaluationLog
from app.optimise.gradients import GradientReport
from app.optimise.problem import OptimisationProblem


class Stop(StrEnum):
    """Why the run ended. Every one of these has a different next step."""

    #: The driver converged and the point it converged on is feasible. The only
    #: value for which `solution` is not None.
    CONVERGED = "converged"

    #: The rebuild budget ran out first. Next step: raise it, narrow the bounds,
    #: or cut the number of design variables.
    BUDGET_SPENT = "budget_spent"

    #: The driver stopped short of its own tolerance — its iteration limit, or a
    #: line search that could make no further progress.
    DRIVER_STOPPED = "driver_stopped"

    #: The driver reported convergence, but the point it converged on violates a
    #: constraint that was actually measured. A converged infeasible point is not
    #: an answer; it usually means the feasible set is empty under these bounds.
    NO_FEASIBLE_POINT = "no_feasible_point"

    #: The driver reported convergence, but a constraint at the final point could
    #: not be measured, so nobody knows whether the design is acceptable.
    #: Separate from NO_FEASIBLE_POINT because the recovery is different: go and
    #: measure it, rather than change the design.
    UNVERIFIED = "unverified"

    #: Nothing built anywhere in the search. Almost always the bounds: they
    #: describe a region no geometry occupies.
    NOTHING_BUILT = "nothing_built"

    #: The driver itself raised. A bug or a bad option, not a statement about the
    #: design.
    DRIVER_FAILED = "driver_failed"

    @property
    def converged(self) -> bool:
        return self is Stop.CONVERGED


@dataclass(frozen=True)
class OptimisationResult:
    """The outcome of one optimisation run, with nothing overclaimed.

    Construct through `converged()` / `did_not_converge()` rather than directly:
    the constructor cannot check that a `_solution` handed to it is really
    feasible, and the classmethods can.
    """

    problem: OptimisationProblem
    stop: Stop
    message: str
    log: EvaluationLog
    driver: str

    #: How the search was steered, when it was. Present so a reader can see
    #: whether the run used our own `sensitivity.py` gradients or fell back to
    #: the driver's finite differences, and why.
    gradients: tuple[GradientReport, ...] = ()

    #: Anything the caller should know about how the run was set up — a starting
    #: value clamped into its bounds, a driver option overridden.
    notes: tuple[str, ...] = ()

    _solution: Evaluation | None = field(default=None, repr=False)

    # -- the accessors -------------------------------------------------------

    @property
    def converged(self) -> bool:
        return self.stop.converged

    @property
    def solution(self) -> Evaluation | None:
        """The optimised design, or **None** where the run did not converge.

        Gated on `stop` at read time and not only at construction, so a result
        assembled wrongly — by a future driver, by a test, by a refactor — still
        cannot hand out a last iterate as an answer. The check is cheap and the
        failure it prevents is the expensive one.
        """
        if not self.converged:
            return None
        if self._solution is None or not self._solution.feasible:
            return None
        return self._solution

    @property
    def best_seen(self) -> Evaluation | None:
        """The best feasible point the search visited. **Not the answer.**

        Present because a run that spent its budget still learned something, and
        a caller deciding whether to raise the budget wants to see how close it
        got. It is not a solution: nothing established that it is optimal, and on
        a non-converged run nothing established that it is even a local minimum.
        Anything reporting this to a user must say which of the two it is.
        """
        return self.log.best(self.problem)

    @property
    def closest_to_feasible(self) -> Evaluation | None:
        """The least-violating built point — a diagnosis, never a design."""
        return self.log.closest_to_feasible()

    @property
    def values(self) -> Mapping[str, float] | None:
        """The converged design variables, or None. Same gate as `solution`."""
        found = self.solution
        return None if found is None else dict(found.values)

    @property
    def objective(self) -> float | None:
        """The converged objective value, or None. Same gate as `solution`."""
        found = self.solution
        return None if found is None else found.objective

    @property
    def approximate(self) -> bool:
        """True where the converged point read a number that was not measured.

        A converged optimum whose constraint was checked against a ray-cast wall
        thickness is a real result and a caveated one, and the caveat travels
        with it rather than being looked up later.
        """
        found = self.solution
        return bool(found and found.approximate)

    # -- construction --------------------------------------------------------

    @classmethod
    def converged_at(
        cls,
        problem: OptimisationProblem,
        *,
        solution: Evaluation,
        log: EvaluationLog,
        driver: str,
        message: str = "",
        gradients: tuple[GradientReport, ...] = (),
        notes: tuple[str, ...] = (),
    ) -> OptimisationResult:
        """A converged run — **refused unless the point is actually feasible**.

        A driver reporting success at a point that violates a measured constraint
        does not get to produce a converged result. It becomes
        `NO_FEASIBLE_POINT`, or `UNVERIFIED` where the constraint could not be
        measured at all, and either way `solution` is None.
        """
        if not solution.built or solution.objective is None:
            return cls.stopped(
                problem,
                stop=Stop.NOTHING_BUILT,
                log=log,
                driver=driver,
                message=(
                    f"{driver} reported convergence at a point that produced no "
                    f"measurable part: {solution.reason}"
                ),
                gradients=gradients,
                notes=notes,
            )
        if solution.unmeasured_constraints:
            unchecked = "; ".join(str(one) for one in solution.unmeasured_constraints)
            return cls.stopped(
                problem,
                stop=Stop.UNVERIFIED,
                log=log,
                driver=driver,
                message=(
                    f"{driver} converged, but the design it converged on could not be "
                    f"fully checked, so it is not being reported as an answer. {unchecked} "
                    "Measure it and re-run, or drop the constraint if it is not needed."
                ),
                gradients=gradients,
                notes=notes,
            )
        if solution.violated:
            broken = "; ".join(str(one) for one in solution.violated)
            return cls.stopped(
                problem,
                stop=Stop.NO_FEASIBLE_POINT,
                log=log,
                driver=driver,
                message=(
                    f"{driver} converged on a design that breaks a stated requirement, "
                    f"so there is no answer to report. {broken} Widen the bounds, relax "
                    "the constraint, or accept that no design in this space satisfies it."
                ),
                gradients=gradients,
                notes=notes,
            )
        return cls(
            problem=problem,
            stop=Stop.CONVERGED,
            message=message
            or (
                f"{driver} converged in {len(log)} evaluations "
                f"({len(log.failures())} of which did not build)."
            ),
            log=log,
            driver=driver,
            gradients=gradients,
            notes=notes,
            _solution=solution,
        )

    @classmethod
    def stopped(
        cls,
        problem: OptimisationProblem,
        *,
        stop: Stop,
        log: EvaluationLog,
        driver: str,
        message: str,
        gradients: tuple[GradientReport, ...] = (),
        notes: tuple[str, ...] = (),
    ) -> OptimisationResult:
        """A run that did not converge. `_solution` is never populated here."""
        if stop is Stop.CONVERGED:  # pragma: no cover - guarded by the type of use
            raise ValueError(
                "OptimisationResult.stopped() cannot be used to report convergence; "
                "converged_at() is the only path to Stop.CONVERGED and it checks the "
                "point is feasible first."
            )
        return cls(
            problem=problem,
            stop=stop,
            message=message,
            log=log,
            driver=driver,
            gradients=gradients,
            notes=notes,
        )

    # -- reporting -----------------------------------------------------------

    def summary(self) -> str:
        """The run in a few lines, leading with whether it is an answer.

        Leading with it on purpose. A summary that opens with a number and
        mentions non-convergence three lines down is read as a number.
        """
        counts = self.log.counts()
        lines: list[str]
        if self.converged and self.solution is not None:
            found = self.solution
            where = ", ".join(f"{k} = {v:g}" for k, v in found.values.items())
            caveat = " (read from an approximated number)" if found.approximate else ""
            lines = [
                f"{self.problem.name}: converged.",
                f"  {self.problem.objective.measurement} = "
                f"{float(found.objective or 0.0):g}{caveat} at {where}",
            ]
            for constraint in found.constraints:
                lines.append(f"  {constraint}")
        else:
            lines = [
                f"{self.problem.name}: DID NOT CONVERGE ({self.stop.value}).",
                f"  {self.message}",
                "  No optimised design is being reported. The points below were "
                "visited, not verified.",
            ]
            best = self.best_seen
            if best is not None:
                lines.append(f"  best feasible point visited: {best}")
            near = self.closest_to_feasible
            if best is None and near is not None:
                lines.append(f"  closest to feasible: {near}")

        lines.append(
            f"  {counts['evaluations']} evaluations via {self.driver}: "
            f"{counts['built']} built, {counts['failed']} failed, "
            f"{counts['feasible']} feasible."
        )
        for note in self.notes:
            lines.append(f"  note: {note}")
        for report in self.gradients:
            if not report.available:
                lines.append(f"  gradients: {report.reason}")
                break
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """The record. `solution` is null on a non-converged run, in the data too.

        Serialisation is where an honesty rule is most easily lost — a dict that
        carried `best_seen` under a key called `solution` would undo the whole
        module for anything reading the JSON.
        """
        found = self.solution
        return {
            "problem": self.problem.name,
            "converged": self.converged,
            "stop": self.stop.value,
            "message": self.message,
            "driver": self.driver,
            "objective": {
                "measurement": self.problem.objective.measurement,
                "sense": self.problem.objective.sense.value,
            },
            "solution": None if found is None else found.to_dict(),
            "best_feasible_visited": (
                None if self.best_seen is None else self.best_seen.to_dict()
            ),
            "notes": list(self.notes),
            "history": self.log.to_dict(),
        }

    def __str__(self) -> str:
        return self.summary()


__all__ = ["OptimisationResult", "Stop"]
