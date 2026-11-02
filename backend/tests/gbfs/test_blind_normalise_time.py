from datetime import UTC, datetime, timedelta

import pytest
from blind_helpers import (
    FLOOR,
    R_SECONDS,
    SOURCE,
    R,
    assert_counts_null,
    assert_quality,
    feeds,
    flag_values,
    single,
    status,
)
from pydantic import JsonValue

from citypulse.gbfs.normalise import NormalisationPolicy, normalise


@pytest.mark.parametrize(
    ("value", "valid", "freshness", "future"),
    [
        (FLOOR - 1, False, "unknown", False),
        (FLOOR, True, "stale", False),
        (R_SECONDS, True, "fresh", False),
        (R_SECONDS + 1, True, "fresh", True),
        (R_SECONDS + 300, True, "fresh", True),
        (R_SECONDS + 301, False, "unknown", False),
        (86400, False, "unknown", False),
        (None, False, "unknown", False),
        (True, False, "unknown", False),
        (False, False, "unknown", False),
        (float(R_SECONDS), False, "unknown", False),
        (str(R_SECONDS), False, "unknown", False),
        ([], False, "unknown", False),
        ({}, False, "unknown", False),
    ],
)
def test_last_reported_validation_and_inclusive_boundaries(
    value: JsonValue, valid: bool, freshness: str, future: bool
) -> None:
    station = single(report=status(last_reported=value))
    assert station.status_valid is valid
    assert station.freshness == freshness
    assert station.service_state == "in_service"
    assert all(flag is True for flag in flag_values(station))
    expected: set[tuple[str, str]] = set()
    if valid:
        assert isinstance(value, int)
        assert not isinstance(value, bool)
        assert station.observed_at == datetime.fromtimestamp(value, UTC)
        assert station.observed_at is not None
        assert station.observed_at.utcoffset() == timedelta(0)
        assert station.num_bikes_available == 3
        assert station.num_docks_available == 7
        assert station.occupancy == pytest.approx(0.3)
        assert station.state == ("stale" if freshness == "stale" else "in_service")
        assert station.risk_eligible is (freshness == "fresh")
    else:
        assert station.observed_at is None
        assert station.state == "status_invalid"
        assert station.risk_eligible is False
        assert_counts_null(station)
        assert station.other_counts == {"num_ebikes_available": None}
        expected.update(
            {
                ("last_reported_invalid", "station_status.last_reported"),
                ("status_invalid", "station_status.record"),
            }
        )
    if future:
        expected.add(("last_reported_future", "station_status.last_reported"))
    if freshness == "stale":
        expected.add(("stale", "station_status.last_reported"))
    assert_quality(station, expected)


def test_absent_last_reported_invalidates_status() -> None:
    record = status()
    record.pop("last_reported")
    station = single(report=record)
    assert station.observed_at is None
    assert station.status_valid is False
    assert station.freshness == "unknown"
    assert station.service_state == "in_service"
    assert station.state == "status_invalid"
    assert_counts_null(station)
    assert_quality(
        station,
        {
            ("last_reported_invalid", "station_status.last_reported"),
            ("status_invalid", "station_status.record"),
        },
    )


def test_policy_defaults_match_the_frozen_contract() -> None:
    policy = NormalisationPolicy()
    assert policy.stale_after_seconds == 3600
    assert policy.future_skew_seconds == 300


@pytest.mark.parametrize(
    ("age", "threshold", "stale"),
    [
        (3600, None, False),
        (3601, None, True),
        (10, 10, False),
        (11, 10, True),
        (0, 0, False),
        (1, 0, True),
    ],
)
def test_staleness_uses_a_strict_configurable_threshold(
    age: int, threshold: int | None, stale: bool
) -> None:
    policy = None if threshold is None else NormalisationPolicy(stale_after_seconds=threshold)
    station = single(report=status(last_reported=R_SECONDS - age), policy=policy)
    assert station.status_valid is True
    assert station.freshness == ("stale" if stale else "fresh")
    assert station.state == ("stale" if stale else "in_service")
    assert station.risk_eligible is (not stale)
    assert station.num_bikes_available == 3
    assert station.num_docks_available == 7
    expected: set[tuple[str, str]] = {("stale", "station_status.last_reported")} if stale else set()
    assert_quality(station, expected)


@pytest.mark.parametrize(
    ("skew", "offset", "valid"),
    [(2, 2, True), (2, 3, False), (600, 600, True), (600, 601, False), (0, 0, True), (0, 1, False)],
)
def test_custom_future_skew_is_inclusive(skew: int, offset: int, valid: bool) -> None:
    station = single(
        report=status(last_reported=R_SECONDS + offset),
        policy=NormalisationPolicy(future_skew_seconds=skew),
    )
    assert station.status_valid is valid
    if valid:
        assert station.observed_at == datetime.fromtimestamp(R_SECONDS + offset, UTC)
        assert station.state == "in_service"
        expected: set[tuple[str, str]] = (
            {("last_reported_future", "station_status.last_reported")} if offset else set()
        )
    else:
        assert station.observed_at is None
        assert station.state == "status_invalid"
        assert_counts_null(station)
        expected = {
            ("last_reported_invalid", "station_status.last_reported"),
            ("status_invalid", "station_status.record"),
        }
    assert_quality(station, expected)


def test_provenance_keeps_source_times_and_fetch_times_separate() -> None:
    information_at = R - timedelta(seconds=1000)
    inputs = feeds(
        information_at=information_at,
        information_metadata={"last_updated": R_SECONDS - 1100},
        status_metadata={"last_updated": R_SECONDS - 20},
    )
    batch = normalise(inputs, source=SOURCE)
    assert batch.source == SOURCE
    assert batch.collected_at == R
    assert batch.data_as_of == datetime.fromtimestamp(R_SECONDS - 20, UTC)
    assert len(batch.provenance) == 2
    for item, raw, expected_time in zip(
        batch.provenance,
        (inputs.information_payload, inputs.status_payload),
        (R_SECONDS - 1100, R_SECONDS - 20),
        strict=True,
    ):
        assert item.feed_name == raw.feed_name
        assert item.sha256 == raw.sha256
        assert item.collected_at == raw.collected_at
        assert item.last_updated == datetime.fromtimestamp(expected_time, UTC)
        assert item.collected_at.utcoffset() == timedelta(0)
        assert item.last_updated is not None
        assert item.last_updated.utcoffset() == timedelta(0)
        assert item.ttl_seconds == 10
        assert item.version == "2.2"
    assert [item.feed_name for item in batch.provenance] == [
        "station_information",
        "station_status",
    ]
    assert batch.provenance[0].collected_at == information_at
    assert len(batch.stations) == 1
    assert batch.stations[0].observed_at == datetime.fromtimestamp(R_SECONDS - 10, UTC)
    assert batch.stations[0].state == "in_service"
    assert_quality(batch, set())


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
@pytest.mark.parametrize(
    ("case", "valid"),
    [("floor_minus_one", False), ("floor", True), ("skew", True), ("over_skew", False)],
)
def test_feed_timestamp_boundaries_use_that_payloads_fetch_time(
    feed_name: str, case: str, valid: bool
) -> None:
    information_at = R - timedelta(seconds=1000)
    own_time = information_at if feed_name == "station_information" else R
    own_seconds = int(own_time.timestamp())
    value = {
        "floor_minus_one": FLOOR - 1,
        "floor": FLOOR,
        "skew": own_seconds + 300,
        "over_skew": own_seconds + 301,
    }[case]
    info_metadata: dict[str, JsonValue] = {"last_updated": R_SECONDS - 1100}
    status_metadata: dict[str, JsonValue] = {"last_updated": R_SECONDS - 20}
    target = info_metadata if feed_name == "station_information" else status_metadata
    target["last_updated"] = value
    inputs = feeds(
        information_at=information_at,
        information_metadata=info_metadata,
        status_metadata=status_metadata,
    )
    batch = normalise(inputs, source=SOURCE)
    index = 0 if feed_name == "station_information" else 1
    if valid:
        assert batch.provenance[index].last_updated == datetime.fromtimestamp(value, UTC)
        assert_quality(batch, set())
    else:
        assert batch.provenance[index].last_updated is None
        assert_quality(batch, {("feed_timestamp_invalid", f"{feed_name}.last_updated")})
    assert len(batch.stations) == 1
    assert batch.stations[0].state == "in_service"
    assert_quality(batch.stations[0], set())
    assert batch.collected_at == R
    if feed_name == "station_status" and not valid:
        assert batch.data_as_of is None


@pytest.mark.parametrize("skew", [1, 2])
def test_custom_skew_also_applies_to_both_feed_timestamps(skew: int) -> None:
    information_at = R - timedelta(seconds=1000)
    inputs = feeds(
        information_at=information_at,
        information_metadata={"last_updated": R_SECONDS - 998},
        status_metadata={"last_updated": R_SECONDS + 2},
    )
    batch = normalise(
        inputs,
        source=SOURCE,
        policy=NormalisationPolicy(future_skew_seconds=skew),
    )
    if skew == 2:
        assert batch.provenance[0].last_updated == information_at + timedelta(seconds=2)
        assert batch.provenance[1].last_updated == R + timedelta(seconds=2)
        assert_quality(batch, set())
    else:
        assert batch.provenance[0].last_updated is None
        assert batch.provenance[1].last_updated is None
        assert batch.data_as_of is None
        assert_quality(
            batch,
            {
                ("feed_timestamp_invalid", "station_information.last_updated"),
                ("feed_timestamp_invalid", "station_status.last_updated"),
            },
        )
