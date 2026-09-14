"""The surrogate flywheel: every solved run is a labelled datapoint.

Master plan E10.4, under the rule E22 task 1 writes for every surrogate here —
**it may rank, it may never decide, every answer carries its error basis, and
the decision point spends a real solve** (`screening.py` is that rule as types).

Three parts:

1. **Harvest.** `app.simulation.datapoints.harvest` reads an organisation's finished structural runs and
   turns each into a `Datapoint`: features read off the geometry version and the
   load case, a response read off the stored result, and the provenance of that
   response — which solver, which version, and whether its mesh dependence was
   ever measured. Every run it cannot use is listed with the reason. Nothing is
   stored: a datapoint is derived on read, so there is no second copy of a
   result to drift from the row it came from.
2. **Train.** `PowerLawSurrogate` fits `log r = b₀ + Σ bᵢ log fᵢ`. Chosen over a
   generic regressor because it is the shape linear elasticity already has:
   displacement is linear in force and in 1/E *exactly*, and a family of similar
   parts scales as a power of its dimensions — a cantilever's tip deflection is
   4FL³/(Ebh³) — so on the textbook families the model is exact and its fitted
   exponents are readable physics. Elsewhere it is a first-order model and the
   leave-one-out error says how first-order.
3. **Screen.** Through `screening.screen`: rank candidates by the surrogate,
   rebuild the top few with the real solver, act on the measurements.

**Why this is not a `Solver`, although the plan's first draft said it would drop
in as one.** A `Solver` answers a job, and a job's output is stored as a field,
drawn on the part, interpreted into prose and fed to the verification register —
every one of which treats it as measured. A surrogate answering there would be
the third forbidden speedup in `docs/MAKING_IT_FASTER.md` (sampling where the
provenance says measured) and would *decide*, which E22's rule forbids. So it
lives here, beside the optimiser, where its answers can only order candidates.

Four refusals, each of a surrogate that would train and mislead:

* a run whose response came from a copy (`cache_hit`) is skipped — the solve it
  copies is already a datapoint, and counting it twice weights one answer double;
* a feature that is constant across the data is refused by name: its exponent
  cannot be learned, and a least-squares fit would invent one;
* a non-positive feature or response is refused, because its logarithm does not
  exist — and a zero displacement is a fixture problem, not a data point;
* too few datapoints to estimate the error is refused, as a response surface is.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import numpy as np

from app.optimise.errors import OptimisationError, VariableError
from app.optimise.screening import Estimate

#: Datapoints beyond the number of coefficients a fit needs, as `surface.py` asks.
MIN_SPARE_POINTS: Final = 2

#: Relative spread below which a feature is treated as constant.
CONSTANT_SPREAD: Final = 1e-9


class SurrogateError(OptimisationError):
    """The datapoints cannot support the surrogate asked for. Nothing was trained."""


@dataclass(frozen=True)
class Datapoint:
    """One solved run as a surrogate sees it, with where the answer came from."""

    source: str
    features: Mapping[str, float]
    response: float
    solver: str = ""
    solver_version: str | None = None
    #: `single-grid` unless a convergence study measured the mesh dependence.
    mesh_convergence: str = "single-grid"


@dataclass(frozen=True)
class PowerLawSurrogate:
    """`response ≈ exp(b₀) · Π fᵢ^bᵢ`, with its leave-one-out error in log space."""

    response: str
    features: tuple[str, ...]
    intercept: float
    exponents: dict[str, float]
    samples: int
    ranges: dict[str, tuple[float, float]]
    loo_rms_log_error: float
    loo_max_log_error: float
    #: Distinct `solver version` strings among the datapoints.
    solvers: tuple[str, ...]
    #: How many datapoints came from a run whose mesh dependence was never measured.
    single_grid: int

    @classmethod
    def train(
        cls, datapoints: Iterable[Datapoint], features: Sequence[str], *, response: str = ""
    ) -> PowerLawSurrogate:
        points = list(datapoints)
        names = tuple(features)
        if not names:
            raise SurrogateError("A surrogate needs at least one feature to learn from.")
        needed = len(names) + 1 + MIN_SPARE_POINTS
        if len(points) < needed:
            raise SurrogateError(
                f"{len(names)} feature(s) need at least {needed} datapoints for the surrogate to "
                f"estimate its own error; {len(points)} given. Solve more runs, or learn fewer "
                "features."
            )
        for point in points:
            for name in names:
                value = point.features.get(name)
                if value is None:
                    raise SurrogateError(f"Datapoint {point.source} has no {name}.")
                if not value > 0.0:
                    raise SurrogateError(
                        f"Datapoint {point.source} has {name} = {value:g}. A power law needs "
                        "every feature positive; a zero or negative one has no logarithm."
                    )
            if not point.response > 0.0:
                raise SurrogateError(
                    f"Datapoint {point.source} has a response of {point.response:g}. A zero "
                    "displacement or stress is a fixture or load problem, not a data point, "
                    "and it has no logarithm."
                )

        logs = np.log(np.asarray([[p.features[n] for n in names] for p in points], dtype=np.float64))
        spread = logs.max(axis=0) - logs.min(axis=0)
        constant = [name for name, width in zip(names, spread, strict=True) if width <= CONSTANT_SPREAD]
        if constant:
            raise SurrogateError(
                f"Every datapoint has the same {', '.join(constant)}, so its influence cannot "
                "be learned — a fit would invent an exponent for it. Drop it from the features, "
                "or solve runs that vary it."
            )
        design = np.column_stack([np.ones(len(points)), logs])
        singular = np.linalg.svd(design, compute_uv=False)
        if singular[-1] <= 1e-10 * singular[0]:
            raise SurrogateError(
                "The features move together across the datapoints (one is a power of the "
                "others), so their separate exponents cannot be told apart. Drop one, or "
                "solve runs that vary them independently."
            )
        target = np.log(np.asarray([p.response for p in points], dtype=np.float64))
        coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
        residual = target - design @ coefficients
        q, _ = np.linalg.qr(design)
        leverage = np.sum(q**2, axis=1)
        if np.any(leverage >= 1.0 - 1e-9):
            raise SurrogateError(
                "At least one datapoint alone determines a coefficient, so its leave-one-out "
                "error is undefined. Solve more runs spread across the features."
            )
        loo = residual / (1.0 - leverage)
        return cls(
            response=response,
            features=names,
            intercept=float(coefficients[0]),
            exponents={name: float(c) for name, c in zip(names, coefficients[1:], strict=True)},
            samples=len(points),
            ranges={
                name: (float(np.exp(logs[:, i].min())), float(np.exp(logs[:, i].max())))
                for i, name in enumerate(names)
            },
            loo_rms_log_error=float(np.sqrt(np.mean(loo**2))),
            loo_max_log_error=float(np.max(np.abs(loo))),
            solvers=tuple(
                sorted({f"{p.solver} {p.solver_version or '(version not recorded)'}" for p in points})
            ),
            single_grid=sum(1 for p in points if p.mesh_convergence == "single-grid"),
        )

    @property
    def error_factor(self) -> float:
        """The one-sigma multiplicative band: typically within ×/÷ this factor."""
        return math.exp(self.loo_rms_log_error)

    def predict(self, values: Mapping[str, float]) -> Estimate:
        missing = [name for name in self.features if name not in values]
        if missing:
            raise VariableError(f"The surrogate needs {missing} to estimate {self.response}.")
        logs = []
        for name in self.features:
            value = float(values[name])
            if not value > 0.0:
                raise VariableError(f"{name} = {value:g} has no logarithm; features must be positive.")
            logs.append(math.log(value))
        estimate = math.exp(
            self.intercept + sum(self.exponents[name] * one for name, one in zip(self.features, logs, strict=True))
        )
        beyond = [
            name
            for name in self.features
            if not self.ranges[name][0] <= float(values[name]) <= self.ranges[name][1]
        ]
        factor = self.error_factor
        return Estimate(
            value=estimate,
            low=estimate / factor,
            high=estimate * factor,
            basis=(
                f"×/÷ {factor:.4g}, the leave-one-out RMS error of a power law over "
                f"{self.samples} solved runs ({self.single_grid} of them single-grid, so their "
                "own mesh error is not in this band)"
            ),
            extrapolated=bool(beyond),
            note=(
                f"outside the trained range in {', '.join(beyond)}: the band was measured "
                "inside it and says nothing about here."
                if beyond
                else ""
            ),
        )

    def summary(self) -> str:
        law = " · ".join(f"{name}^{self.exponents[name]:+.4g}" for name in self.features)
        return (
            f"{self.response or 'response'} ≈ {math.exp(self.intercept):.6g} · {law}, trained on "
            f"{self.samples} solved runs from {', '.join(self.solvers)}; leave-one-out error "
            f"×/÷ {self.error_factor:.4g} (worst ×/÷ {math.exp(self.loo_max_log_error):.4g}). "
            f"{self.single_grid} of the runs are single-grid: the surrogate inherits whatever "
            "mesh error they carry, and this band does not include it."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "response": self.response,
            "features": list(self.features),
            "intercept": self.intercept,
            "exponents": dict(self.exponents),
            "samples": self.samples,
            "ranges": {k: list(v) for k, v in self.ranges.items()},
            "loo_rms_log_error": self.loo_rms_log_error,
            "error_factor": self.error_factor,
            "solvers": list(self.solvers),
            "single_grid": self.single_grid,
            "provenance": "approximated",
        }


__all__ = [
    "MIN_SPARE_POINTS",
    "Datapoint",
    "PowerLawSurrogate",
    "SurrogateError",
]
