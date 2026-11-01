import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import JsonValue, ValidationError

from citypulse.gbfs.models import SourceContext, Station, StationSnapshotBatch
from citypulse.gbfs.normalise import NormalisationPolicy, normalise
from citypulse.gbfs.raw import FetchedFeeds, RawPayload, build_fetched_feeds

type Record = dict[str, JsonValue]

R = datetime(2026, 10, 25, 0, 15, 51, tzinfo=UTC)
R_POSIX = int(R.timestamp())
FRESH = R_POSIX - 60
FLOOR = 946684800
SOURCE = SourceContext(
    source_id="bixi",
    system_id="Bixi_MTL",
    attribution="BIXI Montréal",
    licence_url="https://creativecommons.org/licenses/by/4.0/",
)
SERVICE_FLAGS = ("is_installed", "is_renting", "is_returning")
OMIT: Record = {}


def information_record(station_id: str = "1", /, **fields: JsonValue) -> Record:
    base: Record = {
        "station_id": station_id,
        "name": "Station",
        "short_name": "S",
        "external_id": "ext",
        "lat": 45.5,
        "lon": -73.6,
        "capacity": 30,
    }
    return base | fields


def status_record(station_id: str = "1", /, **fields: JsonValue) -> Record:
    base: Record = {
        "station_id": station_id,
        "num_bikes_available": 5,
        "num_docks_available": 15,
        "num_bikes_disabled": 4,
        "num_docks_disabled": 6,
        "num_ebikes_available": 2,
        "is_installed": True,
        "is_renting": True,
        "is_returning": True,
        "last_reported": FRESH,
    }
    return base | fields


def without(record: Record, *names: str) -> Record:
    return {key: value for key, value in record.items() if key not in names}


def document(records: list[JsonValue], **fields: JsonValue) -> Record:
    base: Record = {"last_updated": R_POSIX - 5, "ttl": 10, "data": {"stations": records}}
    return base | fields


def feeds_from_bodies(information: bytes, status: bytes) -> FetchedFeeds:
    return build_fetched_feeds(
        discovery=RawPayload.from_body(
            feed_name="discovery", body=b"{}", collected_at=R, request_url="https://x/gbfs.json"
        ),
        information=RawPayload.from_body(
            feed_name="station_information",
            body=information,
            collected_at=R - timedelta(seconds=1),
            request_url="https://x/information.json",
        ),
        status=RawPayload.from_body(
            feed_name="station_status",
            body=status,
            collected_at=R,
            request_url="https://x/status.json",
        ),
    )


def make_feeds(information: Record, status: Record) -> FetchedFeeds:
    return feeds_from_bodies(json.dumps(information).encode(), json.dumps(status).encode())


def run(
    information: list[JsonValue],
    status: list[JsonValue],
    policy: NormalisationPolicy | None = None,
) -> StationSnapshotBatch:
    feeds = make_feeds(document(information), document(status))
    if policy is None:
        return normalise(feeds, source=SOURCE)
    return normalise(feeds, source=SOURCE, policy=policy)


def one(
    information: Record | None = OMIT,
    status: Record | None = OMIT,
    policy: NormalisationPolicy | None = None,
) -> Station:
    """One station. Default records are valid; None leaves the record out of its feed."""
    info = information_record() if information is OMIT else information
    stat = status_record() if status is OMIT else status
    batch = run([info] if info is not None else [], [stat] if stat is not None else [], policy)
    assert len(batch.stations) == 1
    return batch.stations[0]


def paths(station: Station, code: str) -> set[str]:
    return {detail.path for detail in station.quality_details if detail.code == code}


def assert_no_availability(station: Station) -> None:
    assert station.num_bikes_available is None
    assert station.num_docks_available is None
    assert station.num_bikes_disabled is None
    assert station.num_docks_disabled is None
    assert station.occupancy is None
    assert all(value is None for value in station.other_counts.values())


def test_valid_station_in_service() -> None:
    station = one()
    assert station.state == "in_service"
    assert station.status_valid is True
    assert station.service_state == "in_service"
    assert station.freshness == "fresh"
    assert station.quality_flags == ()
    assert station.quality_details == ()
    assert station.mappable is True
    assert station.risk_eligible is True
    assert (station.name, station.short_name, station.external_id) == ("Station", "S", "ext")
    assert (station.latitude, station.longitude, station.capacity) == (45.5, -73.6, 30)
    assert station.num_bikes_available == 5
    assert station.num_docks_available == 15
    assert station.num_bikes_disabled == 4
    assert station.num_docks_disabled == 6
    assert station.other_counts == {"num_ebikes_available": 2}
    assert station.observed_at == datetime.fromtimestamp(FRESH, UTC)
    assert station.is_installed is True


def test_ac1_information_without_status_has_null_availability_never_zero() -> None:
    station = one(status=None)
    assert station.state == "no_status"
    assert station.quality_flags == ("status_missing",)
    assert station.status_valid is False
    assert station.service_state == "unknown"
    assert station.freshness == "unknown"
    assert station.observed_at is None
    assert station.is_installed is None
    assert station.status_ref is None
    assert station.information_ref is not None
    assert station.mappable is True
    assert station.risk_eligible is False
    assert_no_availability(station)
    assert station.other_counts == {}


@pytest.mark.parametrize("value", [True, False])
def test_ac2_booleans_pass_without_a_flag(value: bool) -> None:
    station = one(status=status_record(is_renting=value))
    assert station.is_renting is value
    assert "flag_coerced" not in station.quality_flags
    assert station.status_valid is True


@pytest.mark.parametrize(("raw", "expected"), [(1, True), (0, False)])
def test_ac2_integers_are_coerced_and_flagged(raw: int, expected: bool) -> None:
    station = one(status=status_record(is_returning=raw))
    assert station.is_returning is expected
    assert paths(station, "flag_coerced") == {"station_status.is_returning"}
    assert station.status_valid is True


def test_ac2_each_coerced_flag_keeps_its_own_detail_under_one_code() -> None:
    station = one(status=status_record(is_installed=1, is_renting=1, is_returning=1))
    assert station.quality_flags == ("flag_coerced",)
    assert paths(station, "flag_coerced") == {f"station_status.{name}" for name in SERVICE_FLAGS}


@pytest.mark.parametrize("value", ["1", "true", 2, -1, 1.0, 0.0, None, [1]])
def test_dq3_other_flag_values_invalidate_the_status(value: JsonValue) -> None:
    station = one(status=status_record(is_installed=value))
    assert station.is_installed is None
    assert paths(station, "flag_invalid") == {"station_status.is_installed"}
    assert "status_invalid" in station.quality_flags
    assert station.status_valid is False
    assert station.state == "status_invalid"
    assert station.service_state == "unknown"
    assert_no_availability(station)


@pytest.mark.parametrize("name", SERVICE_FLAGS)
def test_dq3_an_absent_flag_invalidates_the_status(name: str) -> None:
    station = one(status=without(status_record(), name))
    assert paths(station, "flag_invalid") == {f"station_status.{name}"}
    assert station.state == "status_invalid"


@pytest.mark.parametrize("name", SERVICE_FLAGS)
@pytest.mark.parametrize("value", [False, 0])
def test_ac3_any_false_flag_is_out_of_service_with_counts_kept(name: str, value: JsonValue) -> None:
    station = one(status=status_record(**{name: value}))
    assert station.state == "out_of_service"
    assert station.service_state == "out_of_service"
    assert station.status_valid is True
    assert station.num_bikes_available == 5
    assert station.occupancy == 0.25
    assert station.risk_eligible is False
    assert station.mappable is True


def test_ac3_not_installed_but_renting_is_out_of_service_and_not_a_flag() -> None:
    station = one(status=status_record(is_installed=0, is_renting=1, is_returning=1))
    assert station.state == "out_of_service"
    assert "out_of_service" not in station.quality_flags


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        pytest.param(0, 0, id="zero-int"),
        pytest.param(0.0, 0.0, id="zero-float"),
        pytest.param(90.001, -73.0, id="latitude-high"),
        pytest.param(-90.001, -73.0, id="latitude-low"),
        pytest.param(45.0, 180.001, id="longitude-high"),
        pytest.param(45.0, -180.001, id="longitude-low"),
        pytest.param("45.5", -73.6, id="latitude-string"),
        pytest.param(45.5, "-73.6", id="longitude-string"),
        pytest.param(True, -73.6, id="latitude-bool"),
        pytest.param(45.5, False, id="longitude-bool"),
        pytest.param(None, -73.6, id="latitude-null"),
        pytest.param(45.5, None, id="longitude-null"),
        pytest.param(10**400, -73.6, id="latitude-huge-int"),
    ],
)
def test_ac4_invalid_coordinates_are_quarantined(latitude: JsonValue, longitude: JsonValue) -> None:
    station = one(information=information_record(lat=latitude, lon=longitude))
    assert station.state == "quarantined"
    assert station.mappable is False
    assert station.risk_eligible is False
    assert station.latitude is None
    assert station.longitude is None
    assert paths(station, "coordinates_invalid") == {"station_information.coordinates"}
    assert station.num_bikes_available == 5  # kept and counted, only left off the map


@pytest.mark.parametrize("name", ["lat", "lon"])
def test_ac4_absent_coordinate_is_quarantined(name: str) -> None:
    station = one(information=without(information_record(), name))
    assert station.state == "quarantined"
    assert station.latitude is None
    assert station.longitude is None


def test_dq5_non_finite_coordinate_from_a_huge_literal_is_quarantined() -> None:
    placeholder = json.dumps(document([information_record(lat="HUGE")])).encode()
    body = placeholder.replace(b'"HUGE"', b"1e999")
    feeds = feeds_from_bodies(body, json.dumps(document([status_record()])).encode())
    station = normalise(feeds, source=SOURCE).stations[0]
    assert station.state == "quarantined"
    assert station.latitude is None


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        pytest.param(90, 180, id="limits-high"),
        pytest.param(-90, -180, id="limits-low"),
        pytest.param(0, -73.6, id="zero-latitude-only"),
        pytest.param(45.5, 0, id="zero-longitude-only"),
        pytest.param(45.39, -71.9, id="far-from-montreal"),
        pytest.param(-33.9, 151.2, id="other-hemisphere"),
    ],
)
def test_dq5_valid_coordinates_have_no_service_area_box(latitude: float, longitude: float) -> None:
    station = one(information=information_record(lat=latitude, lon=longitude))
    assert station.state == "in_service"
    assert station.latitude == float(latitude)
    assert station.longitude == float(longitude)
    assert isinstance(station.latitude, float)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(86400, id="sentinel"),
        pytest.param(FLOOR - 1, id="below-floor"),
        pytest.param(R_POSIX + 301, id="beyond-skew"),
        pytest.param(float(FRESH), id="float"),
        pytest.param(True, id="bool"),
        pytest.param(str(FRESH), id="string"),
        pytest.param(None, id="null"),
        pytest.param(10**400, id="huge"),
    ],
)
def test_ac5_invalid_last_reported_invalidates_the_status(value: JsonValue) -> None:
    station = one(status=status_record(last_reported=value))
    assert station.observed_at is None
    assert "last_reported_invalid" in station.quality_flags
    assert "status_invalid" in station.quality_flags
    assert station.status_valid is False
    assert station.state == "status_invalid"
    assert station.freshness == "unknown"
    assert_no_availability(station)
    assert station.is_installed is True  # the flags keep their own validated values


def test_ac5_absent_last_reported_invalidates_the_status() -> None:
    station = one(status=without(status_record(), "last_reported"))
    assert station.observed_at is None
    assert station.state == "status_invalid"


def test_dq6_floor_is_inclusive() -> None:
    station = one(status=status_record(last_reported=FLOOR))
    assert station.observed_at == datetime(2000, 1, 1, tzinfo=UTC)
    assert station.status_valid is True


def test_dq6_end_of_the_future_window_is_kept_and_flagged() -> None:
    station = one(status=status_record(last_reported=R_POSIX + 300))
    assert station.observed_at == R + timedelta(seconds=300)
    assert station.quality_flags == ("last_reported_future",)
    assert station.status_valid is True
    assert station.freshness == "fresh"


def test_dq6_reference_time_itself_is_not_future() -> None:
    assert one(status=status_record(last_reported=R_POSIX)).quality_flags == ()


def test_dq6_future_window_follows_the_policy() -> None:
    policy = NormalisationPolicy(future_skew_seconds=10)
    assert one(status=status_record(last_reported=R_POSIX + 10), policy=policy).status_valid is True
    station = one(status=status_record(last_reported=R_POSIX + 11), policy=policy)
    assert station.status_valid is False


@pytest.mark.parametrize(
    ("age", "expected"), [(3599, "fresh"), (3600, "fresh"), (3601, "stale"), (86400, "stale")]
)
def test_dq7_stale_threshold_is_strict(age: int, expected: str) -> None:
    station = one(status=status_record(last_reported=R_POSIX - age))
    assert station.freshness == expected
    assert ("stale" in station.quality_flags) is (expected == "stale")
    assert station.state == ("stale" if expected == "stale" else "in_service")
    assert station.risk_eligible is (expected == "fresh")
    assert station.num_bikes_available == 5


def test_dq7_threshold_follows_the_policy() -> None:
    policy = NormalisationPolicy(stale_after_seconds=100)
    assert one(status=status_record(last_reported=R_POSIX - 100), policy=policy).state == (
        "in_service"
    )
    assert one(status=status_record(last_reported=R_POSIX - 101), policy=policy).state == "stale"


@pytest.mark.parametrize(
    ("bikes", "docks", "expected"),
    [(5, 15, 0.25), (20, 0, 1.0), (0, 20, 0.0), (1, 3, 0.25), (1, 0, 1.0), (0, 1, 0.0)],
)
def test_ac7_occupancy(bikes: int, docks: int, expected: float) -> None:
    station = one(status=status_record(num_bikes_available=bikes, num_docks_available=docks))
    assert station.occupancy == expected
    assert station.occupancy is not None


def test_ac7_zero_denominator_gives_null_without_invalidating_the_status() -> None:
    station = one(status=status_record(num_bikes_available=0, num_docks_available=0))
    assert station.occupancy is None
    assert station.status_valid is True
    assert station.num_bikes_available == 0
    assert station.num_docks_available == 0


def test_dq8_capacity_and_disabled_equipment_never_change_occupancy() -> None:
    station = one(
        information=information_record(capacity=999),
        status=status_record(num_bikes_disabled=50, num_docks_disabled=50),
    )
    assert station.occupancy == 0.25


@pytest.mark.parametrize("name", ["num_bikes_available", "num_docks_available"])
def test_ac6_negative_available_count_invalidates_the_status(name: str) -> None:
    station = one(status=status_record(**{name: -1}))
    assert paths(station, "count_negative") == {f"station_status.{name}"}
    assert "count_invalid" not in station.quality_flags
    assert "status_invalid" in station.quality_flags
    assert station.state == "status_invalid"
    assert_no_availability(station)


@pytest.mark.parametrize("value", ["5", 5.0, True, None, [5]])
@pytest.mark.parametrize("name", ["num_bikes_available", "num_docks_available"])
def test_dq9_non_integer_available_count_invalidates_the_status(
    name: str, value: JsonValue
) -> None:
    station = one(status=status_record(**{name: value}))
    assert paths(station, "count_invalid") == {f"station_status.{name}"}
    assert station.state == "status_invalid"
    assert_no_availability(station)


@pytest.mark.parametrize("name", ["num_bikes_available", "num_docks_available"])
def test_dq9_absent_available_count_invalidates_the_status(name: str) -> None:
    station = one(status=without(status_record(), name))
    assert paths(station, "count_invalid") == {f"station_status.{name}"}
    assert station.state == "status_invalid"


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (-1, "count_negative"),
        ("2", "count_invalid"),
        (1.5, "count_invalid"),
        (True, "count_invalid"),
    ],
)
@pytest.mark.parametrize("name", ["num_bikes_disabled", "num_docks_disabled"])
def test_ac6_bad_auxiliary_count_is_null_and_keeps_the_status_valid(
    name: str, value: JsonValue, code: str
) -> None:
    station = one(status=status_record(**{name: value}))
    assert getattr(station, name) is None
    assert paths(station, code) == {f"station_status.{name}"}
    assert station.status_valid is True
    assert station.num_bikes_available == 5
    assert station.occupancy == 0.25


def test_ac6_bad_other_count_is_null_and_keeps_the_status_valid() -> None:
    station = one(status=status_record(num_ebikes_available=-2, num_scooters_available="x"))
    assert station.other_counts == {"num_ebikes_available": None, "num_scooters_available": None}
    assert paths(station, "count_negative") == {"station_status.num_ebikes_available"}
    assert paths(station, "count_invalid") == {"station_status.num_scooters_available"}
    assert station.status_valid is True


def test_dq9_absent_auxiliary_counts_are_null_without_a_flag() -> None:
    record = without(status_record(), "num_bikes_disabled", "num_docks_disabled")
    station = one(status=record)
    assert station.num_bikes_disabled is None
    assert station.num_docks_disabled is None
    assert station.quality_flags == ()
    assert station.status_valid is True


def test_dq9_zero_is_a_valid_count_not_a_missing_one() -> None:
    station = one(status=status_record(num_bikes_available=0, num_bikes_disabled=0))
    assert station.num_bikes_available == 0
    assert station.num_bikes_disabled == 0
    assert station.quality_flags == ()


def test_invalid_status_nulls_other_counts_but_keeps_their_names() -> None:
    station = one(status=status_record(last_reported=86400))
    assert station.other_counts == {"num_ebikes_available": None}


def test_ac8_status_without_information_is_kept_and_flagged() -> None:
    station = one(information=None)
    assert "information_missing" in station.quality_flags
    assert station.state == "quarantined"
    assert station.mappable is False
    assert station.risk_eligible is False
    assert station.information_ref is None
    assert station.status_ref is not None
    assert (station.name, station.latitude, station.longitude, station.capacity) == (
        None,
        None,
        None,
        None,
    )
    assert station.num_bikes_available == 5
    assert "coordinates_invalid" not in station.quality_flags


def test_ac9_unknown_fields_stay_reachable_through_the_record_ref() -> None:
    feeds = make_feeds(
        document([information_record(extra_information={"a": [1]})], extra_top=1),
        document([status_record(extra_status=True)]),
    )
    station = normalise(feeds, source=SOURCE).stations[0]
    assert station.information_ref is not None
    assert station.status_ref is not None
    information = feeds.information.data.stations[station.information_ref.record_index]
    status = feeds.status.data.stations[station.status_ref.record_index]
    assert isinstance(information, dict)
    assert isinstance(status, dict)
    assert information["extra_information"] == {"a": [1]}
    assert status["extra_status"] is True
    assert feeds.information.model_extra == {"extra_top": 1}
    assert "extra_status" not in Station.model_fields


@pytest.mark.parametrize(
    "entry", [pytest.param(value, id=repr(value)) for value in (3, "x", None, [1], True)]
)
def test_dq10_non_object_entry_is_rejected(entry: JsonValue) -> None:
    batch = run([information_record(), entry], [status_record()])
    assert [station.station_id for station in batch.stations] == ["1"]
    assert len(batch.rejected_records) == 1
    rejected = batch.rejected_records[0]
    assert rejected.quality_flags == ("record_malformed",)
    assert rejected.station_id is None
    assert rejected.source.feed == "station_information"
    assert rejected.source.record_index == 1
    assert rejected.quality_details[0].path == "station_information.record"


@pytest.mark.parametrize(
    "value", [pytest.param(value, id=repr(value)) for value in ("", None, 5, True, ["1"])]
)
def test_dq10_invalid_id_is_rejected(value: JsonValue) -> None:
    batch = run([], [status_record(station_id="1") | {"station_id": value}])
    assert batch.stations == ()
    rejected = batch.rejected_records[0]
    assert rejected.quality_flags == ("station_id_invalid",)
    assert rejected.station_id is None
    assert rejected.quality_details[0].path == "station_status.station_id"


def test_dq10_absent_id_is_rejected() -> None:
    batch = run([], [without(status_record(), "station_id")])
    assert batch.stations == ()
    assert batch.rejected_records[0].quality_flags == ("station_id_invalid",)


def test_dq10_ids_are_preserved_exactly() -> None:
    batch = run([information_record(" 1 ")], [status_record("1")])
    assert [station.station_id for station in batch.stations] == [" 1 ", "1"]
    assert [station.state for station in batch.stations] == ["no_status", "quarantined"]


def test_dq10_id_repeated_in_status_removes_the_information_record_too() -> None:
    batch = run(
        [information_record("1"), information_record("2")],
        [status_record("1"), status_record("1"), status_record("2")],
    )
    assert [station.station_id for station in batch.stations] == ["2"]
    assert [(r.source.feed, r.source.record_index) for r in batch.rejected_records] == [
        ("station_information", 0),
        ("station_status", 0),
        ("station_status", 1),
    ]
    assert all(r.quality_flags == ("duplicate_id",) for r in batch.rejected_records)
    assert all(r.station_id == "1" for r in batch.rejected_records)


def test_rejected_records_are_sorted_by_feed_and_index() -> None:
    batch = run([information_record("1"), 3], [status_record("1"), status_record("1")])
    assert [(r.source.feed, r.source.record_index) for r in batch.rejected_records] == [
        ("station_information", 0),
        ("station_information", 1),
        ("station_status", 0),
        ("station_status", 1),
    ]


def test_invalid_count_alone_makes_a_fresh_operating_station_not_risk_eligible() -> None:
    station = one(status=status_record(num_bikes_available=-1))
    assert station.service_state == "in_service"
    assert station.freshness == "fresh"
    assert station.mappable is True
    assert station.risk_eligible is False


def test_dq10_id_repeated_in_information_removes_the_status_record_too() -> None:
    batch = run([information_record("1"), information_record("1")], [status_record("1")])
    assert batch.stations == ()
    assert len(batch.rejected_records) == 3


def test_identical_duplicates_are_still_rejected() -> None:
    batch = run([information_record()], [status_record(), status_record()])
    assert batch.stations == ()


def test_precedence_quarantined_hides_nothing() -> None:
    station = one(
        information=information_record(lat=0, lon=0),
        status=status_record(is_installed=0, last_reported=86400),
    )
    assert station.state == "quarantined"
    assert station.service_state == "out_of_service"
    assert station.status_valid is False
    assert station.freshness == "unknown"
    assert {"coordinates_invalid", "last_reported_invalid", "status_invalid"} <= set(
        station.quality_flags
    )


def test_precedence_missing_information_wins_over_invalid_status() -> None:
    station = one(information=None, status=status_record(last_reported=86400))
    assert station.state == "quarantined"
    assert {"information_missing", "status_invalid"} <= set(station.quality_flags)


def test_precedence_quarantined_wins_over_no_status() -> None:
    station = one(information=information_record(lat=0, lon=0), status=None)
    assert station.state == "quarantined"
    assert set(station.quality_flags) == {"coordinates_invalid", "status_missing"}


def test_precedence_status_invalid_wins_over_out_of_service() -> None:
    station = one(status=status_record(is_renting=False, last_reported=86400))
    assert station.state == "status_invalid"
    assert station.service_state == "out_of_service"


def test_precedence_out_of_service_wins_over_stale() -> None:
    station = one(status=status_record(is_renting=False, last_reported=R_POSIX - 7200))
    assert station.state == "out_of_service"
    assert station.freshness == "stale"
    assert "stale" in station.quality_flags


def test_precedence_status_invalid_wins_over_stale_and_keeps_the_stale_fact() -> None:
    station = one(status=status_record(is_renting="x", last_reported=R_POSIX - 7200))
    assert station.state == "status_invalid"
    assert station.freshness == "stale"
    assert station.observed_at is not None


@pytest.mark.parametrize("value", [-1, "30", 1.5, True, None])
def test_capacity_invalid(value: JsonValue) -> None:
    station = one(information=information_record(capacity=value))
    assert station.capacity is None
    assert paths(station, "capacity_invalid") == {"station_information.capacity"}
    assert station.state == "in_service"


def test_capacity_absent_has_no_flag_and_zero_is_kept() -> None:
    assert one(information=without(information_record(), "capacity")).quality_flags == ()
    assert one(information=information_record(capacity=0)).capacity == 0


@pytest.mark.parametrize("field", ["name", "short_name", "external_id"])
def test_text_fields(field: str) -> None:
    station = one(information=information_record(**{field: 12}))
    assert getattr(station, field) is None
    assert paths(station, "name_invalid") == {f"station_information.{field}"}
    assert one(information=without(information_record(), field)).quality_flags == ()


def test_flags_are_sorted_and_unique_and_details_sorted() -> None:
    station = one(status=status_record(is_installed=1, is_renting=1, num_ebikes_available=-1))
    assert list(station.quality_flags) == sorted(set(station.quality_flags))
    keys = [(detail.code, detail.path) for detail in station.quality_details]
    assert keys == sorted(set(keys))


def test_stations_are_sorted_by_code_point() -> None:
    ids = ["9", "10", "a", "B"]
    batch = run([information_record(i) for i in ids], [status_record(i) for i in ids])
    assert [station.station_id for station in batch.stations] == ["10", "9", "B", "a"]


def test_feed_times_fetch_times_and_station_times_stay_apart() -> None:
    batch = run([information_record()], [status_record()])
    assert batch.collected_at == R
    assert batch.data_as_of == datetime.fromtimestamp(R_POSIX - 5, UTC)
    assert batch.stations[0].observed_at == datetime.fromtimestamp(FRESH, UTC)
    information, status = batch.provenance
    assert (information.feed_name, status.feed_name) == ("station_information", "station_status")
    assert information.collected_at == R - timedelta(seconds=1)
    assert status.collected_at == R
    assert information.ttl_seconds == 10
    assert batch.source == SOURCE
    assert batch.quality_flags == ()


def test_provenance_points_to_the_raw_bytes() -> None:
    feeds = make_feeds(document([]), document([]))
    batch = normalise(feeds, source=SOURCE)
    assert batch.provenance[0].sha256 == feeds.information_payload.sha256
    assert batch.provenance[1].sha256 == feeds.status_payload.sha256
    assert batch.provenance[0].sha256 != feeds.discovery.sha256


@pytest.mark.parametrize(
    "value",
    [86400, FLOOR - 1, R_POSIX + 301, "1", 1.5, True, None],
)
def test_invalid_status_feed_time_gives_no_data_as_of(value: JsonValue) -> None:
    feeds = make_feeds(document([]), document([], last_updated=value))
    batch = normalise(feeds, source=SOURCE)
    assert batch.data_as_of is None
    assert batch.quality_flags == ("feed_timestamp_invalid",)
    assert batch.quality_details[0].path == "station_status.last_updated"


def test_absent_feed_time_is_flagged_and_never_replaced_by_the_fetch_time() -> None:
    feeds = make_feeds(document([]), without(document([]), "last_updated"))
    batch = normalise(feeds, source=SOURCE)
    assert batch.data_as_of is None
    assert batch.quality_flags == ("feed_timestamp_invalid",)


def test_invalid_information_feed_time_keeps_data_as_of() -> None:
    feeds = make_feeds(document([], last_updated="x"), document([]))
    batch = normalise(feeds, source=SOURCE)
    assert batch.data_as_of is not None
    assert batch.quality_details[0].path == "station_information.last_updated"
    assert batch.provenance[0].last_updated is None


def test_feed_time_window_uses_the_collected_at_of_its_own_payload() -> None:
    feeds = make_feeds(document([], last_updated=R_POSIX + 300), document([]))
    batch = normalise(feeds, source=SOURCE)  # information was collected one second before R
    assert batch.provenance[0].last_updated is None
    assert batch.quality_flags == ("feed_timestamp_invalid",)


@pytest.mark.parametrize("value", [-1, "10", 1.5, True, None])
def test_invalid_ttl(value: JsonValue) -> None:
    feeds = make_feeds(document([]), document([], ttl=value))
    batch = normalise(feeds, source=SOURCE)
    assert batch.provenance[1].ttl_seconds is None
    assert batch.quality_flags == ("ttl_invalid",)
    assert batch.quality_details[0].path == "station_status.ttl"


def test_absent_ttl_has_no_flag_and_zero_is_kept() -> None:
    absent = normalise(make_feeds(document([]), without(document([]), "ttl")), source=SOURCE)
    assert absent.provenance[1].ttl_seconds is None
    assert absent.quality_flags == ()
    zero = normalise(make_feeds(document([]), document([], ttl=0)), source=SOURCE)
    assert zero.provenance[1].ttl_seconds == 0


def test_version_string_is_kept_and_anything_else_is_null_without_a_flag() -> None:
    kept = normalise(make_feeds(document([]), document([], version="2.2")), source=SOURCE)
    assert kept.provenance[1].version == "2.2"
    other = normalise(make_feeds(document([]), document([], version=2.2)), source=SOURCE)
    assert other.provenance[1].version is None
    assert other.quality_flags == ()


def test_normalise_is_pure() -> None:
    feeds = make_feeds(
        document([information_record("1"), information_record("1"), 3]),
        document([status_record("2", is_renting=1, extra={"a": [1]})]),
    )
    information_before = copy.deepcopy(feeds.information.data.stations)
    status_before = copy.deepcopy(feeds.status.data.stations)
    first = normalise(feeds, source=SOURCE)
    second = normalise(feeds, source=SOURCE)
    assert first == second
    assert feeds.information.data.stations == information_before
    assert feeds.status.data.stations == status_before


def test_results_are_frozen() -> None:
    station = one()
    with pytest.raises(ValidationError):
        station.state = "stale"  # pyright: ignore[reportAttributeAccessIssue]
