"""CLI to backfill paired ERA5 Archive + Historical Forecast data for the 32-capital ML cohort."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select

from mxweather.db.models import DimMunicipality, FactForecastRevision, FactObservedDaily
from mxweather.db.session import SessionLocal
from mxweather.ingestion.open_meteo import (
    create_http_client,
    fetch_era5_archive,
    fetch_historical_forecast,
    finish_ingestion_run,
    start_ingestion_run,
    update_municipality_elevation,
    upsert_forecast_revisions,
    upsert_observed_daily,
)
from mxweather.quality.gate import check_cohort_completeness, validate_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mxweather.scripts.run_backfill")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill Open-Meteo ERA5 + Historical Forecast for the ML cohort capitals."
    )
    parser.add_argument(
        "--months",
        type=int,
        default=24,
        help="Number of months to backfill ending at --end-date (default: 24).",
    )
    parser.add_argument(
        "--limit-municipalities",
        type=int,
        default=None,
        help="Optional cap on number of cohort municipalities to backfill (for fast testing).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Polite delay in seconds between municipality HTTP requests (default: 0.25).",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="End date (YYYY-MM-DD). Defaults to today minus 5 days to account for ERA5 lag.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip municipalities that already have >= 90%% of requested days loaded in Postgres.",
    )
    return parser.parse_args(argv)


def run_backfill(
    *,
    months: int = 24,
    limit_municipalities: int | None = None,
    delay_seconds: float = 0.25,
    end_date: date | None = None,
    skip_existing: bool = False,
) -> dict[str, Any]:
    """Execute paired ERA5 + Historical Forecast backfill and return a structured JSON summary."""
    resolved_end = end_date or (date.today() - timedelta(days=5))
    resolved_start = resolved_end - timedelta(days=int(months * 30.4375))

    observed_loaded_total = 0
    observed_quarantined_total = 0
    forecast_loaded_total = 0
    forecast_quarantined_total = 0
    elevations_updated = 0
    errors: list[dict[str, Any]] = []
    observed_muns: set[tuple[int, int]] = set()
    forecast_muns: set[tuple[int, int]] = set()

    with SessionLocal() as session:
        stmt = (
            select(DimMunicipality)
            .where(DimMunicipality.is_ml_cohort.is_(True))
            .order_by(DimMunicipality.state_id, DimMunicipality.municipality_id)
        )
        municipalities = list(session.scalars(stmt).all())
        if limit_municipalities is not None:
            municipalities = municipalities[:limit_municipalities]

        valid_muns = {(m.state_id, m.municipality_id) for m in municipalities}
        min_existing_days = int((resolved_end - resolved_start).days * 0.90)
        existing_obs_counts = {
            (row[0], row[1]): int(row[2])
            for row in session.execute(
                select(
                    FactObservedDaily.state_id,
                    FactObservedDaily.municipality_id,
                    func.count(),
                )
                .where(FactObservedDaily.source_provider == "open_meteo_era5")
                .group_by(FactObservedDaily.state_id, FactObservedDaily.municipality_id)
            ).all()
        }
        existing_fcst_counts = {
            (row[0], row[1]): int(row[2])
            for row in session.execute(
                select(
                    FactForecastRevision.state_id,
                    FactForecastRevision.municipality_id,
                    func.count(),
                )
                .where(FactForecastRevision.source_provider == "open_meteo_historical_forecast")
                .group_by(FactForecastRevision.state_id, FactForecastRevision.municipality_id)
            ).all()
        }
        for mun_key, cnt in existing_obs_counts.items():
            if mun_key in valid_muns and cnt >= min_existing_days:
                observed_muns.add(mun_key)
        for mun_key, cnt in existing_fcst_counts.items():
            if mun_key in valid_muns and cnt >= min_existing_days:
                forecast_muns.add(mun_key)

        logger.info(
            "Starting backfill for %d cohort municipalities (%s to %s; pre-existing paired=%d)",
            len(municipalities),
            resolved_start.isoformat(),
            resolved_end.isoformat(),
            len(observed_muns & forecast_muns),
        )

        with create_http_client() as client:
            for idx, mun in enumerate(municipalities):
                mun_key = (mun.state_id, mun.municipality_id)
                skip_obs = skip_existing and mun_key in observed_muns
                skip_fcst = skip_existing and mun_key in forecast_muns
                if skip_obs and skip_fcst:
                    continue
                if idx > 0 and delay_seconds > 0:
                    time.sleep(delay_seconds)

                # 1. ERA5 Archive ground truth
                if not skip_obs:
                    era5_run = start_ingestion_run(session, "open_meteo_era5")
                    try:
                        era5_res = fetch_era5_archive(
                            mun.lat,
                            mun.lon,
                            resolved_start,
                            resolved_end,
                            state_id=mun.state_id,
                            municipality_id=mun.municipality_id,
                            client=client,
                        )
                        if era5_res.elevation_m is not None:
                            update_municipality_elevation(
                                session, mun.state_id, mun.municipality_id, era5_res.elevation_m
                            )
                            elevations_updated += 1

                        valid_obs, quar_obs, _ = validate_batch(
                            era5_res.rows,
                            record_type="observed",
                            valid_municipalities=valid_muns,
                            payload_sha256=era5_res.payload_sha256,
                        )
                        loaded_obs = upsert_observed_daily(session, valid_obs, era5_run.run_id)
                        finish_ingestion_run(
                            session,
                            era5_run,
                            status="SUCCESS",
                            records_read=len(era5_res.rows),
                            records_loaded=loaded_obs,
                            records_quarantined=len(quar_obs),
                            payload_sha256=era5_res.payload_sha256,
                        )
                        observed_loaded_total += loaded_obs
                        observed_quarantined_total += len(quar_obs)
                        if loaded_obs > 0:
                            observed_muns.add(mun_key)
                    except Exception as exc:
                        logger.error(
                            "ERA5 backfill failed for %s (%s): %s",
                            mun.municipality_name,
                            mun_key,
                            exc,
                        )
                        finish_ingestion_run(
                            session,
                            era5_run,
                            status="FAILED",
                            records_read=0,
                            records_loaded=0,
                            records_quarantined=0,
                            error_detail=str(exc)[:1000],
                        )
                        errors.append(
                            {"municipality": list(mun_key), "provider": "open_meteo_era5", "error": str(exc)}
                        )

                if not skip_obs and not skip_fcst and delay_seconds > 0:
                    time.sleep(delay_seconds)

                # 2. Historical Forecast (NWP baseline)
                if not skip_fcst:
                    hf_run = start_ingestion_run(session, "open_meteo_historical_forecast")
                    try:
                        hf_res = fetch_historical_forecast(
                            mun.lat,
                            mun.lon,
                            resolved_start,
                            resolved_end,
                            state_id=mun.state_id,
                            municipality_id=mun.municipality_id,
                            client=client,
                        )
                        valid_fcst, quar_fcst, _ = validate_batch(
                            hf_res.rows,
                            record_type="forecast",
                            valid_municipalities=valid_muns,
                            payload_sha256=hf_res.payload_sha256,
                        )
                        loaded_fcst = upsert_forecast_revisions(session, valid_fcst, hf_run.run_id)
                        finish_ingestion_run(
                            session,
                            hf_run,
                            status="SUCCESS",
                            records_read=len(hf_res.rows),
                            records_loaded=loaded_fcst,
                            records_quarantined=len(quar_fcst),
                            payload_sha256=hf_res.payload_sha256,
                        )
                        forecast_loaded_total += loaded_fcst
                        forecast_quarantined_total += len(quar_fcst)
                        if loaded_fcst > 0:
                            forecast_muns.add(mun_key)
                    except Exception as exc:
                        logger.error(
                            "Historical forecast backfill failed for %s (%s): %s",
                            mun.municipality_name,
                            mun_key,
                            exc,
                        )
                        finish_ingestion_run(
                            session,
                            hf_run,
                            status="FAILED",
                            records_read=0,
                            records_loaded=0,
                            records_quarantined=0,
                            error_detail=str(exc)[:1000],
                        )
                        errors.append(
                            {
                                "municipality": list(mun_key),
                                "provider": "open_meteo_historical_forecast",
                                "error": str(exc),
                            }
                        )

                session.commit()

        q08_report = check_cohort_completeness(observed_muns, forecast_muns, valid_muns)

    return {
        "start_date": resolved_start.isoformat(),
        "end_date": resolved_end.isoformat(),
        "municipalities_targeted": len(valid_muns),
        "elevations_updated": elevations_updated,
        "observed_loaded": observed_loaded_total,
        "observed_quarantined": observed_quarantined_total,
        "forecast_loaded": forecast_loaded_total,
        "forecast_quarantined": forecast_quarantined_total,
        "cohort_completeness": q08_report,
        "error_count": len(errors),
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    end_dt = date.fromisoformat(args.end_date) if args.end_date else None
    summary = run_backfill(
        months=args.months,
        limit_municipalities=args.limit_municipalities,
        delay_seconds=args.delay,
        end_date=end_dt,
        skip_existing=args.skip_existing,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
