"""Assertions for machines, not parts.

Phase 5.1. `assertions.py` checks a claim about a number that is already in a
measurement payload — `mass_kg <= 4.2`. That is the right shape for a part, and
it cannot express the things that decide whether a *machine* works: does the arm
clear the frame through its whole travel, does the stack of six tolerances still
fit, is the first mode above the drive frequency, does the bracket hold the load
case somebody named.

**The difference is that these claims must be *produced*, not read.** A mass is
in the payload because the kernel measured the part once. A clearance through a
motion range does not exist until something poses the assembly at a series of
positions and measures each one. So a machine check is a **measurement source**:
it computes a number, files it under a path with its provenance, and then the
existing `Assertion` machinery compares it. Nothing here invents a second
comparison language, and `UNMEASURED`, `gap` and the report all come for free.

**The tools are injected, never imported.** This package's load-bearing property
is that it runs offline in under a second with no kernel, no solver and no
socket — see `execute`, which takes its runner as a callable for the same
reason. A module here that reached for `app.kernel` would pull ~166 MB of OCP
into every test in the package. So a check is handed a `MachineTools`, and a
tool that is absent is `unavailable` **with a reason naming what is missing** —
which is also exactly what a check needing a solver Kryova has not federated yet
should say. An unmeasured claim is never a pass; that rule is what lets 5.1 land
complete while some of what it describes waits on Phase 6.

    checks = [
        MinimumWall(2.0),
        MassBudget(4.2),
        StackUp([("bore", 40.0, 0.05), ("bearing", -32.0, 0.02)], limit_mm=0.10),
        FactorOfSafety("lift", at_least=1.5),
    ]
    report = evaluate_machine(checks, subject=shape, tools=tools)
    if not report:
        print(report.summary())

`StackUp` needs no tools at all — it is arithmetic over declared contributors —
so it is exact, offline, and the one check here that can never come back
unmeasured.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

from app.design.assertions import Assertion, AssertionReport, check_assertions
from app.design.errors import SpecError

#: Where a machine check files what it measured. A namespace of its own so a
#: produced number can never be confused with one the kernel measured off the
#: part — `machine.clearance.arm_sweep` is visibly not `bounding_box_mm`.
NAMESPACE: Final = "machine"

#: Default samples across a motion range. Interference is checked at positions,
#: because a continuous sweep is a different and much harder problem, so this is
#: the resolution of the answer and it is reported rather than assumed. 24 puts
#: a sample every 15° on a full revolution.
DEFAULT_SAMPLES: Final = 24

#: Below this many samples the answer is not a sweep, it is two poses with a gap
#: between them, and calling it "clear through the range" would be a lie.
MINIMUM_SAMPLES: Final = 3

StackUpMethod = Literal["worst_case", "rss"]


class MachineTools(Protocol):
    """What a machine check needs from the world outside this package.

    A Protocol rather than an ABC because the caller supplies it — the kernel
    binding, a test double, or a partial object with only the tools one check
    needs. Every method may raise `ToolUnavailable`; that is the supported way
    to say "this machine has no solver", and it becomes an `UNMEASURED` result
    with the reason attached rather than a crash or a false pass.
    """

    def mass_kg(self, subject: Any) -> float: ...

    def bounding_box_mm(self, subject: Any) -> tuple[float, float, float]: ...

    def minimum_wall_mm(self, subject: Any) -> tuple[float, bool]:
        """Thinnest wall, and whether the answer was sampled rather than exact."""

    def minimum_distance_mm(self, first: Any, second: Any) -> float: ...

    def posed(self, subject: Any, at: float) -> Any:
        """`subject` placed at one point of its motion range, 0.0 to 1.0."""

    def first_natural_frequency_hz(self, subject: Any) -> float: ...

    def factor_of_safety(self, subject: Any, load_case: str) -> float: ...

    def buckling_factor(self, subject: Any, load_case: str) -> float: ...


class ToolUnavailable(RuntimeError):
    """The tool a check needs is not present on this machine, or not built yet.

    Carries the sentence the user reads, so it must say what is missing and what
    would make it available — "no solver is federated yet (Phase 6)" rather than
    "not implemented".
    """


@dataclass(frozen=True)
class Measured:
    """One number a check produced, and how much it is worth.

    `value is None` and a reason is the honest empty answer. `approximate` marks
    a number that came from sampling — a ray-cast wall, a motion checked at 24
    poses — and rides through to the assertion result, so a report can never
    present a sampled clearance as an exact one.
    """

    path: str
    value: float | None = None
    reason: str = ""
    approximate: bool = False
    method: str = ""


class MachineCheck(ABC):
    """One claim about a machine: what it asserts, and how to measure it."""

    #: Short, stable, and what a failure is reported under.
    name: str

    @abstractmethod
    def assertions(self) -> tuple[Assertion, ...]:
        """The claims this check makes, in the vocabulary `assertions.py` already has."""

    @abstractmethod
    def measure(self, subject: Any, tools: MachineTools) -> tuple[Measured, ...]:
        """Produce the numbers those claims read. Must not raise for a missing tool."""

    def _path(self, leaf: str) -> str:
        return f"{NAMESPACE}.{self.name}.{leaf}"


def _attempt(path: str, work: Any, *, method: str = "", approximate: bool = False) -> Measured:
    """Run one measurement, turning every failure into an honest empty answer.

    `ToolUnavailable` is the expected one and keeps its own sentence.
    `AttributeError` is the case where the caller passed a partial tools object
    with only the methods it cared about, which is a supported thing to do and
    must read as "this machine cannot answer that" rather than as a bug.
    """
    try:
        return Measured(path=path, value=float(work()), method=method, approximate=approximate)
    except ToolUnavailable as exc:
        return Measured(path=path, reason=str(exc))
    except AttributeError as exc:
        return Measured(
            path=path,
            reason=f"the tools supplied do not provide what this check needs ({exc}).",
        )
    except (ValueError, ArithmeticError, RuntimeError) as exc:
        return Measured(path=path, reason=f"the measurement failed: {exc}")


# ---------------------------------------------------------------------------
# The library.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MassBudget(MachineCheck):
    """The machine must not weigh more than this."""

    limit_kg: float
    name: str = "mass"
    note: str = ""

    def assertions(self) -> tuple[Assertion, ...]:
        return (
            Assertion(
                name=f"{self.name} within budget",
                measure=self._path("kg"),
                comparison="<=",
                bound=self.limit_kg,
                note=self.note,
            ),
        )

    def measure(self, subject: Any, tools: MachineTools) -> tuple[Measured, ...]:
        return (_attempt(self._path("kg"), lambda: tools.mass_kg(subject), method="integrated"),)


@dataclass(frozen=True)
class Envelope(MachineCheck):
    """The machine must fit inside a stated box.

    Three assertions rather than one, because "it does not fit" is not an
    actionable answer and "it is 40 mm too long in Z" is.
    """

    x_mm: float
    y_mm: float
    z_mm: float
    name: str = "envelope"
    note: str = ""

    def assertions(self) -> tuple[Assertion, ...]:
        limits = (("x", self.x_mm), ("y", self.y_mm), ("z", self.z_mm))
        return tuple(
            Assertion(
                name=f"{self.name} fits in {axis}",
                measure=self._path(axis),
                comparison="<=",
                bound=limit,
                note=self.note,
            )
            for axis, limit in limits
        )

    def measure(self, subject: Any, tools: MachineTools) -> tuple[Measured, ...]:
        size: list[float] | None = None

        def read(index: int) -> float:
            nonlocal size
            if size is None:
                size = list(tools.bounding_box_mm(subject))
            return size[index]

        return tuple(
            _attempt(self._path(axis), lambda index=index: read(index), method="bounding box")
            for index, axis in enumerate("xyz")
        )


@dataclass(frozen=True)
class MinimumWall(MachineCheck):
    """No wall thinner than the process can make, or the load can carry."""

    floor_mm: float
    name: str = "wall"
    note: str = ""

    def assertions(self) -> tuple[Assertion, ...]:
        return (
            Assertion(
                name=f"{self.name} at least {self.floor_mm:g} mm",
                measure=self._path("minimum_mm"),
                comparison=">=",
                bound=self.floor_mm,
                note=self.note,
            ),
        )

    def measure(self, subject: Any, tools: MachineTools) -> tuple[Measured, ...]:
        path = self._path("minimum_mm")
        try:
            value, sampled = tools.minimum_wall_mm(subject)
        except ToolUnavailable as exc:
            return (Measured(path=path, reason=str(exc)),)
        except AttributeError as exc:
            return (Measured(path=path, reason=f"no wall-thickness tool was supplied ({exc})."),)
        except (ValueError, RuntimeError) as exc:
            return (Measured(path=path, reason=f"the measurement failed: {exc}"),)
        # A ray-cast thickness is an upper bound from a finite set of directions.
        # Carried through as `approximate` so a wall that passes by 0.01 mm on a
        # sampled measurement is never read as a wall that passes.
        return (
            Measured(
                path=path,
                value=float(value),
                approximate=bool(sampled),
                method="ray cast" if sampled else "exact",
            ),
        )


@dataclass(frozen=True)
class ClearanceThroughMotion(MachineCheck):
    """Nothing touches anything through the whole travel.

    **Sampled, and it says so.** A continuous swept-volume interference check is
    a different and much harder problem; this poses the moving part at
    `samples` points and measures the closest approach at each. That answers the
    question honestly at a stated resolution, and the resolution is reported —
    a mechanism that only collides between two adjacent samples will pass this,
    which is why the result is marked approximate and why `samples` has a floor.
    """

    against: Any
    floor_mm: float
    name: str = "clearance"
    samples: int = DEFAULT_SAMPLES
    note: str = ""

    def __post_init__(self) -> None:
        if self.samples < MINIMUM_SAMPLES:
            raise SpecError(
                f"{self.name}: {self.samples} samples is not a sweep — it is a few poses "
                f"with gaps between them, and calling the result 'clear through the "
                f"range' would be untrue. Use at least {MINIMUM_SAMPLES}."
            )

    def assertions(self) -> tuple[Assertion, ...]:
        note = self.note or (
            f"Checked at {self.samples} poses across the range; a collision that happens "
            "only between two of them is not visible to this check."
        )
        return (
            Assertion(
                name=f"{self.name} at least {self.floor_mm:g} mm through travel",
                measure=self._path("minimum_mm"),
                comparison=">=",
                bound=self.floor_mm,
                note=note,
            ),
        )

    def measure(self, subject: Any, tools: MachineTools) -> tuple[Measured, ...]:
        path = self._path("minimum_mm")

        def sweep() -> float:
            closest = math.inf
            for index in range(self.samples):
                at = index / (self.samples - 1)
                posed = tools.posed(subject, at)
                closest = min(closest, tools.minimum_distance_mm(posed, self.against))
            if not math.isfinite(closest):  # pragma: no cover - samples has a floor
                raise ValueError("the sweep produced no measurement")
            return closest

        return (
            _attempt(path, sweep, method=f"{self.samples}-pose sweep", approximate=True),
        )


@dataclass(frozen=True)
class FirstNaturalFrequency(MachineCheck):
    """The first mode must sit clear of whatever is going to shake it."""

    above_hz: float
    name: str = "first_mode"
    note: str = ""

    def assertions(self) -> tuple[Assertion, ...]:
        return (
            Assertion(
                name=f"{self.name} above {self.above_hz:g} Hz",
                measure=self._path("hz"),
                comparison=">",
                bound=self.above_hz,
                note=self.note,
            ),
        )

    def measure(self, subject: Any, tools: MachineTools) -> tuple[Measured, ...]:
        return (
            _attempt(
                self._path("hz"),
                lambda: tools.first_natural_frequency_hz(subject),
                method="modal",
            ),
        )


@dataclass(frozen=True)
class FactorOfSafety(MachineCheck):
    """The part must hold a **named** load case with margin.

    The load case is named rather than described, because a factor of safety
    against an unnamed case is a number nobody can reproduce or argue with — and
    Decision 3 says a result is bound to the load case that produced it.
    """

    load_case: str
    at_least: float
    name: str = "fos"
    note: str = ""

    def __post_init__(self) -> None:
        if not str(self.load_case).strip():
            raise SpecError(
                f"{self.name}: a factor of safety needs the name of the load case it is "
                "against — an unnamed one is a number nobody can reproduce."
            )

    def assertions(self) -> tuple[Assertion, ...]:
        return (
            Assertion(
                name=f"{self.name} >= {self.at_least:g} under {self.load_case}",
                comparison=">=",
                measure=self._path(f"{self.load_case}.factor_of_safety"),
                bound=self.at_least,
                note=self.note,
            ),
        )

    def measure(self, subject: Any, tools: MachineTools) -> tuple[Measured, ...]:
        return (
            _attempt(
                self._path(f"{self.load_case}.factor_of_safety"),
                lambda: tools.factor_of_safety(subject, self.load_case),
                method=f"linear static, {self.load_case}",
            ),
        )


@dataclass(frozen=True)
class BucklingFactor(MachineCheck):
    """A slender member must not buckle before it yields."""

    load_case: str
    at_least: float
    name: str = "buckling"
    note: str = ""

    def assertions(self) -> tuple[Assertion, ...]:
        return (
            Assertion(
                name=f"{self.name} >= {self.at_least:g} under {self.load_case}",
                comparison=">=",
                measure=self._path(f"{self.load_case}.factor"),
                bound=self.at_least,
                note=self.note,
            ),
        )

    def measure(self, subject: Any, tools: MachineTools) -> tuple[Measured, ...]:
        return (
            _attempt(
                self._path(f"{self.load_case}.factor"),
                lambda: tools.buckling_factor(subject, self.load_case),
                method=f"linear buckling, {self.load_case}",
            ),
        )


@dataclass(frozen=True)
class StackUp(MachineCheck):
    """A chain of tolerances must still fit.

    The only check here that needs no geometry and no tools: it is arithmetic
    over contributors somebody declared, and it is exact.

    **Both methods, and the caller says which.** Worst case sums the tolerances
    and is what a safety-critical fit is designed to; RSS is the root of the sum
    of squares, is what a production run actually sees when the contributors are
    independent, and is smaller — often by half on a six-part stack. Picking one
    silently would mean either designing to a case that never occurs or shipping
    a fit that fails on the tails, so there is no default.
    """

    contributors: Sequence[tuple[str, float, float]]
    limit_mm: float
    method: StackUpMethod = "worst_case"
    name: str = "stack_up"
    note: str = ""
    #: Nominal target the chain closes on. The variation is what is compared to
    #: the limit, so this is carried for the report rather than for the check.
    nominal_mm: float = 0.0

    def __post_init__(self) -> None:
        if not self.contributors:
            raise SpecError(
                f"{self.name}: a stack-up with no contributors is not a check — it always "
                "passes, whatever the limit."
            )
        for label, _, tolerance in self.contributors:
            if tolerance < 0:
                raise SpecError(
                    f"{self.name}: contributor {label!r} has a negative tolerance "
                    f"({tolerance}). A tolerance is a half-width; use a positive number."
                )
        if self.method not in ("worst_case", "rss"):
            raise SpecError(
                f"{self.name}: {self.method!r} is not a stack-up method. Use 'worst_case' "
                "(what a safety-critical fit is designed to) or 'rss' (what a production "
                "run sees when the contributors are independent)."
            )

    def assertions(self) -> tuple[Assertion, ...]:
        note = self.note or (
            "Worst case: every contributor at its limit at once."
            if self.method == "worst_case"
            else "RSS: assumes the contributors vary independently. It is smaller than "
            "worst case and is not what a safety-critical fit is designed to."
        )
        return (
            Assertion(
                name=f"{self.name} within {self.limit_mm:g} mm ({self.method})",
                measure=self._path("variation_mm"),
                comparison="<=",
                bound=self.limit_mm,
                note=note,
            ),
        )

    def measure(self, subject: Any, tools: MachineTools) -> tuple[Measured, ...]:
        tolerances = [tolerance for _, _, tolerance in self.contributors]
        if self.method == "worst_case":
            variation = sum(tolerances)
        else:
            variation = math.sqrt(sum(one * one for one in tolerances))
        return (
            Measured(
                path=self._path("variation_mm"),
                value=variation,
                method=self.method,
            ),
            Measured(
                path=self._path("nominal_mm"),
                value=self.nominal_mm or sum(value for _, value, _ in self.contributors),
                method="declared",
            ),
        )


@dataclass(frozen=True)
class CostBudget(MachineCheck):
    """What the machine may cost to make.

    **Declared, and honestly unmeasurable today.** There is no cost model in
    this codebase — Phase 13 owns it — so this check exists to say so in the
    report rather than to be quietly left out of the library 5.1 describes. It
    comes back `UNMEASURED` with the reason, which is never a pass, and it
    becomes real the day a tool answers `cost`.
    """

    limit: float
    currency: str = "EUR"
    name: str = "cost"
    note: str = ""

    def assertions(self) -> tuple[Assertion, ...]:
        return (
            Assertion(
                name=f"{self.name} within {self.limit:g} {self.currency}",
                measure=self._path("total"),
                comparison="<=",
                bound=self.limit,
                note=self.note,
            ),
        )

    def measure(self, subject: Any, tools: MachineTools) -> tuple[Measured, ...]:
        return (
            _attempt(
                self._path("total"),
                lambda: getattr(tools, "cost")(subject, self.currency),  # noqa: B009
                method="cost model",
            ),
        )


# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MachineTooling:
    """A `MachineTools` that has nothing, for the cases where that is the truth.

    Every method raises `ToolUnavailable` with a sentence naming what would make
    it work. Useful on its own — a spec can be validated, and its assertions
    reported as unmeasured, on a machine with no kernel at all — and useful as a
    base for a real implementation that only has some of them.
    """

    reason: str = "no measuring tools were supplied."

    def __getattr__(self, name: str) -> Any:
        def refuse(*_: Any, **__: Any) -> float:
            raise ToolUnavailable(f"{name}: {self.reason}")

        return refuse


def machine_measurements(
    subject: Any, checks: Sequence[MachineCheck], tools: MachineTools
) -> dict[str, Any]:
    """Run every check and assemble the payload `check_assertions` reads.

    The payload carries the kernel's own provenance sidecar, so a sampled
    clearance arrives at the assertion layer marked approximate through the
    machinery that is already there — `_is_approximate` consults it per path,
    and an exact mass beside a ray-cast wall is not tainted by it.
    """
    payload: dict[str, Any] = {}
    provenance = _provenance()
    for check in checks:
        for one in check.measure(subject, tools):
            if one.value is None:
                provenance.attach(payload, one.path, provenance.unavailable(one.reason))
                continue
            _place(payload, one.path, one.value)
            provenance.attach(
                payload,
                one.path,
                provenance.approximated(one.method)
                if one.approximate
                else provenance.measured(one.method),
            )
    return payload


def evaluate_machine(
    checks: Sequence[MachineCheck],
    *,
    subject: Any = None,
    tools: MachineTools | None = None,
) -> AssertionReport:
    """Measure, then check. The whole of 5.1 in one call.

    `tools` left out is `MachineTooling()` — everything unmeasured, with a reason
    — rather than an error, because "what would this spec check, and can this
    machine check it?" is a question worth being able to ask offline.
    """
    supplied: MachineTools = tools if tools is not None else MachineTooling()  # type: ignore[assignment]
    payload = machine_measurements(subject, checks, supplied)
    claims = [one for check in checks for one in check.assertions()]
    return check_assertions(claims, payload)


def _place(payload: dict[str, Any], path: str, value: float) -> None:
    """Write `value` at a dotted path, creating the intermediate dicts.

    Dotted rather than flat because `assertions.read_measurement` walks dots, and
    a load case's name is part of the path (`machine.fos.lift.factor_of_safety`)
    so two cases on one part do not collide.
    """
    parts = path.split(".")
    here = payload
    for part in parts[:-1]:
        nested = here.setdefault(part, {})
        if not isinstance(nested, dict):  # pragma: no cover - names are check-controlled
            raise SpecError(f"Two checks disagree about what {part!r} is in {path!r}.")
        here = nested
    here[parts[-1]] = value


def _provenance() -> Any:
    """`app.kernel.provenance`, imported on use — see `assertions._provenance`."""
    from app.kernel import provenance

    return provenance


__all__ = [
    "DEFAULT_SAMPLES",
    "MINIMUM_SAMPLES",
    "NAMESPACE",
    "BucklingFactor",
    "ClearanceThroughMotion",
    "CostBudget",
    "Envelope",
    "FactorOfSafety",
    "FirstNaturalFrequency",
    "MachineCheck",
    "MachineTooling",
    "MachineTools",
    "MassBudget",
    "Measured",
    "MinimumWall",
    "StackUp",
    "StackUpMethod",
    "ToolUnavailable",
    "evaluate_machine",
    "machine_measurements",
]
