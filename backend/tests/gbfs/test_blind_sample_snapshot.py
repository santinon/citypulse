from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
from blind_helpers import (
    FLAG_FIELDS,
    SOURCE,
    JsonObject,
    array_value,
    assert_counts_null,
    assert_quality,
    integer_value,
    object_value,
    profile_at,
    read_document,
    string_value,
)

from citypulse.gbfs.models import StationSnapshotBatch
from citypulse.gbfs.normalise import normalise
from citypulse.gbfs.raw import FetchedFeeds, RawPayload, build_fetched_feeds

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "sample-data" / "gbfs" / "20261025T001551Z"
type Snapshot = tuple[FetchedFeeds, StationSnapshotBatch, JsonObject]


@pytest.fixture(scope="module")
def snapshot() -> Snapshot:
    manifest = read_document(SNAPSHOT / "manifest.json")
    profile = read_document(SNAPSHOT / "profile.json")
    entries = {
        string_value(object_value(value)["path"]): object_value(value)
        for value in array_value(manifest["files"])
    }

    def load(relative_path: str, feed_name: str) -> RawPayload:
        entry = entries[relative_path]
        body = (SNAPSHOT / relative_path).read_bytes()
        raw = RawPayload.from_body(
            feed_name=feed_name,
            body=body,
            collected_at=datetime.fromisoformat(string_value(entry["collected_at"])),
            request_url=string_value(entry["url"]),
            final_url=string_value(entry["final_url"]),
            http_status=integer_value(entry["http_status"]),
            content_type=string_value(entry["content_type"]),
            last_modified=string_value(entry["last_modified"]),
        )
        assert len(body) == integer_value(entry["bytes"])
        assert raw.sha256 == string_value(entry["sha256"])
        return raw

    inputs = build_fetched_feeds(
        discovery=load("gbfs.json", "discovery"),
        information=load("en/station_information.json", "station_information"),
        status=load("en/station_status.json", "station_status"),
    )
    return inputs, normalise(inputs, source=SOURCE), profile


def test_sample_parsing_and_join_counts_match_profile(snapshot: Snapshot) -> None:
    inputs, batch, profile = snapshot
    assert len(inputs.information.data.stations) == integer_value(
        profile_at(profile, "stations", "information_records")
    )
    assert len(inputs.status.data.stations) == integer_value(
        profile_at(profile, "stations", "status_records")
    )
    assert len(batch.stations) == integer_value(profile_at(profile, "stations", "join", "both"))
    assert integer_value(profile_at(profile, "stations", "join", "information_only")) == 0
    assert integer_value(profile_at(profile, "stations", "join", "status_only")) == 0
    assert batch.rejected_records == ()
    assert_quality(batch, set())
    assert all(station.information_ref is not None for station in batch.stations)
    assert all(station.status_ref is not None for station in batch.stations)
    ids = [station.station_id for station in batch.stations]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_sample_quality_counts_match_profile(snapshot: Snapshot) -> None:
    _inputs, batch, profile = snapshot
    station_count = integer_value(profile_at(profile, "stations", "information_records"))
    assert (
        sum("flag_coerced" in station.quality_flags for station in batch.stations) == station_count
    )
    expected_details = sum(
        integer_value(profile_at(profile, "fields", "station_status", field, "types", "int"))
        for field in FLAG_FIELDS
    )
    actual_details = [
        detail
        for station in batch.stations
        for detail in station.quality_details
        if detail.code == "flag_coerced"
    ]
    assert len(actual_details) == expected_details
    for station in batch.stations:
        assert {
            detail.path for detail in station.quality_details if detail.code == "flag_coerced"
        } == {f"station_status.{field}" for field in FLAG_FIELDS}
    assert sum(
        "last_reported_invalid" in station.quality_flags for station in batch.stations
    ) == integer_value(profile_at(profile, "last_reported", "invalid"))
    assert sum(station.is_installed is False for station in batch.stations) == integer_value(
        profile_at(profile, "flags", "is_installed", "int:0")
    )
    assert sum("stale" in station.quality_flags for station in batch.stations) == integer_value(
        profile_at(profile, "last_reported", "older_than", "3600")
    )
    assert sum(station.mappable for station in batch.stations) == integer_value(
        profile_at(profile, "coordinates", "valid")
    )
    for station in batch.stations:
        if "last_reported_invalid" in station.quality_flags:
            assert station.observed_at is None
            assert station.status_valid is False
            assert "status_invalid" in station.quality_flags
            assert_counts_null(station)


def test_sample_quarantined_ids_and_hidden_invalid_status_match_profile(
    snapshot: Snapshot,
) -> None:
    _inputs, batch, profile = snapshot
    expected_records = array_value(profile_at(profile, "coordinates", "zero_zero_stations"))
    expected = {
        string_value(object_value(record)["station_id"]): object_value(record)
        for record in expected_records
    }
    actual = {
        station.station_id: station
        for station in batch.stations
        if "coordinates_invalid" in station.quality_flags
    }
    assert set(actual) == set(expected)
    assert len(actual) == integer_value(profile_at(profile, "coordinates", "zero_zero"))
    for station_id, station in actual.items():
        assert station.state == "quarantined"
        assert station.latitude is None
        assert station.longitude is None
        assert station.mappable is False
        assert station.risk_eligible is False
        assert station.capacity == integer_value(expected[station_id]["capacity"])
        invalid = expected[station_id]["last_reported_invalid"]
        assert isinstance(invalid, bool)
        assert ("last_reported_invalid" in station.quality_flags) is invalid


def test_sample_primary_state_counts_match_the_frozen_contract(snapshot: Snapshot) -> None:
    _inputs, batch, _profile = snapshot
    assert Counter(station.state for station in batch.stations) == {
        "in_service": 1055,
        "stale": 61,
        "quarantined": 4,
        "out_of_service": 1,
    }


def test_sample_provenance_matches_profile_and_manifest(snapshot: Snapshot) -> None:
    inputs, batch, profile = snapshot
    assert batch.collected_at == datetime.fromisoformat(
        string_value(profile_at(profile, "reference_time"))
    )
    assert batch.collected_at == inputs.status_payload.collected_at
    assert len(batch.provenance) == 2
    for provenance, raw, relative_path in zip(
        batch.provenance,
        (inputs.information_payload, inputs.status_payload),
        ("en/station_information.json", "en/station_status.json"),
        strict=True,
    ):
        assert provenance.feed_name == raw.feed_name
        assert provenance.sha256 == raw.sha256
        assert provenance.collected_at == raw.collected_at
        assert provenance.last_updated == datetime.fromtimestamp(
            integer_value(profile_at(profile, "files", relative_path, "last_updated")), UTC
        )
        assert provenance.ttl_seconds == integer_value(
            profile_at(profile, "files", relative_path, "ttl")
        )
        assert profile_at(profile, "files", relative_path, "has_version") is False
        assert provenance.version is None
    assert batch.data_as_of == batch.provenance[1].last_updated
    assert batch.data_as_of != batch.collected_at
