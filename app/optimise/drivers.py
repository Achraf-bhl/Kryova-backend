"""The optimisers themselves, behind one seam.

Same discipline as `solve.Solver` and `jobs.JobQueue`: an interface with one
method, so swapping the optimiser does not touch the vocabulary, the honesty
rules, or the record. A driver receives an `Evaluator` and returns a
`DriverOutcome` — where it stopped, whether it believes it converged, and why.
It never decides whether the run *succeeded*: that is `result.py`'s job, and it
checks the driver's opinion against the measured constraints before agreeing
with it.

**Two drivers ship, and which is the default is a decision worth stating.**

`ScipyDriver` is the default. It uses `scipy.optimize.minimize` — SLSQP where
there are constraints, L-BFGS-B where there are only bounds — and takes its
gradient from `app/design/sensitivity.py` when one is available. scipy is
already a dependency of this project, for the FEA.

`OpenMdaoDriver` is the master plan's named MDO framework (10.3, Apache-2.0,
NASA Glenn) and it is here because that is the register's choice and because the
work it is *for* is coming: multidisciplinary runs where a thermal solve feeds a
structural solve feeds a mass, and OpenMDAO's unified-derivatives machinery is
the reason to have it rather than a wrapper around the same SLSQP. It is not the
default today for an honest reason: for a single-discipline problem in a handful
of variables, `ScipyOptimizeDriver` *is* `scipy.optimize.minimize`, so choosing
it would mean carrying a framework to reach a function we already call directly.
It becomes the default the first time a run has two disciplines in it.

**Measured on the Windows seat, 2026-09-06:** OpenMDAO 3.45.0 installs and
imports cleanly on Python 3.14.3 (it is a pure-Python wheel; the only additions
are networkx and requests). One caveat found the hard way — an install into a
path near Windows' 260-character limit fails *partially*, leaving a package that
imports and then dies on `No module named 'openmdao.jacobians'`. That is
MAX_PATH, not Python 3.14. Enable long paths, or install somewhere shorter.

**A driver that is not installed is a `DriverUnavailable`, not an ImportError.**
The message names the driver that is present, because the recovery from "the
optional MDO framework is missing" is to use the one that is not.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

from app.optimise.errors import DriverUnavailable
from app.optimise.evaluate import Evaluation, Evaluator
from app.optimise.gradients import GradientReport, objective_gradient

#: The name a caller passes to `optimise(driver=...)`.
SCIPY: Final = "scipy"
OPENMDAO: Final = "openmdao"


@dataclass(frozen=True)
class DriverOutcome:
    """Where a driver stopped and what it thinks of the point it stopped at.

    `converged` is the driver's *opinion* and is never the final word — see
    `OptimisationResult.converged_at`, which refuses to agree with it at a point
    that violates a measured constraint.
    """

    converged: bool
    message: str

    #: The point the driver ended on, as it reported it. None where it never
    #: produced one.
    values: Mapping[str, float] | None = None

    #: True where the driver stopped because it ran out of iterations or
    #: evaluations rather than because it met its tolerance.
    budget_spent: bool = False

    #: True where the optimiser itself raised. Kept apart from an ordinary
    #: non-convergence because the recovery is different in kind: a driver that
    #: threw is a bug or a bad option, and says nothing at all about the design.
    failed: bool = False

    #: Every gradient computed during the run, in order. Reported so a reader
    #: can see whether ours or the driver's differencing steered the search.
    gradients: tuple[GradientReport, ...] = ()


class Driver(Protocol):
    """One optimisation algorithm. The seam."""

    @property
    def name(self) -> str: ...

    def available(self) -> tuple[bool, str]:
        """`(installed, why not)`. Checked before a run so the refusal is early."""

    def run(self, evaluator: Evaluator, start: Mapping[str, float]) -> DriverOutcome:
        """Search from `start`, evaluating only through `evaluator`."""


# -- scipy -------------------------------------------------------------------


class ScipyDriver:
    """`scipy.optimize.minimize`, with our gradients where we have them.

    SLSQP when the problem has constraints, L-BFGS-B when it has only bounds.
    Both honour bounds exactly, which matters here in a way it does not in a
    textbook problem: a bound is often the difference between a part that builds
    and one that does not, and an optimiser that steps outside it to look around
    spends real rebuilds finding that out.
    """

    __slots__ = ("_gradients", "_method")

    def __init__(self, *, method: str | None = None, gradients: bool = True) -> None:
        self._method = method
        self._gradients = gradients

    @property
    def name(self) -> str:
        return SCIPY if self._method is None else f"{SCIPY}/{self._method}"

    def available(self) -> tuple[bool, str]:
        try:
            import scipy.optimize  # noqa: F401
        except ImportError as missing:  # pragma: no cover - scipy is a hard dependency
            return False, f"scipy is not installed here ({missing})."
        return True, ""

    def method_for(self, evaluator: Evaluator) -> str:
        if self._method is not None:
            return self._method
        return "SLSQP" if evaluator.problem.constraints else "L-BFGS-B"

    def run(self, evaluator: Evaluator, start: Mapping[str, float]) -> DriverOutcome:
        from scipy.optimize import minimize

        problem = evaluator.problem
        method = self.method_for(evaluator)
        collected: list[GradientReport] = []

        # SLSQP has no notion of a "buildability" constraint, so it is passed as
        # an ordinary inequality alongside the declared ones. It is not recorded
        # as a declared constraint anywhere — `Evaluator.driver_constraints`
        # appends it, and only there.
        def objective(vector: Sequence[float]) -> float:
            return evaluator.driver_objective(self._at(evaluator, vector))

        def jacobian(vector: Sequence[float]) -> list[float]:
            report = objective_gradient(evaluator, problem.named(vector))
            collected.append(report)
            found = report.vector(problem)
            if found is None:
                # Fall back to scipy's own differencing at this point. Recorded
                # as a report saying so, because a run that silently changed
                # gradient scheme half way through would be unreadable later.
                return _numerical_gradient(objective, vector)
            sign = problem.objective.sense.signum
            return [sign * value for value in found]

        constraints: list[dict[str, Any]] = []
        if method in ("SLSQP", "COBYLA", "trust-constr"):
            constraints = [
                {
                    "type": "ineq",
                    "fun": lambda vector: evaluator.driver_constraints(
                        self._at(evaluator, vector)
                    ),
                }
            ]

        options: dict[str, Any] = {"maxiter": problem.max_evaluations}
        if method in ("SLSQP", "L-BFGS-B"):
            options["ftol"] = problem.tolerance

        try:
            outcome = minimize(
                objective,
                x0=problem.vector(start),
                method=method,
                jac=jacobian if self._gradients else None,
                bounds=problem.bounds,
                constraints=constraints,
                options=options,
            )
        except Exception as failed:  # noqa: BLE001 - a driver bug is not a design finding
            return DriverOutcome(
                converged=False,
                message=(
                    f"scipy's {method} raised: {failed}. That is an optimiser problem "
                    "rather than a statement about the design; try another method or "
                    "check the design variables' scaling."
                ),
                failed=True,
                gradients=tuple(collected),
            )

        # `converged` is scipy's own opinion and nothing else. Whether the
        # evaluation budget ran out is reported separately and the *decision* is
        # made in `run.py`: a driver that converged on values the budget stopped
        # it rebuilding converged on stale penalties, and only the caller of both
        # is in a position to say so.
        return DriverOutcome(
            converged=bool(outcome.success),
            message=str(getattr(outcome, "message", "")).strip() or "scipy returned no message.",
            values=problem.named(outcome.x),
            budget_spent=evaluator.exhausted
            or bool(getattr(outcome, "status", 0) in (1, 3) and not outcome.success),
            gradients=tuple(collected),
        )

    @staticmethod
    def _at(evaluator: Evaluator, vector: Sequence[float]) -> Evaluation:
        return evaluator.at(evaluator.problem.named(vector))


def _numerical_gradient(
    objective: Any, vector: Sequence[float], *, step: float = 1e-6
) -> list[float]:
    """Forward difference, used only where our own gradient was unavailable.

    Deliberately crude and deliberately local. It exists so a single unbuildable
    probe does not end the run; it is not an alternative implementation of
    `app/design/sensitivity.py`, which is what the rest of the run uses and which
    knows about topology changes and step sizing.
    """
    base = objective(vector)
    out: list[float] = []
    for index in range(len(vector)):
        moved = list(vector)
        scale = abs(moved[index]) or 1.0
        delta = step * scale
        moved[index] += delta
        out.append((objective(moved) - base) / delta)
    return out


# -- OpenMDAO ----------------------------------------------------------------


class OpenMdaoDriver:
    """The master plan's MDO framework, driving the same evaluator.

    The part is wrapped as a single `ExplicitComponent` with one input per design
    variable and one output per objective/constraint, and OpenMDAO's own
    `ScipyOptimizeDriver` runs SLSQP over it. Partials are declared `fd` —
    OpenMDAO differences the component itself, so this driver does *not* use
    `app/design/sensitivity.py`. That is a real difference between the two
    drivers and it is stated rather than hidden: OpenMDAO's finite differencing
    does not know that a failed rebuild is not zero sensitivity, so a run near a
    buildability edge is better served by `ScipyDriver` today.

    Every rebuild still goes through the `Evaluator`, so the history, the budget
    and the honesty rules are identical whichever driver ran.
    """

    __slots__ = ("_optimizer",)

    def __init__(self, *, optimizer: str = "SLSQP") -> None:
        self._optimizer = optimizer

    @property
    def name(self) -> str:
        return f"{OPENMDAO}/{self._optimizer}"

    def available(self) -> tuple[bool, str]:
        try:
            import openmdao.api  # noqa: F401
        except ImportError as missing:
            return False, (
                f"OpenMDAO is not installed here ({missing}). Install it with "
                "`pip install openmdao`, or run with driver='scipy', which is the "
                "default and needs nothing extra. On Windows, install into a short "
                "path or enable long-path support: an install truncated by MAX_PATH "
                "imports and then fails on a missing submodule."
            )
        return True, ""

    def run(self, evaluator: Evaluator, start: Mapping[str, float]) -> DriverOutcome:
        installed, why = self.available()
        if not installed:
            raise DriverUnavailable(why)

        import numpy as np
        import openmdao.api as om

        problem = evaluator.problem
        names = problem.names
        n_constraints = len(problem.constraints) + 1  # + buildability, see evaluate.py

        driver_self = self

        class _Part(om.ExplicitComponent):  # type: ignore[misc]
            """The geometry, as OpenMDAO sees it: floats in, floats out."""

            def setup(self) -> None:
                for variable in problem.variables:
                    self.add_input(driver_self._tag(variable.name), val=float(start[variable.name]))
                self.add_output("objective", val=0.0)
                self.add_output("constraints", val=np.zeros(n_constraints))
                self.declare_partials("*", "*", method="fd")

            def compute(self, inputs: Any, outputs: Any) -> None:
                values = {
                    name: float(inputs[driver_self._tag(name)][0]) for name in names
                }
                evaluation = evaluator.at(values)
                outputs["objective"] = evaluator.driver_objective(evaluation)
                outputs["constraints"] = np.array(
                    evaluator.driver_constraints(evaluation), dtype=float
                )

        om_problem = om.Problem(reports=False)
        om_problem.model.add_subsystem("part", _Part(), promotes=["*"])
        om_problem.driver = om.ScipyOptimizeDriver(optimizer=self._optimizer)
        om_problem.driver.options["tol"] = problem.tolerance
        om_problem.driver.options["maxiter"] = problem.max_evaluations
        om_problem.driver.options["disp"] = False

        for variable in problem.variables:
            om_problem.model.add_design_var(
                self._tag(variable.name), lower=variable.lower, upper=variable.upper
            )
        om_problem.model.add_objective("objective")
        om_problem.model.add_constraint("constraints", lower=0.0)

        try:
            om_problem.setup()
            for variable in problem.variables:
                om_problem.set_val(self._tag(variable.name), float(start[variable.name]))
            outcome = om_problem.run_driver()
        except Exception as broke:  # noqa: BLE001 - a driver bug is not a design finding
            return DriverOutcome(
                converged=False,
                message=(
                    f"OpenMDAO's {self._optimizer} raised: {broke}. That is an optimiser "
                    "problem rather than a statement about the design."
                ),
                failed=True,
            )

        values = {
            name: float(om_problem.get_val(self._tag(name))[0]) for name in names
        }
        # `run_driver` used to return a truthy *failure* flag and now returns a
        # DriverResult whose `success` is the opposite polarity; reading the
        # object as a bool still works and prints a deprecation warning. Read
        # `success` where it exists and fall back to the old sense where it does
        # not, so this driver is not pinned to one OpenMDAO minor version.
        success = getattr(outcome, "success", None)
        succeeded = bool(success) if success is not None else not bool(outcome)
        return DriverOutcome(
            converged=succeeded,
            message=(
                f"OpenMDAO {self._optimizer} "
                + ("converged." if succeeded else "did not converge.")
            ),
            values=values,
            budget_spent=evaluator.exhausted,
        )

    @staticmethod
    def _tag(name: str) -> str:
        r"""An OpenMDAO variable name for one of ours.

        OpenMDAO addresses variables by a dotted path, so `Pad.1\length_mm`
        would be read as a system called `Pad` containing a variable called
        `1\length_mm` — a promotion that resolves to nothing and reports it as a
        connection error a long way from the cause. Sanitised here, and only
        here.
        """
        out = "".join(character if character.isalnum() else "_" for character in name)
        return out if out[:1].isalpha() else f"v_{out}"


# -- choosing one ------------------------------------------------------------

_DRIVERS: Final[dict[str, Any]] = {
    SCIPY: ScipyDriver,
    OPENMDAO: OpenMdaoDriver,
}


def driver_named(name: str | Driver | None) -> Driver:
    """Resolve a driver by name, or pass one through.

    `None` is the default driver — scipy — and a name this build does not know
    is refused with the list of names it does, rather than falling back silently
    to the default. A run that quietly used a different optimiser from the one
    asked for is a result nobody can reproduce.
    """
    if name is None:
        return ScipyDriver()
    if not isinstance(name, str):
        return name
    factory = _DRIVERS.get(name.strip().lower())
    if factory is None:
        known = ", ".join(sorted(_DRIVERS))
        raise DriverUnavailable(
            f"{name!r} is not an optimiser this build knows. Available: {known}."
        )
    return factory()  # type: ignore[no-any-return]


def usable(driver: Driver) -> tuple[bool, str]:
    """Whether a driver can actually run here, without running it."""
    return driver.available()


def is_close(left: float, right: float, *, tolerance: float) -> bool:
    """Shared by the drivers' convergence reporting. Relative where it can be.

    Absolute comparison is wrong for the same reason it is wrong for eigenvalues
    in `app/solve/`: an objective is a mass in kilograms or a volume in cubic
    millimetres, five orders of magnitude apart, and one threshold cannot serve
    both.
    """
    return math.isclose(left, right, rel_tol=tolerance, abs_tol=tolerance)


__all__ = [
    "OPENMDAO",
    "SCIPY",
    "Driver",
    "DriverOutcome",
    "OpenMdaoDriver",
    "ScipyDriver",
    "driver_named",
    "is_close",
    "usable",
]
