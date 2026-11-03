"""Synchronous Psycopg migrations, independent of the application's async engine."""

from alembic import context
from sqlalchemy import Connection, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

from citypulse.db.models import Base
from citypulse.settings import Settings

config = context.config
target_metadata = Base.metadata


def database_url() -> URL:
    value = Settings().database_url
    if value is None:
        raise RuntimeError("DATABASE_URL is required to run migrations.")
    url = make_url(value)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("Use a postgresql+psycopg database URL.")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def migrate_connection(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        if not isinstance(supplied_connection, Connection):
            raise TypeError("Alembic's connection attribute must be a sync Connection.")
        migrate_connection(supplied_connection)
        return

    engine = create_engine(
        database_url(),
        poolclass=NullPool,
        connect_args={"options": "-c timezone=UTC", "connect_timeout": 5},
    )
    try:
        with engine.connect() as connection:
            migrate_connection(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
