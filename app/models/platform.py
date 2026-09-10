"""Feature flags, announcements and maintenance mode (P3.5, P3.7).

The three small switches with the largest blast radius in the product, so they
share a module and a rule: **every one of them is evaluated on the server**, and
the answer rides to the frontend rather than being re-decided there. A flag the
UI reads differently from the API is a feature that is half on, which is worse
than either state.

`app/core/flags.py` holds the evaluation; this is only the storage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey
from app.models.types import EnumText

if TYPE_CHECKING:
    from app.models.user import User


class FeatureFlag(UUIDPrimaryKey, TimestampMixin, Base):
    """One switch, with a global default and a rollout percentage."""

    __tablename__ = "feature_flags"
    __table_args__ = (UniqueConstraint("key", name="uq_feature_flags_key"),)

    key: Mapped[str] = mapped_column(String(80), index=True)
    description: Mapped[str] = mapped_column(Text, default="")

    #: The default for anyone with no override and outside the rollout.
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    #: 0-100. Evaluated **deterministically per subject**, not randomly — see
    #: `core/flags.py`. A flag that re-rolled the dice per request would flicker
    #: a feature on and off under one user, which is indistinguishable from a
    #: bug and impossible to support.
    rollout_percentage: Mapped[int] = mapped_column(Integer, default=0)

    #: The kill switch, and it **outranks every override**. That asymmetry is
    #: the whole point: the moment a feature is hurting people, the operator
    #: needs one action that turns it off for everybody, including the tenants
    #: somebody had specially enabled it for. A kill switch an override can beat
    #: is not a kill switch.
    killed: Mapped[bool] = mapped_column(Boolean, default=False)

    overrides: Mapped[list["FeatureFlagOverride"]] = relationship(
        back_populates="flag", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FeatureFlag {self.key} enabled={self.enabled} killed={self.killed}>"


class FeatureFlagOverride(UUIDPrimaryKey, TimestampMixin, Base):
    """A flag forced on or off for one tenant or one person.

    Exactly one of `organisation_id` / `user_id` is set. Two nullable columns
    rather than a polymorphic subject, because there are two kinds and there
    will not be a third — and a `subject_type` string would need a check
    constraint to say the same thing less clearly.
    """

    __tablename__ = "feature_flag_overrides"
    __table_args__ = (
        Index("ix_flag_overrides_flag", "feature_flag_id"),
        UniqueConstraint(
            "feature_flag_id", "organisation_id", name="uq_flag_override_org"
        ),
        UniqueConstraint("feature_flag_id", "user_id", name="uq_flag_override_user"),
    )

    feature_flag_id: Mapped[str] = mapped_column(
        ForeignKey("feature_flags.id", ondelete="CASCADE")
    )
    organisation_id: Mapped[str | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), default=None
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), default=None
    )
    enabled: Mapped[bool] = mapped_column(Boolean)

    flag: Mapped["FeatureFlag"] = relationship(back_populates="overrides")


class AnnouncementLevel(StrEnum):
    """How loudly a banner speaks. Three, not five: an operator choosing
    between "notice" and "advisory" under pressure is a delay, not a nuance."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Announcement(UUIDPrimaryKey, TimestampMixin, Base):
    """A banner both clients render, live between two times (P3.7)."""

    __tablename__ = "announcements"
    __table_args__ = (Index("ix_announcements_window", "starts_at", "ends_at"),)

    message: Mapped[str] = mapped_column(Text)
    #: `EnumText`, not `String(16)`. **This was a live defect**: a row loaded
    #: from the database gave a plain `str`, so `announcement.level.value`
    #: raised `AttributeError` on any deployment that had actually published an
    #: announcement — while passing in every test that wrote and read one in a
    #: single session. See `models/types.EnumText`.
    level: Mapped[AnnouncementLevel] = mapped_column(
        EnumText(AnnouncementLevel, 16), default=AnnouncementLevel.INFO
    )
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    #: Null means "until somebody withdraws it". Allowed, but it is the reason
    #: `withdrawn_at` exists: a banner with no end and no off switch is the one
    #: that is still up six months later.
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    withdrawn_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    published_by_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    published_by: Mapped["User"] = relationship()

    def is_live(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(timezone.utc)
        if self.withdrawn_at is not None:
            return False
        if moment < self.starts_at:
            return False
        return self.ends_at is None or moment < self.ends_at


class MaintenanceWindow(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mode: mutations are refused in words, not by erroring (P3.7).

    A row rather than a setting, because turning maintenance on must not need a
    deployment — that is the situation it exists for — and because who turned it
    on and why are exactly what somebody asks afterwards.

    **`allow_staff` defaults to true.** The people who need to fix whatever
    caused the maintenance are the ones holding staff grants, and a read-only
    mode that locks them out too is a read-only mode somebody works around by
    turning it off.
    """

    __tablename__ = "maintenance_windows"

    reason: Mapped[str] = mapped_column(Text)
    #: What a user is told. Separate from `reason`, which is the operator's own
    #: note: "migrating the primary database" is not what a customer needs, and
    #: "back by 14:00 UTC" is not what an incident review needs.
    message: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    expected_end_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    allow_staff: Mapped[bool] = mapped_column(Boolean, default=True)
    started_by_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    started_by: Mapped["User"] = relationship()

    def is_active(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(timezone.utc)
        return self.ended_at is None or moment < self.ended_at


__all__ = [
    "Announcement",
    "AnnouncementLevel",
    "FeatureFlag",
    "FeatureFlagOverride",
    "MaintenanceWindow",
]
