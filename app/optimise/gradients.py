"""Gradients for the optimiser, from `app/design/sensitivity.py`.

`sensitivity.py` has computed ∂measurement/∂parameter since Phase 5.3 and until
now had **no caller outside its own tests**. It is exactly what a gradient-based
optimiser needs, it already gets right the four things a naive finite difference
gets wrong, and re-deriving any of them here would have meant re-deriving them
worse:

* a build that fails at the perturbed value is **not zero sensitivity** — it
  comes back unprobed with a reason, where a hand-rolled difference would report
  0.0 and tell the optimiser to leave the parameter alone, which is the exact
  opposite of the truth about a parameter sitting at its limit;
* a step that changes the part's topology is **refused, not reported**, because
  the ratio between two different parts is not a derivative;
* the scheme actually used — central, forward or backward — is recorded per
  parameter, so a first-order one-sided difference is not silently presented as
  a second-order central one;
* the step is relative to the parameter's own magnitude, chosen to sit above a
  kernel's noise floor and below the scale at which a feature changes character.

**Nothing in `sensitivity.py` needed to change to be used here.** It probes a
`DesignSpec`'s free parameters through an injected `probe`, and it never
compiles the spec — the probe does whatever it likes with it. So an optimisation
over a part with no `DesignSpec` at all (one the agent built call by call, whose
parameters are journal dimensions like `Pad.1\\length_mm`) is served by handing
it a *carrier* spec: a `DesignSpec` with no features whose `ParameterSet` mirrors
the design variables, and a probe that reads the values back out and evaluates
the real model. The design variables' names are aliased to lowercase identifiers
for the trip, because `params.Parameter` requires one and `Pad.1\\length_mm` is
not.

**A gradient with a component nobody could measure is not delivered.** Filling
the hole with 0.0 is the failure `sensitivity.py`'s own docstring names, and
filling it with the driver's finite difference silently mixes two schemes in one
Jacobian. So the whole gradient comes back unavailable with the reason, and the
driver falls back to its own differencing — which is worse, and says so.

Every probe `sensitivity` makes goes through the `Evaluator`, so the rebuilds it
costs are in the history and count against the budget like any other. A run whose
history is 60 rows of which 48 are `purpose="gradient"` is a different story from
one that searched 60 designs, and the record has to be able to tell them apart.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from app.design.errors import SpecError
from app.design.params import Parameter
from app.design.sensitivity import (
    RELATIVE_STEP,
    Influence,
    Sensitivity,
    sensitivity,
)
from app.design.spec import DesignSpec
from app.optimise.evaluate import Evaluator
from app.optimise.problem import OptimisationProblem

#: Name of the carrier spec. Never built, never compiled, never shown to a user —
#: it exists only to give `sensitivity()` a `ParameterSet` to perturb.
_CARRIER: Final = "gradient-carrier"

_NOT_IDENTIFIER: Final = re.compile(r"[^a-z0-9_]+")


@dataclass(frozen=True)
class GradientReport:
    """The gradient at one point, or the reason there is not one.

    `values` is only meaningful when `available`; it is left empty otherwise
    rather than partially filled, because a Jacobian with one column missing is
    not a Jacobian with one column missing — it is a wrong Jacobian, and a
    caller that reads `values` without reading `available` should get nothing
    useful rather than something plausible.
    """

    at: Mapping[str, float]
    available: bool
    values: Mapping[str, float] = field(default_factory=dict)

    #: Why not, when not. Always a sentence naming the parameter at fault.
    reason: str = ""

    #: The full sensitivity, kept for reporting — it carries the scheme, the
    #: step and the elasticity ranking, which is what tells a user *which*
    #: dimension is driving their mass.
    sensitivity: Sensitivity | None = None

    def vector(self, problem: OptimisationProblem) -> list[float] | None:
        """The gradient in the driver's vector order, or None if unavailable."""
        if not self.available:
            return None
        return [self.values[name] for name in problem.names]

    def ranked(self) -> tuple[Influence, ...]:
        """Most influential parameter first, by elasticity. Empty if not probed."""
        return () if self.sensitivity is None else self.sensitivity.ranked()

    def __str__(self) -> str:
        if not self.available:
            return f"no gradient here — {self.reason}"
        terms = ", ".join(f"d/d{name} = {value:+.6g}" for name, value in self.values.items())
        return terms


def objective_gradient(
    evaluator: Evaluator,
    at: Mapping[str, float],
    *,
    relative_step: float = RELATIVE_STEP,
) -> GradientReport:
    """∂objective/∂variable at one point, through `app.design.sensitivity`.

    The probes are recorded in the evaluator's log with `purpose="gradient"` and
    count against the evaluation budget, because they are rebuilds and somebody
    is paying for them.
    """
    problem = evaluator.problem
    alias_of, name_of = _aliases(problem)

    try:
        carrier = DesignSpec.of(
            _CARRIER,
            parameters=[
                Parameter(name=alias_of[name], value=float(at[name]))
                for name in problem.names
            ],
            description=(
                "Not a design. A carrier for app.design.sensitivity's parameter set, so "
                "the optimiser's gradients come from the module that already gets finite "
                "differencing right."
            ),
        )
    except SpecError as bad:
        # A design-variable name that cannot be aliased onto a legal parameter
        # name — one that collides with a function available inside expressions,
        # say. Reported as no gradient rather than raised: the run is still
        # perfectly able to proceed on the driver's own differences, and killing
        # it over the spelling of a name would be absurd.
        return GradientReport(
            at=dict(at),
            available=False,
            reason=(
                f"the design variables could not be presented to app.design.sensitivity: "
                f"{bad} The driver will fall back to its own finite differences."
            ),
        )

    def probe(spec: DesignSpec) -> Mapping[str, Any]:
        values = {
            name_of[one.name]: float(one.value)
            for one in spec.parameters
            if one.value is not None
        }
        evaluation = evaluator.at(values, purpose="gradient")
        if not evaluation.built:
            # `sensitivity` reads a failed probe from an exception, which is how
            # it distinguishes "could not be built here" from "zero influence".
            # Raising is therefore the honest translation of a recorded failure,
            # not a lapse back into exceptions: the evaluation is already in the
            # log before this line runs.
            raise _ProbeFailed(evaluation.reason)
        return evaluation.measurements

    try:
        influence = sensitivity(
            carrier,
            problem.objective.measurement,
            probe=probe,
            relative_step=relative_step,
        )
    except _ProbeFailed as unbuilt:
        # `sensitivity` guards the *perturbed* probes and deliberately does not
        # guard the baseline one — a baseline that will not build means there is
        # no point to differentiate about, which is a caller error there. Here it
        # is an ordinary event: an optimiser asks for a gradient wherever its
        # line search has reached, including outside the buildable region. The
        # evaluation is already in the log; this turns it into no gradient rather
        # than into a dead run.
        return GradientReport(
            at=dict(at),
            available=False,
            reason=(
                f"the part does not build at this point, so there is no gradient here: "
                f"{unbuilt} The driver will fall back to its own finite differences."
            ),
        )

    if influence.baseline is None:
        return GradientReport(
            at=dict(at),
            available=False,
            reason=(
                f"{problem.objective.measurement!r} could not be measured at this point, "
                "so there is nothing to differentiate. The driver will fall back to its "
                "own finite differences."
            ),
            sensitivity=influence,
        )

    values: dict[str, float] = {}
    for one in influence.influences:
        name = name_of.get(one.parameter)
        if name is None:  # pragma: no cover - the carrier declares exactly these
            continue
        if one.derivative is None:
            return GradientReport(
                at=dict(at),
                available=False,
                reason=(
                    f"{name} could not be differenced here: {one.reason} A gradient with "
                    "a component nobody measured is not a gradient — filling it with zero "
                    "would tell the optimiser this dimension does nothing, which is the "
                    "opposite of what is true about one at its limit. The driver will "
                    "fall back to its own finite differences."
                ),
                sensitivity=influence,
            )
        values[name] = one.derivative

    missing = [name for name in problem.names if name not in values]
    if missing:  # pragma: no cover - only reachable if the carrier loses a parameter
        return GradientReport(
            at=dict(at),
            available=False,
            reason=f"no derivative was returned for {sorted(missing)}.",
            sensitivity=influence,
        )

    return GradientReport(
        at=dict(at),
        available=True,
        values=values,
        sensitivity=influence,
    )


class _ProbeFailed(Exception):
    """A build that failed inside a sensitivity probe.

    Private and never escapes this module: `sensitivity._probe_at` catches every
    exception from the probe and turns it into an unprobed `Influence` carrying
    the reason, which is exactly where this needs to end up.
    """


def _aliases(problem: OptimisationProblem) -> tuple[dict[str, str], dict[str, str]]:
    r"""Design-variable names ↔ lowercase identifiers `params.Parameter` accepts.

    `Parameter.__post_init__` requires `name.isidentifier()` and lowercase — it
    has to, because expressions refer to parameters by name and a name with a
    dot in it could not be referred to at all. A journal dimension is
    `Pad.1\length_mm`, which is neither. So the carrier spec uses aliases and
    this is the only place the two spellings meet.

    Aliases are derived from the real name and **disambiguated by position**, so
    two variables that sanitise to the same identifier stay distinct — otherwise
    `ParameterSet.of` refuses the duplicate and the gradient silently becomes
    unavailable for a reason nobody could act on.
    """
    alias_of: dict[str, str] = {}
    name_of: dict[str, str] = {}
    for index, name in enumerate(problem.names):
        base = _NOT_IDENTIFIER.sub("_", name.lower()).strip("_") or "v"
        if not base[0].isalpha():
            base = f"v_{base}"
        alias = base
        if alias in name_of:
            alias = f"{base}_{index}"
        alias_of[name] = alias
        name_of[alias] = name
    return alias_of, name_of


__all__ = ["GradientReport", "objective_gradient"]
