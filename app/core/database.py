from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    connect_args={"sslmode": "require"},
    # Neon sits behind a pooler that drops idle connections; without pre-ping
    # the first query after an idle spell fails instead of reconnecting.
    pool_pre_ping=True,
    pool_recycle=settings.db_pool_recycle_seconds,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    # Compile every table reference schema-qualified instead of trusting
    # search_path. Neon's pooled endpoint is PgBouncer in transaction-pooling
    # mode, where session state set by one client stays on the shared backend
    # connection and is handed to the next one.
    execution_options={"schema_translate_map": {None: settings.db_schema}},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
