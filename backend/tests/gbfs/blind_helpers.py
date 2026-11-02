import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from citypulse.gbfs.models import (
    RejectedRecord,
    SourceContext,
    Station,
    StationSnapshotBatch,
)
from citypulse.gbfs.normalise import NormalisationPolicy, normalise
from citypulse.gbfs.raw import FetchedFeeds, RawPayload, build_fetched_feeds

type JsonObject = dict[str, JsonValue]
type QualitySubject = Station | RejectedRecord | StationSnapshotBatch

R = datetime(2026, 1, 2, 12, tzinfo=UTC)
R_SECONDS = int(R.timestamp())
FLOOR = 946684800
FLAG_FIELDS = ("is_installed", "is_renting", "is_returning")
COUNT_FIELDS = (
    "num_bikes_available",
    "num_docks_available",
    "num_bikes_disabled",
    "num_docks_disabled",
    "num_ebikes_available",
)
SOURCE = SourceContext(
    source_id="fixture-source",
    system_id="fixture-system",
    attribution="Synthetic test fixture",
    licence_url="https://example.org/licence",
)
JSON_ADAPTER = TypeAdapter(JsonValue)


def information(station_id: JsonValue = "s", **changes: JsonValue) -> JsonObject:
    record: JsonObject = {
        "station_id": station_id,
        "name": "Fixture station",
        "short_name": "001",
        "external_id": "external-s",
        "lat": 45.5,
        "lon": -73.6,
        "capacity": 100,
    }
    record.update(changes)
    return record


def status(station_id: JsonValue = "s", **changes: JsonValue) -> JsonObject:
    record: JsonObject = {
        "station_id": station_id,
        "is_installed": True,
        "is_renting": True,
        "is_returning": True,
        "last_reported": R_SECONDS - 10,
        "num_bikes_available": 3,
        "num_docks_available": 7,
        "num_bikes_disabled": 2,
        "num_docks_disabled": 1,
        "num_ebikes_available": 1,
    }
    record.update(changes)
    return record


def envelope(records: list[JsonValue], **changes: JsonValue) -> JsonObject:
    document: JsonObject = {
        "last_updated": R_SECONDS - 20,
        "ttl": 10,
        "version": "2.2",
        "data": {"stations": records},
    }
    document.update(changes)
    return document


def payload(feed_name: str, body: bytes, collected_at: datetime = R) -> RawPayload:
    return RawPayload.from_body(
        feed_name=feed_name,
        body=body,
        collected_at=collected_at,
        request_url=f"https://example.org/{feed_name}",
    )


def feeds(
    information_records: list[JsonValue] | None = None,
    status_records: list[JsonValue] | None = None,
    *,
    information_metadata: JsonObject | None = None,
    status_metadata: JsonObject | None = None,
    information_omit: tuple[str, ...] = (),
    status_omit: tuple[str, ...] = (),
    information_at: datetime = R,
    status_at: datetime = R,
) -> FetchedFeeds:
    info_document = envelope(
        [information()] if information_records is None else information_records,
        **(information_metadata or {}),
    )
    status_document = envelope(
        [status()] if status_records is None else status_records,
        **(status_metadata or {}),
    )
    for field in information_omit:
        info_document.pop(field)
    for field in status_omit:
        status_document.pop(field)
    return build_fetched_feeds(
        discovery=payload("discovery", b'{"data": {}}'),
        information=payload(
            "station_information",
            json.dumps(info_document, allow_nan=False).encode(),
            information_at,
        ),
        status=payload(
            "station_status",
            json.dumps(status_document, allow_nan=False).encode(),
            status_at,
        ),
    )


def single(
    *,
    info: JsonObject | None = None,
    report: JsonObject | None = None,
    policy: NormalisationPolicy | None = None,
) -> Station:
    inputs = feeds(
        [information() if info is None else info],
        [status() if report is None else report],
    )
    if policy is None:
        batch = normalise(inputs, source=SOURCE)
    else:
        batch = normalise(inputs, source=SOURCE, policy=policy)
    assert batch.rejected_records == ()
    assert batch.quality_flags == ()
    assert batch.quality_details == ()
    assert len(batch.stations) == 1
    return batch.stations[0]


def assert_quality(subject: QualitySubject, expected: set[tuple[str, str]]) -> None:
    assert tuple((item.code, item.path) for item in subject.quality_details) == tuple(
        sorted(expected)
    )
    assert subject.quality_flags == tuple(sorted({code for code, _path in expected}))


def flag_values(station: Station) -> tuple[bool | None, bool | None, bool | None]:
    return station.is_installed, station.is_renting, station.is_returning


def count_values(station: Station) -> dict[str, int | None]:
    return {
        "num_bikes_available": station.num_bikes_available,
        "num_docks_available": station.num_docks_available,
        "num_bikes_disabled": station.num_bikes_disabled,
        "num_docks_disabled": station.num_docks_disabled,
        **station.other_counts,
    }


def assert_counts_null(station: Station) -> None:
    assert station.num_bikes_available is None
    assert station.num_docks_available is None
    assert station.num_bikes_disabled is None
    assert station.num_docks_disabled is None
    assert station.occupancy is None
    for value in station.other_counts.values():
        assert value is None


def object_value(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


def array_value(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def string_value(value: JsonValue) -> str:
    assert isinstance(value, str)
    return value


def integer_value(value: JsonValue) -> int:
    assert isinstance(value, int)
    assert not isinstance(value, bool)
    return value


def read_document(path: Path) -> JsonObject:
    return object_value(JSON_ADAPTER.validate_json(path.read_bytes()))


def profile_at(profile: JsonObject, *keys: str) -> JsonValue:
    current: JsonValue = profile
    for key in keys:
        current = object_value(current)[key]
    return current
