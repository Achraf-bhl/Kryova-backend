"""What is being optimised: design variables, an objective, and constraints.

This is the half of Phase 10.3 that is *ours*. A driver — scipy's SLSQP,
OpenMDAO's, a future gradient-free one — knows about vectors of floats. It knows
nothing about a pad length, a wall thickness, or a mass measured off a B-rep,
and it must not: the moment a driver learns which parameter is a fillet radius,
swapping drivers becomes a rewrite. So the vocabulary that binds an optimisation
to *this* system lives here and the drivers stay ignorant.

Three bindings, each to something that already exists:

* **A design variable is a named parameter of a built part.** On a `DesignSpec`
  that is a free `params.Parameter`; on a part the agent assembled call by call
  it is a journal dimension addressed as `Pad.1\\length_mm`
  (`app.kernel.occt.operations.parameters`). Either way the name is the model's
  name and this module does not interpret it.
* **An objective is a measurement path**, read exactly as an assertion reads one
  — `mass_kg`, `bounding_box_mm.size[2]` — through
  `app.design.assertions.read_measurement`. Paths rather than a fixed
  vocabulary, for the reason `assertions.py` gives: the payload is whatever the
  measuring tool returned.
* **A constraint is an `Assertion`.** Not a parallel type. `assertions.py`
  already carries a measurement path, a comparison, a bound that may be a
  formula over the design's own parameters, a tolerance, and — the part that
  matters most here — the rule that a claim which could not be measured is
  `UNMEASURED` and never a pass. Re-deriving that here would have meant
  re-deriving that rule, and it is the one rule this package cannot afford to
  get subtly different.

**Bounds on a design variable are mandatory, and that is a deliberate refusal.**
Every other optimisation library makes them optional and defaults to ±∞. Here
each evaluation is a geometry rebuild, and an unbounded search reaches a fillet
radius of 10⁶ mm within a handful of steps — where every rebuild fails, every
failure is a constraint violation, and the optimiser spends its whole budget
learning that the outside of the design space is bad. Bounds are also the only
place a *physical* limit (a plate cannot be 0 mm thick) can be stated, since the
kernel will happily build a degenerate solid and measure it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.design.assertions import Assertion, AssertionResult, Outcome
from app.optimise.errors import ObjectiveError, VariableError

#: Evaluations before the run is abandoned, when a caller does not say. Each one
#: is a rebuild of the whole part, so this is a wall-clock budget in disguise:
#: a part that rebuilds in 200 ms spends 40 s here. Deliberately small — a run
#: that has not converged in 200 rebuilds of a handful of variables is not going
#: to, and the honest outcome is to say so early rather than late.
DEFAULT_MAX_EVALUATIONS: Final = 200

#: Convergence tolerance handed to the driver when a caller does not say. In the
#: objective's own unit, which is why it is loose: a mass in kilograms converged
#: to 1e-6 kg is a milligram, and no geometry kernel's mass integration is worth
#: that. A caller who knows their objective's scale should say so.
DEFAULT_TOLERANCE: Final = 1e-6


class Sense(StrEnum):
    """Which way is better."""

    MINIMISE = "minimise"
    MAXIMISE = "maximise"

    @property
    def signum(self) -> float:
        """+1 for a minimisation, -1 for a maximisation.

        Every driver here minimises. A maximisation is handed to it negated and
        the sign is put back before anything is *recorded*, so an `Evaluation`
        always carries the measurement as measured — nobody reading a history
        should have to know which way the driver was pointed.
        """
        return 1.0 if self is Sense.MINIMISE else -1.0


@dataclass(frozen=True)
class DesignVariable:
    """One dimension the optimiser is allowed to move.

    `name` is whatever the model calls it and is never parsed here — a
    `DesignSpec` parameter is `wall_thickness_mm`, a journal dimension is
    `Pad.1\\length_mm`, and this module must work for both without knowing the
    difference.
    """

    name: str
    lower: float
    upper: float

    #: Where to start. None means "wherever the model currently is", which is
    #: the usual and better answer — the baseline part is a point somebody chose
    #: and it is nearly always a better start than the middle of a box.
    initial: float | None = None

    #: The unit the value is in, for reporting. Not converted, ever — the whole
    #: codebase is mm-N-MPa and this is a label.
    unit: str = ""

    description: str = ""

    def __post_init__(self) -> None:
        for label, value in (("lower", self.lower), ("upper", self.upper)):
            if not math.isfinite(value):
                raise VariableError(
                    f"{self.name}: the {label} bound is {value}. Bounds must be finite. "
                    "Every evaluation here rebuilds the part, and an unbounded search "
                    "reaches a value no geometry can carry within a few steps — the run "
                    "then spends its whole budget learning that the outside of the design "
                    "space is bad. Give it the range the dimension is physically allowed."
                )
        if self.lower >= self.upper:
            raise VariableError(
                f"{self.name}: lower bound {self.lower:g} is not below upper bound "
                f"{self.upper:g}, so there is nothing between them to search. Swap them, "
                "or widen the range."
            )
        if self.initial is not None and not self.lower <= self.initial <= self.upper:
            raise VariableError(
                f"{self.name}: the starting value {self.initial:g} is outside its own "
                f"bounds [{self.lower:g}, {self.upper:g}]. A start outside the box is "
                "either a typo or a bound that does not describe the part."
            )

    @property
    def span(self) -> float:
        return self.upper - self.lower

    def clamp(self, value: float) -> float:
        """The nearest value inside the bounds.

        Used only where a driver hands back a point a hair outside its own box —
        SLSQP does, by around 1e-16 — and never to quietly rescue a value the
        caller asked for. `__post_init__` refuses those instead.
        """
        return min(self.upper, max(self.lower, value))

    def start(self, baseline: Mapping[str, float]) -> float:
        """Where this variable begins, given what the model currently reads.

        A baseline outside the declared bounds is **clamped and it is not
        silent** — the caller is told through `OptimisationProblem.start`, which
        collects the notes. Refusing outright would make it impossible to
        optimise a part someone already built slightly out of range, which is a
        common and reasonable thing to want to fix.
        """
        if self.initial is not None:
            return self.initial
        current = baseline.get(self.name)
        if current is None:
            return 0.5 * (self.lower + self.upper)
        return self.clamp(float(current))


@dataclass(frozen=True)
class Objective:
    """The single number being driven up or down.

    One objective, not many. A multi-objective run is a different thing with a
    different answer — a Pareto front, not a point — and pretending a weighted
    sum of mass and stiffness is "the optimum" is the kind of quiet dishonesty
    Decision 3 is about. Master plan 10.3 lists multi-objective trade-offs; when
    they land they land as their own result type, not as a `weights` argument
    here.
    """

    measurement: str
    sense: Sense = Sense.MINIMISE
    description: str = ""

    def __post_init__(self) -> None:
        if not self.measurement or not str(self.measurement).strip():
            raise ObjectiveError(
                "An objective needs something to measure, e.g. 'mass_kg' or "
                "'bounding_box_mm.size[2]'. It is read from the measurement payload by "
                "the same path an assertion uses."
            )

    def driver_value(self, measured: float) -> float:
        """The measurement as the (always minimising) driver should see it."""
        return self.sense.signum * measured

    def __str__(self) -> str:
        return f"{self.sense.value} {self.measurement}"


def margin(result: AssertionResult) -> float | None:
    """How much room a constraint has left. Negative means violated.

    The bridge between `assertions.py`'s three-valued outcome and the single
    signed number every optimiser wants for `g(x) >= 0`. Returns **None** for an
    `UNMEASURED` constraint rather than 0.0 or a large positive number, because
    both of those are the claim "this constraint is satisfied" made about
    something nobody measured — which is the failure `assertions.py` exists to
    prevent, re-introduced one layer up. The caller decides what to do with a
    None; `evaluate.py` treats the whole point as infeasible.
    """
    if result.outcome is Outcome.UNMEASURED:
        return None
    if result.measured is None or result.expected is None:
        return None

    comparison = result.assertion.comparison
    tolerance = result.assertion.tolerance
    measured, expected = result.measured, result.expected

    if comparison in ("<=", "<"):
        return expected + tolerance - measured
    if comparison in (">=", ">"):
        return measured - expected + tolerance
    if comparison == "==":
        return tolerance - abs(measured - expected)
    if comparison == "!=":
        # Not a constraint an optimiser can steer by: the feasible set has a
        # hole in it and the margin is discontinuous at the hole. Reported as
        # satisfied-or-not with no usable gradient, which is honest; a driver
        # that needs a smooth g gets told the problem is unsuitable.
        return 1.0 if abs(measured - expected) > tolerance else -1.0
    return None  # pragma: no cover - COMPARISONS is closed


@dataclass(frozen=True)
class OptimisationProblem:
    """A complete, checkable statement of what "better" means for one part."""

    name: str
    variables: tuple[DesignVariable, ...]
    objective: Objective
    constraints: tuple[Assertion, ...] = ()

    #: Hard ceiling on rebuilds. Counts *every* build, including the ones a
    #: gradient sweep asks for and the ones that failed — a budget that only
    #: counted successes would be spent without the caller seeing it go.
    max_evaluations: int = DEFAULT_MAX_EVALUATIONS

    tolerance: float = DEFAULT_TOLERANCE

    description: str = ""

    #: Free-text provenance: the requirement this optimisation serves.
    note: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise VariableError(
                "An optimisation needs a name; it is what the run is reported under and "
                "what a provenance record is keyed on."
            )
        if not self.variables:
            raise VariableError(
                f"{self.name}: an optimisation with no design variables has nothing to "
                "change. Declare at least one DesignVariable naming a parameter of the "
                "part — catia_list_parameters shows what a built part has."
            )
        seen: dict[str, int] = {}
        for index, variable in enumerate(self.variables):
            if variable.name in seen:
                raise VariableError(
                    f"{self.name}: {variable.name!r} is declared twice (positions "
                    f"{seen[variable.name]} and {index}). Two boxes for one dimension "
                    "means one of them is silently ignored."
                )
            seen[variable.name] = index
        if self.max_evaluations < 1:
            raise VariableError(
                f"{self.name}: max_evaluations is {self.max_evaluations}, so nothing "
                "would ever be built. It counts rebuilds, including failed ones; "
                f"{DEFAULT_MAX_EVALUATIONS} is the default."
            )
        if self.tolerance <= 0:
            raise VariableError(
                f"{self.name}: a tolerance of {self.tolerance} cannot be reached by any "
                "real measurement. It is in the objective's own unit; "
                f"{DEFAULT_TOLERANCE} is the default."
            )

    @classmethod
    def of(
        cls,
        name: str,
        *,
        variables: Iterable[DesignVariable],
        objective: Objective,
        constraints: Iterable[Assertion] = (),
        max_evaluations: int = DEFAULT_MAX_EVALUATIONS,
        tolerance: float = DEFAULT_TOLERANCE,
        description: str = "",
        note: str = "",
    ) -> OptimisationProblem:
        return cls(
            name=name,
            variables=tuple(variables),
            objective=objective,
            constraints=tuple(constraints),
            max_evaluations=max_evaluations,
            tolerance=tolerance,
            description=description,
            note=note,
        )

    # -- the vector view the drivers use -------------------------------------

    @property
    def names(self) -> tuple[str, ...]:
        """Variable names in declared order. **The vector order, everywhere.**"""
        return tuple(variable.name for variable in self.variables)

    @property
    def bounds(self) -> tuple[tuple[float, float], ...]:
        return tuple((variable.lower, variable.upper) for variable in self.variables)

    def start(self, baseline: Mapping[str, float]) -> tuple[dict[str, float], tuple[str, ...]]:
        """The starting point, plus any notes about how it was arrived at.

        Returns `(values, notes)`. A note is produced where the model's current
        value had to be clamped into the declared bounds — silently moving
        somebody's starting point is how a run reports converging on a design
        the caller never asked about.
        """
        values: dict[str, float] = {}
        notes: list[str] = []
        for variable in self.variables:
            chosen = variable.start(baseline)
            values[variable.name] = chosen
            current = baseline.get(variable.name)
            if variable.initial is None and current is None:
                notes.append(
                    f"{variable.name}: the model reported no current value, so the run "
                    f"starts at the middle of its bounds ({chosen:g})."
                )
            elif (
                variable.initial is None
                and current is not None
                and float(current) != chosen
            ):
                notes.append(
                    f"{variable.name}: the part is at {float(current):g}, outside the "
                    f"declared bounds [{variable.lower:g}, {variable.upper:g}], so the "
                    f"run starts from {chosen:g}."
                )
        return values, tuple(notes)

    def vector(self, values: Mapping[str, float]) -> list[float]:
        """Named values → the driver's vector, in declared order."""
        return [float(values[name]) for name in self.names]

    def named(self, vector: Sequence[float]) -> dict[str, float]:
        """The driver's vector → named values, clamped back into the box.

        Clamped because SLSQP returns points a rounding error outside their own
        bounds, and a rebuild at `thickness = -1e-17` fails for a reason that has
        nothing to do with the design.
        """
        if len(vector) != len(self.variables):
            raise VariableError(  # pragma: no cover - a driver bug, not a user one
                f"{self.name}: the driver returned {len(vector)} values for "
                f"{len(self.variables)} design variables."
            )
        return {
            variable.name: variable.clamp(float(value))
            for variable, value in zip(self.variables, vector, strict=True)
        }

    def __str__(self) -> str:
        head = f"{self.name}: {self.objective}"
        over = ", ".join(
            f"{v.name} in [{v.lower:g}, {v.upper:g}]{' ' + v.unit if v.unit else ''}"
            for v in self.variables
        )
        lines = [f"{head} over {over}"]
        for constraint in self.constraints:
            lines.append(
                f"  subject to {constraint.name}: {constraint.measure} "
                f"{constraint.comparison} {constraint.bound}"
            )
        return "\n".join(lines)


__all__ = [
    "DEFAULT_MAX_EVALUATIONS",
    "DEFAULT_TOLERANCE",
    "DesignVariable",
    "Objective",
    "OptimisationProblem",
    "Sense",
    "margin",
]
