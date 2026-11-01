import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from citypulse.gbfs.errors import UpstreamError, UpstreamJsonError, UpstreamSchemaError
from citypulse.gbfs.raw import RawPayload, build_fetched_feeds, parse_station_feed

NOW = datetime(2026, 10, 25, 0, 15, 51, tzinfo=UTC)
URL = "https://example.org/en/station_status.json"


def payload(body: bytes, feed_name: str = "station_status") -> RawPayload:
    return RawPayload.from_body(feed_name=feed_name, body=body, collected_at=NOW, request_url=URL)


def feed_body(*stations: object) -> bytes:
    return json.dumps({"data": {"stations": list(stations)}}).encode()


def test_from_body_hashes_the_exact_bytes_and_fills_the_defaults() -> None:
    body = b'{"data": {"stations": []}}  \n'
    result = payload(body)
    assert result.body == body
    assert result.sha256 == hashlib.sha256(body).hexdigest()
    assert result.final_url == URL
    assert result.attempt == 1
    assert result.http_status == 200


def test_from_body_refuses_a_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        RawPayload.from_body(
            feed_name="station_status",
            body=b"{}",
            collected_at=datetime(2026, 10, 25),
            request_url=URL,
        )


def test_from_body_stores_the_time_in_utc() -> None:
    toronto = timezone(timedelta(hours=-4))
    result = RawPayload.from_body(
        feed_name="station_status",
        body=b"{}",
        collected_at=NOW.astimezone(toronto),
        request_url=URL,
    )
    assert result.collected_at == NOW
    assert result.collected_at.utcoffset() == timedelta(0)


def test_body_is_left_out_of_the_representation() -> None:
    assert "secret-looking-body" not in repr(payload(b"secret-looking-body"))


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"not json", id="not-json"),
        pytest.param(b"", id="empty"),
        pytest.param(b'{"data": {"stations": ["\xff"]}}', id="not-utf8"),
        pytest.param(b'{"data": {"stations": []}, "data": {"stations": []}}', id="repeated-top"),
        pytest.param(b'{"data": {"stations": [{"a": 1, "a": 2}]}}', id="repeated-in-station"),
        pytest.param(b'{"data": {"stations": [NaN]}}', id="nan"),
        pytest.param(b'{"data": {"stations": [Infinity]}}', id="infinity"),
        pytest.param(b'{"data": {"stations": [-Infinity]}}', id="minus-infinity"),
        pytest.param(b"[" * 200_000, id="too-deep"),
    ],
)
def test_invalid_json_raises_a_typed_error_that_keeps_the_capture(body: bytes) -> None:
    capture = payload(body)
    with pytest.raises(UpstreamJsonError) as caught:
        parse_station_feed(capture)
    assert caught.value.resource == "station_status"
    assert caught.value.url == URL
    assert caught.value.captures == (capture,)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"null", id="null"),
        pytest.param(b"[]", id="not-an-object"),
        pytest.param(b"{}", id="no-data"),
        pytest.param(b'{"data": []}', id="data-not-an-object"),
        pytest.param(b'{"data": {}}', id="no-stations"),
        pytest.param(b'{"data": {"stations": null}}', id="stations-null"),
        pytest.param(b'{"data": {"stations": {}}}', id="stations-not-a-list"),
    ],
)
def test_wrong_envelope_raises_a_typed_error_that_keeps_the_capture(body: bytes) -> None:
    capture = payload(body, feed_name="station_information")
    with pytest.raises(UpstreamSchemaError) as caught:
        parse_station_feed(capture)
    assert isinstance(caught.value, UpstreamError)
    assert caught.value.resource == "station_information"
    assert caught.value.captures == (capture,)


def test_empty_station_list_is_valid() -> None:
    assert parse_station_feed(payload(feed_body())).data.stations == []


def test_records_are_kept_without_any_coercion() -> None:
    record = {"a": 1, "b": True, "c": "1", "d": 1.0, "e": None, "f": [1], "g": {"h": False}}
    stations = parse_station_feed(payload(feed_body(record, 7, "x", None))).data.stations
    assert stations == [record, 7, "x", None]
    first = stations[0]
    assert isinstance(first, dict)
    assert type(first["a"]) is int
    assert type(first["b"]) is bool
    assert type(first["d"]) is float


def test_unknown_fields_are_kept_at_every_level() -> None:
    document = {
        "last_updated": 1,
        "ttl": 2,
        "version": "2.2",
        "extra_top": {"nested": [1]},
        "data": {"stations": [{"station_id": "1", "extra_station": [True]}], "extra_data": 3},
    }
    feed = parse_station_feed(payload(json.dumps(document).encode()))
    assert feed.model_extra == {"extra_top": {"nested": [1]}}
    assert feed.data.model_extra == {"extra_data": 3}
    assert feed.data.stations == [{"station_id": "1", "extra_station": [True]}]
    assert (feed.last_updated, feed.ttl, feed.version) == (1, 2, "2.2")


def test_build_fetched_feeds_parses_both_feeds_and_defaults_the_captures() -> None:
    discovery = payload(b"{}", feed_name="discovery")
    information = payload(feed_body({"station_id": "1"}), feed_name="station_information")
    status = payload(feed_body({"station_id": "2"}))
    feeds = build_fetched_feeds(discovery=discovery, information=information, status=status)
    assert feeds.information.data.stations == [{"station_id": "1"}]
    assert feeds.status.data.stations == [{"station_id": "2"}]
    assert feeds.captures == (discovery, information, status)


def test_build_fetched_feeds_keeps_the_given_captures_and_raises_on_a_bad_feed() -> None:
    discovery = payload(b"{}", feed_name="discovery")
    good = payload(feed_body())
    retried = payload(b"not json")
    feeds = build_fetched_feeds(
        discovery=discovery,
        information=good,
        status=good,
        captures=(discovery, retried, good, good),
    )
    assert feeds.captures == (discovery, retried, good, good)
    with pytest.raises(UpstreamJsonError):
        build_fetched_feeds(discovery=discovery, information=good, status=retried)
