"""What this conversation has already done, read from the record.

A conversation that is picked up again a week later has three separate accounts
of its own past, and only one of them is a fact:

* **The window** replays the last handful of messages verbatim. Anything older
  is simply gone, and the whole point of a design session is that the
  interesting decisions are old.
* **The summary** is an LLM's prose account of what the window dropped. It is
  the best available compression and it is still a paraphrase -- it is written
  by the same kind of model that will read it, under the same pressure to be
  fluent, and nothing checks it against what happened.
* **`CatiaOperation`** is the append-only log of every call that reached a
  workstation: the tool, its arguments, whether it worked, what it said when it
  did not. It was written at the moment of the call and no model has touched it.

This module reads the third one. It exists because that log had never been read
by anything -- it was written on every call, indexed for exactly this query, and
surfaced nowhere. So the agent's answer to "where did we get to" came from
whichever of the first two accounts had survived, which is how a resumed
conversation ends up re-cutting a pocket it already cut, or quietly abandoning
the fillet that failed just before the user closed the tab.

Two shapes, for two jobs:

`resume_lines` is a handful of lines for the per-turn state block. Small on
purpose -- every line is paid for on every turn -- so it carries the things that
cannot be derived from anywhere else: how much work is behind this conversation,
how long ago it stopped, and **which attempts failed and were never made to
work**. That last one is the whole point. The feature tree shows what exists; it
cannot show what someone tried and gave up on, and a resumed session that
silently drops an unfinished intention is the failure this is written against.

`build_history` is the full account, paged, behind a tool the model calls when
it actually needs it. Keeping it out of the block is deliberate: an agent that
is told everything every turn stops being able to find anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.sanitise import sanitise_untrusted
from app.models.base import utcnow
from app.models.catia import CatiaOperation

#: How many operations `build_history` returns in one call, at most. A long
#: session is hundreds of calls and the model does not need them all at once;
#: the count in the payload says how many were left, so it can ask for more.
HISTORY_PAGE_LIMIT = 60

#: How far back the per-turn reduction reads. The state block is rebuilt on
#: every turn, so this is the one query here that must stay bounded; a
#: months-old conversation has no natural ceiling. Set well above any real
#: session so the bound is a backstop rather than a policy.
ACTIVITY_SCAN_LIMIT = 500

#: Cap on any single string lifted out of the log. Errors are CATIA's own text
#: and arguments came from the model, so both are untrusted; and a state block
#: is meant to be scanned, not read.
MAX_FIELD_CHARS = 160

#: Unresolved failures named in the state block. More than a few is not a list
#: of loose ends, it is a session that went wrong in one way repeatedly, and the
#: count beside it says so better than the enumeration would.
MAX_UNRESOLVED_IN_BLOCK = 4

#: Argument keys worth showing beside a tool name, most identifying first. A log
#: line reading `catia_pad` is nearly useless; `catia_pad(Sketch.1)` is the
#: difference between recognising the operation and guessing at it.
_IDENTIFYING_KEYS = (
    "name",
    "feature",
    "sketch",
    "parameter",
    "material",
    "label",
    "plane",
    "component",
    "view",
    "table",
    "expression",
    "condition",
)


@dataclass(frozen=True)
class Unresolved:
    """A tool whose most recent attempt in this conversation failed.

    Deliberately keyed on the tool alone rather than on the tool *and* its
    arguments. Keying on arguments would be more precise and would also make
    this useless: a fillet retried at a smaller radius is a different argument
    set, so every retry would leave its predecessor sitting in the list forever,
    and a list that never empties is a list nobody reads.

    What this claims is exactly what it says: the last attempt failed. It does
    not claim the user still wants it. That judgement is the model's, which is
    why the error text travels with it.
    """

    tool: str
    error: str
    attempts: int
    at: datetime


@dataclass(frozen=True)
class Activity:
    """The conversation's CATIA record, reduced to what the state block needs."""

    operations: int = 0
    failures: int = 0
    last_at: datetime | None = None
    unresolved: list[Unresolved] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.operations == 0


def catia_activity(db: Session, conversation_id: str | None) -> Activity:
    """Reduce this conversation's operation log to counts and loose ends.

    A count and one bounded scan over `ix_catia_operations_conversation_created`,
    reading four columns rather than whole ORM rows -- it runs on every turn of
    every conversation, so what it costs is what every turn costs.

    The reduction is done here rather than in SQL. "The most recent attempt at
    each tool" is a window function in Postgres and a syntax error in the SQLite
    the tests run on, and doing it in Python keeps one implementation that both
    are known to agree on.
    """
    if not conversation_id:
        return Activity()

    total = (
        db.scalar(
            select(func.count())
            .select_from(CatiaOperation)
            .where(CatiaOperation.conversation_id == conversation_id)
        )
        or 0
    )
    if not total:
        return Activity()

    # Newest `ACTIVITY_SCAN_LIMIT` rows, then flipped back into build order.
    # Bounded because this runs on every turn of every conversation and a
    # months-old session has no ceiling otherwise. Bounded in the safe
    # direction, too: dropping the oldest rows can only *lose* a loose end that
    # has sat untouched for hundreds of operations, never invent one, and the
    # count above still reports the true total.
    rows = db.execute(
        select(
            CatiaOperation.tool,
            CatiaOperation.ok,
            CatiaOperation.error,
            CatiaOperation.created_at,
        )
        .where(CatiaOperation.conversation_id == conversation_id)
        .order_by(CatiaOperation.created_at.desc(), CatiaOperation.id.desc())
        .limit(ACTIVITY_SCAN_LIMIT)
    ).all()[::-1]

    # Last outcome wins, so a tool that failed and was then made to work leaves
    # nothing behind. Insertion order is preserved, which puts the oldest
    # unresolved failure first -- the one most likely to have been forgotten.
    latest: dict[str, Unresolved] = {}
    failures = 0
    for tool, ok, error, created_at in rows:
        if ok:
            latest.pop(tool, None)
            continue
        failures += 1
        previous = latest.get(tool)
        latest[tool] = Unresolved(
            tool=tool,
            error=error or "no reason was recorded",
            attempts=(previous.attempts + 1) if previous else 1,
            at=created_at,
        )

    return Activity(
        operations=total,
        failures=failures,
        last_at=rows[-1][3],
        unresolved=list(latest.values()),
    )


def resume_lines(db: Session, conversation_id: str | None) -> list[str]:
    """The state-block lines describing work already done, or nothing.

    Returns `[]` for a conversation that has never touched CATIA, which is most
    of them -- this must cost nothing on a turn about a simulation result.
    """
    activity = catia_activity(db, conversation_id)
    if activity.empty:
        return []

    lines = [
        f"catia_work_so_far: {activity.operations} operation(s) already ran in this "
        f"conversation, the last {_age(activity.last_at)}. They are recorded; call "
        "design_history to read what was done rather than asking the user to repeat it."
    ]

    if not activity.unresolved:
        return lines

    shown = activity.unresolved[:MAX_UNRESOLVED_IN_BLOCK]
    remainder = len(activity.unresolved) - len(shown)
    detail = "; ".join(
        f"{item.tool} failed {_age(item.at)}"
        + (f" after {item.attempts} attempts" if item.attempts > 1 else "")
        + f' -- "{_clean(item.error)}"'
        for item in shown
    )
    if remainder:
        detail += f"; and {remainder} more"
    lines.append(
        "catia_unfinished: the most recent attempt at each of these failed and "
        f"nothing has made it work since -- {detail}. Do not assume it was "
        "abandoned on purpose; check whether the user still wants it before "
        "moving on, and do not re-run it blind."
    )
    return lines


def build_history(
    db: Session,
    conversation_id: str | None,
    *,
    limit: int = HISTORY_PAGE_LIMIT,
    failures_only: bool = False,
) -> dict[str, Any]:
    """The full record of CATIA work in one conversation, newest last.

    Newest *last* on purpose. This is read as a build order -- the sequence that
    produced the part now on screen -- and reversing it to put the newest first
    would make every reader reconstruct the order themselves.
    """
    if not conversation_id:
        return {
            "operations": [],
            "total": 0,
            "returned": 0,
            "note": "This conversation has no CATIA history: it is not bound to one.",
        }

    limit = max(1, min(int(limit), HISTORY_PAGE_LIMIT))
    condition = CatiaOperation.conversation_id == conversation_id
    query = select(CatiaOperation).where(condition)
    if failures_only:
        query = query.where(CatiaOperation.ok.is_(False))
        condition = condition & CatiaOperation.ok.is_(False)

    total = db.scalar(select(func.count()).select_from(CatiaOperation).where(condition)) or 0

    # Newest `limit` rows, then flipped back into build order. Ordering
    # descending in SQL and reversing in Python is what keeps this a LIMIT
    # rather than a full read of a long session.
    rows = list(
        db.scalars(
            query.order_by(CatiaOperation.created_at.desc(), CatiaOperation.id.desc()).limit(limit)
        )
    )
    rows.reverse()

    activity = catia_activity(db, conversation_id)
    return {
        "operations": [_history_entry(row) for row in rows],
        "total": total,
        "returned": len(rows),
        "older_not_shown": max(0, total - len(rows)),
        "unresolved": [
            {"tool": item.tool, "error": _clean(item.error), "attempts": item.attempts}
            for item in activity.unresolved
        ],
    }


def _history_entry(operation: CatiaOperation) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "tool": operation.tool,
        "ok": operation.ok,
        "at": operation.created_at.isoformat(),
        "age": _age(operation.created_at),
    }
    subject = _subject(operation.arguments)
    if subject:
        entry["on"] = subject
    if operation.error:
        entry["error"] = _clean(operation.error)
    # What the operation produced, where the daemon named it. Enough to follow a
    # build order without shipping a whole result payload per row.
    result = operation.result if isinstance(operation.result, dict) else {}
    for key in ("feature", "sketch", "doc_name", "checkpoint_id"):
        value = result.get(key)
        if isinstance(value, (str, int, float)):
            entry["produced"] = _clean(str(value))
            break
    return entry


def _subject(arguments: Any) -> str | None:
    """The one argument worth showing beside the tool name."""
    if not isinstance(arguments, dict):
        return None
    for key in _IDENTIFYING_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return _clean(value)
    return None


def _clean(value: str) -> str:
    return sanitise_untrusted(str(value), max_chars=MAX_FIELD_CHARS)


#: Thresholds for `_age`, coarsest first. Coarse on purpose: the model needs to
#: know whether this is the same sitting or a different week, and a precise
#: "2 days, 4 hours and 11 minutes ago" invites it to reason about a number that
#: means nothing.
_AGE_STEPS: tuple[tuple[timedelta, str, float], ...] = (
    (timedelta(days=1), "day", 86_400.0),
    (timedelta(hours=1), "hour", 3_600.0),
    (timedelta(minutes=1), "minute", 60.0),
)


def _age(when: datetime | None) -> str:
    """How long ago something happened, in words, always rounded down."""
    if when is None:
        return "at an unrecorded time"
    if when.tzinfo is None:
        when = when.replace(tzinfo=utcnow().tzinfo)
    delta = utcnow() - when
    if delta < timedelta(0):
        # A clock that disagrees with the database's is not worth a branch of
        # arithmetic; it is worth not printing a negative age.
        return "just now"
    for threshold, unit, seconds in _AGE_STEPS:
        if delta >= threshold:
            count = int(delta.total_seconds() // seconds)
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return "less than a minute ago"
