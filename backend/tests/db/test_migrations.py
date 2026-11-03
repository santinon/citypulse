"""Migration round trips, metadata parity, UTC connections and database settings."""

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from pydantic import ValidationError
from sqlalchemy import BigInteger, Connection, Engine, inspect, text
from sqlalchemy.dialects.postgresql import JSONB

from citypulse.db.models import Base
from citypulse.db.session import make_engine, make_sessionmaker
from citypulse.settings import Settings

APPLICATION_TABLES = {
    "ingest_run",
    "raw_payload",
    "station",
    "station_location_history",
    "station_observation",
}


def _application_tables(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names()) - {"alembic_version"}


@pytest.mark.db
def test_initial_revision_round_trip(alembic_config: Config, db_connection: Connection) -> None:
    assert _application_tables(db_connection) == set()
    assert MigrationContext.configure(db_connection).get_current_heads() == ()
    db_connection.rollback()

    command.upgrade(alembic_config, "head")
    assert _application_tables(db_connection) == APPLICATION_TABLES
    assert MigrationContext.configure(db_connection).get_current_heads() == ("0001",)
    db_connection.rollback()

    command.downgrade(alembic_config, "base")
    assert _application_tables(db_connection) == set()
    assert MigrationContext.configure(db_connection).get_current_heads() == ()
    db_connection.rollback()

    command.upgrade(alembic_config, "head")
    assert _application_tables(db_connection) == APPLICATION_TABLES
    assert MigrationContext.configure(db_connection).get_current_heads() == ("0001",)


@pytest.mark.db
def test_models_match_initial_migration(migrated_db: Connection, alembic_config: Config) -> None:
    assert _application_tables(migrated_db) == APPLICATION_TABLES
    migrated_db.rollback()
    command.check(alembic_config)


@pytest.mark.db
def test_identity_primary_keys_and_restricting_foreign_keys(migrated_db: Connection) -> None:
    inspector = inspect(migrated_db)
    foreign_key_count = 0
    for table_name in sorted(APPLICATION_TABLES):
        columns = inspector.get_columns(table_name)
        identity = next(column for column in columns if column["name"] == "id")
        assert isinstance(identity["type"], BigInteger)
        assert identity.get("identity") is not None
        primary_key = inspector.get_pk_constraint(table_name)
        assert primary_key["name"] == f"pk_{table_name}"
        assert primary_key["constrained_columns"] == ["id"]
        for foreign_key in inspector.get_foreign_keys(table_name):
            assert foreign_key.get("options", {}).get("ondelete") == "RESTRICT"
            foreign_key_count += 1
    assert foreign_key_count == 9


async def _assert_async_connections_use_utc(url: str) -> None:
    engine = make_engine(url)
    try:
        sessions = make_sessionmaker(engine)
        async with sessions() as session:
            assert await session.scalar(text("SHOW TimeZone")) == "UTC"
        # Hold both connections simultaneously to exercise two physical connections.
        async with engine.connect() as first, engine.connect() as second:
            assert await first.scalar(text("SHOW TimeZone")) == "UTC"
            assert await second.scalar(text("SHOW TimeZone")) == "UTC"
    finally:
        await engine.dispose()


@pytest.mark.db
def test_sync_and_async_connections_use_utc(migrated_db: Connection, db_engine: Engine) -> None:
    assert migrated_db.scalar(text("SHOW TimeZone")) == "UTC"
    url = db_engine.url.render_as_string(hide_password=False)
    # Psycopg's async implementation needs a selector loop on Windows.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_assert_async_connections_use_utc(url))


def test_database_configuration_is_optional() -> None:
    settings = Settings()
    assert settings.database_url is None
    assert settings.raw_payload_retention_days is None


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_retention_days_must_be_positive(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("RAW_PAYLOAD_RETENTION_DAYS", value)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value", [1, 14])
def test_retention_days_can_be_configured(monkeypatch: pytest.MonkeyPatch, value: int) -> None:
    monkeypatch.setenv("RAW_PAYLOAD_RETENTION_DAYS", str(value))
    assert Settings().raw_payload_retention_days == value


@pytest.mark.parametrize(
    "url",
    [
        "sqlite://",
        "postgresql+asyncpg://localhost/citypulse",
        "postgresql://localhost/citypulse",
    ],
)
def test_application_engine_requires_psycopg(url: str) -> None:
    with pytest.raises(ValueError, match=r"postgresql\+psycopg"):
        make_engine(url)


def test_none_in_a_json_column_is_sql_null_so_not_null_refuses_it() -> None:
    json_types = [
        column.type
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, JSONB)
    ]
    assert len(json_types) == 12
    assert all(json_type.none_as_null for json_type in json_types)
