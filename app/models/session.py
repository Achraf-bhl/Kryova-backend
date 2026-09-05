"""Sessions as rows — master plan P1.1 and P1.2.

**What this replaces, and why it was a real defect.** `User.refresh_token_hash`
held one hash per *user*, so a person with a laptop and a desktop had one
session between them: signing in on the second silently invalidated the first,
and the first learned about it at its next refresh, as an indistinguishable
"Invalid refresh token". Worse, there was no way to revoke one device without
revoking all of them, and no way at all to tell a legitimate refresh from a
stolen token being replayed — the old hash was simply overwritten, so a thief's
token and the victim's token were the same single slot and whoever refreshed
last won.

Decision 7 states the shape this has to take, and it is the 2026 OWASP-aligned
consensus: **refresh tokens rotate per use, in per-device families, with reuse
detection — a replayed rotated token kills the family.**

Three parts of that are load-bearing and each is easy to get subtly wrong:

**A family is a device, not a login.** Every rotation stays in the same family,
so the chain laptop→laptop→laptop is one row being updated rather than a
hundred rows accumulating. That is what makes "sign out this device" a single
revocation and "sign out everywhere" a revocation by user.

**Rotation without reuse detection is theatre**, in the plan's own words. The
point of rotating is not that the old token stops working — it is that the old
token *being presented* is evidence. So the previous hash is kept, deliberately,
and a hit on it means the token was captured: the whole family is revoked and
the person has to sign in again. Discarding the previous hash turns a theft
signal into a generic 401 that a thief and a victim both see.

**The previous hash needs a grace window, and the window must be short.** Two
tabs refreshing at once, or a request retried after a dropped connection, will
legitimately present the same token twice within a second or two, and treating
that as theft would sign people out for having flaky wifi. So a hit on the
previous hash inside `REUSE_GRACE_SECONDS` is served rather than punished, and
outside it is theft. The window is the entire difference between a security
control and a support burden, which is why it is a named constant with a reason
rather than a number inline.

**An absolute cap ends even a perfect chain.** A family that rotates correctly
for ever is a credential that never expires, so `absolute_expires_at` is set at
creation and never extended. Rotation moves `last_used_at`; it does not move the
deadline.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import (
    TimestampMixin,
    UTCDateTime,
    UUIDPrimaryKey,
    new_uuid,
    utcnow,
)

if TYPE_CHECKING:
    from app.models.user import User

#: How long a just-rotated token is still accepted. Long enough for two tabs
#: refreshing together or one retried request; far too short to be worth
#: stealing. Every acceptance inside the window is recorded on the row, so a
#: family being replayed repeatedly inside it is still visible.
REUSE_GRACE_SECONDS = 10


class SessionRevocation(str, Enum):
    """Why a session stopped. Kept because "revoked" alone cannot be acted on.

    A person seeing `TOKEN_REUSE` in their device list has been told something
    important — someone had their token — and support seeing it can tell that
    case apart from an ordinary sign-out without guessing.
    """

    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    TOKEN_REUSE = "token_reuse"
    PASSWORD_CHANGED = "password_changed"
    EXPIRED = "expired"
    ADMIN = "admin"


class UserSession(UUIDPrimaryKey, TimestampMixin, Base):
    """One device's refresh-token family.

    Named `UserSession` rather than `Session` because SQLAlchemy's own `Session`
    is imported in nearly every module here, and two things called `Session` one
    import apart is how a type annotation ends up meaning the wrong one.
    """

    __tablename__ = "user_sessions"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    #: The family this chain belongs to. Distinct from `id` because the row is
    #: updated in place on rotation — the family id is what survives, and what a
    #: reuse revocation is keyed on. They are equal at creation and stay so
    #: today; keeping them separate is what allows a future design that appends
    #: a row per rotation (for a full audit trail) without changing anything
    #: that reads a family.
    family_id: Mapped[str] = mapped_column(String(36), default=new_uuid, index=True)

    #: SHA-256 of the token that is currently valid. Never the token itself: a
    #: database dump must not be a set of live credentials.
    token_hash: Mapped[str] = mapped_column(String(64), index=True)

    #: SHA-256 of the token this one replaced. Kept on purpose — see the module
    #: docstring: this is the reuse *detector*, not a leftover.
    previous_token_hash: Mapped[str | None] = mapped_column(
        String(64), default=None, index=True
    )

    #: When the rotation that produced `token_hash` happened. The grace window
    #: is measured from here, so it cannot be extended by an unrelated write.
    rotated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    #: What the person sees in their device list. Derived from the user agent,
    #: never trusted as display-safe on its own.
    device_label: Mapped[str | None] = mapped_column(String(120), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(400), default=None)
    ip_address: Mapped[str | None] = mapped_column(String(45), default=None)

    last_used_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    #: Set once, at creation, and never extended. A chain that rotates perfectly
    #: for ever is a credential that never expires.
    absolute_expires_at: Mapped[datetime] = mapped_column(UTCDateTime)

    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), default=None)

    #: Whether this family was ended by a replay. Separate from the reason string
    #: because it is the one condition the *user* must be told about, and a
    #: boolean is what a query filters on cheaply.
    compromised: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="sessions")

    __table_args__ = (
        # Refresh looks a token up by hash and by previous hash on every call,
        # and it is the hottest authenticated path in the API. Both columns are
        # indexed individually above; this composite serves the "is this family
        # still live" question that revocation asks.
        Index("ix_user_sessions_user_active", "user_id", "revoked_at"),
    )

    def is_live(self, now: datetime | None = None) -> bool:
        """Whether this session may still be refreshed.

        Revocation and absolute expiry are checked together because a caller
        that checks only one has a hole: a revoked-but-unexpired family, or an
        expired-but-never-revoked one, are both refusable and neither is
        obviously the other's business.
        """
        moment = now or utcnow()
        return self.revoked_at is None and self.absolute_expires_at > moment

    def within_grace(self, now: datetime | None = None) -> bool:
        """Whether a presentation of the *previous* token is still legitimate.

        See the module docstring: outside this window a previous token is a
        theft signal, and inside it is two tabs racing.
        """
        moment = now or utcnow()
        return moment - self.rotated_at <= timedelta(seconds=REUSE_GRACE_SECONDS)

    def revoke(self, reason: SessionRevocation, now: datetime | None = None) -> None:
        """End this session, keeping the first reason if it is already ended.

        First reason rather than last, because the first is the true one: a
        family revoked for token reuse and then swept by an expiry job must
        still read as compromised, or the evidence of the theft is overwritten
        by routine housekeeping.
        """
        if self.revoked_at is not None:
            return
        self.revoked_at = now or utcnow()
        self.revoked_reason = reason.value
        if reason is SessionRevocation.TOKEN_REUSE:
            self.compromised = True


__all__ = ["REUSE_GRACE_SECONDS", "SessionRevocation", "UserSession"]
