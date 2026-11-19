"""Scoring pipeline: load verified MOS bundle, classify alerts, and upsert fact_ml_forecast."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mxweather.db.models import (
    DimMunicipality,
    FactForecastRevision,
    FactMlForecast,
    FactObservedDaily,
)
from mxweather.ml.features import build_pit_features
from mxweather.ml.registry import load_latest_model_bundle, load_model_bundle
from mxweather.ml.train import WeatherMOSBundle, enforce_quantile_monotonicity


def _finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    val = float(value)
    return val if math.isfinite(val) else None


def classify_alert_level(
    *,
    precip_mm: float | None,
    tmax_c: float | None,
    tmin_c: float | None,
    wind_gust_kmh: float | None,
    calibrated_rain_prob: float | None,
) -> str:
    """Classify verifiable forecast-threshold alert tier.

    WARNING_P0:
        precip_mm >= 25 or tmax_c >= 38 or tmin_c <= 2 or wind_gust_kmh >= 50
    ADVISORY:
        precip_mm >= 10 or calibrated_rain_prob >= 0.70 or tmax_c >= 35 or wind_gust_kmh >= 35
    NORMAL:
        otherwise
    """
    p = _finite_or_none(precip_mm)
    tx = _finite_or_none(tmax_c)
    tn = _finite_or_none(tmin_c)
    wg = _finite_or_none(wind_gust_kmh)
    rp = _finite_or_none(calibrated_rain_prob)

    if (
        (p is not None and p >= 25.0)
        or (tx is not None and tx >= 38.0)
        or (tn is not None and tn <= 2.0)
        or (wg is not None and wg >= 50.0)
    ):
        return "WARNING_P0"

    if (
        (p is not None and p >= 10.0)
        or (rp is not None and rp >= 0.70)
        or (tx is not None and tx >= 35.0)
        or (wg is not None and wg >= 35.0)
    ):
        return "ADVISORY"

    return "NORMAL"


def score_forecast_dataframe(
    bundle: WeatherMOSBundle,
    forecast_df: pd.DataFrame,
    observed_df: pd.DataFrame,
    municipalities_df: pd.DataFrame,
    *,
    model_version: str,
    target_mask: np.ndarray | None = None,
) -> pd.DataFrame:
    """Build PIT features, predict quantiles + rain probability, and classify alert levels.

    If `target_mask` is supplied, PIT features are computed over all rows in
    `forecast_df` (preserving historical bias context) and only masked rows are
    scored and returned.
    """
    if forecast_df.empty:
        return pd.DataFrame()

    pit_df = build_pit_features(forecast_df, observed_df, municipalities_df)
    if target_mask is not None:
        pit_df = pit_df.loc[np.asarray(target_mask, dtype=bool)].reset_index(drop=True)
    if pit_df.empty:
        return pd.DataFrame()

    preds = bundle.predict(pit_df)

    # Round to DB precision and re-enforce monotonicity after rounding.
    tmax_p10, tmax_p50, tmax_p90 = enforce_quantile_monotonicity(
        np.round(preds["pred_tmax_p10"].to_numpy(dtype=np.float64), 2),
        np.round(preds["pred_tmax_p50"].to_numpy(dtype=np.float64), 2),
        np.round(preds["pred_tmax_p90"].to_numpy(dtype=np.float64), 2),
    )
    tmin_p10, tmin_p50, tmin_p90 = enforce_quantile_monotonicity(
        np.round(preds["pred_tmin_p10"].to_numpy(dtype=np.float64), 2),
        np.round(preds["pred_tmin_p50"].to_numpy(dtype=np.float64), 2),
        np.round(preds["pred_tmin_p90"].to_numpy(dtype=np.float64), 2),
    )
    cal_rain = np.clip(
        np.round(preds["calibrated_rain_prob"].to_numpy(dtype=np.float64), 4),
        0.0,
        1.0,
    )

    alerts = [
        classify_alert_level(
            precip_mm=row_p,
            tmax_c=row_tx,
            tmin_c=row_tn,
            wind_gust_kmh=row_wg,
            calibrated_rain_prob=row_rp,
        )
        for row_p, row_tx, row_tn, row_wg, row_rp in zip(
            pit_df["precip_mm"].to_numpy(dtype=np.float64),
            tmax_p50,
            tmin_p50,
            pit_df["wind_gust_kmh"].to_numpy(dtype=np.float64),
            cal_rain,
            strict=True,
        )
    ]

    out = pd.DataFrame(
        {
            "model_version": model_version,
            "state_id": pit_df["state_id"].astype(int),
            "municipality_id": pit_df["municipality_id"].astype(int),
            "issued_at": (
                pit_df["issued_at"]
                if "issued_at" in pit_df.columns
                else datetime.now(UTC)
            ),
            "valid_date": pd.to_datetime(pit_df["valid_date"]).dt.date,
            "lead_day": pit_df["lead_day"].astype(int),
            "raw_tmax_c": np.round(pit_df["tmax_c"].to_numpy(dtype=np.float64), 2),
            "pred_tmax_p10": tmax_p10,
            "pred_tmax_p50": tmax_p50,
            "pred_tmax_p90": tmax_p90,
            "raw_tmin_c": np.round(pit_df["tmin_c"].to_numpy(dtype=np.float64), 2),
            "pred_tmin_p10": tmin_p10,
            "pred_tmin_p50": tmin_p50,
            "pred_tmin_p90": tmin_p90,
            "raw_precip_prob_pct": pit_df["precip_prob_pct"],
            "calibrated_rain_prob": cal_rain,
            "alert_level": alerts,
        }
    )
    return out


def upsert_ml_forecasts(session: Session, scored_df: pd.DataFrame) -> int:
    """Idempotently upsert scored predictions into `fact_ml_forecast`."""
    if scored_df.empty:
        return 0

    records: list[dict[str, Any]] = []
    for row in scored_df.to_dict(orient="records"):
        raw_prob = row.get("raw_precip_prob_pct")
        raw_prob_int = (
            int(round(float(raw_prob)))
            if raw_prob is not None and math.isfinite(float(raw_prob))
            else None
        )
        issued_val = row["issued_at"]
        if isinstance(issued_val, pd.Timestamp):
            issued_val = issued_val.to_pydatetime()
        if issued_val.tzinfo is None:
            issued_val = issued_val.replace(tzinfo=UTC)

        valid_val = row["valid_date"]
        if isinstance(valid_val, pd.Timestamp):
            valid_val = valid_val.date()

        records.append(
            {
                "model_version": str(row["model_version"]),
                "state_id": int(row["state_id"]),
                "municipality_id": int(row["municipality_id"]),
                "issued_at": issued_val,
                "valid_date": valid_val,
                "lead_day": int(row["lead_day"]),
                "raw_tmax_c": Decimal(f"{float(row['raw_tmax_c']):.2f}"),
                "pred_tmax_p10": Decimal(f"{float(row['pred_tmax_p10']):.2f}"),
                "pred_tmax_p50": Decimal(f"{float(row['pred_tmax_p50']):.2f}"),
                "pred_tmax_p90": Decimal(f"{float(row['pred_tmax_p90']):.2f}"),
                "raw_tmin_c": Decimal(f"{float(row['raw_tmin_c']):.2f}"),
                "pred_tmin_p10": Decimal(f"{float(row['pred_tmin_p10']):.2f}"),
                "pred_tmin_p50": Decimal(f"{float(row['pred_tmin_p50']):.2f}"),
                "pred_tmin_p90": Decimal(f"{float(row['pred_tmin_p90']):.2f}"),
                "raw_precip_prob_pct": raw_prob_int,
                "calibrated_rain_prob": Decimal(f"{float(row['calibrated_rain_prob']):.4f}"),
                "alert_level": str(row["alert_level"]),
            }
        )

    stmt = insert(FactMlForecast).values(records)
    update_cols = {
        "lead_day": stmt.excluded.lead_day,
        "raw_tmax_c": stmt.excluded.raw_tmax_c,
        "pred_tmax_p10": stmt.excluded.pred_tmax_p10,
        "pred_tmax_p50": stmt.excluded.pred_tmax_p50,
        "pred_tmax_p90": stmt.excluded.pred_tmax_p90,
        "raw_tmin_c": stmt.excluded.raw_tmin_c,
        "pred_tmin_p10": stmt.excluded.pred_tmin_p10,
        "pred_tmin_p50": stmt.excluded.pred_tmin_p50,
        "pred_tmin_p90": stmt.excluded.pred_tmin_p90,
        "raw_precip_prob_pct": stmt.excluded.raw_precip_prob_pct,
        "calibrated_rain_prob": stmt.excluded.calibrated_rain_prob,
        "alert_level": stmt.excluded.alert_level,
        "scored_at": func.now(),
    }
    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=[
            "model_version",
            "state_id",
            "municipality_id",
            "issued_at",
            "valid_date",
        ],
        set_=update_cols,
    )
    session.execute(upsert_stmt)
    session.commit()
    return len(records)


def score_latest_forecasts_in_db(
    session: Session,
    *,
    model_version: str | None = None,
    artifacts_dir: Path | None = None,
    lookback_days: int = 60,
    score_recent_days: int = 7,
) -> dict[str, Any]:
    """Load verified model bundle, score latest forecasts in Postgres, and upsert results."""
    loaded = (
        load_model_bundle(model_version, artifacts_dir=artifacts_dir)
        if model_version
        else load_latest_model_bundle(artifacts_dir=artifacts_dir)
    )
    resolved_version: str = loaded["model_version"]
    bundle: WeatherMOSBundle = loaded["model_bundle"]

    max_valid_date: date | None = session.execute(
        select(func.max(FactForecastRevision.valid_date))
    ).scalar_one_or_none()
    if max_valid_date is None:
        return {
            "model_version": resolved_version,
            "rows_scored": 0,
            "alert_counts": {},
        }

    history_start = max_valid_date - timedelta(days=lookback_days)
    score_start = max_valid_date - timedelta(days=score_recent_days)

    mun_rows = session.execute(
        select(
            DimMunicipality.state_id,
            DimMunicipality.municipality_id,
            DimMunicipality.lat,
            DimMunicipality.lon,
            DimMunicipality.elevation_m,
        )
    ).all()
    municipalities_df = pd.DataFrame(
        [
            {
                "state_id": int(r.state_id),
                "municipality_id": int(r.municipality_id),
                "lat": float(r.lat),
                "lon": float(r.lon),
                "elevation_m": float(r.elevation_m) if r.elevation_m is not None else np.nan,
            }
            for r in mun_rows
        ]
    )

    fcst_rows = session.execute(
        select(
            FactForecastRevision.source_provider,
            FactForecastRevision.state_id,
            FactForecastRevision.municipality_id,
            FactForecastRevision.issued_at,
            FactForecastRevision.valid_date,
            FactForecastRevision.lead_day,
            FactForecastRevision.tmax_c,
            FactForecastRevision.tmin_c,
            FactForecastRevision.precip_mm,
            FactForecastRevision.precip_prob_pct,
            FactForecastRevision.wind_speed_kmh,
            FactForecastRevision.wind_gust_kmh,
        ).where(FactForecastRevision.valid_date >= history_start)
    ).all()
    forecast_df = pd.DataFrame(
        [
            {
                "source_provider": r.source_provider,
                "state_id": int(r.state_id),
                "municipality_id": int(r.municipality_id),
                "issued_at": r.issued_at,
                "valid_date": r.valid_date,
                "lead_day": int(r.lead_day),
                "tmax_c": float(r.tmax_c),
                "tmin_c": float(r.tmin_c),
                "precip_mm": float(r.precip_mm) if r.precip_mm is not None else np.nan,
                "precip_prob_pct": float(r.precip_prob_pct) if r.precip_prob_pct is not None else np.nan,
                "wind_speed_kmh": float(r.wind_speed_kmh) if r.wind_speed_kmh is not None else np.nan,
                "wind_gust_kmh": float(r.wind_gust_kmh) if r.wind_gust_kmh is not None else np.nan,
            }
            for r in fcst_rows
        ]
    )

    if forecast_df.empty:
        return {
            "model_version": resolved_version,
            "rows_scored": 0,
            "alert_counts": {},
        }

    # Keep the latest revision per (state_id, municipality_id, valid_date).
    forecast_df = (
        forecast_df.sort_values(["state_id", "municipality_id", "valid_date", "issued_at"])
        .drop_duplicates(subset=["state_id", "municipality_id", "valid_date"], keep="last")
        .reset_index(drop=True)
    )

    obs_rows = session.execute(
        select(
            FactObservedDaily.source_provider,
            FactObservedDaily.state_id,
            FactObservedDaily.municipality_id,
            FactObservedDaily.obs_date,
            FactObservedDaily.tmax_c,
            FactObservedDaily.tmin_c,
            FactObservedDaily.precip_mm,
        ).where(FactObservedDaily.obs_date >= history_start)
    ).all()
    observed_df = pd.DataFrame(
        [
            {
                "source_provider": r.source_provider,
                "state_id": int(r.state_id),
                "municipality_id": int(r.municipality_id),
                "obs_date": r.obs_date,
                "tmax_c": float(r.tmax_c),
                "tmin_c": float(r.tmin_c),
                "precip_mm": float(r.precip_mm) if r.precip_mm is not None else np.nan,
            }
            for r in obs_rows
        ]
    )

    target_mask = (pd.to_datetime(forecast_df["valid_date"]).dt.date >= score_start).to_numpy()
    scored_df = score_forecast_dataframe(
        bundle,
        forecast_df,
        observed_df,
        municipalities_df,
        model_version=resolved_version,
        target_mask=target_mask,
    )
    upserted = upsert_ml_forecasts(session, scored_df)
    alert_counts = (
        scored_df["alert_level"].value_counts().to_dict() if not scored_df.empty else {}
    )
    return {
        "model_version": resolved_version,
        "rows_scored": upserted,
        "alert_counts": {str(k): int(v) for k, v in alert_counts.items()},
    }
