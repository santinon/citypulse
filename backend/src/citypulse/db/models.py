"""Stored source inputs and quality findings; derived station states are not columns."""

from datetime import UTC, datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

COORDINATE_PAIR_CHECK = (
    "(latitude IS NULL AND longitude IS NULL) OR "
    "(latitude IS NOT NULL AND longitude IS NOT NULL "
    "AND latitude BETWEEN -90 AND 90 "
    "AND longitude BETWEEN -180 AND 180 "
    "AND NOT (latitude = 0 AND longitude = 0))"
)


# None becomes SQL NULL, so NOT NULL refuses it. The default would store a JSON null and pass.
JSON_COLUMN = JSONB(none_as_null=True)


class UTCDateTime(TypeDecorator[datetime]):
    """Reject naive application inputs and normalise aware datetimes to UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("Database timestamps must be timezone-aware.")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IngestRun(Base):
    __tablename__ = "ingest_run"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[str] = mapped_column(Text)
    system_id: Mapped[str] = mapped_column(Text)
    attribution: Mapped[str] = mapped_column(Text)
    licence_url: Mapped[str] = mapped_column(Text)
    discovery_url: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(Text)
    error: Mapped[dict[str, object] | None] = mapped_column(JSON_COLUMN)
    quality_flags: Mapped[list[str]] = mapped_column(
        JSON_COLUMN, server_default=text("'[]'::jsonb")
    )
    quality_details: Mapped[list[dict[str, str]]] = mapped_column(
        JSON_COLUMN, server_default=text("'[]'::jsonb")
    )


class RawPayloadRow(Base):
    __tablename__ = "raw_payload"
    __table_args__ = (
        CheckConstraint("attempt > 0", name="attempt_positive"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_lowercase_hex"),
        CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="content_sha256_lowercase_hex",
        ),
        CheckConstraint("byte_length >= 0", name="byte_length_nonnegative"),
        CheckConstraint(
            "(body IS NULL) = (body_expired_at IS NOT NULL OR body_same_as_payload_id IS NOT NULL)",
            name="body_expiry",
        ),
        Index("ix_raw_payload_ingest_run_id_feed_name", "ingest_run_id", "feed_name"),
        Index(
            "ix_raw_payload_live_collected_at",
            "collected_at",
            postgresql_where=text("body IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ingest_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ingest_run.id", ondelete="RESTRICT")
    )
    feed_name: Mapped[str] = mapped_column(Text)
    request_url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer)
    http_status: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime())
    sha256: Mapped[str] = mapped_column(CHAR(64))
    content_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    byte_length: Mapped[int] = mapped_column(BigInteger)
    # Zlib-compressed response bytes; sha256 and byte_length describe the original bytes.
    body: Mapped[bytes | None] = mapped_column(LargeBinary)
    body_expired_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    body_same_as_payload_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("raw_payload.id", ondelete="RESTRICT")
    )
    retain_forever: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    last_updated: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ttl_seconds: Mapped[int | None] = mapped_column(BigInteger)
    version: Mapped[str | None] = mapped_column(Text)
    diagnostics: Mapped[dict[str, object]] = mapped_column(
        JSON_COLUMN, server_default=text("'{}'::jsonb")
    )


class StationRow(Base):
    __tablename__ = "station"
    __table_args__ = (
        UniqueConstraint("system_id", "gbfs_station_id"),
        CheckConstraint("length(system_id) > 0", name="system_id_nonempty"),
        CheckConstraint("length(gbfs_station_id) > 0", name="gbfs_station_id_nonempty"),
        CheckConstraint("capacity >= 0", name="capacity_nonnegative"),
        CheckConstraint(COORDINATE_PAIR_CHECK, name="coordinates_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    system_id: Mapped[str] = mapped_column(Text)
    gbfs_station_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    short_name: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Double)
    longitude: Mapped[float | None] = mapped_column(Double)
    capacity: Mapped[int | None] = mapped_column(BigInteger)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    latest_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ingest_run.id", ondelete="RESTRICT")
    )
    information_payload_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("raw_payload.id", ondelete="RESTRICT")
    )
    information_record_index: Mapped[int | None] = mapped_column(Integer)
    current_status: Mapped[dict[str, object]] = mapped_column(JSON_COLUMN)
    quality_flags: Mapped[list[str]] = mapped_column(JSON_COLUMN)
    quality_details: Mapped[list[dict[str, str]]] = mapped_column(JSON_COLUMN)


class StationLocationHistory(Base):
    __tablename__ = "station_location_history"
    __table_args__ = (
        UniqueConstraint("station_id", "recorded_from"),
        CheckConstraint("recorded_until > recorded_from", name="recorded_interval"),
        CheckConstraint(COORDINATE_PAIR_CHECK, name="coordinates_valid"),
        Index(
            "uq_station_location_history_open_station",
            "station_id",
            unique=True,
            postgresql_where=text("recorded_until IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    station_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("station.id", ondelete="RESTRICT")
    )
    recorded_from: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    latitude: Mapped[float | None] = mapped_column(Double)
    longitude: Mapped[float | None] = mapped_column(Double)
    information_payload_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("raw_payload.id", ondelete="RESTRICT")
    )
    information_record_index: Mapped[int | None] = mapped_column(Integer)
    quality_flags: Mapped[list[str]] = mapped_column(JSON_COLUMN)
    quality_details: Mapped[list[dict[str, str]]] = mapped_column(JSON_COLUMN)


class StationObservation(Base):
    __tablename__ = "station_observation"
    __table_args__ = (
        UniqueConstraint("station_id", "last_reported"),
        CheckConstraint("status_record_index >= 0", name="status_record_index_nonnegative"),
        CheckConstraint("num_bikes_available >= 0", name="num_bikes_available_nonnegative"),
        CheckConstraint("num_docks_available >= 0", name="num_docks_available_nonnegative"),
        CheckConstraint("num_bikes_disabled >= 0", name="num_bikes_disabled_nonnegative"),
        CheckConstraint("num_docks_disabled >= 0", name="num_docks_disabled_nonnegative"),
        Index("ix_station_observation_last_reported", "last_reported"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    station_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("station.id", ondelete="RESTRICT")
    )
    ingest_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ingest_run.id", ondelete="RESTRICT")
    )
    last_reported: Mapped[datetime] = mapped_column(UTCDateTime())
    status_payload_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("raw_payload.id", ondelete="RESTRICT")
    )
    status_record_index: Mapped[int] = mapped_column(Integer)
    num_bikes_available: Mapped[int | None] = mapped_column(BigInteger)
    num_docks_available: Mapped[int | None] = mapped_column(BigInteger)
    num_bikes_disabled: Mapped[int | None] = mapped_column(BigInteger)
    num_docks_disabled: Mapped[int | None] = mapped_column(BigInteger)
    other_counts: Mapped[dict[str, int | None]] = mapped_column(
        JSON_COLUMN, server_default=text("'{}'::jsonb")
    )
    is_installed: Mapped[bool | None] = mapped_column(Boolean)
    is_renting: Mapped[bool | None] = mapped_column(Boolean)
    is_returning: Mapped[bool | None] = mapped_column(Boolean)
    status_valid: Mapped[bool] = mapped_column(Boolean)
    quality_flags: Mapped[list[str]] = mapped_column(JSON_COLUMN)
    quality_details: Mapped[list[dict[str, str]]] = mapped_column(JSON_COLUMN)
