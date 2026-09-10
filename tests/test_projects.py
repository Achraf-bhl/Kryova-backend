from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, event
from sqlalchemy.orm import Session

from app.core import maintenance
from app.core.config import settings
from tests.typing import AuthenticatedTestClient


def test_project_crud_round_trip(auth_client: AuthenticatedTestClient) -> None:
    created = auth_client.post(
        "/api/v1/projects", json={"name": "Drone arm", "description": "carbon"}
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    listed = auth_client.get("/api/v1/projects")
    assert [p["id"] for p in listed.json()["items"]] == [project_id]

    updated = auth_client.patch(f"/api/v1/projects/{project_id}", json={"name": "Drone arm v2"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Drone arm v2"
    assert updated.json()["description"] == "carbon"  # untouched field survives PATCH

    assert auth_client.delete(f"/api/v1/projects/{project_id}").status_code == 204
    assert auth_client.get(f"/api/v1/projects/{project_id}").status_code == 404


def test_projects_require_authentication(client: AuthenticatedTestClient) -> None:
    assert client.get("/api/v1/projects").status_code == 401


def test_another_users_project_is_not_visible(
    auth_client: AuthenticatedTestClient, project_id: str
) -> None:
    auth_client.post(
        "/api/v1/auth/register", json={"email": "other@kryova.dev", "password": "another-password"}
    )
    auth_client.post(
        "/api/v1/auth/login",
        data={"username": "other@kryova.dev", "password": "another-password"},
    )
    auth_client.headers["x-csrf-token"] = auth_client.cookies["kryova_csrf"]

    response = auth_client.get(
        f"/api/v1/projects/{project_id}",
    )
    # 404 not 403: project ids must not be enumerable across accounts.
    assert response.status_code == 404


# --------------------------------------------------------------------------
# Round-trip budget. Creating a project is the cheapest thing the product does
# and was, measured on the Windows seat, one of the slowest: 7 network round
# trips against Neon at ~80 ms each. Four of them were the dependency chain
# reading the same handful of tenancy rows over and over -- the user, then the
# memberships for the RLS context, then the memberships again looking for the
# personal organisation, plus a `SELECT 1` pool pre-ping in front of all of it.
#
# These tests pin the shape rather than the timing, because a timing assertion
# on a shared cloud database is a flaky test. An N+1 reintroduced here fails
# loudly and names itself.
# --------------------------------------------------------------------------


@contextmanager
def recorded_sql(connection: Connection) -> Iterator[list[str]]:
    """Every statement the request issues on the test's connection.

    The schema qualifier is stripped, because these tests are about *how many*
    round trips a request makes and which tables they touch -- not about which
    schema they were translated into. On SQLite the table is `users`; on
    PostgreSQL, where every reference is compiled schema-qualified via
    `schema_translate_map`, the same statement reads `kryova_test.users`. Left
    in, the assertions below silently only hold on SQLite, which is the exact
    shape of drift the "test on the engine you ship on" rule exists to catch --
    and is how they were found, by pointing the suite at a real PostgreSQL.
    """
    captured: list[str] = []
    qualifier = f"{settings.test_schema}."

    def before(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        captured.append(" ".join(statement.split()).lower().replace(qualifier, ""))

    event.listen(connection, "before_cursor_execute", before)
    try:
        yield captured
    finally:
        event.remove(connection, "before_cursor_execute", before)


def _selects(captured: list[str]) -> list[str]:
    # `set_config` is the RLS tenant publish, not a read. It is one round trip
    # and it stays -- Decision 7 -- so it is excluded from the read budget
    # rather than pretended away.
    return [s for s in captured if s.startswith("select") and "set_config" not in s]


def test_creating_a_project_reads_the_database_once(
    auth_client: AuthenticatedTestClient, db_connection: Connection
) -> None:
    """One SELECT, one INSERT. Everything the request needs about tenancy
    arrives with the user it authenticated."""
    # The first project creates the personal organisation; measure the steady
    # state, which is every project after it.
    assert auth_client.post("/api/v1/projects", json={"name": "first"}).status_code == 201

    with recorded_sql(db_connection) as captured:
        response = auth_client.post("/api/v1/projects", json={"name": "second"})
    assert response.status_code == 201, response.text

    selects = _selects(captured)
    assert len(selects) == 1, f"expected one read, got {len(selects)}:\n" + "\n".join(selects)

    only_read = selects[0]
    assert " from users" in only_read
    # The memberships and their organisations come back on that same statement.
    # Without them, `organisation_ids_for_user` and `personal_organisation`
    # each go to the database on their own -- the N+1 this pins.
    assert "join memberships" in only_read
    assert "join organisations" in only_read

    inserts = [s for s in captured if s.startswith("insert")]
    assert len(inserts) == 1, inserts
    assert " into projects" in inserts[0]


def test_the_first_project_creates_the_personal_organisation_without_a_lookup(
    auth_client: AuthenticatedTestClient, db_connection: Connection, db_session: Session
) -> None:
    """A user with no memberships at all needs no confirming SELECT.

    The eager load already proved the set is empty, and an empty set of
    memberships cannot hide a personal organisation -- the query it replaces
    joins through memberships to find one.
    """
    # Warm the maintenance-window cache first. It is a per-process read every
    # `maintenance.CACHE_SECONDS`, not a per-request one, so counting it here
    # would measure process start rather than what this request costs -- and the
    # cache itself is pinned in `test_platform.TestTheWindowIsCached`.
    maintenance.current_window(db_session)

    with recorded_sql(db_connection) as captured:
        response = auth_client.post("/api/v1/projects", json={"name": "first ever"})
    assert response.status_code == 201, response.text

    assert len(_selects(captured)) == 1, _selects(captured)
    inserted = [s for s in captured if s.startswith("insert")]
    assert sorted(
        table for table in ("organisations", "memberships", "projects")
        if any(f" into {table}" in s for s in inserted)
    ) == ["memberships", "organisations", "projects"]


def test_reading_a_project_does_not_query_memberships_separately(
    auth_client: AuthenticatedTestClient, db_connection: Connection, project_id: str
) -> None:
    """`_project_for_role` answers from the session, not a second query."""
    with recorded_sql(db_connection) as captured:
        assert auth_client.get(f"/api/v1/projects/{project_id}").status_code == 200

    selects = _selects(captured)
    # The user (with its tenancy) and the project -- and under these fixtures,
    # which share one session across requests, the project is already in the
    # identity map, so one. Never three, which is what a separate membership
    # lookup would make it.
    assert len(selects) <= 2, "\n".join(selects)
    standalone_membership_reads = [
        s for s in selects if " from memberships" in s and " from users" not in s
    ]
    assert standalone_membership_reads == [], standalone_membership_reads
