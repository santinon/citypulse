"""Raw capture of feed responses, and tolerant models of the feed envelope.

The envelope is strict: `data.stations` must be a list. Station records stay plain JSON values, with
no coercion and with unknown fields kept, so one bad record never rejects a whole feed. Field
validation belongs to `citypulse.gbfs.normalise`.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import NoReturn, Self

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from citypulse.gbfs.errors import UpstreamJsonError, UpstreamSchemaError


@dataclass(frozen=True, slots=True)
class RawPayload:
    """One complete response, exactly as received."""

    feed_name: str
    attempt: int
    request_url: str
    final_url: str
    http_status: int
    content_type: str | None
    last_modified: str | None
    body: bytes = field(repr=False)
    sha256: str
    collected_at: datetime

    @classmethod
    def from_body(
        cls,
        *,
        feed_name: str,
        body: bytes,
        collected_at: datetime,
        request_url: str,
        final_url: str | None = None,
        attempt: int = 1,
        http_status: int = 200,
        content_type: str | None = None,
        last_modified: str | None = None,
    ) -> Self:
        """Hash the body and normalise the time to UTC. A naive time raises ValueError."""
        if collected_at.utcoffset() is None:
            raise ValueError("collected_at must be timezone-aware")
        return cls(
            feed_name=feed_name,
            attempt=attempt,
            request_url=request_url,
            final_url=final_url if final_url is not None else request_url,
            http_status=http_status,
            content_type=content_type,
            last_modified=last_modified,
            body=body,
            sha256=hashlib.sha256(body).hexdigest(),
            collected_at=collected_at.astimezone(UTC),
        )


class RawStationData(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, frozen=True)

    stations: list[JsonValue]


class RawStationFeed(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, frozen=True)

    last_updated: JsonValue = None
    ttl: JsonValue = None
    version: JsonValue = None
    data: RawStationData


def _unique_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"repeated object key {key!r}")
        result[key] = value
    return result


def _no_constant(name: str) -> NoReturn:
    raise ValueError(f"non-finite number {name}")


def parse_station_feed(payload: RawPayload) -> RawStationFeed:
    """Parse a station feed body, strictly. Nothing is parsed before the capture exists."""
    try:
        document = json.loads(
            payload.body.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_no_constant,
        )
    except (ValueError, RecursionError) as error:
        raise UpstreamJsonError(
            f"invalid JSON: {error}",
            resource=payload.feed_name,
            url=payload.final_url,
            captures=(payload,),
        ) from error
    try:
        return RawStationFeed.model_validate(document)
    except (ValidationError, RecursionError) as error:
        raise UpstreamSchemaError(
            "wrong feed envelope",
            resource=payload.feed_name,
            url=payload.final_url,
            captures=(payload,),
        ) from error


@dataclass(frozen=True, slots=True)
class FetchedFeeds:
    """The responses of one complete fetch, with both station feeds parsed."""

    discovery: RawPayload
    information_payload: RawPayload
    status_payload: RawPayload
    information: RawStationFeed
    status: RawStationFeed
    captures: tuple[RawPayload, ...]


def build_fetched_feeds(
    *,
    discovery: RawPayload,
    information: RawPayload,
    status: RawPayload,
    captures: tuple[RawPayload, ...] | None = None,
) -> FetchedFeeds:
    return FetchedFeeds(
        discovery=discovery,
        information_payload=information,
        status_payload=status,
        information=parse_station_feed(information),
        status=parse_station_feed(status),
        captures=captures if captures is not None else (discovery, information, status),
    )
