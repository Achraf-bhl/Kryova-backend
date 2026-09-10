

import os
import struct
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import cast

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, event, make_url, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import mail
from app.api.deps import get_media_service, get_session_scope
from app.api.rate_limit import auth_limiter
from app.core import email_verification, maintenance
from app.core.config import _as_psycopg_url, settings
from app.core.database import Base, get_db
from app.jobs import InlineJobQueue, get_job_queue
from app.mail.message import Outbox
from app.main import app
from app.media import LocalMediaStore, MediaService, get_media_store
from app.models import User
from tests.typing import AuthenticatedTestClient

# Shared by every connection in the process, so the schema one connection
# creates is the schema the next one sees.
_IN_MEMORY_SQLITE = "sqlite+pysqlite:///:memory:"


def _load_test_database_url_from_the_files_settings_reads() -> None:
    """Put `TEST_DATABASE_URL` into the environment if only a `.env` file has it.

    `app.core.config` reads `(".env", ".env.local")` and pytest does not, so a
    developer who sets `TEST_DATABASE_URL` beside `DATABASE_URL` in `.env.local`
    has every reason to think they configured the suite and has silently
    configured nothing: the run falls back to SQLite and the RLS, JSONB and
    cascade tests skip themselves. That is the failure this repository has
    already had once, where one green tick stood for a Postgres suite that had
    never touched Postgres.

    It writes to `os.environ` rather than being consulted by the resolver, so
    the resolver stays a pure function of the environment and
    `monkeypatch.delenv` still means what it says. A real environment variable
    always wins, so CI -- which exports one -- is untouched.
    """
    if os.environ.get("TEST_DATABASE_URL", "").strip():
        return
    for name in settings.model_config.get("env_file", ()):
        if not Path(name).exists():
            continue
        value = (dotenv_values(Path(name)).get("TEST_DATABASE_URL") or "").strip()
        if value:
            os.environ["TEST_DATABASE_URL"] = value


_load_test_database_url_from_the_files_settings_reads()


def _resolve_test_database_url() -> str:
    """Where the suite is allowed to create and drop tables.

    Refuses anything that resolves to the same host and database as
    `DATABASE_URL`. The fixtures below create tables and drop a schema; doing
    that against the application's own database is a data-loss bug waiting for
    the one run where the schema names happen to collide.
    """
    configured = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not configured:
        return _IN_MEMORY_SQLITE

    target, application = make_url(configured), make_url(settings.database_url)
    same_host = (target.host or "") == (application.host or "")
    same_database = (target.database or "") == (application.database or "")
    if target.get_backend_name() != "sqlite" and same_host and same_database:
        raise pytest.UsageError(
            "TEST_DATABASE_URL points at the same host and database as DATABASE_URL "
            f"({target.host}/{target.database}). The suite creates and drops tables; "
            "give it its own database (or leave TEST_DATABASE_URL unset for SQLite)."
        )
    return configured


def _build_engine(url: str) -> Engine:
    if make_url(url).get_backend_name() != "sqlite":
        # Onto psycopg 3, exactly as `Settings` does for the application. A bare
        # `postgresql://` URL is SQLAlchemy's psycopg2 spelling and psycopg2 is
        # not installed, so without this a URL that is correct in every other
        # respect dies at connect time with `No module named 'psycopg2'` -- and
        # a URL pasted from `DATABASE_URL`, which is normalised, would not work
        # here. Done at engine construction rather than in the resolver so the
        # resolver still returns what was configured, unchanged.
        return create_engine(_as_psycopg_url(url), pool_pre_ping=True)

    # One shared connection: an in-memory SQLite database belongs to the
    # connection that opened it, and a fresh one would find no tables.
    engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)

    # pysqlite's driver-level transaction handling has to be turned off and
    # replaced, or the per-test rollback below does nothing: it commits
    # implicitly before DDL and never opens a transaction of its own, so every
    # test's rows survive into the next one. This is SQLAlchemy's documented
    # recipe, not a workaround.
    @event.listens_for(engine, "connect")
    def _disable_pysqlite_transactions(dbapi_connection, _record) -> None:
        dbapi_connection.isolation_level = None
        # Nothing enforces ON DELETE CASCADE without this, so a cascade bug
        # would pass here and fail on Postgres.
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def _emit_begin(connection) -> None:
        connection.exec_driver_sql("BEGIN")

    return engine


@pytest.fixture(scope="session")
def db_connection() -> Iterator[Connection]:
    engine = _build_engine(_resolve_test_database_url())
    is_sqlite = engine.dialect.name == "sqlite"
    schema = None if is_sqlite else settings.test_schema

    if schema is not None:
        with engine.connect() as setup:
            setup.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            setup.commit()

    connection = engine.connect()
    if schema is not None:
        # Schema-qualify rather than `SET search_path`: on a pooled endpoint a
        # session-level SET outlives the checkout and leaks to the next client.
        connection = connection.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(connection)
    connection.commit()

    try:
        yield connection
    finally:
        connection.close()
        if schema is not None:
            with engine.connect() as teardown:
                teardown.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                teardown.commit()
        engine.dispose()


@pytest.fixture
def db_session(db_connection: Connection) -> Iterator[Session]:
    transaction = db_connection.begin()
    session = Session(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()


@pytest.fixture
def media_store(tmp_path: Path) -> LocalMediaStore:
    """A throwaway blob store, so heavy files never touch the real media root."""
    return LocalMediaStore(tmp_path / "media", chunk_size=64 * 1024)


@pytest.fixture(autouse=True)
def outbox() -> Iterator[Outbox]:
    """Every message the suite sends, and nothing leaves the process.

    Autouse deliberately. Without it the default transport is `console`, so a
    suite run would write verification links and reset tokens into the pytest
    log — and a machine that had configured SMTP for development would have the
    test suite emailing real people. Installing the memory transport for every
    test makes both impossible rather than unlikely.

    Request it by name to assert on what was sent.
    """
    collected = Outbox()
    mail.use_transport(mail.MemoryTransport(collected))
    try:
        yield collected
    finally:
        # Back to settings-derived, so a test that installs its own transport
        # cannot leak it into the next one.
        mail.use_transport(None)


@pytest.fixture(autouse=True)
def _forget_the_maintenance_window() -> Iterator[None]:
    """No test inherits another test's cached maintenance state.

    `core/maintenance` caches the window for `CACHE_SECONDS` in a module global,
    which is right in a worker process and wrong in a suite: tests share one
    process, so a test that starts maintenance would leave every test that runs
    within ten seconds of it refusing writes, and a test that ran first would
    hide a window a later one had just declared. Both failures would land
    somewhere other than the test that caused them.

    Autouse and on both sides of the yield, because either direction of leak is
    the same bug.
    """
    maintenance.invalidate()
    try:
        yield
    finally:
        maintenance.invalidate()


@pytest.fixture
def client(
    db_session: Session, media_store: LocalMediaStore, tmp_path: Path, monkeypatch
) -> Iterator[AuthenticatedTestClient]:
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_media_store] = lambda: media_store

    app.dependency_overrides[get_media_service] = lambda: MediaService(db_session, media_store)
    # Jobs run inline on the request thread, against the same transaction the
    # test holds open. A worker thread would use its own connection and see none
    # of the uncommitted test data.
    app.dependency_overrides[get_job_queue] = InlineJobQueue
    app.dependency_overrides[get_session_scope] = lambda: (lambda: nullcontext(db_session))

    # No `with`: skipping lifespan keeps every test off a second connection.
    test_client = cast(AuthenticatedTestClient, TestClient(app))
    test_client.media = MediaService(db_session, media_store)
    test_client.store = media_store
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def auth_client(
    client: AuthenticatedTestClient, db_session: Session
) -> AuthenticatedTestClient:
    """A client already registered, verified, and carrying a bearer token."""
    auth_limiter.reset()
    credentials = {"email": "eng@kryova.dev", "password": "correct-horse-battery"}
    response = client.post("/api/v1/auth/register", json=credentials)
    assert response.status_code == 201, response.text

    # Verified directly rather than by following the emailed link (P1.5).
    # Clicking it here would make every project-creating test in the suite
    # depend on the verification flow, so one defect there would present as
    # hundreds of unrelated failures. `tests/test_auth_verification.py` drives
    # the real link end to end, where a failure names the thing that broke.
    registered = db_session.get(User, response.json()["id"])
    assert registered is not None
    email_verification.mark_verified(registered, now=email_verification.utcnow())
    db_session.flush()

    login_response = client.post(
        "/api/v1/auth/login",
        data={"username": credentials["email"], "password": credentials["password"]},
    )
    assert login_response.status_code == 200, login_response.text
    client.headers["x-csrf-token"] = client.cookies["kryova_csrf"]
    return client


def register_verified(
    client: TestClient,
    db_session: Session,
    email: str,
    password: str = "correct-horse-battery",
) -> str:
    """Register an account and confirm its address. Returns the user id.

    Since P1.5 an unverified account cannot create a project, so any fixture
    that registers a *second* user and then makes something for them has to
    confirm the address first. This exists so that step is one call rather than
    four lines copied into a dozen fixtures — and so the day the rule changes
    there is one place to change it.

    Does not sign in: callers differ on whether they want the session on this
    client or another.
    """
    response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    user_id: str = response.json()["id"]
    user = db_session.get(User, user_id)
    assert user is not None
    email_verification.mark_verified(user, now=email_verification.utcnow())
    db_session.flush()
    return user_id


@pytest.fixture
def current_user_id(auth_client: AuthenticatedTestClient) -> str:
    return auth_client.get("/api/v1/auth/me").json()["id"]


@pytest.fixture
def project_id(auth_client: AuthenticatedTestClient) -> str:
    response = auth_client.post("/api/v1/projects", json={"name": "Bracket"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def binary_stl(triangles: list[tuple[tuple[float, float, float], ...]]) -> bytes:
    """Build a minimal binary STL from triangles of three (x, y, z) vertices."""
    out = bytearray(b"\0" * 80)
    out += struct.pack("<I", len(triangles))
    for tri in triangles:
        out += struct.pack("<3f", 0.0, 0.0, 1.0)
        for vertex in tri:
            out += struct.pack("<3f", *vertex)
        out += struct.pack("<H", 0)
    return bytes(out)


@pytest.fixture
def cube_stl() -> bytes:
    return binary_stl(
        [
            ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 20.0, 0.0)),
            ((0.0, 0.0, 0.0), (10.0, 20.0, 0.0), (0.0, 20.0, 5.0)),
        ]
    )
