"""The suite must never be able to run against the application's database.

Before this, `db_connection` created a schema inside whatever `DATABASE_URL`
pointed at and then `DROP SCHEMA ... CASCADE`-ed it on teardown. On a developer
machine that is the production Neon project, one schema-name collision away
from destroying real data.
"""

import pytest

from app.core.config import settings
from tests.conftest import _IN_MEMORY_SQLITE, _resolve_test_database_url

PRODUCTION = "postgresql+psycopg://user:pw@ep-prod-pooler.eu-west-2.aws.neon.tech/neondb"


@pytest.fixture
def application_database(monkeypatch):
    monkeypatch.setattr(settings, "database_url", PRODUCTION)


def test_an_unset_variable_falls_back_to_sqlite(monkeypatch, application_database) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    assert _resolve_test_database_url() == _IN_MEMORY_SQLITE


def test_the_application_database_is_refused(monkeypatch, application_database) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", PRODUCTION)
    with pytest.raises(pytest.UsageError, match="same host and database"):
        _resolve_test_database_url()


def test_a_bare_postgres_spelling_of_the_same_target_is_also_refused(
    monkeypatch, application_database
) -> None:
    # The driver prefix differs; the database it destroys does not.
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql://user:pw@ep-prod-pooler.eu-west-2.aws.neon.tech/neondb",
    )
    with pytest.raises(pytest.UsageError):
        _resolve_test_database_url()


def test_a_different_database_on_the_same_host_is_allowed(
    monkeypatch, application_database
) -> None:
    # A scratch Neon branch is the fidelity-preserving way to run these.
    scratch = "postgresql://user:pw@ep-prod-pooler.eu-west-2.aws.neon.tech/kryova_scratch"
    monkeypatch.setenv("TEST_DATABASE_URL", scratch)
    assert _resolve_test_database_url() == scratch


def test_a_local_postgres_is_allowed(monkeypatch, application_database) -> None:
    local = "postgresql://localhost:5432/neondb"
    monkeypatch.setenv("TEST_DATABASE_URL", local)
    assert _resolve_test_database_url() == local


def test_an_explicit_sqlite_file_is_allowed(monkeypatch, application_database) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", "sqlite+pysqlite:///./scratch.db")
    assert _resolve_test_database_url() == "sqlite+pysqlite:///./scratch.db"


def test_the_running_suite_is_not_pointed_at_the_application_database() -> None:
    """A live assertion, not a unit test of the helper: whatever this run
    resolved to, it is not where the application keeps its data."""
    from sqlalchemy import make_url

    target = make_url(_resolve_test_database_url())
    application = make_url(settings.database_url)
    assert target.get_backend_name() == "sqlite" or (
        (target.host, target.database) != (application.host, application.database)
    )


def test_rows_do_not_leak_between_tests(db_session) -> None:
    """Isolation is by transaction rollback. pysqlite defers BEGIN and commits
    implicitly before DDL, so without the driver-level override in conftest
    every test's rows would survive into the next one."""
    from sqlalchemy import func, select

    from app.models import User

    db_session.add(User(email="leak-check@kryova.dev", hashed_password="x"))
    db_session.flush()
    assert db_session.scalar(select(func.count()).select_from(User)) >= 1


def test_the_previous_test_left_nothing_behind(db_session) -> None:
    from sqlalchemy import select

    from app.models import User

    assert db_session.scalar(select(User).where(User.email == "leak-check@kryova.dev")) is None
