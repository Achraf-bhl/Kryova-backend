"""Test fixtures.

Tests that touch the database run against the same Neon Postgres the
application uses, in a dedicated schema. This keeps JSONB, enum and cascade
behaviour identical between tests and production.

Neon is a round trip away (~250 ms from here), so the fixtures are built to
spend as few round trips as possible:

* one physical connection for the entire session, not one per test;
* isolation by transaction rollback, not by rebuilding the schema per test;
* no application lifespan per test -- startup behaviour is tested directly in
  `test_startup.py` rather than paid for a hundred times over.

The physics tests (`test_solver.py`, `test_mesh.py`) never request a database
fixture, so they never open a connection at all and run offline in under a
second.
"""

import struct
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from app.api.deps import get_media_service, get_session_scope
from app.api.rate_limit import auth_limiter
from app.core.config import settings
from app.core.database import Base, engine, get_db
from app.jobs import InlineJobQueue, get_job_queue
from app.main import app
from app.media import LocalMediaStore, MediaService, get_media_store
from tests.typing import AuthenticatedTestClient


@pytest.fixture(scope="session")
def db_connection() -> Iterator[Connection]:
    """Build the test schema once, on one connection held for the whole run.

    The schema is selected with `schema_translate_map`, which qualifies table
    names when the SQL is compiled. `SET search_path` would be the obvious
    alternative and is a trap: Neon's pooled endpoint is PgBouncer in
    transaction-pooling mode, so a session-level SET stays on the shared backend
    connection and leaks into whichever client gets it next. A test run really
    can point an unrelated process at the test schema that way -- and then break
    it by dropping that schema on teardown.
    """
    schema = settings.test_schema
    with engine.connect() as setup:
        setup.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        setup.commit()
    connection = engine.connect().execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(connection)
    connection.commit()

    try:
        yield connection
    finally:
        connection.close()
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
