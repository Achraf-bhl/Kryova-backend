"""Engine, session factory and the readiness probe's database check.

One engine, one dialect: psycopg 3 against Neon. There is no async engine --
every route, job and script in this codebase is synchronous, and an unused
async engine only pulls in a driver nobody installs.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    connect_args={"sslmode": "require"},
    # Neon drops idle connections and the pooled endpoint hands the dead socket
    # back out; recycling before it does turns a user-visible error into a
    # reconnect, and pre_ping catches the ones that die inside the window.
    pool_pre_ping=True,
    pool_recycle=settings.db_pool_recycle_seconds,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    # Schema-qualify every statement instead of issuing `SET search_path`: on the
    # PgBouncer transaction-pooling endpoint a session-level SET survives on the
    # shared backend connection and is handed to the next client.
    execution_options={"schema_translate_map": {None: settings.db_schema}},
)

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
