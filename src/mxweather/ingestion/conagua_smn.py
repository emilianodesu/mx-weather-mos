"""Resilient CONAGUA SMN GZip/JSON adapter with circuit-breaker degradation and offline replay."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from mxweather.config import settings
from mxweather.db.models import RawIngestionRun
from mxweather.ingestion.open_meteo import archive_raw_payload, create_http_client
from mxweather.quality.gate import PayloadIntegrityError, validate_raw_payload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmnFetchResult:
    """Result from a live or offline CONAGUA SMN ingestion attempt."""

    status: str  # "SUCCESS" | "DEGRADED_UPSTREAM"
    rows: list[dict[str, Any]]
    payload_sha256: str | None
    raw_path: Path | None
    error_detail: str | None = None
    run_id: uuid.UUID | None = None


def _parse_smn_int(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            # Handle integer strings or whole-number float strings like "90.0"
            if "." in stripped:
                dec = Decimal(stripped)
                if dec.is_finite() and dec == int(dec):
                    return int(dec)
                return value
            return int(stripped)
        except (ValueError, InvalidOperation):
            return value
    return value


def _parse_smn_decimal(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return value  # Let quality gate check finiteness if NaN/Inf
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            dec = Decimal(stripped)
            return dec if dec.is_finite() else value
        except InvalidOperation:
            return value
    return value


def _parse_smn_dloc(dloc: Any) -> date | Any:
    """Parse CONAGUA SMN ``dloc`` (e.g. ``'20241029T00'``) into a ``datetime.date``."""
    if isinstance(dloc, date) and not isinstance(dloc, datetime):
        return dloc
    if isinstance(dloc, str):
        stripped = dloc.strip()
        if len(stripped) >= 8 and stripped[:8].isdigit():
            try:
                return date(int(stripped[0:4]), int(stripped[4:6]), int(stripped[6:8]))
            except ValueError:
                return dloc
        try:
            return date.fromisoformat(stripped[:10])
        except ValueError:
            return dloc
    return dloc


def decode_smn_payload(raw_bytes: bytes) -> tuple[list[dict[str, Any]], str]:
    """Decode raw UTF-8 JSON or GZip-compressed JSON from CONAGUA SMN.

    Raises :class:`PayloadIntegrityError` if HTML, corrupt GZip, or non-list JSON is supplied.
    """
    parsed, sha256 = validate_raw_payload(raw_bytes, expect_gzip=False)
    if not isinstance(parsed, list):
        raise PayloadIntegrityError(
            f"CONAGUA SMN payload must be a JSON array, got {type(parsed).__name__}"
        )
    return parsed, sha256


def parse_smn_records(
    records: list[dict[str, Any]],
    *,
    issued_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Parse raw CONAGUA SMN records into ``fact_forecast_revision`` rows.

    Preserves ``raf`` as ``wind_gust_kmh`` and integer ``ndia`` (0..3) as ``lead_day``.
    When ``issued_at`` is omitted, derives ``issued_at`` as ``(valid_date - ndia) 00:00 UTC``
    so replay against historical fixtures satisfies zero-leakage and lead_day consistency.
    """
    normalized_issued_at: datetime | None = None
    if issued_at is not None:
        if issued_at.tzinfo is not None:
            normalized_issued_at = issued_at.astimezone(UTC)
        else:
            normalized_issued_at = issued_at  # Preserve naive datetime for S05 gate rejection

    rows: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue

        state_id = _parse_smn_int(rec.get("ides"))
        municipality_id = _parse_smn_int(rec.get("idmun"))
        valid_date = _parse_smn_dloc(rec.get("dloc"))
        lead_day = _parse_smn_int(rec.get("ndia"))

        if normalized_issued_at is not None:
            row_issued_at: Any = normalized_issued_at
        elif isinstance(valid_date, date) and isinstance(lead_day, int) and not isinstance(lead_day, bool):
            issue_day = valid_date - timedelta(days=lead_day)
            row_issued_at = datetime(
                issue_day.year, issue_day.month, issue_day.day, 0, 0, tzinfo=UTC
            )
        else:
            row_issued_at = None

        raw_desc = rec.get("desciel")
        sky_desc = raw_desc.strip() if isinstance(raw_desc, str) and raw_desc.strip() else None

        row: dict[str, Any] = {
            "source_provider": "conagua_smn",
            "state_id": state_id,
            "municipality_id": municipality_id,
            "issued_at": row_issued_at,
            "valid_date": valid_date,
            "lead_day": lead_day,
            "tmax_c": _parse_smn_decimal(rec.get("tmax")),
            "tmin_c": _parse_smn_decimal(rec.get("tmin")),
            "precip_mm": _parse_smn_decimal(rec.get("prec")),
            "precip_prob_pct": _parse_smn_int(rec.get("probprec")),
            "cloud_cover_pct": _parse_smn_decimal(rec.get("cc")),
            "wind_speed_kmh": _parse_smn_decimal(rec.get("velvien")),
            "wind_gust_kmh": _parse_smn_decimal(rec.get("raf")),
            "sky_desc": sky_desc,
        }

        if "lat" in rec and rec["lat"] is not None:
            row["lat"] = _parse_smn_decimal(rec["lat"])
        if "lon" in rec and rec["lon"] is not None:
            row["lon"] = _parse_smn_decimal(rec["lon"])

        rows.append(row)

    return rows


def load_smn_fixture(
    fixture_path: Path | None = None,
    *,
    issued_at: datetime | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Load and parse an offline CONAGUA SMN JSON/GZip fixture (default golden sample)."""
    path = Path(fixture_path) if fixture_path is not None else (
        settings.mxweather_fixtures_dir / "smn_golden_sample.json"
    )
    raw_bytes = path.read_bytes()
    records, sha256 = decode_smn_payload(raw_bytes)
    rows = parse_smn_records(records, issued_at=issued_at)
    return rows, sha256


def _record_degraded_run(
    session: Session | None,
    error_detail: str,
    *,
    payload_sha256: str | None = None,
) -> uuid.UUID | None:
    if session is None:
        return None
    now_utc = datetime.now(UTC)
    run = RawIngestionRun(
        run_id=uuid.uuid4(),
        source_provider="conagua_smn",
        started_at=now_utc,
        completed_at=now_utc,
        status="DEGRADED_UPSTREAM",
        payload_sha256=payload_sha256,
        records_read=0,
        records_loaded=0,
        records_quarantined=0,
        error_detail=error_detail[:1000],
    )
    session.add(run)
    session.flush()
    return run.run_id


def fetch_live_smn(
    session: Session | None = None,
    *,
    url: str | None = None,
    timeout_seconds: float | None = None,
    client: httpx.Client | None = None,
    data_dir: Path | None = None,
    issued_at: datetime | None = None,
) -> SmnFetchResult:
    """Attempt live CONAGUA SMN fetch with circuit-breaker graceful degradation.

    If the upstream government endpoint times out, returns a non-200 HTTP status,
    or responds with HTML / corrupt GZip instead of JSON, logs a ``DEGRADED_UPSTREAM``
    row in ``raw_ingestion_run`` (when ``session`` is provided) and returns gracefully
    without raising an unhandled exception.
    """
    target_url = url or settings.conagua_smn_url
    timeout = timeout_seconds if timeout_seconds is not None else settings.conagua_smn_timeout_seconds

    own_client = client is None
    http = client or create_http_client(timeout_seconds=timeout)
    try:
        try:
            response = http.get(target_url)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            detail = f"CONAGUA SMN upstream request failed: {type(exc).__name__}: {exc}"
            logger.warning(detail)
            run_id = _record_degraded_run(session, detail)
            return SmnFetchResult(
                status="DEGRADED_UPSTREAM",
                rows=[],
                payload_sha256=None,
                raw_path=None,
                error_detail=detail,
                run_id=run_id,
            )

        if response.status_code != 200:
            detail = (
                f"CONAGUA SMN upstream returned HTTP {response.status_code} "
                f"(content-type={response.headers.get('content-type', 'unknown')})"
            )
            logger.warning(detail)
            run_id = _record_degraded_run(session, detail)
            return SmnFetchResult(
                status="DEGRADED_UPSTREAM",
                rows=[],
                payload_sha256=None,
                raw_path=None,
                error_detail=detail,
                run_id=run_id,
            )

        raw_bytes = response.content
        try:
            records, sha256 = decode_smn_payload(raw_bytes)
        except PayloadIntegrityError as exc:
            detail = f"CONAGUA SMN upstream returned non-JSON/corrupt payload: {exc}"
            logger.warning(detail)
            run_id = _record_degraded_run(session, detail)
            return SmnFetchResult(
                status="DEGRADED_UPSTREAM",
                rows=[],
                payload_sha256=None,
                raw_path=None,
                error_detail=detail,
                run_id=run_id,
            )

        ext = "json.gz" if raw_bytes[:2] == b"\x1f\x8b" else "json"
        _, raw_path = archive_raw_payload(
            raw_bytes, "conagua_smn", data_dir=data_dir, extension=ext
        )
        rows = parse_smn_records(records, issued_at=issued_at)
        return SmnFetchResult(
            status="SUCCESS",
            rows=rows,
            payload_sha256=sha256,
            raw_path=raw_path,
            error_detail=None,
            run_id=None,
        )
    finally:
        if own_client:
            http.close()
