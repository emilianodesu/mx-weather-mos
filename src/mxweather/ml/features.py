"""Pure Point-in-Time (PIT) feature engineering with strict zero-leakage guarantees."""

from __future__ import annotations

import math
from typing import Final

import numpy as np
import pandas as pd

FEATURE_COLUMNS: Final[list[str]] = [
    "tmax_c",
    "tmin_c",
    "diurnal_range",
    "precip_mm",
    "precip_prob_pct",
    "wind_speed_kmh",
    "wind_gust_kmh",
    "lead_day",
    "lat",
    "lon",
    "elevation_m",
    "doy_sin",
    "doy_cos",
    "bias_tmax_7d",
    "bias_tmax_30d",
    "bias_tmin_7d",
    "bias_tmin_30d",
    "obs_tmax_clim_30d",
    "obs_tmin_clim_30d",
    "obs_precip_clim_30d",
    "obs_rain_rate_30d",
]

AUX_BASELINE_COLUMNS: Final[list[str]] = [
    "persistence_tmax_c",
    "persistence_tmin_c",
    "persistence_rain_flag",
]

KEY_COLS: Final[list[str]] = ["state_id", "municipality_id"]


def elevation_band(elev_m: float | None) -> str:
    """Classify municipality elevation (meters) into Mexico climate bands."""
    if elev_m is None or (isinstance(elev_m, float) and math.isnan(elev_m)):
        return "unknown"
    val = float(elev_m)
    if val < 500.0:
        return "coastal_<500m"
    if val <= 1800.0:
        return "midland_500_1800m"
    return "highland_>1800m"


def season_label(month: int) -> str:
    """Classify calendar month into Mexico rainy (May-Oct) or dry (Nov-Apr) season."""
    m = int(month)
    if 5 <= m <= 10:
        return "rainy_may_oct"
    return "dry_nov_apr"


def _normalize_observed(observed_df: pd.DataFrame) -> pd.DataFrame:
    """Return a clean, deduplicated observed DataFrame with canonical column names."""
    if observed_df.empty:
        return pd.DataFrame(
            columns=[
                "state_id",
                "municipality_id",
                "obs_date",
                "obs_tmax_c",
                "obs_tmin_c",
                "obs_precip_mm",
            ]
        )

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

    obs["obs_date"] = pd.to_datetime(obs["obs_date"]).dt.normalize().astype("datetime64[ns]")
    for col in ("obs_tmax_c", "obs_tmin_c", "obs_precip_mm"):
        if col not in obs.columns:
            obs[col] = np.nan
        obs[col] = pd.to_numeric(obs[col], errors="coerce").astype("float64")

    obs["state_id"] = obs["state_id"].astype("int64")
    obs["municipality_id"] = obs["municipality_id"].astype("int64")

    sort_cols = ["state_id", "municipality_id", "obs_date"]
    if "source_provider" in obs.columns:
        sort_cols.append("source_provider")
    obs = obs.sort_values(sort_cols).drop_duplicates(
        subset=["state_id", "municipality_id", "obs_date"], keep="first"
    )
    return obs[
        [
            "state_id",
            "municipality_id",
            "obs_date",
            "obs_tmax_c",
            "obs_tmin_c",
            "obs_precip_mm",
        ]
    ].reset_index(drop=True)


def _build_observed_rolling_history(
    forecast_df: pd.DataFrame,
    obs: pd.DataFrame,
) -> pd.DataFrame:
    """Compute strictly backward-looking rolling stats indexed by obs_date.

    For each observed date `d`, every rolling window (`7D`, `30D`) only uses
    rows with date `<= d`. Downstream `merge_asof` joins each forecast row to
    `obs_date <= valid_date - max(1, lead_day) - 1`, guaranteeing strict
    zero-leakage (`obs_date < valid_date - max(1, lead_day)`).
    """
    out_cols = [
        "state_id",
        "municipality_id",
        "obs_date",
        "bias_tmax_7d",
        "bias_tmax_30d",
        "bias_tmin_7d",
        "bias_tmin_30d",
        "obs_tmax_clim_30d",
        "obs_tmin_clim_30d",
        "obs_precip_clim_30d",
        "obs_rain_rate_30d",
        "persistence_tmax_c",
        "persistence_tmin_c",
        "persistence_rain_flag",
    ]
    if obs.empty:
        return pd.DataFrame(columns=out_cols)

    # Pair historical forecasts onto observed dates to compute historical NWP bias.
    hist_fcst = forecast_df[
        [c for c in ["state_id", "municipality_id", "valid_date", "issued_at", "tmax_c", "tmin_c"] if c in forecast_df.columns]
    ].copy()
    hist_fcst["state_id"] = hist_fcst["state_id"].astype("int64")
    hist_fcst["municipality_id"] = hist_fcst["municipality_id"].astype("int64")
    hist_fcst["obs_date"] = pd.to_datetime(hist_fcst["valid_date"]).dt.normalize().astype("datetime64[ns]")
    hist_fcst["hist_fcst_tmax_c"] = pd.to_numeric(hist_fcst["tmax_c"], errors="coerce").astype("float64")
    hist_fcst["hist_fcst_tmin_c"] = pd.to_numeric(hist_fcst["tmin_c"], errors="coerce").astype("float64")

    if "issued_at" in hist_fcst.columns:
        hist_fcst = hist_fcst.sort_values(["state_id", "municipality_id", "obs_date", "issued_at"])
    else:
        hist_fcst = hist_fcst.sort_values(["state_id", "municipality_id", "obs_date"])
    hist_fcst = hist_fcst.drop_duplicates(
        subset=["state_id", "municipality_id", "obs_date"], keep="last"
    )[["state_id", "municipality_id", "obs_date", "hist_fcst_tmax_c", "hist_fcst_tmin_c"]]

    merged = obs.merge(
        hist_fcst,
        on=["state_id", "municipality_id", "obs_date"],
        how="left",
    )
    merged["bias_tmax"] = merged["hist_fcst_tmax_c"] - merged["obs_tmax_c"]
    merged["bias_tmin"] = merged["hist_fcst_tmin_c"] - merged["obs_tmin_c"]
    merged["obs_rain_flag"] = np.where(
        merged["obs_precip_mm"].notna(),
        (merged["obs_precip_mm"] >= 1.0).astype("float64"),
        np.nan,
    )

    frames: list[pd.DataFrame] = []
    for (state_id, mun_id), grp in merged.groupby(["state_id", "municipality_id"], sort=False):
        g = grp.sort_values("obs_date").set_index("obs_date")
        rolled = pd.DataFrame(index=g.index)
        rolled["state_id"] = int(state_id)
        rolled["municipality_id"] = int(mun_id)
        rolled["bias_tmax_7d"] = g["bias_tmax"].rolling("7D", min_periods=1).mean()
        rolled["bias_tmax_30d"] = g["bias_tmax"].rolling("30D", min_periods=1).mean()
        rolled["bias_tmin_7d"] = g["bias_tmin"].rolling("7D", min_periods=1).mean()
        rolled["bias_tmin_30d"] = g["bias_tmin"].rolling("30D", min_periods=1).mean()
        rolled["obs_tmax_clim_30d"] = g["obs_tmax_c"].rolling("30D", min_periods=1).mean()
        rolled["obs_tmin_clim_30d"] = g["obs_tmin_c"].rolling("30D", min_periods=1).mean()
        rolled["obs_precip_clim_30d"] = g["obs_precip_mm"].rolling("30D", min_periods=1).mean()
        rolled["obs_rain_rate_30d"] = g["obs_rain_flag"].rolling("30D", min_periods=1).mean()
        rolled["persistence_tmax_c"] = g["obs_tmax_c"]
        rolled["persistence_tmin_c"] = g["obs_tmin_c"]
        rolled["persistence_rain_flag"] = g["obs_rain_flag"]
        frames.append(rolled.reset_index())

    history = pd.concat(frames, ignore_index=True)
    return history[out_cols].sort_values(["obs_date", "state_id", "municipality_id"]).reset_index(drop=True)


def build_pit_features(
    forecast_df: pd.DataFrame,
    observed_df: pd.DataFrame,
    municipalities_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build strictly Point-in-Time (PIT) features for forecast rows.

    All lagged bias, rolling climatology, and persistence features are computed
    strictly from observations with:
        obs_date <= valid_date - max(1, lead_day) - 1
        (i.e. strictly `obs_date < valid_date - max(1, lead_day)`)
    and, when `issued_at` is present, additionally `obs_date < issued_at::date`.

    Returns a DataFrame preserving the original row order of `forecast_df` with
    metadata columns, `FEATURE_COLUMNS`, `AUX_BASELINE_COLUMNS`, `elevation_band`,
    and `season_label`.
    """
    if forecast_df.empty:
        empty_cols = [
            "state_id",
            "municipality_id",
            "valid_date",
            *FEATURE_COLUMNS,
            *AUX_BASELINE_COLUMNS,
            "elevation_band",
            "season_label",
        ]
        return pd.DataFrame(columns=empty_cols)

    df = forecast_df.copy().reset_index(drop=True)
    df["_orig_idx"] = np.arange(len(df), dtype=np.int64)
    df["state_id"] = df["state_id"].astype("int64")
    df["municipality_id"] = df["municipality_id"].astype("int64")
    df["valid_date"] = pd.to_datetime(df["valid_date"]).dt.normalize().astype("datetime64[ns]")

    if "lead_day" not in df.columns:
        df["lead_day"] = 0
    df["lead_day"] = pd.to_numeric(df["lead_day"], errors="coerce").fillna(0).astype("float64")

    # Join static municipality metadata (lat, lon, elevation_m).
    mun = municipalities_df.copy()
    mun["state_id"] = mun["state_id"].astype("int64")
    mun["municipality_id"] = mun["municipality_id"].astype("int64")
    meta_cols = [c for c in ["lat", "lon", "elevation_m"] if c in mun.columns]
    for col in meta_cols:
        if col in df.columns:
            df = df.drop(columns=[col])
    df = df.merge(
        mun[["state_id", "municipality_id", *meta_cols]].drop_duplicates(
            subset=["state_id", "municipality_id"], keep="first"
        ),
        on=["state_id", "municipality_id"],
        how="left",
    )

    # Cast raw forecast and geospatial features to float64.
    raw_numeric_cols = [
        "tmax_c",
        "tmin_c",
        "precip_mm",
        "precip_prob_pct",
        "wind_speed_kmh",
        "wind_gust_kmh",
        "lat",
        "lon",
        "elevation_m",
    ]
    for col in raw_numeric_cols:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    df["diurnal_range"] = df["tmax_c"] - df["tmin_c"]

    # Cyclical day-of-year encoding.
    doy = df["valid_date"].dt.day_of_year.astype("float64")
    angle = 2.0 * np.pi * doy / 365.25
    df["doy_sin"] = np.sin(angle)
    df["doy_cos"] = np.cos(angle)

    # Strict zero-leakage cutoff: obs_date < valid_date - max(1, lead_day)
    # Equivalent for integer days: obs_date <= valid_date - max(1, lead_day) - 1.
    effective_lag_days = np.maximum(1, np.floor(df["lead_day"]).astype("int64")) + 1
    asof_cutoff = df["valid_date"] - pd.to_timedelta(effective_lag_days, unit="D")

    if "issued_at" in df.columns:
        issued_dt = pd.to_datetime(df["issued_at"], utc=True, errors="coerce")
        issued_prev_day = (
            issued_dt.dt.tz_localize(None).dt.normalize() - pd.Timedelta(days=1)
        ).astype("datetime64[ns]")
        asof_cutoff = pd.Series(
            np.where(issued_prev_day.notna(), np.minimum(asof_cutoff, issued_prev_day), asof_cutoff),
            index=df.index,
        )
        asof_cutoff = pd.to_datetime(asof_cutoff).astype("datetime64[ns]")

    df["_asof_cutoff"] = asof_cutoff.astype("datetime64[ns]")

    obs_clean = _normalize_observed(observed_df)
    history = _build_observed_rolling_history(df, obs_clean)

    lag_cols = [
        "bias_tmax_7d",
        "bias_tmax_30d",
        "bias_tmin_7d",
        "bias_tmin_30d",
        "obs_tmax_clim_30d",
        "obs_tmin_clim_30d",
        "obs_precip_clim_30d",
        "obs_rain_rate_30d",
        *AUX_BASELINE_COLUMNS,
    ]

    if history.empty:
        for col in lag_cols:
            df[col] = np.nan
    else:
        left_sorted = df.sort_values(["_asof_cutoff", "state_id", "municipality_id"]).reset_index(drop=True)
        right_sorted = history.sort_values(["obs_date", "state_id", "municipality_id"]).reset_index(drop=True)
        joined = pd.merge_asof(
            left_sorted,
            right_sorted,
            left_on="_asof_cutoff",
            right_on="obs_date",
            by=["state_id", "municipality_id"],
            direction="backward",
        )
        df = joined.drop(columns=["obs_date"], errors="ignore")

    df = df.sort_values("_orig_idx").drop(columns=["_orig_idx", "_asof_cutoff"]).reset_index(drop=True)

    # Categorical evaluation slices.
    df["elevation_band"] = df["elevation_m"].map(elevation_band)
    df["season_label"] = df["valid_date"].dt.month.map(season_label)

    # Ensure all feature and auxiliary columns have consistent float64 dtype.
    for col in [*FEATURE_COLUMNS, *AUX_BASELINE_COLUMNS]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    return df
