from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import make_url

import app.models  # noqa: F401  -- registers all models on Base.metadata
from app.core.config import settings
from app.core.database import Base
from app.models.base import UTCDateTime

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Render custom column types as the plain SQL types they compile to.

    UTCDateTime is a Python-side TypeDecorator; its DDL is just a timestamp.
    Emitting the decorator would make migrations import application code, which
    then breaks the moment that code moves.
    """
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        render_item=render_item,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _migration_url() -> str:
    """Migrations talk to Neon's direct endpoint, never the -pooler host.

    They need ``search_path`` set, and a SET on the PgBouncer
    transaction-pooling endpoint survives on the shared backend connection and
    is handed to the next client. A direct connection owns its session, so the
    SET is contained to this migration run.
    """
    url = make_url(settings.database_url)
    host = url.host or ""
    if "-pooler." in host:
        url = url.set(host=host.replace("-pooler.", ".", 1))
    return url.render_as_string(hide_password=False)


def run_migrations_online() -> None:
    section = dict(config.get_section(config.config_ini_section, {}))
    section["sqlalchemy.url"] = _migration_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        if not connection.dialect.has_schema(connection, settings.db_schema):
            connection.execute(
                text(f'CREATE SCHEMA "{settings.db_schema}"')
            )
            connection.commit()

        # Two different mechanisms are needed because Alembic uses two code
        # paths: CREATE TABLE compiles through the translate map, while
        # ALTER TABLE renders the bare table name and ignores it. Without
        # search_path an add_column lands in public while the app reads
        # settings.db_schema. Safe only on the direct endpoint above.
        connection.execute(text(f'SET search_path TO "{settings.db_schema}"'))
        connection.commit()
        connection = connection.execution_options(
            schema_translate_map={None: settings.db_schema}
        )

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()
        # The CREATE SCHEMA above put this connection into commit-as-you-go
        # mode, so Alembic treats the migration transaction as externally
        # owned and will not commit it. Without this the whole upgrade is
        # silently rolled back and `alembic current` stays empty.
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
