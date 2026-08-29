"""Agent conversations, their turns, and what the model cost.

The transcript is the agent's memory. Every turn is persisted -- including the
tool calls it made and what they returned -- so a later turn can see what was
already tried, what failed and why. Replaying the stored transcript is what
stops the agent re-running a simulation it already ran, or re-asking for a
value the user gave three messages ago.

Two things sit alongside the transcript because they cannot be derived from it:

**A rolling summary.** A design session in CATIA produces dozens of tool calls,
and no context window holds them forever. `summary` is the compacted account of
everything up to `summary_through_sequence`; the window covers the rest. See
`app/ai/context.py`.

**Token accounting.** `AITokenUsage` is one row per model call, which is what
makes a per-user daily budget enforceable and a per-conversation total cheap to
read. FEA compute is already metered; this is the same for LLM spend.
"""

from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import Date, ForeignKey, Index, Integer, String, Text, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKey
from app.models.types import JSONB_compat as JSONB

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.user import User


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessageRoleType(TypeDecorator):
    """Store the role as text, but always hand back a `MessageRole`.

    Without this the column is a bare `String(16)`, so a row *written* in this
    session carries the enum while a row *loaded* from the database carries a
    plain `str`. Every `role is MessageRole.USER` check then silently changes
    answer depending on whether the object came from the identity map or a
    SELECT -- which means it works in a test that writes and reads in one
    session, and fails on the next request, where the whole transcript loads
    fresh and every message falls through to the last branch.

    `StrEnum` makes this doubly easy to miss: `role == MessageRole.USER` is True
    either way, so only the identity checks break, and they break silently.

    Text, not a native enum: the column is already VARCHAR in every deployed
    database, and adding a value to the enum should never need a type migration.
    """

    impl = String(16)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return MessageRole(value).value

    def process_result_value(self, value: Any, dialect: Any) -> "MessageRole | None":
        if value is None:
            return None
        return MessageRole(value)


class Conversation(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "conversations"

    title: Mapped[str] = mapped_column(String(255), default="New conversation")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Scoping a conversation to a project is what lets the agent resolve "the
    # bracket" or "the latest run" without the user repeating ids. Nullable:
    # a conversation can start before a project is chosen.
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, default=None
    )

    #: Compacted account of everything up to `summary_through_sequence`, written
    #: by a dedicated LLM call once the transcript outgrows the context window.
    #: Re-injected every turn so the agent keeps the early history it can no
    #: longer see message-by-message.
    summary: Mapped[str | None] = mapped_column(Text, default=None)
    #: Exclusive upper bound: messages with `sequence < this` are covered by the
    #: summary and are never replayed verbatim again.
    summary_through_sequence: Mapped[int] = mapped_column(Integer, default=0)

    #: Running totals, denormalised from `AITokenUsage` so reading a
    #: conversation costs no aggregate query.
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)

    #: Last known post-state of the conversation's CATIA document -- features,
    #: parameters, mass, bounding box -- refreshed from what each mutating CATIA
    #: tool returns. A cache of the bridge's answers, so the per-turn state
    #: block can describe the part without a round trip to a workstation that
    #: may be asleep.
    #:
    #: The *binding* itself is not here: `CatiaDocument.conversation_id` is the
    #: single source of truth for which document a conversation owns, enforced
    #: by a unique constraint. This column caches what that document looked
    #: like, never which document it is.
    catia_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)

    owner: Mapped["User"] = relationship()
    project: Mapped["Project | None"] = relationship()
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.sequence",
    )

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ConversationMessage(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        # Replaying a transcript in order is the hot path, and the uniqueness is
        # load-bearing rather than incidental: `sequence` is what orders the
        # window and what the summary boundary is expressed in, so two messages
        # sharing one would make both ambiguous. The index has existed since the
        # conversations migration; declaring it here is what stops
        # `alembic check` reporting drift against it.
        Index(
            "ix_conversation_messages_sequence",
            "conversation_id",
            "sequence",
            unique=True,
        ),
    )

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # Explicit ordering column rather than sorting on created_at: several
    # messages in one agent turn are written inside the same transaction and
    # can share a timestamp to the microsecond.
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[MessageRole] = mapped_column(MessageRoleType)
    content: Mapped[str | None] = mapped_column(Text, default=None)

    #: Assistant turns only: the tool calls this turn requested.
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, default=None)
    #: Tool turns only: which call this is the result of, and which tool ran.
    tool_call_id: Mapped[str | None] = mapped_column(String(64), default=None)
    tool_name: Mapped[str | None] = mapped_column(String(64), default=None)
    #: True when the tool raised. Kept rather than dropped so the agent can see
    #: its own failures on the next turn instead of repeating them.
    is_error: Mapped[bool] = mapped_column(default=False)
    #: How long the tool took. Stored so a rehydrated conversation shows the
    #: same step list as the live stream did, rather than a timing-free stub.
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class AITokenUsage(UUIDPrimaryKey, TimestampMixin, Base):
    """One row per model call, so LLM spend is as visible as FEA compute.

    Kept as an append-only ledger rather than a counter per user: the daily
    budget needs a day-scoped sum, the conversation view needs a
    conversation-scoped sum, and an incident needs to know which purpose and
    which model spent the tokens. All three fall out of the same rows.
    """

    __tablename__ = "ai_token_usage"
    __table_args__ = (
        # The budget check runs on every chat turn and is exactly this lookup.
        Index("ix_ai_token_usage_user_day", "user_id", "usage_date"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: Nullable: interpretation and load-case drafting are not conversational,
    #: and a deleted conversation must not take its spend history with it.
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True, default=None
    )
    #: UTC calendar day, denormalised so the budget query is an index lookup
    #: rather than a timezone-sensitive expression over `created_at`.
    usage_date: Mapped[date] = mapped_column(Date)
    #: Which feature spent the tokens: chat, interpret, load_case, summary, title.
    purpose: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
