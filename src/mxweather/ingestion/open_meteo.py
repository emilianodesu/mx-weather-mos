"""Open-Meteo HTTP client, SHA-256 raw archiving, parsers, and PostgreSQL upsert helpers."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from mxweather.config import settings
from mxweather.db.models import (
    DimMunicipality,
    FactForecastRevision,
    FactObservedDaily,
    RawIngestionRun,
)
from mxweather.quality.gate import validate_raw_payload

logger = logging.getLogger(__name__)

ERA5_DAILY_VARS = (
    "temperature_2m_max,temperature_2m_min,precipitation_sum,"
    "wind_speed_10m_max,wind_gusts_10m_max"
)
FORECAST_DAILY_VARS = (
    "temperature_2m_max,temperature_2m_min,precipitation_sum,"
    "precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max"
)

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

_OBSERVED_DB_COLS = (
    "source_provider",
    "state_id",
    "municipality_id",
    "obs_date",
    "tmax_c",
    "tmin_c",
    "precip_mm",
    "wind_speed_kmh",
    "wind_gust_kmh",
    "run_id",
)

_FORECAST_DB_COLS = (
    "source_provider",
    "state_id",
    "municipality_id",
    "issued_at",
    "valid_date",
    "lead_day",
    "tmax_c",
    "tmin_c",
    "precip_mm",
    "precip_prob_pct",
    "wind_speed_kmh",
    "wind_gust_kmh",
    "cloud_cover_pct",
    "sky_desc",
    "run_id",
)


@dataclass(frozen=True)
class OpenMeteoFetchResult:
    """Structured result from an Open-Meteo fetch and parse operation."""

    source_provider: str
    rows: list[dict[str, Any]]
    elevation_m: Decimal | None
    payload_sha256: str
    raw_path: Path | None


def archive_raw_payload(
    raw_bytes: bytes,
    source_provider: str,
    *,
    data_dir: Path | None = None,
    extension: str = "json",
) -> tuple[str, Path]:
    """Persist raw bytes under ``<data_dir>/raw/<source_provider>/<sha256>.<ext>``.

    Content-addressed writes are idempotent: identical bytes map to the same path.
    """
    import hashlib

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    base_dir = Path(data_dir) if data_dir is not None else settings.mxweather_data_dir
    target_dir = base_dir / "raw" / source_provider
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{sha256}.{extension}"
    if not target_path.exists():
        target_path.write_bytes(raw_bytes)
    return sha256, target_path


def create_http_client(timeout_seconds: float = 30.0) -> httpx.Client:
    """Create an httpx client with strict TLS verification and HTTP/2 disabled for compatibility."""
    return httpx.Client(
        verify=True,
        timeout=httpx.Timeout(timeout_seconds, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": "mx-weather-intel/0.1.0 (+local-portfolio-pipeline)"},
    )


def _get_with_backoff(
    url: str,
    params: dict[str, Any],
    *,
    client: httpx.Client | None = None,
    max_retries: int = 3,
    base_delay_seconds: float = 0.5,
    max_delay_seconds: float = 8.0,
) -> bytes:
    """Execute a GET request with bounded exponential backoff on transient failures."""
    own_client = client is None
    http = client or create_http_client()
    try:
        attempt = 0
        while True:
            try:
                response = http.get(url, params=params)
                if response.status_code == 200:
                    return response.content
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                    delay = min(base_delay_seconds * (2**attempt), max_delay_seconds)
                    logger.warning(
                        "Open-Meteo HTTP %s on %s (attempt %d/%d); retrying in %.2fs",
                        response.status_code,
                        url,
                        attempt + 1,
                        max_retries,
                        delay,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                response.raise_for_status()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < max_retries:
                    delay = min(base_delay_seconds * (2**attempt), max_delay_seconds)
                    logger.warning(
                        "Open-Meteo transport error on %s (attempt %d/%d): %s; retrying in %.2fs",
                        url,
                        attempt + 1,
                        max_retries,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                raise
    finally:
        if own_client:
            http.close()


def _to_optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value  # type: ignore[return-value]  # Preserved for quality gate S07
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    return value  # Preserved without silent coercion


def _to_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value  # type: ignore[return-value]
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value  # Preserved for quality gate


def _extract_elevation(payload: dict[str, Any]) -> Decimal | None:
    raw = payload.get("elevation")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        dec = Decimal(str(raw))
        return dec if dec.is_finite() else None
    except Exception:
        return None


def parse_era5_payload(
    payload: dict[str, Any],
    *,
    state_id: int,
    municipality_id: int,
    lat: float | Decimal | None = None,
    lon: float | Decimal | None = None,
) -> tuple[list[dict[str, Any]], Decimal | None]:
    """Parse an Open-Meteo ERA5 archive JSON payload into ``fact_observed_daily`` rows."""
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    tmax_list = daily.get("temperature_2m_max") or []
    tmin_list = daily.get("temperature_2m_min") or []
    precip_list = daily.get("precipitation_sum") or []
    wind_list = daily.get("wind_speed_10m_max") or []
    gust_list = daily.get("wind_gusts_10m_max") or []

    resolved_lat = lat if lat is not None else payload.get("latitude")
    resolved_lon = lon if lon is not None else payload.get("longitude")
    elevation_m = _extract_elevation(payload)

    rows: list[dict[str, Any]] = []
    for idx, date_str in enumerate(times):
        obs_date = date.fromisoformat(date_str) if isinstance(date_str, str) else date_str
        row: dict[str, Any] = {
            "source_provider": "open_meteo_era5",
            "state_id": int(state_id),
            "municipality_id": int(municipality_id),
            "obs_date": obs_date,
            "tmax_c": _to_optional_decimal(tmax_list[idx]) if idx < len(tmax_list) else None,
            "tmin_c": _to_optional_decimal(tmin_list[idx]) if idx < len(tmin_list) else None,
            "precip_mm": _to_optional_decimal(precip_list[idx]) if idx < len(precip_list) else None,
            "wind_speed_kmh": (
                _to_optional_decimal(wind_list[idx]) if idx < len(wind_list) else None
            ),
            "wind_gust_kmh": (
                _to_optional_decimal(gust_list[idx]) if idx < len(gust_list) else None
            ),
        }
        if resolved_lat is not None:
            row["lat"] = resolved_lat
        if resolved_lon is not None:
            row["lon"] = resolved_lon
        rows.append(row)

    return rows, elevation_m


def parse_historical_forecast_payload(
    payload: dict[str, Any],
    *,
    state_id: int,
    municipality_id: int,
    lat: float | Decimal | None = None,
    lon: float | Decimal | None = None,
) -> tuple[list[dict[str, Any]], Decimal | None]:
    """Parse an Open-Meteo Historical Forecast payload into ``fact_forecast_revision`` rows.

    Sets ``source_provider="open_meteo_historical_forecast"``, ``lead_day=0``, and
    ``issued_at = valid_date 00:00 UTC``.
    """
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    tmax_list = daily.get("temperature_2m_max") or []
    tmin_list = daily.get("temperature_2m_min") or []
    precip_list = daily.get("precipitation_sum") or []
    prob_list = daily.get("precipitation_probability_max") or []
    wind_list = daily.get("wind_speed_10m_max") or []
    gust_list = daily.get("wind_gusts_10m_max") or []
    cloud_list = daily.get("cloud_cover_mean") or []

    resolved_lat = lat if lat is not None else payload.get("latitude")
    resolved_lon = lon if lon is not None else payload.get("longitude")
    elevation_m = _extract_elevation(payload)

    rows: list[dict[str, Any]] = []
    for idx, date_str in enumerate(times):
        valid_date = date.fromisoformat(date_str) if isinstance(date_str, str) else date_str
        issued_at = datetime(
            valid_date.year, valid_date.month, valid_date.day, 0, 0, tzinfo=UTC
        )
        row: dict[str, Any] = {
            "source_provider": "open_meteo_historical_forecast",
            "state_id": int(state_id),
            "municipality_id": int(municipality_id),
            "issued_at": issued_at,
            "valid_date": valid_date,
            "lead_day": 0,
            "tmax_c": _to_optional_decimal(tmax_list[idx]) if idx < len(tmax_list) else None,
            "tmin_c": _to_optional_decimal(tmin_list[idx]) if idx < len(tmin_list) else None,
            "precip_mm": _to_optional_decimal(precip_list[idx]) if idx < len(precip_list) else None,
            "precip_prob_pct": _to_optional_int(prob_list[idx]) if idx < len(prob_list) else None,
            "wind_speed_kmh": (
                _to_optional_decimal(wind_list[idx]) if idx < len(wind_list) else None
            ),
            "wind_gust_kmh": (
                _to_optional_decimal(gust_list[idx]) if idx < len(gust_list) else None
            ),
            "cloud_cover_pct": (
                _to_optional_decimal(cloud_list[idx]) if idx < len(cloud_list) else None
            ),
            "sky_desc": None,
        }
        if resolved_lat is not None:
            row["lat"] = resolved_lat
        if resolved_lon is not None:
            row["lon"] = resolved_lon
        rows.append(row)

    return rows, elevation_m


def parse_live_forecast_payload(
    payload: dict[str, Any],
    *,
    state_id: int,
    municipality_id: int,
    issued_at: datetime | None = None,
    lat: float | Decimal | None = None,
    lon: float | Decimal | None = None,
) -> tuple[list[dict[str, Any]], Decimal | None]:
    """Parse an Open-Meteo Live Forecast payload into ``fact_forecast_revision`` rows.

    Ensures ``issued_at`` is UTC-aligned to the hour and ``valid_date >= issued_at.date()``.
    """
    if issued_at is None:
        issued_utc = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    else:
        issued_utc = issued_at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)

    issued_date = issued_utc.date()
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    tmax_list = daily.get("temperature_2m_max") or []
    tmin_list = daily.get("temperature_2m_min") or []
    precip_list = daily.get("precipitation_sum") or []
    prob_list = daily.get("precipitation_probability_max") or []
    wind_list = daily.get("wind_speed_10m_max") or []
    gust_list = daily.get("wind_gusts_10m_max") or []
    cloud_list = daily.get("cloud_cover_mean") or []

    resolved_lat = lat if lat is not None else payload.get("latitude")
    resolved_lon = lon if lon is not None else payload.get("longitude")
    elevation_m = _extract_elevation(payload)

    rows: list[dict[str, Any]] = []
    for idx, date_str in enumerate(times):
        valid_date = date.fromisoformat(date_str) if isinstance(date_str, str) else date_str
        if valid_date < issued_date:
            continue
        lead_day = (valid_date - issued_date).days
        row: dict[str, Any] = {
            "source_provider": "open_meteo_forecast",
            "state_id": int(state_id),
            "municipality_id": int(municipality_id),
            "issued_at": issued_utc,
            "valid_date": valid_date,
            "lead_day": lead_day,
            "tmax_c": _to_optional_decimal(tmax_list[idx]) if idx < len(tmax_list) else None,
            "tmin_c": _to_optional_decimal(tmin_list[idx]) if idx < len(tmin_list) else None,
            "precip_mm": _to_optional_decimal(precip_list[idx]) if idx < len(precip_list) else None,
            "precip_prob_pct": _to_optional_int(prob_list[idx]) if idx < len(prob_list) else None,
            "wind_speed_kmh": (
                _to_optional_decimal(wind_list[idx]) if idx < len(wind_list) else None
            ),
            "wind_gust_kmh": (
                _to_optional_decimal(gust_list[idx]) if idx < len(gust_list) else None
            ),
            "cloud_cover_pct": (
                _to_optional_decimal(cloud_list[idx]) if idx < len(cloud_list) else None
            ),
            "sky_desc": None,
        }
        if resolved_lat is not None:
            row["lat"] = resolved_lat
        if resolved_lon is not None:
            row["lon"] = resolved_lon
        rows.append(row)

    return rows, elevation_m


def fetch_era5_archive(
    lat: float | Decimal,
    lon: float | Decimal,
    start_date: date | str,
    end_date: date | str,
    *,
    state_id: int = 0,
    municipality_id: int = 0,
    client: httpx.Client | None = None,
    data_dir: Path | None = None,
    max_retries: int = 3,
) -> OpenMeteoFetchResult:
    """Fetch ERA5-Land daily archive observations and archive the raw JSON by SHA-256."""
    start_str = start_date.isoformat() if isinstance(start_date, date) else start_date
    end_str = end_date.isoformat() if isinstance(end_date, date) else end_date
    params = {
        "latitude": float(lat),
        "longitude": float(lon),
        "start_date": start_str,
        "end_date": end_str,
        "daily": ERA5_DAILY_VARS,
        "timezone": "UTC",
    }
    raw_bytes = _get_with_backoff(
        settings.open_meteo_archive_url,
        params,
        client=client,
        max_retries=max_retries,
    )
    payload, sha256 = validate_raw_payload(raw_bytes, expect_gzip=False)
    _, raw_path = archive_raw_payload(
        raw_bytes, "open_meteo_era5", data_dir=data_dir, extension="json"
    )
    rows, elevation_m = parse_era5_payload(
        payload,
        state_id=state_id,
        municipality_id=municipality_id,
        lat=lat,
        lon=lon,
    )
    return OpenMeteoFetchResult(
        source_provider="open_meteo_era5",
        rows=rows,
        elevation_m=elevation_m,
        payload_sha256=sha256,
        raw_path=raw_path,
    )


def fetch_historical_forecast(
    lat: float | Decimal,
    lon: float | Decimal,
    start_date: date | str,
    end_date: date | str,
    *,
    state_id: int = 0,
    municipality_id: int = 0,
    client: httpx.Client | None = None,
    data_dir: Path | None = None,
    max_retries: int = 3,
) -> OpenMeteoFetchResult:
    """Fetch historical NWP daily forecasts and archive the raw JSON by SHA-256."""
    start_str = start_date.isoformat() if isinstance(start_date, date) else start_date
    end_str = end_date.isoformat() if isinstance(end_date, date) else end_date
    params = {
        "latitude": float(lat),
        "longitude": float(lon),
        "start_date": start_str,
        "end_date": end_str,
        "daily": FORECAST_DAILY_VARS,
        "timezone": "UTC",
    }
    raw_bytes = _get_with_backoff(
        settings.open_meteo_historical_forecast_url,
        params,
        client=client,
        max_retries=max_retries,
    )
    payload, sha256 = validate_raw_payload(raw_bytes, expect_gzip=False)
    _, raw_path = archive_raw_payload(
        raw_bytes, "open_meteo_historical_forecast", data_dir=data_dir, extension="json"
    )
    rows, elevation_m = parse_historical_forecast_payload(
        payload,
        state_id=state_id,
        municipality_id=municipality_id,
        lat=lat,
        lon=lon,
    )
    return OpenMeteoFetchResult(
        source_provider="open_meteo_historical_forecast",
        rows=rows,
        elevation_m=elevation_m,
        payload_sha256=sha256,
        raw_path=raw_path,
    )


def fetch_live_forecast(
    lat: float | Decimal,
    lon: float | Decimal,
    days: int = 4,
    *,
    state_id: int = 0,
    municipality_id: int = 0,
    issued_at: datetime | None = None,
    client: httpx.Client | None = None,
    data_dir: Path | None = None,
    max_retries: int = 3,
) -> OpenMeteoFetchResult:
    """Fetch operational multi-day forecast (default 4 days, lead_day 0..3)."""
    params = {
        "latitude": float(lat),
        "longitude": float(lon),
        "forecast_days": int(days),
        "daily": FORECAST_DAILY_VARS,
        "timezone": "UTC",
    }
    raw_bytes = _get_with_backoff(
        settings.open_meteo_live_forecast_url,
        params,
        client=client,
        max_retries=max_retries,
    )
    payload, sha256 = validate_raw_payload(raw_bytes, expect_gzip=False)
    _, raw_path = archive_raw_payload(
        raw_bytes, "open_meteo_forecast", data_dir=data_dir, extension="json"
    )
    rows, elevation_m = parse_live_forecast_payload(
        payload,
        state_id=state_id,
        municipality_id=municipality_id,
        issued_at=issued_at,
        lat=lat,
        lon=lon,
    )
    return OpenMeteoFetchResult(
        source_provider="open_meteo_forecast",
        rows=rows,
        elevation_m=elevation_m,
        payload_sha256=sha256,
        raw_path=raw_path,
    )


def start_ingestion_run(
    session: Session,
    source_provider: str,
    *,
    payload_sha256: str | None = None,
) -> RawIngestionRun:
    """Insert an initial RUNNING audit row into ``raw_ingestion_run``."""
    run = RawIngestionRun(
        run_id=uuid.uuid4(),
        source_provider=source_provider,
        started_at=datetime.now(UTC),
        status="RUNNING",
        payload_sha256=payload_sha256,
        records_read=0,
        records_loaded=0,
        records_quarantined=0,
    )
    session.add(run)
    session.flush()
    return run


def finish_ingestion_run(
    session: Session,
    run: RawIngestionRun,
    *,
    status: str,
    records_read: int,
    records_loaded: int,
    records_quarantined: int,
    payload_sha256: str | None = None,
    error_detail: str | None = None,
) -> RawIngestionRun:
    """Finalize an audit row in ``raw_ingestion_run``."""
    run.completed_at = datetime.now(UTC)
    run.status = status
    if payload_sha256 is not None:
        run.payload_sha256 = payload_sha256
    run.records_read = records_read
    run.records_loaded = records_loaded
    run.records_quarantined = records_quarantined
    run.error_detail = error_detail
    session.flush()
    return run


def update_municipality_elevation(
    session: Session,
    state_id: int,
    municipality_id: int,
    elevation_m: Decimal | None,
) -> None:
    """Update ``dim_municipality.elevation_m`` when elevation is available."""
    if elevation_m is None:
        return
    stmt = (
        update(DimMunicipality)
        .where(
            DimMunicipality.state_id == state_id,
            DimMunicipality.municipality_id == municipality_id,
        )
        .values(elevation_m=elevation_m)
    )
    session.execute(stmt)


def _strip_columns(
    rows: list[dict[str, Any]],
    allowed_cols: tuple[str, ...],
    run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        item = {col: row.get(col) for col in allowed_cols if col != "run_id"}
        item["run_id"] = run_id
        cleaned.append(item)
    return cleaned


def upsert_observed_daily(
    session: Session,
    rows: list[dict[str, Any]],
    run_id: uuid.UUID,
    *,
    batch_size: int = 500,
) -> int:
    """Idempotently upsert rows into ``fact_observed_daily`` (zero duplicates on replay)."""
    if not rows:
        return 0
    payload = _strip_columns(rows, _OBSERVED_DB_COLS, run_id)
    total = 0
    for start in range(0, len(payload), batch_size):
        chunk = payload[start : start + batch_size]
        stmt = pg_insert(FactObservedDaily).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_provider", "state_id", "municipality_id", "obs_date"],
            set_={
                "tmax_c": stmt.excluded.tmax_c,
                "tmin_c": stmt.excluded.tmin_c,
                "precip_mm": stmt.excluded.precip_mm,
                "wind_speed_kmh": stmt.excluded.wind_speed_kmh,
                "wind_gust_kmh": stmt.excluded.wind_gust_kmh,
                "run_id": stmt.excluded.run_id,
            },
        )
        session.execute(stmt)
        total += len(chunk)
    return total


def upsert_forecast_revisions(
    session: Session,
    rows: list[dict[str, Any]],
    run_id: uuid.UUID,
    *,
    batch_size: int = 500,
) -> int:
    """Idempotently upsert rows into ``fact_forecast_revision`` (zero duplicates on replay)."""
    if not rows:
        return 0
    payload = _strip_columns(rows, _FORECAST_DB_COLS, run_id)
    total = 0
    for start in range(0, len(payload), batch_size):
        chunk = payload[start : start + batch_size]
        stmt = pg_insert(FactForecastRevision).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                "source_provider",
                "state_id",
                "municipality_id",
                "issued_at",
                "valid_date",
            ],
            set_={
                "lead_day": stmt.excluded.lead_day,
                "tmax_c": stmt.excluded.tmax_c,
                "tmin_c": stmt.excluded.tmin_c,
                "precip_mm": stmt.excluded.precip_mm,
                "precip_prob_pct": stmt.excluded.precip_prob_pct,
                "wind_speed_kmh": stmt.excluded.wind_speed_kmh,
                "wind_gust_kmh": stmt.excluded.wind_gust_kmh,
                "cloud_cover_pct": stmt.excluded.cloud_cover_pct,
                "sky_desc": stmt.excluded.sky_desc,
                "run_id": stmt.excluded.run_id,
            },
        )
        session.execute(stmt)
        total += len(chunk)
    return total
