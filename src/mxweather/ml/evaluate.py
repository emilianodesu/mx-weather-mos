"""Forecast verification metrics, reliability curves, and sliced evaluation."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from mxweather.ml.baselines import compute_all_baselines


def _valid_pair(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    yt = np.asarray(y_true, dtype=np.float64)
    yp = np.asarray(y_pred, dtype=np.float64)
    mask = np.isfinite(yt) & np.isfinite(yp)
    return yt[mask], yp[mask]


def _clean_float(val: float | None) -> float | None:
    if val is None or (isinstance(val, float) and not math.isfinite(val)):
        return None
    return float(val)


def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt, yp = _valid_pair(y_true, y_pred)
    if len(yt) == 0:
        return float("nan")
    return float(np.mean(np.abs(yp - yt)))


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt, yp = _valid_pair(y_true, y_pred)
    if len(yt) == 0:
        return float("nan")
    return float(np.sqrt(np.mean((yp - yt) ** 2)))


def bias(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Mean signed error: mean(y_pred - y_true)."""
    yt, yp = _valid_pair(y_true, y_pred)
    if len(yt) == 0:
        return float("nan")
    return float(np.mean(yp - yt))


def pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, alpha: float) -> float:
    """Quantile (pinball) loss for quantile level `alpha` in (0, 1)."""
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    yt, yp = _valid_pair(y_true, y_pred)
    if len(yt) == 0:
        return float("nan")
    diff = yt - yp
    return float(np.mean(np.maximum(alpha * diff, (alpha - 1.0) * diff)))


def interval_coverage_80(
    y_true: np.ndarray,
    y_p10: np.ndarray,
    y_p90: np.ndarray,
) -> float:
    """Empirical coverage of the nominal 80% [p10, p90] prediction interval."""
    yt = np.asarray(y_true, dtype=np.float64)
    lo = np.asarray(y_p10, dtype=np.float64)
    hi = np.asarray(y_p90, dtype=np.float64)
    mask = np.isfinite(yt) & np.isfinite(lo) & np.isfinite(hi)
    if not np.any(mask):
        return float("nan")
    return float(np.mean((yt[mask] >= lo[mask]) & (yt[mask] <= hi[mask])))


def brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    """Brier score for binary rain occurrence probabilities."""
    yt, yp = _valid_pair(y_true, y_prob)
    if len(yt) == 0:
        return float("nan")
    yp = np.clip(yp, 0.0, 1.0)
    return float(np.mean((yp - yt) ** 2))


def log_loss(y_true: np.ndarray, y_prob: np.ndarray, eps: float = 1e-15) -> float:
    """Binary cross-entropy (log loss) with safe clipping."""
    yt, yp = _valid_pair(y_true, y_prob)
    if len(yt) == 0:
        return float("nan")
    yp = np.clip(yp, eps, 1.0 - eps)
    return float(-np.mean(yt * np.log(yp) + (1.0 - yt) * np.log(1.0 - yp)))


def reliability_curve(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int = 10,
) -> dict[str, list[Any]]:
    """Compute reliability diagram bin statistics on [0, 1]."""
    yt, yp = _valid_pair(y_true, y_prob)
    yp = np.clip(yp, 0.0, 1.0)
    edges = np.linspace(0.0, 1.0, n_bins + 1)

    mean_pred_prob: list[float | None] = []
    fraction_positive: list[float | None] = []
    counts: list[int] = []

    for i in range(n_bins):
        lo = edges[i]
        hi = edges[i + 1]
        if i == n_bins - 1:
            in_bin = (yp >= lo) & (yp <= hi)
        else:
            in_bin = (yp >= lo) & (yp < hi)
        cnt = int(np.sum(in_bin))
        counts.append(cnt)
        if cnt == 0:
            mean_pred_prob.append(None)
            fraction_positive.append(None)
        else:
            mean_pred_prob.append(float(np.mean(yp[in_bin])))
            fraction_positive.append(float(np.mean(yt[in_bin])))

    return {
        "bin_edges": [float(e) for e in edges],
        "mean_pred_prob": mean_pred_prob,
        "fraction_positive": fraction_positive,
        "count": counts,
    }


def expected_calibration_error(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int = 10,
) -> float:
    """Compute Expected Calibration Error (ECE) over `n_bins` equal-width bins."""
    yt, yp = _valid_pair(y_true, y_prob)
    total = len(yt)
    if total == 0:
        return float("nan")

    curve = reliability_curve(yt, yp, n_bins=n_bins)
    ece = 0.0
    for mp, fp, cnt in zip(
        curve["mean_pred_prob"],
        curve["fraction_positive"],
        curve["count"],
        strict=True,
    ):
        if cnt > 0 and mp is not None and fp is not None:
            ece += (cnt / total) * abs(fp - mp)
    return float(ece)


def _evaluate_segment(
    seg_df: pd.DataFrame,
    *,
    model_version: str,
    split_name: str,
    segment_key: str,
) -> list[dict[str, Any]]:
    """Compute all temperature and rain evaluation rows for one slice."""
    if seg_df.empty:
        return []

    baselines = compute_all_baselines(seg_df)
    rows: list[dict[str, Any]] = []

    for target in ("tmax", "tmin"):
        y_true = seg_df[f"obs_{target}_c"].to_numpy(dtype=np.float64)
        p10 = seg_df[f"pred_{target}_p10"].to_numpy(dtype=np.float64)
        p50 = seg_df[f"pred_{target}_p50"].to_numpy(dtype=np.float64)
        p90 = seg_df[f"pred_{target}_p90"].to_numpy(dtype=np.float64)

        b = baselines[target]
        valid_n = int(np.sum(np.isfinite(y_true) & np.isfinite(p50)))
        if valid_n == 0:
            continue

        point_metrics = [
            ("mae", mae),
            ("rmse", rmse),
            ("bias", bias),
        ]
        for mname, fn in point_metrics:
            ml_val = _clean_float(fn(y_true, p50))
            if ml_val is None:
                continue
            rows.append(
                {
                    "model_version": model_version,
                    "split_name": split_name,
                    "segment_key": segment_key,
                    "metric_name": f"{target}_{mname}",
                    "raw_baseline_val": _clean_float(fn(y_true, b["raw_nwp"])),
                    "ml_model_val": ml_val,
                    "persistence_val": _clean_float(fn(y_true, b["persistence"])),
                    "climatology_val": _clean_float(fn(y_true, b["climatology_30d"])),
                    "sample_count": valid_n,
                }
            )

        # Quantile pinball losses and 80% interval coverage.
        for q_label, alpha, pred_q in (
            ("pinball_p10", 0.10, p10),
            ("pinball_p50", 0.50, p50),
            ("pinball_p90", 0.90, p90),
        ):
            ml_q = _clean_float(pinball_loss(y_true, pred_q, alpha))
            if ml_q is None:
                continue
            rows.append(
                {
                    "model_version": model_version,
                    "split_name": split_name,
                    "segment_key": segment_key,
                    "metric_name": f"{target}_{q_label}",
                    "raw_baseline_val": (
                        _clean_float(pinball_loss(y_true, b["raw_nwp"], alpha))
                        if q_label == "pinball_p50"
                        else None
                    ),
                    "ml_model_val": ml_q,
                    "persistence_val": (
                        _clean_float(pinball_loss(y_true, b["persistence"], alpha))
                        if q_label == "pinball_p50"
                        else None
                    ),
                    "climatology_val": (
                        _clean_float(pinball_loss(y_true, b["climatology_30d"], alpha))
                        if q_label == "pinball_p50"
                        else None
                    ),
                    "sample_count": valid_n,
                }
            )

        cov_80 = _clean_float(interval_coverage_80(y_true, p10, p90))
        if cov_80 is not None:
            rows.append(
                {
                    "model_version": model_version,
                    "split_name": split_name,
                    "segment_key": segment_key,
                    "metric_name": f"{target}_coverage_80",
                    "raw_baseline_val": None,
                    "ml_model_val": cov_80,
                    "persistence_val": None,
                    "climatology_val": None,
                    "sample_count": valid_n,
                }
            )

    # Rain occurrence probability metrics (obs_precip_mm >= 1.0).
    if "obs_rain_flag" in seg_df.columns and "calibrated_rain_prob" in seg_df.columns:
        y_rain = seg_df["obs_rain_flag"].to_numpy(dtype=np.float64)
        p_ml = seg_df["calibrated_rain_prob"].to_numpy(dtype=np.float64)
        rb = baselines["rain"]
        rain_n = int(np.sum(np.isfinite(y_rain) & np.isfinite(p_ml)))
        if rain_n > 0:
            for mname, fn in (
                ("rain_brier", brier_score),
                ("rain_log_loss", log_loss),
                ("rain_ece", expected_calibration_error),
            ):
                ml_val = _clean_float(fn(y_rain, p_ml))
                if ml_val is None:
                    continue
                rows.append(
                    {
                        "model_version": model_version,
                        "split_name": split_name,
                        "segment_key": segment_key,
                        "metric_name": mname,
                        "raw_baseline_val": _clean_float(fn(y_rain, rb["raw_nwp"])),
                        "ml_model_val": ml_val,
                        "persistence_val": _clean_float(fn(y_rain, rb["persistence"])),
                        "climatology_val": _clean_float(fn(y_rain, rb["climatology_30d"])),
                        "sample_count": rain_n,
                    }
                )

    return rows


def evaluate_predictions(
    eval_df: pd.DataFrame,
    *,
    model_version: str,
    split_name: str,
    n_bins: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate predictions overall and sliced by elevation_band and season_label.

    Returns:
        rows: list of dicts matching `MlModelEvaluation` schema.
        calibration_payload: reliability curve JSON for calibrated vs raw rain probabilities.
    """
    rows: list[dict[str, Any]] = []
    rows.extend(
        _evaluate_segment(
            eval_df,
            model_version=model_version,
            split_name=split_name,
            segment_key="overall",
        )
    )

    if "elevation_band" in eval_df.columns:
        for band, grp in eval_df.groupby("elevation_band", sort=True):
            rows.extend(
                _evaluate_segment(
                    grp,
                    model_version=model_version,
                    split_name=split_name,
                    segment_key=f"elevation:{band}",
                )
            )

    if "season_label" in eval_df.columns:
        for season, grp in eval_df.groupby("season_label", sort=True):
            rows.extend(
                _evaluate_segment(
                    grp,
                    model_version=model_version,
                    split_name=split_name,
                    segment_key=f"season:{season}",
                )
            )

    calibration_payload: dict[str, Any] = {
        "model_version": model_version,
        "split_name": split_name,
        "n_bins": n_bins,
        "calibrated": None,
        "raw_nwp": None,
    }
    if "obs_rain_flag" in eval_df.columns and "calibrated_rain_prob" in eval_df.columns:
        y_rain = eval_df["obs_rain_flag"].to_numpy(dtype=np.float64)
        p_cal = eval_df["calibrated_rain_prob"].to_numpy(dtype=np.float64)
        calibration_payload["calibrated"] = reliability_curve(y_rain, p_cal, n_bins=n_bins)
        if "precip_prob_pct" in eval_df.columns:
            p_raw = np.clip(eval_df["precip_prob_pct"].to_numpy(dtype=np.float64) / 100.0, 0.0, 1.0)
            calibration_payload["raw_nwp"] = reliability_curve(y_rain, p_raw, n_bins=n_bins)

    return rows, calibration_payload
