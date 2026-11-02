import pytest
from blind_helpers import (
    R_SECONDS,
    SOURCE,
    JsonObject,
    assert_quality,
    feeds,
)
from pydantic import JsonValue

from citypulse.gbfs.normalise import normalise


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
@pytest.mark.parametrize(
    "value",
    [None, True, False, float(R_SECONDS), str(R_SECONDS), [], {}],
)
def test_invalid_feed_timestamp_types_are_flagged_without_invalidating_stations(
    feed_name: str, value: JsonValue
) -> None:
    info_metadata: JsonObject = {}
    status_metadata: JsonObject = {}
    target = info_metadata if feed_name == "station_information" else status_metadata
    target["last_updated"] = value
    batch = normalise(
        feeds(information_metadata=info_metadata, status_metadata=status_metadata),
        source=SOURCE,
    )
    index = 0 if feed_name == "station_information" else 1
    assert batch.provenance[index].last_updated is None
    assert_quality(batch, {("feed_timestamp_invalid", f"{feed_name}.last_updated")})
    assert len(batch.stations) == 1
    assert batch.stations[0].status_valid is True
    assert batch.stations[0].state == "in_service"
    assert_quality(batch.stations[0], set())
    if feed_name == "station_status":
        assert batch.data_as_of is None


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
def test_absent_feed_timestamp_is_flagged(feed_name: str) -> None:
    inputs = feeds(
        information_omit=("last_updated",) if feed_name == "station_information" else (),
        status_omit=("last_updated",) if feed_name == "station_status" else (),
    )
    batch = normalise(inputs, source=SOURCE)
    index = 0 if feed_name == "station_information" else 1
    assert batch.provenance[index].last_updated is None
    assert_quality(batch, {("feed_timestamp_invalid", f"{feed_name}.last_updated")})
    assert len(batch.stations) == 1
    assert batch.stations[0].state == "in_service"
    if feed_name == "station_status":
        assert batch.data_as_of is None


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
@pytest.mark.parametrize("value", [0, 1, 10])
def test_nonnegative_integer_ttl_is_kept(feed_name: str, value: int) -> None:
    info_metadata: JsonObject = {}
    status_metadata: JsonObject = {}
    target = info_metadata if feed_name == "station_information" else status_metadata
    target["ttl"] = value
    batch = normalise(
        feeds(information_metadata=info_metadata, status_metadata=status_metadata),
        source=SOURCE,
    )
    index = 0 if feed_name == "station_information" else 1
    assert batch.provenance[index].ttl_seconds == value
    assert type(batch.provenance[index].ttl_seconds) is int
    assert_quality(batch, set())


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
@pytest.mark.parametrize("value", [None, -1, True, False, 1.0, "10", [], {}])
def test_invalid_ttl_is_null_and_flagged(feed_name: str, value: JsonValue) -> None:
    info_metadata: JsonObject = {}
    status_metadata: JsonObject = {}
    target = info_metadata if feed_name == "station_information" else status_metadata
    target["ttl"] = value
    batch = normalise(
        feeds(information_metadata=info_metadata, status_metadata=status_metadata),
        source=SOURCE,
    )
    index = 0 if feed_name == "station_information" else 1
    assert batch.provenance[index].ttl_seconds is None
    assert_quality(batch, {("ttl_invalid", f"{feed_name}.ttl")})


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
def test_absent_ttl_is_null_without_a_flag(feed_name: str) -> None:
    inputs = feeds(
        information_omit=("ttl",) if feed_name == "station_information" else (),
        status_omit=("ttl",) if feed_name == "station_status" else (),
    )
    batch = normalise(inputs, source=SOURCE)
    index = 0 if feed_name == "station_information" else 1
    assert batch.provenance[index].ttl_seconds is None
    assert_quality(batch, set())


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
@pytest.mark.parametrize("value", ["", "2.2"])
def test_string_version_is_kept(feed_name: str, value: str) -> None:
    info_metadata: JsonObject = {}
    status_metadata: JsonObject = {}
    target = info_metadata if feed_name == "station_information" else status_metadata
    target["version"] = value
    batch = normalise(
        feeds(information_metadata=info_metadata, status_metadata=status_metadata),
        source=SOURCE,
    )
    index = 0 if feed_name == "station_information" else 1
    assert batch.provenance[index].version == value
    assert_quality(batch, set())


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
@pytest.mark.parametrize("value", [None, False, 2, 2.2, [], {}])
def test_non_string_version_is_null_without_a_flag(feed_name: str, value: JsonValue) -> None:
    info_metadata: JsonObject = {}
    status_metadata: JsonObject = {}
    target = info_metadata if feed_name == "station_information" else status_metadata
    target["version"] = value
    batch = normalise(
        feeds(information_metadata=info_metadata, status_metadata=status_metadata),
        source=SOURCE,
    )
    index = 0 if feed_name == "station_information" else 1
    assert batch.provenance[index].version is None
    assert_quality(batch, set())


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
def test_absent_version_is_null_without_a_flag(feed_name: str) -> None:
    inputs = feeds(
        information_omit=("version",) if feed_name == "station_information" else (),
        status_omit=("version",) if feed_name == "station_status" else (),
    )
    batch = normalise(inputs, source=SOURCE)
    index = 0 if feed_name == "station_information" else 1
    assert batch.provenance[index].version is None
    assert_quality(batch, set())


def test_multiple_feed_errors_have_sorted_unique_codes_and_distinct_details() -> None:
    batch = normalise(
        feeds(
            information_metadata={"last_updated": None, "ttl": -1},
            status_metadata={"last_updated": None, "ttl": 1.0},
        ),
        source=SOURCE,
    )
    assert batch.data_as_of is None
    assert len(batch.stations) == 1
    assert batch.stations[0].state == "in_service"
    assert_quality(
        batch,
        {
            ("feed_timestamp_invalid", "station_information.last_updated"),
            ("feed_timestamp_invalid", "station_status.last_updated"),
            ("ttl_invalid", "station_information.ttl"),
            ("ttl_invalid", "station_status.ttl"),
        },
    )
    assert_quality(batch.stations[0], set())
