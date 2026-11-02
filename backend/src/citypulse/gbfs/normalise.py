"""Pure normalisation of fetched feeds: no I/O, no clock, inputs unchanged.

Implements docs/data-quality.md. The reference time R is `collected_at` of the status payload.
Every problem found is kept as a quality flag, so a state never hides another fact.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import JsonValue

from citypulse.gbfs.models import (
    FeedName,
    FeedProvenance,
    Freshness,
    QualityCode,
    QualityDetail,
    RecordRef,
    RejectedRecord,
    ServiceState,
    SourceContext,
    Station,
    StationSnapshotBatch,
    StationState,
)
from citypulse.gbfs.raw import FetchedFeeds, RawPayload, RawStationFeed

TIMESTAMP_FLOOR = 946684800  # 2000-01-01T00:00:00Z: catches the 86400 sentinel and unknown ones
REQUIRED_COUNTS = ("num_bikes_available", "num_docks_available")
OPTIONAL_COUNTS = ("num_bikes_disabled", "num_docks_disabled")
SERVICE_FLAGS = ("is_installed", "is_renting", "is_returning")
INFORMATION: FeedName = "station_information"
STATUS: FeedName = "station_status"

type _Record = Mapping[str, JsonValue]
type _Issues = set[tuple[QualityCode, str]]


@dataclass(frozen=True, slots=True)
class NormalisationPolicy:
    stale_after_seconds: int = 3600
    future_skew_seconds: int = 300


DEFAULT_POLICY = NormalisationPolicy()


@dataclass(frozen=True, slots=True)
class _Located:
    ref: RecordRef
    record: _Record


def _details(issues: _Issues) -> tuple[QualityDetail, ...]:
    return tuple(QualityDetail(code=code, path=path) for code, path in sorted(issues))


def _flags(issues: _Issues) -> tuple[QualityCode, ...]:
    return tuple(sorted({code for code, _ in issues}))


def _rejected(
    ref: RecordRef, station_id: str | None, code: QualityCode, field: str
) -> RejectedRecord:
    issues: _Issues = {(code, f"{ref.feed}.{field}")}
    return RejectedRecord(
        source=ref,
        station_id=station_id,
        quality_flags=_flags(issues),
        quality_details=_details(issues),
    )


def _group_records(
    feed: FeedName, entries: Iterable[JsonValue]
) -> tuple[dict[str, list[_Located]], list[RejectedRecord]]:
    """Group usable records by id. Records without a usable id are rejected at once (DQ-10)."""
    by_id: defaultdict[str, list[_Located]] = defaultdict(list)
    rejected: list[RejectedRecord] = []
    for index, entry in enumerate(entries):
        ref = RecordRef(feed=feed, record_index=index)
        if not isinstance(entry, dict):
            rejected.append(_rejected(ref, None, "record_malformed", "record"))
            continue
        station_id = entry.get("station_id")
        if not isinstance(station_id, str) or not station_id:
            rejected.append(_rejected(ref, None, "station_id_invalid", "station_id"))
            continue
        by_id[station_id].append(_Located(ref=ref, record=entry))
    return by_id, rejected


def _select_records(
    feeds: FetchedFeeds,
) -> tuple[dict[str, _Located], dict[str, _Located], tuple[RejectedRecord, ...]]:
    """Split the feeds into usable records. An id repeated in one feed is unusable in both."""
    information, rejected = _group_records(INFORMATION, feeds.information.data.stations)
    status, rejected_status = _group_records(STATUS, feeds.status.data.stations)
    rejected += rejected_status
    repeated = {
        station_id
        for groups in (information, status)
        for station_id, located in groups.items()
        if len(located) > 1
    }
    for groups in (information, status):
        for station_id in repeated & groups.keys():
            rejected += [
                _rejected(item.ref, station_id, "duplicate_id", "station_id")
                for item in groups[station_id]
            ]
    rejected.sort(key=lambda item: (item.source.feed, item.source.record_index))
    usable = (
        {key: value[0] for key, value in groups.items() if key not in repeated}
        for groups in (information, status)
    )
    return next(usable), next(usable), tuple(rejected)


def _source_time(
    value: JsonValue, collected_at: datetime, policy: NormalisationPolicy
) -> datetime | None:
    """A source time, or None outside [floor, collected_at + skew] (DQ-6). Integers only."""
    if type(value) is not int:
        return None
    ceiling = collected_at + timedelta(seconds=policy.future_skew_seconds)
    if not TIMESTAMP_FLOOR <= value <= ceiling.timestamp():
        return None
    return datetime.fromtimestamp(value, UTC)


def _coordinate(value: JsonValue, limit: int) -> float | None:
    """The comparison alone rejects NaN and infinities, and never overflows on a huge integer."""
    if (type(value) is int or type(value) is float) and abs(value) <= limit:
        return float(value)
    return None


def _coordinates(record: _Record, issues: _Issues) -> tuple[float, float] | None:
    """A valid WGS84 pair, or None (DQ-5). There is no service-area box."""
    latitude = _coordinate(record.get("lat"), 90)
    longitude = _coordinate(record.get("lon"), 180)
    if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
        issues.add(("coordinates_invalid", f"{INFORMATION}.coordinates"))
        return None
    return latitude, longitude


def _capacity(record: _Record, issues: _Issues) -> int | None:
    if "capacity" not in record:
        return None
    value = record["capacity"]
    if type(value) is int and value >= 0:
        return value
    issues.add(("capacity_invalid", f"{INFORMATION}.capacity"))
    return None


def _text(record: _Record, field: str, issues: _Issues) -> str | None:
    if field not in record:
        return None
    value = record[field]
    if isinstance(value, str):
        return value
    issues.add(("name_invalid", f"{INFORMATION}.{field}"))
    return None


def _service_flag(record: _Record, field: str, issues: _Issues) -> bool | None:
    """Booleans pass. Integers 0 and 1 are coerced and flagged. Nothing else is accepted (DQ-3)."""
    value = record.get(field)
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        issues.add(("flag_coerced", f"{STATUS}.{field}"))
        return value == 1
    issues.add(("flag_invalid", f"{STATUS}.{field}"))
    return None


def _count(record: _Record, field: str, issues: _Issues) -> int | None:
    """An integer >= 0, or None with a flag (DQ-9). Only the two available counts are required."""
    if field not in record:
        if field in REQUIRED_COUNTS:
            issues.add(("count_invalid", f"{STATUS}.{field}"))
        return None
    value = record[field]
    if type(value) is not int:
        issues.add(("count_invalid", f"{STATUS}.{field}"))
        return None
    if value < 0:
        issues.add(("count_negative", f"{STATUS}.{field}"))
        return None
    return value


def _service_state(flags: Iterable[bool | None], has_status: bool) -> ServiceState:
    values = list(flags)
    if False in values:  # DQ-4: any false flag is out of service, not a shortage
        return "out_of_service"
    if not has_status or None in values:
        return "unknown"
    return "in_service"


def _freshness(
    observed_at: datetime | None, reference: datetime, policy: NormalisationPolicy, issues: _Issues
) -> Freshness:
    if observed_at is None:
        return "unknown"
    if (reference - observed_at).total_seconds() > policy.stale_after_seconds:
        issues.add(("stale", f"{STATUS}.last_reported"))
        return "stale"
    return "fresh"


def _state(
    *,
    has_coordinates: bool,
    has_status: bool,
    status_valid: bool,
    service_state: ServiceState,
    freshness: Freshness,
) -> StationState:
    """First match wins. Without an information record there are no coordinates either."""
    if not has_coordinates:
        return "quarantined"
    if not has_status:
        return "no_status"
    if not status_valid:
        return "status_invalid"
    if service_state == "out_of_service":
        return "out_of_service"
    return "stale" if freshness == "stale" else "in_service"


def _station(
    station_id: str,
    information: _Located | None,
    status: _Located | None,
    reference: datetime,
    policy: NormalisationPolicy,
) -> Station:
    issues: _Issues = set()
    info: _Record = information.record if information is not None else {}
    record: _Record = status.record if status is not None else {}

    coordinates = None
    if information is not None:
        coordinates = _coordinates(info, issues)
    else:
        issues.add(("information_missing", f"{INFORMATION}.record"))

    observed_at = None
    flags: dict[str, bool | None] = dict.fromkeys(SERVICE_FLAGS)
    counts: dict[str, int | None] = dict.fromkeys(REQUIRED_COUNTS + OPTIONAL_COUNTS)
    other_counts: dict[str, int | None] = {}
    if status is not None:
        flags = {field: _service_flag(record, field, issues) for field in SERVICE_FLAGS}
        observed_at = _source_time(record.get("last_reported"), reference, policy)
        if observed_at is None:
            issues.add(("last_reported_invalid", f"{STATUS}.last_reported"))
        elif observed_at > reference:
            issues.add(("last_reported_future", f"{STATUS}.last_reported"))
        counts = {field: _count(record, field, issues) for field in counts}
        other_counts = {
            field: _count(record, field, issues)
            for field in sorted(record)
            if field.startswith("num_") and field not in counts
        }

    bikes, docks = counts["num_bikes_available"], counts["num_docks_available"]
    status_valid = (
        status is not None
        and None not in flags.values()
        and observed_at is not None
        and bikes is not None
        and docks is not None
    )
    if status is None:
        issues.add(("status_missing", f"{STATUS}.record"))
    elif not status_valid:
        issues.add(("status_invalid", f"{STATUS}.record"))

    occupancy = None
    if status_valid and bikes is not None and docks is not None and bikes + docks > 0:
        occupancy = bikes / (bikes + docks)  # DQ-8: capacity is never the denominator
    if not status_valid:  # DQ-1: missing is not zero, and a bad record gives no usable count
        counts = dict.fromkeys(counts)
        other_counts = dict.fromkeys(other_counts)

    service_state = _service_state(flags.values(), status is not None)
    freshness = _freshness(observed_at, reference, policy, issues)
    return Station(
        station_id=station_id,
        name=_text(info, "name", issues),
        short_name=_text(info, "short_name", issues),
        external_id=_text(info, "external_id", issues),
        latitude=coordinates[0] if coordinates is not None else None,
        longitude=coordinates[1] if coordinates is not None else None,
        capacity=_capacity(info, issues),
        num_bikes_available=counts["num_bikes_available"],
        num_docks_available=counts["num_docks_available"],
        num_bikes_disabled=counts["num_bikes_disabled"],
        num_docks_disabled=counts["num_docks_disabled"],
        other_counts=other_counts,
        is_installed=flags["is_installed"],
        is_renting=flags["is_renting"],
        is_returning=flags["is_returning"],
        observed_at=observed_at,
        occupancy=occupancy,
        status_valid=status_valid,
        service_state=service_state,
        freshness=freshness,
        state=_state(
            has_coordinates=coordinates is not None,
            has_status=status is not None,
            status_valid=status_valid,
            service_state=service_state,
            freshness=freshness,
        ),
        information_ref=information.ref if information is not None else None,
        status_ref=status.ref if status is not None else None,
        quality_flags=_flags(issues),
        quality_details=_details(issues),
    )


def _provenance(
    name: FeedName,
    payload: RawPayload,
    feed: RawStationFeed,
    policy: NormalisationPolicy,
    issues: _Issues,
) -> FeedProvenance:
    last_updated = _source_time(feed.last_updated, payload.collected_at, policy)
    if last_updated is None:
        issues.add(("feed_timestamp_invalid", f"{name}.last_updated"))
    ttl_seconds = None
    if "ttl" in feed.model_fields_set:
        if type(feed.ttl) is int and feed.ttl >= 0:
            ttl_seconds = feed.ttl
        else:
            issues.add(("ttl_invalid", f"{name}.ttl"))
    return FeedProvenance(
        feed_name=name,
        sha256=payload.sha256,
        collected_at=payload.collected_at,
        last_updated=last_updated,
        ttl_seconds=ttl_seconds,
        version=feed.version if isinstance(feed.version, str) else None,
    )


def normalise(
    feeds: FetchedFeeds,
    *,
    source: SourceContext,
    policy: NormalisationPolicy = DEFAULT_POLICY,
) -> StationSnapshotBatch:
    """Turn fetched feeds into one snapshot batch, keeping every problem as a quality flag."""
    reference = feeds.status_payload.collected_at
    information, status, rejected = _select_records(feeds)
    issues: _Issues = set()
    provenance = (
        _provenance(INFORMATION, feeds.information_payload, feeds.information, policy, issues),
        _provenance(STATUS, feeds.status_payload, feeds.status, policy, issues),
    )
    return StationSnapshotBatch(
        source=source,
        collected_at=reference,
        provenance=provenance,
        stations=tuple(
            _station(
                station_id,
                information.get(station_id),
                status.get(station_id),
                reference,
                policy,
            )
            for station_id in sorted(information.keys() | status.keys())
        ),
        rejected_records=rejected,
        quality_flags=_flags(issues),
        quality_details=_details(issues),
    )
