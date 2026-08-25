"""Agent conversations and their turns.

The transcript is the agent's memory. Every turn is persisted -- including the
tool calls it made and what they returned -- so a later turn can see what was
already tried, what failed and why. Replaying the stored transcript is what
stops the agent re-running a simulation it already ran, or re-asking for a
value the user gave three messages ago.
"""

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Integer, String, Text
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


class Conversation(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "conversations"

    title: Mapped[str] = mapped_column(String(255), default="New conversation")
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Scoping a conversation to a project is what lets the agent resolve "the
    # bracket" or "the latest run" without the user repeating ids. Nullable:
    # a conversation can start before a project is chosen.
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, default=None
    )

    owner: Mapped["User"] = relationship()
    project: Mapped["Project | None"] = relationship()
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.sequence",
    )


class ConversationMessage(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "conversation_messages"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # Explicit ordering column rather than sorting on created_at: several
    # messages in one agent turn are written inside the same transaction and
    # can share a timestamp to the microsecond.
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[MessageRole] = mapped_column(String(16))
    content: Mapped[str | None] = mapped_column(Text, default=None)

    #: Assistant turns only: the tool calls this turn requested.
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, default=None)
    #: Tool turns only: which call this is the result of, and which tool ran.
    tool_call_id: Mapped[str | None] = mapped_column(String(64), default=None)
    tool_name: Mapped[str | None] = mapped_column(String(64), default=None)
    #: True when the tool raised. Kept rather than dropped so the agent can see
    #: its own failures on the next turn instead of repeating them.
    is_error: Mapped[bool] = mapped_column(default=False)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
