"""Continue, as a typed action rather than a sentence somebody has to type.

A turn that ran out of tool rounds used to leave the user to type something, and what
people type there is "go on". The model reads that as a new request and sometimes
answers it by starting the design again. Three kinds of stop now carry a `NextAction`
(ROAD_TO_10 2.2, 2.4 and 3.6), the UI shows one **Continue** button, and pressing it
sends `continuation: "continue"` with no text at all. The server then writes the
instruction itself, naming the first open task of the plan the model declared, and
stores it marked as a continuation -- so the transcript records an act, not prose in
the user's voice.

**Which stops are continuable, and which are deliberately not.**

* `step_budget` -- out of rounds. Always.
* `repeated_calls` -- kept repeating a refused call. The continuation says so and tells
  the model not to send that call again; a bare "go on" would walk into the same wall.
* `task_boundary` -- ended on purpose between two tasks because the rest of the plan
  would not fit (2.4). The point of ending there is that Continue is the next step.
* `provider_busy` -- the model service stayed busy past the transport's retries (3.6).
* **`needs_input` and `awaiting_approval` are not.** Both are waiting on a person who has
  been asked a specific question, and the typed `Intervention` already is their
  one-click action. A bare Continue would answer the question by ignoring it, and for an
  approval gate it would walk past the checkpoint, which is the one thing a checkpoint
  exists to prevent. (ROAD_TO_10 listed `needs_input`; this is the reason it is not.)
* `cancelled` -- the user said stop. Offering to resume what they just stopped would be
  the product second-guessing them.

**The server decides, twice.** The client sends no reason and no text. Whether a
conversation can be continued is read from the stored turn record, never from what the
client says, so a pressed button, a reload and a hand-written request all see one answer
(`pending`). Only the newest turn can be continued, and only while nothing has been said
since: a Continue under an answer that was already continued would resume work that is
already running.

Pure except for `pending`, which is one indexed read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import prompts
from app.ai.sanitise import sanitise_untrusted
from app.ai.taskgraph import PlanError, Task, TaskGraph
from app.ai.turn_metrics import (
    STOP_PROVIDER_BUSY,
    STOP_REPEATED_CALLS,
    STOP_STEP_BUDGET,
    STOP_TASK_BOUNDARY,
)
from app.models import Conversation, MessageRole, TurnMetric

#: The stops a person can continue with one press. See the module docstring for why the
#: two that wait on a decision, and the one the user asked for, are not here.
CONTINUABLE: Final = frozenset(
    {STOP_STEP_BUDGET, STOP_REPEATED_CALLS, STOP_TASK_BOUNDARY, STOP_PROVIDER_BUSY}
)

#: What a task is assumed to cost, in loop rounds, when none has closed this turn yet and
#: as the floor under the measured average. Starting a task with fewer rounds left than
#: this ends the turn mid-task, which is what stopping at a boundary exists to avoid.
MIN_ROUNDS_PER_TASK: Final = 3

#: Characters of a task title carried into the instruction the model reads.
TITLE_CHARS: Final = 120

LABEL: Final = "Continue"

_WHY: Final = {
    STOP_STEP_BUDGET: "it used all of its tool rounds, not because the work was finished",
    STOP_REPEATED_CALLS: "it kept repeating a call that had already been refused",
    STOP_TASK_BOUNDARY: (
        "it ended at the end of a task on purpose, because the rest of the plan would not "
        "fit in one turn's tool rounds"
    ),
    STOP_PROVIDER_BUSY: (
        "the model service was too busy to answer after several tries. Nothing that ran "
        "was lost"
    ),
}


@dataclass(frozen=True, slots=True)
class OpenTask:
    id: str
    title: str
    state: str


@dataclass(frozen=True, slots=True)
class NextAction:
    """What one press can do next, as data for the client."""

    reason: str
    detail: str
    open_tasks: tuple[OpenTask, ...] = ()
    kind: str = "continue"
    label: str = LABEL

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "label": self.label,
            "detail": self.detail,
            "open_tasks": [
                {"id": task.id, "title": task.title, "state": task.state}
                for task in self.open_tasks
            ],
        }


def graph_of(conversation: Conversation) -> TaskGraph:
    """The conversation's plan, or an empty one when none was declared or it is unreadable.

    A plan this build cannot read must not make Continue unavailable: the button's job is
    to get a stopped turn moving, and it works without a plan.
    """
    try:
        return TaskGraph.from_dict(conversation.task_graph)
    except PlanError:
        return TaskGraph()


def _title(task: Task) -> str:
    return sanitise_untrusted(" ".join(task.title.split()), max_chars=TITLE_CHARS)


def open_tasks(graph: TaskGraph) -> tuple[OpenTask, ...]:
    """Every task not yet settled, in plan order. A blocked one is listed, and is not ready."""
    return tuple(
        OpenTask(id=task.id, title=_title(task), state=task.state.value)
        for task in graph.order()
        if not task.state.settled
    )


def _next_task(graph: TaskGraph) -> Task | None:
    ready = graph.ready()
    return ready[0] if ready else None


def plan_progress(graph: TaskGraph) -> dict[str, Any] | None:
    """The plan as a returning user reads it (2.3): how far it got, what is left, what is next.

    `None` for a conversation that never declared one, which is most of them. Built from the
    stored graph and nothing else, so a "welcome back" speaks from what the server recorded
    and not from the summary's paraphrase of it.
    """
    if not graph.tasks:
        return None
    following = _next_task(graph)
    return {
        "total": len(graph.tasks),
        "settled": graph.settled_count(),
        "open": [{"id": t.id, "title": t.title, "state": t.state} for t in open_tasks(graph)],
        "next": {"id": following.id, "title": _title(following)} if following else None,
    }


def for_stop(stop_reason: str, graph: TaskGraph) -> NextAction | None:
    """The action a turn that stopped for `stop_reason` offers, or None.

    The one decision point, shared by the `done` event of a live turn and by the stored
    record a reload reads, so the two cannot offer different things.
    """
    if stop_reason not in CONTINUABLE:
        return None
    following = _next_task(graph)
    if stop_reason == STOP_TASK_BOUNDARY and following is not None:
        detail = f"Starts the next task: {following.id}: {_title(following)}."
    elif stop_reason == STOP_REPEATED_CALLS:
        detail = (
            "Carries on from what is already built, and tells the agent not to send the "
            "refused call again."
        )
    elif stop_reason == STOP_PROVIDER_BUSY:
        detail = "Tries again from where it stopped. Everything that ran is kept."
    else:
        detail = "Carries on from what is already built, with a fresh set of tool rounds."
        if following is not None:
            detail += f" Next in the plan: {following.id}: {_title(following)}."
    return NextAction(reason=stop_reason, detail=detail, open_tasks=open_tasks(graph))


def pending(db: Session, conversation: Conversation) -> NextAction | None:
    """What Continue would do for this conversation right now, or None.

    Read from the stored turn record: the newest turn's stop reason, and nothing said
    since. "Nothing said since" is the last stored message being the assistant's -- a
    turn that opens with the user's message stores it before it does anything, so a turn
    in flight, or one that failed after being asked, is not continuable.
    """
    if not conversation.id or not conversation.messages:
        return None
    if conversation.messages[-1].role is not MessageRole.ASSISTANT:
        return None
    newest = db.execute(
        select(TurnMetric.stop_reason, TurnMetric.created_at)
        .where(TurnMetric.conversation_id == conversation.id)
        .order_by(TurnMetric.created_at.desc(), TurnMetric.id.desc())
        .limit(1)
    ).first()
    if newest is None:
        return None
    stop_reason, recorded_at = newest
    # A turn recorded before the newest messages were rewound belongs to an answer that was
    # deleted; the answer now last in the transcript is an earlier turn's, which finished.
    if conversation.rewound_at is not None and recorded_at <= conversation.rewound_at:
        return None
    return for_stop(stop_reason, graph_of(conversation))


def message_for(action: NextAction, conversation: Conversation) -> str:
    """The instruction the model reads when Continue is pressed.

    Written here and never by the client, and carrying `prompts.CONTINUATION_NOTE` so the
    transcript can tell it from the engineer's own words (`is_continuation`) and the
    window and tool selection, which anchor on what the engineer said, pass over it.
    """
    graph = graph_of(conversation)
    following = _next_task(graph)
    parts = [
        f"{prompts.CONTINUATION_NOTE}The user pressed Continue and typed nothing. "
        f"Your previous turn stopped because {_WHY[action.reason]}."
    ]
    if following is not None:
        remaining = len(action.open_tasks)
        parts.append(
            f"Carry on with {following.id}: {_title(following)}. "
            f"{remaining} task(s) of the plan are still open."
        )
    else:
        parts.append(
            "Carry on from what is already built. Read the current design and its "
            "features first, and do not redo work that is already recorded."
        )
    if action.reason == STOP_REPEATED_CALLS:
        parts.append(
            "Do not send the call that was refused again: take a different approach, or "
            "say what is blocking you."
        )
    return " ".join(parts)


def is_continuation(content: str | None) -> bool:
    """Whether a stored user message is the server's Continue instruction."""
    return bool(content) and str(content).startswith(prompts.CONTINUATION_NOTE)


def should_pause_at_boundary(
    graph: TaskGraph, *, rounds_used: int, tasks_closed: int, rounds_left: int
) -> bool:
    """Whether to end the turn between tasks instead of starting one that will not fit (2.4).

    Asked only at the moment a task has just been settled. The next task is assumed to
    cost what the ones closed this turn cost on average, never less than
    `MIN_ROUNDS_PER_TASK`; when fewer rounds than that are left, ending here, with a
    progress report and Continue, beats running out in the middle of a task. A plan with
    nothing ready has nothing to start, so there is nothing to pause for.
    """
    if _next_task(graph) is None:
        return False
    per_task = max(MIN_ROUNDS_PER_TASK, -(-rounds_used // tasks_closed) if tasks_closed else 0)
    return rounds_left < per_task
