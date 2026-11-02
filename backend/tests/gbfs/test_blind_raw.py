import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

import pytest
from blind_helpers import JsonObject, R, envelope, information, payload, status
from pydantic import JsonValue

from citypulse.gbfs.errors import UpstreamError, UpstreamJsonError, UpstreamSchemaError
from citypulse.gbfs.raw import RawPayload, build_fetched_feeds, parse_station_feed


@pytest.mark.parametrize("body", [b'{"data":{"stations":[]}}', b' { "different": true }\r\n'])
def test_raw_payload_preserves_bytes_and_metadata(body: bytes) -> None:
    collected_at = R.replace(microsecond=123456)
    raw = RawPayload.from_body(
        feed_name="station_status",
        body=body,
        collected_at=collected_at,
        request_url="https://example.org/request",
        final_url="https://example.org/final",
        attempt=2,
        http_status=503,
        content_type="application/json; charset=utf-8",
        last_modified="Fri, 02 Jan 2026 11:00:00 GMT",
    )
    assert raw.feed_name == "station_status"
    assert raw.body == body
    assert raw.sha256 == hashlib.sha256(body).hexdigest()
    assert raw.collected_at == collected_at
    assert raw.collected_at.utcoffset() == timedelta(0)
    assert raw.request_url == "https://example.org/request"
    assert raw.final_url == "https://example.org/final"
    assert raw.attempt == 2
    assert raw.http_status == 503
    assert raw.content_type == "application/json; charset=utf-8"
    assert raw.last_modified == "Fri, 02 Jan 2026 11:00:00 GMT"
    with pytest.raises(FrozenInstanceError):
        raw.attempt = 3  # pyright: ignore[reportAttributeAccessIssue]


def test_raw_payload_defaults_and_naive_time_rejection() -> None:
    raw = payload("station_information", b"fixture")
    assert raw.final_url == raw.request_url
    assert raw.attempt == 1
    assert raw.http_status == 200
    assert raw.content_type is None
    assert raw.last_modified is None
    with pytest.raises(ValueError):
        payload("station_information", b"fixture", datetime(2026, 1, 2, 12))


def test_upstream_error_stores_context_and_defaults() -> None:
    raw = payload("station_status", b"fixture")
    error = UpstreamError(
        "failure",
        resource="station_status",
        url="https://example.org/status",
        attempts=3,
        status_code=503,
        captures=(raw,),
    )
    assert str(error) == "failure"
    assert error.resource == "station_status"
    assert error.url == "https://example.org/status"
    assert error.attempts == 3
    assert error.status_code == 503
    assert error.captures == (raw,)
    default = UpstreamError("failure", resource="station_information")
    assert default.resource == "station_information"
    assert default.url is None
    assert default.attempts == 0
    assert default.status_code is None
    assert default.captures == ()


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"\xff",
        b'{"data":',
        b'{"data":{"stations":[]}} trailing',
        b'{"data":{"stations":[]},"data":{"stations":[]}}',
        b'{"data":{"stations":[],"stations":[]}}',
        b'{"data":{"stations":[{"nested":{"x":1,"x":2}}]}}',
        b'{"data":{"stations":[NaN]}}',
        b'{"data":{"stations":[Infinity]}}',
        b'{"data":{"stations":[-Infinity]}}',
    ],
)
def test_invalid_json_raises_typed_error_with_capture(feed_name: str, body: bytes) -> None:
    raw = payload(feed_name, body)
    with pytest.raises(UpstreamJsonError) as raised:
        parse_station_feed(raw)
    assert isinstance(raised.value, UpstreamError)
    assert raised.value.resource == feed_name
    assert raised.value.captures == (raw,)


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
@pytest.mark.parametrize(
    "document",
    [
        None,
        [],
        1,
        "feed",
        {},
        {"data": None},
        {"data": []},
        {"data": "stations"},
        {"data": {}},
        {"data": {"stations": None}},
        {"data": {"stations": {}}},
        {"data": {"stations": "[]"}},
        {"data": {"stations": True}},
        {"data": {"stations": 1}},
    ],
)
def test_bad_envelope_raises_typed_schema_error(feed_name: str, document: JsonValue) -> None:
    raw = payload(feed_name, json.dumps(document).encode())
    with pytest.raises(UpstreamSchemaError) as raised:
        parse_station_feed(raw)
    assert isinstance(raised.value, UpstreamError)
    assert raised.value.resource == feed_name
    assert raised.value.captures == (raw,)


def test_empty_stations_and_absent_optional_metadata_are_valid() -> None:
    parsed = parse_station_feed(payload("station_information", b'{"data":{"stations":[]}}'))
    assert parsed.data.stations == []
    assert parsed.last_updated is None
    assert parsed.ttl is None
    assert parsed.version is None


def test_unknown_fields_and_unvalidated_record_values_are_preserved() -> None:
    records: list[JsonValue] = [
        information(
            is_charging=False,
            extension={"nested": [None, 1.0, True, "value"]},
        ),
        status(is_installed=1, num_bikes_available=1.0),
        None,
        17,
        ["malformed", False],
    ]
    document: JsonObject = {
        "last_updated": False,
        "ttl": "10",
        "version": 2,
        "extension": {"envelope": [1, None]},
        "data": {"stations": records, "extension": {"data": "kept"}},
    }
    parsed = parse_station_feed(payload("station_information", json.dumps(document).encode()))
    assert parsed.model_dump() == document
    assert parsed.last_updated is False
    assert parsed.ttl == "10"
    assert parsed.version == 2
    parsed_status = parsed.data.stations[1]
    assert isinstance(parsed_status, dict)
    assert type(parsed_status["is_installed"]) is int
    assert type(parsed_status["num_bikes_available"]) is float


def test_build_fetched_feeds_parses_both_and_preserves_capture_order() -> None:
    discovery = payload("discovery", b'{"data":{}}')
    info = payload("station_information", json.dumps(envelope([information()])).encode())
    report = payload("station_status", json.dumps(envelope([status()])).encode())
    built = build_fetched_feeds(discovery=discovery, information=info, status=report)
    assert built.discovery == discovery
    assert built.information_payload == info
    assert built.status_payload == report
    assert built.information.data.stations == [information()]
    assert built.status.data.stations == [status()]
    assert built.captures == (discovery, info, report)

    retry = payload("station_information", b"retry response")
    captures = (discovery, retry, info, report)
    explicit = build_fetched_feeds(
        discovery=discovery, information=info, status=report, captures=captures
    )
    assert explicit.captures == captures
    assert explicit.information == built.information
    assert explicit.status == built.status
    with pytest.raises(FrozenInstanceError):
        explicit.captures = ()  # pyright: ignore[reportAttributeAccessIssue]
