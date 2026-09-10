"""Proving an address is reachable, and rationing how often we try (P1.5).

Separate from `routes/auth.py` because the throttle and the token lifetime are
the parts worth testing without a client, and because P3's admin panel confirms
an address by hand through the same `mark_verified`.

**The throttle is per account, not per IP.** The abuse this stops is using
Kryova as a free way to post mail at somebody — sign up with their address, then
hit resend. That attacker chooses the *address*; the network they ask from is
free and rotatable. An IP budget rations the wrong thing, and the existing
`auth_limiter` already covers the shape it is good at.

**A resend does not reveal whether the account exists**, for the same reason
password reset does not. `request_verification` returns a `Throttled` result the
route deliberately does not surface differently from success.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_token
from app.models import User


@dataclass(frozen=True)
class VerificationRequest:
    """What a resend produced, if anything.

    `token` is None when the request was throttled or the address was already
    verified. The caller sends mail iff there is a token, so the two "do
    nothing" cases need no branch of their own.
    """

    token: str | None
    retry_after_seconds: int = 0
    already_verified: bool = False

    @property
    def should_send(self) -> bool:
        return self.token is not None


def request_verification(db: Session, user: User, *, now: datetime) -> VerificationRequest:
    """Mint a verification token, unless one was minted too recently.

    Replaces any outstanding token rather than adding one. Two live links to the
    same account means the older one keeps working after the user asked for a
    fresh one — which is the exact situation a resend exists to get out of, and
    it doubles the window a leaked link is usable in.
    """
    if user.is_verified:
        return VerificationRequest(token=None, already_verified=True)

    window = timedelta(seconds=settings.email_verification_resend_seconds)
    last = user.email_verification_sent_at
    if last is not None and now - last < window:
        remaining = window - (now - last)
        return VerificationRequest(
            token=None, retry_after_seconds=max(1, int(remaining.total_seconds()))
        )

    raw = secrets.token_urlsafe(32)
    user.email_verification_token_hash = hash_token(raw)
    user.email_verification_sent_at = now
    return VerificationRequest(token=raw)


def expires_at(sent_at: datetime) -> datetime:
    return sent_at + timedelta(hours=settings.email_verification_ttl_hours)


def confirm(db: Session, token: str, *, now: datetime) -> User | None:
    """The user this token verifies, or None if it is unknown or expired.

    Single use: the hash is cleared on success, so a link forwarded to a mailing
    list cannot be replayed. An expired token is also cleared — leaving it would
    let somebody who found an old link keep probing it after the account had
    been verified some other way.
    """
    if not token:
        return None
    presented = hash_token(token)
    from sqlalchemy import select

    user = db.scalar(select(User).where(User.email_verification_token_hash == presented))
    if user is None:
        return None
    sent = user.email_verification_sent_at
    if sent is None or now > expires_at(sent):
        user.email_verification_token_hash = None
        return None
    mark_verified(user, now=now)
    return user


def mark_verified(user: User, *, now: datetime) -> None:
    """Record the address as proved, from a link or from an administrator."""
    user.email_verified_at = now
    user.email_verification_token_hash = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "VerificationRequest",
    "confirm",
    "expires_at",
    "mark_verified",
    "request_verification",
    "utcnow",
]
