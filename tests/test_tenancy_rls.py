"""Row-level security: what is left when the application's scoping is wrong.

`test_tenancy.py` tests the application deciding correctly. This file removes
that decision on purpose -- it runs queries with no tenant predicate at all,
and with the *wrong* one -- and asserts the database refuses anyway. That is
the whole claim of Decision 7: RLS is the safety net **under** application
scoping, for the day the application is wrong.

PostgreSQL only, and it skips rather than lies when the suite is running on
SQLite (`TEST_DATABASE_URL` unset). A skipped test says so; a test that quietly
passes on a database with no policies is worse than no test.

**These run as a role that cannot bypass RLS, and they have to.** Neon's
`neondb_owner` -- the role the application connects as -- is created with
`BYPASSRLS`, which outranks both `ENABLE` and `FORCE ROW LEVEL SECURITY`, and
made every assertion below pass vacuously the first time they were run. So the
isolation tests `SET LOCAL ROLE` into a probe role created without it. What
that proves is that the *policies* are right; what it does not prove, and what
`test_the_application_role_must_not_bypass_row_level_security` says out loud,
is that the deployment is. The fix is in `connected_role_bypasses_rls`.

The DDL exercised here is imported from the P2 migration itself rather than
retyped, so what is proven is the policy that ships. A hand-copied policy in a
test proves the copy works.
"""

import importlib.util
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Connection, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import (
    NO_TENANTS,
    TENANT_SETTING,
    connected_role_bypasses_rls,
    current_tenant_setting,
    tenant_scope,
)
from app.models import (
    GeometryVersion,
    Media,
    MediaKind,
    Membership,
    Organisation,
    OrgRole,
    Project,
    User,
)

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"

#: A login-less role without BYPASSRLS, created by the fixture below. Tests
#: switch into it with SET LOCAL ROLE -- transaction-scoped for the same reason
#: SET LOCAL is, so it cannot outlive the test that asked for it.
PROBE_ROLE = "kryova_rls_probe"


@contextmanager
def as_tenant(session: Session, organisation_ids: list[str]) -> Iterator[None]:
    """Run the block as a member of these organisations and nothing else.

    Two switches, both transaction-local: the role, so RLS applies at all, and
    the tenant setting, so it applies to these tenants. Neither is reset on
    exit -- the surrounding test transaction is rolled back anyway, and an
    explicit reset would itself fail in the one test that deliberately aborts
    its transaction.
    """
    session.execute(text(f"SET LOCAL ROLE {PROBE_ROLE}"))
    with tenant_scope(session, organisation_ids):
        yield


def _p2_migration() -> ModuleType:
    """The migration module that owns the RLS DDL, found by what it defines.

    By `rls_statements`, not by filename: a revision file gets renamed when
    somebody rewords the message, and a test that breaks on a reword is a test
    people delete.
    """
    for path in sorted(VERSIONS.glob("*.py")):
        if "rls_statements" not in path.read_text(encoding="utf-8"):
            continue
        spec = importlib.util.spec_from_file_location(f"_p2_{path.stem}", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    raise AssertionError("No migration defines rls_statements(); the P2 policies are gone.")


@pytest.fixture(scope="module")
def migration() -> ModuleType:
    return _p2_migration()


@pytest.fixture(scope="module")
def rls(db_connection: Connection, migration: ModuleType) -> Iterator[str]:
    """Apply the shipped RLS policies to the test schema.

    Module-scoped so it lands before any test's transaction opens: this is DDL,
    and the per-test transaction is rolled back.
    """
    if db_connection.dialect.name != "postgresql":
        pytest.skip("RLS is a PostgreSQL feature; set TEST_DATABASE_URL to a Postgres database")

    schema = settings.test_schema
    for statement in migration.rls_statements(schema):
        db_connection.execute(text(statement))

    # The probe role is what makes every assertion below mean anything on a
    # database whose owning role holds BYPASSRLS. Created idempotently: roles
    # are cluster-wide, so the next run finds it already there.
    db_connection.execute(
        text(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{PROBE_ROLE}') THEN CREATE ROLE {PROBE_ROLE} NOLOGIN NOBYPASSRLS; "
            "END IF; END $$"
        )
    )
    # Membership in the role, or SET ROLE is refused. Automatic for the creator
    # from PostgreSQL 16; explicit here because it is not, on 15 and earlier.
    db_connection.execute(text(f"GRANT {PROBE_ROLE} TO CURRENT_USER"))
    db_connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {PROBE_ROLE}'))
    db_connection.execute(
        text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "
            f'"{schema}" TO {PROBE_ROLE}'
        )
    )
    db_connection.commit()
    try:
        yield schema
    finally:
        for statement in migration.rls_teardown_statements(schema):
            db_connection.execute(text(statement))
        db_connection.commit()


class Tenants:
    """Two organisations, two users, one project each."""

    def __init__(self, session: Session) -> None:
        self.alice = User(email="alice@kryova.dev", hashed_password="x")
        self.bob = User(email="bob@kryova.dev", hashed_password="x")
        session.add_all([self.alice, self.bob])
        session.flush()

        self.org_a = Organisation(name="Alpha Machines", slug="alpha", is_personal=False)
        self.org_b = Organisation(name="Beta Machines", slug="beta", is_personal=False)
        session.add_all([self.org_a, self.org_b])
        session.flush()

        session.add_all(
            [
                Membership(
                    organisation_id=self.org_a.id, user_id=self.alice.id, role=OrgRole.OWNER
                ),
                Membership(organisation_id=self.org_b.id, user_id=self.bob.id, role=OrgRole.OWNER),
            ]
        )
        self.project_a = Project(
            name="Alpha press", owner_id=self.alice.id, organisation_id=self.org_a.id
        )
        self.project_b = Project(
            name="Beta gearbox", owner_id=self.bob.id, organisation_id=self.org_b.id
        )
        session.add_all([self.project_a, self.project_b])
        session.flush()


@pytest.fixture
def tenants(rls: str, db_session: Session) -> Tenants:
    """Two tenants, created with no tenant context -- which the policies allow.

    That makes the setup itself a small demonstration of the trade-off the
    migration documents: with no context the policies stand aside, so fixtures,
    migrations and background jobs keep working.
    """
    return Tenants(db_session)


class TestTheDatabaseRefusesWhatTheApplicationForgot:
    def test_an_unscoped_select_returns_only_the_callers_tenant(
        self, tenants: Tenants, db_session: Session
    ) -> None:
        """`select(Project)` with no WHERE clause at all -- the query somebody
        forgets to scope. Application scoping has been removed on purpose."""
        with as_tenant(db_session, [tenants.org_a.id]):
            visible = list(db_session.scalars(select(Project)))

        assert [project.name for project in visible] == ["Alpha press"]

    def test_a_deliberately_wrong_scope_still_cannot_reach_the_other_tenant(
        self, tenants: Tenants, db_session: Session
    ) -> None:
        """The application asks for Bob's project by id while the request
        belongs to Alice -- exactly the bug `get_owned_project` exists to
        prevent, with `get_owned_project` taken out of the way."""
        wanted = tenants.project_b.id
        with as_tenant(db_session, [tenants.org_a.id]):
            found = db_session.scalars(select(Project).where(Project.id == wanted)).first()

        assert found is None

    def test_the_organisation_row_itself_is_invisible(
        self, tenants: Tenants, db_session: Session
    ) -> None:
        with as_tenant(db_session, [tenants.org_a.id]):
            slugs = {org.slug for org in db_session.scalars(select(Organisation))}
        assert slugs == {"alpha"}

    def test_memberships_do_not_leak_the_other_tenants_people(
        self, tenants: Tenants, db_session: Session
    ) -> None:
        with as_tenant(db_session, [tenants.org_a.id]):
            rows = list(db_session.scalars(select(Membership)))
        assert {row.user_id for row in rows} == {tenants.alice.id}

    def test_a_child_row_is_invisible_when_its_project_is(
        self, tenants: Tenants, db_session: Session
    ) -> None:
        """`geometry_versions` carries no organisation column; it reaches its
        tenant through `projects`, and the policy has to follow."""
        blob = Media(
            owner_id=tenants.bob.id,
            kind=MediaKind.CAD,
            filename="gearbox.stl",
            size_bytes=1,
            sha256="0" * 64,
        )
        db_session.add(blob)
        db_session.flush()
        db_session.add(
            GeometryVersion(
                project_id=tenants.project_b.id,
                media_id=blob.id,
                version_number=1,
                filename="gearbox.stl",
                file_format="stl",
            )
        )
        db_session.flush()

        with as_tenant(db_session, [tenants.org_a.id]):
            assert list(db_session.scalars(select(GeometryVersion))) == []
        with as_tenant(db_session, [tenants.org_b.id]):
            assert len(list(db_session.scalars(select(GeometryVersion)))) == 1

    def test_a_user_in_both_tenants_sees_both(
        self, tenants: Tenants, db_session: Session
    ) -> None:
        """The setting is a list, not a value: someone who belongs to three
        organisations must not be cut down to one."""
        with as_tenant(db_session, [tenants.org_a.id, tenants.org_b.id]):
            names = {project.name for project in db_session.scalars(select(Project))}
        assert names == {"Alpha press", "Beta gearbox"}

    def test_a_user_with_no_tenants_sees_nothing(
        self, tenants: Tenants, db_session: Session
    ) -> None:
        """An empty membership list must not be published as an empty setting:
        an empty setting is how the policies recognise "no context" and stand
        aside, which would turn a user with no tenants into a superuser."""
        with as_tenant(db_session, []):
            assert current_tenant_setting(db_session) == NO_TENANTS
            assert list(db_session.scalars(select(Project))) == []


class TestWritesAreFenced:
    def test_a_row_cannot_be_written_into_another_tenant(
        self, tenants: Tenants, db_session: Session
    ) -> None:
        """`USING` hides rows; a write check is what stops them being created.

        The migration spells `WITH CHECK` out even though PostgreSQL defaults
        it to the `USING` expression on a `FOR ALL` policy -- verified by
        deleting the clause, which changes nothing. It is kept because the
        default disappears the moment anyone splits this into per-command
        policies, and a silent loss of the write check is a request planting a
        project inside somebody else's organisation."""
        with as_tenant(db_session, [tenants.org_a.id]):
            db_session.add(
                Project(
                    name="Trojan",
                    owner_id=tenants.alice.id,
                    organisation_id=tenants.org_b.id,
                )
            )
            with pytest.raises(DBAPIError) as refused:
                db_session.flush()
        assert "row-level security" in str(refused.value).lower()

    def test_a_row_can_be_written_into_your_own(
        self, tenants: Tenants, db_session: Session
    ) -> None:
        with as_tenant(db_session, [tenants.org_a.id]):
            db_session.add(
                Project(
                    name="Alpha jig",
                    owner_id=tenants.alice.id,
                    organisation_id=tenants.org_a.id,
                )
            )
            db_session.flush()
            assert len(list(db_session.scalars(select(Project)))) == 2


class TestTheSetLocalContract:
    """Decision 7's one sanctioned `SET`, and why it is safe here.

    The pooled Neon endpoint is PgBouncer in transaction-pooling mode: the
    backend connection goes to the next client the moment the transaction ends.
    `SET LOCAL` is discarded at exactly that boundary. A session-level `SET` is
    not, and would hand one tenant's context to the next request that borrowed
    the connection.
    """

    @pytest.fixture
    def committing_session(self, rls: str, db_connection: Connection) -> Iterator[Session]:
        """A session on its own connection, whose commits are real.

        The suite's `db_session` runs inside a transaction that is rolled back,
        so it can never demonstrate what survives a COMMIT -- which is the
        entire question here.
        """
        connection = db_connection.engine.connect().execution_options(
            schema_translate_map={None: settings.test_schema}
        )
        session = Session(bind=connection, expire_on_commit=False)
        try:
            yield session
        finally:
            session.rollback()
            session.close()
            connection.close()

    def test_the_context_does_not_survive_the_commit(self, committing_session: Session) -> None:
        """Break `set_config(..., true)` to `false` -- a session-level SET --
        and this test fails: the value is still there after the commit, ready
        to be handed to whoever gets the connection next."""
        with tenant_scope(committing_session, ["11111111-1111-1111-1111-111111111111"]):
            assert current_tenant_setting(committing_session) == (
                "11111111-1111-1111-1111-111111111111"
            )
            committing_session.commit()

        committing_session.rollback()
        assert current_tenant_setting(committing_session) in (None, "")

    def test_the_context_is_re_established_after_a_commit_inside_the_scope(
        self, committing_session: Session
    ) -> None:
        """A request that commits half way through must not spend its second
        half with no tenant context. `tenant_scope` republishes on every
        transaction the session opens, not once."""
        with tenant_scope(committing_session, ["22222222-2222-2222-2222-222222222222"]):
            committing_session.commit()
            assert current_tenant_setting(committing_session) == (
                "22222222-2222-2222-2222-222222222222"
            )

    def test_leaving_the_scope_stops_republishing(self, committing_session: Session) -> None:
        """The listener has to come off, or a session reused for background
        work would keep re-asserting a tenant nobody is signed in as."""
        with tenant_scope(committing_session, ["33333333-3333-3333-3333-333333333333"]):
            pass
        committing_session.commit()
        committing_session.rollback()
        assert current_tenant_setting(committing_session) in (None, "")


class TestThePoliciesAreActuallyOn:
    def test_the_setting_name_is_the_same_string_in_both_places(
        self, migration: ModuleType
    ) -> None:
        """A typo here is a policy that matches nothing and protects nothing,
        with no error anywhere to say so."""
        assert migration.TENANT_SETTING == TENANT_SETTING

    def test_every_tenant_table_has_row_level_security_forced(
        self, rls: str, db_session: Session
    ) -> None:
        """`ENABLE` alone is decoration: PostgreSQL exempts a table's owner
        from its own policies, and the application connects as the role that
        owns these tables. Only `FORCE` applies them to the owner."""
        rows = db_session.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = :schema) "
                "AND relname IN ('organisations', 'memberships', 'organisation_invitations', "
                "'projects', 'geometry_versions', 'simulation_jobs')"
            ),
            {"schema": rls},
        ).all()

        assert len(rows) == 6
        for name, enabled, forced in rows:
            assert enabled, f"{name} has no row-level security"
            assert forced, f"{name} does not force row-level security on its owner"

    @pytest.mark.xfail(
        reason=(
            "Neon's neondb_owner holds BYPASSRLS and cannot drop it; the application "
            "connects as that role, so the policies above are deployed and correct but "
            "inert in production until DATABASE_URL points at a NOBYPASSRLS role. The "
            "recipe is in connected_role_bypasses_rls's docstring. When this starts "
            "XPASSing, delete the marker."
        ),
        strict=False,
    )
    def test_the_application_role_must_not_bypass_row_level_security(
        self, rls: str, db_session: Session
    ) -> None:
        assert connected_role_bypasses_rls(db_session) is False

    def test_the_bypass_diagnostic_reports_the_truth(
        self, rls: str, db_session: Session
    ) -> None:
        """Whatever the answer is, it must be the real one -- a check that
        cannot see the problem is how the problem survives."""
        reported = connected_role_bypasses_rls(db_session)
        actual = db_session.scalar(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
        assert reported is bool(actual)
