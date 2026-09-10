"""Raising and deciding approval gates (P5 task 5).

The model is in `app/models/gates.py` and says why a gate is a row. This is the
behaviour, and it holds three rules that the route layer must not be able to
talk it out of.

**A decision is against a digest, not a name.** `decide` refuses when the
subject has moved since the gate was raised. An approval that tracked "the
current spec" would be a signature on a blank page — the reviewer read one
thing and authorised whatever it later became.

**A rejection carries a reason.** "No" without one is not actionable, and the
agent's next move depends entirely on *why*: a rejected wall thickness and a
rejected material are different problems.

**Nobody decides their own gate by default.** `SELF_APPROVAL` is a policy
argument rather than a hard refusal, because a one-person deployment — which is
most self-hosted installs — would otherwise be unable to approve anything at
all. But it defaults to refusing, so a team gets four-eyes without configuring
it, and a soloist opts out knowingly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApprovalGate, GateState, User

#: How long a gate waits before it is nobody's decision to make. Long enough to
#: cross a weekend, because the reviewer is a person with a calendar and a gate
#: that expires overnight is a gate that expires unread.
DEFAULT_GATE_DAYS = 7


class GateRefusal(StrEnum):
    """Why a decision was refused. An enum so the route maps it to a status and
    a sentence in one place, rather than matching on prose."""

    ALREADY_DECIDED = "already_decided"
    EXPIRED = "expired"
    SUBJECT_MOVED = "subject_moved"
    SELF_APPROVAL = "self_approval"
    NO_REASON_GIVEN = "no_reason_given"

    @property
    def sentence(self) -> str:
        return {
            GateRefusal.ALREADY_DECIDED: (
                "This has already been decided. Raise a new gate rather than "
                "changing an answer somebody has acted on."
            ),
            GateRefusal.EXPIRED: (
                "This gate expired before it was decided, so it stands undecided "
                "rather than refused. Raise it again if it still matters."
            ),
            GateRefusal.SUBJECT_MOVED: (
                "What this gate was raised against has changed since it was "
                "raised, so approving it now would approve something nobody "
                "reviewed. Raise a new gate against the current version."
            ),
            GateRefusal.SELF_APPROVAL: (
                "This gate was raised by you, and this deployment asks for a "
                "second pair of eyes."
            ),
            GateRefusal.NO_REASON_GIVEN: (
                "A rejection needs a reason: what happens next depends entirely "
                "on why this was turned down."
            ),
        }[self]


@dataclass(frozen=True)
class Decision:
    """The outcome of `decide`: the gate, or why it could not be decided."""

    gate: ApprovalGate | None
    refusal: GateRefusal | None = None

    @property
    def ok(self) -> bool:
        return self.refusal is None


def digest_of(subject: Any) -> str:
    """A stable digest of whatever is being approved.

    `sort_keys` and `separators` because the same spec serialised twice must
    give the same digest or every gate refuses itself on a whitespace
    difference. Anything unserialisable falls back to `repr`, which is worse
    but is never silently *equal* to something it differs from.
    """
    text = json.dumps(subject, sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def raise_gate(
    db: Session,
    *,
    organisation_id: str,
    requested_by: User,
    title: str,
    question: str,
    subject_type: str,
    subject_id: str,
    subject: Any,
    evidence: dict[str, Any] | None = None,
    project_id: str | None = None,
    conversation_id: str | None = None,
    expires_in_days: int = DEFAULT_GATE_DAYS,
    now: datetime | None = None,
) -> ApprovalGate:
    """Ask for a sign-off, pinned to the subject as it stands right now."""
    moment = now or datetime.now(timezone.utc)
    gate = ApprovalGate(
        organisation_id=organisation_id,
        project_id=project_id,
        conversation_id=conversation_id,
        title=title,
        question=question,
        state=GateState.PENDING,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_digest=digest_of(subject),
        evidence=evidence or {},
        requested_by_id=requested_by.id,
        expires_at=moment + timedelta(days=expires_in_days),
    )
    db.add(gate)
    db.flush()
    return gate


def decide(
    db: Session,
    gate: ApprovalGate,
    *,
    approve: bool,
    by: User,
    subject: Any,
    note: str | None = None,
    allow_self_approval: bool = False,
    now: datetime | None = None,
) -> Decision:
    """Approve or reject, or say why neither is possible.

    `subject` is the thing as it stands *now*, and is re-digested here rather
    than trusted from the caller: a route that passed the recorded digest back
    in would make the staleness check a tautology.
    """
    moment = now or datetime.now(timezone.utc)

    if gate.state.is_decided:
        return Decision(None, GateRefusal.ALREADY_DECIDED)
    if not gate.is_open(moment):
        # Recorded as expired rather than left pending, so the next reader sees
        # a settled row. Note `decided_by_id` stays null -- nobody decided this.
        gate.state = GateState.EXPIRED
        db.flush()
        return Decision(None, GateRefusal.EXPIRED)
    if digest_of(subject) != gate.subject_digest:
        return Decision(None, GateRefusal.SUBJECT_MOVED)
    if approve and by.id == gate.requested_by_id and not allow_self_approval:
        return Decision(None, GateRefusal.SELF_APPROVAL)
    if not approve and not (note or "").strip():
        return Decision(None, GateRefusal.NO_REASON_GIVEN)

    gate.state = GateState.APPROVED if approve else GateState.REJECTED
    gate.decided_at = moment
    gate.decided_by_id = by.id
    gate.decision_note = (note or "").strip() or None
    db.flush()
    return Decision(gate)


def expire_due(db: Session, *, now: datetime | None = None) -> int:
    """Settle every gate whose moment has passed. Returns how many.

    Gates are also expired lazily by `decide`, which is what makes the decision
    path correct on its own. This exists so a list of open gates does not fill
    with rows nobody can act on — and it is a sweep rather than a filter for the
    reason `EXPIRED` is a state at all: "nobody decided this in time" is
    something that happened, and a row that merely stops matching a query never
    happened to anybody.
    """
    moment = now or datetime.now(timezone.utc)
    stale = db.scalars(
        select(ApprovalGate).where(
            ApprovalGate.state == GateState.PENDING,
            ApprovalGate.expires_at.is_not(None),
            ApprovalGate.expires_at <= moment,
        )
    ).all()
    for gate in stale:
        gate.state = GateState.EXPIRED
    if stale:
        db.commit()
    return len(stale)


def open_gates(
    db: Session, *, organisation_id: str, conversation_id: str | None = None
) -> list[ApprovalGate]:
    """Everything still waiting, newest first."""
    query = select(ApprovalGate).where(
        ApprovalGate.organisation_id == organisation_id,
        ApprovalGate.state == GateState.PENDING,
    )
    if conversation_id is not None:
        query = query.where(ApprovalGate.conversation_id == conversation_id)
    return list(db.scalars(query.order_by(ApprovalGate.created_at.desc())))


__all__ = [
    "DEFAULT_GATE_DAYS",
    "Decision",
    "GateRefusal",
    "decide",
    "digest_of",
    "expire_due",
    "open_gates",
    "raise_gate",
]
