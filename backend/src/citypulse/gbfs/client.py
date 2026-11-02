"""HTTP client for the GBFS feed: one fetch reads discovery, then both station feeds.

Feed URLs come from the discovery file at every fetch, never from a hardcoded path (DQ-14). All or
nothing: any failure raises a typed error carrying the responses captured so far, and no partial
result is returned. Each complete response is captured before any parsing (DQ-13).
"""

import asyncio
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypedDict
from urllib.parse import urljoin, urlsplit

import httpx2

from citypulse import __version__
from citypulse.gbfs.errors import (
    DiscoveryError,
    PayloadTooLargeError,
    UnsafeFeedUrlError,
    UpstreamError,
    UpstreamHttpError,
    UpstreamSchemaError,
    UpstreamTimeoutError,
    UpstreamTransportError,
)
from citypulse.gbfs.raw import FetchedFeeds, RawPayload, build_fetched_feeds, parse_json

DISCOVERY = "discovery"
INFORMATION = "station_information"
STATUS = "station_status"
LANGUAGE = "en"
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
RETRY_AFTER_STATUSES = {429, 503}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_HEADER_DIGITS = 18
HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "identity",
    "User-Agent": f"citypulse/{__version__} (+https://github.com/santinon/citypulse)",
}

type _Origin = tuple[str, str, int]


class _Context(TypedDict):
    """Where an error happened."""

    resource: str
    url: str
    attempts: int


@dataclass(frozen=True, slots=True)
class ClientOptions:
    connect_timeout: float = 5.0
    read_timeout: float = 10.0
    write_timeout: float = 5.0
    pool_timeout: float = 5.0
    attempt_budget: float = 30.0
    fetch_budget: float = 120.0
    max_attempts: int = 3
    backoff: tuple[float, ...] = (0.5, 1.0)
    max_redirects: int = 5
    max_body_bytes: int = 10 * 1024 * 1024


DEFAULT_OPTIONS = ClientOptions()


def _now() -> datetime:
    return datetime.now(UTC)


def make_http_client(*, transport: httpx2.AsyncBaseTransport | None = None) -> httpx2.AsyncClient:
    """No proxy or authentication from the environment. The caller owns its lifecycle."""
    return httpx2.AsyncClient(transport=transport, trust_env=False, follow_redirects=False)


def _origin_of(resource: str, url: str) -> _Origin:
    """The origin of an HTTP(S) URL without credentials. Anything else is refused."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise UnsafeFeedUrlError("malformed URL", resource=resource) from error
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname:
        raise UnsafeFeedUrlError("only HTTP(S) URLs are allowed", resource=resource, url=url)
    if parts.username is not None or parts.password is not None:
        raise UnsafeFeedUrlError("URL with credentials refused", resource=resource)
    return scheme, parts.hostname, port or (443 if scheme == "https" else 80)


def _resolve(resource: str, base: str, reference: str) -> str:
    try:
        return urljoin(base, reference)
    except ValueError as error:
        raise UnsafeFeedUrlError("malformed URL", resource=resource) from error


def _header_number(text: str) -> int | None:
    """A non-negative decimal header, or None. More digits than int() accepts count as huge."""
    if not (text.isascii() and text.isdigit()):
        return None
    digits = text.lstrip("0") or "0"
    return int(digits) if len(digits) <= MAX_HEADER_DIGITS else sys.maxsize


def _feed_urls(discovery: RawPayload) -> dict[str, str]:
    """Find both required feeds by exact name, in English only, whatever the list order."""

    def unusable(message: str) -> DiscoveryError:
        return DiscoveryError(message, resource=DISCOVERY, url=discovery.final_url)

    document = parse_json(discovery)
    data = document.get("data") if isinstance(document, dict) else None
    language = data.get(LANGUAGE) if isinstance(data, dict) else None
    feeds = language.get("feeds") if isinstance(language, dict) else None
    if not isinstance(feeds, list):
        raise unusable(f"no feed list for language {LANGUAGE!r}")
    found: dict[str, str] = {}
    for feed in feeds:
        if not isinstance(feed, dict):
            raise unusable("a feed entry is not an object")
        name, url = feed.get("name"), feed.get("url")
        if name in (INFORMATION, STATUS):
            if not isinstance(url, str):
                raise unusable(f"feed {name} has no URL")
            if name in found:
                raise unusable(f"feed {name} is listed twice")
            found[name] = _resolve(name, discovery.final_url, url)
    for name in (INFORMATION, STATUS):
        if name not in found:
            raise unusable(f"feed {name} is missing")
    return found


class GbfsClient:
    def __init__(
        self,
        http: httpx2.AsyncClient,
        discovery_url: str,
        *,
        options: ClientOptions = DEFAULT_OPTIONS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._http = http
        self._discovery_url = discovery_url
        self._options = options
        self._sleep = sleep
        self._clock = clock
        self._timeout = httpx2.Timeout(
            connect=options.connect_timeout,
            read=options.read_timeout,
            write=options.write_timeout,
            pool=options.pool_timeout,
        )

    async def fetch(self) -> FetchedFeeds:
        captures: list[RawPayload] = []
        resource = DISCOVERY
        started = time.monotonic()
        try:
            async with asyncio.timeout(self._options.fetch_budget):
                origin = _origin_of(DISCOVERY, self._discovery_url)
                discovery = await self._get(
                    DISCOVERY, self._discovery_url, origin, captures, started
                )
                urls = _feed_urls(discovery)
                for name, url in urls.items():
                    self._check_url(name, url, origin)
                resource = INFORMATION
                information = await self._get(
                    INFORMATION, urls[INFORMATION], origin, captures, started
                )
                resource = STATUS
                status = await self._get(STATUS, urls[STATUS], origin, captures, started)
                feeds = build_fetched_feeds(
                    discovery=discovery,
                    information=information,
                    status=status,
                    captures=tuple(captures),
                )
                # Parsing does not yield to the event loop, so the timeout cannot fire during it.
                if time.monotonic() - started > self._options.fetch_budget:
                    raise TimeoutError
                return feeds
        except TimeoutError as error:
            raise UpstreamTimeoutError(
                "the fetch budget is spent", resource=resource, captures=tuple(captures)
            ) from error
        except UpstreamError as error:
            error.captures = tuple(captures)
            raise

    @staticmethod
    def _check_url(resource: str, url: str, origin: _Origin) -> None:
        if _origin_of(resource, url) != origin:
            raise UnsafeFeedUrlError("another origin refused", resource=resource, url=url)

    async def _get(
        self, resource: str, url: str, origin: _Origin, captures: list[RawPayload], started: float
    ) -> RawPayload:
        """One resource, with bounded retries. Only a 2xx response comes back."""
        options = self._options
        for attempt in range(1, options.max_attempts + 1):
            context: _Context = {"resource": resource, "url": url, "attempts": attempt}
            status = None
            headers = httpx2.Headers()
            failure: UpstreamError
            try:
                async with asyncio.timeout(options.attempt_budget):
                    payload, headers = await self._attempt(resource, url, origin, attempt, captures)
            except (TimeoutError, httpx2.TimeoutException) as error:
                failure = UpstreamTimeoutError(f"timeout: {error!r}", **context)
            except httpx2.TransportError as error:
                failure = UpstreamTransportError(f"transport failure: {error!r}", **context)
            except httpx2.InvalidURL as error:
                raise UnsafeFeedUrlError("malformed URL", resource=resource) from error
            else:
                status = payload.http_status
                if 200 <= status < 300:
                    return payload
                failure = UpstreamHttpError(f"HTTP {status}", status_code=status, **context)
                if status not in RETRY_STATUSES:
                    raise failure
            if attempt == options.max_attempts:
                raise failure
            await self._sleep(self._delay(status, headers, attempt, started, failure))
        raise AssertionError("max_attempts must be at least 1")

    def _delay(
        self,
        status: int | None,
        headers: httpx2.Headers,
        attempt: int,
        started: float,
        failure: UpstreamError,
    ) -> float:
        """The backoff, or an integer Retry-After that fits in what remains of the fetch budget."""
        backoff = self._options.backoff
        delay = backoff[min(attempt, len(backoff)) - 1]
        text = headers.get("retry-after", "") if status in RETRY_AFTER_STATUSES else ""
        requested = _header_number(text)
        if requested is not None:
            delay = requested
            if delay > self._options.fetch_budget - (time.monotonic() - started):
                raise failure
        return delay

    async def _attempt(
        self, resource: str, url: str, origin: _Origin, attempt: int, captures: list[RawPayload]
    ) -> tuple[RawPayload, httpx2.Headers]:
        """One attempt, following redirects by hand so that each target is checked first."""
        current = url
        last_status = None
        for _ in range(self._options.max_redirects + 1):
            self._check_url(resource, current, origin)
            payload, headers = await self._request(resource, url, current, attempt)
            captures.append(payload)
            last_status = payload.http_status
            location = headers.get("location")
            if payload.http_status not in REDIRECT_STATUSES or location is None:
                return payload, headers
            current = _resolve(resource, current, location)
        raise UpstreamHttpError(
            f"more than {self._options.max_redirects} redirects",
            resource=resource,
            url=current,
            attempts=attempt,
            status_code=last_status,
        )

    async def _request(
        self, resource: str, request_url: str, url: str, attempt: int
    ) -> tuple[RawPayload, httpx2.Headers]:
        """Read one response, up to the size limit. Oversized or broken bodies are no captures."""
        limit = self._options.max_body_bytes
        context: _Context = {"resource": resource, "url": url, "attempts": attempt}
        async with self._http.stream(
            "GET", url, headers=HEADERS, timeout=self._timeout, follow_redirects=False
        ) as response:
            encoding = response.headers.get("content-encoding", "identity").strip().lower()
            if encoding not in {"", "identity"}:
                raise UpstreamSchemaError(f"unexpected content encoding {encoding!r}", **context)
            declared = _header_number(response.headers.get("content-length", ""))
            if declared is not None and declared > limit:
                raise PayloadTooLargeError(f"body above {limit} bytes", **context)
            chunks: list[bytes] = []
            size = 0
            # With no content encoding, these are the exact bytes received.
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise PayloadTooLargeError(f"body above {limit} bytes", **context)
                chunks.append(chunk)
        payload = RawPayload.from_body(
            feed_name=resource,
            body=b"".join(chunks),
            collected_at=self._clock(),
            request_url=request_url,
            final_url=url,
            attempt=attempt,
            http_status=response.status_code,
            content_type=response.headers.get("content-type"),
            last_modified=response.headers.get("last-modified"),
        )
        return payload, response.headers
