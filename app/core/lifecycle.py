"""Suspending, reinstating and erasing an account (P3.4).

**The only place that writes `is_active`, `suspended_at` or the deletion
columns.** Those fields are read by every guard in the service, so a second
writer is a second chance to leave them disagreeing — an account with
`suspended_at` set and `is_active` still true would be visibly suspended in the
console and fully usable through the API.

Three rules that are the substance of the feature rather than its mechanics:

1. **Suspension revokes every session family.** Setting `is_active = False`
   alone leaves live access tokens working for up to their fifteen minutes, and
   the refresh chain working until somebody notices — `routes/auth.py::refresh`
   does check `is_active`, but nothing forces the check to happen. Revoking is
   what makes the suspension immediate, and it reuses P1.1's machinery.
2. **Deletion is scheduled, never immediate.** The grace window *is* the
   feature. `purge` is a separate call that a job makes when the date arrives,
   and until then `cancel_deletion` restores the account completely.
3. **The purge goes through `MediaService`.** Blobs are content-addressed and
   shared, so a user's file may be byte-identical to somebody else's; deleting
   from the store directly would take another tenant's geometry with it.
   `MediaService.delete` drops the bytes only once nothing references them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.sessions import revoke_all
from app.models import Project, SessionRevocation, ShareLink, ShareRevocation, User

logger = logging.getLogger(__name__)

#: How long a scheduled deletion can be undone. Thirty days is the shape the
#: GDPR conversation has converged on and, more usefully, is long enough that a
#: person who deleted the wrong account notices before it is gone.
DELETION_GRACE_DAYS: Final = 30


class LifecycleError(Exception):
    """A lifecycle change was refused, with a message meant for the actor."""


@dataclass(frozen=True)
class PurgeReport:
    """What a purge actually erased. Returned so the caller can log a fact.

    A purge that says "done" and nothing else is impossible to audit and
    impossible to debug — the interesting failures here are partial ones.
    """

    user_id: str
    projects: int
    media_rows: int
    blobs_removed: int
    share_links_revoked: int


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def suspend(
    db: Session, user: User, *, reason: str, by: User, now: datetime | None = None
) -> User:
    """Withdraw access, immediately and everywhere.

    Idempotent: suspending an already-suspended account updates the reason and
    is not an error, because the realistic caller is an operator who is not sure
    whether the first click registered.
    """
    if not reason.strip():
        raise LifecycleError("A suspension needs a reason. It goes in the audit log.")
    moment = now or utcnow()
    user.is_active = False
    user.suspended_at = user.suspended_at or moment
    user.suspension_reason = reason
    user.suspended_by_id = by.id
    # The half that makes it take effect rather than merely be recorded.
    revoke_all(db, user, SessionRevocation.ADMIN)
    db.flush()
    return user


def reinstate(db: Session, user: User, *, now: datetime | None = None) -> User:
    """Restore access. The account's work was never touched.

    Does **not** restore sessions, and cannot: the families were revoked, and a
    revoked family is evidence that stays. The user signs in again, which is
    also the honest signal that something happened to their account.
    """
    del now
    if not user.is_suspended:
        raise LifecycleError("That account is not suspended.")
    user.is_active = True
    user.suspended_at = None
    user.suspension_reason = None
    user.suspended_by_id = None
    db.flush()
    return user


def schedule_deletion(
    db: Session,
    user: User,
    *,
    by: User,
    grace_days: int = DELETION_GRACE_DAYS,
    now: datetime | None = None,
) -> datetime:
    """Mark an account for erasure after a grace window. Returns the date.

    Access is withdrawn at once — a person who asked to be deleted should not
    keep using the product while the clock runs — but through the same
    suspension path, so cancelling restores everything.
    """
    if grace_days < 1:
        raise LifecycleError("A deletion needs a grace window of at least a day.")
    moment = now or utcnow()
    purge_at = moment + timedelta(days=grace_days)
    user.deletion_requested_at = moment
    user.deletion_requested_by_id = by.id
    user.deletion_scheduled_at = purge_at
    if not user.is_suspended:
        suspend(db, user, reason="Account deletion requested", by=by, now=moment)
    db.flush()
    return purge_at


def cancel_deletion(db: Session, user: User, *, now: datetime | None = None) -> User:
    """Call it off. Restores access as well, since scheduling withdrew it."""
    del now
    if not user.is_scheduled_for_deletion:
        raise LifecycleError("That account is not scheduled for deletion.")
    user.deletion_scheduled_at = None
    user.deletion_requested_at = None
    user.deletion_requested_by_id = None
    if user.is_suspended and user.suspension_reason == "Account deletion requested":
        # Only lift the suspension this scheduling imposed. An account that was
        # already suspended for abuse before somebody asked to delete it must
        # stay suspended when the deletion is called off.
        reinstate(db, user)
    db.flush()
    return user


def due_for_purge(db: Session, *, now: datetime | None = None) -> list[User]:
    """Accounts whose grace window has run out."""
    moment = now or utcnow()
    return list(
        db.scalars(
            select(User).where(
                User.deletion_scheduled_at.is_not(None),
                User.deletion_scheduled_at <= moment,
            )
        ).all()
    )


def purge(db: Session, user: User, media: object, *, now: datetime | None = None) -> PurgeReport:
    """Erase an account and everything belonging to it. **Irreversible.**

    `media` is a `MediaService`, typed loosely to keep `app/core/` free of a
    dependency on `app/media/` — the same reason `app/design/` takes its runner
    as a callable. It must expose `delete(media_row)`.

    Order matters and is not arbitrary: share links first (so nothing can be
    opened while the rest is being taken apart), then blobs through
    `MediaService` (which refcounts, so a file another tenant also uploaded
    survives), then the user row, whose cascades take projects, geometry,
    simulations, memberships, sessions and the second factor with it.
    """
    moment = now or utcnow()
    project_ids = list(db.scalars(select(Project.id).where(Project.owner_id == user.id)))

    revoked = 0
    if project_ids:
        for link in db.scalars(
            select(ShareLink).where(
                ShareLink.project_id.in_(project_ids), ShareLink.revoked_at.is_(None)
            )
        ):
            link.revoke(ShareRevocation.PROJECT_DELETED, now=moment)
            revoked += 1
    db.flush()

    from sqlalchemy import func

    from app.models import Media  # local: `app/core/` must not import `app/media/`

    rows = list(db.scalars(select(Media).where(Media.owner_id == user.id)))
    digests = {row.sha256 for row in rows}
    media_rows = len(rows)
    for row in rows:
        try:
            # `MediaService.delete` refcounts: it removes the bytes only once no
            # row references them, so a user whose STEP file is byte-identical
            # to another tenant's does not take theirs with them.
            media.delete(row)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - a missing blob must not stop a purge
            logger.warning(
                "could not remove a blob during purge",
                extra={"media_id": row.id, "detail": str(exc)},
            )
    db.flush()
    # Counted afterwards rather than taken from a return value: `delete` reports
    # nothing, and a digest still referenced is a blob that correctly survived.
    blobs = sum(
        1
        for digest in digests
        if not db.scalar(select(func.count()).select_from(Media).where(Media.sha256 == digest))
    )

    report = PurgeReport(
        user_id=user.id,
        projects=len(project_ids),
        media_rows=media_rows,
        blobs_removed=blobs,
        share_links_revoked=revoked,
    )
    db.delete(user)
    db.flush()
    return report


__all__ = [
    "DELETION_GRACE_DAYS",
    "LifecycleError",
    "PurgeReport",
    "cancel_deletion",
    "due_for_purge",
    "purge",
    "reinstate",
    "schedule_deletion",
    "suspend",
    "utcnow",
]
