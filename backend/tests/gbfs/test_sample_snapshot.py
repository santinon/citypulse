"""AC 11: the committed snapshot gives the figures that the audit wrote in profile.json."""

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from citypulse.gbfs.models import SourceContext, StationSnapshotBatch
from citypulse.gbfs.normalise import normalise
from citypulse.gbfs.raw import FetchedFeeds, RawPayload, build_fetched_feeds

SNAPSHOT = Path(__file__).resolve().parents[3] / "sample-data" / "gbfs" / "20261025T001551Z"
SOURCE = SourceContext(
    source_id="bixi",
    system_id="Bixi_MTL",
    attribution="BIXI Montréal",
    licence_url="https://creativecommons.org/licenses/by/4.0/",
)


def load_json(name: str) -> dict[str, Any]:
    return json.loads((SNAPSHOT / name).read_text(encoding="utf-8"))


def load_feeds() -> FetchedFeeds:
    payloads = {
        entry["role"] if entry["feed"] is None else entry["feed"]: RawPayload.from_body(
            feed_name=entry["role"] if entry["feed"] is None else entry["feed"],
            body=(SNAPSHOT / entry["path"]).read_bytes(),
            collected_at=datetime.fromisoformat(entry["collected_at"]),
            request_url=entry["final_url"],
        )
        for entry in load_json("manifest.json")["files"]
    }
    return build_fetched_feeds(
        discovery=payloads["discovery"],
        information=payloads["station_information"],
        status=payloads["station_status"],
    )


@pytest.fixture(scope="module")
def profile() -> dict[str, Any]:
    return load_json("profile.json")


@pytest.fixture(scope="module")
def feeds() -> FetchedFeeds:
    return load_feeds()


@pytest.fixture(scope="module")
def batch(feeds: FetchedFeeds) -> StationSnapshotBatch:
    return normalise(feeds, source=SOURCE)


def with_flag(batch: StationSnapshotBatch, code: str) -> set[str]:
    return {station.station_id for station in batch.stations if code in station.quality_flags}


def test_reference_time_is_the_one_of_the_audit(
    batch: StationSnapshotBatch, profile: dict[str, Any]
) -> None:
    assert batch.collected_at == datetime.fromisoformat(profile["reference_time"])


def test_every_station_joins_and_nothing_is_rejected(
    batch: StationSnapshotBatch, profile: dict[str, Any]
) -> None:
    join = profile["stations"]["join"]
    assert len(batch.stations) == join["both"] == 1121
    assert join["information_only"] == join["status_only"] == 0
    assert batch.rejected_records == ()
    assert batch.quality_flags == ()
    assert with_flag(batch, "status_missing") == set()
    assert with_flag(batch, "information_missing") == set()


def test_invalid_coordinates_are_the_documented_stations(
    batch: StationSnapshotBatch, profile: dict[str, Any]
) -> None:
    expected = {item["station_id"] for item in profile["coordinates"]["zero_zero_stations"]}
    assert expected == {"304", "36", "724", "747"}
    assert with_flag(batch, "coordinates_invalid") == expected
    mappable = sum(station.mappable for station in batch.stations)
    assert mappable == profile["coordinates"]["valid"]


def test_invalid_last_reported_are_the_documented_stations(
    batch: StationSnapshotBatch, profile: dict[str, Any]
) -> None:
    stations = profile["coordinates"]["zero_zero_stations"]
    expected = {item["station_id"] for item in stations if item["last_reported_invalid"]}
    assert profile["last_reported"]["invalid"] == 3 == len(expected)
    assert with_flag(batch, "last_reported_invalid") == expected
    assert with_flag(batch, "status_invalid") == expected
    assert with_flag(batch, "last_reported_future") == set()
    invalid = [station for station in batch.stations if station.station_id in expected]
    assert all(station.observed_at is None for station in invalid)
    assert all(station.num_bikes_available is None for station in invalid)


def test_every_integer_flag_is_coerced_with_its_own_detail(batch: StationSnapshotBatch) -> None:
    assert len(with_flag(batch, "flag_coerced")) == 1121
    details = sum(
        detail.code == "flag_coerced"
        for station in batch.stations
        for detail in station.quality_details
    )
    assert details == 3363


def test_not_installed_stations(batch: StationSnapshotBatch, profile: dict[str, Any]) -> None:
    not_installed = sum(station.is_installed is False for station in batch.stations)
    assert not_installed == profile["flags"]["combinations"]["0,1,1"] == 5
    out = sum(station.service_state == "out_of_service" for station in batch.stations)
    assert out == not_installed


def test_stale_flag_matches_the_older_than_one_hour_figure(
    batch: StationSnapshotBatch, profile: dict[str, Any]
) -> None:
    assert len(with_flag(batch, "stale")) == profile["last_reported"]["older_than"]["3600"] == 63
    assert sum(station.freshness == "stale" for station in batch.stations) == 63


def test_ages_match_the_audit(batch: StationSnapshotBatch, profile: dict[str, Any]) -> None:
    ages = [
        (batch.collected_at - station.observed_at).total_seconds()
        for station in batch.stations
        if station.observed_at is not None
    ]
    documented = profile["last_reported"]["age_seconds"]
    assert len(ages) == documented["n"]
    assert min(ages) == documented["min"]
    assert max(ages) == documented["max"]


def test_state_counts(batch: StationSnapshotBatch) -> None:
    assert Counter(station.state for station in batch.stations) == {
        "in_service": 1055,
        "stale": 61,
        "quarantined": 4,
        "out_of_service": 1,
    }


def test_source_times_stay_apart(batch: StationSnapshotBatch) -> None:
    status_feed = load_json("en/station_status.json")
    assert batch.data_as_of == datetime.fromtimestamp(status_feed["last_updated"], UTC)
    assert batch.data_as_of != batch.collected_at


def test_unknown_fields_of_the_snapshot_stay_reachable(
    batch: StationSnapshotBatch, feeds: FetchedFeeds
) -> None:
    station = batch.stations[0]
    assert station.information_ref is not None
    record = feeds.information.data.stations[station.information_ref.record_index]
    assert isinstance(record, dict)
    assert record["station_id"] == station.station_id
    assert "rental_methods" in record
    assert "rental_methods" not in type(station).model_fields


def test_provenance_hashes_match_the_manifest(
    batch: StationSnapshotBatch, feeds: FetchedFeeds
) -> None:
    by_feed = {entry["feed"]: entry for entry in load_json("manifest.json")["files"]}
    assert batch.provenance[0].sha256 == by_feed["station_information"]["sha256"]
    assert batch.provenance[1].sha256 == by_feed["station_status"]["sha256"]
    assert feeds.status_payload.sha256 == by_feed["station_status"]["sha256"]
