"""End-to-end synthetic ML pipeline tests: train -> save -> verify -> load -> score."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mxweather.ml.evaluate import (
    bias,
    brier_score,
    expected_calibration_error,
    interval_coverage_80,
    log_loss,
    mae,
    pinball_loss,
    reliability_curve,
    rmse,
)
from mxweather.ml.registry import (
    ModelIntegrityError,
    load_latest_model_bundle,
    verify_manifest,
)
from mxweather.ml.score import classify_alert_level, score_forecast_dataframe
from mxweather.ml.train import train_and_evaluate


def _make_multi_month_dataset(n_days: int = 240) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Generate 8-month synthetic dataset spanning all 3 elevation bands and both seasons."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2025-01-01", periods=n_days, freq="D")

    municipalities = pd.DataFrame(
        [
            {"state_id": 31, "municipality_id": 50, "lat": 20.97, "lon": -89.59, "elevation_m": 10.0},
            {"state_id": 25, "municipality_id": 6, "lat": 24.81, "lon": -107.39, "elevation_m": 60.0},
            {"state_id": 14, "municipality_id": 39, "lat": 20.66, "lon": -103.35, "elevation_m": 1566.0},
            {"state_id": 19, "municipality_id": 39, "lat": 25.69, "lon": -100.32, "elevation_m": 540.0},
            {"state_id": 9, "municipality_id": 15, "lat": 19.43, "lon": -99.13, "elevation_m": 2240.0},
            {"state_id": 15, "municipality_id": 106, "lat": 19.29, "lon": -99.66, "elevation_m": 2660.0},
        ]
    )

    fcst_rows = []
    obs_rows = []
    for _, mun in municipalities.iterrows():
        elev = float(mun["elevation_m"])
        base_tmax = 34.0 - 0.005 * elev
        systematic_bias = 1.8 if elev > 1800 else (-1.2 if elev < 500 else 0.6)

        for d in dates:
            doy = d.day_of_year
            seasonal = 4.0 * np.sin(2.0 * np.pi * (doy - 90) / 365.25)
            is_rainy = 5 <= d.month <= 10
            rain_prob_true = 0.45 if is_rainy else 0.12

            obs_tmax = base_tmax + seasonal + rng.normal(0.0, 1.5)
            obs_tmin = obs_tmax - 12.0 + rng.normal(0.0, 1.0)
            rained = rng.random() < rain_prob_true
            obs_precip = float(rng.gamma(2.5, 4.0) + 1.0) if rained else float(rng.uniform(0.0, 0.3))

            obs_rows.append(
                {
                    "state_id": int(mun["state_id"]),
                    "municipality_id": int(mun["municipality_id"]),
                    "obs_date": d,
                    "tmax_c": obs_tmax,
                    "tmin_c": obs_tmin,
                    "precip_mm": obs_precip,
                }
            )

            lead_day = 1
            raw_prob_pct = int(
                np.clip(
                    (75.0 if rained else 20.0) + rng.normal(0.0, 18.0),
                    0.0,
                    100.0,
                )
            )
            fcst_rows.append(
                {
                    "state_id": int(mun["state_id"]),
                    "municipality_id": int(mun["municipality_id"]),
                    "issued_at": pd.Timestamp(d - pd.Timedelta(days=lead_day), tz="UTC"),
                    "valid_date": d,
                    "lead_day": lead_day,
                    "tmax_c": obs_tmax + systematic_bias + rng.normal(0.0, 0.9),
                    "tmin_c": obs_tmin - 0.5 * systematic_bias + rng.normal(0.0, 0.8),
                    "precip_mm": max(0.0, obs_precip + rng.normal(0.0, 1.5)),
                    "precip_prob_pct": raw_prob_pct,
                    "wind_speed_kmh": float(12.0 + rng.uniform(0.0, 10.0)),
                    "wind_gust_kmh": float(20.0 + rng.uniform(0.0, 20.0)),
                }
            )

    return pd.DataFrame(fcst_rows), pd.DataFrame(obs_rows), municipalities


def test_end_to_end_train_save_verify_load_score(tmp_path: Path) -> None:
    forecast_df, observed_df, municipalities_df = _make_multi_month_dataset(n_days=240)

    result = train_and_evaluate(
        forecast_df,
        observed_df,
        municipalities_df,
        model_version="test_mos_v1",
        n_folds=4,
        holdout_fraction=0.15,
        n_estimators=25,
        artifacts_dir=tmp_path,
        save_bundle=True,
    )

    bundle_dir = result["bundle_dir"]
    assert bundle_dir is not None and bundle_dir.exists()

    # Manifest verification must pass on untouched bundle.
    digests = verify_manifest(bundle_dir)
    assert set(digests.keys()) == {
        "calibration_curve.json",
        "feature_schema.json",
        "metrics.json",
        "model_bundle.joblib",
    }

    # Check evaluation slices exist (overall, all 3 elevation bands, rainy/dry seasons).
    eval_rows = result["evaluation_rows"]
    segments = {row["segment_key"] for row in eval_rows}
    assert "overall" in segments
    assert "elevation:coastal_<500m" in segments
    assert "elevation:midland_500_1800m" in segments
    assert "elevation:highland_>1800m" in segments
    assert "season:rainy_may_oct" in segments
    assert "season:dry_nov_apr" in segments

    # Load via latest.json pointer with SHA-256 verification.
    loaded = load_latest_model_bundle(artifacts_dir=tmp_path)
    assert loaded["model_version"] == "test_mos_v1"

    # Score the last 10 days of forecasts.
    max_date = pd.to_datetime(forecast_df["valid_date"]).max()
    target_mask = (pd.to_datetime(forecast_df["valid_date"]) >= max_date - pd.Timedelta(days=10)).to_numpy()
    scored = score_forecast_dataframe(
        loaded["model_bundle"],
        forecast_df,
        observed_df,
        municipalities_df,
        model_version=loaded["model_version"],
        target_mask=target_mask,
    )

    assert not scored.empty
    # Quantile monotonicity on 100% of predictions.
    assert (scored["pred_tmax_p10"] <= scored["pred_tmax_p50"]).all()
    assert (scored["pred_tmax_p50"] <= scored["pred_tmax_p90"]).all()
    assert (scored["pred_tmin_p10"] <= scored["pred_tmin_p50"]).all()
    assert (scored["pred_tmin_p50"] <= scored["pred_tmin_p90"]).all()

    # Calibrated rain probabilities strictly bounded in [0, 1].
    assert (scored["calibrated_rain_prob"] >= 0.0).all()
    assert (scored["calibrated_rain_prob"] <= 1.0).all()

    # Valid alert tiers.
    assert set(scored["alert_level"].unique()).issubset({"NORMAL", "ADVISORY", "WARNING_P0"})

    # Tampering with metrics.json must trigger ModelIntegrityError on load.
    metrics_path = bundle_dir / "metrics.json"
    metrics_path.write_text(metrics_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ModelIntegrityError, match="SHA-256 mismatch"):
        load_latest_model_bundle(artifacts_dir=tmp_path)


def test_metrics_analytical_values() -> None:
    y_true = np.array([10.0, 20.0, 30.0, 40.0])
    y_pred = np.array([12.0, 18.0, 32.0, 38.0])

    assert mae(y_true, y_pred) == pytest.approx(2.0)
    assert rmse(y_true, y_pred) == pytest.approx(2.0)
    assert bias(y_true, y_pred) == pytest.approx(0.0)
    assert pinball_loss(y_true, y_pred, alpha=0.5) == pytest.approx(1.0)

    p10 = np.array([9.0, 19.0, 29.0, 41.0])
    p90 = np.array([15.0, 25.0, 35.0, 45.0])
    assert interval_coverage_80(y_true, p10, p90) == pytest.approx(0.75)

    y_bin = np.array([0.0, 0.0, 1.0, 1.0])
    p_bin = np.array([0.1, 0.2, 0.8, 0.9])
    assert brier_score(y_bin, p_bin) == pytest.approx(0.025)
    assert log_loss(y_bin, p_bin) == pytest.approx(
        -(np.log(0.9) + np.log(0.8) + np.log(0.8) + np.log(0.9)) / 4.0
    )
    ece = expected_calibration_error(y_bin, p_bin, n_bins=10)
    assert 0.0 <= ece <= 1.0

    curve = reliability_curve(y_bin, p_bin, n_bins=10)
    assert len(curve["bin_edges"]) == 11
    assert len(curve["mean_pred_prob"]) == 10
    assert len(curve["fraction_positive"]) == 10
    assert sum(curve["count"]) == 4


def test_alert_level_classification_thresholds() -> None:
    assert classify_alert_level(precip_mm=25.0, tmax_c=28.0, tmin_c=15.0, wind_gust_kmh=20.0, calibrated_rain_prob=0.5) == "WARNING_P0"
    assert classify_alert_level(precip_mm=0.0, tmax_c=38.0, tmin_c=20.0, wind_gust_kmh=10.0, calibrated_rain_prob=0.1) == "WARNING_P0"
    assert classify_alert_level(precip_mm=0.0, tmax_c=15.0, tmin_c=2.0, wind_gust_kmh=10.0, calibrated_rain_prob=0.1) == "WARNING_P0"
    assert classify_alert_level(precip_mm=0.0, tmax_c=25.0, tmin_c=15.0, wind_gust_kmh=50.0, calibrated_rain_prob=0.1) == "WARNING_P0"

    assert classify_alert_level(precip_mm=12.0, tmax_c=25.0, tmin_c=15.0, wind_gust_kmh=20.0, calibrated_rain_prob=0.5) == "ADVISORY"
    assert classify_alert_level(precip_mm=2.0, tmax_c=25.0, tmin_c=15.0, wind_gust_kmh=20.0, calibrated_rain_prob=0.70) == "ADVISORY"
    assert classify_alert_level(precip_mm=0.0, tmax_c=35.0, tmin_c=20.0, wind_gust_kmh=20.0, calibrated_rain_prob=0.1) == "ADVISORY"
    assert classify_alert_level(precip_mm=0.0, tmax_c=25.0, tmin_c=15.0, wind_gust_kmh=35.0, calibrated_rain_prob=0.1) == "ADVISORY"

    assert classify_alert_level(precip_mm=0.0, tmax_c=28.0, tmin_c=14.0, wind_gust_kmh=20.0, calibrated_rain_prob=0.2) == "NORMAL"
