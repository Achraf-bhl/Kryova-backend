"""Recording and replaying a streaming turn, so a dropped stream can be resumed.

P5 task 1's other missing half. Step events have streamed over SSE since the
chat shipped, but a stream that dropped mid-turn lost the live view until the
turn finished: nothing was lost from the *record* — every step is persisted as
it happens — but a reader had no way to catch up on a turn still running, which
is exactly the turn they care about.

**The cursor is the whole design.** Every event written gets a sequence number
that is monotonic within the conversation, and it goes on the wire as the SSE
`id:` field as well as inside the payload. A client that loses the connection
comes back with the last number it saw and is handed everything after it. No
guessing, no overlap, and — because the payload is stored exactly as it was
sent — a replayed event and a live one are byte-identical, so the client has one
code path rather than two.

**Three things this deliberately is not.**

It is not the transcript. `ConversationMessage` is, and it is what
`GET /ai/conversations/{id}` rehydrates from. These rows are a resume buffer for
a turn in flight; a reader who comes back tomorrow is served by the transcript,
which is complete and permanent. That is why the retention here is minutes.

It is not a pub/sub. The follower polls, at a rate that is a fraction of the
gap between agent steps. A real broker would be a fifth interface, a deployment
dependency, and a second place for an event to be lost — for a feature whose
whole job is to survive a connection that was already lost once.

It is not durable across a prune. `prune` deletes on age, so a client that was
away longer than `RETENTION_MINUTES` is told so and falls back to reloading the
conversation. Saying "you were away too long, here is the record" is honest;
silently handing back a partial turn as though it were the whole one is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.conversation import TurnEvent

logger = logging.getLogger(__name__)

#: How long a turn's events stay resumable. Ten minutes is comfortably longer
#: than any turn observed so far (the longest Level-4 run was forty steps over
#: roughly four minutes) and short enough that the table never becomes a second
#: copy of the conversation history.
RETENTION_MINUTES = 10

#: How long a follower waits between polls. A fraction of the gap between agent
#: steps, which is seconds: fast enough that a resumed stream feels live, slow
#: enough that a hundred reconnected readers are not a hundred queries a second.
FOLLOW_INTERVAL_S = 0.4

#: How long a follower will wait with nothing arriving before it gives up. A
#: turn that has produced no event for two minutes has either finished without
#: writing a terminal event — which is a bug — or is wedged. Either way the
#: reader is better served by being told than by a connection that never closes.
FOLLOW_IDLE_TIMEOUT_S = 120.0

#: Event types after which there is nothing more to follow. `done` and `error`
#: are how every path out of `stream_agent` ends.
TERMINAL_TYPES = frozenset({"done", "error"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Replay:
    """What a reconnecting client is handed.

    `gap` is the honest half. When the requested cursor is older than anything
    still stored, the events that would have filled the hole are gone, and the
    client is told rather than handed a turn with a silent bite out of the
    middle — which would render as an agent that skipped three steps.
    """

    events: list[dict[str, Any]]
    gap: bool


def record(
    db: Session, conversation_id: str, turn_id: str, payload: dict[str, Any]
) -> int:
    """Store one event and return its sequence number.

    The sequence is taken from the conversation's own maximum rather than from a
    counter held by the streaming request, so a second turn started after a
    crash cannot reuse numbers the first one already handed to a client.
    """
    current = (
        db.scalar(
            select(func.max(TurnEvent.sequence)).where(
                TurnEvent.conversation_id == conversation_id
            )
        )
        or 0
    )
    sequence = current + 1
    db.add(
        TurnEvent(
            conversation_id=conversation_id,
            sequence=sequence,
            turn_id=turn_id,
            payload=payload,
        )
    )
    db.flush()
    return sequence


def read_after(db: Session, conversation_id: str, after: int, limit: int = 500) -> Replay:
    """Everything stored for this conversation after `after`, oldest first.

    `after=0` means "from the beginning of what is still kept", which is what a
    client with no cursor asks for.
    """
    rows = list(
        db.scalars(
            select(TurnEvent)
            .where(TurnEvent.conversation_id == conversation_id, TurnEvent.sequence > after)
            .order_by(TurnEvent.sequence)
            .limit(limit)
        )
    )
    oldest = db.scalar(
        select(func.min(TurnEvent.sequence)).where(TurnEvent.conversation_id == conversation_id)
    )
    # A gap exists when the first thing still stored is *later* than the next
    # number the client expected. `after + 1` is that expectation; anything
    # above it means the events in between were pruned.
    gap = bool(after and oldest is not None and oldest > after + 1)
    return Replay(events=[_with_sequence(row) for row in rows], gap=gap)


def latest_sequence(db: Session, conversation_id: str) -> int:
    return (
        db.scalar(
            select(func.max(TurnEvent.sequence)).where(
                TurnEvent.conversation_id == conversation_id
            )
        )
        or 0
    )


def prune(db: Session, *, now: datetime | None = None) -> int:
    """Delete events past the retention window. Returns how many went.

    Called on the write path rather than from a scheduler: there is no cron in
    this deployment, and a table that only grows is a table that is fine until
    the day it is not. The scan is on `created_at`, which is indexed.
    """
    cutoff = (now or utcnow()) - timedelta(minutes=RETENTION_MINUTES)
    result = db.execute(delete(TurnEvent).where(TurnEvent.created_at < cutoff))
    # `rowcount` is on `CursorResult`, which a DELETE always returns; the static
    # type of `execute` is the wider `Result`, which does not declare it.
    return int(getattr(result, "rowcount", 0) or 0)


def is_terminal(payload: dict[str, Any]) -> bool:
    return str(payload.get("type") or "") in TERMINAL_TYPES


def _with_sequence(row: TurnEvent) -> dict[str, Any]:
    """The stored payload with its cursor attached.

    The number is added on read rather than stored inside the payload so the
    stored bytes are exactly what the live stream produced — one encoder, one
    shape, and no chance of a replayed event carrying a sequence that disagrees
    with the row it came from.
    """
    return {**row.payload, "seq": row.sequence, "turn_id": row.turn_id}
