import math
from dataclasses import replace

import pytest
from blind_helpers import (
    SOURCE,
    assert_quality,
    envelope,
    feeds,
    information,
    single,
)
from pydantic import JsonValue

from citypulse.gbfs.models import Station
from citypulse.gbfs.normalise import normalise
from citypulse.gbfs.raw import RawStationFeed


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (90, 180),
        (-90, -180),
        (90, -180),
        (-90, 180),
        (0, 180),
        (90, 0),
        (1.0, 0.0),
        (45.4, -71.9),
    ],
)
def test_coordinate_limits_and_locations_outside_montreal_are_valid(
    latitude: float, longitude: float
) -> None:
    station = single(info=information(lat=latitude, lon=longitude))
    assert station.latitude == latitude
    assert station.longitude == longitude
    assert station.state == "in_service"
    assert station.mappable is True
    assert station.risk_eligible is True
    assert_quality(station, set())


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (0, 0),
        (0.0, -0.0),
        (90.000001, 0),
        (-90.000001, 0),
        (45, 180.000001),
        (45, -180.000001),
        (True, -73),
        (False, -73),
        (45, True),
        (45, False),
        (None, -73),
        (45, None),
        ("45", -73),
        (45, "-73"),
        ([], -73),
        (45, {}),
    ],
)
def test_invalid_coordinates_clear_both_values_and_quarantine(
    latitude: JsonValue, longitude: JsonValue
) -> None:
    station = single(info=information(lat=latitude, lon=longitude))
    _assert_quarantined_coordinates(station)


@pytest.mark.parametrize("field", ["lat", "lon"])
def test_absent_coordinate_quarantines_the_pair(field: str) -> None:
    record = information()
    record.pop(field)
    station = single(info=record)
    _assert_quarantined_coordinates(station)


@pytest.mark.parametrize("field", ["lat", "lon"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_coordinates_are_rejected_in_pure_normalisation(
    field: str, value: float
) -> None:
    inputs = feeds()
    parsed = RawStationFeed.model_validate(envelope([information(**{field: value})]))
    batch = normalise(replace(inputs, information=parsed), source=SOURCE)
    assert len(batch.stations) == 1
    _assert_quarantined_coordinates(batch.stations[0])


def _assert_quarantined_coordinates(station: Station) -> None:
    assert station.latitude is None
    assert station.longitude is None
    assert station.state == "quarantined"
    assert station.mappable is False
    assert station.risk_eligible is False
    assert station.status_valid is True
    assert station.service_state == "in_service"
    assert station.freshness == "fresh"
    assert station.num_bikes_available == 3
    assert station.num_docks_available == 7
    assert station.occupancy == pytest.approx(0.3)
    assert_quality(station, {("coordinates_invalid", "station_information.coordinates")})


@pytest.mark.parametrize("capacity", [0, 1, 100])
def test_nonnegative_integer_capacity_is_kept(capacity: int) -> None:
    station = single(info=information(capacity=capacity))
    assert station.capacity == capacity
    assert type(station.capacity) is int
    assert station.occupancy == pytest.approx(0.3)
    assert station.status_valid is True
    assert_quality(station, set())


@pytest.mark.parametrize("capacity", [None, -1, True, False, 1.0, "1", [], {}])
def test_invalid_capacity_is_null_without_invalidating_status(capacity: JsonValue) -> None:
    station = single(info=information(capacity=capacity))
    assert station.capacity is None
    assert station.status_valid is True
    assert station.state == "in_service"
    assert station.risk_eligible is True
    assert station.occupancy == pytest.approx(0.3)
    assert_quality(station, {("capacity_invalid", "station_information.capacity")})


def test_absent_capacity_is_null_without_a_flag() -> None:
    record = information()
    record.pop("capacity")
    station = single(info=record)
    assert station.capacity is None
    assert station.status_valid is True
    assert station.occupancy == pytest.approx(0.3)
    assert_quality(station, set())


@pytest.mark.parametrize("field", ["name", "short_name", "external_id"])
@pytest.mark.parametrize("value", ["", "  kept verbatim  ", "é"])
def test_string_identity_fields_are_kept_verbatim(field: str, value: str) -> None:
    station = single(info=information(**{field: value}))
    actual = {
        "name": station.name,
        "short_name": station.short_name,
        "external_id": station.external_id,
    }
    assert actual[field] == value
    assert_quality(station, set())


@pytest.mark.parametrize("field", ["name", "short_name", "external_id"])
@pytest.mark.parametrize("value", [None, False, 1, 1.0, [], {}])
def test_non_string_identity_fields_are_null_and_flagged(field: str, value: JsonValue) -> None:
    station = single(info=information(**{field: value}))
    actual = {
        "name": station.name,
        "short_name": station.short_name,
        "external_id": station.external_id,
    }
    assert actual[field] is None
    assert station.status_valid is True
    assert station.risk_eligible is True
    assert_quality(station, {("name_invalid", f"station_information.{field}")})


@pytest.mark.parametrize("field", ["name", "short_name", "external_id"])
def test_absent_identity_fields_are_null_without_a_flag(field: str) -> None:
    record = information()
    record.pop(field)
    station = single(info=record)
    actual = {
        "name": station.name,
        "short_name": station.short_name,
        "external_id": station.external_id,
    }
    assert actual[field] is None
    assert_quality(station, set())


def test_repeated_information_quality_codes_have_distinct_sorted_details() -> None:
    station = single(info=information(name=None, short_name=7, external_id=False))
    assert station.name is None
    assert station.short_name is None
    assert station.external_id is None
    assert_quality(
        station,
        {
            ("name_invalid", "station_information.name"),
            ("name_invalid", "station_information.short_name"),
            ("name_invalid", "station_information.external_id"),
        },
    )
