from datetime import UTC, datetime

import pytest
from blind_helpers import (
    COUNT_FIELDS,
    FLAG_FIELDS,
    R_SECONDS,
    assert_counts_null,
    assert_quality,
    count_values,
    flag_values,
    information,
    single,
    status,
)
from pydantic import JsonValue


@pytest.mark.parametrize("field", FLAG_FIELDS)
@pytest.mark.parametrize(
    ("value", "expected", "coerced"),
    [(True, True, False), (False, False, False), (1, True, True), (0, False, True)],
)
def test_boolean_flags_and_only_integer_zero_one_are_accepted(
    field: str, value: JsonValue, expected: bool, coerced: bool
) -> None:
    station = single(report=status(**{field: value}))
    expected_flags: dict[str, bool] = dict.fromkeys(FLAG_FIELDS, True)
    expected_flags[field] = expected
    for name, actual in zip(FLAG_FIELDS, flag_values(station), strict=True):
        assert actual is expected_flags[name]
    assert station.status_valid is True
    assert station.service_state == ("in_service" if expected else "out_of_service")
    assert station.state == ("in_service" if expected else "out_of_service")
    assert station.risk_eligible is expected
    assert station.num_bikes_available == 3
    assert station.num_docks_available == 7
    assert station.occupancy == pytest.approx(0.3)
    expected_details: set[tuple[str, str]] = (
        {("flag_coerced", f"station_status.{field}")} if coerced else set()
    )
    assert_quality(station, expected_details)


@pytest.mark.parametrize("field", FLAG_FIELDS)
@pytest.mark.parametrize("value", [None, -1, 2, 1.0, 0.0, "1", "true", [], {}])
def test_invalid_flag_invalidates_status_but_keeps_valid_observation_time(
    field: str, value: JsonValue
) -> None:
    _assert_invalid_flag(field, status(**{field: value}))


@pytest.mark.parametrize("field", FLAG_FIELDS)
def test_absent_flag_invalidates_status(field: str) -> None:
    record = status()
    record.pop(field)
    _assert_invalid_flag(field, record)


def _assert_invalid_flag(field: str, record: dict[str, JsonValue]) -> None:
    station = single(report=record)
    for name, actual in zip(FLAG_FIELDS, flag_values(station), strict=True):
        if name == field:
            assert actual is None
        else:
            assert actual is True
    assert station.status_valid is False
    assert station.service_state == "unknown"
    assert station.freshness == "fresh"
    assert station.state == "status_invalid"
    assert station.risk_eligible is False
    assert station.observed_at == datetime.fromtimestamp(R_SECONDS - 10, UTC)
    assert_counts_null(station)
    assert station.other_counts == {"num_ebikes_available": None}
    assert_quality(
        station,
        {
            ("flag_invalid", f"station_status.{field}"),
            ("status_invalid", "station_status.record"),
        },
    )


def test_false_flag_wins_over_an_unknown_service_flag() -> None:
    station = single(report=status(is_installed=False, is_renting=None))
    assert station.is_installed is False
    assert station.is_renting is None
    assert station.is_returning is True
    assert station.service_state == "out_of_service"
    assert station.status_valid is False
    assert station.state == "status_invalid"
    assert_counts_null(station)
    assert_quality(
        station,
        {
            ("flag_invalid", "station_status.is_renting"),
            ("status_invalid", "station_status.record"),
        },
    )


def test_out_of_service_precedes_stale_and_keeps_counts() -> None:
    station = single(report=status(is_installed=0, last_reported=R_SECONDS - 3601))
    assert station.is_installed is False
    assert station.is_renting is True
    assert station.is_returning is True
    assert station.status_valid is True
    assert station.service_state == "out_of_service"
    assert station.freshness == "stale"
    assert station.state == "out_of_service"
    assert station.risk_eligible is False
    assert station.num_bikes_available == 3
    assert station.num_docks_available == 7
    assert station.num_bikes_disabled == 2
    assert station.num_docks_disabled == 1
    assert station.other_counts == {"num_ebikes_available": 1}
    assert station.occupancy == pytest.approx(0.3)
    assert_quality(
        station,
        {
            ("flag_coerced", "station_status.is_installed"),
            ("stale", "station_status.last_reported"),
        },
    )


@pytest.mark.parametrize("field", (*COUNT_FIELDS, "num_widgets"))
@pytest.mark.parametrize("value", [0, 1, 1000000])
def test_every_present_num_field_accepts_nonnegative_integers(field: str, value: int) -> None:
    station = single(report=status(**{field: value}))
    actual = count_values(station)[field]
    assert actual == value
    assert type(actual) is int
    assert station.status_valid is True
    assert station.risk_eligible is True
    assert_quality(station, set())


@pytest.mark.parametrize("field", (*COUNT_FIELDS, "num_widgets"))
@pytest.mark.parametrize(
    ("value", "code"),
    [
        (-1, "count_negative"),
        (None, "count_invalid"),
        (True, "count_invalid"),
        (False, "count_invalid"),
        (1.0, "count_invalid"),
        (-1.0, "count_invalid"),
        ("1", "count_invalid"),
        ([], "count_invalid"),
        ({}, "count_invalid"),
    ],
)
def test_invalid_counts_are_null_and_only_required_counts_invalidate_status(
    field: str, value: JsonValue, code: str
) -> None:
    station = single(report=status(**{field: value}))
    assert count_values(station)[field] is None
    required = field in {"num_bikes_available", "num_docks_available"}
    expected = {(code, f"station_status.{field}")}
    if required:
        assert station.status_valid is False
        assert station.state == "status_invalid"
        assert station.risk_eligible is False
        assert_counts_null(station)
        assert station.other_counts == {"num_ebikes_available": None}
        expected.add(("status_invalid", "station_status.record"))
    else:
        assert station.status_valid is True
        assert station.state == "in_service"
        assert station.risk_eligible is True
        assert station.num_bikes_available == 3
        assert station.num_docks_available == 7
        assert station.occupancy == pytest.approx(0.3)
    assert station.service_state == "in_service"
    assert station.freshness == "fresh"
    assert station.observed_at == datetime.fromtimestamp(R_SECONDS - 10, UTC)
    assert all(value is True for value in flag_values(station))
    assert_quality(station, expected)


@pytest.mark.parametrize("field", COUNT_FIELDS)
def test_absent_counts_distinguish_required_and_auxiliary_fields(field: str) -> None:
    record = status()
    record.pop(field)
    station = single(report=record)
    if field in {"num_bikes_available", "num_docks_available"}:
        assert station.status_valid is False
        assert station.state == "status_invalid"
        assert_counts_null(station)
        assert station.other_counts == {"num_ebikes_available": None}
        assert_quality(
            station,
            {
                ("count_invalid", f"station_status.{field}"),
                ("status_invalid", "station_status.record"),
            },
        )
    else:
        assert station.status_valid is True
        assert station.state == "in_service"
        assert station.occupancy == pytest.approx(0.3)
        if field == "num_ebikes_available":
            assert station.other_counts == {}
        else:
            assert count_values(station)[field] is None
            assert station.other_counts == {"num_ebikes_available": 1}
        assert_quality(station, set())


@pytest.mark.parametrize(
    ("bikes", "docks", "capacity", "expected"),
    [
        (3, 7, 100, 0.3),
        (0, 10, 0, 0.0),
        (10, 0, 999, 1.0),
        (0, 0, 100, None),
        (1, 2, 300, 1 / 3),
    ],
)
def test_occupancy_uses_available_counts_and_never_capacity_or_disabled_counts(
    bikes: int, docks: int, capacity: int, expected: float | None
) -> None:
    station = single(
        info=information(capacity=capacity),
        report=status(
            num_bikes_available=bikes,
            num_docks_available=docks,
            num_bikes_disabled=9,
            num_docks_disabled=11,
        ),
    )
    assert station.num_bikes_available == bikes
    assert station.num_docks_available == docks
    assert station.num_bikes_disabled == 9
    assert station.num_docks_disabled == 11
    assert station.status_valid is True
    assert station.risk_eligible is True
    if expected is None:
        assert station.occupancy is None
    else:
        assert station.occupancy == pytest.approx(expected)
    assert_quality(station, set())
