"""What a benchmark *is* here, and the rules that stop one being invented.

Master plan 7.1, **the machinery only**. This module defines what a benchmark
is, what a target may claim, and how a run is classified. It holds **no
benchmark instances** — the catalogue is `app.verify.nafems`, and the split is
deliberate: the rules below have to be readable without the cases beside them,
because they are what stops a case being invented. Nothing here should be read
as evidence that any analysis has been validated; that is the register's
question (`app.verify.register`) and its answer is still no.

A benchmark is a published problem with a published answer. The
value of running one comes entirely from the answer having been arrived at by
somebody else, so the single most damaging thing this module could permit is a
target that looks published and is not. Every rule below exists to make that
impossible rather than merely discouraged:

* **A target must say where it came from.** `TargetBasis.PUBLISHED` requires a
  non-empty `source`, and `DERIVED` requires the formula and the function that
  computes it. A number with no provenance is refused at construction, so it
  cannot reach a test, let alone a published register.
* **"I am not sure" is a first-class state.** `TargetBasis.UNKNOWN` carries a
  reason and *forbids* a value. This is what a case with a target the author
  could not verify looks like: fully encoded, runnable where possible, measured,
  and honestly not validated. A fabricated target is worse than no benchmark,
  and an `UNKNOWN` target is how the codebase says so out loud.
* **A benchmark that cannot run says why, in words that name the missing
  capability.** `blocked_reason` is not an apology; it is the phase's most
  useful output — a catalogue that reports "blocked: needs shell elements" is
  telling you what to build next, where a silent skip tells you nothing.

**Outcomes are six, and only one of them is a pass.** `MEASURED` — ran fine,
nothing to compare against — is never a pass, for the same reason
`app.design.assertions` treats an unmeasured assertion as never a pass. A suite
that counted `MEASURED` as green would report a validated solver on a catalogue
where no target had been entered at all. Nor is `UNCONVERGED`, where the case
ran and 7.2 refused to let the number out.

**Verification and validation are two axes and this module is one of them.**
ASME V&V 20 separates *verification* — are the equations being solved correctly,
which is what `app.verify.convergence` measures — from *validation* — are they
the right equations, which is what comparing against a published benchmark
measures. An `Outcome` is a validation verdict; the convergence verdict rides
alongside it and a catalogue must report both. Collapsing them would let a case
that happened to land on the target from a grid nobody checked read as fully
evidenced.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.verify.convergence import ConvergenceStudy
from app.verify.provenance import RunProvenance


class TargetBasis(StrEnum):
    """Where a benchmark's expected answer came from."""

    #: Reproduced from a published reference, named in `source`.
    PUBLISHED = "published"
    #: Computed from a closed-form solution implemented in this repository.
    #: `source` names the formula and the function; the target is exact by
    #: construction, so it cannot rot the way a transcribed number can.
    DERIVED = "derived"
    #: Not encoded. `reason` says what must happen before it can be.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Target:
    """The answer a benchmark is expected to produce, and its pedigree."""

    basis: TargetBasis
    unit: str
    value: float | None = None
    #: Acceptance band as a fraction of the target — 0.02 is ±2%. It must be
    #: justified in `tolerance_reason`: a tolerance chosen after seeing the
    #: result is not a tolerance, it is a curve fit.
    tolerance: float | None = None
    tolerance_reason: str = ""
    source: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.basis is TargetBasis.UNKNOWN:
            if self.value is not None:
                raise ValueError(
                    "A target marked UNKNOWN carries no value. Recording a number "
                    "under an 'unknown' basis is how a guess becomes a benchmark "
                    "target; if the value is known, cite it as PUBLISHED."
                )
            if not self.reason.strip():
                raise ValueError(
                    "An unknown target must say what would make it known — which "
                    "document to read, which value to look up."
                )
            return

        if self.value is None:
            raise ValueError(f"A {self.basis} target must carry a value.")
        if self.value == 0.0:
            raise ValueError(
                "A target of exactly zero cannot carry a relative tolerance, because "
                "`tolerance` is a fraction *of the target* and every fraction of zero "
                "is zero. A benchmark whose published answer is zero — a symmetry "
                "plane that must not move, a rigid-body mode — is an absolute "
                "comparison against a floor, not a percentage band, and encoding it "
                "here would compare an absolute deviation against a fractional band "
                "and call any small number validated."
            )
        if self.tolerance is None or not 0.0 < self.tolerance < 1.0:
            raise ValueError(
                f"A {self.basis} target needs a tolerance in (0, 1) — the fraction of "
                "the target the measured value may differ by. Without one there is no "
                "statement of accuracy, and 7.4 asks precisely for accuracy."
            )
        if not self.tolerance_reason.strip():
            raise ValueError(
                "A tolerance must be justified: what physical or discretisation error "
                "does this band cover? An unjustified band is one that will be widened "
                "the first time a run misses it."
            )
        if not self.source.strip():
            raise ValueError(
                f"A {self.basis} target must name its source. A number with no source "
                "is not a benchmark, whatever it is compared against."
            )

    @property
    def known(self) -> bool:
        return self.basis is not TargetBasis.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"basis": str(self.basis), "unit": self.unit}
        if self.known:
            payload |= {
                "value": self.value,
                "tolerance": self.tolerance,
                "tolerance_reason": self.tolerance_reason,
                "source": self.source,
            }
        else:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class BenchmarkRun:
    """What a benchmark's `run` callable hands back.

    `value` is `None` when the case ran but the convergence study refused to let
    a number be stated. That is not an error and not a failure to compare — it
    is 7.2 reaching the benchmark layer, and `run_benchmark` classifies it as
    `UNCONVERGED`.
    """

    value: float | None
    provenance: RunProvenance
    convergence: ConvergenceStudy | None = None


@dataclass(frozen=True)
class Benchmark:
    """One published problem, encoded.

    `run` is `None` for a benchmark that cannot be executed against this
    codebase yet; `blocked_reason` then says which capability is missing. That
    pairing is enforced, so a benchmark can never be silently skipped.
    """

    id: str
    title: str
    #: One of the analyses a catalogue groups by: linear-static, modal,
    #: buckling, thermal-stress. Free text; `BenchmarkOutcome` carries it through
    #: so a report can group on it.
    analysis: str
    description: str
    target: Target
    run: Callable[[], BenchmarkRun] | None = None
    blocked_reason: str = ""
    #: True when a run takes long enough that it should not sit in the default
    #: test path. `run_suite` filters on it.
    slow: bool = False
    references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("A benchmark needs an id; a report keys on it.")
        if self.run is None and not self.blocked_reason.strip():
            raise ValueError(
                f"Benchmark {self.id!r} has no `run` and no `blocked_reason`. A case "
                "that cannot execute must name the capability it is waiting for — "
                "that reason is the useful output, and without it the case is just a "
                "silent skip."
            )
        if self.run is not None and self.blocked_reason.strip():
            raise ValueError(
                f"Benchmark {self.id!r} is both runnable and blocked. Give one."
            )

    @property
    def runnable(self) -> bool:
        return self.run is not None


class Outcome(StrEnum):
    """The *validation* verdict for one benchmark. Exactly one of these is a pass.

    The verification verdict — whether the discretisation error was bounded — is
    the convergence study's, and is carried separately. See the module docstring.
    """

    #: Ran, a known target existed, and the measured value is inside the band.
    VALIDATED = "validated"
    #: Ran, a known target existed, and the measured value is outside the band.
    DEVIATED = "deviated"
    #: Ran and produced a number, but there is no target to compare it against.
    #: **Never a pass.** The same rule `assertions.py` applies to an assertion
    #: nothing could measure.
    MEASURED = "measured"
    #: Ran, but the convergence study would not permit a value to be stated, so
    #: there is nothing to compare. **Never a pass**, and deliberately distinct
    #: from `DEVIATED`: the model may well be right, and no one can tell yet.
    UNCONVERGED = "unconverged"
    #: Could not run. `detail` names the missing capability.
    BLOCKED = "blocked"
    #: Ran and raised. `detail` carries the traceback's last line.
    ERRORED = "errored"


@dataclass(frozen=True)
class BenchmarkOutcome:
    """The result of running one benchmark, with everything behind it."""

    benchmark_id: str
    title: str
    analysis: str
    outcome: Outcome
    target: Target
    measured_value: float | None = None
    relative_deviation: float | None = None
    detail: str = ""
    seconds: float = 0.0
    provenance: dict[str, Any] | None = None
    convergence: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        """True only for `VALIDATED`. Written as a property so no caller has to
        remember which of the six outcomes count — and so `MEASURED` cannot be
        mistaken for one that does."""
        return self.outcome is Outcome.VALIDATED

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.benchmark_id,
            "title": self.title,
            "analysis": self.analysis,
            "outcome": str(self.outcome),
            "target": self.target.to_dict(),
            "measured_value": self.measured_value,
            "relative_deviation": self.relative_deviation,
            "seconds": round(self.seconds, 3),
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.provenance is not None:
            payload["provenance"] = self.provenance
        if self.convergence is not None:
            payload["convergence"] = self.convergence
        return payload


def run_benchmark(benchmark: Benchmark) -> BenchmarkOutcome:
    """Run one benchmark and classify it. Never raises.

    Never raises because a suite of benchmarks is a *report*, and one case
    blowing up must not remove the evidence for the other twenty. The exception
    is recorded as `ERRORED`, which is not a pass, so nothing is hidden by the
    catch — an errored case is as loud in a report as a deviated one.
    """
    if benchmark.run is None:
        return BenchmarkOutcome(
            benchmark_id=benchmark.id,
            title=benchmark.title,
            analysis=benchmark.analysis,
            outcome=Outcome.BLOCKED,
            target=benchmark.target,
            detail=benchmark.blocked_reason,
        )

    started = time.perf_counter()
    try:
        run = benchmark.run()
    except Exception as exc:  # noqa: BLE001 - a report survives one bad case
        return BenchmarkOutcome(
            benchmark_id=benchmark.id,
            title=benchmark.title,
            analysis=benchmark.analysis,
            outcome=Outcome.ERRORED,
            target=benchmark.target,
            detail=f"{type(exc).__name__}: {exc}",
            seconds=time.perf_counter() - started,
            provenance={"traceback": traceback.format_exc().splitlines()[-1]},
        )
    seconds = time.perf_counter() - started

    provenance = run.provenance.to_dict()
    convergence = None if run.convergence is None else run.convergence.to_dict()

    if run.value is None:
        return BenchmarkOutcome(
            benchmark_id=benchmark.id,
            title=benchmark.title,
            analysis=benchmark.analysis,
            outcome=Outcome.UNCONVERGED,
            target=benchmark.target,
            detail=(
                run.convergence.report()
                if run.convergence is not None
                else "The case reported no value and gave no convergence study to explain why."
            ),
            seconds=seconds,
            provenance=provenance,
            convergence=convergence,
        )

    if not benchmark.target.known:
        return BenchmarkOutcome(
            benchmark_id=benchmark.id,
            title=benchmark.title,
            analysis=benchmark.analysis,
            outcome=Outcome.MEASURED,
            target=benchmark.target,
            measured_value=run.value,
            detail=(
                "The case ran and produced a value, but no published target is "
                "encoded, so nothing has been validated. " + benchmark.target.reason
            ),
            seconds=seconds,
            provenance=provenance,
            convergence=convergence,
        )

    assert benchmark.target.value is not None  # guaranteed by Target.__post_init__
    assert benchmark.target.tolerance is not None
    reference = benchmark.target.value
    # `Target.__post_init__` refuses a zero-valued target, so this division is
    # safe by construction rather than by a fallback that would silently compare
    # an absolute deviation against a fractional band.
    deviation = (run.value - reference) / reference
    inside = abs(deviation) <= benchmark.target.tolerance

    return BenchmarkOutcome(
        benchmark_id=benchmark.id,
        title=benchmark.title,
        analysis=benchmark.analysis,
        outcome=Outcome.VALIDATED if inside else Outcome.DEVIATED,
        target=benchmark.target,
        measured_value=run.value,
        relative_deviation=deviation,
        detail=(
            f"{run.value:.6g} against {reference:.6g} {benchmark.target.unit} "
            f"({deviation * 100:+.3f}%, band ±{benchmark.target.tolerance * 100:.3f}%)"
        ),
        seconds=seconds,
        provenance=provenance,
        convergence=convergence,
    )


@dataclass(frozen=True)
class Suite:
    """A named collection of benchmarks, with the id uniqueness they need.

    Duplicate ids raise at construction, the same rule `app.catia_kb` applies to
    its entries and for the same reason: a report keys on the id, so a
    duplicate would silently overwrite a case's result with another case's.
    """

    name: str
    benchmarks: tuple[Benchmark, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for benchmark in self.benchmarks:
            if benchmark.id in seen:
                raise ValueError(
                    f"Duplicate benchmark id {benchmark.id!r} in suite {self.name!r}. "
                    "A report keys on the id and would report one case's result "
                    "under the other's name."
                )
            seen.add(benchmark.id)

    def select(self, *, include_slow: bool = False) -> tuple[Benchmark, ...]:
        return tuple(b for b in self.benchmarks if include_slow or not b.slow)


def run_suite(suite: Suite, *, include_slow: bool = False) -> tuple[BenchmarkOutcome, ...]:
    """Run a suite and return every outcome, in catalogue order.

    A blocked benchmark is reported whether or not it is slow: it costs nothing
    to run and its blocker is the point.
    """
    chosen = tuple(
        b for b in suite.benchmarks if include_slow or not b.slow or not b.runnable
    )
    return tuple(run_benchmark(b) for b in chosen)


__all__ = [
    "Benchmark",
    "BenchmarkOutcome",
    "BenchmarkRun",
    "Outcome",
    "Suite",
    "Target",
    "TargetBasis",
    "run_benchmark",
    "run_suite",
]
