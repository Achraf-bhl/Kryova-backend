"""Running a compiled plan, and reporting exactly how far it got.

`compile_spec` turns a design into a `Plan`; this walks one. That is the whole
job, and it is small on purpose — the compiler already refused everything it
could refuse, so an executor that starts doing its own checking is an executor
that has begun to disagree with the compiler about what a valid plan is.

Three things it does that a `for` loop over the calls would not.

**It resolves the one late-bound value.** A plan may carry `Created(feature)`
meaning "whatever CATIA called the thing that feature's call made". Only the
run knows that, so the executor collects each creating call's reported `feature`
into a map and hands it to `bind()` before the next call goes out.

**It stops at the first failure, and names the feature.** A build is ordered and
each feature is generally standing on the one before it, so continuing past a
failed pad produces a cascade of errors about geometry that was never made, and
the useful one — the first — scrolls away. What comes back instead is the index,
the tool, the semantic name of the feature, and the message the seat gave.

**It reports what it did rather than what it intended.** `BuildReport` carries
the calls that actually completed and the CATIA names they produced. That is
what a failed run needs in order to be diagnosed (C4), and what a successful one
needs in order to be measured (C3) — in both cases the interesting question is
about the part that exists, and the plan only describes the part that was meant.

**The runner is injected, not imported.** Everything else in `app/design/` is
pure: it depends on the operation registry and on nothing that holds a database
session or a socket. Executing is where that would break, so the seam is a
callable — `app.catia.dispatch.call_catia` bound to a user in production, a mock
in tests, and a recorder when a plan is being dry-run. The package keeps its
property that a design can be compiled, diffed and checked with no CATIA
anywhere, which is what makes the tests for all of it run offline.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.design.compile import Created, Plan, PlannedCall, bind
from app.design.errors import SpecError

#: The key a mutating tool reports its new element's CATIA name under. Every
#: creating operation returns it (`_mutation_result` in the daemon builds it),
#: and the compiler's rename depends on it, so its absence is a real failure
#: rather than a missing nicety — see `_created_name`.
FEATURE_KEY = "feature"


class CallRunner(Protocol):
    """Whatever actually performs one call.

    Deliberately narrower than `call_catia`: no database, no user, no
    conversation. Those are bound at the edge, so nothing in this package has to
    know they exist.

    An implementation signals failure by raising. Any exception is caught and
    turned into a `BuildFailure` — the executor does not care whether it was a
    `CatiaError`, a timeout or a socket dying, because the answer is the same in
    every case: stop, say where, keep what was built.
    """

    def __call__(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class CallResult:
    """One completed call and what it returned."""

    index: int
    tool: str
    feature: str | None
    arguments: Mapping[str, Any]
    result: Mapping[str, Any]
    seconds: float

    @property
    def created_name(self) -> str | None:
        """The CATIA name this call reported making, if it made anything."""
        name = self.result.get(FEATURE_KEY)
        return str(name) if isinstance(name, str) and name else None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"index": self.index, "tool": self.tool}
        if self.feature is not None:
            out["feature"] = self.feature
        created = self.created_name
        if created is not None:
            out["created"] = created
        out["seconds"] = round(self.seconds, 3)
        return out


@dataclass(frozen=True)
class BuildFailure:
    """Where a run stopped, in the terms the design was written in.

    `feature` is the semantic name — `plate.lightening_pocket`, not `Pocket.2` —
    because that is the handle the author has on it and the thing they would
    edit. `catia_name` is carried alongside when one was allocated, since that is
    what an engineer looking at the tree on the workstation will see.
    """

    index: int
    tool: str
    feature: str | None
    catia_name: str | None
    message: str

    #: True when the executor refused to make the call at all — a late-bound
    #: name that no result supplied. Distinguished from a seat refusal because
    #: the recovery is different: this one is a bug here or in the compiler, and
    #: retrying the same plan cannot fix it.
    before_the_call: bool = False

    def __str__(self) -> str:
        where = f"{self.tool} (call {self.index})"
        if self.feature is not None:
            where = f"{self.feature} — {where}"
        return f"{where}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "index": self.index,
            "tool": self.tool,
            "message": self.message,
        }
        if self.feature is not None:
            out["feature"] = self.feature
        if self.catia_name is not None:
            out["catia_name"] = self.catia_name
        if self.before_the_call:
            out["before_the_call"] = True
        return out


@dataclass(frozen=True)
class BuildReport:
    """What running a plan actually did.

    Truthy when the whole plan ran. A partial run is falsey and carries the
    failure, so `if report:` reads correctly at a call site that only cares
    whether the part got built.
    """

    design: str
    plan_digest: str
    completed: tuple[CallResult, ...] = ()
    failure: BuildFailure | None = None

    #: Semantic feature name -> the name CATIA gave what its creating call made.
    #: The map `bind()` was fed, kept because it is the only record of what the
    #: seat invented, and a diagnosis often turns on it.
    created: Mapping[str, str] = field(default_factory=dict)

    #: Features the plan did not build because their `when` was false. Carried
    #: from the plan so a report answers "where is the pocket?" without needing
    #: the plan beside it.
    suppressed: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.failure is None

    def __bool__(self) -> bool:
        return self.ok

    def __iter__(self) -> Iterator[CallResult]:
        return iter(self.completed)

    def __len__(self) -> int:
        return len(self.completed)

    @property
    def seconds(self) -> float:
        return sum(call.seconds for call in self.completed)

    def features_built(self) -> tuple[str, ...]:
        """Semantic names of the features that completed, in build order."""
        seen: dict[str, None] = {}
        for call in self.completed:
            if call.feature is not None:
                seen.setdefault(call.feature, None)
        return tuple(seen)

    def last_result(self) -> Mapping[str, Any]:
        """The final call's payload — the freshest post-state the run produced.

        Mutating tools return the mass and bounding box alongside the feature
        name, so the last one is usually a measurement of the finished part and
        is what an assertion check can run against without another round trip.
        """
        return self.completed[-1].result if self.completed else {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "design": self.design,
            "plan_digest": self.plan_digest,
            "ok": self.ok,
            "completed": [call.to_dict() for call in self.completed],
            "created": dict(sorted(self.created.items())),
        }
        if self.suppressed:
            out["suppressed"] = list(self.suppressed)
        if self.failure is not None:
            out["failure"] = self.failure.to_dict()
        return out


def execute_plan(
    plan: Plan,
    runner: CallRunner,
    *,
    stop_after: int | None = None,
) -> BuildReport:
    """Run `plan` through `runner`, stopping at the first failure.

    Never raises for a CAD failure — a seat refusing a pad is an outcome, and
    the caller (a self-correction loop, an API handler) wants it as data. The
    only exceptions that escape are the ones that mean this code is wrong.

    `stop_after` runs a prefix of the plan, which is what a partial rebuild
    needs: recompile, find the first call that differs from last time, and
    replay only from there.
    """
    created: dict[str, str] = {}
    completed: list[CallResult] = []

    for call in plan.calls:
        if stop_after is not None and call.index >= stop_after:
            break
        outcome = _run_one(call, runner, created, plan)
        if isinstance(outcome, BuildFailure):
            return BuildReport(
                design=plan.design,
                plan_digest=plan.digest(),
                completed=tuple(completed),
                failure=outcome,
                created=dict(created),
                suppressed=plan.suppressed,
            )
        completed.append(outcome)
        _record_created(outcome, created)

    return BuildReport(
        design=plan.design,
        plan_digest=plan.digest(),
        completed=tuple(completed),
        failure=None,
        created=dict(created),
        suppressed=plan.suppressed,
    )


def _run_one(
    call: PlannedCall,
    runner: CallRunner,
    created: Mapping[str, str],
    plan: Plan,
) -> CallResult | BuildFailure:
    """One call: bind its late-bound values, run it, time it."""
    catia_name = _catia_name(plan, call.feature)

    try:
        arguments = bind(call.arguments, created)
    except SpecError as exc:
        # The compiler emitted a `Created` for something whose creating call
        # reported no feature name. Worth a distinct failure: no amount of
        # retrying the plan fixes it, and the fix is in the tool, not the design.
        return BuildFailure(
            index=call.index,
            tool=call.tool,
            feature=call.feature,
            catia_name=catia_name,
            message=str(exc),
            before_the_call=True,
        )

    started = time.monotonic()
    try:
        result = runner(call.tool, arguments)
    except Exception as exc:  # noqa: BLE001 - every failure is the same outcome here
        return BuildFailure(
            index=call.index,
            tool=call.tool,
            feature=call.feature,
            catia_name=catia_name,
            message=str(exc) or exc.__class__.__name__,
        )
    elapsed = time.monotonic() - started

    return CallResult(
        index=call.index,
        tool=call.tool,
        feature=call.feature,
        arguments=arguments,
        result=dict(result or {}),
        seconds=elapsed,
    )


def _record_created(call: CallResult, created: dict[str, str]) -> None:
    """Remember what a feature's *creating* call made.

    `setdefault`, not assignment, and the distinction is load-bearing. A feature
    that cannot be named on creation compiles to two calls with the same
    semantic name — the pad, then the rename — and both return a `feature` key.
    The second returns the new name, so assigning would leave the map holding
    the post-rename name under a key whose whole purpose is to answer "what did
    CATIA call it *before* we renamed it". Nothing downstream needs that today,
    because the rename is the only consumer and it runs first; recording the
    creating call keeps the contract true if a second consumer ever appears.
    """
    if call.feature is None:
        return
    name = call.created_name
    if name is not None:
        created.setdefault(call.feature, name)


def _catia_name(plan: Plan, feature: str | None) -> str | None:
    """The name the plan intended for a feature, if it allocated one."""
    if feature is None:
        return None
    try:
        return plan.catia_name(feature)
    except SpecError:
        # Unaddressable features have no allocated name. Not an error here —
        # the plan already reported them, and a failure in one still wants to
        # be reported by its semantic name.
        return None


__all__ = [
    "FEATURE_KEY",
    "BuildFailure",
    "BuildReport",
    "CallResult",
    "CallRunner",
    "Created",
    "execute_plan",
]
