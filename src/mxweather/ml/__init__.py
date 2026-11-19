"""AI/ML package for MOS bias correction, quantile forecasting, and rain calibration."""

from mxweather.ml.features import (
    FEATURE_COLUMNS,
    build_pit_features,
    elevation_band,
    season_label,
)
from mxweather.ml.registry import (
    ModelIntegrityError,
    load_latest_model_bundle,
    load_model_bundle,
    save_model_bundle,
    verify_manifest,
)

__all__ = [
    "FEATURE_COLUMNS",
    "ModelIntegrityError",
    "build_pit_features",
    "elevation_band",
    "load_latest_model_bundle",
    "load_model_bundle",
    "save_model_bundle",
    "season_label",
    "verify_manifest",
]
