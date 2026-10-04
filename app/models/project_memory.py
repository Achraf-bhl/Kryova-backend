"""Project memory: facts that outlive one conversation (ROAD_TO_10 2.7).

The house material, the fastener standard the shop uses, the units of the drawing template:
things an engineer would otherwise retype at the start of every conversation about a
project. They belong to the **project**, as rows the user can see, edit and delete -- never
as something the model remembers by itself, because a fact the user did not see being saved
is a fact they cannot correct, and one wrong sentence injected into every future turn is a
quiet, permanent defect.

**Two states, and only one of them reaches the model.** A fact the user typed is `CONFIRMED`
at once. A fact the agent noticed is `PROPOSED` and sits in the user's view until they
confirm it or dismiss it; the state block quotes confirmed facts only. The agent can ask, and
the user decides, which is the whole of the rule: *each one is shown and confirmed before it
is saved*.

`author` is `"user"` or `"agent"` and `author_id` is null for the agent -- the same
convention `DesignRevision` keeps, because "did a person write this" is the question an
audit of a signed-off design asks, and `NULL` must not mean "unknown".
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey
from app.models.types import EnumText


class MemoryState(StrEnum):
    """Whether the model may read a fact. Two states, because there is one question."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"


class ProjectMemory(UUIDPrimaryKey, TimestampMixin, Base):
    """One fact about a project, in one sentence."""

    __tablename__ = "project_memories"
    __table_args__ = (
        # The two reads: this project's facts in the order written, and "how many are open".
        Index("ix_project_memories_project_state", "project_id", "state", "created_at"),
    )

    #: Carried on the row because it is what the row-level-security policy reads; it is
    #: always the project's own organisation and is set from it, never from a request.
    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))

    text: Mapped[str] = mapped_column(Text)
    #: `EnumText`, not a bare string: see `models/types.EnumText` for the four times a bare
    #: string typed as an enum returned a `str` on a loaded row.
    state: Mapped[MemoryState] = mapped_column(
        EnumText(MemoryState, 16), default=MemoryState.CONFIRMED
    )

    author: Mapped[str] = mapped_column(String(16), default="user")
    #: Null with `author == "agent"` means the agent, not "unknown".
    author_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    #: Where the agent noticed it, so the user can see the conversation that produced a
    #: proposal. Set null when that conversation is deleted: the fact outlives it.
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), default=None
    )

    confirmed_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
