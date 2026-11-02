import asyncio
import json
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import JsonValue

from citypulse import __version__
from citypulse.gbfs.client import ClientOptions, GbfsClient, make_http_client
from citypulse.gbfs.errors import (
    DiscoveryError,
    PayloadTooLargeError,
    UnsafeFeedUrlError,
    UpstreamError,
    UpstreamHttpError,
    UpstreamJsonError,
    UpstreamSchemaError,
    UpstreamTimeoutError,
    UpstreamTransportError,
)
from citypulse.gbfs.models import SourceContext
from citypulse.gbfs.normalise import normalise
from citypulse.gbfs.raw import FetchedFeeds, build_fetched_feeds

type Reply = httpx2.Response | Exception | Callable[[httpx2.Request], Awaitable[httpx2.Response]]

BASE = "https://feed.example"
DISCOVERY_URL = f"{BASE}/gbfs/gbfs.json"
INFORMATION_URL = f"{BASE}/any/path/information.json"
STATUS_URL = f"{BASE}/other/status-file.json"
NOW = datetime(2026, 10, 25, 0, 15, 51, tzinfo=UTC)
FEED = json.dumps({"last_updated": 1, "ttl": 10, "data": {"stations": []}}).encode()


def discovery_body(
    information: str = INFORMATION_URL, status: str = STATUS_URL, language: str = "en"
) -> bytes:
    feeds: list[JsonValue] = [
        {"name": "system_information", "url": f"{BASE}/x.json"},
        {"name": "station_status", "url": status},
        {"name": "station_information", "url": information},
    ]
    return json.dumps({"data": {language: {"feeds": feeds}}}).encode()


def ok(body: bytes = FEED, **headers: str) -> httpx2.Response:
    return httpx2.Response(200, content=body, headers=headers)


class Server:
    """Scripted replies per URL. The last reply of a URL repeats. Records every request."""

    def __init__(self, replies: dict[str, list[Reply]]) -> None:
        self.replies = replies
        self.requests: list[httpx2.Request] = []
        self.counts: defaultdict[str, int] = defaultdict(int)

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.requests.append(request)
        if url not in self.replies:
            return httpx2.Response(404)
        script = self.replies[url]
        reply = script[min(self.counts[url], len(script) - 1)]
        self.counts[url] += 1
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx2.Response):
            return reply
        return await reply(request)


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)


def standard(**overrides: list[Reply]) -> dict[str, list[Reply]]:
    replies: dict[str, list[Reply]] = {
        DISCOVERY_URL: [ok(discovery_body())],
        INFORMATION_URL: [ok()],
        STATUS_URL: [ok()],
    }
    aliases = {"discovery": DISCOVERY_URL, "information": INFORMATION_URL, "status": STATUS_URL}
    for name, script in overrides.items():
        replies[aliases[name]] = script
    return replies


def fetch(
    server: Server,
    *,
    url: str = DISCOVERY_URL,
    options: ClientOptions | None = None,
    sleeps: Sleeps | None = None,
) -> FetchedFeeds:
    async def run() -> FetchedFeeds:
        async with make_http_client(transport=httpx2.MockTransport(server)) as http:
            client = GbfsClient(
                http,
                url,
                options=options if options is not None else ClientOptions(),
                sleep=sleeps if sleeps is not None else Sleeps(),
                clock=lambda: NOW,
            )
            return await client.fetch()

    return asyncio.run(run())


def failing(
    server: Server,
    error: type[UpstreamError],
    *,
    url: str = DISCOVERY_URL,
    options: ClientOptions | None = None,
    sleeps: Sleeps | None = None,
) -> UpstreamError:
    with pytest.raises(error) as caught:
        fetch(server, url=url, options=options, sleeps=sleeps)
    return caught.value


def test_fetch_reads_discovery_then_both_feeds_with_identifying_headers() -> None:
    server = Server(standard())
    feeds = fetch(server)
    assert server.urls == [DISCOVERY_URL, INFORMATION_URL, STATUS_URL]
    assert feeds.information.data.stations == []
    assert feeds.status_payload.final_url == STATUS_URL
    for request in server.requests:
        assert request.method == "GET"
        assert request.headers["accept"] == "application/json"
        assert request.headers["accept-encoding"] == "identity"
        assert __version__ in request.headers["user-agent"]
        assert "citypulse" in request.headers["user-agent"]


def test_payloads_keep_exact_bytes_time_and_response_metadata() -> None:
    body = b'{"data":   {"stations": []}, "extra": 1}\n'
    reply = ok(body, **{"content-type": "application/json", "last-modified": "yesterday"})
    server = Server(standard(status=[reply]))
    feeds = fetch(server)
    payload = feeds.status_payload
    assert payload.body == body
    assert payload.collected_at == NOW
    assert payload.http_status == 200
    assert payload.content_type == "application/json"
    assert payload.last_modified == "yesterday"
    assert payload.attempt == 1
    assert [capture.feed_name for capture in feeds.captures] == [
        "discovery",
        "station_information",
        "station_status",
    ]


def test_ac10_feed_urls_are_read_from_discovery_at_every_fetch() -> None:
    moved = f"{BASE}/moved/status.json"
    discovery: list[Reply] = [ok(discovery_body()), ok(discovery_body(status=moved))]
    replies = standard(discovery=discovery)
    replies[moved] = [ok()]
    server = Server(replies)
    fetch(server)
    fetch(server)
    assert server.urls[:3] == [DISCOVERY_URL, INFORMATION_URL, STATUS_URL]
    assert server.urls[3:] == [DISCOVERY_URL, INFORMATION_URL, moved]


def test_relative_feed_urls_resolve_against_the_final_discovery_url() -> None:
    new_home = f"{BASE}/new/gbfs.json"
    replies = standard(
        discovery=[httpx2.Response(302, headers={"location": new_home})],
    )
    replies[new_home] = [ok(discovery_body("en/information.json", "en/status.json"))]
    replies[f"{BASE}/new/en/information.json"] = [ok()]
    replies[f"{BASE}/new/en/status.json"] = [ok()]
    server = Server(replies)
    feeds = fetch(server)
    assert feeds.discovery.final_url == new_home
    assert feeds.discovery.request_url == DISCOVERY_URL
    assert feeds.status_payload.final_url == f"{BASE}/new/en/status.json"
    assert len(feeds.captures) == 4


@pytest.mark.parametrize(
    "document",
    [
        pytest.param([], id="not-an-object"),
        pytest.param({}, id="no-data"),
        pytest.param({"data": []}, id="data-not-an-object"),
        pytest.param({"data": {"fr": {"feeds": []}}}, id="no-english-no-fallback"),
        pytest.param({"data": {"en": []}}, id="language-not-an-object"),
        pytest.param({"data": {"en": {}}}, id="no-feed-list"),
        pytest.param({"data": {"en": {"feeds": {}}}}, id="feeds-not-a-list"),
        pytest.param({"data": {"en": {"feeds": [1]}}}, id="entry-not-an-object"),
        pytest.param({"data": {"en": {"feeds": []}}}, id="no-feed"),
    ],
)
def test_unusable_discovery_raises(document: JsonValue) -> None:
    server = Server(standard(discovery=[ok(json.dumps(document).encode())]))
    error = failing(server, DiscoveryError)
    assert error.resource == "discovery"
    assert server.urls == [DISCOVERY_URL]
    assert len(error.captures) == 1


@pytest.mark.parametrize("missing", ["station_information", "station_status"])
def test_missing_required_feed_raises(missing: str) -> None:
    feeds: list[JsonValue] = [
        {"name": name, "url": f"{BASE}/{name}.json"}
        for name in ("station_information", "station_status")
        if name != missing
    ]
    body = json.dumps({"data": {"en": {"feeds": feeds}}}).encode()
    error = failing(Server(standard(discovery=[ok(body)])), DiscoveryError)
    assert missing in str(error)


def test_required_feed_listed_twice_raises() -> None:
    feeds: list[JsonValue] = [
        {"name": "station_status", "url": STATUS_URL},
        {"name": "station_status", "url": STATUS_URL},
        {"name": "station_information", "url": INFORMATION_URL},
    ]
    body = json.dumps({"data": {"en": {"feeds": feeds}}}).encode()
    failing(Server(standard(discovery=[ok(body)])), DiscoveryError)


def test_required_feed_without_a_text_url_raises() -> None:
    feeds: list[JsonValue] = [
        {"name": "station_status", "url": 3},
        {"name": "station_information", "url": INFORMATION_URL},
    ]
    body = json.dumps({"data": {"en": {"feeds": feeds}}}).encode()
    error = failing(Server(standard(discovery=[ok(body)])), DiscoveryError)
    assert "no URL" in str(error)


def test_entry_that_is_not_an_object_raises_even_with_both_feeds_present() -> None:
    feeds: list[JsonValue] = [
        {"name": "station_status", "url": STATUS_URL},
        {"name": "station_information", "url": INFORMATION_URL},
        1,
    ]
    body = json.dumps({"data": {"en": {"feeds": feeds}}}).encode()
    server = Server(standard(discovery=[ok(body)]))
    failing(server, DiscoveryError)
    assert server.urls == [DISCOVERY_URL]


def test_invalid_discovery_json_raises_without_retry() -> None:
    server = Server(standard(discovery=[ok(b"not json")]))
    error = failing(server, UpstreamJsonError)
    assert server.urls == [DISCOVERY_URL]
    assert error.resource == "discovery"


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_ac10_retried_statuses_end_in_a_typed_error_with_no_result(status: int) -> None:
    server = Server(standard(status=[httpx2.Response(status)]))
    sleeps = Sleeps()
    error = failing(server, UpstreamHttpError, sleeps=sleeps)
    assert error.status_code == status
    assert error.attempts == 3
    assert error.resource == "station_status"
    assert error.url == STATUS_URL
    assert server.urls.count(STATUS_URL) == 3
    assert sleeps.calls == [0.5, 1.0]
    assert [capture.http_status for capture in error.captures] == [200, 200, status, status, status]


def test_retry_then_success_keeps_every_response_in_the_captures() -> None:
    server = Server(standard(information=[httpx2.Response(503), ok()]))
    sleeps = Sleeps()
    feeds = fetch(server, sleeps=sleeps)
    assert sleeps.calls == [0.5]
    assert feeds.information_payload.attempt == 2
    assert [(c.feed_name, c.http_status, c.attempt) for c in feeds.captures] == [
        ("discovery", 200, 1),
        ("station_information", 503, 1),
        ("station_information", 200, 2),
        ("station_status", 200, 1),
    ]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 501])
def test_other_statuses_are_not_retried(status: int) -> None:
    server = Server(standard(discovery=[httpx2.Response(status)]))
    sleeps = Sleeps()
    error = failing(server, UpstreamHttpError, sleeps=sleeps)
    assert error.status_code == status
    assert error.attempts == 1
    assert server.urls == [DISCOVERY_URL]
    assert sleeps.calls == []


def test_ac10_timeouts_are_retried_then_raise_a_typed_error() -> None:
    server = Server(standard(status=[httpx2.ReadTimeout("slow")]))
    sleeps = Sleeps()
    error = failing(server, UpstreamTimeoutError, sleeps=sleeps)
    assert error.attempts == 3
    assert error.status_code is None
    assert sleeps.calls == [0.5, 1.0]


def test_connection_failures_are_retried_then_raise_a_typed_error() -> None:
    server = Server(standard(discovery=[httpx2.ConnectError("refused")]))
    error = failing(server, UpstreamTransportError)
    assert error.attempts == 3
    assert server.urls == [DISCOVERY_URL] * 3
    assert error.captures == ()


def test_a_failure_then_success_recovers() -> None:
    server = Server(standard(status=[httpx2.ConnectError("refused"), ok()]))
    assert fetch(server).status_payload.attempt == 2


def test_ac10_invalid_feed_json_raises_with_all_captures_and_no_retry() -> None:
    server = Server(standard(status=[ok(b"not json")]))
    error = failing(server, UpstreamJsonError)
    assert server.urls.count(STATUS_URL) == 1
    assert error.resource == "station_status"
    assert len(error.captures) == 3


def test_ac10_missing_stations_raises_with_all_captures_and_no_retry() -> None:
    server = Server(standard(information=[ok(b'{"data": {}}')]))
    error = failing(server, UpstreamSchemaError)
    assert server.urls.count(INFORMATION_URL) == 1
    assert error.resource == "station_information"
    assert len(error.captures) == 3


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_replaces_the_backoff(status: int) -> None:
    reply = httpx2.Response(status, headers={"retry-after": "7"})
    server = Server(standard(status=[reply, ok()]))
    sleeps = Sleeps()
    fetch(server, sleeps=sleeps)
    assert sleeps.calls == [7]


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_beyond_the_budget_fails_at_once(status: int) -> None:
    reply = httpx2.Response(status, headers={"retry-after": "121"})
    server = Server(standard(status=[reply]))
    sleeps = Sleeps()
    error = failing(server, UpstreamHttpError, sleeps=sleeps)
    assert error.attempts == 1
    assert error.status_code == status
    assert sleeps.calls == []
    assert server.urls.count(STATUS_URL) == 1


@pytest.mark.parametrize("value", ["soon", "1.5", "-3", "Sun, 25 Oct 2026 00:15:55 GMT", "٣"])
def test_other_retry_after_formats_are_ignored(value: str) -> None:
    reply = httpx2.Response(429, headers={b"retry-after": value.encode()})
    server = Server(standard(status=[reply, ok()]))
    sleeps = Sleeps()
    fetch(server, sleeps=sleeps)
    assert sleeps.calls == [0.5]


def test_retry_after_with_too_many_digits_for_int_fails_at_once() -> None:
    reply = httpx2.Response(429, headers={"retry-after": "9" * 5000})
    sleeps = Sleeps()
    error = failing(Server(standard(status=[reply])), UpstreamHttpError, sleeps=sleeps)
    assert error.attempts == 1
    assert sleeps.calls == []
    assert len(error.captures) == 3


def test_retry_after_keeps_its_value_with_leading_zeros() -> None:
    reply = httpx2.Response(429, headers={"retry-after": "0" * 5000 + "7"})
    sleeps = Sleeps()
    fetch(Server(standard(status=[reply, ok()])), sleeps=sleeps)
    assert sleeps.calls == [7]


def test_declared_length_with_too_many_digits_for_int_is_refused() -> None:
    reply = httpx2.Response(200, content=chunks(b""), headers={"content-length": "9" * 5000})
    error = failing(Server(standard(status=[reply])), PayloadTooLargeError)
    assert error.resource == "station_status"


def test_malformed_feed_url_raises_a_typed_error_with_the_capture() -> None:
    server = Server(standard(discovery=[ok(discovery_body(status="https://["))]))
    error = failing(server, UnsafeFeedUrlError)
    assert error.resource == "station_status"
    assert server.urls == [DISCOVERY_URL]
    assert len(error.captures) == 1


def test_malformed_redirect_target_raises_a_typed_error() -> None:
    reply = httpx2.Response(302, headers={"location": "https://["})
    error = failing(Server(standard(status=[reply])), UnsafeFeedUrlError)
    assert error.resource == "station_status"
    assert len(error.captures) == 3


def test_budget_spent_while_parsing_is_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow_build(**arguments: Any) -> FetchedFeeds:
        time.sleep(0.2)
        return build_fetched_feeds(**arguments)

    monkeypatch.setattr("citypulse.gbfs.client.build_fetched_feeds", slow_build)
    error = failing(
        Server(standard()), UpstreamTimeoutError, options=ClientOptions(fetch_budget=0.1)
    )
    assert len(error.captures) == 3


def test_retry_after_is_ignored_on_other_statuses() -> None:
    reply = httpx2.Response(500, headers={"retry-after": "7"})
    server = Server(standard(status=[reply, ok()]))
    sleeps = Sleeps()
    fetch(server, sleeps=sleeps)
    assert sleeps.calls == [0.5]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_are_followed_by_hand(status: int) -> None:
    target = f"{BASE}/redirected.json"
    replies = standard(status=[httpx2.Response(status, headers={"location": "/redirected.json"})])
    replies[target] = [ok()]
    server = Server(replies)
    feeds = fetch(server)
    assert feeds.status_payload.final_url == target
    assert feeds.status_payload.request_url == STATUS_URL
    assert server.urls[-2:] == [STATUS_URL, target]


def test_redirect_to_another_origin_is_refused_before_any_request() -> None:
    elsewhere = "https://elsewhere.example/status.json"
    replies = standard(status=[httpx2.Response(302, headers={"location": elsewhere})])
    server = Server(replies)
    error = failing(server, UnsafeFeedUrlError)
    assert elsewhere not in server.urls
    assert error.url == elsewhere


def test_too_many_redirects_raise() -> None:
    server = Server(standard(status=[httpx2.Response(302, headers={"location": STATUS_URL})]))
    options = ClientOptions(max_redirects=2)
    error = failing(server, UpstreamHttpError, options=options)
    assert error.status_code == 302
    assert server.urls.count(STATUS_URL) == 3


def test_exactly_the_maximum_of_redirects_is_followed() -> None:
    first, second = f"{BASE}/1.json", f"{BASE}/2.json"
    replies = standard(status=[httpx2.Response(302, headers={"location": first})])
    replies[first] = [httpx2.Response(302, headers={"location": second})]
    replies[second] = [ok()]
    feeds = fetch(Server(replies), options=ClientOptions(max_redirects=2))
    assert feeds.status_payload.final_url == second


def test_redirect_status_without_a_location_is_an_http_error() -> None:
    server = Server(standard(status=[httpx2.Response(302)]))
    assert failing(server, UpstreamHttpError).status_code == 302
    assert server.urls.count(STATUS_URL) == 1


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("https://elsewhere.example/x.json", id="other-host"),
        pytest.param("http://feed.example/x.json", id="other-scheme"),
        pytest.param("https://feed.example:8443/x.json", id="other-port"),
        pytest.param("ftp://feed.example/x.json", id="ftp"),
        pytest.param("file:///etc/passwd", id="file"),
        pytest.param("https://user:pw@192.0.2.1/x.json", id="credentials"),
        pytest.param("https://feed.example:notaport/x.json", id="malformed"),
    ],
)
def test_unsafe_feed_url_is_refused_before_any_feed_request(url: str) -> None:
    server = Server(standard(discovery=[ok(discovery_body(status=url))]))
    error = failing(server, UnsafeFeedUrlError)
    assert server.urls == [DISCOVERY_URL]
    assert error.resource == "station_status"
    assert "pw" not in str(error.url)


def test_same_origin_ignores_case_and_the_default_port() -> None:
    url = "HTTPS://FEED.example:443/other/status-file.json"
    replies = standard(discovery=[ok(discovery_body(status=url))])
    feeds = fetch(Server(replies))
    assert feeds.status_payload.http_status == 200


@pytest.mark.parametrize(
    "url", ["ftp://feed.example/gbfs.json", "https://user:pw@192.0.2.1/gbfs.json", "no-scheme"]
)
def test_unsafe_discovery_url_sends_no_request(url: str) -> None:
    server = Server({})
    failing(server, UnsafeFeedUrlError, url=url)
    assert server.urls == []


def test_attempt_budget_counts_as_a_timeout_and_is_retried() -> None:
    async def slow(_: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(5)
        return ok()

    server = Server(standard(status=[slow]))
    sleeps = Sleeps()
    options = ClientOptions(attempt_budget=0.02)
    error = failing(server, UpstreamTimeoutError, options=options, sleeps=sleeps)
    assert error.attempts == 3
    assert sleeps.calls == [0.5, 1.0]


def test_fetch_budget_counts_as_a_timeout_and_keeps_the_captures() -> None:
    async def slow(_: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(5)
        return ok()

    server = Server(standard(status=[slow]))
    error = failing(server, UpstreamTimeoutError, options=ClientOptions(fetch_budget=0.05))
    assert error.resource == "station_status"
    assert len(error.captures) == 2
    assert server.urls.count(STATUS_URL) == 1


def test_cancellation_is_never_swallowed() -> None:
    started = asyncio.Event()

    async def slow(_: httpx2.Request) -> httpx2.Response:
        started.set()
        await asyncio.sleep(5)
        return ok()

    server = Server(standard(status=[slow]))

    async def scenario() -> None:
        async with make_http_client(transport=httpx2.MockTransport(server)) as http:
            task = asyncio.create_task(GbfsClient(http, DISCOVERY_URL).fetch())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


async def chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


LIMIT = max(len(discovery_body()), len(FEED))
LIMITED = ClientOptions(max_body_bytes=LIMIT)


def test_body_exactly_at_the_limit_is_accepted() -> None:
    body = FEED + b" " * (LIMIT - len(FEED))
    feeds = fetch(Server(standard(status=[ok(body)])), options=LIMITED)
    assert len(feeds.status_payload.body) == LIMIT


def test_body_above_the_limit_while_streaming_is_refused_without_retry_or_capture() -> None:
    reply = httpx2.Response(200, content=chunks(b"a" * LIMIT, b"b"))
    server = Server(standard(status=[reply]))
    error = failing(server, PayloadTooLargeError, options=LIMITED)
    assert error.resource == "station_status"
    assert error.attempts == 1
    assert server.urls.count(STATUS_URL) == 1
    assert [capture.feed_name for capture in error.captures] == [
        "discovery",
        "station_information",
    ]


def test_declared_length_above_the_limit_is_refused() -> None:
    reply = httpx2.Response(200, content=chunks(b""), headers={"content-length": str(LIMIT + 1)})
    failing(Server(standard(status=[reply])), PayloadTooLargeError, options=LIMITED)


def test_dishonest_declared_length_does_not_bypass_the_limit() -> None:
    reply = httpx2.Response(
        200, content=chunks(b"a" * (LIMIT + 1)), headers={"content-length": "1"}
    )
    failing(Server(standard(status=[reply])), PayloadTooLargeError, options=LIMITED)


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate"])
def test_unexpected_content_encoding_is_refused(encoding: str) -> None:
    reply = httpx2.Response(200, content=chunks(FEED), headers={"content-encoding": encoding})
    server = Server(standard(status=[reply]))
    error = failing(server, UpstreamSchemaError)
    assert server.urls.count(STATUS_URL) == 1
    assert error.resource == "station_status"


def test_identity_content_encoding_is_accepted() -> None:
    server = Server(standard(status=[ok(FEED, **{"content-encoding": "identity"})]))
    assert fetch(server).status_payload.body == FEED


def test_interrupted_body_is_retried_and_never_captured() -> None:
    async def broken() -> AsyncIterator[bytes]:
        yield b"partial"
        raise httpx2.ReadError("connection lost")

    server = Server(standard(status=[httpx2.Response(200, content=broken()), ok()]))
    feeds = fetch(server)
    assert feeds.status_payload.attempt == 2
    assert all(capture.body != b"partial" for capture in feeds.captures)


def test_http_client_ignores_the_environment_and_redirects() -> None:
    async def scenario() -> None:
        async with make_http_client() as http:
            assert http.trust_env is False
            assert http.follow_redirects is False

    asyncio.run(scenario())


def test_fetch_and_normalise_the_committed_snapshot_end_to_end() -> None:
    root = Path(__file__).resolve().parents[3] / "sample-data" / "gbfs" / "20261025T001551Z"
    replies = standard(
        information=[ok((root / "en" / "station_information.json").read_bytes())],
        status=[ok((root / "en" / "station_status.json").read_bytes())],
    )
    source = SourceContext(
        source_id="bixi",
        system_id="Bixi_MTL",
        attribution="BIXI Montréal",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
    )
    batch = normalise(fetch(Server(replies)), source=source)
    assert len(batch.stations) == 1121
    assert batch.collected_at == NOW
