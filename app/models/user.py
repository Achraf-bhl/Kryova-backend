from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.session import UserSession


class User(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    #: **Superseded by `user_sessions` (P1.1) and kept only until the column is
    #: dropped.** One hash per user meant one session per person: signing in on a
    #: second device silently ended the first, and there was no way to tell a
    #: legitimate refresh from a stolen token being replayed, because both wrote
    #: to the same slot and whoever refreshed last won. Nothing reads it now.
    refresh_token_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    password_reset_token_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    password_reset_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    full_name: Mapped[str | None] = mapped_column(String(255), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    projects: Mapped[list["Project"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )

    #: Every device this person is signed in on, live or revoked. Revoked rows
    #: are kept rather than deleted: a family ended by token reuse is evidence
    #: that someone had their credentials, and deleting it destroys the only
    #: record that it happened.
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
