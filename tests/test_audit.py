"""The audit log: append-only in the database, and chained so tampering shows.

Every guard here is verified by **breaking the thing it guards**, not by
watching it hold. The append-only tests drop the trigger inside the test's own
transaction, prove the same UPDATE then succeeds, and prove the hash chain
notices -- which is the only way to know that the refusal came from the trigger
rather than from a malformed statement, a missing table or a typo in the test.

These run on whatever `TEST_DATABASE_URL` resolves to, SQLite included, because
the append-only DDL is written for both dialects. That is deliberate: a
guarantee that is absent on the dialect the suite runs on by default is a
guarantee nobody has ever seen work.
"""

import importlib.util
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType
from typing import Iterator

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.routes.auth import _client_ip
from app.core.audit import STAFF_SETTING, AuditService, Principal, request_origin
from app.core.config import settings
from app.core.database import TENANT_SETTING
from app.models import User
from app.models.audit import (
    _HASHED_FIELDS,
    AppendOnlyViolation,
    AuditAction,
    AuditEvent,
    AuditOutcome,
    append_only_statements,
    append_only_teardown_statements,
    verify_chain,
)

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def audit(db_session: Session) -> AuditService:
    """The service, writing into the test's own session.

    In production the scope opens a *fresh* session so an entry survives the
    request's transaction being rolled back (`get_audit_service`). Here it has
    to be the test's session or the rows would be invisible to the assertions.
    """
    return AuditService(lambda: nullcontext(db_session))


@pytest.fixture
def actor(db_session: Session) -> User:
    user = User(email="ops@kryova.dev", hashed_password="x", full_name="Ops")
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def subject(db_session: Session) -> User:
    user = User(email="customer@example.com", hashed_password="x")
    db_session.add(user)
    db_session.flush()
    return user


def _qualified(session: Session) -> str:
    """`audit_events`, spelled the way raw SQL has to spell it on this dialect.

    The suite never sets `search_path` (see `app/core/database.py` for why), so
    a hand-written statement on Postgres must qualify the schema itself.
    """
    if session.get_bind().dialect.name == "postgresql":
        return f'"{settings.test_schema}"."audit_events"'
    return "audit_events"


def _dialect(session: Session) -> tuple[str, str | None]:
    name = session.get_bind().dialect.name
    return name, (settings.test_schema if name == "postgresql" else None)


@pytest.fixture
def without_the_trigger(db_session: Session) -> Iterator[None]:
    """Remove the append-only guard for the body of a test, then put it back.

    This is what "verify a guard by breaking the thing it guards" means here.
    The DDL is transactional on both PostgreSQL and SQLite, and the whole test
    runs inside a transaction the fixtures roll back, so the guard is restored
    even if the assertion fails -- but it is restored explicitly as well,
    because a suite that leaves a shared schema unprotected on one failure
    silently passes every later test in the file.
    """
    dialect, schema = _dialect(db_session)
    for statement in append_only_teardown_statements(dialect, schema):
        db_session.execute(text(statement))
    try:
        yield
    finally:
        for statement in append_only_statements(dialect, schema):
            db_session.execute(text(statement))


# ---------------------------------------------------------------------------


class TestTheLogCannotBeChanged:
    """The single most important property in P3: a row, once written, stands."""

    def test_the_database_refuses_an_update(
        self, audit: AuditService, db_session: Session, actor: User
    ) -> None:
        entry = audit.record(
            AuditAction.JOB_FAILED,
            AuditOutcome.SUCCEEDED,
            principal=Principal.of(actor),
            target_type="simulation_job",
            target_id="job-1",
        )
        savepoint = db_session.begin_nested()
        with pytest.raises(DBAPIError) as refused:
            db_session.execute(
                text(f"UPDATE {_qualified(db_session)} SET reason = 'nothing happened' WHERE id = :id"),
                {"id": entry.id},
            )
        savepoint.rollback()
        assert "append-only" in str(refused.value)

    def test_the_database_refuses_a_delete(
        self, audit: AuditService, db_session: Session, actor: User
    ) -> None:
        entry = audit.record(
            AuditAction.JOB_RETRIED, AuditOutcome.SUCCEEDED, principal=Principal.of(actor)
        )
        savepoint = db_session.begin_nested()
        with pytest.raises(DBAPIError) as refused:
            db_session.execute(
                text(f"DELETE FROM {_qualified(db_session)} WHERE id = :id"), {"id": entry.id}
            )
        savepoint.rollback()
        assert "append-only" in str(refused.value)

    def test_it_is_the_trigger_doing_it(
        self,
        audit: AuditService,
        db_session: Session,
        actor: User,
        without_the_trigger: None,
    ) -> None:
        """Break the guard and the same statement goes through.

        Without this, the two tests above prove only that *something* refuses --
        a misspelled table, a syntax error and a working trigger all raise. With
        the trigger dropped the UPDATE succeeds, so the refusal above is
        attributable to the trigger and to nothing else.
        """
        entry = audit.record(
            AuditAction.QUOTA_READ, AuditOutcome.PERMITTED, principal=Principal.of(actor)
        )
        db_session.execute(
            text(f"UPDATE {_qualified(db_session)} SET reason = 'tampered' WHERE id = :id"),
            {"id": entry.id},
        )
        db_session.expire_all()
        assert db_session.get(AuditEvent, entry.id).reason == "tampered"

    def test_a_row_changed_behind_the_trigger_breaks_the_chain(
        self,
        audit: AuditService,
        db_session: Session,
        actor: User,
        without_the_trigger: None,
    ) -> None:
        """The second half of the argument: prevention *and* detection.

        The trigger is the table's owner's to drop, and on Neon the application
        connects as that owner. So the honest claim is not "this cannot happen"
        but "this cannot happen quietly" -- which is what the hash chain is for.
        """
        for index in range(3):
            audit.record(
                AuditAction.QUOTA_READ,
                AuditOutcome.PERMITTED,
                principal=Principal.of(actor),
                target_id=f"target-{index}",
            )
        assert audit.verify().intact

        second = db_session.scalars(
            text(f"SELECT id FROM {_qualified(db_session)} WHERE sequence = 2")
        ).one()
        db_session.execute(
            text(f"UPDATE {_qualified(db_session)} SET reason = 'quietly edited' WHERE id = :id"),
            {"id": second},
        )
        db_session.expire_all()

        verification = audit.verify()
        assert not verification.intact
        assert verification.broken_at == 2
        assert "entry_hash" in (verification.problem or "")

    def test_a_row_removed_behind_the_trigger_breaks_the_chain(
        self,
        audit: AuditService,
        db_session: Session,
        actor: User,
        without_the_trigger: None,
    ) -> None:
        for index in range(3):
            audit.record(
                AuditAction.QUOTA_READ,
                AuditOutcome.PERMITTED,
                principal=Principal.of(actor),
                target_id=f"target-{index}",
            )
        db_session.execute(text(f"DELETE FROM {_qualified(db_session)} WHERE sequence = 2"))
        db_session.expire_all()

        verification = audit.verify()
        assert not verification.intact
        # The gap is found before the hash mismatch is: a missing row and an
        # edited row are different accusations and must not be confused.
        assert verification.broken_at == 3
        assert "sequence" in (verification.problem or "")

    def test_the_orm_refuses_an_update_before_the_database_has_to(
        self, audit: AuditService, db_session: Session, actor: User
    ) -> None:
        entry = audit.record(
            AuditAction.JOB_FAILED, AuditOutcome.FAILED, principal=Principal.of(actor)
        )
        entry.reason = "let me just fix that"
        with pytest.raises(AppendOnlyViolation) as refused:
            db_session.flush()
        db_session.rollback()
        assert entry.id in str(refused.value)

    def test_the_orm_refuses_a_delete(
        self, audit: AuditService, db_session: Session, actor: User
    ) -> None:
        entry = audit.record(
            AuditAction.JOB_FAILED, AuditOutcome.FAILED, principal=Principal.of(actor)
        )
        db_session.delete(entry)
        with pytest.raises(AppendOnlyViolation):
            db_session.flush()
        db_session.rollback()

    def test_the_service_offers_no_way_to_change_anything(self) -> None:
        """There is no `update`, no `delete`, no `redact` -- by construction.

        A method that always raises would only advertise the idea; the absence
        is the design.
        """
        surface = {name for name in dir(AuditService) if not name.startswith("_")}
        assert surface == {"record", "verify"}


class TestTheGuardIsWhereTheTableIs:
    def test_the_trigger_exists_in_the_schema_the_tests_use(self, db_session: Session) -> None:
        """`create_all` installs it, not only the migration.

        A table that is append-only in production and ordinary in the test
        schema would make every assertion in this file a statement about
        nothing.
        """
        dialect, schema = _dialect(db_session)
        if dialect == "sqlite":
            names = set(
                db_session.scalars(
                    text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
                )
            )
            assert {"audit_events_no_update", "audit_events_no_delete"} <= names
        else:
            names = set(
                db_session.scalars(
                    text(
                        "SELECT tgname FROM pg_trigger t "
                        "JOIN pg_class c ON c.oid = t.tgrelid "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE c.relname = 'audit_events' AND n.nspname = :schema"
                    ),
                    {"schema": schema},
                )
            )
            assert {"audit_events_append_only", "audit_events_no_truncate"} <= names

    def test_the_table_carries_no_foreign_keys(self, db_session: Session) -> None:
        """A cascade would erase the record; a SET NULL is an UPDATE the trigger
        refuses. Either would be a way for deleting an account to rewrite what
        was done to it."""
        inspector = inspect(db_session.get_bind())
        _, schema = _dialect(db_session)
        assert inspector.get_foreign_keys("audit_events", schema=schema) == []


class TestTheChain:
    def test_entries_chain_and_are_contiguous(
        self, audit: AuditService, actor: User
    ) -> None:
        entries = [
            audit.record(
                AuditAction.QUOTA_READ, AuditOutcome.PERMITTED, principal=Principal.of(actor)
            )
            for _ in range(4)
        ]
        assert [entry.sequence for entry in entries] == [1, 2, 3, 4]
        assert entries[0].previous_hash is None
        for earlier, later in zip(entries, entries[1:]):
            assert later.previous_hash == earlier.entry_hash
        assert audit.verify().intact

    def test_every_column_is_inside_the_digest(self) -> None:
        """A column outside the hash is a column that can be edited invisibly.

        This is the test that fails when somebody adds a field to `AuditEvent`
        and forgets `_HASHED_FIELDS` -- which is exactly the change that would
        put a hole in the chain without anything else noticing.
        """
        columns = {column.name for column in AuditEvent.__table__.columns}
        assert columns - {"entry_hash"} == set(_HASHED_FIELDS)

    def test_a_tampered_copy_is_detected_without_touching_the_database(
        self, audit: AuditService, actor: User, db_session: Session
    ) -> None:
        for index in range(3):
            audit.record(
                AuditAction.QUOTA_READ,
                AuditOutcome.PERMITTED,
                principal=Principal.of(actor),
                target_id=str(index),
            )
        rows = list(db_session.query(AuditEvent).order_by(AuditEvent.sequence))
        assert verify_chain(rows).intact

        rows[1].target_id = "somebody else"
        broken = verify_chain(rows)
        assert not broken.intact and broken.broken_at == 2

    def test_the_digest_is_stable_across_reads(
        self, audit: AuditService, actor: User, db_session: Session
    ) -> None:
        """Recomputing from a row read back must give the same hash.

        This is where a timezone-naive datetime or an enum stored by name would
        show up: the value written and the value read would differ, and every
        verification would report tampering that never happened.
        """
        entry = audit.record(
            AuditAction.IMPERSONATION_STARTED,
            AuditOutcome.SUCCEEDED,
            principal=Principal.of(actor),
            reason="ticket 41",
            detail={"mode": "read", "nested": {"b": 2, "a": 1}},
        )
        db_session.expire_all()
        reloaded = db_session.get(AuditEvent, entry.id)
        assert reloaded is not None
        assert reloaded.compute_hash() == reloaded.entry_hash


class TestBothIdentities:
    def test_an_ordinary_principal_records_one_person_twice(
        self, audit: AuditService, actor: User
    ) -> None:
        entry = audit.record(
            AuditAction.JOB_RETRIED, AuditOutcome.SUCCEEDED, principal=Principal.of(actor)
        )
        assert entry.actor_user_id == entry.subject_user_id == actor.id
        assert entry.impersonated is False

    def test_an_impersonated_principal_records_two(
        self, audit: AuditService, actor: User, subject: User
    ) -> None:
        principal = Principal(
            actor_user_id=actor.id,
            actor_email=actor.email,
            subject_user_id=subject.id,
            subject_email=subject.email,
            impersonated=True,
            may_write=False,
        )
        entry = audit.record(
            AuditAction.IMPERSONATED_REQUEST, AuditOutcome.PERMITTED, principal=principal
        )
        assert entry.actor_user_id == actor.id
        assert entry.subject_user_id == subject.id
        assert entry.actor_user_id != entry.subject_user_id
        assert entry.impersonated is True
        # And the emails, so the row still reads after either account is gone.
        assert entry.actor_email == actor.email
        assert entry.subject_email == subject.email

    def test_the_row_has_no_single_user_column_to_collapse_into(self) -> None:
        columns = {column.name for column in AuditEvent.__table__.columns}
        assert "user_id" not in columns
        assert {"actor_user_id", "subject_user_id"} <= columns


class TestTheMigrationAndTheModelAgree:
    """The append-only SQL is written twice on purpose. It may not drift."""

    @staticmethod
    def _p3_migration() -> ModuleType:
        for path in sorted(VERSIONS.glob("*.py")):
            if "audit_rls_statements" not in path.read_text(encoding="utf-8"):
                continue
            spec = importlib.util.spec_from_file_location(f"_p3_{path.stem}", path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
        raise AssertionError("No migration defines audit_rls_statements(); the P3 DDL is gone.")

    def test_the_append_only_sql_is_identical(self) -> None:
        migration = self._p3_migration()
        assert migration.append_only_statements("kryova") == append_only_statements(
            "postgresql", "kryova"
        )
        assert migration.append_only_teardown_statements(
            "kryova"
        ) == append_only_teardown_statements("postgresql", "kryova")

    def test_the_settings_the_policy_reads_are_the_ones_the_app_publishes(self) -> None:
        """A typo here is a policy that never matches and therefore never
        protects -- the same failure the P2 test guards against."""
        migration = self._p3_migration()
        assert migration.STAFF_SETTING == STAFF_SETTING
        assert migration.TENANT_SETTING == TENANT_SETTING

    def test_the_policy_lets_staff_read_and_never_blocks_an_append(self) -> None:
        migration = self._p3_migration()
        policy = "\n".join(migration.audit_rls_statements("kryova"))
        assert f"current_setting('{STAFF_SETTING}', true) = 'on'" in policy
        # Appending must never be refusable by tenant context: a log that can be
        # blocked by putting a request in the wrong tenant can be suppressed.
        assert "WITH CHECK (true)" in policy

    def test_the_raise_uses_one_percent_not_two(self) -> None:
        """`%%` would raise "too many parameters specified for RAISE" instead of
        the message it means -- still a refusal, but reporting the wrong reason,
        which is how a guard stops being readable."""
        create = append_only_statements("postgresql", "kryova")[0]
        assert "% is refused', TG_OP" in create
        assert "%%" not in create


class TestWhereTheRequestCameFrom:
    """`request_origin` is a copy of the rate limiter's `_client_ip`. Pin it."""

    @staticmethod
    def _request(headers: dict[str, str], client: tuple[str, int] | None = ("10.0.0.1", 1)):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": client,
        }
        return Request(scope)

    def test_the_socket_address_is_used_when_no_proxy_is_declared(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "trust_proxy_headers", False)
        request = self._request({"x-forwarded-for": "1.2.3.4", "user-agent": "curl"})
        address, agent = request_origin(request)
        assert address == "10.0.0.1" == _client_ip(request)
        assert agent == "curl"

    def test_a_declared_proxy_chain_is_counted_from_the_right(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "trust_proxy_headers", True)
        monkeypatch.setattr(settings, "trusted_proxy_count", 1)
        # One trusted proxy appends the address it saw, so the *rightmost* entry
        # is the real client and everything to its left was written by the
        # caller. A forged `1.2.3.4` at the front must not reach the log.
        request = self._request({"x-forwarded-for": "1.2.3.4, 5.6.7.8"})
        address, _ = request_origin(request)
        assert address == "5.6.7.8" == _client_ip(request)

    def test_a_shorter_chain_than_configured_falls_back_to_the_socket(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(settings, "trust_proxy_headers", True)
        monkeypatch.setattr(settings, "trusted_proxy_count", 3)
        request = self._request({"x-forwarded-for": "1.2.3.4"})
        address, _ = request_origin(request)
        assert address == "10.0.0.1" == _client_ip(request)


class TestTheLogIsWrittenOutsideTheRequest:
    def test_the_service_is_built_from_a_session_scope_not_the_request_session(self) -> None:
        """A refused action usually ends in a rollback, and a log written into
        the transaction being rolled back is not a log. `get_audit_service` must
        take the same seam background jobs use."""
        from app.api.deps import SessionScopeDep, get_audit_service

        annotations = get_audit_service.__annotations__
        assert annotations["scope"] is SessionScopeDep
