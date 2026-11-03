"""Tests require an empty, dedicated PostgreSQL database and leave it at base."""

import hashlib
import os
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, Engine, create_engine, insert, inspect, text
from sqlalchemy.engine import make_url

from citypulse.db.models import Base

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        reason = "TEST_DATABASE_URL is unset; database tests need a dedicated PostgreSQL database."
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail(f"{reason} REQUIRE_DB=1 forbids skipping.")
        pytest.skip(reason)

    url = make_url(value)
    if url.drivername != "postgresql+psycopg":
        pytest.fail("TEST_DATABASE_URL must use postgresql+psycopg.")

    engine = create_engine(
        url,
        connect_args={"options": "-c timezone=UTC", "connect_timeout": 5},
    )
    try:
        # A configured but unreachable database is an error, never a skip.
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_connection(db_engine: Engine) -> Iterator[Connection]:
    with db_engine.connect() as connection:
        tables = set(inspect(connection).get_table_names()) - {"alembic_version"}
        if tables or MigrationContext.configure(connection).get_current_heads():
            pytest.fail("Database tests require an empty database at Alembic base.")
        connection.rollback()
        yield connection


@pytest.fixture
def alembic_config(db_connection: Connection) -> Iterator[Config]:
    config = Config(str(ALEMBIC_INI))
    config.attributes["connection"] = db_connection
    try:
        yield config
    finally:
        # Discard test data or a failed transaction before running the downgrade.
        db_connection.rollback()
        command.downgrade(config, "base")
        remaining = set(inspect(db_connection).get_table_names()) - {"alembic_version"}
        assert not remaining, f"Downgrade left application tables: {remaining}"
        assert MigrationContext.configure(db_connection).get_current_heads() == ()
        db_connection.rollback()


@pytest.fixture
def migrated_db(alembic_config: Config, db_connection: Connection) -> Connection:
    command.upgrade(alembic_config, "head")
    return db_connection


def _insert_id(connection: Connection, table_name: str, values: dict[str, object]) -> int:
    table = Base.metadata.tables[table_name]
    return int(
        connection.execute(insert(table).values(**values).returning(table.c.id)).scalar_one()
    )


@pytest.fixture
def seed_ids(migrated_db: Connection) -> dict[str, int]:
    """Small synthetic rows; all availability and coordinates are explicitly supplied."""
    run_id = _insert_id(
        migrated_db,
        "ingest_run",
        {
            "source_id": "fixture",
            "system_id": "system-a",
            "attribution": "Synthetic test fixture",
            "licence_url": "https://example.org/licence",
            "discovery_url": "https://example.org/gbfs.json",
            "started_at": NOW,
            "status": "running",
        },
    )
    original_body = b'{"data":{"stations":[]}}'
    payload_id = _insert_id(
        migrated_db,
        "raw_payload",
        {
            "ingest_run_id": run_id,
            "feed_name": "station_status",
            "request_url": "https://example.org/status.json",
            "final_url": "https://example.org/status.json",
            "attempt": 1,
            "http_status": 200,
            "collected_at": NOW,
            "sha256": hashlib.sha256(original_body).hexdigest(),
            "byte_length": len(original_body),
            "body": zlib.compress(original_body),
        },
    )
    station_id = _insert_id(
        migrated_db,
        "station",
        {
            "system_id": "system-a",
            "gbfs_station_id": "station-1",
            "first_seen_at": NOW,
            "last_seen_at": NOW,
            "latest_run_id": run_id,
            "latitude": 45.5,
            "longitude": -73.6,
            "current_status": {"num_bikes_available": None},
            "quality_flags": [],
            "quality_details": [],
        },
    )
    location_id = _insert_id(
        migrated_db,
        "station_location_history",
        {
            "station_id": station_id,
            "recorded_from": NOW,
            "latitude": 45.5,
            "longitude": -73.6,
            "quality_flags": [],
            "quality_details": [],
        },
    )
    observation_id = _insert_id(
        migrated_db,
        "station_observation",
        {
            "station_id": station_id,
            "ingest_run_id": run_id,
            "last_reported": NOW,
            "status_payload_id": payload_id,
            "status_record_index": 0,
            "status_valid": False,
            "quality_flags": [],
            "quality_details": [],
        },
    )
    return {
        "ingest_run": run_id,
        "raw_payload": payload_id,
        "station": station_id,
        "station_location_history": location_id,
        "station_observation": observation_id,
    }
