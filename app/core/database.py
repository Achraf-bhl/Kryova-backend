"""Engine, session factory and the readiness probe's database check.

One engine, one dialect: psycopg 3 against Neon. There is no async engine --
every route, job and script in this codebase is synchronous, and an unused
async engine only pulls in a driver nobody installs.
"""

import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event, exc, text
from sqlalchemy.orm import DeclarativeBase, Session, SessionTransaction, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


#: How long a pooled connection may have been sitting idle before it is pinged
#: on checkout. See `install_idle_pre_ping` -- this is the whole trade-off.
PRE_PING_IDLE_SECONDS = 5.0

#: Where the last checkin time is stashed on the pool's connection record.
IDLE_SINCE_KEY = "kryova_idle_since"

engine = create_engine(
    settings.database_url,
    connect_args={"sslmode": "require"},
    # Neon drops idle connections and the pooled endpoint hands the dead socket
    # back out; recycling before it does turns a user-visible error into a
    # reconnect. `pool_pre_ping=False` here does NOT mean the ping is gone --
    # `install_idle_pre_ping` below replaces it with the same check, skipped on a
    # connection that was in use moments ago. SQLAlchemy's own pre-ping has no
    # such threshold, so it spent a full Neon round trip (~80 ms measured) on
    # *every* request, which on a three-query route was a quarter of the
    # latency.
    pool_pre_ping=False,
    pool_recycle=settings.db_pool_recycle_seconds,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    # Schema-qualify every statement instead of issuing `SET search_path`: on the
    # PgBouncer transaction-pooling endpoint a session-level SET survives on the
    # shared backend connection and is handed to the next client.
    execution_options={"schema_translate_map": {None: settings.db_schema}},
)


def install_idle_pre_ping(target: Engine, idle_seconds: float = PRE_PING_IDLE_SECONDS) -> None:
    """Pessimistic disconnect checking, but only on a connection that has rested.

    SQLAlchemy's `pool_pre_ping` runs `SELECT 1` on every checkout. Against a
    local Postgres that is free; against Neon it is a full network round trip,
    and this service checks a connection out once per HTTP request -- so a
    route issuing three statements was paying four. Measured on the Windows
    seat: 76 ms of a 640 ms `POST /projects`.

    What the ping actually defends against is a socket that died *while it sat
    in the pool*, which is a function of how long it sat there. A connection
    handed back a moment ago is alive with overwhelming probability, so the
    check is skipped below `idle_seconds` and performed above it. The residual
    risk is a connection that dies inside that window; it surfaces as the
    disconnect error SQLAlchemy would have raised anyway, and the pool
    invalidates on it. Keeping the window small is the only reason the number
    is 5 s and not 60 -- back-to-back requests (the GUI's sign-in, create,
    list burst) fall inside it; an idle user does not, and pays for the
    certainty.

    Raising `DisconnectionError` from `checkout` is SQLAlchemy's documented
    recipe: the pool discards the connection, opens a fresh one and retries the
    checkout once, so the request never sees it.

    A function rather than two decorated module-level listeners so the
    behaviour can be tested on a throwaway engine -- an unpinned claim about
    reconnection is the kind that is discovered false in production.
    """

    @event.listens_for(target, "checkin")
    def _mark_returned(_dbapi_connection: Any, connection_record: Any) -> None:
        record_info = getattr(connection_record, "info", None)
        if record_info is not None:
            record_info[IDLE_SINCE_KEY] = time.monotonic()

    @event.listens_for(target, "checkout")
    def _ping_if_idle(
        dbapi_connection: Any, connection_record: Any, _connection_proxy: Any
    ) -> None:
        record_info = getattr(connection_record, "info", None)
        idle_since = record_info.get(IDLE_SINCE_KEY) if record_info is not None else None
        if idle_since is not None and time.monotonic() - idle_since < idle_seconds:
            return
        try:
            alive = target.dialect.do_ping(dbapi_connection)
        except Exception as failure:
            raise exc.DisconnectionError(
                "The pooled database connection was closed while idle; reconnecting."
            ) from failure
        if not alive:
            # Some dialects answer False rather than raising. Same conclusion.
            raise exc.DisconnectionError(
                "The pooled database connection did not answer; reconnecting."
            )


install_idle_pre_ping(engine)


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database() -> None:
    """Round-trip a trivial query, raising if the database is unreachable.

    Deliberately uses a fresh connection from the pool rather than a request's
    session: a readiness probe that reuses a healthy checked-out connection
    would keep reporting healthy after the endpoint has gone away.
    """
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


#: The GUC the RLS policies read. A custom (dotted) name is required: Postgres
#: only accepts `SET` on a parameter it does not know if the name is qualified.
TENANT_SETTING = "kryova.organisation_ids"

#: What the setting holds when the signed-in user belongs to no organisation at
#: all. It cannot be the empty string: an empty setting is how the policies
#: recognise "no tenant context established" and stand aside, so an empty
#: string would turn a user with no tenants into a user with every tenant. A
#: value that is not a uuid matches no row, which is the intended answer.
NO_TENANTS = "-"


@contextmanager
def tenant_scope(session: Session, organisation_ids: Iterable[str]) -> Iterator[None]:
    """Publish the caller's tenants to Postgres for the life of the transaction.

    This is the one sanctioned `SET` in the codebase (Decision 7), and the
    reason it is safe where `SET search_path` is not comes down to one word:
    `LOCAL`. The `-pooler` host is PgBouncer in transaction-pooling mode, so a
    backend connection is handed to the next client the moment the transaction
    ends -- a session-level `SET` rides along with it and silently reconfigures
    somebody else's queries (this has already happened here: a running server
    started resolving its tables into the test schema). `SET LOCAL` is scoped to
    the *transaction*: Postgres restores the previous value at COMMIT or
    ROLLBACK, which is exactly the boundary at which PgBouncer may reassign the
    connection. There is no window in which the value outlives the transaction
    that set it.

    It is issued as `set_config(name, value, is_local => true)` rather than the
    `SET LOCAL` statement, because `SET` takes no bind parameters and the value
    here is user-derived; `set_config` is the same operation as a function call
    and parameterises cleanly. Never build the statement by interpolation.

    The value is re-published on `after_begin` rather than once, because the
    transaction it belongs to is not the only one the request has: `db.commit()`
    discards the setting (that is the point) and SQLAlchemy autobegins a fresh
    transaction for the next statement. Setting it once would leave every query
    after the first commit running with no tenant context -- which the policies
    read as "system work" and let through.

    A no-op on anything but PostgreSQL, so the SQLite test path is unaffected.
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        yield
        return

    unique = sorted({str(value) for value in organisation_ids if value})
    value = ",".join(unique) if unique else NO_TENANTS

    def _publish(connection: Connection) -> None:
        connection.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": TENANT_SETTING, "value": value},
        )

    def _on_begin(_session: Session, _transaction: SessionTransaction, connection: Connection) -> None:
        _publish(connection)

    event.listen(session, "after_begin", _on_begin)
    try:
        if session.in_transaction():
            # A transaction was already open when the scope was entered (the
            # usual case: authenticating the request read the users table), so
            # `after_begin` has been and gone for it.
            _publish(session.connection())
        yield
    finally:
        event.remove(session, "after_begin", _on_begin)


def current_tenant_setting(session: Session) -> str | None:
    """What Postgres currently believes the tenant context is, or None.

    Exists for the isolation tests: a guard nobody can observe is a guard
    nobody notices the loss of.
    """
    if session.get_bind().dialect.name != "postgresql":
        return None
    return session.scalar(text("SELECT current_setting(:name, true)"), {"name": TENANT_SETTING})


def connected_role_bypasses_rls(session: Session) -> bool | None:
    """Does the role this session connects as ignore row-level security?

    `None` on anything but PostgreSQL. Otherwise `True` means every policy on
    every table is decoration for this connection, silently and with nothing
    logged: `BYPASSRLS` outranks both `ENABLE` and `FORCE ROW LEVEL SECURITY`.

    This exists because the answer on Neon is currently **yes**. The database's
    default role, `neondb_owner`, is created with `BYPASSRLS` and cannot alter
    itself to drop it ("Only roles with the CREATEROLE attribute and the ADMIN
    option on role neondb_owner may alter this role"). So the P2 policies are
    deployed and correct -- `tests/test_tenancy_rls.py` proves them against a
    role that cannot bypass them -- and are *inert for the application* until
    it connects as a role that cannot either:

        CREATE ROLE kryova_app LOGIN PASSWORD '...' NOBYPASSRLS;
        GRANT USAGE ON SCHEMA kryova TO kryova_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA kryova TO kryova_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA kryova
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO kryova_app;

    Migrations keep using the owning role -- they have to, they alter the
    tables -- so this is a second URL in the deployment, not a replacement for
    the one there is. Naming the gap is the point: an unmeasured claim is never
    a pass, and "we have RLS" while the connection bypasses it is exactly that.
    """
    if session.get_bind().dialect.name != "postgresql":
        return None
    return bool(
        session.scalar(text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"))
    )
