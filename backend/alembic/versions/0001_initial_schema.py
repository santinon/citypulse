"""Create the initial source, payload, station and observation tables.

Revision ID: 0001
Revises: None
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ingest_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("attribution", sa.Text(), nullable=False),
        sa.Column("licence_url", sa.Text(), nullable=False),
        sa.Column("discovery_url", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column(
            "quality_flags",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "quality_details",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name=op.f("ck_ingest_run_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingest_run")),
    )
    op.create_table(
        "raw_payload",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("ingest_run_id", sa.BigInteger(), nullable=False),
        sa.Column("feed_name", sa.Text(), nullable=False),
        sa.Column("request_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("content_sha256", sa.CHAR(64), nullable=True),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("body", sa.LargeBinary(), nullable=True),
        sa.Column("body_expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("body_same_as_payload_id", sa.BigInteger(), nullable=True),
        sa.Column("retain_forever", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ttl_seconds", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.Text(), nullable=True),
        sa.Column(
            "diagnostics",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("attempt > 0", name=op.f("ck_raw_payload_attempt_positive")),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_raw_payload_sha256_lowercase_hex"),
        ),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_raw_payload_content_sha256_lowercase_hex"),
        ),
        sa.CheckConstraint(
            "byte_length >= 0",
            name=op.f("ck_raw_payload_byte_length_nonnegative"),
        ),
        sa.CheckConstraint(
            "(body IS NULL) = (body_expired_at IS NOT NULL OR body_same_as_payload_id IS NOT NULL)",
            name=op.f("ck_raw_payload_body_expiry"),
        ),
        sa.ForeignKeyConstraint(
            ["ingest_run_id"],
            ["ingest_run.id"],
            name=op.f("fk_raw_payload_ingest_run_id_ingest_run"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["body_same_as_payload_id"],
            ["raw_payload.id"],
            name=op.f("fk_raw_payload_body_same_as_payload_id_raw_payload"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raw_payload")),
    )
    op.create_index(
        "ix_raw_payload_ingest_run_id_feed_name",
        "raw_payload",
        ["ingest_run_id", "feed_name"],
    )
    op.create_index(
        "ix_raw_payload_live_collected_at",
        "raw_payload",
        ["collected_at"],
        postgresql_where=sa.text("body IS NOT NULL"),
    )
    op.create_table(
        "station",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("gbfs_station_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("short_name", sa.Text(), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Double(), nullable=True),
        sa.Column("longitude", sa.Double(), nullable=True),
        sa.Column("capacity", sa.BigInteger(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_run_id", sa.BigInteger(), nullable=False),
        sa.Column("information_payload_id", sa.BigInteger(), nullable=True),
        sa.Column("information_record_index", sa.Integer(), nullable=True),
        sa.Column("current_status", postgresql.JSONB(), nullable=False),
        sa.Column("quality_flags", postgresql.JSONB(), nullable=False),
        sa.Column("quality_details", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "length(system_id) > 0",
            name=op.f("ck_station_system_id_nonempty"),
        ),
        sa.CheckConstraint(
            "length(gbfs_station_id) > 0",
            name=op.f("ck_station_gbfs_station_id_nonempty"),
        ),
        sa.CheckConstraint("capacity >= 0", name=op.f("ck_station_capacity_nonnegative")),
        sa.CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL "
            "AND latitude BETWEEN -90 AND 90 "
            "AND longitude BETWEEN -180 AND 180 "
            "AND NOT (latitude = 0 AND longitude = 0))",
            name=op.f("ck_station_coordinates_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["latest_run_id"],
            ["ingest_run.id"],
            name=op.f("fk_station_latest_run_id_ingest_run"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["information_payload_id"],
            ["raw_payload.id"],
            name=op.f("fk_station_information_payload_id_raw_payload"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_station")),
        sa.UniqueConstraint(
            "system_id", "gbfs_station_id", name=op.f("uq_station_system_id_gbfs_station_id")
        ),
    )
    op.create_table(
        "station_location_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("station_id", sa.BigInteger(), nullable=False),
        sa.Column("recorded_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latitude", sa.Double(), nullable=True),
        sa.Column("longitude", sa.Double(), nullable=True),
        sa.Column("information_payload_id", sa.BigInteger(), nullable=True),
        sa.Column("information_record_index", sa.Integer(), nullable=True),
        sa.Column("quality_flags", postgresql.JSONB(), nullable=False),
        sa.Column("quality_details", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "recorded_until > recorded_from",
            name=op.f("ck_station_location_history_recorded_interval"),
        ),
        sa.CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL "
            "AND latitude BETWEEN -90 AND 90 "
            "AND longitude BETWEEN -180 AND 180 "
            "AND NOT (latitude = 0 AND longitude = 0))",
            name=op.f("ck_station_location_history_coordinates_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["station.id"],
            name=op.f("fk_station_location_history_station_id_station"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["information_payload_id"],
            ["raw_payload.id"],
            name=op.f("fk_station_location_history_information_payload_id_raw_payload"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_station_location_history")),
        sa.UniqueConstraint(
            "station_id",
            "recorded_from",
            name=op.f("uq_station_location_history_station_id_recorded_from"),
        ),
    )
    op.create_index(
        "uq_station_location_history_open_station",
        "station_location_history",
        ["station_id"],
        unique=True,
        postgresql_where=sa.text("recorded_until IS NULL"),
    )
    op.create_table(
        "station_observation",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("station_id", sa.BigInteger(), nullable=False),
        sa.Column("ingest_run_id", sa.BigInteger(), nullable=False),
        sa.Column("last_reported", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_payload_id", sa.BigInteger(), nullable=False),
        sa.Column("status_record_index", sa.Integer(), nullable=False),
        sa.Column("num_bikes_available", sa.BigInteger(), nullable=True),
        sa.Column("num_docks_available", sa.BigInteger(), nullable=True),
        sa.Column("num_bikes_disabled", sa.BigInteger(), nullable=True),
        sa.Column("num_docks_disabled", sa.BigInteger(), nullable=True),
        sa.Column(
            "other_counts",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_installed", sa.Boolean(), nullable=True),
        sa.Column("is_renting", sa.Boolean(), nullable=True),
        sa.Column("is_returning", sa.Boolean(), nullable=True),
        sa.Column("status_valid", sa.Boolean(), nullable=False),
        sa.Column("quality_flags", postgresql.JSONB(), nullable=False),
        sa.Column("quality_details", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "status_record_index >= 0",
            name=op.f("ck_station_observation_status_record_index_nonnegative"),
        ),
        sa.CheckConstraint(
            "num_bikes_available >= 0",
            name=op.f("ck_station_observation_num_bikes_available_nonnegative"),
        ),
        sa.CheckConstraint(
            "num_docks_available >= 0",
            name=op.f("ck_station_observation_num_docks_available_nonnegative"),
        ),
        sa.CheckConstraint(
            "num_bikes_disabled >= 0",
            name=op.f("ck_station_observation_num_bikes_disabled_nonnegative"),
        ),
        sa.CheckConstraint(
            "num_docks_disabled >= 0",
            name=op.f("ck_station_observation_num_docks_disabled_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["station.id"],
            name=op.f("fk_station_observation_station_id_station"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingest_run_id"],
            ["ingest_run.id"],
            name=op.f("fk_station_observation_ingest_run_id_ingest_run"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["status_payload_id"],
            ["raw_payload.id"],
            name=op.f("fk_station_observation_status_payload_id_raw_payload"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_station_observation")),
        sa.UniqueConstraint(
            "station_id",
            "last_reported",
            name=op.f("uq_station_observation_station_id_last_reported"),
        ),
    )
    op.create_index(
        "ix_station_observation_last_reported",
        "station_observation",
        ["last_reported"],
    )


def downgrade() -> None:
    op.drop_index("ix_station_observation_last_reported", table_name="station_observation")
    op.drop_table("station_observation")
    op.drop_index(
        "uq_station_location_history_open_station",
        table_name="station_location_history",
    )
    op.drop_table("station_location_history")
    op.drop_table("station")
    op.drop_index("ix_raw_payload_live_collected_at", table_name="raw_payload")
    op.drop_index("ix_raw_payload_ingest_run_id_feed_name", table_name="raw_payload")
    op.drop_table("raw_payload")
    op.drop_table("ingest_run")
