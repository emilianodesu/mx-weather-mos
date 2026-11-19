"""Health check endpoint verifying Postgres, PostGIS, active model, and latest ingestion run."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session

from mxweather.api.schemas import HealthResponse, IngestionRunSummary
from mxweather.config import settings
from mxweather.db.models import RawIngestionRun
from mxweather.db.session import get_db

router = APIRouter(tags=["health"])


def read_active_model_version(artifacts_dir: Path | None = None) -> str | None:
    """Read active model version from artifacts/models/latest.json if present."""
    base_dir = artifacts_dir if artifacts_dir is not None else settings.mxweather_artifacts_dir
    latest_path = base_dir / "models" / "latest.json"
    if not latest_path.is_file():
        return None
    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("model_version") or payload.get("version")
    return str(version) if version else None


@router.get("/health", response_model=HealthResponse, summary="Service and database health check")
def health_check(db: Session = Depends(get_db)) -> HealthResponse:
    """Check PostgreSQL + PostGIS availability, active ML model version, and latest ingestion run."""
    database_ok = False
    postgis_version: str | None = None
    latest_run_summary: IngestionRunSummary | None = None

    try:
        pg_ver = db.execute(text("SELECT PostGIS_Version()")).scalar()
        database_ok = True
        postgis_version = str(pg_ver) if pg_ver is not None else None

        latest_run = db.execute(
            select(RawIngestionRun).order_by(desc(RawIngestionRun.started_at)).limit(1)
        ).scalar_one_or_none()

        if latest_run is not None:
            latest_run_summary = IngestionRunSummary(
                run_id=str(latest_run.run_id),
                source_provider=latest_run.source_provider,
                status=latest_run.status,
                started_at=latest_run.started_at,
                completed_at=latest_run.completed_at,
                records_loaded=int(latest_run.records_loaded),
                records_quarantined=int(latest_run.records_quarantined),
            )
    except Exception:
        database_ok = False
        postgis_version = None
        latest_run_summary = None

    active_model_version = read_active_model_version()
    status = "ok" if database_ok else "degraded"

    return HealthResponse(
        status=status,
        database_ok=database_ok,
        postgis_version=postgis_version,
        active_model_version=active_model_version,
        latest_ingestion_run=latest_run_summary,
        checked_at=datetime.now(UTC),
    )
