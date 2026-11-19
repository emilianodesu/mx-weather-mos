"""Zero-leakage and temporal walk-forward split verification tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mxweather.ml.features import (
    AUX_BASELINE_COLUMNS,
    FEATURE_COLUMNS,
    build_pit_features,
    elevation_band,
    season_label,
)
from mxweather.ml.train import generate_walk_forward_splits


def _make_synthetic_pit_frames(n_days: int = 90) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(123)
    dates = pd.date_range("2025-01-01", periods=n_days, freq="D")

    municipalities = pd.DataFrame(
        [
            {"state_id": 9, "municipality_id": 15, "lat": 19.4326, "lon": -99.1332, "elevation_m": 2240.0},
            {"state_id": 31, "municipality_id": 50, "lat": 20.9674, "lon": -89.5926, "elevation_m": 10.0},
        ]
    )

    fcst_rows = []
    obs_rows = []
    for _, mun in municipalities.iterrows():
        base_tmax = 24.0 if mun["elevation_m"] > 1000 else 33.0
        for idx, d in enumerate(dates):
            obs_tmax = base_tmax + 3.0 * np.sin(idx / 12.0) + rng.normal(0.0, 0.8)
            obs_tmin = obs_tmax - 11.0 + rng.normal(0.0, 0.5)
            obs_precip = float(max(0.0, rng.exponential(2.5) - 1.5))
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
            for lead_day in (0, 1, 2, 3):
                issued_at = pd.Timestamp(d - pd.Timedelta(days=lead_day), tz="UTC")
                fcst_rows.append(
                    {
                        "state_id": int(mun["state_id"]),
                        "municipality_id": int(mun["municipality_id"]),
                        "issued_at": issued_at,
                        "valid_date": d,
                        "lead_day": lead_day,
                        "tmax_c": obs_tmax + 1.5 + rng.normal(0.0, 0.4),
                        "tmin_c": obs_tmin - 0.8 + rng.normal(0.0, 0.4),
                        "precip_mm": max(0.0, obs_precip + rng.normal(0.0, 0.5)),
                        "precip_prob_pct": int(np.clip(obs_precip * 12.0, 0, 100)),
                        "wind_speed_kmh": 14.0 + rng.uniform(0.0, 8.0),
                        "wind_gust_kmh": 22.0 + rng.uniform(0.0, 12.0),
                    }
                )

    return pd.DataFrame(fcst_rows), pd.DataFrame(obs_rows), municipalities


def test_future_perturbation_zero_leakage() -> None:
    """Mutating observed targets on dates >= cutoff_date by +999.0 must produce
    bit-for-bit identical PIT features for all forecast rows with valid_date <= cutoff_date.
    """
    forecast_df, observed_df, municipalities_df = _make_synthetic_pit_frames(n_days=90)
    cutoff_date = pd.Timestamp("2025-02-20")

    baseline_features = build_pit_features(forecast_df, observed_df, municipalities_df)

    perturbed_obs = observed_df.copy()
    future_mask = pd.to_datetime(perturbed_obs["obs_date"]) >= cutoff_date
    assert future_mask.any()
    perturbed_obs.loc[future_mask, "tmax_c"] += 999.0
    perturbed_obs.loc[future_mask, "tmin_c"] += 999.0
    perturbed_obs.loc[future_mask, "precip_mm"] += 999.0

    perturbed_features = build_pit_features(forecast_df, perturbed_obs, municipalities_df)

    check_cols = [*FEATURE_COLUMNS, *AUX_BASELINE_COLUMNS]
    pre_cutoff_mask = baseline_features["valid_date"] <= cutoff_date
    assert pre_cutoff_mask.sum() > 0

    # Bit-for-bit equality for all rows with valid_date <= cutoff_date.
    pd.testing.assert_frame_equal(
        baseline_features.loc[pre_cutoff_mask, check_cols].reset_index(drop=True),
        perturbed_features.loc[pre_cutoff_mask, check_cols].reset_index(drop=True),
        check_exact=True,
    )

    # Sanity check: rows strictly after cutoff_date + max_lead + 1 MUST reflect the perturbation,
    # proving the rolling features actually consume historical observations.
    well_after_mask = baseline_features["valid_date"] >= cutoff_date + pd.Timedelta(days=10)
    assert well_after_mask.any()
    diff = (
        perturbed_features.loc[well_after_mask, "obs_tmax_clim_30d"]
        - baseline_features.loc[well_after_mask, "obs_tmax_clim_30d"]
    ).abs()
    assert (diff > 50.0).all()


def test_walk_forward_splits_strict_temporal_ordering() -> None:
    """Verify 4 expanding temporal folds + holdout with zero date overlap or shuffling."""
    dates = pd.date_range("2024-01-01", periods=365, freq="D").repeat(4)
    cv_splits, holdout = generate_walk_forward_splits(dates, n_folds=4, holdout_fraction=0.15)

    assert len(cv_splits) == 4

    prev_train_end = None
    prev_val_end = None
    for idx, split in enumerate(cv_splits, start=1):
        assert split.name == f"cv_fold_{idx}"
        assert split.train_mask.any()
        assert split.val_mask.any()
        assert not np.any(split.train_mask & split.val_mask)
        assert split.train_end < split.val_start

        if prev_train_end is not None:
            assert split.train_end > prev_train_end
        if prev_val_end is not None:
            assert split.val_start > prev_val_end
        prev_train_end = split.train_end
        prev_val_end = split.val_end

    # Holdout must be strictly after all CV folds.
    assert holdout.name == "holdout"
    assert holdout.train_end < holdout.val_start
    assert holdout.val_start > cv_splits[-1].val_end
    assert not np.any(holdout.train_mask & holdout.val_mask)


def test_elevation_band_and_season_label_helpers() -> None:
    assert elevation_band(10.0) == "coastal_<500m"
    assert elevation_band(499.9) == "coastal_<500m"
    assert elevation_band(500.0) == "midland_500_1800m"
    assert elevation_band(1800.0) == "midland_500_1800m"
    assert elevation_band(2240.0) == "highland_>1800m"
    assert elevation_band(None) == "unknown"

    for rainy_month in (5, 6, 7, 8, 9, 10):
        assert season_label(rainy_month) == "rainy_may_oct"
    for dry_month in (11, 12, 1, 2, 3, 4):
        assert season_label(dry_month) == "dry_nov_apr"


def test_walk_forward_raises_on_insufficient_dates() -> None:
    tiny_dates = pd.date_range("2025-01-01", periods=5, freq="D")
    with pytest.raises(ValueError, match="unique dates"):
        generate_walk_forward_splits(tiny_dates, n_folds=4)
