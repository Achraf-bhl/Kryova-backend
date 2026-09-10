"""Issuing, resolving and revoking share links; moving a project (P2.5).

Sits beside `core/sessions.py` for the same reason: the rules about a credential
belong somewhere testable without a client, and the route should read as a thin
wrapper over a decision made here.

**The single most important line in this module is `resolve`.** It is the only
function in the service that answers a request with no principal, no membership
and no tenant context, so everything about it is built to have the smallest
possible reach: it takes a token, returns at most one project, and has no
parameter that could widen what it returns. A share route that took a
`project_id` *and* a token would be one missing comparison away from being a
cross-tenant read; this one has nothing to compare.

**Every refusal is the same refusal.** Expired, revoked, never existed,
project since transferred — `resolve` returns `None` for all of them and the
route answers 404. Telling a holder that their token *was* valid is information
they did not have, and it is exactly the information that makes guessing worth
continuing.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_token
from app.models import (
    Organisation,
    Project,
    ProjectTransfer,
    ShareLink,
    ShareRevocation,
    User,
)

#: Default life of a link, and the ceiling a caller may ask for. A share link is
#: a credential with no second factor and no account behind it, so "forever" is
#: not on the menu — the ceiling is what stops a link pasted into an email
#: thread in 2026 still opening in 2029.
DEFAULT_SHARE_DAYS: Final = 14
MAX_SHARE_DAYS: Final = 365


class SharingError(Exception):
    """A share or transfer was refused, with a message meant for the user."""


@dataclass(frozen=True)
class IssuedShare:
    """A new link. `token` is the only time the raw value exists."""

    link: ShareLink
    token: str


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def issue(
    db: Session,
    *,
    project: Project,
    created_by: User,
    days: int = DEFAULT_SHARE_DAYS,
    label: str | None = None,
    note: str | None = None,
    allow_geometry_download: bool = False,
    now: datetime | None = None,
) -> IssuedShare:
    """Mint a read-only link to one project."""
    if days < 1:
        raise SharingError("A share link has to last at least a day.")
    if days > MAX_SHARE_DAYS:
        raise SharingError(
            f"A share link can last at most {MAX_SHARE_DAYS} days. "
            "Issue a new one when this expires."
        )
    moment = now or utcnow()
    raw = secrets.token_urlsafe(32)
    link = ShareLink(
        project_id=project.id,
        # From the project, not from the caller: a link that recorded the
        # issuer's organisation rather than the project's could outlive a
        # transfer and keep pointing into a tenant it never belonged to.
        organisation_id=project.organisation_id,
        created_by_id=created_by.id,
        token_hash=hash_token(raw),
        label=label,
        note=note,
        expires_at=moment + timedelta(days=days),
        allow_geometry_download=allow_geometry_download,
    )
    db.add(link)
    db.flush()
    return IssuedShare(link=link, token=raw)


def resolve(db: Session, token: str, *, now: datetime | None = None) -> ShareLink | None:
    """The live link this token opens, or None.

    None covers every refusal — see the module docstring. The caller answers
    404 and must not branch on why.
    """
    if not token:
        return None
    link = db.scalar(select(ShareLink).where(ShareLink.token_hash == hash_token(token)))
    if link is None or not link.is_live(now):
        return None
    return link


def record_view(db: Session, link: ShareLink, *, now: datetime | None = None) -> None:
    """Count one open.

    A counter rather than a row per view: the question an issuer actually asks
    is "has anybody opened this", and a row per hit on an unauthenticated route
    is an unbounded write anyone with the URL can drive.
    """
    link.view_count += 1
    link.last_viewed_at = now or utcnow()


def revoke(
    db: Session,
    link: ShareLink,
    reason: ShareRevocation = ShareRevocation.REVOKED,
    *,
    now: datetime | None = None,
) -> None:
    link.revoke(reason, now=now or utcnow())
    db.flush()


def live_links(db: Session, project: Project, *, now: datetime | None = None) -> list[ShareLink]:
    """Every link that would still open for this project, newest first."""
    moment = now or utcnow()
    rows = db.scalars(
        select(ShareLink)
        .where(ShareLink.project_id == project.id)
        .order_by(ShareLink.created_at.desc())
    ).all()
    return [row for row in rows if row.is_live(moment)]


def transfer(
    db: Session,
    *,
    project: Project,
    destination: Organisation,
    actor: User,
    reason: str | None = None,
    now: datetime | None = None,
) -> ProjectTransfer:
    """Move a project to another organisation, with its history.

    **Everything hanging off the project follows it** — geometry versions,
    simulations, conversations — because they are keyed by `project_id` and
    nothing about them is tenant-scoped independently. That is what makes this a
    move rather than a copy, and it is why `owner_id` is deliberately *not*
    changed: the plan calls it provenance, not permission, and rewriting it
    would erase who actually did the work.

    **Live share links are revoked**, and this is the subtle half. A link issued
    by the old tenant is a window into a project that now belongs to somebody
    else; leaving it open would let a supplier the previous owner trusted keep
    reading the new owner's work. `ShareLink.is_live` refuses on the
    organisation mismatch anyway — this makes the refusal explicit and gives it
    a reason a human can read.
    """
    moment = now or utcnow()
    if project.organisation_id == destination.id:
        raise SharingError(f"This project is already in {destination.name}.")

    record = ProjectTransfer(
        project_id=project.id,
        from_organisation_id=project.organisation_id,
        to_organisation_id=destination.id,
        transferred_by_id=actor.id,
        reason=reason,
    )
    for link in live_links(db, project, now=moment):
        link.revoke(ShareRevocation.TRANSFERRED, now=moment)

    project.organisation_id = destination.id
    db.add(record)
    db.flush()
    return record


def history(db: Session, project: Project) -> list[ProjectTransfer]:
    """Every tenant this project has belonged to, oldest first."""
    return list(
        db.scalars(
            select(ProjectTransfer)
            .where(ProjectTransfer.project_id == project.id)
            .order_by(ProjectTransfer.created_at)
        ).all()
    )


__all__ = [
    "DEFAULT_SHARE_DAYS",
    "MAX_SHARE_DAYS",
    "IssuedShare",
    "SharingError",
    "history",
    "issue",
    "live_links",
    "record_view",
    "resolve",
    "revoke",
    "transfer",
    "utcnow",
]
