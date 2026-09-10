"""Enrolling, confirming and spending a second factor (P1.7).

`core/totp.py` is the algorithm and knows nothing about storage; `models/mfa.py`
is the storage and knows nothing about the algorithm. This is where they meet,
and it is the only module that should ever hold both a `Session` and a decrypted
secret.

**Everything here takes `now` as an argument.** A TOTP implementation whose
clock is `datetime.now()` cannot be tested without sleeping thirty seconds, and
the replay guard cannot be tested at all. Same rule `app/assembly/locking.py`
follows and for the same reason.

**Refusals are deliberately vague to the caller and specific in the log.** A
sign-in that says "that code was already used" tells an attacker holding a
captured code that they have the right code and the wrong moment, which is more
than they had. The route says "That code is not valid"; `MfaRefusal` carries the
real reason for the audit log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decrypt_at_rest, encrypt_at_rest, hash_token
from app.core.totp import (
    consume,
    generate_recovery_codes,
    generate_secret,
    normalise_recovery_code,
    provisioning_uri,
)
from app.models import RecoveryCode, TotpEnrolment, User

logger = logging.getLogger(__name__)


class MfaRefusal(StrEnum):
    """Why a second factor was not accepted. For the log, never for the user."""

    NOT_ENROLLED = "not_enrolled"
    NOT_CONFIRMED = "not_confirmed"
    WRONG_CODE = "wrong_code"
    #: Arithmetically correct and already spent — the replay guard firing.
    REPLAYED = "replayed"
    #: The secret could not be decrypted, which in practice means `SECRET_KEY`
    #: was rotated. Distinct from `WRONG_CODE` because the remedy is completely
    #: different: the user must re-enrol and no code they type will ever work.
    UNREADABLE_SECRET = "unreadable_secret"
    NO_RECOVERY_CODE = "no_recovery_code"


@dataclass(frozen=True)
class Enrolment:
    """A started enrolment, with the two things the user needs exactly once."""

    secret: str
    uri: str
    recovery_codes: list[str]


class MfaError(Exception):
    """A second factor was refused. Carries the reason for the audit log."""

    def __init__(self, refusal: MfaRefusal, detail: str = "That code is not valid.") -> None:
        super().__init__(detail)
        self.refusal = refusal
        self.detail = detail


def enrolment_for(db: Session, user: User) -> TotpEnrolment | None:
    return db.scalar(select(TotpEnrolment).where(TotpEnrolment.user_id == user.id))


def is_enrolled(db: Session, user: User) -> bool:
    """Whether this account has a *confirmed* second factor.

    The one question the login route asks. A started-but-unconfirmed enrolment
    answers False, which is what stops a user who scanned a broken QR code from
    being locked out by their own half-finished setup.
    """
    enrolment = enrolment_for(db, user)
    return enrolment is not None and enrolment.is_active


def begin_enrolment(db: Session, user: User, *, now: datetime | None = None) -> Enrolment:
    """Start (or restart) enrolment. Nothing is enforced until `confirm`.

    Restarting replaces any unconfirmed row and **regenerates the recovery
    codes**, because codes shown alongside a secret the user then abandoned are
    codes they may have written down for an enrolment that no longer exists.
    Rather than reason about which set is live, there is only ever one.
    """
    del now  # symmetry with the rest of the module; nothing here is time-bound
    secret = generate_secret()
    existing = enrolment_for(db, user)
    if existing is not None:
        db.delete(existing)
        db.flush()

    db.add(TotpEnrolment(user_id=user.id, secret_encrypted=encrypt_at_rest(secret)))
    codes = _issue_recovery_codes(db, user)
    db.flush()
    return Enrolment(
        secret=secret,
        uri=provisioning_uri(secret=secret, account=user.email),
        recovery_codes=codes,
    )


def _issue_recovery_codes(db: Session, user: User) -> list[str]:
    """Replace every recovery code with a fresh set. Returns them in the clear.

    Used ones are deleted too. Keeping a spent code's row past a regeneration
    would report "3 of 10 used" about a set that no longer exists.
    """
    for old in db.scalars(select(RecoveryCode).where(RecoveryCode.user_id == user.id)):
        db.delete(old)
    db.flush()
    codes = generate_recovery_codes()
    for code in codes:
        db.add(
            RecoveryCode(user_id=user.id, code_hash=hash_token(normalise_recovery_code(code)))
        )
    return codes


def confirm_enrolment(db: Session, user: User, code: str, *, now: datetime) -> TotpEnrolment:
    """Prove the app works, and only then make the factor real.

    Raises `MfaError` and leaves the enrolment unconfirmed on a wrong code, so a
    user can try again with the same secret rather than rescanning.
    """
    enrolment = enrolment_for(db, user)
    if enrolment is None:
        raise MfaError(MfaRefusal.NOT_ENROLLED, "Start setting up two-factor authentication first.")
    secret = decrypt_at_rest(enrolment.secret_encrypted)
    if secret is None:
        raise MfaError(
            MfaRefusal.UNREADABLE_SECRET,
            "This setup can no longer be read. Start again to get a new code.",
        )
    step = consume(secret, code, now=now.timestamp(), last_step=enrolment.last_step)
    if step is None:
        raise MfaError(MfaRefusal.WRONG_CODE)
    enrolment.confirmed_at = now
    enrolment.last_step = step
    enrolment.last_used_at = now
    db.flush()
    return enrolment


def verify_code(db: Session, user: User, code: str, *, now: datetime) -> TotpEnrolment:
    """Spend one TOTP code at sign-in. Burns the step on success."""
    enrolment = enrolment_for(db, user)
    if enrolment is None:
        raise MfaError(MfaRefusal.NOT_ENROLLED)
    if not enrolment.is_active:
        raise MfaError(MfaRefusal.NOT_CONFIRMED)
    secret = decrypt_at_rest(enrolment.secret_encrypted)
    if secret is None:
        raise MfaError(
            MfaRefusal.UNREADABLE_SECRET,
            "Two-factor authentication cannot be checked for this account. Contact support.",
        )
    step = consume(secret, code, now=now.timestamp(), last_step=enrolment.last_step)
    if step is None:
        # Told apart for the log only. The message the user gets is identical
        # either way, on purpose.
        replayed = enrolment.last_step is not None
        raise MfaError(MfaRefusal.REPLAYED if replayed else MfaRefusal.WRONG_CODE)
    enrolment.last_step = step
    enrolment.last_used_at = now
    db.flush()
    return enrolment


def spend_recovery_code(db: Session, user: User, code: str, *, now: datetime) -> RecoveryCode:
    """Use one recovery code. Single use, enforced by the row."""
    presented = hash_token(normalise_recovery_code(code))
    row = db.scalar(
        select(RecoveryCode).where(
            RecoveryCode.user_id == user.id,
            RecoveryCode.code_hash == presented,
            RecoveryCode.used_at.is_(None),
        )
    )
    if row is None:
        raise MfaError(MfaRefusal.NO_RECOVERY_CODE)
    row.used_at = now
    db.flush()
    return row


def unused_recovery_code_count(db: Session, user: User) -> int:
    codes = db.scalars(
        select(RecoveryCode).where(
            RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None)
        )
    ).all()
    return len(codes)


def regenerate_recovery_codes(db: Session, user: User) -> list[str]:
    """A fresh set, invalidating every existing one. Requires a live enrolment."""
    if not is_enrolled(db, user):
        raise MfaError(MfaRefusal.NOT_ENROLLED, "Two-factor authentication is not set up.")
    codes = _issue_recovery_codes(db, user)
    db.flush()
    return codes


def disable(db: Session, user: User) -> bool:
    """Remove the factor and every recovery code. True if there was one.

    The recovery codes go too, and that matters: leaving them would mean an
    account with no second factor still had ten standing credentials that skip
    one, which is strictly worse than never having enrolled.
    """
    enrolment = enrolment_for(db, user)
    had_one = enrolment is not None
    if enrolment is not None:
        db.delete(enrolment)
    for code in db.scalars(select(RecoveryCode).where(RecoveryCode.user_id == user.id)):
        db.delete(code)
    db.flush()
    return had_one


def utcnow() -> datetime:
    """The one clock read in this module, so callers can inject instead."""
    return datetime.now(timezone.utc)


__all__ = [
    "Enrolment",
    "MfaError",
    "MfaRefusal",
    "begin_enrolment",
    "confirm_enrolment",
    "disable",
    "enrolment_for",
    "is_enrolled",
    "regenerate_recovery_codes",
    "spend_recovery_code",
    "unused_recovery_code_count",
    "utcnow",
    "verify_code",
]
