from copy import deepcopy
from datetime import UTC, datetime

import pytest
from blind_helpers import (
    R_SECONDS,
    SOURCE,
    JsonObject,
    R,
    assert_counts_null,
    assert_quality,
    feeds,
    flag_values,
    information,
    single,
    status,
)
from pydantic import JsonValue

from citypulse.gbfs.normalise import normalise


def test_valid_station_has_expected_values_and_references() -> None:
    batch = normalise(feeds(), source=SOURCE)
    assert batch.source == SOURCE
    assert batch.collected_at == R
    assert batch.rejected_records == ()
    assert_quality(batch, set())
    assert len(batch.stations) == 1
    station = batch.stations[0]
    assert station.station_id == "s"
    assert (station.name, station.short_name, station.external_id) == (
        "Fixture station",
        "001",
        "external-s",
    )
    assert (station.latitude, station.longitude, station.capacity) == (45.5, -73.6, 100)
    assert station.num_bikes_available == 3
    assert station.num_docks_available == 7
    assert station.num_bikes_disabled == 2
    assert station.num_docks_disabled == 1
    assert station.other_counts == {"num_ebikes_available": 1}
    assert all(value is True for value in flag_values(station))
    assert station.observed_at == datetime.fromtimestamp(R_SECONDS - 10, UTC)
    assert station.occupancy == pytest.approx(0.3)
    assert station.status_valid is True
    assert station.service_state == "in_service"
    assert station.freshness == "fresh"
    assert station.state == "in_service"
    assert station.mappable is True
    assert station.risk_eligible is True
    assert station.information_ref is not None
    assert station.status_ref is not None
    assert (station.information_ref.feed, station.information_ref.record_index) == (
        "station_information",
        0,
    )
    assert (station.status_ref.feed, station.status_ref.record_index) == ("station_status", 0)
    assert_quality(station, set())


def test_information_without_status_is_missing_and_never_zero() -> None:
    batch = normalise(feeds(status_records=[]), source=SOURCE)
    assert len(batch.stations) == 1
    station = batch.stations[0]
    assert station.station_id == "s"
    assert station.information_ref is not None
    assert station.status_ref is None
    assert station.status_valid is False
    assert station.observed_at is None
    assert all(value is None for value in flag_values(station))
    assert_counts_null(station)
    assert station.service_state == "unknown"
    assert station.freshness == "unknown"
    assert station.state == "no_status"
    assert station.mappable is True
    assert station.risk_eligible is False
    assert_quality(station, {("status_missing", "station_status.record")})


def test_status_without_information_is_kept_and_quarantined() -> None:
    inputs = feeds(information_records=[])
    batch = normalise(inputs, source=SOURCE)
    assert len(batch.stations) == 1
    assert batch.rejected_records == ()
    station = batch.stations[0]
    assert station.station_id == "s"
    assert station.information_ref is None
    assert station.status_ref is not None
    assert station.status_ref.record_index == 0
    assert station.status_ref.feed == "station_status"
    assert station.name is None
    assert station.short_name is None
    assert station.external_id is None
    assert station.latitude is None
    assert station.longitude is None
    assert station.capacity is None
    assert station.num_bikes_available == 3
    assert station.num_docks_available == 7
    assert station.occupancy == pytest.approx(0.3)
    assert station.status_valid is True
    assert station.service_state == "in_service"
    assert station.freshness == "fresh"
    assert station.state == "quarantined"
    assert station.mappable is False
    assert station.risk_eligible is False
    assert_quality(station, {("information_missing", "station_information.record")})
    assert inputs.status.data.stations == [status()]


def test_empty_feeds_give_an_empty_batch() -> None:
    batch = normalise(feeds([], []), source=SOURCE)
    assert batch.stations == ()
    assert batch.rejected_records == ()
    assert batch.source == SOURCE
    assert batch.collected_at == R
    assert len(batch.provenance) == 2
    assert_quality(batch, set())


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
@pytest.mark.parametrize("record", [None, False, 0, 1.0, "record", []])
def test_non_object_entries_are_rejected(feed_name: str, record: JsonValue) -> None:
    info_records: list[JsonValue] = [information("keep")]
    status_records: list[JsonValue] = [status("keep")]
    if feed_name == "station_information":
        info_records.insert(0, record)
    else:
        status_records.insert(0, record)
    batch = normalise(feeds(info_records, status_records), source=SOURCE)
    assert [station.station_id for station in batch.stations] == ["keep"]
    assert batch.stations[0].state == "in_service"
    assert len(batch.rejected_records) == 1
    rejected = batch.rejected_records[0]
    assert (rejected.source.feed, rejected.source.record_index) == (feed_name, 0)
    assert rejected.station_id is None
    assert_quality(rejected, {("record_malformed", f"{feed_name}.record")})


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
@pytest.mark.parametrize("station_id", [None, False, 1, 1.0, "", [], {}])
def test_invalid_station_ids_are_rejected(feed_name: str, station_id: JsonValue) -> None:
    _assert_invalid_id(feed_name, {"station_id": station_id})


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
def test_absent_station_id_is_rejected(feed_name: str) -> None:
    _assert_invalid_id(feed_name, {})


def _assert_invalid_id(feed_name: str, record: JsonObject) -> None:
    info_records: list[JsonValue] = [information("keep")]
    status_records: list[JsonValue] = [status("keep")]
    if feed_name == "station_information":
        info_records.insert(0, record)
    else:
        status_records.insert(0, record)
    batch = normalise(feeds(info_records, status_records), source=SOURCE)
    assert [station.station_id for station in batch.stations] == ["keep"]
    assert batch.stations[0].state == "in_service"
    assert len(batch.rejected_records) == 1
    rejected = batch.rejected_records[0]
    assert (rejected.source.feed, rejected.source.record_index) == (feed_name, 0)
    assert_quality(rejected, {("station_id_invalid", f"{feed_name}.station_id")})


@pytest.mark.parametrize(("info_copies", "status_copies"), [(2, 1), (1, 2), (2, 2), (2, 0), (0, 2)])
def test_duplicate_id_rejects_every_record_including_the_counterpart(
    info_copies: int, status_copies: int
) -> None:
    info_records: list[JsonValue] = [information("dup") for _ in range(info_copies)]
    status_records: list[JsonValue] = [status("dup") for _ in range(status_copies)]
    info_records.append(information("keep"))
    status_records.append(status("keep"))
    batch = normalise(feeds(info_records, status_records), source=SOURCE)
    assert [station.station_id for station in batch.stations] == ["keep"]
    expected = [
        *[("station_information", index) for index in range(info_copies)],
        *[("station_status", index) for index in range(status_copies)],
    ]
    assert [
        (record.source.feed, record.source.record_index) for record in batch.rejected_records
    ] == expected
    for record in batch.rejected_records:
        assert record.station_id == "dup"
        assert_quality(record, {("duplicate_id", f"{record.source.feed}.station_id")})


def test_ids_are_unchanged_sorted_by_code_point_and_refs_keep_original_indices() -> None:
    ids = ["z", "a", " A", "A", "2", "10", "é"]
    reversed_ids = list(reversed(ids))
    batch = normalise(
        feeds([information(key) for key in ids], [status(key) for key in reversed_ids]),
        source=SOURCE,
    )
    assert [station.station_id for station in batch.stations] == [
        " A",
        "10",
        "2",
        "A",
        "a",
        "z",
        "é",
    ]
    assert batch.rejected_records == ()
    for station in batch.stations:
        assert station.information_ref is not None
        assert station.status_ref is not None
        assert station.information_ref.record_index == ids.index(station.station_id)
        assert station.status_ref.record_index == reversed_ids.index(station.station_id)
        assert station.state == "in_service"


def test_quarantine_preserves_invalid_status_service_and_freshness_facts() -> None:
    station = single(
        info=information(lat=0, lon=0),
        report=status(is_installed=False, is_renting="yes", last_reported=R_SECONDS - 3601),
    )
    assert station.state == "quarantined"
    assert station.status_valid is False
    assert station.service_state == "out_of_service"
    assert station.freshness == "stale"
    assert station.is_installed is False
    assert station.is_renting is None
    assert station.observed_at == datetime.fromtimestamp(R_SECONDS - 3601, UTC)
    assert station.mappable is False
    assert station.risk_eligible is False
    assert_counts_null(station)
    assert_quality(
        station,
        {
            ("coordinates_invalid", "station_information.coordinates"),
            ("flag_invalid", "station_status.is_renting"),
            ("stale", "station_status.last_reported"),
            ("status_invalid", "station_status.record"),
        },
    )


def test_quarantine_precedes_missing_status() -> None:
    batch = normalise(feeds([information(lat=0, lon=0)], []), source=SOURCE)
    assert len(batch.stations) == 1
    station = batch.stations[0]
    assert station.state == "quarantined"
    assert station.status_valid is False
    assert station.service_state == "unknown"
    assert station.freshness == "unknown"
    assert_counts_null(station)
    assert_quality(
        station,
        {
            ("coordinates_invalid", "station_information.coordinates"),
            ("status_missing", "station_status.record"),
        },
    )


def test_invalid_status_precedes_out_of_service_and_stale() -> None:
    station = single(
        report=status(
            is_installed=False,
            num_bikes_available=-1,
            last_reported=R_SECONDS - 3601,
        )
    )
    assert station.state == "status_invalid"
    assert station.status_valid is False
    assert station.service_state == "out_of_service"
    assert station.freshness == "stale"
    assert station.mappable is True
    assert station.risk_eligible is False
    assert_counts_null(station)
    assert_quality(
        station,
        {
            ("count_negative", "station_status.num_bikes_available"),
            ("stale", "station_status.last_reported"),
            ("status_invalid", "station_status.record"),
        },
    )


def test_normalisation_is_repeatable_and_does_not_mutate_inputs() -> None:
    inputs = feeds(
        [information(extension={"nested": [None, True, 1.0]})],
        [status(is_installed=1, extension={"nested": ["kept"]})],
        information_metadata={"extension": {"feed": [1, 2]}},
    )
    original_inputs = deepcopy(inputs)
    original_source = deepcopy(SOURCE)
    first = normalise(inputs, source=SOURCE)
    assert inputs == original_inputs
    assert original_source == SOURCE
    second = normalise(inputs, source=SOURCE)
    assert first == second
    assert inputs == original_inputs
    assert original_source == SOURCE
    assert len(first.stations) == 1
    assert first.stations[0].is_installed is True
    assert_quality(first.stations[0], {("flag_coerced", "station_status.is_installed")})
