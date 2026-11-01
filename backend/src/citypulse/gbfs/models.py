"""Canonical station snapshots. Datetimes are timezone-aware UTC; null means unknown, never 0."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

QualityCode = Literal[
    "status_missing",
    "status_invalid",
    "information_missing",
    "flag_coerced",
    "flag_invalid",
    "coordinates_invalid",
    "duplicate_id",
    "station_id_invalid",
    "record_malformed",
    "last_reported_invalid",
    "last_reported_future",
    "stale",
    "count_invalid",
    "count_negative",
    "capacity_invalid",
    "name_invalid",
    "feed_timestamp_invalid",
    "ttl_invalid",
]
StationState = Literal[
    "quarantined", "no_status", "status_invalid", "out_of_service", "stale", "in_service"
]
ServiceState = Literal["in_service", "out_of_service", "unknown"]
Freshness = Literal["fresh", "stale", "unknown"]
FeedName = Literal["station_information", "station_status"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SourceContext(_Model):
    """Supplied by the caller. Attribution travels with the data (AGENTS.md data contract)."""

    source_id: str
    system_id: str
    attribution: str
    licence_url: str


class QualityDetail(_Model):
    code: QualityCode
    path: str  # "<feed name>.<field>"


class RecordRef(_Model):
    """Where a record sits in `data.stations`. Unknown source fields stay reachable there."""

    feed: FeedName
    record_index: int


class FeedProvenance(_Model):
    feed_name: FeedName
    sha256: str
    collected_at: datetime
    last_updated: datetime | None
    ttl_seconds: int | None
    version: str | None


class RejectedRecord(_Model):
    """A record that cannot be joined: it creates no station but stays visible."""

    source: RecordRef
    station_id: str | None
    quality_flags: tuple[QualityCode, ...]
    quality_details: tuple[QualityDetail, ...]


class Station(_Model):
    station_id: str
    name: str | None
    short_name: str | None
    external_id: str | None
    latitude: float | None
    longitude: float | None
    capacity: int | None
    num_bikes_available: int | None
    num_docks_available: int | None
    num_bikes_disabled: int | None
    num_docks_disabled: int | None
    other_counts: dict[str, int | None]
    is_installed: bool | None
    is_renting: bool | None
    is_returning: bool | None
    observed_at: datetime | None
    occupancy: float | None
    status_valid: bool
    service_state: ServiceState
    freshness: Freshness
    state: StationState
    information_ref: RecordRef | None
    status_ref: RecordRef | None
    quality_flags: tuple[QualityCode, ...]
    quality_details: tuple[QualityDetail, ...]

    @property
    def mappable(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def risk_eligible(self) -> bool:
        """May count in the two separate risks. The thresholds themselves belong to #7."""
        return (
            self.mappable
            and self.status_valid
            and self.service_state == "in_service"
            and self.freshness == "fresh"
        )


class StationSnapshotBatch(_Model):
    source: SourceContext
    collected_at: datetime
    provenance: tuple[FeedProvenance, ...]  # exactly information, then status
    stations: tuple[Station, ...]
    rejected_records: tuple[RejectedRecord, ...]
    quality_flags: tuple[QualityCode, ...]
    quality_details: tuple[QualityDetail, ...]

    @property
    def data_as_of(self) -> datetime | None:
        """`last_updated` of the status feed. Never replaced by the fetch time."""
        return self.provenance[-1].last_updated
