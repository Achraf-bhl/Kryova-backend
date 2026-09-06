"""Writing the audit log, and the identity that every entry is written from.

`app/models/audit.py` says what a row *is* and makes it impossible to change;
this says how one is appended and who is recorded on it. The split is the same
one the rest of the codebase keeps between a model and a service, with one
addition that matters:

**The log is written in its own transaction, not the request's.** A refused
action is exactly the entry you most want, and a refused action usually ends in
an exception, a rollback, or both -- so a log written into the request's
transaction would be rolled back by the very failure it exists to record. The
session comes from an injected `SessionScope`, the same seam background jobs
use, so the write commits independently of whatever the request goes on to do.
The tests override that scope onto their own session, which is how the rows are
visible inside a test transaction.
"""

import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import Request
from sqlalchemy import Connection, event, func, select, text
from sqlalchemy.orm import Session, SessionTransaction

from app.core.config import settings
from app.models.audit import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
    ChainVerification,
    verify_chain,
)
from app.models.base import new_uuid, utcnow
from app.models.user import User
from app.simulation.runner import SessionScope

logger = logging.getLogger(__name__)

#: HTTP methods that cannot change anything, and therefore the exact set a
#: read-only impersonation session is allowed to use. A method not named here is
#: treated as a write, which is the safe direction to be wrong in: an unknown
#: verb is refused rather than waved through.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: A second GUC beside `kryova.organisation_ids`, published the same way and for
#: the same reason -- see `app.core.database.tenant_scope` for the full argument
#: about why `SET LOCAL` is the one sanctioned `SET` on a pooled endpoint. This
#: one says the connection is doing platform work on behalf of verified staff,
#: which is what lets the audit log's RLS policy hand the whole table to the
#: operations console while still confining an org owner to their own slice.
#:
#: It is published by `require_staff` and by nothing else, *after* the grant has
#: been read from the database. It is never published for an impersonated
#: principal: a staff member sitting in a user's seat sees exactly what that
#: user sees, which is the entire point of impersonating rather than reading.
STAFF_SETTING = "kryova.staff"
STAFF_ON = "on"


def request_origin(request: Request) -> tuple[str | None, str | None]:
    """Where the request came from: (address, user agent).

    The address is resolved exactly as the rate limiter's `_client_ip` resolves
    it, and for the same reason: `X-Forwarded-For` is written by whoever sends
    the request, so believing it unconditionally lets a caller write whatever
    address they like into the audit log. It is read only when a reverse proxy
    is declared to be in front, and then counted from the right-hand end.

    Deliberately a copy rather than an import of `routes.auth._client_ip`: the
    routes layer imports this module's service through `deps`, so importing back
    the other way is a cycle. `tests/test_audit.py` asserts the two agree on the
    cases that distinguish them, which is what stops the copy drifting.
    """
    peer = request.client.host if request.client else None
    user_agent = (request.headers.get("user-agent") or None) or None
    if user_agent is not None:
        user_agent = user_agent[:400]

    if not settings.trust_proxy_headers:
        return peer, user_agent
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer, user_agent
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    index = len(hops) - settings.trusted_proxy_count
    if not hops or index < 0:
        return peer, user_agent
    return hops[index], user_agent


@dataclass(frozen=True)
class Principal:
    """Who is making this request, in both halves, always.

    `actor` is the human whose credentials were presented. `subject` is the
    identity the work is done under. For ordinary traffic they are the same
    person and `impersonated` is False; under impersonation they differ and
    **both are carried everywhere**, because an impersonated action recorded
    against the subject alone is indistinguishable from that user doing it
    themselves, which is the accusation this phase has to be able to answer.

    `may_write` is False by default for an impersonated principal and True for
    an ordinary one -- read-only is not a mode staff opt into, it is the only
    mode they get until somebody escalates the session on the record.
    """

    actor_user_id: str
    actor_email: str | None
    subject_user_id: str
    subject_email: str | None
    impersonated: bool = False
    may_write: bool = True
    session_id: str | None = None
    reason: str | None = None
    expires_at: datetime | None = None
    ip_address: str | None = None
    user_agent: str | None = None

    @classmethod
    def of(
        cls, user: User, ip_address: str | None = None, user_agent: str | None = None
    ) -> "Principal":
        """An ordinary, non-impersonated principal: actor and subject are one."""
        return cls(
            actor_user_id=user.id,
            actor_email=user.email,
            subject_user_id=user.id,
            subject_email=user.email,
            impersonated=False,
            may_write=True,
            ip_address=ip_address,
            user_agent=user_agent,
        )


class AuditService:
    """Append to the log. There is no other operation, by construction.

    No `update`, no `delete`, no `redact`. A correction to the record is a new
    entry that says what was wrong; the database would refuse anything else
    anyway (`app/models/audit.py`), and offering a method that always raises
    would only invite somebody to look for the flag that turns it off.
    """

    def __init__(self, scope: SessionScope) -> None:
        self._scope = scope

    # -- writing ---------------------------------------------------------

    def record(
        self,
        action: AuditAction,
        outcome: AuditOutcome,
        *,
        principal: Principal | None = None,
        organisation_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> AuditEvent:
        """Append one entry, chained to the one before it, and commit it.

        Both identities come off the `Principal` and are never collapsed: an
        anonymous or system event leaves them null, which is honest, and an
        impersonated one fills in four columns rather than two.
        """
        with self._scope() as session:
            self._lock(session)
            tip = session.scalars(
                select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(1)
            ).first()
            entry = AuditEvent(
                sequence=(tip.sequence + 1) if tip is not None else 1,
                previous_hash=tip.entry_hash if tip is not None else None,
                action=action,
                outcome=outcome,
                actor_user_id=principal.actor_user_id if principal else None,
                actor_email=principal.actor_email if principal else None,
                subject_user_id=principal.subject_user_id if principal else None,
                subject_email=principal.subject_email if principal else None,
                impersonated=bool(principal and principal.impersonated),
                organisation_id=organisation_id,
                target_type=target_type,
                target_id=target_id,
                ip_address=principal.ip_address if principal else None,
                user_agent=principal.user_agent if principal else None,
                reason=reason,
                detail=detail,
            )
            # The id has to be *materialised* now, not left to the flush.
            # `UUIDPrimaryKey` supplies it with a Python-side `default=`, which
            # SQLAlchemy applies during the flush -- so a freshly constructed
            # row still has `id is None`, and hashing it here would fold a null
            # into the digest and a uuid into the stored row. Every entry then
            # fails its own verification the moment it is read back, which is a
            # log that reports tampering on itself. Assigning it is not a second
            # source of truth: it is the same `new_uuid()` the column default
            # would have called moments later. (`personal_slug` carries the same
            # note for the same trap.)
            if entry.id is None:
                entry.id = new_uuid()
            if occurred_at is not None:
                entry.occurred_at = occurred_at
            elif entry.occurred_at is None:
                # `default=utcnow` is applied at flush; the hash is computed
                # before that, so the value has to exist now or the row's own
                # timestamp would not be inside its own digest.
                entry.occurred_at = utcnow()
            entry.entry_hash = entry.compute_hash()
            session.add(entry)
            session.commit()
            return entry

    def _lock(self, session: Session) -> None:
        """Serialise appends, so two of them cannot chain onto the same tip.

        Without this, two concurrent writers both read the same last entry, both
        claim the next sequence number, and the chain forks -- and a fork is
        indistinguishable from a deletion when it is verified, so the failure
        mode is a log that reports tampering that never happened. A
        transaction-scoped advisory lock is the cheapest correct answer on
        Postgres: it takes no row locks, contends with nothing else, and is
        released at COMMIT, which is the same boundary PgBouncer may reassign
        the connection at.

        SQLite serialises writers by itself, so there is nothing to do there.
        """
        if session.get_bind().dialect.name != "postgresql":
            return
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _AUDIT_LOCK_KEY})

    # -- reading ---------------------------------------------------------

    def verify(self) -> ChainVerification:
        """Verify the whole chain, from the first entry ever written.

        The whole chain and never a page of it: a slice's first entry points at
        a hash outside the slice, so a partial verification would either report
        a false break or need an exception that is a hole to hide a row in.
        """
        with self._scope() as session:
            entries = list(session.scalars(select(AuditEvent).order_by(AuditEvent.sequence)))
            return verify_chain(entries)


#: An arbitrary but fixed key for the advisory lock. Any constant works; it only
#: has to be the same one in every process appending to this table.
_AUDIT_LOCK_KEY = 0x4B52594F5641_01 & 0x7FFFFFFFFFFFFFFF


@contextmanager
def staff_scope(session: Session) -> Iterator[None]:
    """Publish "this connection is doing verified staff work" for the transaction.

    A near-copy of `app.core.database.tenant_scope`, and deliberately so: the
    `SET LOCAL` argument there applies here word for word, including why the
    value is re-published on `after_begin` rather than once (a `commit()`
    discards it, and SQLAlchemy autobegins the next transaction without it).
    Read that docstring before changing this one.

    The value is only ever `on`, and it is only ever published after a live
    `StaffGrant` has been read. It is never published while impersonating.
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        yield
        return

    def _publish(connection: Connection) -> None:
        connection.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": STAFF_SETTING, "value": STAFF_ON},
        )

    def _on_begin(
        _session: Session, _transaction: SessionTransaction, connection: Connection
    ) -> None:
        _publish(connection)

    event.listen(session, "after_begin", _on_begin)
    try:
        if session.in_transaction():
            _publish(session.connection())
        yield
    finally:
        event.remove(session, "after_begin", _on_begin)


def current_staff_setting(session: Session) -> str | None:
    """What Postgres currently believes about staff standing, or None.

    Exists for the tests, for the reason `current_tenant_setting` exists: a
    guard nobody can observe is a guard nobody notices the loss of.
    """
    if session.get_bind().dialect.name != "postgresql":
        return None
    return session.scalar(text("SELECT current_setting(:name, true)"), {"name": STAFF_SETTING})


def audit_page(
    session: Session,
    *,
    organisation_id: str | None = None,
    actor_user_id: str | None = None,
    subject_user_id: str | None = None,
    action: AuditAction | None = None,
    impersonated_only: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> tuple[int, Iterable[AuditEvent]]:
    """One page of the log, newest first, with the total.

    `organisation_id` is an equality filter and never a negation: rows with no
    tenant are platform events and belong to nobody's slice, so an org owner
    reading their own audit sees their organisation's rows and nothing else --
    including nothing about the platform.
    """
    conditions = []
    if organisation_id is not None:
        conditions.append(AuditEvent.organisation_id == organisation_id)
    if actor_user_id is not None:
        conditions.append(AuditEvent.actor_user_id == actor_user_id)
    if subject_user_id is not None:
        conditions.append(AuditEvent.subject_user_id == subject_user_id)
    if action is not None:
        conditions.append(AuditEvent.action == action)
    if impersonated_only:
        conditions.append(AuditEvent.impersonated.is_(True))

    total = session.scalar(select(func.count()).select_from(AuditEvent).where(*conditions)) or 0
    rows = session.scalars(
        select(AuditEvent)
        .where(*conditions)
        .order_by(AuditEvent.sequence.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return total, rows


__all__ = [
    "SAFE_METHODS",
    "STAFF_SETTING",
    "AuditService",
    "Principal",
    "audit_page",
    "current_staff_setting",
    "request_origin",
    "staff_scope",
]
