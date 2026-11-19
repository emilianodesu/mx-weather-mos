"""SQLAlchemy 2.0 + PostGIS ORM models for mx-weather-intel."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DimState(Base):
    __tablename__ = "dim_state"

    state_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    state_name: Mapped[str] = mapped_column(Text, nullable=False)


class DimMunicipality(Base):
    __tablename__ = "dim_municipality"

    state_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("dim_state.state_id", ondelete="CASCADE"), primary_key=True
    )
    municipality_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    municipality_name: Mapped[str] = mapped_column(Text, nullable=False)
    lat: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    lon: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    elevation_m: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    utc_offset_hours: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=-6)
    is_ml_cohort: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    geom: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint("lat >= 14.0 AND lat <= 33.0", name="chk_mun_lat_mx"),
        CheckConstraint("lon >= -119.0 AND lon <= -86.0", name="chk_mun_lon_mx"),
        Index("ix_dim_municipality_cohort", "is_ml_cohort"),
    )


class RawIngestionRun(Base):
    __tablename__ = "raw_ingestion_run"

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_provider: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    payload_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    records_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_loaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_quarantined: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class FactForecastRevision(Base):
    __tablename__ = "fact_forecast_revision"

    source_provider: Mapped[str] = mapped_column(Text, primary_key=True)
    state_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    municipality_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    valid_date: Mapped[date] = mapped_column(Date, primary_key=True)
    lead_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    tmax_c: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    tmin_c: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    precip_mm: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    precip_prob_pct: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    wind_speed_kmh: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    wind_gust_kmh: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    cloud_cover_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    sky_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("raw_ingestion_run.run_id"), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["state_id", "municipality_id"],
            ["dim_municipality.state_id", "dim_municipality.municipality_id"],
            name="fk_forecast_municipality",
            ondelete="CASCADE",
        ),
        CheckConstraint("tmax_c >= tmin_c", name="chk_fcst_tmax_ge_tmin"),
        CheckConstraint("tmax_c >= -25.0 AND tmax_c <= 55.0", name="chk_fcst_tmax_range"),
        CheckConstraint("tmin_c >= -25.0 AND tmin_c <= 55.0", name="chk_fcst_tmin_range"),
        CheckConstraint("precip_mm IS NULL OR (precip_mm >= 0 AND precip_mm <= 600)", name="chk_fcst_precip_range"),
        CheckConstraint("precip_prob_pct IS NULL OR (precip_prob_pct >= 0 AND precip_prob_pct <= 100)", name="chk_fcst_prob_range"),
        CheckConstraint("lead_day >= 0 AND lead_day <= 7", name="chk_fcst_lead_day"),
        CheckConstraint("valid_date >= (issued_at AT TIME ZONE 'UTC')::date", name="chk_fcst_no_leakage"),
        Index("ix_fcst_mun_valid_issued", "state_id", "municipality_id", "valid_date", "issued_at"),
    )


class FactObservedDaily(Base):
    __tablename__ = "fact_observed_daily"

    source_provider: Mapped[str] = mapped_column(Text, primary_key=True)
    state_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    municipality_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    obs_date: Mapped[date] = mapped_column(Date, primary_key=True)
    tmax_c: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    tmin_c: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    precip_mm: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    wind_speed_kmh: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    wind_gust_kmh: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("raw_ingestion_run.run_id"), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["state_id", "municipality_id"],
            ["dim_municipality.state_id", "dim_municipality.municipality_id"],
            name="fk_observed_municipality",
            ondelete="CASCADE",
        ),
        CheckConstraint("tmax_c >= tmin_c", name="chk_obs_tmax_ge_tmin"),
        CheckConstraint("precip_mm IS NULL OR precip_mm >= 0", name="chk_obs_precip_nonneg"),
        Index("ix_obs_mun_date", "state_id", "municipality_id", "obs_date"),
    )


class FactMlForecast(Base):
    __tablename__ = "fact_ml_forecast"

    model_version: Mapped[str] = mapped_column(Text, primary_key=True)
    state_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    municipality_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    valid_date: Mapped[date] = mapped_column(Date, primary_key=True)
    lead_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    raw_tmax_c: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    pred_tmax_p10: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    pred_tmax_p50: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    pred_tmax_p90: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    raw_tmin_c: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    pred_tmin_p10: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    pred_tmin_p50: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    pred_tmin_p90: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    raw_precip_prob_pct: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    calibrated_rain_prob: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    alert_level: Mapped[str] = mapped_column(Text, nullable=False)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["state_id", "municipality_id"],
            ["dim_municipality.state_id", "dim_municipality.municipality_id"],
            name="fk_ml_forecast_municipality",
            ondelete="CASCADE",
        ),
        CheckConstraint("pred_tmax_p10 <= pred_tmax_p50 AND pred_tmax_p50 <= pred_tmax_p90", name="chk_ml_tmax_quantiles"),
        CheckConstraint("pred_tmin_p10 <= pred_tmin_p50 AND pred_tmin_p50 <= pred_tmin_p90", name="chk_ml_tmin_quantiles"),
        CheckConstraint("calibrated_rain_prob >= 0.0 AND calibrated_rain_prob <= 1.0", name="chk_ml_rain_prob"),
        CheckConstraint("alert_level IN ('NORMAL', 'ADVISORY', 'WARNING_P0')", name="chk_ml_alert_level"),
        Index("ix_ml_fcst_mun_valid", "state_id", "municipality_id", "valid_date"),
    )


class MlModelEvaluation(Base):
    __tablename__ = "ml_model_evaluation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    split_name: Mapped[str] = mapped_column(Text, nullable=False)
    segment_key: Mapped[str] = mapped_column(Text, nullable=False)
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    raw_baseline_val: Mapped[float | None] = mapped_column(Double, nullable=True)
    ml_model_val: Mapped[float] = mapped_column(Double, nullable=False)
    persistence_val: Mapped[float | None] = mapped_column(Double, nullable=True)
    climatology_val: Mapped[float | None] = mapped_column(Double, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_ml_eval_version_split", "model_version", "split_name", "segment_key"),
    )
