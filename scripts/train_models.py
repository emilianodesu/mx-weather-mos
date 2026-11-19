"""CLI entrypoint to train MOS LightGBM models, log evaluation metrics, and save bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mxweather.db.session import SessionLocal
from mxweather.ml.train import (
    load_training_data_from_db,
    train_and_evaluate,
    write_evaluation_rows,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train MOS bias-correction & calibrated rain models from Postgres data."
    )
    parser.add_argument(
        "--model-version",
        type=str,
        default=None,
        help="Optional explicit model version tag (defaults to UTC timestamp tag).",
    )
    parser.add_argument(
        "--n-folds",
        type=int,
        default=4,
        help="Number of expanding temporal walk-forward CV folds (default: 4).",
    )
    parser.add_argument(
        "--holdout-fraction",
        type=float,
        default=0.15,
        help="Fraction of trailing dates reserved for final holdout (default: 0.15).",
    )
    parser.add_argument(
        "--n-estimators",
        type=int,
        default=200,
        help="LightGBM boosting iterations (default: 200).",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=None,
        help="Override artifacts directory (defaults to MXWEATHER_ARTIFACTS_DIR).",
    )
    parser.add_argument(
        "--skip-db-eval-write",
        action="store_true",
        help="Train and save bundle without writing rows to ml_model_evaluation.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with SessionLocal() as session:
        forecast_df, observed_df, municipalities_df = load_training_data_from_db(session)
        result = train_and_evaluate(
            forecast_df,
            observed_df,
            municipalities_df,
            model_version=args.model_version,
            n_folds=args.n_folds,
            holdout_fraction=args.holdout_fraction,
            n_estimators=args.n_estimators,
            artifacts_dir=args.artifacts_dir,
            save_bundle=True,
        )

        db_rows_written = 0
        if not args.skip_db_eval_write:
            db_rows_written = write_evaluation_rows(
                session,
                result["model_version"],
                result["evaluation_rows"],
            )

    holdout_overall = {
        row["metric_name"]: {
            "raw_nwp": row["raw_baseline_val"],
            "ml_model": row["ml_model_val"],
            "persistence": row["persistence_val"],
            "climatology_30d": row["climatology_val"],
            "sample_count": row["sample_count"],
        }
        for row in result["evaluation_rows"]
        if row["split_name"] == "holdout" and row["segment_key"] == "overall"
    }

    summary = {
        "model_version": result["model_version"],
        "bundle_dir": str(result["bundle_dir"]),
        "evaluation_rows_total": len(result["evaluation_rows"]),
        "db_evaluation_rows_written": db_rows_written,
        "holdout_overall": holdout_overall,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
