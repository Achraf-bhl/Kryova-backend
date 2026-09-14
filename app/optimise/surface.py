"""Response surfaces: a cheap approximation that says how wrong it is.

Master plan 10.3. A polynomial fitted by least squares to a survey's measured
points (`doe.py`), in the design variables' own names. It answers "roughly what
would this measure at a point nobody built" in microseconds, and it is the
simplest instance of the rule E22 task 1 writes for every surrogate in this
product: **an approximation may rank, and may never decide.**

That rule lives in `screening.py` as types — an `Estimate` with a band and
no verdict, a `Ranking` with no `best`, and `screen`, which rebuilds before it
recommends — and a surface reaches a caller only through them.

**The error basis is leave-one-out, not R².** R² is how well the surface passes
through the points it was fitted to, and a surface with as many coefficients as
points passes through all of them with R² = 1 while predicting nothing. The
leave-one-out residual — each point predicted by the surface fitted without it,
computed exactly from the hat matrix, `e_i / (1 − h_ii)` — is an estimate of
error at a point the fit did not see, which is the only kind of point anyone
asks a surface about. Both are reported; only one is the error basis.

Three refusals, each of a surface that would fit and mislead:

1. **Too few points to estimate its own error.** A surface needs more measured
   points than coefficients, by `MIN_SPARE_POINTS`, or every leverage is 1 and
   the leave-one-out error is undefined — a fit that interpolates is not
   evidence about anything between the points.
2. **A survey that cannot tell the terms apart.** Two factorial levels cannot
   see curvature: every `x²` column is a constant and duplicates the intercept.
   `lstsq` would return *a* coefficient for it — the minimum-norm one, which is
   meaningless — so a rank-deficient design is refused naming the fix.
3. **A point outside a design variable's bounds.** Refused. A point inside the
   bounds but outside what the survey sampled is answered and flagged
   `extrapolated`, because the bounds are a statement about the design and the
   sampled range is a statement about the survey, and only the first is the
   caller's to relax.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from app.design.assertions import read_measurement
from app.optimise.errors import OptimisationError, VariableError
from app.optimise.evaluate import Evaluation
from app.optimise.problem import OptimisationProblem
from app.optimise.screening import APPROXIMATED, Estimate

#: Measured points required beyond the number of coefficients. Two, not one:
#: with one spare point every leverage can still be 1 at the design's corners.
MIN_SPARE_POINTS: Final = 2

#: Relative singular-value floor below which a column is taken as a combination
#: of the others. Scaled to the largest singular value, since the columns are
#: built in normalised coordinates on [−1, 1] and are all order one.
RANK_TOLERANCE: Final = 1e-10

#: Leverage at or above which a point is its own prediction. `e/(1−h)` is then
#: a division by roughly nothing and the leave-one-out error is not a number.
MAX_LEVERAGE: Final = 1.0 - 1e-9


class SurfaceError(OptimisationError):
    """The survey cannot support the surface asked for. Nothing was fitted."""


def _terms(n_variables: int, degree: int) -> tuple[tuple[int, ...], ...]:
    """Exponent tuples for every monomial of total degree <= `degree`."""
    out: list[tuple[int, ...]] = []
    for total in range(degree + 1):
        for combination in itertools.combinations_with_replacement(range(n_variables), total):
            exponents = [0] * n_variables
            for index in combination:
                exponents[index] += 1
            out.append(tuple(exponents))
    return tuple(out)


@dataclass(frozen=True)
class ResponseSurface:
    """A least-squares polynomial over normalised design variables."""

    measurement: str
    names: tuple[str, ...]
    bounds: tuple[tuple[float, float], ...]
    degree: int
    terms: tuple[tuple[int, ...], ...]
    coefficients: tuple[float, ...]
    #: Measured points the fit used.
    samples: int
    #: Survey points left out, each with why — never silently dropped.
    excluded: tuple[str, ...]
    #: The range each variable was actually sampled over, in declared order.
    sampled: tuple[tuple[float, float], ...]
    r_squared: float
    loo_rms_error: float
    loo_max_error: float

    @classmethod
    def fit(
        cls,
        problem: OptimisationProblem,
        evaluations: Iterable[Evaluation],
        *,
        measurement: str | None = None,
        degree: int = 2,
    ) -> ResponseSurface:
        """Fit to every point that built and reported `measurement`.

        `measurement` defaults to the problem's objective path, and may name any
        path in the payload — a constraint quantity is as reasonable a thing to
        approximate as the objective.
        """
        if degree not in (1, 2):
            raise SurfaceError(
                f"A degree-{degree} response surface is not offered. Degree 1 (linear) and "
                "degree 2 (quadratic, with interactions) are; a higher degree needs a "
                "survey large enough that it is usually cheaper to build the points."
            )
        path = measurement or problem.objective.measurement
        names = problem.names
        rows: list[list[float]] = []
        values: list[float] = []
        excluded: list[str] = []
        for one in evaluations:
            where = ", ".join(f"{name}={one.values.get(name, float('nan')):g}" for name in names)
            if not one.built:
                excluded.append(f"{where}: did not build — {one.reason}")
                continue
            read = read_measurement(one.measurements, path)
            if read is None or not math.isfinite(read):
                excluded.append(f"{where}: built, but reported no finite {path!r}.")
                continue
            if any(name not in one.values for name in names):
                excluded.append(f"{where}: does not set every design variable.")
                continue
            rows.append([float(one.values[name]) for name in names])
            values.append(float(read))

        terms = _terms(len(names), degree)
        needed = len(terms) + MIN_SPARE_POINTS
        if len(rows) < needed:
            raise SurfaceError(
                f"A degree-{degree} surface over {len(names)} variable(s) has {len(terms)} "
                f"coefficients and needs at least {needed} measured points to estimate its "
                f"own error; {len(rows)} measured"
                + (f" ({len(excluded)} left out)" if excluded else "")
                + ". A surface through exactly as many points as it has coefficients "
                "passes through all of them and predicts nothing. Survey more points."
            )

        x = np.asarray(rows, dtype=np.float64)
        y = np.asarray(values, dtype=np.float64)
        bounds = problem.bounds
        design = _design_matrix(_normalise(x, bounds), terms)

        singular = np.linalg.svd(design, compute_uv=False)
        rank = int(np.sum(singular > RANK_TOLERANCE * singular[0]))
        if rank < len(terms):
            raise SurfaceError(
                f"The surveyed points cannot tell the {len(terms)} terms of a degree-{degree} "
                f"surface apart (the design has rank {rank}). The usual cause is a two-level "
                "factorial asked for a quadratic: at two levels every squared term is a "
                "constant. Survey at three or more levels, use a Latin hypercube, or fit "
                "degree 1."
            )

        coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
        fitted = design @ coefficients
        residual = y - fitted
        total = float(np.sum((y - y.mean()) ** 2))
        r_squared = 1.0 - float(np.sum(residual**2)) / total if total > 0.0 else 1.0

        # Leave-one-out residuals exactly, from the hat matrix's diagonal —
        # no refitting, and no approximation in it.
        q, _ = np.linalg.qr(design)
        leverage = np.sum(q**2, axis=1)
        if np.any(leverage >= MAX_LEVERAGE):
            raise SurfaceError(
                "At least one surveyed point fully determines its own coefficient "
                "(leverage 1), so its leave-one-out error is undefined and so is the "
                "surface's error basis. Add points near the corners of the design space."
            )
        loo = residual / (1.0 - leverage)

        sampled = tuple((float(x[:, i].min()), float(x[:, i].max())) for i in range(len(names)))
        return cls(
            measurement=path,
            names=names,
            bounds=bounds,
            degree=degree,
            terms=terms,
            coefficients=tuple(float(c) for c in coefficients),
            samples=len(rows),
            excluded=tuple(excluded),
            sampled=sampled,
            r_squared=r_squared,
            loo_rms_error=float(np.sqrt(np.mean(loo**2))),
            loo_max_error=float(np.max(np.abs(loo))),
        )

    def predict(self, values: Mapping[str, float]) -> Estimate:
        """The surface's value at a point, with its error basis and extrapolation flag."""
        missing = [name for name in self.names if name not in values]
        if missing:
            raise VariableError(
                f"The surface over {', '.join(self.names)} needs a value for {missing}."
            )
        point = [float(values[name]) for name in self.names]
        for name, value, (lower, upper) in zip(self.names, point, self.bounds, strict=True):
            if not lower <= value <= upper:
                raise VariableError(
                    f"{name}={value:g} is outside its design bounds [{lower:g}, {upper:g}]. A "
                    "response surface is not asked about designs the problem does not "
                    "allow; widen the bounds and survey again if that region matters."
                )
        beyond = [
            name
            for name, value, (low, high) in zip(self.names, point, self.sampled, strict=True)
            if not low <= value <= high
        ]
        row = _design_matrix(
            _normalise(np.asarray([point], dtype=np.float64), self.bounds), self.terms
        )
        value = float(row[0] @ np.asarray(self.coefficients))
        note = (
            f"outside the surveyed range in {', '.join(beyond)}: the error basis was measured "
            "inside it and says nothing about here."
            if beyond
            else ""
        )
        return Estimate(
            value=value,
            low=value - self.loo_rms_error,
            high=value + self.loo_rms_error,
            basis=(
                f"± the leave-one-out RMS error ({self.loo_rms_error:.4g}) of a degree-"
                f"{self.degree} response surface over {self.samples} measured points"
            ),
            extrapolated=bool(beyond),
            note=note,
        )

    def summary(self) -> str:
        lines = [
            f"Degree-{self.degree} response surface for {self.measurement} over "
            f"{', '.join(self.names)}: {self.samples} measured points, "
            f"R² {self.r_squared:.4f} on the fitted points, leave-one-out RMS error "
            f"{self.loo_rms_error:.4g} (worst {self.loo_max_error:.4g}).",
        ]
        if self.excluded:
            lines.append(f"{len(self.excluded)} surveyed point(s) left out:")
            lines.extend(f"  {one}" for one in self.excluded)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "measurement": self.measurement,
            "names": list(self.names),
            "degree": self.degree,
            "samples": self.samples,
            "excluded": list(self.excluded),
            "r_squared": self.r_squared,
            "loo_rms_error": self.loo_rms_error,
            "loo_max_error": self.loo_max_error,
            "provenance": APPROXIMATED,
        }


def _normalise(
    x: NDArray[np.float64], bounds: Sequence[tuple[float, float]]
) -> NDArray[np.float64]:
    lower = np.asarray([b[0] for b in bounds], dtype=np.float64)
    upper = np.asarray([b[1] for b in bounds], dtype=np.float64)
    return np.asarray(2.0 * (x - lower) / (upper - lower) - 1.0, dtype=np.float64)


def _design_matrix(
    z: NDArray[np.float64], terms: Sequence[tuple[int, ...]]
) -> NDArray[np.float64]:
    columns = [np.prod(z ** np.asarray(exponents), axis=1) for exponents in terms]
    return np.column_stack(columns)


__all__ = [
    "MIN_SPARE_POINTS",
    "ResponseSurface",
    "SurfaceError",
]
