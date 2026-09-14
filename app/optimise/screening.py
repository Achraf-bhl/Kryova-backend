"""Approximations may rank and may never decide — the rule, as types.

Master plan E22 task 1 derives the rule from the best published surrogate
(DoMINO on DrivAerML: 12–50% surface errors, R² 0.96 on the integrated quantity,
and *non-monotonic errors when ranking successive designs*): **a surrogate may
rank, and may never decide; every surrogate answer carries its error basis; and
the decision point always spends a real solve.** With VVUQ 70 unpublished there
is no standard to appeal to, so the rule is Kryova's own and it is written here,
where every approximation in `app/optimise/` has to pass through it.

It is enforced by what the types lack, the way `OptimisationResult` has no `x`:

* `Estimate` carries a value, a one-sigma band and the sentence that says where
  the band came from. It has no `measured`, `passed` or `feasible`, and its
  provenance is `approximated` whatever produced it.
* `Ranking` orders candidates. It has no `best`, `winner` or `optimum`. It names
  the candidates to *rebuild* (`to_verify`, at least one) and carries
  `MAY_RANK_NEVER_DECIDE` verbatim.
* `screen` is the only function here that returns a design worth acting on, and
  what it returns is the best **measured** candidate — every candidate it
  reports was rebuilt through the `Evaluator`, and the approximation's opinion of
  it travels beside the measurement, never in place of it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

from app.optimise.errors import VariableError
from app.optimise.evaluate import Evaluation, EvaluationLog, Evaluator, Model
from app.optimise.problem import OptimisationProblem, Sense

#: The provenance word every estimate carries — the same vocabulary as
#: `app/kernel/provenance.py`: measured / approximated / unavailable.
APPROXIMATED: Final = "approximated"

#: Printed with every ranking, verbatim. One wording, for the reason
#: `standards.NOT_VALIDATED` has one.
MAY_RANK_NEVER_DECIDE: Final = (
    "This ordering comes from an approximation, not from a build. It may be used to "
    "choose which candidates to build next; it may not be used to accept, reject or "
    "sign off any of them. Rebuild the ones you intend to keep and read the measured "
    "numbers."
)

#: The `purpose` a screened candidate's rebuild is recorded under.
VERIFY_PURPOSE: Final = "verify"


@dataclass(frozen=True)
class Estimate:
    """An approximated value, its one-sigma band, and where the band came from."""

    value: float
    low: float
    high: float
    #: A sentence: what kind of error estimate the band is, and over what data.
    basis: str
    #: True where the point lies outside what the approximation was fitted to.
    extrapolated: bool
    provenance: str = APPROXIMATED
    note: str = ""

    def overlaps(self, other: Estimate) -> bool:
        return self.low <= other.high and other.low <= self.high

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "low": self.low,
            "high": self.high,
            "basis": self.basis,
            "extrapolated": self.extrapolated,
            "provenance": self.provenance,
            "note": self.note,
        }


class Estimator(Protocol):
    def predict(self, values: Mapping[str, float]) -> Estimate: ...


@dataclass(frozen=True)
class Ranked:
    candidate: dict[str, float]
    estimate: Estimate


@dataclass(frozen=True)
class Ranking:
    """Candidates in approximated order. **No `best`.**"""

    order: tuple[Ranked, ...]
    sense: Sense
    #: How many from the top a caller is told to rebuild before acting on any.
    verify_top: int
    statement: str = MAY_RANK_NEVER_DECIDE

    @property
    def to_verify(self) -> tuple[dict[str, float], ...]:
        return tuple(one.candidate for one in self.order[: self.verify_top])

    @property
    def indistinguishable(self) -> bool:
        """True where the top two estimates' bands overlap.

        The order between them is then noise by the approximation's own measure,
        and a reader told only the order would see a difference that is not there.
        """
        if len(self.order) < 2:
            return False
        return self.order[0].estimate.overlaps(self.order[1].estimate)


def rank(
    estimator: Estimator,
    candidates: Iterable[Mapping[str, float]],
    *,
    sense: Sense = Sense.MINIMISE,
    verify_top: int = 3,
) -> Ranking:
    """Order candidates by an estimator, best first in `sense`."""
    if verify_top < 1:
        raise VariableError(
            "verify_top must be at least 1. A ranking that sends no candidate to be "
            "rebuilt has decided, and an approximation may not decide."
        )
    scored = [Ranked(candidate=dict(one), estimate=estimator.predict(one)) for one in candidates]
    scored.sort(key=lambda one: sense.signum * one.estimate.value)
    return Ranking(order=tuple(scored), sense=sense, verify_top=min(verify_top, len(scored)))


@dataclass(frozen=True)
class Screening:
    """A ranking, the rebuilds it paid for, and the best of what was measured."""

    ranking: Ranking
    log: EvaluationLog
    problem: OptimisationProblem

    @property
    def best_measured(self) -> Evaluation | None:
        """The best **feasible, rebuilt** candidate, or None. Never an estimate."""
        return self.log.best(self.problem)

    def estimate_for(self, evaluation: Evaluation) -> Estimate | None:
        for one in self.ranking.order:
            if dict(one.candidate) == dict(evaluation.values):
                return one.estimate
        return None

    def summary(self) -> str:
        found = self.best_measured
        head = (
            f"Screened {len(self.ranking.order)} candidates by approximation and rebuilt the "
            f"top {self.ranking.verify_top}."
        )
        if found is None:
            tail = "None of the rebuilt candidates was feasible, so nothing is recommended."
        else:
            estimate = self.estimate_for(found)
            said = (
                f" (the approximation had said {estimate.value:g}, band "
                f"[{estimate.low:g}, {estimate.high:g}])"
                if estimate
                else ""
            )
            tail = (
                f"Best measured: {self.problem.objective.measurement} {found.objective:g}"
                f"{said}, at "
                + ", ".join(f"{name}={value:g}" for name, value in found.values.items())
                + "."
            )
        return "\n".join([head, tail, self.ranking.statement])


def screen(
    problem: OptimisationProblem,
    model: Model,
    estimator: Estimator,
    candidates: Iterable[Mapping[str, float]],
    *,
    verify_top: int = 3,
) -> Screening:
    """Rank by approximation, then rebuild the top candidates and judge on those.

    The objective's sense comes from the problem. Every rebuild goes through the
    `Evaluator`, so it is in the log, counts against the problem's budget, and is
    judged against the problem's constraints exactly as any optimiser's point is.
    """
    ranking = rank(
        estimator, candidates, sense=problem.objective.sense, verify_top=verify_top
    )
    evaluator = Evaluator(problem, model)
    for candidate in ranking.to_verify:
        evaluator.at(candidate, purpose=VERIFY_PURPOSE)
    return Screening(ranking=ranking, log=evaluator.log, problem=problem)


__all__ = [
    "APPROXIMATED",
    "MAY_RANK_NEVER_DECIDE",
    "VERIFY_PURPOSE",
    "Estimate",
    "Estimator",
    "Ranked",
    "Ranking",
    "Screening",
    "rank",
    "screen",
]
