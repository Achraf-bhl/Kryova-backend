"""Approval gates: a sign-off with a record, not a message that scrolls away.

P5 task 5 and E16 task 5 are the two halves of one thing — the plan says
"structured approval gates — a reviewable diff with a sign-off record, not a
chat message (P5 owns the surface)". This is the record; `routes/gates.py` is
the API and the frontend is the page.

**A gate is a row, and that is the whole design.** The alternative — asking in
the conversation and reading the answer back out of it — fails in three ways
that a row does not: the transcript is trimmed (`app/ai/resume.py` exists
because of exactly this), an LLM paraphrase of an approval is not an approval,
and nobody can later answer "who signed this off and what did they see". The
last one is the question that matters, because it is the one asked after
something goes wrong.

**What was approved is pinned, not referenced.** `subject_digest` is the digest
of the thing at the moment of asking, and `decide` refuses if it has moved. A
gate that approved "the current design" would approve whatever the design became
afterwards, which is not a sign-off, it is a signature on a blank page.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey
from app.models.types import EnumText
from app.models.types import JSONB_compat as JSONB

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.project import Project
    from app.models.user import User


class GateState(StrEnum):
    """Where a gate stands. Four, and `EXPIRED` is not a fifth kind of rejection.

    An expired gate was never decided: nobody looked, or nobody had authority
    while it mattered. Folding it into `REJECTED` would put a refusal on record
    that nobody made, which is the same class of error as an approval nobody
    gave.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def is_decided(self) -> bool:
        return self in (GateState.APPROVED, GateState.REJECTED)


class ApprovalGate(UUIDPrimaryKey, TimestampMixin, Base):
    """One thing waiting for a person to say yes or no."""

    __tablename__ = "approval_gates"
    __table_args__ = (
        Index("ix_approval_gates_open", "organisation_id", "state"),
        Index("ix_approval_gates_conversation", "conversation_id"),
    )

    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), default=None, index=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), default=None
    )

    title: Mapped[str] = mapped_column(String(255))
    #: What is being asked, in the words the *reviewer* needs — not the agent's
    #: internal reasoning. A gate whose question only makes sense to whoever
    #: raised it is a gate that gets approved unread.
    question: Mapped[str] = mapped_column(Text)

    #: `EnumText`, not `String(16)`. A bare string column hands back a plain
    #: `str` for any row loaded from the database, so `gate.state.is_decided`
    #: raises on every gate a reviewer actually opens while passing in any test
    #: that writes and reads in one session. See `models/types.EnumText`.
    state: Mapped[GateState] = mapped_column(
        EnumText(GateState, 16), default=GateState.PENDING
    )

    #: The thing being approved, at the moment of asking. A design-spec digest,
    #: a plan digest, a simulation id. `decide` compares it and refuses a stale
    #: one — see the module docstring.
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(64))
    subject_digest: Mapped[str] = mapped_column(String(64))

    #: What the reviewer is shown: the spec diff, the assertions it reaches, the
    #: cost estimate. Structured rather than prose so the page can render it and
    #: a later reader can see exactly what was on screen.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    requested_by_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    #: **Nullable, and it stays null on an expiry.** A gate nobody decided has
    #: no decider, and filling this with the system or the requester would put a
    #: name against a decision that was never made.
    decided_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    #: Required on a rejection by `routes/gates.py`. "No" without a reason is
    #: not actionable, and the agent's next move depends entirely on why.
    decision_note: Mapped[str | None] = mapped_column(Text, default=None)

    requested_by: Mapped["User"] = relationship(foreign_keys=[requested_by_id])
    decided_by: Mapped["User | None"] = relationship(foreign_keys=[decided_by_id])
    project: Mapped["Project | None"] = relationship()
    conversation: Mapped["Conversation | None"] = relationship()

    def is_open(self, now: datetime | None = None) -> bool:
        """Whether this gate is still waiting on somebody."""
        if self.state is not GateState.PENDING:
            return False
        if self.expires_at is None:
            return True
        return (now or datetime.now(timezone.utc)) < self.expires_at


__all__ = ["ApprovalGate", "GateState"]
