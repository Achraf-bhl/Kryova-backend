"""Building a whole plan in one go rather than one round trip at a time (E15 task 1).

The phase states it as *"a `Plan` as one kernel session (OCCT); a CATScript
executed once (CATIA), never 10⁵ COM calls."* The two halves have different
shapes because the two backends do.

**OCCT: one session was already the design; what was missing is the economics.**
`OcctRunner` holds one `PartDocument` for its lifetime, so a plan run through one
runner is already one session — the naming labels persist, nothing is reopened.
What made a large plan expensive was not the session boundary but `Detail.FULL`:
every mutating call measures the whole shape afterwards, because the interactive
agent cannot react to a number it was not given. Measuring integrates over the
part, so at 10⁵ operations that *is* the run. `batch_detail` is the knob, and
`build` is the entry point that turns it down and says so.

**CATIA: one script, and it is worth about 2× — measured, not assumed.** This
paragraph used to say "there is no way to lower the cost of a COM round trip;
the only fix is not to make 10⁵ of them", which is true about the round trip and
overstates what the fix buys. Measured on the V5-R33 seat on 2026-09-18, the
same work twice, each route made to prove it built what it claimed:

    200 points (2 COM calls each)   1.022 s over COM   0.566 s in one script   1.8×
    25 pads (~9 COM calls each)     1.826 s over COM   0.835 s in one script   2.2×

So a script is **about twice as fast, on both the cheapest call CATIA has and on
a real feature**, and the second number is the surprising one: the saving was
expected to collapse on a pad, where CATIA has actual geometry to build, and it
does not — because a pad is still nine COM calls and V5 builds a 20 mm block
faster than the marshalling costs. The saving tracks the *number of calls*, not
their weight.

What that means for the 10⁵ in the phase's own wording: **2.0 hours over COM
against 0.9 hours as one script.** A 2× is worth having and it does not change
what is feasible — a plan that is intractable interactively is intractable
batched. Anyone reaching for this to make a big plan possible should read that
pair of numbers first and go and find the other order of magnitude somewhere
else.

`as_catscript` emits a plan as one CATScript, which the seat runs once. That is
written here rather than in `app/catia/` because the input is a `Plan` and this
package is where a plan is turned into something a backend will accept.

**The CATIA half is emitted and still not driven, and the reason is now sharper
than "there is no seat".** There is a seat, and the timings above were taken on
it. What is missing is `KryovaDispatch`: every emitted line calls it and **it
does not exist anywhere in this repository or on the seat**. THE QUEUE E4 says
to write it, and its own wording contains the contradiction that has kept it
unwritten — write the dispatcher in CATScript, but do not re-implement CATIA's
API a second time, when the mapping it would have to reuse (`scripts/catia_bridge/
com/`) is Python and cannot be called from inside CNEXT. The ways out are to
*generate* the VBScript dispatcher from the same operation registry the bridge
reads, so there is one source and two emissions; or to accept that the batch win
is ~2× and spend the effort elsewhere. That decision is recorded in THE QUEUE
with the numbers rather than taken quietly here.

So: the script is generated, its shape is asserted, and **no emitted script has
ever been executed** — it could not be, since the subroutine it calls is
undefined. A batch path that has never run is one nobody should trust yet, and
saying so is what stops somebody building a release on it.

**Nothing here changes what gets built.** A plan run in batch and a plan run
interactively issue the same calls in the same order with the same arguments;
what differs is how much each one reports back. Any divergence beyond that would
break the determinism property (I5) that says the same spec compiles to the same
geometry — so `build` returns the same `BuildReport` type, and the report's
`plan_digest` is the same either way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.design.compile import Plan, PlannedCall, compile_spec
from app.design.execute import BuildReport, CallRunner, execute_plan
from app.design.spec import DesignSpec

#: What `build` asks the caller for: the batch decision in, the runner out. A
#: factory rather than a runner, because the decision is what a caller needs in
#: order to construct the runner at the right detail — making it outside would
#: put the threshold in two places.
RunnerFactory = Callable[["BatchPlan"], CallRunner]

#: Above this many calls a plan is treated as a batch by default: detail is
#: lowered, because nobody is reading the per-call post-state of a five-hundred
#: call replay. Below it the interactive default stands — the agent *is* reading
#: it, and a batch threshold that swallowed a twelve-call part would take away
#: the feedback the correction loop runs on.
BATCH_THRESHOLD: Final = 50


@dataclass(frozen=True)
class BatchPlan:
    """A plan and how it should be executed.

    `reason` exists so a log line can say *why* the detail was lowered. "Detail:
    MINIMAL" with nothing beside it is the kind of setting somebody later
    changes back because they cannot tell what it was for.
    """

    plan: Plan
    batched: bool
    reason: str

    @property
    def calls(self) -> int:
        return len(self.plan.calls)


def plan_for(spec: DesignSpec, *, threshold: int = BATCH_THRESHOLD) -> BatchPlan:
    """Compile `spec` and decide whether it is a batch."""
    plan = compile_spec(spec)
    count = len(plan.calls)
    if count > threshold:
        return BatchPlan(
            plan=plan,
            batched=True,
            reason=(
                f"{count} calls, over the batch threshold of {threshold}: post-state "
                "measurement is turned down, because measuring integrates over the "
                "whole shape and at this size that is the run rather than a detail of it"
            ),
        )
    return BatchPlan(
        plan=plan,
        batched=False,
        reason=(
            f"{count} calls: run interactively, so every call reports its post-state — "
            "the correction loop cannot react to a number it was not given"
        ),
    )


def build(
    spec: DesignSpec,
    runner_for: RunnerFactory,
    *,
    threshold: int = BATCH_THRESHOLD,
) -> tuple[BuildReport, BatchPlan]:
    """Compile and run one spec in one session, batching when it is worth it.

    `runner_for` is handed the `BatchPlan` and returns the `CallRunner` to use.
    Injected for the reason `app/design/execute.py` gives: everything in this
    package stays pure, so a plan can be compiled, batched and checked with no
    kernel anywhere near it, and every test for all of it runs offline.
    """
    batch = plan_for(spec, threshold=threshold)
    return execute_plan(batch.plan, runner_for(batch)), batch


# -- the CATIA half ---------------------------------------------------------


#: The header every emitted script carries. `Option Explicit` is not decoration:
#: CATScript is a VBScript dialect and an undeclared variable is a silent empty
#: string, which in a geometry script is a pad of length zero rather than an
#: error. One line here removes a whole class of failure that would otherwise be
#: discovered as a wrong part.
_SCRIPT_HEADER: Final = (
    "' Generated by Kryova from a compiled DesignSpec. Do not edit by hand:\n"
    "' the design is the spec, and an edit here is lost on the next build.\n"
    "Option Explicit\n"
)


def as_catscript(plan: Plan, *, dispatcher: str = "KryovaDispatch") -> str:
    """Emit a compiled plan as one CATScript.

    **One script, one execution.** The alternative this replaces is a COM round
    trip per call, and a machine is not a dozen calls — the punch press measured
    on 2026-09-07 passed step 34 with the assembly only starting. At 10⁵
    operations the round trips *are* the runtime.

    The script calls one dispatcher subroutine per operation rather than
    inlining CATIA's API: the bridge already owns the mapping from an operation
    name to the COM calls that perform it (`app/catia/`), and re-deriving that
    here would be a second implementation of the whole tool table, drifting from
    the first the day an operation gained an argument.

    **Arguments are emitted as literals, escaped.** A plan's arguments are
    already resolved — expressions evaluated, references bound — so there is
    nothing to compute at run time, and a script that recomputed them would be a
    second compiler with its own opinion about `wall_mm * 2`.

    **No output of this function has ever been executed, and it could not be**:
    every line calls `KryovaDispatch`, which exists in no file in this repository
    and on no seat. The `dispatcher` argument names it, so a caller who writes one
    can point at it. Measured 2026-09-18, one script is worth about 2x the same
    work over COM — see the module docstring for both numbers.
    """
    lines = [_SCRIPT_HEADER, "Sub CATMain()\n", "    Dim result\n"]
    for call in plan.calls:
        lines.append(_script_call(call, dispatcher))
    lines.append("End Sub\n")
    return "".join(lines)


def _script_call(call: PlannedCall, dispatcher: str) -> str:
    arguments = ", ".join(
        f"{_vb_literal(key)}, {_vb_literal(value)}" for key, value in sorted(call.arguments.items())
    )
    comment = f"    ' [{call.index}] {call.feature or call.tool}\n"
    return f"{comment}    result = {dispatcher}({_vb_literal(call.tool)}{', ' if arguments else ''}{arguments})\n"


def _vb_literal(value: Any) -> str:
    """One argument as a VBScript literal.

    **Strings are escaped by doubling the quote**, which is VBScript's only
    escape. Getting this wrong does not produce a syntax error in the useful
    case — it produces a *different string*, so a feature named `1" plate` would
    silently become two arguments. `True`/`False` come before the numeric branch
    because `bool` is a subclass of `int` in Python and would otherwise emit
    `1`, which VBScript does treat as true but which reads as a dimension in a
    generated script somebody has to debug.
    """
    if value is None:
        return "Empty"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int | float):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        # VBScript has no array literal in an expression, so a list is emitted
        # as a delimited string the dispatcher splits. The delimiter is a
        # control character rather than a comma for the obvious reason: a
        # feature name may contain a comma and must not be split on one.
        joined = "\x1f".join(str(item) for item in value)
        return _vb_literal(joined)
    if isinstance(value, Mapping):
        joined = "\x1f".join(f"{key}\x1e{value[key]}" for key in sorted(value))
        return _vb_literal(joined)
    text = str(value).replace('"', '""')
    return f'"{text}"'


def script_lines(plan: Plan) -> Iterable[str]:
    """The emitted script, line by line. For assertions and for a diff."""
    return as_catscript(plan).splitlines()


__all__ = [
    "BATCH_THRESHOLD",
    "BatchPlan",
    "as_catscript",
    "build",
    "plan_for",
    "script_lines",
]
