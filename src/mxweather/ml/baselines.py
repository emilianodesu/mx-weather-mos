"""Baseline forecast generators: raw_nwp, persistence, and 30-day climatology."""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_temperature_baselines(df: pd.DataFrame, target: str) -> dict[str, np.ndarray]:
    """Return raw_nwp, persistence, and climatology_30d arrays for tmax or tmin."""
    if target not in {"tmax", "tmin"}:
        raise ValueError(f"Unsupported temperature target: {target!r}. Expected 'tmax' or 'tmin'.")

    raw_col = f"{target}_c"
    pers_col = f"persistence_{target}_c"
    clim_col = f"obs_{target}_clim_30d"

    return {
        "raw_nwp": df[raw_col].to_numpy(dtype=np.float64),
        "persistence": (
            df[pers_col].to_numpy(dtype=np.float64)
            if pers_col in df.columns
            else np.full(len(df), np.nan, dtype=np.float64)
        ),
        "climatology_30d": (
            df[clim_col].to_numpy(dtype=np.float64)
            if clim_col in df.columns
            else np.full(len(df), np.nan, dtype=np.float64)
        ),
    }


def compute_rain_baselines(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Return raw_nwp, persistence, and climatology_30d rain probability arrays in [0, 1]."""
    raw_prob = (
        np.clip(df["precip_prob_pct"].to_numpy(dtype=np.float64) / 100.0, 0.0, 1.0)
        if "precip_prob_pct" in df.columns
        else np.full(len(df), np.nan, dtype=np.float64)
    )
    persistence_prob = (
        np.clip(df["persistence_rain_flag"].to_numpy(dtype=np.float64), 0.0, 1.0)
        if "persistence_rain_flag" in df.columns
        else np.full(len(df), np.nan, dtype=np.float64)
    )
    climatology_prob = (
        np.clip(df["obs_rain_rate_30d"].to_numpy(dtype=np.float64), 0.0, 1.0)
        if "obs_rain_rate_30d" in df.columns
        else np.full(len(df), np.nan, dtype=np.float64)
    )
    return {
        "raw_nwp": raw_prob,
        "persistence": persistence_prob,
        "climatology_30d": climatology_prob,
    }


def compute_all_baselines(df: pd.DataFrame) -> dict[str, dict[str, np.ndarray]]:
    """Compute all baselines for tmax, tmin, and rain occurrence."""
    return {
        "tmax": compute_temperature_baselines(df, "tmax"),
        "tmin": compute_temperature_baselines(df, "tmin"),
        "rain": compute_rain_baselines(df),
    }
