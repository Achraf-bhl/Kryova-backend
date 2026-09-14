"""Multi-objective trade-offs: a front, never a weighted "optimum".

Master plan 10.3 lists "multi-objective trade-offs (mass vs stiffness vs cost)",
and `problem.Objective` has said since it was written that when they land they
land as their own result type and not as a `weights` argument. This is that
type. **A trade-off has no single answer**: lighter costs stiffness, and where on
that curve a machine should sit is the engineer's decision, made with the curve
in front of them. A weighted sum picks a point for them and hides the curve —
and on a non-convex front it cannot reach some points at any weights at all.

**The method is the epsilon-constraint method**, and it is chosen because every
point it produces is an ordinary `optimise()` run: the first objective is
optimised with every other objective turned into a constraint at a level
between its own extremes. So each point on the front has passed through the
same four-part gate `run.py` applies to any result — the driver converged, the
point was rebuilt here, every constraint was measured, every one passed — and
nothing about honesty has to be re-derived for many objectives.

Three rules:

1. **A sub-problem that did not converge is a gap, not a point.** It is listed
   with its stop reason. A front drawn through a non-converged iterate is the
   non-convergence `result.py` refuses, repeated along a curve.
2. **Dominated points are removed and counted, never silently.** Epsilon runs
   near an anchor can land on a point another run already beats; the front
   reports how many were dropped so its size is not mistaken for its effort.
3. **Between two points the front is not known.** The statement says so. A line
   drawn between them is a picture, not a result.

`sampled_front` reads a front off a survey (`doe.py`) instead. Every point was
built and measured and none was optimised, so the true front lies on or beyond
it, and its statement says exactly that.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np

from app.design.assertions import Assertion, read_measurement
from app.optimise.doe import Experiment
from app.optimise.drivers import Driver
from app.optimise.errors import ObjectiveError, VariableError
from app.optimise.evaluate import Evaluation, Model
from app.optimise.problem import (
    DEFAULT_MAX_EVALUATIONS,
    DEFAULT_TOLERANCE,
    DesignVariable,
    Objective,
    OptimisationProblem,
    Sense,
)
from app.optimise.result import OptimisationResult, Stop
from app.optimise.run import optimise

#: Fewest points a front is asked for: the two anchors and one between them.
#: Two points are the extremes and say nothing about the shape of the trade.
MIN_POINTS: Final = 3

#: More than three objectives is refused. The grid of epsilon levels grows as
#: points^(k−1), and a four-way front nobody can draw is not a thing an engineer
#: chooses on — pairs of it are.
MAX_OBJECTIVES: Final = 3

OPTIMISED_STATEMENT: Final = (
    "Each point is the converged, rebuilt and measured optimum of its own sub-problem. "
    "Between points the front is not known, and choosing a point on it is the "
    "engineer's decision, not the optimiser's."
)

SAMPLED_STATEMENT: Final = (
    "Each point was built and measured, and none was optimised: these are the best "
    "trade-offs among the designs surveyed. The true front lies on or beyond them."
)


@dataclass(frozen=True)
class TradeOff:
    """A multi-objective statement: design variables, objectives, constraints."""

    name: str
    variables: tuple[DesignVariable, ...]
    objectives: tuple[Objective, ...]
    constraints: tuple[Assertion, ...] = ()
    #: Rebuild budget for *each* sub-problem.
    max_evaluations: int = DEFAULT_MAX_EVALUATIONS
    tolerance: float = DEFAULT_TOLERANCE

    def __post_init__(self) -> None:
        if len(self.objectives) < 2:
            raise ObjectiveError(
                f"{self.name}: a trade-off needs at least two objectives; with one it is an "
                "ordinary optimisation — use OptimisationProblem and optimise()."
            )
        if len(self.objectives) > MAX_OBJECTIVES:
            raise ObjectiveError(
                f"{self.name}: {len(self.objectives)} objectives were given and at most "
                f"{MAX_OBJECTIVES} are offered. A front in four or more dimensions cannot be "
                "drawn or chosen on; study the objectives in pairs or threes."
            )
        paths = [one.measurement for one in self.objectives]
        if len(set(paths)) != len(paths):
            raise ObjectiveError(
                f"{self.name}: an objective is named twice ({paths}). Two objectives on one "
                "measurement are one objective, or a contradiction."
            )
        # Borrow OptimisationProblem's checks on variables, budget and tolerance.
        self.problem_for(self.objectives[0], ())

    def problem_for(
        self, objective: Objective, extra: Sequence[Assertion], *, label: str = ""
    ) -> OptimisationProblem:
        return OptimisationProblem(
            name=f"{self.name}: {label or objective}",
            variables=self.variables,
            objective=objective,
            constraints=(*self.constraints, *extra),
            max_evaluations=self.max_evaluations,
            tolerance=self.tolerance,
        )


@dataclass(frozen=True)
class FrontPoint:
    """One measured trade-off: the design and every objective read off it."""

    evaluation: Evaluation
    objectives: dict[str, float]
    #: Which sub-problem produced it, or "survey".
    source: str

    @property
    def values(self) -> Mapping[str, float]:
        return self.evaluation.values

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": dict(self.evaluation.values),
            "objectives": dict(self.objectives),
            "source": self.source,
        }


@dataclass(frozen=True)
class Gap:
    """A sub-problem that produced no point, and why."""

    source: str
    stop: Stop
    message: str


@dataclass(frozen=True)
class ParetoFront:
    """Non-dominated measured points, the gaps, and what was dropped. **No `best`.**"""

    objectives: tuple[Objective, ...]
    points: tuple[FrontPoint, ...]
    gaps: tuple[Gap, ...] = ()
    dominated: int = 0
    runs: tuple[OptimisationResult, ...] = ()
    statement: str = OPTIMISED_STATEMENT
    sampled: bool = False
    evaluations: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        """True where every sub-problem asked for produced a point or a dominated one."""
        return not self.gaps

    def summary(self) -> str:
        names = " vs ".join(str(one) for one in self.objectives)
        lines = [
            f"Trade-off {names}: {len(self.points)} point(s) on the front"
            + (f", {self.dominated} dominated and dropped" if self.dominated else "")
            + (f", {len(self.gaps)} sub-problem(s) produced no point" if self.gaps else "")
            + f", {self.evaluations} rebuilds.",
            self.statement,
        ]
        for point in self.points:
            reads = ", ".join(f"{name} {value:g}" for name, value in point.objectives.items())
            where = ", ".join(f"{name}={value:g}" for name, value in point.values.items())
            lines.append(f"  {reads}  at {where}  ({point.source})")
        for gap in self.gaps:
            lines.append(f"  gap — {gap.source}: {gap.stop.value}: {gap.message}")
        lines.extend(self.notes)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objectives": [str(one) for one in self.objectives],
            "points": [one.to_dict() for one in self.points],
            "gaps": [
                {"source": one.source, "stop": one.stop.value, "message": one.message}
                for one in self.gaps
            ],
            "dominated": self.dominated,
            "sampled": self.sampled,
            "evaluations": self.evaluations,
            "statement": self.statement,
        }


def _reads(evaluation: Evaluation, objectives: Sequence[Objective]) -> dict[str, float] | None:
    out: dict[str, float] = {}
    for one in objectives:
        value = read_measurement(evaluation.measurements, one.measurement)
        if value is None or not np.isfinite(value):
            return None
        out[one.measurement] = float(value)
    return out


def non_dominated(
    candidates: Sequence[FrontPoint],
    objectives: Sequence[Objective],
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> tuple[tuple[FrontPoint, ...], int]:
    """The candidates no other candidate beats, and how many were dropped.

    `a` dominates `b` when it is no worse in every objective and better in at
    least one, each comparison with a relative `tolerance` — two runs landing on
    the same design differ in the last digits, and an exact comparison would keep
    both and draw a front with a duplicated point. Of points equal within the
    tolerance the first is kept.
    """
    signs = [one.sense.signum for one in objectives]
    paths = [one.measurement for one in objectives]

    def key(point: FrontPoint) -> list[float]:
        return [sign * point.objectives[path] for sign, path in zip(signs, paths, strict=True)]

    def dominates(a: list[float], b: list[float]) -> bool:
        slack = [tolerance * max(abs(x), abs(y), 1.0) for x, y in zip(a, b, strict=True)]
        no_worse = all(x <= y + s for x, y, s in zip(a, b, slack, strict=True))
        better = any(x < y - s for x, y, s in zip(a, b, slack, strict=True))
        return no_worse and better

    def same(a: list[float], b: list[float]) -> bool:
        return all(
            abs(x - y) <= tolerance * max(abs(x), abs(y), 1.0) for x, y in zip(a, b, strict=True)
        )

    kept: list[FrontPoint] = []
    keys = [key(one) for one in candidates]
    for index, (point, mine) in enumerate(zip(candidates, keys, strict=True)):
        beaten = any(
            dominates(other, mine) for j, other in enumerate(keys) if j != index
        ) or any(same(keys[j], mine) for j in range(index))
        if not beaten:
            kept.append(point)
    kept.sort(key=lambda one: key(one)[0])
    return tuple(kept), len(candidates) - len(kept)


def _bound_on(objective: Objective, level: float, label: str) -> Assertion:
    comparison = "<=" if objective.sense is Sense.MINIMISE else ">="
    return Assertion(
        name=f"epsilon {label}",
        measure=objective.measurement,
        comparison=comparison,
        bound=float(level),
        note="an epsilon-constraint level set by the trade-off study, not a requirement",
    )


def pareto_front(
    trade_off: TradeOff,
    model: Model,
    *,
    points: int = 7,
    driver: str | Driver | None = None,
) -> ParetoFront:
    """Optimise the first objective with the others held at stepped levels.

    `points` is levels per secondary objective, the anchors included: a two-way
    front is `points` sub-problems (both anchors and `points − 2` between them),
    and a three-way front is `3 + (points − 2)²`. Each sub-problem has the trade
    -off's own `max_evaluations`, and the worst-case total is stated in the
    front's notes so the cost of a denser front is visible beside it.
    """
    if points < MIN_POINTS:
        raise VariableError(
            f"{trade_off.name}: a front of {points} point(s) is only its extremes. Ask for at "
            f"least {MIN_POINTS}."
        )
    objectives = trade_off.objectives
    primary, secondaries = objectives[0], objectives[1:]
    runs_planned = len(objectives) + (points - 2) ** len(secondaries)
    budget = runs_planned * trade_off.max_evaluations
    notes = (
        f"{runs_planned} sub-problems at up to {trade_off.max_evaluations} rebuilds each "
        f"(at most {budget} rebuilds).",
    )

    runs: list[OptimisationResult] = []
    candidates: list[FrontPoint] = []
    gaps: list[Gap] = []

    def record(result: OptimisationResult, source: str) -> Evaluation | None:
        runs.append(result)
        found = result.solution
        if found is None:
            gaps.append(Gap(source=source, stop=result.stop, message=result.message))
            return None
        reads = _reads(found, objectives)
        if reads is None:
            gaps.append(
                Gap(
                    source=source,
                    stop=Stop.UNVERIFIED,
                    message=(
                        "the sub-problem converged, but its design did not report every "
                        "objective, so it cannot be placed on the front."
                    ),
                )
            )
            return None
        candidates.append(FrontPoint(evaluation=found, objectives=reads, source=source))
        return found

    # Anchors: each objective alone. They fix the range the levels step over.
    anchors: list[dict[str, float]] = []
    for objective in objectives:
        source = f"anchor: {objective}"
        found = record(
            optimise(trade_off.problem_for(objective, (), label=source), model, driver=driver),
            source,
        )
        if found is not None:
            reads = _reads(found, objectives)
            if reads is not None:
                anchors.append(reads)

    if len(anchors) < len(objectives):
        front, dropped = non_dominated(candidates, objectives, tolerance=trade_off.tolerance)
        return ParetoFront(
            objectives=objectives,
            points=front,
            gaps=tuple(gaps),
            dominated=dropped,
            runs=tuple(runs),
            evaluations=sum(len(one.log) for one in runs),
            notes=(
                *notes,
                "Not every objective's own optimum was found, so the range to step the "
                "other objectives over is unknown and no interior points were attempted.",
            ),
        )

    ranges = {
        one.measurement: (
            min(anchor[one.measurement] for anchor in anchors),
            max(anchor[one.measurement] for anchor in anchors),
        )
        for one in secondaries
    }
    levels = {
        one.measurement: np.linspace(*ranges[one.measurement], points)[1:-1]
        for one in secondaries
    }

    for combination in np.array(np.meshgrid(*levels.values(), indexing="ij")).reshape(
        len(secondaries), -1
    ).T:
        bounds = [
            _bound_on(objective, float(level), f"{objective.measurement}")
            for objective, level in zip(secondaries, combination, strict=True)
        ]
        label = ", ".join(
            f"{bound.measure} {bound.comparison} {float(bound.bound):g}" for bound in bounds
        )
        source = f"{primary} with {label}"
        record(
            optimise(trade_off.problem_for(primary, bounds, label=source), model, driver=driver),
            source,
        )

    front, dropped = non_dominated(candidates, objectives, tolerance=trade_off.tolerance)
    return ParetoFront(
        objectives=objectives,
        points=front,
        gaps=tuple(gaps),
        dominated=dropped,
        runs=tuple(runs),
        evaluations=sum(len(one.log) for one in runs),
        notes=notes,
    )


def sampled_front(
    experiment: Experiment,
    objectives: Iterable[Objective],
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> ParetoFront:
    """The non-dominated trade-offs among a survey's feasible, measured points."""
    chosen = tuple(objectives)
    if len(chosen) < 2:
        raise ObjectiveError("A front needs at least two objectives.")
    candidates: list[FrontPoint] = []
    unread = 0
    for one in experiment.log.evaluations:
        if not one.feasible:
            continue
        reads = _reads(one, chosen)
        if reads is None:
            unread += 1
            continue
        candidates.append(FrontPoint(evaluation=one, objectives=reads, source="survey"))
    front, dropped = non_dominated(candidates, chosen, tolerance=tolerance)
    counts = experiment.log.counts()
    notes: list[str] = [
        f"From {experiment.plan.describe()}: {counts['feasible']} feasible of "
        f"{counts['evaluations']} surveyed."
    ]
    if unread:
        notes.append(f"{unread} feasible point(s) did not report every objective and were left out.")
    return ParetoFront(
        objectives=chosen,
        points=front,
        dominated=dropped,
        statement=SAMPLED_STATEMENT,
        sampled=True,
        evaluations=counts["evaluations"],
        notes=tuple(notes),
    )


__all__ = [
    "MAX_OBJECTIVES",
    "MIN_POINTS",
    "OPTIMISED_STATEMENT",
    "SAMPLED_STATEMENT",
    "FrontPoint",
    "Gap",
    "ParetoFront",
    "TradeOff",
    "non_dominated",
    "pareto_front",
    "sampled_front",
]
