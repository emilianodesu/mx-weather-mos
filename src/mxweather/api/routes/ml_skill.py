"""ML forecast skill and rain-calibration diagnostics endpoint."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import asc, desc, select
from sqlalchemy.orm import Session

from mxweather.api.routes.health import read_active_model_version
from mxweather.api.schemas import (
    CalibrationBin,
    EvaluationMetricRow,
    MlSkillMetricsGrouped,
    MlSkillResponse,
)
from mxweather.config import settings
from mxweather.db.models import MlModelEvaluation
from mxweather.db.session import get_db

router = APIRouter(prefix="/v1/ml", tags=["ml"])


def _classify_segment_group(segment_key: str) -> str:
    key = segment_key.strip().lower()
    if key == "overall" or key == "all":
        return "overall"
    if (
        key.startswith("elevation")
        or "coastal" in key
        or "midland" in key
        or "highland" in key
    ):
        return "by_elevation_band"
    if key.startswith("season") or "dry" in key or "rainy" in key:
        return "by_season"
    return "other"


def load_calibration_curve(
    model_version: str | None, artifacts_dir: Path | None = None
) -> list[CalibrationBin]:
    """Load calibration_curve.json for the given model version, or return empty list."""
    if not model_version:
        return []
    base_dir = artifacts_dir if artifacts_dir is not None else settings.mxweather_artifacts_dir
    curve_path = base_dir / "models" / model_version / "calibration_curve.json"
    if not curve_path.is_file():
        return []
    try:
        raw = json.loads(curve_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    if isinstance(raw, dict) and isinstance(raw.get("calibrated"), dict):
        cal = raw["calibrated"]
        edges = cal.get("bin_edges") or []
        counts = cal.get("count") or []
        fracs = cal.get("fraction_positive") or []
        means = cal.get("mean_pred_prob") or []
        bins_out: list[CalibrationBin] = []
        for idx, (cnt, frac, mean_p) in enumerate(zip(counts, fracs, means, strict=False)):
            if cnt is None or int(cnt) <= 0 or frac is None or mean_p is None:
                continue
            low = float(edges[idx]) if idx < len(edges) else None
            high = float(edges[idx + 1]) if idx + 1 < len(edges) else None
            bins_out.append(
                CalibrationBin(
                    bin_index=idx,
                    bin_lower=low,
                    bin_upper=high,
                    mean_predicted_prob=float(mean_p),
                    fraction_of_positives=float(frac),
                    count=int(cnt),
                )
            )
        return bins_out

    if isinstance(raw, dict):
        items = raw.get("calibration_curve") or raw.get("bins") or []
    elif isinstance(raw, list):
        items = raw
    else:
        return []

    bins: list[CalibrationBin] = []
    for idx, entry in enumerate(items):
        if not isinstance(entry, dict):
            continue
        try:
            bins.append(
                CalibrationBin(
                    bin_index=entry.get("bin_index", idx),
                    bin_lower=entry.get("bin_lower"),
                    bin_upper=entry.get("bin_upper"),
                    mean_predicted_prob=float(entry["mean_predicted_prob"]),
                    fraction_of_positives=float(entry["fraction_of_positives"]),
                    count=int(entry.get("count", 0)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return bins


@router.get(
    "/skill",
    response_model=MlSkillResponse,
    summary="ML model skill metrics vs. baselines and rain calibration curve",
)
def get_ml_skill(
    model_version: str | None = Query(
        default=None, description="Optional explicit model version; defaults to active model"
    ),
    db: Session = Depends(get_db),
) -> MlSkillResponse:
    """Return evaluation metrics grouped by overall, elevation band, and season + calibration curve."""
    resolved_version = model_version or read_active_model_version()

    if resolved_version is None:
        latest_eval = db.execute(
            select(MlModelEvaluation.model_version)
            .order_by(desc(MlModelEvaluation.evaluated_at))
            .limit(1)
        ).scalar_one_or_none()
        resolved_version = str(latest_eval) if latest_eval is not None else None

    grouped = MlSkillMetricsGrouped()
    if resolved_version is not None:
        rows = db.execute(
            select(MlModelEvaluation)
            .where(MlModelEvaluation.model_version == resolved_version)
            .order_by(
                asc(MlModelEvaluation.split_name),
                asc(MlModelEvaluation.segment_key),
                asc(MlModelEvaluation.metric_name),
            )
        ).scalars().all()

        for r in rows:
            metric_row = EvaluationMetricRow(
                split_name=r.split_name,
                segment_key=r.segment_key,
                metric_name=r.metric_name,
                raw_baseline_val=float(r.raw_baseline_val) if r.raw_baseline_val is not None else None,
                ml_model_val=float(r.ml_model_val),
                persistence_val=float(r.persistence_val) if r.persistence_val is not None else None,
                climatology_val=float(r.climatology_val) if r.climatology_val is not None else None,
                sample_count=int(r.sample_count),
                evaluated_at=r.evaluated_at,
            )
            bucket = _classify_segment_group(r.segment_key)
            getattr(grouped, bucket).append(metric_row)

    calibration_curve = load_calibration_curve(resolved_version)

    return MlSkillResponse(
        model_version=resolved_version,
        metrics=grouped,
        calibration_curve=calibration_curve,
    )
