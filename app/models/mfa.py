"""The second factor, as rows (P1.7).

Two tables rather than columns on `users`, for the same reason `user_sessions`
is a table: enrolment has a lifecycle of its own — created, confirmed, used,
disabled — and recovery codes are a collection. Folding either onto the user row
would mean a nullable column per state and no way to hold ten codes.

**A pending enrolment is not a second factor.** `confirmed_at` is what makes
`TotpEnrolment` count, and it is only set once the user has typed a code the
server generated the same secret for. Without that step, a user who scans a
broken QR code, or scans nothing at all, has locked themselves out of their own
account and only finds out at the next sign-in.

**`last_step` is the replay guard** and belongs here rather than in
`core/totp.py`, which is deliberately free of storage. See that module for why a
correct code must still be refused the second time it is presented.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.user import User


class TotpEnrolment(UUIDPrimaryKey, TimestampMixin, Base):
    """One authenticator app bound to one account."""

    __tablename__ = "totp_enrolments"
    __table_args__ = (
        # One enrolment per user. A second row would be a second valid factor
        # nobody can see in the UI, which is a backdoor with a friendly name.
        UniqueConstraint("user_id", name="uq_totp_enrolments_user"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    #: The base32 shared secret, AES-GCM sealed by `security.encrypt_at_rest`.
    #: Long enough for the versioned envelope, not for the secret alone.
    secret_encrypted: Mapped[str] = mapped_column(String(512))

    #: Set when the user proves the app works. Null means enrolment started and
    #: was never finished, and such a row authorises nothing.
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    #: Highest TOTP step this account has accepted. `BigInteger` because a step
    #: is unix-seconds/30, which passes 2^31 in 2038 — the one place in this
    #: schema where a 32-bit integer has a date on it.
    last_step: Mapped[int | None] = mapped_column(BigInteger, default=None)

    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    user: Mapped["User"] = relationship(back_populates="totp")

    @property
    def is_active(self) -> bool:
        return self.confirmed_at is not None


class RecoveryCode(UUIDPrimaryKey, TimestampMixin, Base):
    """One single-use way back in when the phone is gone.

    Stored as a SHA-256 hash of the normalised code, exactly like a refresh
    token: these are credentials that bypass the second factor, so a database
    that can read them is a database that can sign in as anybody who enrolled.

    A used code is kept with `used_at` set rather than deleted, so "seven of
    your ten codes are gone" is answerable — a user burning through recovery
    codes is either disorganised or being attacked, and both are worth showing
    them.
    """

    __tablename__ = "recovery_codes"
    __table_args__ = (
        Index("ix_recovery_codes_user_unused", "user_id", "used_at"),
        UniqueConstraint("code_hash", name="uq_recovery_codes_hash"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    user: Mapped["User"] = relationship(back_populates="recovery_codes")


__all__ = ["RecoveryCode", "TotpEnrolment"]
