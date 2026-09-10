"""The admin plane: who may act on the platform, as whom, and the record of it.

Three tables, and they belong together because none of them means anything
alone. `StaffGrant` says who is staff. `ImpersonationSession` says who is
currently acting as somebody else, and whether they may write. `AuditEvent` is
the record, and it is the only reason the first two are acceptable at all:
power that is bounded and recorded is support, power that is unrecorded is a
back door (P3.1--P3.3, Decision 7).

**The audit log is append-only, and that is structural rather than a promise.**
A row that can be updated or deleted is not an audit log -- it is a table that
happens to have history in it today. Four things enforce it, in decreasing
order of how much they are worth:

1. **A database trigger** (`append_only_statements` below), installed by the
   P3 migration and by `Base.metadata.create_all`, that raises on UPDATE and
   DELETE. This is the one that counts, because it applies to every path into
   the database -- the ORM, a hand-written `session.execute(text(...))`, a
   psql session, a future migration, a script somebody writes at 2am. Written
   for PostgreSQL *and* SQLite so the guarantee does not evaporate on the
   dialect the test suite runs on by default; a guard that is absent exactly
   where it is tested is not a guard.
2. **A statement-level TRUNCATE trigger.** `TRUNCATE` does not fire row
   triggers, so a table protected only by the rule above can still be emptied
   in one statement. Postgres only; SQLite has no TRUNCATE.
3. **The hash chain.** Every entry carries the previous entry's hash, so a row
   removed by somebody who *could* drop the trigger (the table's owner can)
   leaves a break that `verify_chain` finds. The trigger stops tampering; the
   chain makes tampering that got past it visible. Neither substitutes for the
   other, and saying which does which is the point.
4. **An ORM guard** (`_refuse_audit_mutation`), which turns the mistake into a
   Python exception at the flush that caused it rather than a database error at
   some later commit. Convenience, not security -- it is trivially bypassed by
   raw SQL, which is exactly what rule 1 is for.

**`audit_events` has no foreign keys, deliberately.** Every other table here
references `users` and lets a deletion cascade or null the column. Either would
be fatal on this one: `ON DELETE CASCADE` would let deleting an account erase
the record of what was done to it, and `ON DELETE SET NULL` is an *UPDATE*,
which the append-only trigger refuses -- so a GDPR purge (P3.4) would fail
against a table it must not silently rewrite anyway. The ids are stored as
plain strings, with the email denormalised beside them, so a row stays readable
after the account it names is gone. That is a feature of an audit log, not an
oversight of referential integrity.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Connection,
    Enum,
    ForeignKey,
    Index,
    String,
    event,
    select,
    text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from sqlalchemy.schema import Table

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey, utcnow
from app.models.types import JSONB_compat as JSONB

if TYPE_CHECKING:
    from app.models.user import User


# ---------------------------------------------------------------------------
# Staff (P3.2)
# ---------------------------------------------------------------------------


class StaffRole(str, enum.Enum):
    """What a member of Kryova's own staff may do to the platform.

    Ordered, and every check is "at least this role" -- the same shape as
    `OrgRole`, for the same reason: a set-membership test is a place for one
    route to be updated and another to be forgotten.

    **Staff status grants nothing inside a tenant.** It is not a membership and
    is never consulted by `require_organisation`; the only way for staff to see
    a customer's work is to impersonate a user who can, which is a recorded
    act. That separation is the whole of P3.2.
    """

    #: Read the console, start read-only impersonation. Cannot write anything.
    SUPPORT = "support"
    #: Support, plus operational mutations: retrying and failing stuck jobs.
    OPERATOR = "operator"
    #: All of it, including escalating an impersonation session to write mode.
    PLATFORM_ADMIN = "platform_admin"

    @property
    def rank(self) -> int:
        return _STAFF_RANK[self]

    def at_least(self, required: "StaffRole") -> bool:
        return self.rank >= required.rank


_STAFF_RANK: dict[StaffRole, int] = {
    StaffRole.SUPPORT: 0,
    StaffRole.OPERATOR: 1,
    StaffRole.PLATFORM_ADMIN: 2,
}


def _value_enum(enum_type: type[enum.Enum], length: int) -> Enum:
    """Store the lowercase *values*, not the member names.

    Identical to `organisation._role_enum` and identical in motivation: without
    `values_callable` SQLAlchemy writes `PLATFORM_ADMIN` while every payload,
    policy and migration in the codebase says `platform_admin`.
    """
    return Enum(
        enum_type,
        native_enum=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
    )


class StaffGrant(UUIDPrimaryKey, TimestampMixin, Base):
    """One person's staff standing, with the record of who gave it.

    A table rather than a column on `users`, because the interesting questions
    about staff power are historical -- who granted it, when, why, and when it
    was taken away -- and a column answers none of them.

    **There is deliberately no API that creates one.** An endpoint that mints
    staff grants is an endpoint that escalates whoever can reach it, and the
    first such caller would be whoever compromised a support account. Grants are
    provisioned out of band (a migration, or SQL run by a human with the
    database password) until P3's frontend lands a flow that is itself audited.
    """

    __tablename__ = "staff_grants"
    __table_args__ = (Index("ix_staff_grants_user_revoked", "user_id", "revoked_at"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[StaffRole] = mapped_column(_value_enum(StaffRole, 24))
    #: SET NULL, not CASCADE: deleting the account that granted staff standing
    #: must not erase the standing, and must not erase the fact it was granted.
    granted_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    reason: Mapped[str | None] = mapped_column(String(500), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])

    def is_live(self) -> bool:
        return self.revoked_at is None


def live_staff_grant(session: Session, user_id: str) -> StaffGrant | None:
    """This person's standing on the platform, or None if they have none.

    The *highest* live grant, not the first found: revoking one of two grants
    must lower somebody's power, and picking arbitrarily would make which one
    was revoked decide the answer. Ordered by role and then by age so the result
    is deterministic, which is what makes a 404 from `require_staff`
    reproducible.
    """
    grants = list(
        session.scalars(
            select(StaffGrant)
            .where(StaffGrant.user_id == user_id, StaffGrant.revoked_at.is_(None))
            .order_by(StaffGrant.created_at, StaffGrant.id)
        )
    )
    if not grants:
        return None
    return max(grants, key=lambda grant: grant.role.rank)


# ---------------------------------------------------------------------------
# Impersonation (P3.3)
# ---------------------------------------------------------------------------


class ImpersonationMode(str, enum.Enum):
    """Read-only unless somebody explicitly escalated, and never by default."""

    READ = "read"
    WRITE = "write"


class ImpersonationSession(UUIDPrimaryKey, TimestampMixin, Base):
    """A staff member acting as a user, for a bounded time, in a stated mode.

    A row rather than only a JWT claim, and the difference is not academic. A
    self-contained token cannot be ended: "stop impersonating" against a
    stateless credential is a note in a log, not a revocation, and describing it
    as one would be exactly the kind of claim Decision 3 forbids. With a row,
    `end` genuinely revokes, escalation is a state change somebody can be shown,
    and the read-only default is enforced by the database rather than by a claim
    the holder of the token is trusted not to have re-minted.

    Both identities live here and stay separate for the life of the session --
    `actor_user_id` is the staff member, `subject_user_id` is whose seat they
    are sitting in. Collapsing them into one "effective user" is the failure the
    whole phase exists to prevent.
    """

    __tablename__ = "impersonation_sessions"
    __table_args__ = (
        Index("ix_impersonation_actor_ended", "actor_user_id", "ended_at"),
        Index("ix_impersonation_subject_ended", "subject_user_id", "ended_at"),
    )

    actor_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    subject_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[ImpersonationMode] = mapped_column(
        _value_enum(ImpersonationMode, 8), default=ImpersonationMode.READ
    )
    #: Why the session was opened. Required at creation, and required *again*
    #: with a different value when it is escalated: "investigating ticket 41" is
    #: not a reason to be allowed to write.
    reason: Mapped[str] = mapped_column(String(500))
    escalation_reason: Mapped[str | None] = mapped_column(String(500), default=None)
    #: Time-boxed at creation and shortened on escalation. Never extended.
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    actor: Mapped["User"] = relationship(foreign_keys=[actor_user_id])
    subject: Mapped["User"] = relationship(foreign_keys=[subject_user_id])

    def is_live(self, now: datetime | None = None) -> bool:
        moment = now or utcnow()
        return self.ended_at is None and self.expires_at > moment

    @property
    def may_write(self) -> bool:
        return self.mode is ImpersonationMode.WRITE


# ---------------------------------------------------------------------------
# The log itself (P3.1)
# ---------------------------------------------------------------------------


class AuditAction(str, enum.Enum):
    """A closed vocabulary, because a free-text action cannot be queried.

    "Show me every impersonated write last month" has to be an index lookup, not
    a `LIKE`. Adding a member here is the deliberate act of deciding a new kind
    of thing is worth recording.
    """

    STAFF_GRANTED = "staff.granted"
    STAFF_REVOKED = "staff.revoked"

    IMPERSONATION_STARTED = "impersonation.started"
    IMPERSONATION_ESCALATED = "impersonation.escalated"
    IMPERSONATION_ENDED = "impersonation.ended"
    #: One row per request made while impersonating. P3.3 asks for exactly this:
    #: every request under an assumed identity is recorded with the staff actor.
    IMPERSONATED_REQUEST = "impersonation.request"
    #: A mutating request under a read-only session. The refusal is the single
    #: most valuable row in the table, so it is its own action rather than an
    #: outcome on the one above.
    IMPERSONATED_WRITE_REFUSED = "impersonation.write_refused"

    JOB_RETRIED = "job.retried"
    JOB_FAILED = "job.failed"

    QUOTA_READ = "quota.read"
    ADMIN_ACTION_REFUSED = "admin.refused"

    #: A project changing tenants (P2.5). Recorded here as well as in
    #: `project_transfers` because the two answer different questions: the
    #: table is provenance that travels with the project, this row is "what
    #: did this person do last month" and lives in the append-only chain.
    PROJECT_TRANSFERRED = "project.transferred"
    #: An account suspended or reinstated, and a deletion scheduled or
    #: cancelled (P3.4). Destructive and reversible respectively, and both
    #: are things an org owner is entitled to see happened to them.
    USER_SUSPENDED = "user.suspended"
    USER_REINSTATED = "user.reinstated"
    USER_DELETION_SCHEDULED = "user.deletion_scheduled"
    USER_DELETION_CANCELLED = "user.deletion_cancelled"
    USER_PURGED = "user.purged"
    #: Feature flags and maintenance mode (P3.5, P3.7): small switches with
    #: large blast radius, so who flipped one is worth keeping.
    FLAG_CHANGED = "flag.changed"
    MAINTENANCE_CHANGED = "maintenance.changed"
    ANNOUNCEMENT_PUBLISHED = "announcement.published"
    #: An approval gate approved, rejected, or refused a decision (P5.5). The
    #: refusals are recorded too — an attempt to approve one's own gate is
    #: precisely the event somebody comes looking for afterwards.
    GATE_DECIDED = "gate.decided"
    #: A run or an agent turn stopped on request (P5.6). Kept because "who
    #: stopped the overnight study" is a real question, and because a
    #: cancellation must never be mistaken for a failure.
    RUN_CANCELLED = "run.cancelled"


class AuditOutcome(str, enum.Enum):
    """What happened, and the four answers are deliberately not three.

    `PERMITTED` is not `SUCCEEDED`: a row written when a request is *admitted*
    says the door was opened, and says nothing about whether what came after it
    worked. Reporting admission as success is the same class of dishonesty as an
    unmeasured assertion passing, and the log is the last place to start.
    """

    SUCCEEDED = "succeeded"
    #: Declined by policy. The action did not happen, on purpose.
    REFUSED = "refused"
    #: Attempted and errored.
    FAILED = "failed"
    #: Admitted. The outcome of the work itself is not this row's claim.
    PERMITTED = "permitted"


#: Fields folded into an entry's hash, in a fixed order. Adding a column and
#: forgetting to add it here would leave that column outside the chain and
#: therefore silently editable-in-principle; `tests/test_audit.py` asserts this
#: list covers every column on the table.
_HASHED_FIELDS: tuple[str, ...] = (
    "id",
    "sequence",
    "occurred_at",
    "action",
    "outcome",
    "actor_user_id",
    "actor_email",
    "subject_user_id",
    "subject_email",
    "impersonated",
    "organisation_id",
    "target_type",
    "target_id",
    "ip_address",
    "user_agent",
    "reason",
    "detail",
    "previous_hash",
)


class AuditEvent(UUIDPrimaryKey, Base):
    """One thing that happened, and everything needed to interpret it later.

    **No `TimestampMixin`.** That mixin carries `updated_at` with an `onupdate`
    hook, and a column whose whole purpose is to record a row being changed has
    no business on a table that cannot be changed. `occurred_at` is the only
    time here, and it is when the event happened, not when the row was written.

    The row answers six questions, and the second is the one this phase exists
    for: **who acted** (`actor_*`), **as whom** (`subject_*`, `impersonated`),
    **what they did** (`action`), **to what** (`target_type`/`target_id`,
    `organisation_id`), **when** (`occurred_at`), **from where** (`ip_address`,
    `user_agent`), and **how it went** (`outcome`). An impersonated action that
    recorded only the subject would be indistinguishable from that user doing it
    themselves -- which is precisely the accusation an audit log has to be able
    to answer, so `actor_user_id` and `subject_user_id` are separate columns and
    are never written from a single "current user".
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_sequence", "sequence", unique=True),
        Index("ix_audit_events_occurred", "occurred_at"),
        Index("ix_audit_events_org_occurred", "organisation_id", "occurred_at"),
        Index("ix_audit_events_actor_occurred", "actor_user_id", "occurred_at"),
        Index("ix_audit_events_subject_occurred", "subject_user_id", "occurred_at"),
        Index("ix_audit_events_action_occurred", "action", "occurred_at"),
    )

    #: Position in the chain, 1-based and contiguous. Allocated by
    #: `AuditService` under a lock rather than by a database sequence, because a
    #: sequence has gaps by design (a rolled-back insert consumes a value) and a
    #: gap is indistinguishable from a deleted row when the chain is verified.
    sequence: Mapped[int] = mapped_column(BigInteger)

    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    action: Mapped[AuditAction] = mapped_column(_value_enum(AuditAction, 48))
    outcome: Mapped[AuditOutcome] = mapped_column(_value_enum(AuditOutcome, 16))

    #: Who really acted. Under impersonation this is the staff member, never the
    #: person whose seat they are in.
    actor_user_id: Mapped[str | None] = mapped_column(String(36), default=None)
    actor_email: Mapped[str | None] = mapped_column(String(320), default=None)

    #: Whose identity the action was taken under. Equal to the actor for
    #: ordinary work; different, and both present, under impersonation.
    subject_user_id: Mapped[str | None] = mapped_column(String(36), default=None)
    subject_email: Mapped[str | None] = mapped_column(String(320), default=None)

    #: Redundant with `actor_user_id != subject_user_id` and stored anyway: it
    #: is what an index serves and what a reader filters on, and deriving it in
    #: SQL means every consumer re-derives it and one of them gets it wrong.
    impersonated: Mapped[bool] = mapped_column(Boolean, default=False)

    #: The tenant the action touched, when it touched one. NULL means a
    #: platform-level event with no tenant -- which is why the org slice filters
    #: on equality and never on "not somebody else's".
    organisation_id: Mapped[str | None] = mapped_column(String(36), default=None)

    target_type: Mapped[str | None] = mapped_column(String(64), default=None)
    target_id: Mapped[str | None] = mapped_column(String(64), default=None)

    ip_address: Mapped[str | None] = mapped_column(String(45), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(400), default=None)

    #: The human justification, where one was required (impersonation, and its
    #: escalation). Never invented: a row with no reason says so.
    reason: Mapped[str | None] = mapped_column(String(500), default=None)

    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)

    #: The previous entry's `entry_hash`, or NULL for the first row ever.
    previous_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    #: SHA-256 over `_HASHED_FIELDS`, `previous_hash` included -- always 64
    #: hex characters, and the only column outside the digest, because a value
    #: cannot be part of what it is computed from.
    entry_hash: Mapped[str] = mapped_column(String(64))

    def digest_payload(self) -> str:
        """The exact bytes that are hashed, as a stable string.

        JSON with sorted keys and no whitespace, datetimes normalised to UTC
        ISO-8601, enums reduced to their values. Stability is the requirement:
        the same row read on Postgres and on SQLite, today and in five years,
        must produce the same string or the chain is noise.
        """
        payload = {name: _canonical(getattr(self, name)) for name in _HASHED_FIELDS}
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def compute_hash(self) -> str:
        return hashlib.sha256(self.digest_payload().encode("utf-8")).hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass(frozen=True)
class ChainVerification:
    """The answer to "has anybody been at the log", with the evidence.

    `intact` is not a bare boolean by itself on purpose: an operator being told
    "no" needs to know *which* link broke and how, or the finding is not
    actionable.
    """

    intact: bool
    checked: int
    broken_at: int | None = None
    problem: str | None = None

    @property
    def summary(self) -> str:
        if self.intact:
            return f"{self.checked} entries, chain intact"
        return f"broken at sequence {self.broken_at}: {self.problem}"


def verify_chain(events: list[AuditEvent]) -> ChainVerification:
    """Walk an ordered, complete run of entries and report the first break.

    Three distinct failures, and they are separate because they mean different
    things: a **gap** in `sequence` says a row is missing; a wrong
    `previous_hash` says a row was inserted or removed between two others; a
    wrong `entry_hash` says a row's contents were changed. The trigger should
    have made all three impossible, and this is what says so out loud.

    The argument must be the whole chain from sequence 1. A slice cannot be
    verified: its first entry's `previous_hash` refers to a row outside it, so
    verifying a page of the log would either report a false break or need a
    special case that is a hole to hide a row in.
    """
    previous_hash: str | None = None
    expected_sequence = 1
    for event_row in events:
        if event_row.sequence != expected_sequence:
            return ChainVerification(
                intact=False,
                checked=expected_sequence - 1,
                broken_at=event_row.sequence,
                problem=f"expected sequence {expected_sequence}, found {event_row.sequence}",
            )
        if event_row.previous_hash != previous_hash:
            return ChainVerification(
                intact=False,
                checked=expected_sequence - 1,
                broken_at=event_row.sequence,
                problem="previous_hash does not match the entry before it",
            )
        recomputed = event_row.compute_hash()
        if recomputed != event_row.entry_hash:
            return ChainVerification(
                intact=False,
                checked=expected_sequence - 1,
                broken_at=event_row.sequence,
                problem="entry_hash does not match the entry's contents",
            )
        previous_hash = event_row.entry_hash
        expected_sequence += 1
    return ChainVerification(intact=True, checked=expected_sequence - 1)


# ---------------------------------------------------------------------------
# Append-only, enforced by the database
# ---------------------------------------------------------------------------


class AppendOnlyViolation(RuntimeError):
    """Raised by the ORM guard. The database raises its own, independently."""


AUDIT_TABLE = "audit_events"
APPEND_ONLY_FUNCTION = "kryova_audit_append_only"
APPEND_ONLY_MESSAGE = "audit_events is append-only"


def append_only_statements(dialect: str, schema: str | None = None) -> list[str]:
    """The DDL that makes the table refuse to be changed, per dialect.

    Returned as a list of statements rather than one blob so a caller can run
    them through `op.execute` one at a time and see which failed. The P3
    migration carries its own copy of this SQL -- migrations must keep meaning
    the same thing after the application module has moved -- and
    `tests/test_audit.py` asserts the two agree, so the duplication cannot
    drift silently.
    """
    if dialect == "postgresql":
        qualified = f'"{schema}"."{AUDIT_TABLE}"' if schema else f'"{AUDIT_TABLE}"'
        function = f'"{schema}"."{APPEND_ONLY_FUNCTION}"' if schema else f'"{APPEND_ONLY_FUNCTION}"'
        return [
            f"CREATE OR REPLACE FUNCTION {function}() RETURNS trigger AS $$\n"
            f"BEGIN\n"
            # One `%`, not two. `%` is plpgsql's placeholder and `%%` is a
            # literal percent -- with `%%` the trigger would raise "too many
            # parameters specified for RAISE" instead of the message it means,
            # which still refuses the write but reports the wrong reason.
            # Safe as a bare `%` because this is executed with no bind
            # parameters, so neither SQLAlchemy nor psycopg does any
            # interpolation of its own on the way past.
            f"    RAISE EXCEPTION '{APPEND_ONLY_MESSAGE}: % is refused', TG_OP\n"
            f"        USING ERRCODE = 'restrict_violation';\n"
            f"    RETURN NULL;\n"
            f"END;\n"
            f"$$ LANGUAGE plpgsql",
            f"DROP TRIGGER IF EXISTS {AUDIT_TABLE}_append_only ON {qualified}",
            f"CREATE TRIGGER {AUDIT_TABLE}_append_only "
            f"BEFORE UPDATE OR DELETE ON {qualified} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()",
            # TRUNCATE fires no row triggers, so without this the table is
            # emptied by one statement that the rule above never sees.
            f"DROP TRIGGER IF EXISTS {AUDIT_TABLE}_no_truncate ON {qualified}",
            f"CREATE TRIGGER {AUDIT_TABLE}_no_truncate "
            f"BEFORE TRUNCATE ON {qualified} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {function}()",
            # Belt to the trigger's braces, and the part that survives somebody
            # dropping the trigger: no role but the owner gets to try at all.
            f"REVOKE UPDATE, DELETE, TRUNCATE ON {qualified} FROM PUBLIC",
        ]
    if dialect == "sqlite":
        return [
            f"CREATE TRIGGER IF NOT EXISTS {AUDIT_TABLE}_no_update "
            f"BEFORE UPDATE ON {AUDIT_TABLE} "
            f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}: UPDATE is refused'); END",
            f"CREATE TRIGGER IF NOT EXISTS {AUDIT_TABLE}_no_delete "
            f"BEFORE DELETE ON {AUDIT_TABLE} "
            f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}: DELETE is refused'); END",
        ]
    return []


def append_only_teardown_statements(dialect: str, schema: str | None = None) -> list[str]:
    if dialect == "postgresql":
        qualified = f'"{schema}"."{AUDIT_TABLE}"' if schema else f'"{AUDIT_TABLE}"'
        function = f'"{schema}"."{APPEND_ONLY_FUNCTION}"' if schema else f'"{APPEND_ONLY_FUNCTION}"'
        return [
            f"DROP TRIGGER IF EXISTS {AUDIT_TABLE}_no_truncate ON {qualified}",
            f"DROP TRIGGER IF EXISTS {AUDIT_TABLE}_append_only ON {qualified}",
            f"DROP FUNCTION IF EXISTS {function}()",
        ]
    if dialect == "sqlite":
        return [
            f"DROP TRIGGER IF EXISTS {AUDIT_TABLE}_no_update",
            f"DROP TRIGGER IF EXISTS {AUDIT_TABLE}_no_delete",
        ]
    return []


@event.listens_for(AuditEvent.__table__, "after_create")
def _install_append_only(target: Table, connection: Connection, **_kwargs: Any) -> None:
    """Install the triggers wherever the table is created.

    `Base.metadata.create_all` is how the test schema and a fresh developer
    database get their tables, and a table that is append-only in production and
    ordinary everywhere else is a guarantee nobody has ever seen work. The
    schema is read back from the connection rather than from settings because
    the test suite creates the table under a translated schema
    (`schema_translate_map`), and hard-coding `settings.db_schema` would install
    the trigger on the wrong table -- or on no table at all.
    """
    schema = connection.schema_for_object(target)
    for statement in append_only_statements(connection.dialect.name, schema):
        connection.execute(text(statement))


@event.listens_for(Session, "before_flush")
def _refuse_audit_mutation(session: Session, _context: Any, _instances: Any) -> None:
    """Fail an attempted change to a logged event at the flush that caused it.

    Convenience over the database's refusal, not a substitute for it: this sees
    only the ORM, and the database's trigger sees everything. It exists because
    an `AppendOnlyViolation` naming the row is a better bug report than a
    `restrict_violation` at commit time, three call frames away from the code
    that set the attribute.
    """
    for instance in session.deleted:
        if isinstance(instance, AuditEvent):
            raise AppendOnlyViolation(
                f"{APPEND_ONLY_MESSAGE}: refusing to delete audit event {instance.id}. "
                "Correct the record by appending a new entry, never by editing one."
            )
    for instance in session.dirty:
        if isinstance(instance, AuditEvent) and session.is_modified(
            instance, include_collections=False
        ):
            raise AppendOnlyViolation(
                f"{APPEND_ONLY_MESSAGE}: refusing to update audit event {instance.id}. "
                "Correct the record by appending a new entry, never by editing one."
            )


__all__ = [
    "APPEND_ONLY_MESSAGE",
    "AppendOnlyViolation",
    "AuditAction",
    "AuditEvent",
    "AuditOutcome",
    "ChainVerification",
    "ImpersonationMode",
    "ImpersonationSession",
    "StaffGrant",
    "StaffRole",
    "append_only_statements",
    "append_only_teardown_statements",
    "live_staff_grant",
    "verify_chain",
]
