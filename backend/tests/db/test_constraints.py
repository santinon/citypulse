"""Exercise the migrated PostgreSQL constraints using synthetic rows."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Connection, delete, insert, select, update
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.sql import Executable
from sqlalchemy.sql.dml import Insert

from citypulse.db.models import (
    Base,
    IngestRun,
    RawPayloadRow,
    StationObservation,
    StationRow,
)

pytestmark = pytest.mark.db

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _assert_rejected(connection: Connection, statement: Executable, constraint: str) -> None:
    with pytest.raises(IntegrityError) as error, connection.begin_nested():
        connection.execute(statement)
    assert constraint in str(error.value.orig)


def _clone(
    connection: Connection,
    table_name: str,
    row_id: int,
    overrides: dict[str, object],
) -> Insert:
    table = Base.metadata.tables[table_name]
    row = connection.execute(select(table).where(table.c.id == row_id)).mappings().one()
    values: dict[str, object] = {
        column.name: row[column.name] for column in table.columns if column.name != "id"
    }
    values.update(overrides)
    return insert(table).values(**values)


@pytest.mark.parametrize(
    ("table_name", "column", "value", "check"),
    [
        ("ingest_run", "status", "unknown", "status"),
        ("ingest_run", "status", "", "status"),
        ("raw_payload", "attempt", 0, "attempt_positive"),
        ("raw_payload", "attempt", -1, "attempt_positive"),
        ("raw_payload", "byte_length", -1, "byte_length_nonnegative"),
        ("raw_payload", "sha256", "A" * 64, "sha256_lowercase_hex"),
        ("raw_payload", "sha256", "g" * 64, "sha256_lowercase_hex"),
        ("raw_payload", "sha256", "a" * 63, "sha256_lowercase_hex"),
        ("raw_payload", "sha256", "", "sha256_lowercase_hex"),
        ("raw_payload", "content_sha256", "A" * 64, "content_sha256_lowercase_hex"),
        ("raw_payload", "content_sha256", "g" * 64, "content_sha256_lowercase_hex"),
        ("raw_payload", "content_sha256", "a" * 63, "content_sha256_lowercase_hex"),
        ("raw_payload", "content_sha256", "", "content_sha256_lowercase_hex"),
        ("station", "system_id", "", "system_id_nonempty"),
        ("station", "gbfs_station_id", "", "gbfs_station_id_nonempty"),
        ("station", "capacity", -1, "capacity_nonnegative"),
        (
            "station_observation",
            "status_record_index",
            -1,
            "status_record_index_nonnegative",
        ),
        (
            "station_observation",
            "num_bikes_available",
            -1,
            "num_bikes_available_nonnegative",
        ),
        (
            "station_observation",
            "num_docks_available",
            -1,
            "num_docks_available_nonnegative",
        ),
        (
            "station_observation",
            "num_bikes_disabled",
            -1,
            "num_bikes_disabled_nonnegative",
        ),
        (
            "station_observation",
            "num_docks_disabled",
            -1,
            "num_docks_disabled_nonnegative",
        ),
    ],
)
def test_invalid_scalar_values_are_rejected(
    migrated_db: Connection,
    seed_ids: dict[str, int],
    table_name: str,
    column: str,
    value: object,
    check: str,
) -> None:
    table = Base.metadata.tables[table_name]
    statement = update(table).where(table.c.id == seed_ids[table_name]).values({column: value})
    _assert_rejected(migrated_db, statement, f"ck_{table_name}_{check}")


@pytest.mark.parametrize("status", ["running", "succeeded", "failed"])
def test_run_status_values_are_accepted(
    migrated_db: Connection, seed_ids: dict[str, int], status: str
) -> None:
    migrated_db.execute(
        update(IngestRun).where(IngestRun.id == seed_ids["ingest_run"]).values(status=status)
    )
    assert migrated_db.scalar(select(IngestRun.status)) == status


@pytest.mark.parametrize("content_sha256", [None, "0123456789abcdef" * 4])
def test_content_hash_accepts_null_or_lowercase_hex(
    migrated_db: Connection,
    seed_ids: dict[str, int],
    content_sha256: str | None,
) -> None:
    payload_id = seed_ids["raw_payload"]
    migrated_db.execute(
        update(RawPayloadRow)
        .where(RawPayloadRow.id == payload_id)
        .values(content_sha256=content_sha256)
    )
    assert (
        migrated_db.scalar(
            select(RawPayloadRow.content_sha256).where(RawPayloadRow.id == payload_id)
        )
        == content_sha256
    )


@pytest.mark.parametrize("table_name", ["station", "station_location_history"])
@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (None, 1.0),
        (1.0, None),
        (0.0, 0.0),
        (-90.1, 1.0),
        (90.1, 1.0),
        (1.0, -180.1),
        (1.0, 180.1),
        (float("nan"), 1.0),
        (1.0, float("nan")),
        (float("inf"), 1.0),
        (1.0, float("-inf")),
    ],
)
def test_invalid_coordinate_pairs_are_rejected(
    migrated_db: Connection,
    seed_ids: dict[str, int],
    table_name: str,
    latitude: float | None,
    longitude: float | None,
) -> None:
    table = Base.metadata.tables[table_name]
    statement = (
        update(table)
        .where(table.c.id == seed_ids[table_name])
        .values(latitude=latitude, longitude=longitude)
    )
    _assert_rejected(migrated_db, statement, f"ck_{table_name}_coordinates_valid")


@pytest.mark.parametrize("table_name", ["station", "station_location_history"])
@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (None, None),
        (-90.0, -180.0),
        (90.0, 180.0),
        (0.0, 1.0),
        (1.0, 0.0),
    ],
)
def test_valid_coordinate_pairs_are_accepted(
    migrated_db: Connection,
    seed_ids: dict[str, int],
    table_name: str,
    latitude: float | None,
    longitude: float | None,
) -> None:
    table = Base.metadata.tables[table_name]
    migrated_db.execute(
        update(table)
        .where(table.c.id == seed_ids[table_name])
        .values(latitude=latitude, longitude=longitude)
    )
    actual = migrated_db.execute(
        select(table.c.latitude, table.c.longitude).where(table.c.id == seed_ids[table_name])
    ).one()
    assert tuple(actual) == (latitude, longitude)


@pytest.mark.parametrize(
    ("body", "expired_at", "has_reference", "accepted"),
    [
        (b"compressed fixture", None, False, True),
        (b"compressed fixture", NOW, False, False),
        (b"compressed fixture", None, True, False),
        (b"compressed fixture", NOW, True, False),
        (None, None, False, False),
        (None, NOW, False, True),
        (None, None, True, True),
        (None, NOW, True, True),
    ],
)
def test_body_storage_check(
    migrated_db: Connection,
    seed_ids: dict[str, int],
    body: bytes | None,
    expired_at: datetime | None,
    has_reference: bool,
    accepted: bool,
) -> None:
    kept_payload_id = int(
        migrated_db.execute(
            _clone(migrated_db, "raw_payload", seed_ids["raw_payload"], {}).returning(
                RawPayloadRow.id
            )
        ).scalar_one()
    )
    reference_id = kept_payload_id if has_reference else None
    payload_id = seed_ids["raw_payload"]
    statement = (
        update(RawPayloadRow)
        .where(RawPayloadRow.id == payload_id)
        .values(
            body=body,
            body_expired_at=expired_at,
            body_same_as_payload_id=reference_id,
        )
    )
    if not accepted:
        _assert_rejected(migrated_db, statement, "ck_raw_payload_body_expiry")
        return

    migrated_db.execute(statement)
    assert migrated_db.execute(
        select(
            RawPayloadRow.body,
            RawPayloadRow.body_expired_at,
            RawPayloadRow.body_same_as_payload_id,
        ).where(RawPayloadRow.id == payload_id)
    ).one() == (body, expired_at, reference_id)


def test_body_can_be_expired_without_losing_provenance(
    migrated_db: Connection, seed_ids: dict[str, int]
) -> None:
    before = migrated_db.execute(
        select(RawPayloadRow.sha256, RawPayloadRow.byte_length, RawPayloadRow.collected_at)
    ).one()
    migrated_db.execute(
        update(RawPayloadRow)
        .where(RawPayloadRow.id == seed_ids["raw_payload"])
        .values(body=None, body_expired_at=NOW)
    )
    after = migrated_db.execute(
        select(RawPayloadRow.sha256, RawPayloadRow.byte_length, RawPayloadRow.collected_at)
    ).one()
    assert after == before
    assert migrated_db.execute(select(RawPayloadRow.body, RawPayloadRow.body_expired_at)).one() == (
        None,
        NOW,
    )


def test_body_reference_rejects_a_missing_payload(
    migrated_db: Connection, seed_ids: dict[str, int]
) -> None:
    statement = (
        update(RawPayloadRow)
        .where(RawPayloadRow.id == seed_ids["raw_payload"])
        .values(
            body=None,
            body_expired_at=None,
            body_same_as_payload_id=9223372036854775807,
        )
    )
    _assert_rejected(
        migrated_db,
        statement,
        "fk_raw_payload_body_same_as_payload_id_raw_payload",
    )


def test_payload_referenced_by_another_payload_cannot_be_deleted(
    migrated_db: Connection, seed_ids: dict[str, int]
) -> None:
    kept_payload_id = int(
        migrated_db.execute(
            _clone(migrated_db, "raw_payload", seed_ids["raw_payload"], {}).returning(
                RawPayloadRow.id
            )
        ).scalar_one()
    )
    migrated_db.execute(
        _clone(
            migrated_db,
            "raw_payload",
            seed_ids["raw_payload"],
            {
                "body": None,
                "body_expired_at": None,
                "body_same_as_payload_id": kept_payload_id,
            },
        )
    )
    statement = delete(RawPayloadRow).where(RawPayloadRow.id == kept_payload_id)
    _assert_rejected(
        migrated_db,
        statement,
        "fk_raw_payload_body_same_as_payload_id_raw_payload",
    )


@pytest.mark.parametrize("recorded_until", [NOW, NOW - timedelta(seconds=1)])
def test_location_interval_must_increase(
    migrated_db: Connection,
    seed_ids: dict[str, int],
    recorded_until: datetime,
) -> None:
    table = Base.metadata.tables["station_location_history"]
    statement = (
        update(table)
        .where(table.c.id == seed_ids["station_location_history"])
        .values(recorded_until=recorded_until)
    )
    _assert_rejected(migrated_db, statement, "ck_station_location_history_recorded_interval")


@pytest.mark.parametrize(
    ("table_name", "constraint"),
    [
        ("station", "uq_station_system_id_gbfs_station_id"),
        ("station_observation", "uq_station_observation_station_id_last_reported"),
    ],
)
def test_duplicate_station_and_observation_keys_are_rejected(
    migrated_db: Connection,
    seed_ids: dict[str, int],
    table_name: str,
    constraint: str,
) -> None:
    statement = _clone(migrated_db, table_name, seed_ids[table_name], {})
    _assert_rejected(migrated_db, statement, constraint)


def test_location_start_is_unique_even_for_closed_intervals(
    migrated_db: Connection, seed_ids: dict[str, int]
) -> None:
    table_name = "station_location_history"
    table = Base.metadata.tables[table_name]
    migrated_db.execute(
        update(table)
        .where(table.c.id == seed_ids[table_name])
        .values(recorded_until=NOW + timedelta(seconds=1))
    )
    statement = _clone(
        migrated_db,
        table_name,
        seed_ids[table_name],
        {"recorded_until": NOW + timedelta(seconds=2)},
    )
    _assert_rejected(
        migrated_db,
        statement,
        "uq_station_location_history_station_id_recorded_from",
    )


def test_only_one_location_interval_can_be_open(
    migrated_db: Connection, seed_ids: dict[str, int]
) -> None:
    statement = _clone(
        migrated_db,
        "station_location_history",
        seed_ids["station_location_history"],
        {"recorded_from": NOW + timedelta(seconds=1)},
    )
    _assert_rejected(migrated_db, statement, "uq_station_location_history_open_station")


def test_closing_an_interval_allows_the_next_open_interval(
    migrated_db: Connection, seed_ids: dict[str, int]
) -> None:
    table_name = "station_location_history"
    table = Base.metadata.tables[table_name]
    boundary = NOW + timedelta(seconds=1)
    migrated_db.execute(
        update(table).where(table.c.id == seed_ids[table_name]).values(recorded_until=boundary)
    )
    migrated_db.execute(
        _clone(
            migrated_db,
            table_name,
            seed_ids[table_name],
            {"recorded_from": boundary, "recorded_until": None},
        )
    )
    intervals = migrated_db.execute(
        select(table.c.recorded_from, table.c.recorded_until).order_by(table.c.recorded_from)
    ).all()
    assert [tuple(row) for row in intervals] == [(NOW, boundary), (boundary, None)]


def test_same_source_station_id_is_allowed_in_two_systems(
    migrated_db: Connection, seed_ids: dict[str, int]
) -> None:
    run_table = Base.metadata.tables["ingest_run"]
    second_run = int(
        migrated_db.execute(
            _clone(
                migrated_db,
                "ingest_run",
                seed_ids["ingest_run"],
                {"system_id": "system-b"},
            ).returning(run_table.c.id)
        ).scalar_one()
    )
    migrated_db.execute(
        _clone(
            migrated_db,
            "station",
            seed_ids["station"],
            {"system_id": "system-b", "latest_run_id": second_run},
        )
    )
    assert migrated_db.execute(
        select(StationRow.system_id)
        .where(StationRow.gbfs_station_id == "station-1")
        .order_by(StationRow.system_id)
    ).scalars().all() == ["system-a", "system-b"]
    assert migrated_db.execute(
        select(IngestRun.source_id).order_by(IngestRun.id)
    ).scalars().all() == ["fixture", "fixture"]


@pytest.mark.parametrize(
    ("table_name", "column"),
    [
        ("raw_payload", "ingest_run_id"),
        ("station", "latest_run_id"),
        ("station", "information_payload_id"),
        ("station_location_history", "station_id"),
        ("station_location_history", "information_payload_id"),
        ("station_observation", "station_id"),
        ("station_observation", "ingest_run_id"),
        ("station_observation", "status_payload_id"),
    ],
)
def test_every_foreign_key_rejects_a_missing_parent(
    migrated_db: Connection,
    seed_ids: dict[str, int],
    table_name: str,
    column: str,
) -> None:
    table = Base.metadata.tables[table_name]
    statement = (
        update(table)
        .where(table.c.id == seed_ids[table_name])
        .values({column: 9223372036854775807})
    )
    _assert_rejected(migrated_db, statement, f"fk_{table_name}_{column}_")


@pytest.mark.parametrize("table_name", ["ingest_run", "raw_payload", "station"])
def test_referenced_rows_cannot_be_deleted(
    migrated_db: Connection, seed_ids: dict[str, int], table_name: str
) -> None:
    table = Base.metadata.tables[table_name]
    statement = delete(table).where(table.c.id == seed_ids[table_name])
    _assert_rejected(migrated_db, statement, "fk_")


def test_observation_requires_last_reported(
    migrated_db: Connection, seed_ids: dict[str, int]
) -> None:
    statement = (
        update(StationObservation)
        .where(StationObservation.id == seed_ids["station_observation"])
        .values(last_reported=None)
    )
    _assert_rejected(migrated_db, statement, "last_reported")


def test_unknown_counts_and_flags_stay_null(
    migrated_db: Connection, seed_ids: dict[str, int]
) -> None:
    row = migrated_db.execute(
        select(
            StationObservation.num_bikes_available,
            StationObservation.num_docks_available,
            StationObservation.num_bikes_disabled,
            StationObservation.num_docks_disabled,
            StationObservation.is_installed,
            StationObservation.is_renting,
            StationObservation.is_returning,
        ).where(StationObservation.id == seed_ids["station_observation"])
    ).one()
    assert tuple(row) == (None, None, None, None, None, None, None)
    assert migrated_db.scalar(select(StationRow.current_status)) == {"num_bikes_available": None}


def test_zero_counts_and_capacity_are_valid(
    migrated_db: Connection, seed_ids: dict[str, int]
) -> None:
    migrated_db.execute(
        update(StationObservation)
        .where(StationObservation.id == seed_ids["station_observation"])
        .values(
            num_bikes_available=0,
            num_docks_available=0,
            num_bikes_disabled=0,
            num_docks_disabled=0,
        )
    )
    migrated_db.execute(
        update(StationRow).where(StationRow.id == seed_ids["station"]).values(capacity=0)
    )
    row = migrated_db.execute(
        select(
            StationObservation.num_bikes_available,
            StationObservation.num_docks_available,
            StationObservation.num_bikes_disabled,
            StationObservation.num_docks_disabled,
        )
    ).one()
    assert tuple(row) == (0, 0, 0, 0)
    assert migrated_db.scalar(select(StationRow.capacity)) == 0


def test_server_defaults(migrated_db: Connection, seed_ids: dict[str, int]) -> None:
    assert seed_ids
    assert migrated_db.execute(
        select(IngestRun.quality_flags, IngestRun.quality_details, IngestRun.error)
    ).one() == ([], [], None)
    assert migrated_db.execute(
        select(RawPayloadRow.diagnostics, RawPayloadRow.retain_forever)
    ).one() == ({}, False)
    assert migrated_db.scalar(select(StationObservation.other_counts)) == {}


def test_naive_application_timestamps_are_rejected(
    migrated_db: Connection, seed_ids: dict[str, int]
) -> None:
    statement = (
        update(IngestRun)
        .where(IngestRun.id == seed_ids["ingest_run"])
        .values(started_at=datetime(2026, 1, 1))
    )
    with (
        pytest.raises(StatementError, match="timezone-aware"),
        migrated_db.begin_nested(),
    ):
        migrated_db.execute(statement)


def test_aware_timestamps_are_normalised_to_utc(
    migrated_db: Connection, seed_ids: dict[str, int]
) -> None:
    local = datetime(2026, 1, 1, 3, tzinfo=timezone(timedelta(hours=3)))
    migrated_db.execute(
        update(IngestRun).where(IngestRun.id == seed_ids["ingest_run"]).values(started_at=local)
    )
    stored = migrated_db.execute(select(IngestRun.started_at)).scalar_one()
    assert stored == NOW
    assert stored.utcoffset() == timedelta(0)
