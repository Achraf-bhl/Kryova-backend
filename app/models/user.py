from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.mfa import RecoveryCode, TotpEnrolment
    from app.models.organisation import Membership
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

    #: When the address was proved reachable (P1.5). Null is "unverified", and
    #: it gates *project creation* rather than sign-in: friction where it
    #: protects and not where it annoys, so somebody can look around while a
    #: mistyped address is still fixable.
    email_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    email_verification_token_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    #: When the last verification email went out, for resend throttling. Stored
    #: on the user rather than counted per IP because the thing being rationed
    #: is *mail sent to this address* — an attacker using us to flood somebody's
    #: inbox picks the address, not the network they ask from.
    email_verification_sent_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, default=None
    )

    @property
    def is_verified(self) -> bool:
        return self.email_verified_at is not None

    # --- Account lifecycle (P3.4) -------------------------------------------
    #
    # Suspension and deletion are kept apart on purpose. Suspension is
    # reversible and changes nothing about the work: the designs, simulations
    # and files are untouched, and reinstating restores access exactly.
    # Deletion is not reversible, which is why it is *scheduled* rather than
    # done — the grace window is the whole feature, and `deletion_scheduled_at`
    # is what a purge job reads.

    #: When access was withdrawn. `is_active` is what every guard already
    #: checks; this records the *when* so the console and the audit log can say
    #: it. The two are kept in step by `core/lifecycle.py`, which is the only
    #: thing that should write either.
    suspended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    suspension_reason: Mapped[str | None] = mapped_column(Text, default=None)
    suspended_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    #: When everything belonging to this account is erased for good. Null is
    #: "not scheduled". A row is not deleted the moment it is requested,
    #: because a deletion nobody can stop for 30 days is a support ticket and a
    #: deletion nobody can stop at all is a disaster.
    deletion_scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    deletion_requested_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @property
    def is_suspended(self) -> bool:
        return self.suspended_at is not None

    @property
    def is_scheduled_for_deletion(self) -> bool:
        return self.deletion_scheduled_at is not None

    projects: Mapped[list["Project"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )

    #: Every tenant this person belongs to. The rows here -- not `projects` --
    #: are what authorises access to anything since P2.
    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    #: Every device this person is signed in on, live or revoked. Revoked rows
    #: are kept rather than deleted: a family ended by token reuse is evidence
    #: that someone had their credentials, and deleting it destroys the only
    #: record that it happened.
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    #: The authenticator app, if one is enrolled (P1.7). `uselist=False` because
    #: `TotpEnrolment` carries a unique constraint on `user_id` — one visible
    #: factor per account, so there is no second row for a UI to miss.
    totp: Mapped["TotpEnrolment | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )

    recovery_codes: Mapped[list["RecoveryCode"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
