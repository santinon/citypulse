"""Hand-calculated populations and boundaries, independent of implementation."""

import hashlib
import json

import pytest

from conftest import REFERENCE as R, STAMP, json_bytes


def test_ids_join_and_duplicate_exclusion(snapshot, profile):
    # "dup" is repeated in information and "sdup" in status: both stay out of every join bucket.
    information = [{"station_id": value}
                   for value in ("a", "dup", "dup", "only", "", 3, "sdup")] + [{}, None]
    status = [{"station_id": value}
              for value in ("a", "dup", "b", "b", "other", None, "sdup", "sdup")] + [{}, []]
    actual = profile(snapshot(information, status))["stations"]
    assert actual == {
        "information_records": 9, "status_records": 10,
        "information_invalid_ids": 4, "status_invalid_ids": 3,
        "information_duplicate_ids": 1, "status_duplicate_ids": 2,
        "join": {"both": 1, "information_only": 1, "status_only": 1},
    }


def test_flags_field_census_and_rental_methods(snapshot, profile):
    values = [0, 1, 2, True, False, "1", 1.0, None, [], {}]
    status = [{"is_installed": value, "is_renting": 1, "is_returning": True} for value in values]
    status.append({"is_renting": 1, "is_returning": True})
    information = [{"rental_methods": ["KEY", "KEY", "CREDITCARD"]}, {},
                   {"rental_methods": None}, {"rental_methods": ["KEY", 3]},
                   {"rental_methods": []}, {"rental_methods": "KEY"}]
    actual = profile(snapshot(information, status))
    assert actual["flags"] == {
        "is_installed": {key: 1 for key in ("int:0", "int:1", "int:other", "bool:true",
            "bool:false", "str", "float", "null", "list", "dict", "absent")},
        "is_renting": {"int:1": 11}, "is_returning": {"bool:true": 11},
        "combinations": {"0,1,1": 2, "1,1,1": 2, "x,1,1": 7},
    }
    assert actual["fields"]["station_status"] == {
        "is_installed": {"records": 10, "types": {"int": 3, "bool": 2, "str": 1,
            "float": 1, "null": 1, "list": 1, "dict": 1}},
        "is_renting": {"records": 11, "types": {"int": 11}},
        "is_returning": {"records": 11, "types": {"bool": 11}},
    }
    assert actual["fields"]["station_information"] == {
        "rental_methods": {"records": 5, "types": {"list": 3, "null": 1, "str": 1}},
    }
    assert actual["rental_methods"] == {"values": {"KEY": 1, "CREDITCARD": 1},
                                         "records_without": 1, "records_invalid": 3}


def test_coordinate_precedence_and_zero_station_details(snapshot, profile):
    information = [{}, {"lat": True, "lon": 0}, {"lat": float("inf"), "lon": 181},
        {"lat": 91, "lon": 0}, {"station_id": "z", "lat": 0, "lon": 0, "capacity": 5},
        {"station_id": "a", "lat": 0, "lon": 0, "capacity": 0},
        {"station_id": "b", "lat": 0, "lon": 0, "capacity": 2}, {"lat": 45, "lon": -73}]
    status = [{"station_id": "z", "last_reported": R}, {"station_id": "b", "last_reported": 86400}]
    actual = profile(snapshot(information, status))
    assert actual["coordinates"] == {
        "non_numeric": 2, "non_finite": 1, "out_of_range": 1, "zero_zero": 3, "valid": 1,
        "zero_zero_stations": [
            {"station_id": "a", "capacity": 0, "last_reported_invalid": None},
            {"station_id": "b", "capacity": 2, "last_reported_invalid": True},
            {"station_id": "z", "capacity": 5, "last_reported_invalid": False}],
    }
    assert actual["remote_stations"]["origin"] == {"lat": 45.0, "lon": -73.0}
    assert actual["remote_stations"]["buckets_km"] == {"0-10": 1, "10-50": 0, "50-100": 0, "100+": 0}


def test_capacity_requires_all_five_nonnegative_json_integers(snapshot, profile):
    information, status = [], []
    # Three comparable joins: full identity only, both identities, neither.
    cases = [(5, 1, 2, 1, 1), (3, 1, 2, 0, 0), (9, 1, 2, 0, 0),
             (True, 1, 2, 0, 0), (3, 1.0, 2, 0, 0), (3, 1, -1, 0, 0),
             (3, 1, 2, None, 0), (3, 1, 2, 0, "0")]
    for index, (capacity, bikes, docks, disabled_bikes, disabled_docks) in enumerate(cases):
        information.append({"station_id": str(index), "capacity": capacity})
        status.append({"station_id": str(index), "num_bikes_available": bikes,
                       "num_docks_available": docks, "num_bikes_disabled": disabled_bikes,
                       "num_docks_disabled": disabled_docks})
    information.append({"station_id": "missing", "capacity": 3})
    status.append({"station_id": "missing", "num_bikes_available": 1, "num_docks_available": 2,
                   "num_bikes_disabled": 0})
    # Unmatched and duplicate records cannot inflate either population.
    information.extend([{"station_id": "unmatched", "capacity": 0},
                        {"station_id": "duplicate", "capacity": 0},
                        {"station_id": "duplicate", "capacity": 0}])
    status.append({"station_id": "duplicate", "num_bikes_available": 0,
                   "num_docks_available": 0, "num_bikes_disabled": 0, "num_docks_disabled": 0})
    actual = profile(snapshot(information, status))
    assert actual["stations"]["join"]["both"] == 9
    assert actual["capacity"] == {"population": 3, "excluded": 6,
        "equals_bikes_docks_disabled": 2, "equals_bikes_docks": 1}


def test_last_reported_cutoff_future_quantiles_and_strict_thresholds(snapshot, profile):
    values = [946684799, 946684800, R + 300, R + 301, True, float(R), None,
              R, R - 3600, R - 3601, R - 86400, R - 86401, R - 2592000, R - 2592001]
    status = [{"last_reported": value} for value in values] + [{}]
    actual = profile(snapshot(status=status))["last_reported"]
    # Valid sorted ages: -300, 0, 3600, 3601, 86400, 86401,
    # 2592000, 2592001, 753315200. Median = fifth, p90 = ninth.
    assert actual == {
        "records": 15, "invalid": 6,
        "invalid_values": {"946684799": 1, str(R + 301): 1, "true": 1,
                           "1700000000.0": 1, "null": 1, "absent": 1},
        "future_tolerated": 1,
        "age_seconds": {"n": 9, "min": -300, "median": 86400,
                        "p90": 753315200, "max": 753315200},
        "older_than": {"3600": 6, "86400": 4, "2592000": 2},
    }


def test_even_median_and_nearest_rank_p90(snapshot, profile):
    status = [{"last_reported": R - age} for age in (11, 0, 9, 2, 8, 3, 7, 4, 6, 5)]
    assert profile(snapshot(status=status))["last_reported"]["age_seconds"] == {
        "n": 10, "min": 0, "median": 5.5, "p90": 9, "max": 11,
    }


def test_remote_buckets_use_valid_coordinates_and_component_medians(snapshot, profile):
    # Along the equator: 0, 2.2, 33.4, 77.9 and 222.6 km approximately.
    # Five repeated records fix the origin at (0, 0.01); no point is near an edge.
    points = [(0, 0.01)] * 5 + [(0, 0.03), (0, 0.31), (0, 0.71), (0, 2.01)]
    information = [{"lat": lat, "lon": lon} for lat, lon in points]
    information.extend([{"lat": 0, "lon": 0}, {"lat": 91, "lon": 0}, {}])
    actual = profile(snapshot(information))["remote_stations"]
    assert actual == {
        "origin": {"lat": 0.0, "lon": 0.01},
        "buckets_km": {"0-10": 6, "10-50": 1, "50-100": 1, "100+": 1},
        "beyond_50km": {"count": 2, "lat_min": 0, "lat_max": 0, "lon_min": 0.71, "lon_max": 2.01},
    }


def test_alert_intervals_types_and_unique_summaries(snapshot, profile):
    alerts = [
        {"type": "A", "summary": "z", "times": [{"start": R, "end": R}], "station_ids": ["a"]},
        {"type": "A", "summary": "a", "times": [{"start": R - 1}], "region_ids": ["x"]},
        {"type": "A", "summary": "z", "times": [{"start": R, "end": None}]},
        {"type": "B", "summary": "later", "times": [{"start": R + 1}]},
        {"type": "B", "summary": "past", "times": [{"start": R - 2, "end": R - 1}]},
        {"type": "B", "times": [{"start": True}, {"start": float(R)}, {"end": R}]},
        {"type": "B", "times": [{"start": R, "end": True}, {"start": R, "end": float(R)}]},
        {"summary": "unknown", "station_ids": [], "region_ids": "x"},
        {"type": "", "summary": "empty", "times": []},
        {"type": 3, "summary": 7, "times": [{"start": R + 1}, {"start": R - 5, "end": R}]},
    ]
    assert profile(snapshot(alerts=alerts))["alerts"] == {
        "records": 10, "with_times": 8, "without_times": 2, "active_at_reference": 4,
        "with_station_ids": 1, "with_region_ids": 1, "types": {"A": 3, "B": 4, "invalid": 3},
        "summaries_by_type": {"A": ["a", "z"], "B": ["later", "past"], "invalid": ["empty", "unknown"]},
    }


def test_empty_snapshot_metadata_and_repeatability(snapshot, profile):
    directory = snapshot()
    manifest_bytes = (directory / "manifest.json").read_bytes()
    actual = profile(directory)
    first = (directory / "profile.json").read_bytes()
    assert actual["schema"] == "citypulse.gbfs_audit.profile/1"
    assert actual["script_version"] == "0.1.0" and actual["lang"] == "en"
    assert actual["reference_time"] == STAMP and actual["reference_time_posix"] == R
    assert actual["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert actual["discovery"] == {"languages": ["en"], "feeds": {"en": ["station_information", "station_status"]}}
    manifest = json.loads(manifest_bytes)
    assert actual["files"] == {entry["path"]: {
        "http_status": 200, "content_type": "application/json", "bytes": entry["bytes"],
        "ttl": None if entry["role"] == "discovery" else 10,
        "last_updated": None if entry["role"] == "discovery" else R, "has_version": False,
    } for entry in manifest["files"]}
    assert actual["last_reported"]["age_seconds"] == {"n": 0, "min": None, "median": None, "p90": None, "max": None}
    assert actual["remote_stations"] == {"origin": None,
        "buckets_km": {"0-10": 0, "10-50": 0, "50-100": 0, "100+": 0},
        "beyond_50km": {"count": 0, "lat_min": None, "lat_max": None, "lon_min": None, "lon_max": None}}
    assert actual["alerts"] is None
    assert profile(directory) == actual
    assert (directory / "profile.json").read_bytes() == first
    assert (directory / "manifest.json").read_bytes() == manifest_bytes


@pytest.mark.parametrize("damage", ["flip_byte", "missing_file", "missing_manifest", "bad_manifest",
                                         "required_error", "missing_required", "unsafe_path",
                                         "short_collected_at"])
def test_profile_rejects_untrusted_snapshot(damage, snapshot, run_audit):
    directory = snapshot()
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    feed = directory / "en/station_status.json"
    if damage == "flip_byte":
        # Still valid JSON, so only the SHA-256 check can reject it.
        body = feed.read_bytes().replace(b'"ttl": 10', b'"ttl": 11', 1)
        json.loads(body)
        feed.write_bytes(body)
    elif damage == "missing_file":
        feed.rename(directory / "removed.json")
    elif damage == "missing_manifest":
        manifest_path.rename(directory / "removed-manifest.json")
    elif damage == "bad_manifest":
        manifest_path.write_bytes(b"{broken")
    else:
        if damage == "required_error":
            manifest["files"][-1]["error"] = {"code": "invalid_json", "detail": "test"}
        elif damage == "missing_required":
            manifest["files"].pop()
        elif damage == "short_collected_at":
            # A real date, but not in the exact YYYY-MM-DDTHH:MM:SSZ form.
            manifest["files"][-1]["collected_at"] = "2023-1-4T2:3:4Z"
        else:
            # Keep valid bytes/hash outside the snapshot: otherwise a missing-file
            # check could conceal an absent path-containment check.
            (directory.parent / "outside.json").write_bytes(feed.read_bytes())
            manifest["files"][-1]["path"] = "../outside.json"
        manifest_path.write_bytes(json_bytes(manifest))
    result = run_audit("profile", directory)
    assert result.returncode == 1, result.stdout + result.stderr
    assert not (directory / "profile.json").exists()
