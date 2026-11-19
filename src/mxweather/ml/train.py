"""Temporal walk-forward training for LightGBM MOS regressors and calibrated rain classifier."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.isotonic import IsotonicRegression
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mxweather.db.models import (
    DimMunicipality,
    FactForecastRevision,
    FactObservedDaily,
    MlModelEvaluation,
)
from mxweather.ml.evaluate import evaluate_predictions
from mxweather.ml.features import FEATURE_COLUMNS, build_pit_features
from mxweather.ml.registry import save_model_bundle


@dataclass(frozen=True)
class TemporalSplit:
    """Strictly ordered temporal train/validation split by unique date."""

    name: str
    train_mask: np.ndarray
    val_mask: np.ndarray
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    val_start: pd.Timestamp
    val_end: pd.Timestamp


def enforce_quantile_monotonicity(
    p10: np.ndarray,
    p50: np.ndarray,
    p90: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Enforce p10 <= p50 <= p90 via quantile rearrangement (sorting per row)."""
    stacked = np.vstack(
        [
            np.asarray(p10, dtype=np.float64),
            np.asarray(p50, dtype=np.float64),
            np.asarray(p90, dtype=np.float64),
        ]
    )
    sorted_q = np.sort(stacked, axis=0)
    return sorted_q[0], sorted_q[1], sorted_q[2]


def generate_walk_forward_splits(
    dates: pd.Series | np.ndarray,
    *,
    n_folds: int = 4,
    holdout_fraction: float = 0.15,
    min_initial_train_fraction: float = 0.35,
) -> tuple[list[TemporalSplit], TemporalSplit]:
    """Create `n_folds` expanding temporal CV splits + a final holdout split.

    All rows sharing a `valid_date` are assigned to the same partition, and
    strictly `max(train_dates) < min(val_dates)` holds for every split.
    Zero random shuffling is performed.
    """
    date_series = pd.to_datetime(pd.Series(dates)).dt.normalize().reset_index(drop=True)
    unique_dates = np.sort(date_series.dropna().unique())
    n_dates = len(unique_dates)

    min_required = n_folds + 4
    if n_dates < min_required:
        raise ValueError(
            f"Need at least {min_required} unique dates for {n_folds}-fold walk-forward CV "
            f"+ holdout, got {n_dates}."
        )

    holdout_days = max(1, int(round(n_dates * holdout_fraction)))
    dev_days = n_dates - holdout_days
    if dev_days < n_folds + 2:
        holdout_days = max(1, n_dates - (n_folds + 2))
        dev_days = n_dates - holdout_days

    dev_dates = unique_dates[:dev_days]
    holdout_dates = unique_dates[dev_days:]

    initial_train_days = max(2, int(round(dev_days * min_initial_train_fraction)))
    remaining_for_folds = dev_days - initial_train_days
    if remaining_for_folds < n_folds:
        initial_train_days = max(1, dev_days - n_folds)
        remaining_for_folds = dev_days - initial_train_days

    fold_edges = np.linspace(initial_train_days, dev_days, n_folds + 1, dtype=int)
    cv_splits: list[TemporalSplit] = []

    date_arr = date_series.to_numpy()
    for i in range(n_folds):
        val_start_idx = int(fold_edges[i])
        val_end_idx = int(fold_edges[i + 1])
        if val_end_idx <= val_start_idx:
            continue

        tr_dates = dev_dates[:val_start_idx]
        va_dates = dev_dates[val_start_idx:val_end_idx]

        tr_mask = (date_arr >= tr_dates[0]) & (date_arr <= tr_dates[-1])
        va_mask = (date_arr >= va_dates[0]) & (date_arr <= va_dates[-1])
        cv_splits.append(
            TemporalSplit(
                name=f"cv_fold_{i + 1}",
                train_mask=tr_mask,
                val_mask=va_mask,
                train_start=pd.Timestamp(tr_dates[0]),
                train_end=pd.Timestamp(tr_dates[-1]),
                val_start=pd.Timestamp(va_dates[0]),
                val_end=pd.Timestamp(va_dates[-1]),
            )
        )

    holdout_split = TemporalSplit(
        name="holdout",
        train_mask=(date_arr >= dev_dates[0]) & (date_arr <= dev_dates[-1]),
        val_mask=(date_arr >= holdout_dates[0]) & (date_arr <= holdout_dates[-1]),
        train_start=pd.Timestamp(dev_dates[0]),
        train_end=pd.Timestamp(dev_dates[-1]),
        val_start=pd.Timestamp(holdout_dates[0]),
        val_end=pd.Timestamp(holdout_dates[-1]),
    )
    return cv_splits, holdout_split


class _ConstantProbabilityModel:
    """Fallback when a temporal slice contains only a single rain class."""

    def __init__(self, prob: float) -> None:
        self.prob = float(np.clip(prob, 0.0, 1.0))

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        p = np.full(len(X), self.prob, dtype=np.float64)
        return np.column_stack([1.0 - p, p])


class WeatherMOSBundle:
    """Serializable MOS model bundle (tmax/tmin quantiles + isotonic rain classifier)."""

    def __init__(
        self,
        *,
        feature_columns: list[str],
        tmax_models: dict[str, LGBMRegressor],
        tmin_models: dict[str, LGBMRegressor],
        rain_classifier: Any,
        rain_calibrator: IsotonicRegression | None,
        rain_threshold_mm: float = 1.0,
    ) -> None:
        self.feature_columns = list(feature_columns)
        self.tmax_models = tmax_models
        self.tmin_models = tmin_models
        self.rain_classifier = rain_classifier
        self.rain_calibrator = rain_calibrator
        self.rain_threshold_mm = rain_threshold_mm

    def predict(self, feature_df: pd.DataFrame) -> pd.DataFrame:
        """Predict monotonic tmax/tmin quantiles and calibrated rain probability."""
        X = feature_df[self.feature_columns].astype(np.float64)

        tmax_p10 = self.tmax_models["p10"].predict(X)
        tmax_p50 = self.tmax_models["p50"].predict(X)
        tmax_p90 = self.tmax_models["p90"].predict(X)
        tmax_p10, tmax_p50, tmax_p90 = enforce_quantile_monotonicity(tmax_p10, tmax_p50, tmax_p90)

        tmin_p10 = self.tmin_models["p10"].predict(X)
        tmin_p50 = self.tmin_models["p50"].predict(X)
        tmin_p90 = self.tmin_models["p90"].predict(X)
        tmin_p10, tmin_p50, tmin_p90 = enforce_quantile_monotonicity(tmin_p10, tmin_p50, tmin_p90)

        raw_rain_prob = np.asarray(self.rain_classifier.predict_proba(X)[:, 1], dtype=np.float64)
        if self.rain_calibrator is not None:
            cal_rain_prob = np.asarray(self.rain_calibrator.predict(raw_rain_prob), dtype=np.float64)
        else:
            cal_rain_prob = raw_rain_prob
        cal_rain_prob = np.clip(cal_rain_prob, 0.0, 1.0)

        return pd.DataFrame(
            {
                "pred_tmax_p10": tmax_p10,
                "pred_tmax_p50": tmax_p50,
                "pred_tmax_p90": tmax_p90,
                "pred_tmin_p10": tmin_p10,
                "pred_tmin_p50": tmin_p50,
                "pred_tmin_p90": tmin_p90,
                "calibrated_rain_prob": cal_rain_prob,
            },
            index=feature_df.index,
        )


def _make_lgbm_regressor(
    *,
    objective: str,
    alpha: float | None = None,
    n_estimators: int = 200,
    random_state: int = 42,
) -> LGBMRegressor:
    params: dict[str, Any] = {
        "objective": objective,
        "n_estimators": n_estimators,
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_child_samples": 10,
        "subsample": 0.85,
        "colsample_bytree": 0.85,
        "random_state": random_state,
        "n_jobs": 1,
        "verbose": -1,
    }
    if alpha is not None:
        params["alpha"] = alpha
    return LGBMRegressor(**params)


def _fit_mos_bundle(
    train_df: pd.DataFrame,
    *,
    feature_columns: list[str] = FEATURE_COLUMNS,
    n_estimators: int = 200,
    calibration_fraction: float = 0.20,
    random_state: int = 42,
) -> WeatherMOSBundle:
    """Fit tmax/tmin quantile models and temporally calibrated rain classifier."""
    X_all = train_df[feature_columns].astype(np.float64)

    # Temperature quantile regressors.
    tmax_models: dict[str, LGBMRegressor] = {}
    tmin_models: dict[str, LGBMRegressor] = {}

    for target, store in (("tmax", tmax_models), ("tmin", tmin_models)):
        y = train_df[f"obs_{target}_c"].to_numpy(dtype=np.float64)
        valid = np.isfinite(y)
        X_t = X_all.loc[valid]
        y_t = y[valid]

        m_p50 = _make_lgbm_regressor(
            objective="regression_l1", n_estimators=n_estimators, random_state=random_state
        )
        m_p10 = _make_lgbm_regressor(
            objective="quantile", alpha=0.10, n_estimators=n_estimators, random_state=random_state
        )
        m_p90 = _make_lgbm_regressor(
            objective="quantile", alpha=0.90, n_estimators=n_estimators, random_state=random_state
        )
        m_p50.fit(X_t, y_t)
        m_p10.fit(X_t, y_t)
        m_p90.fit(X_t, y_t)
        store["p10"] = m_p10
        store["p50"] = m_p50
        store["p90"] = m_p90

    # Rain binary classifier + strictly temporal Isotonic Regression calibration slice.
    rain_valid = train_df["obs_rain_flag"].notna().to_numpy()
    rain_df = train_df.loc[rain_valid].copy()
    unique_dates = np.sort(pd.to_datetime(rain_df["valid_date"]).dt.normalize().unique())

    if len(unique_dates) >= 5:
        cal_days = max(1, int(round(len(unique_dates) * calibration_fraction)))
        split_idx = len(unique_dates) - cal_days
        clf_end_date = unique_dates[split_idx - 1]
        cal_start_date = unique_dates[split_idx]

        r_dates = pd.to_datetime(rain_df["valid_date"]).dt.normalize().to_numpy()
        clf_mask = r_dates <= clf_end_date
        cal_mask = r_dates >= cal_start_date
    else:
        clf_mask = np.ones(len(rain_df), dtype=bool)
        cal_mask = np.zeros(len(rain_df), dtype=bool)

    X_clf = rain_df.loc[clf_mask, feature_columns].astype(np.float64)
    y_clf = rain_df.loc[clf_mask, "obs_rain_flag"].astype(int).to_numpy()

    if len(np.unique(y_clf)) < 2:
        rain_clf: Any = _ConstantProbabilityModel(float(np.mean(y_clf)) if len(y_clf) > 0 else 0.0)
        calibrator: IsotonicRegression | None = None
    else:
        rain_clf = LGBMClassifier(
            objective="binary",
            n_estimators=n_estimators,
            learning_rate=0.05,
            num_leaves=31,
            min_child_samples=10,
            subsample=0.85,
            colsample_bytree=0.85,
            random_state=random_state,
            n_jobs=1,
            verbose=-1,
        )
        rain_clf.fit(X_clf, y_clf)

        if np.any(cal_mask):
            X_cal = rain_df.loc[cal_mask, feature_columns].astype(np.float64)
            y_cal = rain_df.loc[cal_mask, "obs_rain_flag"].astype(float).to_numpy()
            raw_cal_probs = np.asarray(rain_clf.predict_proba(X_cal)[:, 1], dtype=np.float64)
            if len(np.unique(raw_cal_probs)) >= 2 and len(np.unique(y_cal)) >= 2:
                calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
                calibrator.fit(raw_cal_probs, y_cal)
            else:
                calibrator = None
        else:
            calibrator = None

    return WeatherMOSBundle(
        feature_columns=feature_columns,
        tmax_models=tmax_models,
        tmin_models=tmin_models,
        rain_classifier=rain_clf,
        rain_calibrator=calibrator,
    )


def prepare_paired_training_dataset(
    forecast_df: pd.DataFrame,
    observed_df: pd.DataFrame,
    municipalities_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build PIT features and join ground-truth observed targets on valid_date == obs_date."""
    pit_df = build_pit_features(forecast_df, observed_df, municipalities_df)

    obs = observed_df.copy()
    rename_map = {}
    if "obs_tmax_c" not in obs.columns and "tmax_c" in obs.columns:
        rename_map["tmax_c"] = "obs_tmax_c"
    if "obs_tmin_c" not in obs.columns and "tmin_c" in obs.columns:
        rename_map["tmin_c"] = "obs_tmin_c"
    if "obs_precip_mm" not in obs.columns and "precip_mm" in obs.columns:
        rename_map["precip_mm"] = "obs_precip_mm"
    if rename_map:
        obs = obs.rename(columns=rename_map)

    obs["valid_date"] = pd.to_datetime(obs["obs_date"]).dt.normalize().astype("datetime64[ns]")
    obs["state_id"] = obs["state_id"].astype("int64")
    obs["municipality_id"] = obs["municipality_id"].astype("int64")
    for col in ("obs_tmax_c", "obs_tmin_c", "obs_precip_mm"):
        obs[col] = pd.to_numeric(obs[col], errors="coerce").astype("float64")

    obs_dedup = (
        obs.sort_values(["state_id", "municipality_id", "valid_date"])
        .drop_duplicates(subset=["state_id", "municipality_id", "valid_date"], keep="first")
        [["state_id", "municipality_id", "valid_date", "obs_tmax_c", "obs_tmin_c", "obs_precip_mm"]]
    )

    paired = pit_df.merge(
        obs_dedup,
        on=["state_id", "municipality_id", "valid_date"],
        how="inner",
    )
    paired["obs_rain_flag"] = np.where(
        paired["obs_precip_mm"].notna(),
        (paired["obs_precip_mm"] >= 1.0).astype("float64"),
        np.nan,
    )
    return paired.sort_values(["valid_date", "state_id", "municipality_id"]).reset_index(drop=True)


def train_and_evaluate(
    forecast_df: pd.DataFrame,
    observed_df: pd.DataFrame,
    municipalities_df: pd.DataFrame,
    *,
    model_version: str | None = None,
    n_folds: int = 4,
    holdout_fraction: float = 0.15,
    n_estimators: int = 200,
    artifacts_dir: Path | None = None,
    save_bundle: bool = True,
) -> dict[str, Any]:
    """Run temporal walk-forward CV, holdout evaluation, and final bundle training."""
    version = model_version or f"mos_lgbm_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    paired_df = prepare_paired_training_dataset(forecast_df, observed_df, municipalities_df)
    if paired_df.empty:
        raise ValueError("No paired forecast-observed rows available for training.")

    cv_splits, holdout_split = generate_walk_forward_splits(
        paired_df["valid_date"],
        n_folds=n_folds,
        holdout_fraction=holdout_fraction,
    )

    all_eval_rows: list[dict[str, Any]] = []
    cv_pred_frames: list[pd.DataFrame] = []
    split_metadata: list[dict[str, Any]] = []

    for split in cv_splits:
        tr_df = paired_df.loc[split.train_mask]
        va_df = paired_df.loc[split.val_mask].copy()
        fold_model = _fit_mos_bundle(tr_df, n_estimators=n_estimators)
        preds = fold_model.predict(va_df)
        for col in preds.columns:
            va_df[col] = preds[col].to_numpy()
        cv_pred_frames.append(va_df)

        fold_rows, _ = evaluate_predictions(va_df, model_version=version, split_name=split.name)
        all_eval_rows.extend(fold_rows)
        split_metadata.append(
            {
                "split_name": split.name,
                "train_start": str(split.train_start.date()),
                "train_end": str(split.train_end.date()),
                "val_start": str(split.val_start.date()),
                "val_end": str(split.val_end.date()),
                "train_rows": int(len(tr_df)),
                "val_rows": int(len(va_df)),
            }
        )

    # Aggregate walk-forward CV predictions across all folds.
    if cv_pred_frames:
        cv_all_df = pd.concat(cv_pred_frames, ignore_index=True)
        cv_agg_rows, _ = evaluate_predictions(
            cv_all_df, model_version=version, split_name="walk_forward_cv"
        )
        all_eval_rows.extend(cv_agg_rows)

    # Final holdout evaluation.
    ho_tr_df = paired_df.loc[holdout_split.train_mask]
    ho_va_df = paired_df.loc[holdout_split.val_mask].copy()
    holdout_model = _fit_mos_bundle(ho_tr_df, n_estimators=n_estimators)
    ho_preds = holdout_model.predict(ho_va_df)
    for col in ho_preds.columns:
        ho_va_df[col] = ho_preds[col].to_numpy()

    holdout_rows, holdout_calibration = evaluate_predictions(
        ho_va_df, model_version=version, split_name="holdout"
    )
    all_eval_rows.extend(holdout_rows)
    split_metadata.append(
        {
            "split_name": "holdout",
            "train_start": str(holdout_split.train_start.date()),
            "train_end": str(holdout_split.train_end.date()),
            "val_start": str(holdout_split.val_start.date()),
            "val_end": str(holdout_split.val_end.date()),
            "train_rows": int(len(ho_tr_df)),
            "val_rows": int(len(ho_va_df)),
        }
    )

    # Fit production bundle on all available paired data (with internal temporal calibration slice).
    final_bundle = _fit_mos_bundle(paired_df, n_estimators=n_estimators)

    feature_schema = {
        "model_version": version,
        "feature_columns": list(FEATURE_COLUMNS),
        "rain_threshold_mm": 1.0,
        "quantiles": ["p10", "p50", "p90"],
        "created_at": datetime.now(UTC).isoformat(),
    }
    metrics_payload = {
        "model_version": version,
        "trained_at": datetime.now(UTC).isoformat(),
        "total_paired_rows": int(len(paired_df)),
        "splits": split_metadata,
        "evaluation_rows": all_eval_rows,
    }

    bundle_dir: Path | None = None
    if save_bundle:
        bundle_dir = save_model_bundle(
            model_version=version,
            model_bundle=final_bundle,
            feature_schema=feature_schema,
            metrics=metrics_payload,
            calibration_curve=holdout_calibration,
            artifacts_dir=artifacts_dir,
            update_latest=True,
        )

    return {
        "model_version": version,
        "bundle": final_bundle,
        "bundle_dir": bundle_dir,
        "evaluation_rows": all_eval_rows,
        "calibration_curve": holdout_calibration,
        "metrics": metrics_payload,
        "holdout_predictions": ho_va_df,
    }


def load_training_data_from_db(
    session: Session,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load municipality metadata, historical forecasts, and observed daily ground truth."""
    mun_rows = session.execute(
        select(
            DimMunicipality.state_id,
            DimMunicipality.municipality_id,
            DimMunicipality.lat,
            DimMunicipality.lon,
            DimMunicipality.elevation_m,
            DimMunicipality.is_ml_cohort,
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
                "is_ml_cohort": bool(r.is_ml_cohort),
            }
            for r in mun_rows
        ]
    )

    cohort_muns = municipalities_df.loc[municipalities_df["is_ml_cohort"]]
    if not cohort_muns.empty:
        cohort_keys = set(
            zip(cohort_muns["state_id"].tolist(), cohort_muns["municipality_id"].tolist(), strict=True)
        )
    else:
        cohort_keys = None

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
        )
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
            if cohort_keys is None or (int(r.state_id), int(r.municipality_id)) in cohort_keys
        ]
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
        )
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
            if cohort_keys is None or (int(r.state_id), int(r.municipality_id)) in cohort_keys
        ]
    )

    return forecast_df, observed_df, municipalities_df


def write_evaluation_rows(
    session: Session,
    model_version: str,
    evaluation_rows: list[dict[str, Any]],
) -> int:
    """Idempotently replace evaluation rows for `model_version` in `ml_model_evaluation`."""
    session.execute(
        delete(MlModelEvaluation).where(MlModelEvaluation.model_version == model_version)
    )
    objects = [
        MlModelEvaluation(
            model_version=row["model_version"],
            split_name=row["split_name"],
            segment_key=row["segment_key"],
            metric_name=row["metric_name"],
            raw_baseline_val=row["raw_baseline_val"],
            ml_model_val=row["ml_model_val"],
            persistence_val=row["persistence_val"],
            climatology_val=row["climatology_val"],
            sample_count=int(row["sample_count"]),
        )
        for row in evaluation_rows
    ]
    session.add_all(objects)
    session.commit()
    return len(objects)
