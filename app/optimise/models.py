r"""What an optimisation is actually run against.

Three `Model` implementations, in increasing order of how much machinery is
behind them. All three satisfy the same protocol, so a driver, the evaluation
record and every honesty rule are identical across them — which is the point of
the seam and is what lets the closed-form verification in
`tests/test_optimise.py` test the same code path a real part uses.

* **`AnalyticModel`** — a plain callable. Used to verify the optimiser against
  problems whose optimum is known exactly: a quadratic in one variable, a
  minimum-mass beam sized by a bending stress. No geometry, so a hundred
  evaluations cost nothing and the answer is checkable against algebra rather
  than against recorded output.
* **`SpecModel`** — a `DesignSpec` and a probe. The design variables are the
  spec's own free parameters; each evaluation replaces their values and hands
  the spec to the probe, which compiles and builds it. The same shape
  `sensitivity.py` already works in.
* **`PartModel`** — a part the agent built call by call, whose parameters are
  journal dimensions (`Pad.1\length_mm`) driven through `catia_set_parameter`.
  This is the one Decision 1 was made for: OCCT rebuilds in milliseconds, so the
  loop that sets a parameter, rebuilds and measures is affordable at all.

**`PartModel` never touches the part it was given.** Each evaluation builds a
*fresh* part from the supplied builder and applies the parameter values to that,
so a value the geometry cannot carry cannot leave the caller's document in any
state at all — not even the "unchanged" state `catia_set_parameter` already
guarantees within one runner. Two guarantees stacked, because the outer one is
the cheap one and the inner one is the one that would be expensive to lose:
sequential sets mean a run of five variables where the fourth is unbuildable
would otherwise leave three applied.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from app.design.params import ParameterSet
from app.design.spec import DesignSpec
from app.optimise.errors import OptimisationError

#: A part builder: called with no arguments, returns a runner with the baseline
#: part already built. Typed loosely on purpose — `app/optimise/` must not import
#: `app/kernel/`, which would drag ~166 MB of OCP into a package whose tests are
#: meant to run offline in under a second (the same argument
#: `assertions.counts_things` makes about `app.kernel.contract`).
Builder = Callable[[], Any]

#: A spec probe: build this spec, return its measurement payload. Identical to
#: `app.design.sensitivity.Probe`, and deliberately the same shape so one
#: callable serves both.
Probe = Callable[[DesignSpec], Mapping[str, Any]]


@dataclass(frozen=True)
class AnalyticModel:
    """A closed-form design: parameter values in, a measurement payload out.

    The verification vehicle. A problem with an exact optimum can be posed
    against this and the optimiser checked against algebra — which is the rule
    the solver tests already follow, and for the same reason: a module verified
    against its own previous output is not verified.
    """

    #: Values → payload. May raise to model a design that cannot be built.
    response: Callable[[Mapping[str, float]], Mapping[str, Any]]

    start: Mapping[str, float] = field(default_factory=dict)
    backend: str = "analytic"

    def baseline(self) -> Mapping[str, float]:
        return dict(self.start)

    def evaluate(self, values: Mapping[str, float]) -> Mapping[str, Any]:
        return self.response(values)


@dataclass(frozen=True)
class SpecModel:
    """A `DesignSpec` whose free parameters are the design variables."""

    spec: DesignSpec
    probe: Probe
    backend: str = "spec"

    def baseline(self) -> Mapping[str, float]:
        """Every free parameter's current value.

        Derived parameters are omitted, not zeroed: a parameter with an
        expression is a *consequence*, `params.Parameter` says so, and offering
        one as something to set produces a value the next resolve overwrites.
        """
        return {
            one.name: float(one.value)
            for one in self.spec.parameters
            if one.expression is None and one.value is not None
        }

    def at(self, values: Mapping[str, float]) -> DesignSpec:
        """The same design with these parameter values. Never mutates the original."""
        known = {one.name for one in self.spec.parameters}
        unknown = sorted(set(values) - known)
        if unknown:
            declared = ", ".join(sorted(known)) or "none"
            raise OptimisationError(
                f"{self.spec.name} declares no parameter called {unknown[0]!r}"
                + (f" (nor {unknown[1:]})" if len(unknown) > 1 else "")
                + f". Declared parameters are: {declared}."
            )
        derived = sorted(
            name
            for name in values
            if any(
                one.name == name and one.expression is not None
                for one in self.spec.parameters
            )
        )
        if derived:
            raise OptimisationError(
                f"{derived[0]!r} is a derived parameter, so setting it directly is "
                "overwritten by the next resolve. Optimise what it is computed from "
                "instead — app.design.sensitivity reports the same exclusion and says "
                "what that is."
            )
        return replace(
            self.spec,
            parameters=ParameterSet.of(
                replace(one, value=values[one.name]) if one.name in values else one
                for one in self.spec.parameters
            ),
        )

    def evaluate(self, values: Mapping[str, float]) -> Mapping[str, Any]:
        return self.probe(self.at(values))


@dataclass(frozen=True)
class PartModel:
    """A part built call by call, driven through `catia_set_parameter`.

    `build` returns a *fresh* runner with the baseline part already built; it is
    called once per evaluation. That looks wasteful and is not: the rebuild is
    what an evaluation is, `catia_set_parameter` replays the whole journal from
    the top anyway, and building from scratch is the only way to guarantee that a
    partially-applied set cannot survive into the next evaluation.
    """

    build: Builder

    #: The tool names, injected rather than imported, so this module does not
    #: depend on `app/kernel/`. They are the CATIA-facing names and are the same
    #: on both backends by design.
    set_tool: str = "catia_set_parameter"
    list_tool: str = "catia_list_parameters"
    measure_tool: str = "catia_measure"

    backend: str = "part"

    def baseline(self) -> Mapping[str, float]:
        """Every dimension the part was built from, by its journal name.

        Empty rather than raising where the part cannot be built at all — the
        starting point is then the middle of the declared bounds, and
        `OptimisationProblem.start` says so in a note rather than leaving it to
        be discovered.
        """
        try:
            runner = self.build()
            listed = runner(self.list_tool, {})
        except Exception:  # noqa: BLE001 - a baseline is a convenience, not a result
            return {}
        found: dict[str, float] = {}
        for entry in listed.get("parameters", ()):
            name, value = entry.get("name"), entry.get("value")
            if isinstance(name, str) and isinstance(value, (int, float)):
                found[name] = float(value)
        return found

    def evaluate(self, values: Mapping[str, float]) -> Mapping[str, Any]:
        """Rebuild at these dimensions and measure.

        Each `catia_set_parameter` replays the part from the top, so N variables
        cost N replays. Left that way rather than batched: the tool's contract is
        one dimension at a time, and a batched variant would be a second code
        path through the replay with its own way of being subtly wrong. If this
        becomes the bottleneck the fix belongs in `parameters.py`, behind the
        same tool name, where both backends get it.
        """
        runner = self.build()
        for name, value in values.items():
            runner(self.set_tool, {"name": name, "value": float(value)})
        return runner(self.measure_tool, {})


__all__ = ["AnalyticModel", "Builder", "PartModel", "Probe", "SpecModel"]
