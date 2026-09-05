"""The loop: build it, check it, work out what is wrong, change the spec, repeat.

C4 in the roadmap, and the thing that makes the rest of Layer B and C feel like
intelligence rather than plumbing. Everything it needs already exists —
`compile_spec` turns a design into a plan, `execute_plan` runs one,
`check_assertions` says whether the result is acceptable, `diff_specs` says what
an edit reached. This assembles them into a bounded cycle and, when the cycle
stops, says exactly why it stopped.

**Deciding when to give up is the hard half, not deciding what to try.** An
unbounded retry loop is the documented way an agent burns a budget without
converging, so there is a hard attempt cap. But a cap alone is a blunt
instrument, and this package can do considerably better than one, because the
compiler is *deterministic*:

* **A repair that does not change the plan cannot change the outcome.** Same
  plan digest ⇒ same calls ⇒ same geometry ⇒ same failure. So a repair that
  compiles to a plan identical to the one that just failed ends the loop
  immediately, with the attempt not counted against the budget — it was not an
  attempt, it was a no-op.
* **A plan seen before is a cycle.** If attempt three compiles to the plan
  attempt one already ran, the loop is oscillating between two designs and will
  do so until the cap. Both of these are *exact* tests rather than heuristics,
  and they exist only because `Plan.digest()` is a real identity.

**A diagnosis that cannot say why is a retry counter wearing a costume.** That
is the distinction the literature on these loops keeps arriving at, and it is
why `Diagnosis` carries the measured value, the bound, the size and direction of
the gap, the feature that failed, and — the part that is actually hard to get
right — *which parameters the repairer is allowed to change*. A derived
parameter cannot be set, so offering one as a fix produces a repair that
`set_parameter` refuses; the diagnosis names what it is derived from instead.

**A repair that produces a spec which does not compile is a normal attempt, not
a crash.** The compiler's error already names the feature and says what to do,
which is the best feedback in the system, so it goes back round as the next
diagnosis. That is the single most valuable path through this loop, because it
is the one an LLM repairer takes most often.

**Nothing here calls CATIA or an LLM.** Building and repairing are both injected
callables, exactly like the solver and job-queue seams elsewhere in this
codebase. The loop is therefore testable end to end with no seat and no model,
which is what `tests/test_design_correct.py` does — and a production wiring
supplies a builder backed by `app.catia.dispatch` and a repairer backed by the
agent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from app.design.assertions import Assertion, AssertionReport, check_assertions
from app.design.compile import Plan, compile_spec
from app.design.diff import builds_the_same, diff_specs
from app.design.errors import SpecError
from app.design.execute import BuildReport
from app.design.spec import DesignSpec

#: How many repairs to try before giving up, when a caller does not say. Three
#: is chosen rather than derived: it is enough for the common shape (build,
#: notice one thing wrong, fix it, confirm) and small enough that a loop which
#: is not converging is abandoned before it has cost much.
DEFAULT_MAX_ATTEMPTS: int = 3


class Stop(StrEnum):
    """Why the loop ended. Every one of these is a different next step."""

    #: Everything built and every assertion passed.
    SATISFIED = "satisfied"

    #: The attempt budget ran out with the design still failing.
    EXHAUSTED = "exhausted"

    #: The repairer declined to propose anything further.
    DECLINED = "declined"

    #: The repair compiled to a plan identical to one that had already failed.
    NO_PROGRESS = "no_progress"

    #: The repair compiled to a plan the loop had already run and moved on from.
    CYCLE = "cycle"


@dataclass(frozen=True)
class Diagnosis:
    """What went wrong, in terms specific enough to act on.

    Handed to the repairer as the entire brief. If a field here is vague, the
    repair is a guess.
    """

    #: "compile" | "build" | "assertion" — which stage failed. The recoveries
    #: are genuinely different: a compile failure is a bad spec, a build failure
    #: is the seat refusing something, an assertion failure is a part that was
    #: built correctly and is not good enough.
    stage: str

    #: One-line statement of the problem, already actionable.
    summary: str

    #: The full detail, one line each: failing assertions with their gaps, or
    #: the failing call with its message.
    details: tuple[str, ...] = ()

    #: The semantic name of the feature at fault, when there is exactly one.
    feature: str | None = None

    #: Parameters the repairer may set, with their current values. Derived
    #: parameters are excluded because `set_parameter` refuses them.
    settable: Mapping[str, float] = field(default_factory=dict)

    #: Derived parameter -> the expression it comes from. Offered so a repairer
    #: that wants to move one is told what to move instead of being refused.
    derived: Mapping[str, str] = field(default_factory=dict)

    #: The reports behind this diagnosis, for a repairer that wants more than
    #: the prose. None on whichever stage was not reached.
    build: BuildReport | None = None
    assertions: AssertionReport | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "stage": self.stage,
            "summary": self.summary,
            "details": list(self.details),
            "settable": dict(sorted(self.settable.items())),
        }
        if self.feature is not None:
            out["feature"] = self.feature
        if self.derived:
            out["derived"] = dict(sorted(self.derived.items()))
        return out

    def __str__(self) -> str:
        lines = [self.summary, *self.details]
        if self.settable:
            names = ", ".join(f"{k}={v:g}" for k, v in sorted(self.settable.items()))
            lines.append(f"Parameters that can be changed: {names}.")
        if self.derived:
            pairs = ", ".join(f"{k} (={v})" for k, v in sorted(self.derived.items()))
            lines.append(f"Derived, so change what they read instead: {pairs}.")
        return "\n".join(lines)


class Builder(Protocol):
    """Compile-and-run, injected.

    Takes the plan and returns what happened. In production this closes over a
    database session and a user; in tests it is a few lines.
    """

    def __call__(self, plan: Plan) -> BuildReport:
        ...


class Measurer(Protocol):
    """Turn a completed build into the numbers the assertions read.

    Separate from the builder because measuring is a *choice* — the cheap route
    reads the post-state the last mutating call already returned, and the
    thorough one spends a round trip on `catia_measure`. A caller picks.
    """

    def __call__(self, report: BuildReport) -> Mapping[str, Any]:
        ...


class Repairer(Protocol):
    """Propose a new spec given what is wrong, or decline by returning None.

    Declining is a first-class answer and the loop stops cleanly on it. A
    repairer that cannot see a fix should say so rather than return the spec it
    was given, which would look like a proposal and be caught one step later as
    no-progress.
    """

    def __call__(self, spec: DesignSpec, diagnosis: Diagnosis) -> DesignSpec | None:
        ...


@dataclass(frozen=True)
class Attempt:
    """One trip round the loop."""

    number: int
    spec: DesignSpec
    plan: Plan | None
    build: BuildReport | None
    assertions: AssertionReport | None
    diagnosis: Diagnosis | None

    @property
    def ok(self) -> bool:
        return self.diagnosis is None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"attempt": self.number, "ok": self.ok}
        if self.plan is not None:
            out["plan_digest"] = self.plan.digest()
        if self.build is not None:
            out["build"] = self.build.to_dict()
        if self.assertions is not None:
            out["assertions"] = self.assertions.to_dict()
        if self.diagnosis is not None:
            out["diagnosis"] = self.diagnosis.to_dict()
        return out


@dataclass(frozen=True)
class CorrectionReport:
    """The whole run: every attempt, and why it stopped.

    Truthy when the design ended up satisfying its assertions.
    """

    design: str
    stop: Stop
    attempts: tuple[Attempt, ...] = ()

    @property
    def ok(self) -> bool:
        return self.stop is Stop.SATISFIED

    def __bool__(self) -> bool:
        return self.ok

    def __len__(self) -> int:
        return len(self.attempts)

    def __iter__(self) -> Any:
        return iter(self.attempts)

    @property
    def final(self) -> Attempt | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def spec(self) -> DesignSpec | None:
        """The spec the loop ended on — the repaired one, if it repaired anything."""
        return self.final.spec if self.final else None

    def changed(self) -> bool:
        """Did the loop actually change the design it was given?"""
        if len(self.attempts) < 2:
            return False
        return self.attempts[0].spec.digest() != self.attempts[-1].spec.digest()

    def summary(self) -> str:
        n = len(self.attempts)
        plural = "" if n == 1 else "s"
        if self.stop is Stop.SATISFIED:
            head = f"{self.design}: satisfied after {n} attempt{plural}"
            if self.changed():
                changed = diff_specs(self.attempts[0].spec, self.attempts[-1].spec)
                return f"{head}, having changed {changed.what_changed()}."
            return f"{head}, unchanged."
        reasons = {
            Stop.EXHAUSTED: f"gave up after {n} attempt{plural}",
            Stop.DECLINED: "no further repair was proposed",
            Stop.NO_PROGRESS: "the proposed repair builds exactly the same part",
            Stop.CYCLE: "the repairs started repeating a design already tried",
        }
        tail = reasons[self.stop]
        last = self.final
        detail = f"\n{last.diagnosis}" if last and last.diagnosis else ""
        return f"{self.design}: {tail}.{detail}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "design": self.design,
            "stop": str(self.stop),
            "ok": self.ok,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


def correct(
    spec: DesignSpec,
    *,
    build: Builder,
    repair: Repairer,
    assertions: Iterable[Assertion] = (),
    measure: Measurer | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> CorrectionReport:
    """Build, check, repair, rebuild — until it is right or it is hopeless.

    `measure` defaults to reading the post-state the last mutating call already
    returned, which carries mass, volume and the bounding box and is free. Pass
    one that calls `catia_measure` when an assertion needs something that is not
    in there.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1; there is no loop with zero attempts.")

    wanted = tuple(assertions)
    measurer = measure or _last_result
    attempts: list[Attempt] = []
    seen: list[Plan] = []
    current = spec

    while True:
        attempt = _one_attempt(len(attempts) + 1, current, build, measurer, wanted)
        attempts.append(attempt)

        if attempt.ok:
            return CorrectionReport(
                design=current.name, stop=Stop.SATISFIED, attempts=tuple(attempts)
            )

        if attempt.plan is not None:
            seen.append(attempt.plan)

        if len(attempts) >= max_attempts:
            return CorrectionReport(
                design=current.name, stop=Stop.EXHAUSTED, attempts=tuple(attempts)
            )

        assert attempt.diagnosis is not None  # noqa: S101 - guaranteed by `ok` above
        proposed = repair(current, attempt.diagnosis)
        if proposed is None:
            return CorrectionReport(
                design=current.name, stop=Stop.DECLINED, attempts=tuple(attempts)
            )

        stop = _progress_check(proposed, attempt.plan, seen)
        if stop is not None:
            return CorrectionReport(design=current.name, stop=stop, attempts=tuple(attempts))

        current = proposed


def _one_attempt(
    number: int,
    spec: DesignSpec,
    build: Builder,
    measure: Measurer,
    assertions: Sequence[Assertion],
) -> Attempt:
    """Compile, build, measure, check — and diagnose whatever stopped it."""
    try:
        plan = compile_spec(spec)
    except SpecError as exc:
        # The most common failure when the repairer is a language model, and the
        # most useful: the compiler's message already names the feature and says
        # what to do, so it goes straight back as the brief for the next repair.
        return Attempt(
            number=number,
            spec=spec,
            plan=None,
            build=None,
            assertions=None,
            diagnosis=Diagnosis(
                stage="compile",
                summary=f"The design does not compile: {exc}",
                details=(),
                settable=_settable(spec),
                derived=_derived(spec),
            ),
        )

    report = build(plan)
    if not report.ok:
        failure = report.failure
        assert failure is not None  # noqa: S101 - `ok` is defined by its absence
        return Attempt(
            number=number,
            spec=spec,
            plan=plan,
            build=report,
            assertions=None,
            diagnosis=Diagnosis(
                stage="build",
                summary=f"The build stopped at {failure.tool}: {failure.message}",
                details=(str(failure),),
                feature=failure.feature,
                settable=_settable(spec),
                derived=_derived(spec),
                build=report,
            ),
        )

    if not assertions:
        # Nothing was claimed about the part, so nothing can be checked. A build
        # that ran is the whole of the answer.
        return Attempt(
            number=number, spec=spec, plan=plan, build=report, assertions=None, diagnosis=None
        )

    checked = check_assertions(assertions, measure(report), parameters=plan.parameters)
    if checked.ok:
        return Attempt(
            number=number, spec=spec, plan=plan, build=report, assertions=checked, diagnosis=None
        )

    return Attempt(
        number=number,
        spec=spec,
        plan=plan,
        build=report,
        assertions=checked,
        diagnosis=_assertion_diagnosis(spec, report, checked),
    )


def _assertion_diagnosis(
    spec: DesignSpec, build: BuildReport, checked: AssertionReport
) -> Diagnosis:
    """Turn a failed assertion report into something a repairer can act on."""
    failed = checked.failed
    unmeasured = checked.unmeasured
    if failed:
        head = (
            f"{len(failed)} assertion(s) failed on a part that built successfully"
            if len(failed) > 1
            else f"{failed[0].name} failed"
        )
    else:
        head = f"{len(unmeasured)} assertion(s) could not be checked"
    return Diagnosis(
        stage="assertion",
        summary=head + ".",
        details=tuple(str(result) for result in (*failed, *unmeasured)),
        settable=_settable(spec),
        derived=_derived(spec),
        build=build,
        assertions=checked,
    )


def _progress_check(
    proposed: DesignSpec, previous: Plan | None, seen: list[Plan]
) -> Stop | None:
    """Would running this repair tell us anything we do not already know?

    Both answers here are exact rather than heuristic, and both depend on the
    compiler being deterministic. A proposal that does not compile is neither —
    it is a genuine new attempt, and the next pass will diagnose it — so it is
    allowed straight through.

    Compared with `builds_the_same` rather than by plan digest, because a plan
    digest covers each call's `note`. A repairer that rewrote a rationale and
    changed nothing else would otherwise read as progress and be run again for
    an identical result, which is the exact failure this check exists to stop.
    """
    try:
        plan = compile_spec(proposed)
    except SpecError:
        return None

    if previous is not None and builds_the_same(previous, plan):
        return Stop.NO_PROGRESS
    if any(builds_the_same(earlier, plan) for earlier in seen):
        return Stop.CYCLE
    return None


def _last_result(report: BuildReport) -> Mapping[str, Any]:
    """The default measurement: what the final mutating call already reported."""
    return report.last_result()


def _settable(spec: DesignSpec) -> dict[str, float]:
    """The parameters a repair is allowed to change, with their current values."""
    return {
        parameter.name: float(parameter.value)
        for parameter in spec.parameters
        if not parameter.is_derived and parameter.value is not None
    }


def _derived(spec: DesignSpec) -> dict[str, str]:
    """The parameters a repair may not set, and what they are computed from."""
    return {
        parameter.name: str(parameter.expression)
        for parameter in spec.parameters
        if parameter.is_derived and parameter.expression is not None
    }


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "Attempt",
    "Builder",
    "CorrectionReport",
    "Diagnosis",
    "Measurer",
    "Repairer",
    "Stop",
    "correct",
]
