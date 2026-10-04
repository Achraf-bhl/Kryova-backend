"""Branching a conversation, and rewinding its newest turn (ROAD_TO_10 2.5).

Two operations on the transcript that look alike and are not:

* **Branch** copies a conversation up to one of the assistant's answers into a new
  conversation, so an engineer can try a different direction without losing the one that
  was working.
* **Rewind** deletes the newest user message and everything after it, hands the text back,
  and the client sends it again (a retry) or changed (an edit).

**What neither can honestly do is roll back a CATIA document.** The document is bound to
*one* conversation (`CatiaDocument.conversation_id` is unique -- CLAUDE.md, *A conversation
acts on the document it owns*), `catia_restore` exists only on a seat and needs an approval
token, and the open kernel's undo is a replay of its build log that no route offers. So the
rules here are built around what can be told apart from outside, and say so when they refuse:

1. **A branch never shares a document and never pretends to have copied one.** It carries the
   messages, the design record as it stood at that answer and, where it is still true, the
   summary and the plan. The CATIA document is not copied -- "save the document as a new
   file" is a bridge operation nobody has run (THE QUEUE) -- and the response says so in
   words, and the state block tells the model on every turn until the branch has a document
   of its own. A branch whose transcript says "Pad.1 exists" with nothing in the state
   block to contradict it is a model that edits a part that is not there.
2. **A branch starts only at an answer** (an assistant message with no tool calls). A
   message with calls has results after it, and cutting between the two leaves a transcript
   no provider accepts.
3. **The design is read as of the branch point, by time.** Design revisions carry no link to
   a message, so the revision in force at an answer is the newest one written no later than
   it. The copy keeps that revision's own timestamp, so a branch of a branch finds the same
   design instead of the day it was copied.
4. **A summary or a plan is copied only where it is still true.** The summary covers
   messages below a boundary: copied only when every one of them was. The plan records
   where the work is *now*, not where it was at some older message: copied only when the
   branch is a whole copy of an idle conversation.
5. **A rewind refuses a turn that changed anything.** If any tool that mutates ran in the
   span (or was asked for -- a call without a result is a crash, not an innocent turn), the
   transcript can be deleted but the part, the design and the plan cannot, and the next turn
   would meet a document that disagrees with what it thinks it did. The refusal names the
   tools and offers a branch from before the message, which is safe. A sign-off raised in
   the span refuses for the same reason: a decision a person made is a record, not a draft.
6. **A rewind never reaches behind the summary,** and never runs while a turn is in flight.

Pure over the session it is handed; commits nothing (the route does).
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai import prompts, turn_events
from app.core import designs
from app.models import (
    ApprovalGate,
    Conversation,
    ConversationMessage,
    MessageRole,
    User,
)
from app.models.conversation import TurnEvent
from app.models.design import DesignRevision

if TYPE_CHECKING:
    from app.ai.tools import ToolBox

logger = logging.getLogger(__name__)

TITLE_MAX = 255
BRANCH_SUFFIX = " (branch)"


class Refused(ValueError):
    """The request cannot be done, with the reason in words. Maps to 409."""


class BadPoint(Refused):
    """The message asked for is not one a branch can start at. Maps to 422."""


@dataclass(frozen=True, slots=True)
class BranchOutcome:
    conversation: Conversation
    copied_messages: int
    from_sequence: int
    design_revision: int | None
    plan_copied: bool
    summary_kept: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RewindOutcome:
    message: str
    removed_messages: int


def is_boundary(message: ConversationMessage) -> bool:
    """Whether a branch may end at this message: an answer, with no calls waiting on results."""
    return (
        message.role is MessageRole.ASSISTANT
        and not message.tool_calls
        and bool(message.content)
    )


def _title(source: Conversation, requested: str | None) -> str:
    if requested and requested.strip():
        return requested.strip()[:TITLE_MAX]
    base = source.title.strip() or "Conversation"
    return base[: TITLE_MAX - len(BRANCH_SUFFIX)] + BRANCH_SUFFIX


def _design_as_of(
    db: Session, source: Conversation, point: ConversationMessage
) -> DesignRevision | None:
    document = designs.load(db, source)
    if document is None:
        return None
    return db.scalar(
        select(DesignRevision)
        .where(
            DesignRevision.design_id == document.id,
            DesignRevision.created_at <= point.created_at,
        )
        .order_by(DesignRevision.revision_number.desc())
        .limit(1)
    )


def branch(
    db: Session,
    source: Conversation,
    user: User,
    *,
    from_sequence: int | None = None,
    title: str | None = None,
) -> BranchOutcome:
    """Copy `source` up to one of its answers into a new conversation of `user`'s."""
    messages = list(source.messages)
    if from_sequence is None:
        boundaries = [m for m in messages if is_boundary(m)]
        if not boundaries:
            raise Refused(
                "There is nothing to branch from yet: no answer has been written in this "
                "conversation. A branch starts at one of the assistant's answers."
            )
        point = boundaries[-1]
    else:
        found = next((m for m in messages if m.sequence == from_sequence), None)
        if found is None:
            raise BadPoint(f"This conversation has no message {from_sequence}.")
        if not is_boundary(found):
            raise BadPoint(
                f"Message {from_sequence} is not one of the assistant's answers. A branch "
                "starts at an answer, because anything between a tool call and its result "
                "would leave a transcript the model cannot continue."
            )
        point = found

    notes: list[str] = []
    child = Conversation(
        owner_id=user.id,
        project_id=source.project_id,
        title=_title(source, title),
        branched_from_id=source.id,
        branched_at_sequence=point.sequence,
    )
    db.add(child)
    db.flush()

    copied = [m for m in messages if m.sequence <= point.sequence]
    for message in copied:
        db.add(
            ConversationMessage(
                conversation_id=child.id,
                sequence=message.sequence,
                role=message.role,
                content=message.content,
                tool_calls=copy.deepcopy(message.tool_calls),
                reasoning=message.reasoning,
                tool_call_id=message.tool_call_id,
                tool_name=message.tool_name,
                is_error=message.is_error,
                duration_ms=message.duration_ms,
                # The original time, so "welcome back" and the design-as-of rule above both
                # read the history's clock and not the day it was copied.
                created_at=message.created_at,
            )
        )

    # Rule 4: the summary only where everything it covers was copied.
    summary_kept = bool(
        source.summary and source.summary_through_sequence <= point.sequence + 1
    )
    if summary_kept:
        child.summary = source.summary
        child.summary_facts = source.summary_facts
        child.summary_through_sequence = source.summary_through_sequence
        notes.append("The summary of the earlier conversation was kept.")
    elif source.summary:
        notes.append(
            "The summary was not copied, because it covers messages after this point. Every "
            "message up to it was copied and the model reads those instead."
        )

    # Rule 4 again: the plan only for a whole copy of an idle conversation.
    plan_copied = bool(source.task_graph) and point.sequence == messages[-1].sequence
    if plan_copied:
        child.task_graph = copy.deepcopy(source.task_graph)
        notes.append("The plan was copied.")
    elif source.task_graph:
        notes.append(
            "The plan was not copied: it records where the work is now, not where it stood at "
            "that message. Ask for it to be rewritten if this direction needs one."
        )

    # Rule 3: the design as it stood at the answer.
    design_revision: int | None = None
    revision = _design_as_of(db, source, point)
    if revision is not None:
        try:
            spec = designs.spec_of_revision(revision)
            saved = designs.save(
                db, child, spec, author=designs.AUTHOR_USER, author_id=user.id
            )
        except Exception as exc:  # noqa: BLE001 - a design this build cannot read must not block the branch
            logger.warning(
                "Could not copy the design of conversation %s into its branch", source.id,
                exc_info=True,
            )
            notes.append(
                "The recorded design could not be copied "
                f"({type(exc).__name__}); the transcript was."
            )
        else:
            saved.revision.created_at = revision.created_at
            design_revision = revision.revision_number
            notes.append(
                f"The recorded design was copied as it stood at that answer (revision "
                f"{revision.revision_number} of the original, revision 1 here)."
            )
    elif designs.load(db, source) is not None:
        notes.append("No design had been recorded yet at that point, so none was copied.")

    # Rule 1: said plainly, every time.
    notes.append(
        "The CATIA document was not copied: a document belongs to one conversation, and this "
        "branch starts without one. Its first build creates a new part; if a design was "
        "copied, `build_design` rebuilds it in one step."
    )

    db.flush()
    return BranchOutcome(
        conversation=child,
        copied_messages=len(copied),
        from_sequence=point.sequence,
        design_revision=design_revision,
        plan_copied=plan_copied,
        summary_kept=summary_kept,
        notes=tuple(notes),
    )


def _real_user_message(messages: list[ConversationMessage]) -> ConversationMessage | None:
    """The newest user message the engineer wrote, skipping the server's own notes."""
    for message in reversed(messages):
        if message.role is MessageRole.USER and not (message.content or "").startswith(
            prompts.CONTROL_NOTE
        ):
            return message
    return None


def rewind(
    db: Session,
    conversation: Conversation,
    toolbox: ToolBox,
    *,
    now: datetime | None = None,
) -> RewindOutcome:
    """Delete the newest user message and everything after it, if that is safe (rule 5, 6).

    Returns the text so the client can send it again or edit it first.
    """
    if turn_events.in_flight(db, conversation.id):
        raise Refused(
            "A turn is still running in this conversation. Stop it, or wait for it to finish, "
            "and then retry."
        )
    messages = list(conversation.messages)
    anchor = _real_user_message(messages)
    if anchor is None or not anchor.content:
        raise Refused("There is no message of yours in this conversation to retry.")
    if anchor.sequence < conversation.summary_through_sequence:
        raise Refused(
            "That message is too far back to retry: it has been folded into the summary. "
            "Branch from an answer instead."
        )

    span = [m for m in messages if m.sequence >= anchor.sequence]
    names: set[str] = set()
    for message in span:
        if message.role is MessageRole.TOOL and message.tool_name:
            names.add(message.tool_name)
        for call in message.tool_calls or []:
            if call.get("name"):
                names.add(str(call["name"]))
    changed = sorted(name for name in names if toolbox.is_mutating(name))
    if changed:
        shown = ", ".join(changed[:5]) + (f" and {len(changed) - 5} more" if len(changed) > 5 else "")
        raise Refused(
            f"That turn changed things ({shown}). Retrying would delete the messages but not "
            "the part, the design or the plan, and the next turn would meet a document that "
            "disagrees with what it thinks it did. Branch from the answer before it instead: "
            "a branch keeps the design as it stood there."
        )
    signed = db.scalar(
        select(ApprovalGate.id)
        .where(
            ApprovalGate.conversation_id == conversation.id,
            ApprovalGate.created_at >= anchor.created_at,
        )
        .limit(1)
    )
    if signed is not None:
        raise Refused(
            "A sign-off was requested during that turn, and a decision is a record rather than "
            "a draft, so it cannot be rewound. Branch from the answer before it instead."
        )

    text = anchor.content
    db.execute(
        delete(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.sequence >= anchor.sequence,
        )
    )
    # The resume buffer would replay events of a turn that no longer exists.
    db.execute(delete(TurnEvent).where(TurnEvent.conversation_id == conversation.id))
    db.expire(conversation, ["messages"])
    conversation.rewound_at = now or datetime.now(timezone.utc)
    db.flush()
    return RewindOutcome(message=text, removed_messages=len(span))
