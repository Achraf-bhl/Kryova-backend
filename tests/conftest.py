"""Test fixtures.

**Tests never run against `DATABASE_URL`.** They used to: the session fixture
created and then `DROP SCHEMA ... CASCADE`-ed a schema inside whatever database
the application was configured for, which on a developer machine pointed at the
production Neon project. The target is now `TEST_DATABASE_URL`, and if that is
unset the suite builds an in-memory SQLite database instead. A `TEST_DATABASE_URL`
that resolves to the same host and database as `DATABASE_URL` is refused
outright -- see `_resolve_test_database_url`.

The trade is real and worth naming: SQLite is not Postgres, so JSONB, enum and
cascade behaviour are exercised less faithfully than they were. Point
`TEST_DATABASE_URL` at a local Postgres (or a scratch Neon branch) to get that
fidelity back; the fixtures below adapt to either.

The rest of the design is unchanged, and matters most when the target is remote:

* one physical connection for the entire session, not one per test;
* isolation by transaction rollback, not by rebuilding the schema per test;
* no application lifespan per test -- startup behaviour is tested directly in
  `test_startup.py` rather than paid for a hundred times over.

The physics tests (`test_solver.py`, `test_mesh.py`) never request a database
fixture, so they never open a connection at all and run offline in under a
second.
"""

import os
import struct
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, event, make_url, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.deps import get_media_service, get_session_scope
from app.api.rate_limit import auth_limiter
from app.core.config import settings
from app.core.database import Base, get_db
from app.jobs import InlineJobQueue, get_job_queue
from app.main import app
from app.media import LocalMediaStore, MediaService, get_media_store
from tests.typing import AuthenticatedTestClient

# Shared by every connection in the process, so the schema one connection
# creates is the schema the next one sees.
_IN_MEMORY_SQLITE = "sqlite+pysqlite:///:memory:"


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
        return create_engine(url, pool_pre_ping=True)

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
def auth_client(client: AuthenticatedTestClient) -> AuthenticatedTestClient:
    """A client already registered and carrying a bearer token."""
    auth_limiter.reset()
    credentials = {"email": "eng@kryova.dev", "password": "correct-horse-battery"}
    response = client.post("/api/v1/auth/register", json=credentials)
    assert response.status_code == 201, response.text
    login_response = client.post(
        "/api/v1/auth/login",
        data={"username": credentials["email"], "password": credentials["password"]},
    )
    assert login_response.status_code == 200, login_response.text
    client.headers["x-csrf-token"] = client.cookies["kryova_csrf"]
    return client


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
