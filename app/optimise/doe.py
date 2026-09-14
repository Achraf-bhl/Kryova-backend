"""Designs of experiments: which points to build before anything is decided.

Master plan 10.3. An optimiser walks; a design of experiments *surveys*. It
chooses its points before seeing a single answer, builds all of them, and hands
back the record — which is what a response surface is fitted to, what a Pareto
front can be read off, and what a surrogate is trained on.

Two plans, because they answer different questions:

* **Full factorial** — every combination of `levels` evenly spaced values per
  variable, bounds included. Exhaustive, and exponential: three variables at
  five levels is 125 rebuilds. It is the plan that can see an interaction on a
  grid a person can read.
* **Latin hypercube** — `samples` points placed so that, projected onto any one
  variable, exactly one point falls in each of `samples` equal strata. Linear in
  the sample count whatever the number of variables, which is why it is the
  plan a surface over more than two or three dimensions is fitted to.

Three rules, and each is the rest of this package's rule applied to a survey:

1. **A plan is refused before it is built if it exceeds the problem's budget.**
   `max_evaluations` is the number of rebuilds the caller agreed to pay for, and
   a survey that silently truncated itself to fit would be a different plan
   reported under the requested plan's name.
2. **A Latin hypercube carries its seed, and the seed is required.** A random
   plan nobody can regenerate is a record nobody can check. There is no default
   seed on purpose: a default makes every run look reproducible while every run
   quietly uses the same points.
3. **Every point is built through the `Evaluator`**, so a point that did not
   build is in the record as one that did not build, with the kernel's reason —
   never dropped. A surface fitted to the survivors says how many were left out
   and why (`surface.py`).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

import numpy as np

from app.optimise.errors import VariableError
from app.optimise.evaluate import Evaluation, EvaluationLog, Evaluator, Model
from app.optimise.problem import OptimisationProblem

#: The `purpose` every survey evaluation is recorded under, so a log read later
#: can tell a surveyed point from one an optimiser chose.
PURPOSE: Final = "doe"

#: Fewer than two levels is not a factorial — one level is a single point
#: repeated in every variable, and nothing about a response can be read off it.
MIN_LEVELS: Final = 2


class Sampling(StrEnum):
    FULL_FACTORIAL = "full-factorial"
    LATIN_HYPERCUBE = "latin-hypercube"


@dataclass(frozen=True)
class Plan:
    """The points a survey will build, chosen before any of them is."""

    problem: OptimisationProblem
    method: Sampling
    points: tuple[dict[str, float], ...]

    #: The Latin hypercube's seed. None for a full factorial, which is not random.
    seed: int | None = None

    #: Levels per variable, for a factorial. Empty for a hypercube.
    levels: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.points)

    def describe(self) -> str:
        names = ", ".join(self.problem.names)
        if self.method is Sampling.FULL_FACTORIAL:
            per = " × ".join(str(self.levels[name]) for name in self.problem.names)
            return f"full factorial over {names}: {per} = {len(self)} points"
        return (
            f"Latin hypercube over {names}: {len(self)} points, seed {self.seed}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method.value,
            "seed": self.seed,
            "levels": dict(self.levels),
            "points": [dict(one) for one in self.points],
        }


def _within_budget(problem: OptimisationProblem, count: int, what: str) -> None:
    if count > problem.max_evaluations:
        raise VariableError(
            f"{problem.name}: {what} is {count} rebuilds and the problem's budget is "
            f"{problem.max_evaluations}. Raise max_evaluations to at least {count}, or "
            "ask for fewer points — a survey is never truncated to fit, because the "
            "points it would drop are chosen by nobody."
        )


def full_factorial(problem: OptimisationProblem, levels: int | dict[str, int]) -> Plan:
    """Every combination of evenly spaced values, bounds included.

    `levels` is one count for every variable or a count per variable by name.
    """
    per: dict[str, int] = (
        {name: levels for name in problem.names}
        if isinstance(levels, int)
        else dict(levels)
    )
    unknown = sorted(set(per) - set(problem.names))
    if unknown:
        raise VariableError(
            f"{problem.name}: levels were given for {unknown}, which are not design "
            f"variables of this problem. Declared: {', '.join(problem.names)}."
        )
    missing = [name for name in problem.names if name not in per]
    if missing:
        raise VariableError(
            f"{problem.name}: no level count for {missing}. Give one per variable, or "
            "one integer for all of them."
        )
    for name, count in per.items():
        if count < MIN_LEVELS:
            raise VariableError(
                f"{problem.name}: {name} has {count} level(s). A factorial needs at least "
                f"{MIN_LEVELS} per variable — one level is the same value everywhere, and "
                "no response to that variable can be read off it."
            )

    total = 1
    for name in problem.names:
        total *= per[name]
    _within_budget(problem, total, "a full factorial at these levels")

    axes = [
        np.linspace(variable.lower, variable.upper, per[variable.name])
        for variable in problem.variables
    ]
    points = tuple(
        {name: float(value) for name, value in zip(problem.names, combination, strict=True)}
        for combination in itertools.product(*axes)
    )
    return Plan(problem=problem, method=Sampling.FULL_FACTORIAL, points=points, levels=per)


def latin_hypercube(problem: OptimisationProblem, samples: int, *, seed: int) -> Plan:
    """`samples` points, one per stratum in every variable's projection.

    Each point is placed uniformly at random *within* its stratum rather than at
    the stratum's centre: centred hypercubes put every point on a lattice, and a
    quadratic fitted to a lattice can be singular in exactly the cross terms a
    surface exists to estimate.
    """
    if samples < 2:
        raise VariableError(
            f"{problem.name}: a Latin hypercube of {samples} point(s) has one stratum per "
            "variable and says nothing about how a response varies. Ask for at least 2."
        )
    _within_budget(problem, samples, f"a Latin hypercube of {samples} points")

    rng = np.random.default_rng(seed)
    columns = []
    for variable in problem.variables:
        strata = rng.permutation(samples)
        offsets = rng.random(samples)
        unit = (strata + offsets) / samples
        columns.append(variable.lower + unit * variable.span)
    grid = np.column_stack(columns)
    points = tuple(
        {
            variable.name: variable.clamp(float(value))
            for variable, value in zip(problem.variables, row, strict=True)
        }
        for row in grid
    )
    return Plan(problem=problem, method=Sampling.LATIN_HYPERCUBE, points=points, seed=seed)


@dataclass(frozen=True)
class Experiment:
    """A plan and everything that came back from building it."""

    plan: Plan
    log: EvaluationLog

    @property
    def problem(self) -> OptimisationProblem:
        return self.plan.problem

    def measured(self) -> tuple[Evaluation, ...]:
        return tuple(one for one in self.log.evaluations if one.measured)

    def failures(self) -> tuple[Evaluation, ...]:
        return self.log.failures()

    def summary(self) -> str:
        counts = self.log.counts()
        return (
            f"{self.plan.describe()}. {counts['built']} of {counts['evaluations']} built, "
            f"{counts['feasible']} feasible."
        )


def run(plan: Plan, model: Model) -> Experiment:
    """Build every point in the plan, recording each outcome, failures included."""
    evaluator = Evaluator(plan.problem, model)
    for point in plan.points:
        evaluator.at(point, purpose=PURPOSE)
    return Experiment(plan=plan, log=evaluator.log)


__all__ = [
    "MIN_LEVELS",
    "PURPOSE",
    "Experiment",
    "Plan",
    "Sampling",
    "full_factorial",
    "latin_hypercube",
    "run",
]
