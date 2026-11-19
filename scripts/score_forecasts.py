"""CLI entrypoint to score latest forecasts using the verified MOS model bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mxweather.db.session import SessionLocal
from mxweather.ml.score import score_latest_forecasts_in_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score latest forecasts in Postgres and upsert into fact_ml_forecast."
    )
    parser.add_argument(
        "--model-version",
        type=str,
        default=None,
        help="Optional explicit model version (defaults to latest.json pointer).",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=None,
        help="Override artifacts directory (defaults to MXWEATHER_ARTIFACTS_DIR).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=60,
        help="Days of historical forecasts/observations loaded for PIT rolling features.",
    )
    parser.add_argument(
        "--score-recent-days",
        type=int,
        default=7,
        help="Score forecasts within this many days of the latest valid_date.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with SessionLocal() as session:
        summary = score_latest_forecasts_in_db(
            session,
            model_version=args.model_version,
            artifacts_dir=args.artifacts_dir,
            lookback_days=args.lookback_days,
            score_recent_days=args.score_recent_days,
        )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
