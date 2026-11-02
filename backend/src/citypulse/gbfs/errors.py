"""Typed errors for everything that can go wrong while reading the GBFS feed."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from citypulse.gbfs.raw import RawPayload


class UpstreamError(Exception):
    """Base error. `captures` holds the complete responses received before the failure."""

    def __init__(
        self,
        message: str,
        *,
        resource: str,
        url: str | None = None,
        attempts: int = 0,
        status_code: int | None = None,
        captures: tuple[RawPayload, ...] = (),
    ) -> None:
        super().__init__(message)
        self.resource = resource
        self.url = url
        self.attempts = attempts
        self.status_code = status_code
        self.captures = captures


class UpstreamTimeoutError(UpstreamError):
    """A request timed out, or the time budget of a fetch is spent."""


class UpstreamTransportError(UpstreamError):
    """A connection or read failure."""


class UpstreamHttpError(UpstreamError):
    """The final HTTP status is not 2xx, or there were too many redirects."""


class DiscoveryError(UpstreamError):
    """The discovery file is valid JSON but its content cannot be used."""


class UnsafeFeedUrlError(UpstreamError):
    """A URL has another scheme, embedded credentials, or another origin."""


class PayloadTooLargeError(UpstreamError):
    """A response body is above the size limit."""


class UpstreamJsonError(UpstreamError):
    """A body is not UTF-8 or not strict JSON (repeated key, NaN or Infinity included)."""


class UpstreamSchemaError(UpstreamError):
    """The feed envelope is wrong, or the response has an unexpected content encoding."""
