"""Strict Pydantic v2 request/response schemas for mx-weather-intel API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AlertLevel = Literal["NORMAL", "ADVISORY", "WARNING_P0"]
ActiveAlertLevel = Literal["ADVISORY", "WARNING_P0"]


class IngestionRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    source_provider: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    records_loaded: int = 0
    records_quarantined: int = 0


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database_ok: bool
    postgis_version: str | None = None
    active_model_version: str | None = None
    latest_ingestion_run: IngestionRunSummary | None = None
    checked_at: datetime


class StateItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    state_id: int
    state_name: str


class StateListResponse(BaseModel):
    count: int
    items: list[StateItem]


class MunicipalityItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    state_id: int
    municipality_id: int
    municipality_name: str
    state_name: str
    lat: float
    lon: float
    elevation_m: float | None = None
    utc_offset_hours: int
    is_ml_cohort: bool
    distance_km: float | None = None


class MunicipalityListResponse(BaseModel):
    count: int
    items: list[MunicipalityItem]


class ForecastDayItem(BaseModel):
    valid_date: date
    lead_day: int
    issued_at: datetime
    source_provider: str
    raw_tmax_c: float
    raw_tmin_c: float
    precip_mm: float | None = None
    raw_precip_prob_pct: int | None = None
    wind_speed_kmh: float | None = None
    wind_gust_kmh: float | None = None
    cloud_cover_pct: float | None = None
    sky_desc: str | None = None
    model_version: str | None = None
    pred_tmax_p10: float | None = None
    pred_tmax_p50: float | None = None
    pred_tmax_p90: float | None = None
    pred_tmin_p10: float | None = None
    pred_tmin_p50: float | None = None
    pred_tmin_p90: float | None = None
    calibrated_rain_prob: float | None = Field(default=None, ge=0.0, le=1.0)
    ml_bias_delta_c: float | None = None
    alert_level: AlertLevel


class MunicipalityForecastResponse(BaseModel):
    municipality: MunicipalityItem
    days: list[ForecastDayItem]
    scope_note: str


class MapForecastPoint(BaseModel):
    state_id: int
    municipality_id: int
    municipality_name: str
    state_name: str
    lat: float
    lon: float
    elevation_m: float | None = None
    valid_date: date
    lead_day: int
    raw_tmax_c: float
    pred_tmax_p50: float | None = None
    raw_tmin_c: float
    pred_tmin_p50: float | None = None
    precip_mm: float | None = None
    calibrated_rain_prob: float | None = Field(default=None, ge=0.0, le=1.0)
    ml_bias_delta_c: float | None = None
    alert_level: AlertLevel


class MapForecastResponse(BaseModel):
    count: int
    items: list[MapForecastPoint]
    scope_note: str


class AlertItem(BaseModel):
    state_id: int
    municipality_id: int
    municipality_name: str
    state_name: str
    valid_date: date
    lead_day: int
    issued_at: datetime
    alert_level: ActiveAlertLevel
    raw_tmax_c: float
    pred_tmax_p50: float | None = None
    raw_tmin_c: float
    pred_tmin_p50: float | None = None
    precip_mm: float | None = None
    wind_gust_kmh: float | None = None
    calibrated_rain_prob: float | None = Field(default=None, ge=0.0, le=1.0)
    trigger_reasons: list[str] = Field(default_factory=list)


class AlertsResponse(BaseModel):
    count: int
    warning_p0_count: int
    advisory_count: int
    scope_note: str
    items: list[AlertItem]


class TrendPoint(BaseModel):
    date: date
    obs_tmax_c: float | None = None
    obs_tmin_c: float | None = None
    obs_precip_mm: float | None = None
    raw_tmax_c: float | None = None
    raw_tmin_c: float | None = None
    ml_tmax_p50: float | None = None
    ml_tmin_p50: float | None = None


class TrendsResponse(BaseModel):
    municipality: MunicipalityItem
    start_date: date
    end_date: date
    count: int
    series: list[TrendPoint]


class EvaluationMetricRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    split_name: str
    segment_key: str
    metric_name: str
    raw_baseline_val: float | None = None
    ml_model_val: float
    persistence_val: float | None = None
    climatology_val: float | None = None
    sample_count: int
    evaluated_at: datetime | None = None


class CalibrationBin(BaseModel):
    bin_index: int | None = None
    bin_lower: float | None = None
    bin_upper: float | None = None
    mean_predicted_prob: float = Field(ge=0.0, le=1.0)
    fraction_of_positives: float = Field(ge=0.0, le=1.0)
    count: int = Field(ge=0)


class MlSkillMetricsGrouped(BaseModel):
    overall: list[EvaluationMetricRow] = Field(default_factory=list)
    by_elevation_band: list[EvaluationMetricRow] = Field(default_factory=list)
    by_season: list[EvaluationMetricRow] = Field(default_factory=list)
    other: list[EvaluationMetricRow] = Field(default_factory=list)


class MlSkillResponse(BaseModel):
    model_version: str | None = None
    metrics: MlSkillMetricsGrouped
    calibration_curve: list[CalibrationBin] = Field(default_factory=list)
