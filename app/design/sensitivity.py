"""Which parameter moves this measurement most.

Phase 5.3. The plan's blunt version: *a validator that cannot say why is a retry
counter.* `assertions.py` says the part is 3.1 kg over budget; `correct.py` can
try a repair and see whether the number moved. Neither can say **which of the
eleven parameters to move, or how far** — so the loop guesses, and a guess that
compiles looks exactly like a fix that works.

This computes ∂measurement/∂parameter by finite difference: perturb one free
parameter, rebuild, measure, compare. Phase 4's decision is what makes it
affordable at all — on a CATIA seat each probe was minutes of a workstation, and
on the open kernel a full sweep of a dozen parameters is a few dozen builds.
That is Decision 1 compounding, and the plan says so.

    influence = sensitivity(spec, "mass_kg", probe=build_and_measure)
    influence.most_influential().parameter      # 'web_thickness_mm'
    aim(influence, gap=3.1)                     # 'reduce web_thickness_mm by ~1.4 mm'

Four things here are not obvious and each is the difference between a number and
a lie:

**Only free parameters are probed.** A parameter with an expression is a
*consequence* — `params.Parameter` says so in as many words — and perturbing one
directly would either be overwritten on the next resolve or break the design's
own arithmetic. Derived parameters are reported as **excluded with a reason**
rather than dropped, because a user asking why the part is heavy and not seeing
`wall_thickness_mm` in the ranking needs to know it was excluded, not that it
has no influence.

**A build that fails at the perturbed value is not zero sensitivity.** Stepping a
fillet radius past what the geometry can carry is the most ordinary thing in the
world, and reporting the failure as 0.0 tells a correction loop "this parameter
does nothing, leave it alone" — the exact opposite of the truth about a
parameter that is at its limit. It comes back unprobed, with the reason.

**A topology change invalidates the difference.** A step big enough to make a
fillet swallow a face, or a pocket break through, compares two different parts
and calls the ratio a derivative. Face, edge and solid counts are in the
measurement contract, so when the payload carries them a change is detected and
the influence is refused rather than reported.

**The ranking is by elasticity, not by derivative.** ∂mass/∂radius is kg/mm and
∂mass/∂angle is kg/degree; the two cannot be compared, and "which matters most"
is meaningless until they can. Elasticity — the percentage change in the
measurement per percentage change in the parameter — is dimensionless and is
what the ranking uses.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final, Literal

from app.design.assertions import read_measurement
from app.design.errors import SpecError
from app.design.params import Parameter, ParameterSet
from app.design.spec import DesignSpec

#: Step as a fraction of the parameter's own magnitude. **The one number here
#: that is a genuine trade.** Too small and the difference disappears into the
#: kernel's own noise — a boolean's tolerance is around 1e-7 mm, and differencing
#: two masses that agree to eleven digits measures rounding, not geometry. Too
#: large and the two builds are different designs and the ratio is not a
#: derivative of anything. 0.1% sits well above the noise for any realistic part
#: and well below the scale at which a feature changes character.
RELATIVE_STEP: Final = 1e-3

#: Step for a parameter whose value is zero, where a relative step is also zero.
#: Absolute, and in whatever unit the parameter carries — which is why it is
#: small: a zero-valued parameter is usually an offset or an angle, where 1e-4 of
#: a millimetre or a degree is a real perturbation and 1 mm is a redesign.
ZERO_VALUE_STEP: Final = 1e-4

#: Paths whose change means the two builds are not the same part. From the
#: measurement contract (`app/kernel/contract.py`), so a payload that carries
#: them gets the check for free and one that does not is honestly unchecked.
TOPOLOGY_PATHS: Final[tuple[str, ...]] = ("face_count", "edge_count", "solid_count")

#: Below this, the derivative is indistinguishable from zero and dividing by it
#: to aim a repair produces a step the size of a planet. `aim` refuses instead.
NEGLIGIBLE: Final = 1e-12

Scheme = Literal["central", "forward", "backward"]

#: What `probe` is: a spec in, a measurement payload out. Injected rather than
#: imported, like `execute`'s runner — this module must not know whether the
#: geometry came from OCCT, from CATIA, or from a test double.
Probe = Callable[[DesignSpec], Mapping[str, Any]]


@dataclass(frozen=True)
class Influence:
    """What one parameter does to one measurement.

    `derivative` carries units (measurement per parameter) and is what `aim`
    uses to size a repair. `elasticity` is dimensionless and is what the ranking
    uses, because kg/mm and kg/degree cannot be compared.
    """

    parameter: str
    derivative: float | None = None
    elasticity: float | None = None
    step: float = 0.0
    scheme: Scheme = "central"

    #: Why it could not be probed, when it could not be. Empty otherwise. Always
    #: a sentence: "the build failed at 8.008 mm" is actionable, "unavailable"
    #: is not.
    reason: str = ""

    #: True when the perturbed build was a topologically different part, so the
    #: difference is across a discontinuity and is not a derivative.
    topology_changed: bool = False

    #: True when the payload carried no topology counts, so nothing could rule
    #: that out. Reported rather than assumed either way.
    topology_unchecked: bool = False

    @property
    def measured(self) -> bool:
        return self.derivative is not None

    @property
    def magnitude(self) -> float:
        """How much this parameter matters, for ranking. Unprobed sorts last."""
        if self.elasticity is not None:
            return abs(self.elasticity)
        return -1.0

    def __str__(self) -> str:
        if not self.measured:
            return f"{self.parameter}: not probed — {self.reason}"
        elasticity = "?" if self.elasticity is None else f"{self.elasticity:+.3g}"
        caveat = " (topology unchecked)" if self.topology_unchecked else ""
        return (
            f"{self.parameter}: d/dp = {self.derivative:+.6g} per unit, "
            f"elasticity {elasticity}, {self.scheme} at step {self.step:g}{caveat}"
        )


@dataclass(frozen=True)
class Sensitivity:
    """Every parameter's influence on one measurement."""

    measure: str
    baseline: float | None
    influences: tuple[Influence, ...] = ()

    def ranked(self) -> tuple[Influence, ...]:
        """Most influential first; unprobed parameters last, in declaration order.

        Sorted on elasticity, never on the raw derivative — see the module
        docstring. Ties break on the declared order rather than on the name, so
        the ranking of a design that has not changed does not change.
        """
        order = {one.parameter: index for index, one in enumerate(self.influences)}
        return tuple(
            sorted(self.influences, key=lambda one: (-one.magnitude, order[one.parameter]))
        )

    def most_influential(self) -> Influence | None:
        """The parameter to reach for first, or None if nothing could be probed."""
        for one in self.ranked():
            if one.measured:
                return one
        return None

    def probed(self) -> tuple[Influence, ...]:
        return tuple(one for one in self.influences if one.measured)

    def unprobed(self) -> tuple[Influence, ...]:
        return tuple(one for one in self.influences if not one.measured)

    def __getitem__(self, parameter: str) -> Influence:
        for one in self.influences:
            if one.parameter == parameter:
                return one
        known = ", ".join(one.parameter for one in self.influences) or "none"
        raise KeyError(f"No influence recorded for {parameter!r}. Probed: {known}.")

    def summary(self) -> str:
        if self.baseline is None:
            return f"{self.measure}: could not be measured on the baseline, so nothing was probed."
        head = f"{self.measure} = {self.baseline:g} at the baseline."
        lines = [str(one) for one in self.ranked()]
        return "\n".join([head, *lines])


def sensitivity(
    spec: DesignSpec,
    measure: str,
    *,
    probe: Probe,
    parameters: Sequence[str] | None = None,
    relative_step: float = RELATIVE_STEP,
) -> Sensitivity:
    """Finite-difference every free parameter against one measurement.

    `probe` builds a spec and returns its measurement payload; it is injected so
    this module never learns whether a kernel, a CATIA seat or a test double
    answered.

    Central difference where both sides build, forward or backward where only
    one does — and the scheme is recorded per parameter, because central is
    second-order accurate and one-sided is first-order, so a report that did not
    say which ran would present two different qualities of answer as one.
    """
    if relative_step <= 0:
        raise SpecError(
            f"A relative step of {relative_step} is not a step. It must be positive; "
            f"{RELATIVE_STEP} is the default and is chosen to sit above a kernel's "
            "noise floor and below the scale at which a feature changes character."
        )

    baseline_payload = probe(spec)
    baseline = read_measurement(baseline_payload, measure)
    if baseline is None:
        return Sensitivity(measure=measure, baseline=None)

    reference = topology_of(baseline_payload)
    free, excluded = _free_parameters(spec.parameters, parameters)
    influences = [
        _influence(spec, measure, one, baseline, reference, probe, relative_step)
        for one in free
    ]
    return Sensitivity(
        measure=measure, baseline=baseline, influences=tuple(influences + excluded)
    )


def _free_parameters(
    declared: ParameterSet, wanted: Sequence[str] | None
) -> tuple[list[Parameter], list[Influence]]:
    """Split the declared parameters into what can be probed and what cannot.

    A derived parameter is **reported as excluded**, not dropped. Silently
    omitting it reads as "no influence", which is the wrong answer about a
    parameter that may be the largest driver in the design — it is simply not
    the thing to change, and the thing to change is whatever it is computed from.
    """
    names = set(wanted) if wanted is not None else None
    if names is not None:
        unknown = names - set(declared.names())
        if unknown:
            known = ", ".join(declared.names()) or "none"
            raise SpecError(
                f"Cannot probe {sorted(unknown)}: this design declares no such parameter. "
                f"Declared: {known}."
            )

    free: list[Parameter] = []
    excluded: list[Influence] = []
    for parameter in declared:
        if names is not None and parameter.name not in names:
            continue
        if parameter.expression is not None:
            excluded.append(
                Influence(
                    parameter=parameter.name,
                    reason=(
                        f"it is derived (= {parameter.expression}), so it is a consequence "
                        "rather than a decision. Probe what it is computed from instead."
                    ),
                )
            )
            continue
        free.append(parameter)
    return free, excluded


def _influence(
    spec: DesignSpec,
    measure: str,
    parameter: Parameter,
    baseline: float,
    reference: Mapping[str, float],
    probe: Probe,
    relative_step: float,
) -> Influence:
    """One parameter, differenced. Never raises; a failure is a reason."""
    assert parameter.value is not None  # noqa: S101 - free parameters carry a value
    at = float(parameter.value)
    step = abs(at) * relative_step if at else ZERO_VALUE_STEP

    up = _probe_at(spec, measure, parameter, at + step, probe, reference)
    down = _probe_at(spec, measure, parameter, at - step, probe, reference)

    if up.value is not None and down.value is not None:
        derivative = (up.value - down.value) / (2.0 * step)
        scheme: Scheme = "central"
        moved = up.topology_changed or down.topology_changed
        unchecked = up.topology_unchecked or down.topology_unchecked
    elif up.value is not None:
        derivative = (up.value - baseline) / step
        scheme = "forward"
        moved, unchecked = up.topology_changed, up.topology_unchecked
    elif down.value is not None:
        derivative = (baseline - down.value) / step
        scheme = "backward"
        moved, unchecked = down.topology_changed, down.topology_unchecked
    else:
        return Influence(
            parameter=parameter.name,
            step=step,
            reason=(
                f"neither direction could be built: up, {up.reason} down, {down.reason}"
            ),
        )

    if moved:
        return Influence(
            parameter=parameter.name,
            step=step,
            scheme=scheme,
            topology_changed=True,
            reason=(
                f"a step of {step:g} changed the part's topology, so the two builds are "
                "different parts and the ratio between them is not a derivative. A "
                "smaller relative_step may stay on one side of the change."
            ),
        )

    # Elasticity is undefined where the baseline is zero — the percentage change
    # in a quantity that is zero is not a number. Reported as None rather than
    # as infinity or as zero, both of which would sort wrongly.
    elasticity = (derivative * at / baseline) if baseline else None
    return Influence(
        parameter=parameter.name,
        derivative=derivative,
        elasticity=elasticity,
        step=step,
        scheme=scheme,
        topology_unchecked=unchecked,
    )


@dataclass(frozen=True)
class _Probed:
    value: float | None = None
    reason: str = ""
    topology_changed: bool = False
    topology_unchecked: bool = False


def _probe_at(
    spec: DesignSpec,
    measure: str,
    parameter: Parameter,
    value: float,
    probe: Probe,
    reference: Mapping[str, float],
) -> _Probed:
    """Rebuild with one parameter moved, and measure. Every failure is a sentence."""
    moved = _with_value(spec, parameter, value)
    try:
        payload = probe(moved)
    except Exception as exc:  # noqa: BLE001 - a failed build is data, not an error here
        return _Probed(reason=f"the build failed at {value:g} ({exc}).")

    found = read_measurement(payload, measure)
    if found is None:
        return _Probed(reason=f"the build at {value:g} did not report {measure!r}.")

    changed, checked = _topology_moved(payload, reference)
    return _Probed(value=found, topology_changed=changed, topology_unchecked=not checked)


def _with_value(spec: DesignSpec, parameter: Parameter, value: float) -> DesignSpec:
    """The same design with one parameter's value replaced.

    Rebuilt rather than mutated: `DesignSpec` is frozen, and a sensitivity sweep
    that edited the caller's spec in place would leave the design holding
    whichever perturbation happened to run last.
    """
    return replace(
        spec,
        parameters=ParameterSet.of(
            replace(one, value=value) if one.name == parameter.name else one
            for one in spec.parameters
        ),
    )


def topology_of(payload: Mapping[str, Any]) -> dict[str, float]:
    """The counts that say whether two builds are the same part.

    Empty when the payload carries none, which is what `topology_unchecked`
    reports — a backend that does not count faces cannot have this check, and
    claiming it did would be worse than saying so.
    """
    found = {path: read_measurement(payload, path) for path in TOPOLOGY_PATHS}
    return {path: value for path, value in found.items() if value is not None}


def _topology_moved(
    payload: Mapping[str, Any], reference: Mapping[str, float]
) -> tuple[bool, bool]:
    """Did this build have a different topology from the baseline?

    Returns (changed, checked). Threaded down from `sensitivity` rather than
    stashed on the probe: a fact this small does not justify making the callable
    stateful, and a probe that is a bound method or a lambda cannot carry one.
    """
    here = topology_of(payload)
    if not here or not reference:
        return False, False
    return any(reference.get(path) != value for path, value in here.items()), True


@dataclass(frozen=True)
class Aim:
    """A suggested repair: which parameter, and how far to move it."""

    parameter: str
    change: float
    #: The parameter's current and suggested values, when the caller supplied
    #: `values`. None rather than 0.0 when it did not — "0 → -1.4" reads as a
    #: real instruction and is a fabrication.
    from_value: float | None
    to_value: float | None
    #: The caveat, always present. A first-order estimate is only valid near the
    #: point it was taken at, and a large suggested move is exactly where it is
    #: least trustworthy.
    caveat: str

    def __str__(self) -> str:
        direction = "increase" if self.change > 0 else "reduce"
        move = f"{direction} {self.parameter} by {abs(self.change):.4g}"
        if self.from_value is not None and self.to_value is not None:
            move += f" ({self.from_value:g} → {self.to_value:g})"
        return f"{move}. {self.caveat}"


def aim(
    influence: Sensitivity,
    gap: float,
    *,
    values: Mapping[str, float] | None = None,
    parameter: str | None = None,
) -> Aim | None:
    """Turn a failing assertion's gap into a parameter and a distance.

    `gap` is `AssertionResult.gap` — how far the wrong side of the bound the
    measurement landed, signed. The first-order step that closes it is
    `-gap / (dm/dp)`.

    Returns None rather than guessing when nothing was probed, or when the most
    influential parameter's derivative is negligible: dividing by ~0 produces a
    step of absurd size, and a correction loop handed one will spend an attempt
    building something that cannot exist.

    **First order only, and the caveat says so on every suggestion.** The
    derivative is taken at the baseline; a large step leaves the neighbourhood
    it describes, and the honest use is to move, rebuild and re-measure rather
    than to trust the arithmetic to land exactly.
    """
    chosen = influence[parameter] if parameter else influence.most_influential()
    if chosen is None or chosen.derivative is None:
        return None
    if abs(chosen.derivative) < NEGLIGIBLE:
        return None

    change = -gap / chosen.derivative
    at = (values or {}).get(chosen.parameter)
    caveat = (
        "First-order estimate from a difference taken at the baseline — rebuild and "
        "re-measure rather than trusting it to land exactly."
    )
    if chosen.scheme != "central":
        caveat += (
            f" The derivative is {chosen.scheme} (only one direction would build), which "
            "is first-order accurate, so treat the size as a direction rather than a value."
        )
    if chosen.topology_unchecked:
        caveat += " Nothing confirmed the perturbed build had the same topology."
    return Aim(
        parameter=chosen.parameter,
        change=change,
        from_value=at,
        to_value=None if at is None else at + change,
        caveat=caveat,
    )


def baseline_values(spec: DesignSpec) -> dict[str, float]:
    """Every free parameter's current value, for `aim` to report against."""
    return {
        one.name: float(one.value)
        for one in spec.parameters
        if one.expression is None and one.value is not None
    }


__all__ = [
    "NEGLIGIBLE",
    "RELATIVE_STEP",
    "TOPOLOGY_PATHS",
    "ZERO_VALUE_STEP",
    "Aim",
    "Influence",
    "Probe",
    "Scheme",
    "Sensitivity",
    "aim",
    "baseline_values",
    "sensitivity",
    "topology_of",
]
