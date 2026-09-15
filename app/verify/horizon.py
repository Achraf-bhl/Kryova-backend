"""Kryova's own agent, measured per duration bucket (master plan E22 task 3).

The literature E22.3 rests on says reliability decays with task length, and that
the decay is domain-stratified rather than uniform. The deliverable is therefore
not a restatement of that: it is *our* number, on *our* traces, per bucket, so
model selection is driven by a measurement of this product rather than by a
leaderboard. This module is the harness that produces it.

**What a trace is read from, and why it is not what the task said.** The task
text names "`CatiaOperation` rows and turn events". `TurnEvent` turns out to be
the wrong source and the model says so in its own docstring: it is a ten-minute
resume buffer, pruned aggressively, holding a second copy of a conversation
whose only distinctive content is timing. A study run over it would silently
measure *the last ten minutes of traffic* and report it as the fleet's history.
The durable pair is `ConversationMessage` — written as each step completes, and
what the product rehydrates from — and `CatiaOperation`, which `app/ai/resume.py`
already establishes as the record of what was actually done. So that is what is
read here.

**The unit is a turn, and its duration is wall-clock.** A turn runs from a user
message to the last message before the next one. That is the span the user
waits through, it is what the cited duration buckets are about, and it is
recoverable exactly from `created_at` without a new column. A *conversation* is
the wrong unit: it can span days of somebody's week with the product idle.

**What "reliability" means here, exactly — and what it does not.**
`completion_rate` is the fraction of turns in a bucket that ended with an
assistant answer and none of the three failure signals this codebase already
records: the step budget ran out (`truncated`), a tool error was the last thing
that happened on its tool, or the turn ended in an escalation. It is computed
from what the transcript already holds and needs nobody to label anything.

**It is not a success rate, and it must never be published as one.** Whether the
part the agent built was *right* is not in these rows — a turn that confidently
builds the wrong bracket completes perfectly. The cited graceful-degradation
figures are success against a known-good answer, so the comparable Kryova number
needs a labelled set, which is what E18's missions are. `measure` therefore takes
an optional `outcomes` map and reports `success_rate` only for the turns it
covers, `None` for the rest. A bucket with no labels reports `None` rather than
borrowing `completion_rate`, because the two answer different questions and the
whole point of the phase is not to confuse them.

**Every literature figure is traced or written as unsourced.** `LITERATURE`
below carries the figures E22.3 quotes. None of them is attributed here, because
no one in this repository has opened the papers: the plan's prose is the only
record, and a plausible citation attached to a remembered number is exactly what
`app/verify/nafems.py` refuses for benchmark targets. They are `UNSOURCED` with
the reason, and the comparison they exist for is refused until they are read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CatiaOperation, ConversationMessage, MessageRole


class Basis(StrEnum):
    """Where a figure came from. The same distinction `nafems.TargetBasis` makes."""

    #: Read from the document, with a URL and the date it was read.
    READ = "read"
    #: Quoted in this repository's own planning prose and nowhere else. Carries
    #: no value that may be compared against, and says so.
    UNSOURCED = "unsourced"
    #: Measured here, from our own traces.
    MEASURED = "measured"


@dataclass(frozen=True, slots=True)
class Figure:
    """One number from the horizon literature, with its provenance or without it.

    A `READ` figure must name where it was read and when. An `UNSOURCED` one
    must not carry a value at all: a number with no source, sitting in a field
    called `value`, is indistinguishable downstream from one somebody checked —
    which is the failure `nafems.Target` refuses for benchmark targets and the
    same refusal belongs here.
    """

    id: str
    claim: str
    basis: Basis
    value: float | None = None
    url: str | None = None
    read_on: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if self.basis is Basis.READ and not (self.url and self.read_on):
            raise ValueError(
                f"Figure {self.id!r} claims to have been read and names no URL and date. "
                "A citation nobody can follow is the form of evidence without the substance."
            )
        if self.basis is Basis.UNSOURCED and self.value is not None:
            raise ValueError(
                f"Figure {self.id!r} is unsourced and carries a value. An unsourced number "
                "must not be comparable: give it a source or leave the value out."
            )
        if self.basis is Basis.UNSOURCED and not self.note:
            raise ValueError(f"Figure {self.id!r} is unsourced and does not say why.")


#: The figures E22.3 quotes. Every one is `UNSOURCED`: they are quoted in the
#: master plan's own prose, and nobody in this repository has opened the papers
#: they came from. Reading them is the other half of this task and is a Windows
#: or a network job, not a Linux one — see THE QUEUE.
#:
#: Do not "fill these in" from memory. That is the precise move
#: `app/verify/nafems.py` exists to prevent, and it was wrong by 4% the one time
#: it was tried there.
LITERATURE: Final[tuple[Figure, ...]] = (
    Figure(
        id="graceful-degradation-code-domain",
        claim=(
            "Reliability decay is domain-stratified: code and tool-driving domains fall "
            "from 0.90 to 0.44 on a graceful degradation score across duration buckets, "
            "while document processing stays flat."
        ),
        basis=Basis.UNSOURCED,
        note=(
            "Quoted in KRYOVA_MASTER_PLAN.md E22 task 3. The study is not named there and "
            "has not been read here, so neither the bucket boundaries nor the definition of "
            "the score is known — and without the bucket boundaries the figure cannot be "
            "compared against anything this harness computes."
        ),
    ),
    Figure(
        id="failure-attribution-split",
        claim=(
            "A 3,100-trajectory / 700-task attribution study splits agent failures "
            "72.5% process-level against 27.5% design-level, over seven categories."
        ),
        basis=Basis.UNSOURCED,
        note="Quoted in the master plan; the study is unnamed there and unread here.",
    ),
    Figure(
        id="catastrophic-failure-rate",
        claim="The strongest models show the highest catastrophic-failure rates, up to 19%.",
        basis=Basis.UNSOURCED,
        note="Quoted in the master plan; unnamed there and unread here.",
    ),
    Figure(
        id="memory-scaffolds-degrade",
        claim=(
            "Memory scaffolds degraded long-horizon performance in all ten models tested, "
            "and added orchestration does not reliably help."
        ),
        basis=Basis.UNSOURCED,
        note=(
            "Quoted in the master plan, and load-bearing: E16.2 cites it as the reason "
            "`app/ai/taskgraph.py` enforces a declared plan rather than generating one, and "
            "E16 task 3's hierarchical memory is held open on it. A claim this consequential "
            "should be read before it is relied on again."
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class Bucket:
    """One duration band, half-open: `low <= duration < high`.

    `high` is `None` for the last band. Half-open so a turn lands in exactly one
    bucket and the boundaries are not quietly double-counted, which is the
    arithmetic that makes two people's "the same" histogram disagree.
    """

    label: str
    low_s: float
    high_s: float | None = None

    def holds(self, seconds: float) -> bool:
        if seconds < self.low_s:
            return False
        return self.high_s is None or seconds < self.high_s


#: The bands this harness reports in. Chosen against *this product's* clock
#: rather than a paper's: a turn on the workstation's local model is four to
#: seven minutes (CLAUDE.md, *Performance*), so bands finer than a minute would
#: put almost every turn in one bucket, and bands wider than an hour would put
#: every mission in another.
#:
#: They are **not** the cited study's buckets, whose boundaries are unknown
#: because the study has not been read. Nothing here may be plotted against
#: those figures until they are.
DEFAULT_BUCKETS: Final[tuple[Bucket, ...]] = (
    Bucket("under 1 min", 0.0, 60.0),
    Bucket("1-5 min", 60.0, 300.0),
    Bucket("5-15 min", 300.0, 900.0),
    Bucket("15-60 min", 900.0, 3_600.0),
    Bucket("over 1 hour", 3_600.0, None),
)


@dataclass(frozen=True, slots=True)
class TurnTrace:
    """One turn, as the durable record holds it.

    Carries no message text. A reliability study needs timings and outcomes, and
    a harness that also carried the prose would be a second copy of every
    conversation in whatever file it wrote — including the quoted contents of
    everyone's attachments.
    """

    conversation_id: str
    #: The user message that opened the turn.
    sequence: int
    started_at: datetime
    ended_at: datetime
    steps: int
    answered: bool
    truncated: bool
    failed_tools: tuple[str, ...] = ()
    operations: int = 0
    failed_operations: int = 0

    @property
    def duration_s(self) -> float:
        return max(0.0, (self.ended_at - self.started_at).total_seconds())

    @property
    def completed(self) -> bool:
        """Did this turn end in an answer, with no failure signal left standing?

        The three signals are the ones the product already records, and each is
        a *recorded* fact rather than a judgement: an answer exists, the step
        budget did not run out, and no tool's last word this turn was an error.
        """
        return self.answered and not self.truncated and not self.failed_tools


@dataclass(frozen=True, slots=True)
class BucketReliability:
    """What one bucket says, with every rate `None` when its denominator is empty.

    A rate over zero turns is not 0.0 and not 1.0; it is nothing, and returning
    a float would put a fabricated point on a chart. The same rule as
    `app/ai/argument_accuracy.py`.
    """

    bucket: Bucket
    turns: int
    completed: int
    labelled: int = 0
    succeeded: int = 0

    @property
    def completion_rate(self) -> float | None:
        return self.completed / self.turns if self.turns else None

    @property
    def success_rate(self) -> float | None:
        """Only over turns somebody labelled. Never inferred from completion."""
        return self.succeeded / self.labelled if self.labelled else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket.label,
            "low_s": self.bucket.low_s,
            "high_s": self.bucket.high_s,
            "turns": self.turns,
            "completed": self.completed,
            "completion_rate": self.completion_rate,
            "labelled": self.labelled,
            "succeeded": self.succeeded,
            "success_rate": self.success_rate,
        }


@dataclass(frozen=True, slots=True)
class Report:
    """The whole measurement, with the caveat carried rather than assumed."""

    buckets: tuple[BucketReliability, ...]
    traces: int
    #: Turns that carried a ground-truth label, across every bucket.
    labelled: int
    statement: str = field(default="")

    def to_dict(self) -> dict[str, Any]:
        return {
            "traces": self.traces,
            "labelled": self.labelled,
            "statement": self.statement or NOT_A_SUCCESS_RATE,
            "buckets": [one.to_dict() for one in self.buckets],
            "literature": [
                {
                    "id": figure.id,
                    "claim": figure.claim,
                    "basis": str(figure.basis),
                    "value": figure.value,
                    "url": figure.url,
                    "read_on": figure.read_on,
                    "note": figure.note,
                }
                for figure in LITERATURE
            ],
        }


#: Printed with every report, and the reason `success_rate` is a separate
#: column. One string, in one place, for `standards.NOT_VALIDATED`'s reason: a
#: statement with two wordings has two standards.
NOT_A_SUCCESS_RATE: Final = (
    "Completion rate is not success rate. It says a turn ended with an answer and no "
    "recorded failure signal; it says nothing about whether the answer was right, and a "
    "turn that confidently builds the wrong part completes perfectly. Success is reported "
    "only for turns carrying a ground-truth label, and is null where none was supplied."
)


def read_traces(
    db: Session,
    *,
    conversation_ids: Sequence[str] | None = None,
    since: datetime | None = None,
) -> list[TurnTrace]:
    """Every turn in the durable record, as traces.

    Reads `ConversationMessage` for the turn boundaries and outcomes and
    `CatiaOperation` for what was actually built. Deliberately not `TurnEvent`:
    it is pruned, so a study over it measures the last ten minutes of traffic.
    """
    query = select(ConversationMessage).order_by(
        ConversationMessage.conversation_id, ConversationMessage.sequence
    )
    if conversation_ids:
        query = query.where(ConversationMessage.conversation_id.in_(list(conversation_ids)))
    if since is not None:
        query = query.where(ConversationMessage.created_at >= since)

    by_conversation: dict[str, list[ConversationMessage]] = {}
    for message in db.scalars(query):
        by_conversation.setdefault(message.conversation_id, []).append(message)

    operations = _operations_by_conversation(db, list(by_conversation))
    traces: list[TurnTrace] = []
    for conversation_id, messages in by_conversation.items():
        traces.extend(
            _traces_for(conversation_id, messages, operations.get(conversation_id, []))
        )
    return traces


def _operations_by_conversation(
    db: Session, conversation_ids: Sequence[str]
) -> dict[str, list[CatiaOperation]]:
    if not conversation_ids:
        return {}
    rows = db.scalars(
        select(CatiaOperation)
        .where(CatiaOperation.conversation_id.in_(list(conversation_ids)))
        .order_by(CatiaOperation.created_at)
    )
    grouped: dict[str, list[CatiaOperation]] = {}
    for row in rows:
        if row.conversation_id is not None:
            grouped.setdefault(row.conversation_id, []).append(row)
    return grouped


def _traces_for(
    conversation_id: str,
    messages: Sequence[ConversationMessage],
    operations: Sequence[CatiaOperation],
) -> list[TurnTrace]:
    """Split one conversation's messages into turns, each opening on a user message.

    The loop's own control messages are user-role too (a provider accepts no
    other role for them), so a naive split would start a new turn in the middle
    of one. They are told apart the way `app/ai/context.py` tells them apart:
    by `prompts.CONTROL_NOTE`.
    """
    from app.ai import prompts

    traces: list[TurnTrace] = []
    current: list[ConversationMessage] = []
    for message in messages:
        opens = message.role is MessageRole.USER and not str(message.content or "").startswith(
            prompts.CONTROL_NOTE
        )
        if opens and current:
            traces.append(_trace(conversation_id, current, operations))
            current = []
        if opens or current:
            current.append(message)
    if current:
        traces.append(_trace(conversation_id, current, operations))
    return traces


def _trace(
    conversation_id: str,
    messages: Sequence[ConversationMessage],
    operations: Sequence[CatiaOperation],
) -> TurnTrace:
    opening = messages[0]
    closing = messages[-1]
    answered = any(
        message.role is MessageRole.ASSISTANT and (message.content or "").strip()
        for message in messages
    )
    steps = sum(1 for message in messages if message.role is MessageRole.TOOL)

    # A tool whose *last* word this turn was an error. Keyed on the tool alone,
    # for `app/ai/resume.py`'s reason: a later success of the same tool clears
    # it, or every superseded retry would count for ever.
    last_by_tool: dict[str, bool] = {}
    for message in messages:
        if message.role is MessageRole.TOOL and message.tool_name:
            last_by_tool[message.tool_name] = bool(message.is_error)

    within = [
        row
        for row in operations
        if opening.created_at <= row.created_at <= closing.created_at
    ]
    return TurnTrace(
        conversation_id=conversation_id,
        sequence=opening.sequence,
        started_at=opening.created_at,
        ended_at=closing.created_at,
        steps=steps,
        answered=answered,
        truncated=_was_truncated(messages),
        failed_tools=tuple(sorted(tool for tool, failed in last_by_tool.items() if failed)),
        operations=len(within),
        failed_operations=sum(1 for row in within if not row.ok),
    )


def _was_truncated(messages: Sequence[ConversationMessage]) -> bool:
    """Did the step budget run out?

    Read from the transcript rather than stored: `AgentReply.truncated` is
    returned to the caller and never written down, so the only durable signal is
    the closing summary the loop asks for when it runs out of steps.
    """
    from app.ai import prompts

    marker = prompts.AGENT_OUT_OF_STEPS.strip()[:60]
    return any(marker and marker in str(message.content or "") for message in messages)


def measure(
    traces: Iterable[TurnTrace],
    *,
    buckets: Sequence[Bucket] = DEFAULT_BUCKETS,
    outcomes: Mapping[tuple[str, int], bool] | None = None,
) -> Report:
    """Group traces into buckets and report each one's rates.

    `outcomes` maps `(conversation_id, sequence)` to whether the turn actually
    achieved what was asked. Supplying it is how a *success* rate comes to
    exist; without it every bucket's `success_rate` is `None`, which is the
    honest answer and not a zero.
    """
    labels = outcomes or {}
    counted = [[0, 0, 0, 0] for _ in buckets]
    total = 0
    for trace in traces:
        for index, bucket in enumerate(buckets):
            if bucket.holds(trace.duration_s):
                counted[index][0] += 1
                counted[index][1] += int(trace.completed)
                key = (trace.conversation_id, trace.sequence)
                if key in labels:
                    counted[index][2] += 1
                    counted[index][3] += int(labels[key])
                total += 1
                break

    return Report(
        buckets=tuple(
            BucketReliability(
                bucket=bucket,
                turns=numbers[0],
                completed=numbers[1],
                labelled=numbers[2],
                succeeded=numbers[3],
            )
            for bucket, numbers in zip(buckets, counted, strict=True)
        ),
        traces=total,
        labelled=sum(numbers[2] for numbers in counted),
        statement=NOT_A_SUCCESS_RATE,
    )


__all__ = [
    "Basis",
    "Bucket",
    "BucketReliability",
    "DEFAULT_BUCKETS",
    "Figure",
    "LITERATURE",
    "NOT_A_SUCCESS_RATE",
    "Report",
    "TurnTrace",
    "measure",
    "read_traces",
]
