"""CLI for daily operational forecast ingestion (Open-Meteo live 4-day + resilient CONAGUA SMN)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from mxweather.db.models import DimMunicipality
from mxweather.db.session import SessionLocal
from mxweather.ingestion.conagua_smn import fetch_live_smn
from mxweather.ingestion.open_meteo import (
    create_http_client,
    fetch_live_forecast,
    finish_ingestion_run,
    start_ingestion_run,
    update_municipality_elevation,
    upsert_forecast_revisions,
)
from mxweather.quality.gate import validate_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mxweather.scripts.run_daily_ingest")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run live Open-Meteo 4-day forecast ingestion + resilient CONAGUA SMN adapter."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="include_all",
        help="Ingest all municipalities in dim_municipality instead of only the 32 ML cohort capitals.",
    )
    parser.add_argument(
        "--limit-municipalities",
        type=int,
        default=None,
        help="Optional cap on number of municipalities to ingest.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=4,
        help="Forecast horizon in days (default: 4, i.e., lead_day 0..3).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Polite delay in seconds between municipality HTTP requests (default: 0.25).",
    )
    parser.add_argument(
        "--skip-smn",
        action="store_true",
        help="Skip attempting the live CONAGUA SMN adapter.",
    )
    return parser.parse_args(argv)


def run_daily_ingest(
    *,
    include_all: bool = False,
    limit_municipalities: int | None = None,
    days: int = 4,
    delay_seconds: float = 0.25,
    skip_smn: bool = False,
) -> dict[str, Any]:
    """Run live Open-Meteo forecast ingestion and resilient CONAGUA SMN attempt."""
    issued_at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    om_loaded_total = 0
    om_quarantined_total = 0
    om_errors: list[dict[str, Any]] = []

    smn_summary: dict[str, Any] = {
        "status": "SKIPPED" if skip_smn else "NOT_RUN",
        "records_read": 0,
        "records_loaded": 0,
        "records_quarantined": 0,
        "error_detail": None,
    }

    with SessionLocal() as session:
        all_mun_keys = set(
            session.execute(
                select(DimMunicipality.state_id, DimMunicipality.municipality_id)
            ).all()
        )
        valid_all_muns = {(int(s), int(m)) for s, m in all_mun_keys}

        stmt = select(DimMunicipality).order_by(
            DimMunicipality.state_id, DimMunicipality.municipality_id
        )
        if not include_all:
            stmt = stmt.where(DimMunicipality.is_ml_cohort.is_(True))
        municipalities = list(session.scalars(stmt).all())
        if limit_municipalities is not None:
            municipalities = municipalities[:limit_municipalities]

        logger.info(
            "Starting live Open-Meteo %d-day forecast ingest for %d municipalities (issued_at=%s)",
            days,
            len(municipalities),
            issued_at.isoformat(),
        )

        with create_http_client() as client:
            for idx, mun in enumerate(municipalities):
                if idx > 0 and delay_seconds > 0:
                    time.sleep(delay_seconds)

                run = start_ingestion_run(session, "open_meteo_forecast")
                try:
                    res = fetch_live_forecast(
                        mun.lat,
                        mun.lon,
                        days=days,
                        state_id=mun.state_id,
                        municipality_id=mun.municipality_id,
                        issued_at=issued_at,
                        client=client,
                    )
                    if mun.elevation_m is None and res.elevation_m is not None:
                        update_municipality_elevation(
                            session, mun.state_id, mun.municipality_id, res.elevation_m
                        )
                    valid_rows, quar_rows, _ = validate_batch(
                        res.rows,
                        record_type="forecast",
                        valid_municipalities=valid_all_muns,
                        payload_sha256=res.payload_sha256,
                    )
                    loaded = upsert_forecast_revisions(session, valid_rows, run.run_id)
                    finish_ingestion_run(
                        session,
                        run,
                        status="SUCCESS",
                        records_read=len(res.rows),
                        records_loaded=loaded,
                        records_quarantined=len(quar_rows),
                        payload_sha256=res.payload_sha256,
                    )
                    om_loaded_total += loaded
                    om_quarantined_total += len(quar_rows)
                except Exception as exc:
                    logger.error(
                        "Live Open-Meteo ingest failed for (%d, %d): %s",
                        mun.state_id,
                        mun.municipality_id,
                        exc,
                    )
                    finish_ingestion_run(
                        session,
                        run,
                        status="FAILED",
                        records_read=0,
                        records_loaded=0,
                        records_quarantined=0,
                        error_detail=str(exc)[:1000],
                    )
                    om_errors.append(
                        {"municipality": [mun.state_id, mun.municipality_id], "error": str(exc)}
                    )
                session.commit()

        # Secondary adapter: CONAGUA SMN with graceful circuit-breaker degradation
        if not skip_smn:
            logger.info("Attempting secondary CONAGUA SMN live adapter...")
            smn_res = fetch_live_smn(session=session)
            if smn_res.status == "SUCCESS":
                smn_run = start_ingestion_run(
                    session, "conagua_smn", payload_sha256=smn_res.payload_sha256
                )
                valid_smn, quar_smn, _ = validate_batch(
                    smn_res.rows,
                    record_type="forecast",
                    valid_municipalities=valid_all_muns,
                    payload_sha256=smn_res.payload_sha256,
                )
                loaded_smn = upsert_forecast_revisions(session, valid_smn, smn_run.run_id)
                finish_ingestion_run(
                    session,
                    smn_run,
                    status="SUCCESS",
                    records_read=len(smn_res.rows),
                    records_loaded=loaded_smn,
                    records_quarantined=len(quar_smn),
                    payload_sha256=smn_res.payload_sha256,
                )
                smn_summary = {
                    "status": "SUCCESS",
                    "records_read": len(smn_res.rows),
                    "records_loaded": loaded_smn,
                    "records_quarantined": len(quar_smn),
                    "error_detail": None,
                }
            else:
                smn_summary = {
                    "status": smn_res.status,
                    "records_read": 0,
                    "records_loaded": 0,
                    "records_quarantined": 0,
                    "error_detail": smn_res.error_detail,
                }
            session.commit()

    return {
        "issued_at": issued_at.isoformat(),
        "open_meteo": {
            "municipalities_targeted": len(municipalities),
            "forecast_days": days,
            "records_loaded": om_loaded_total,
            "records_quarantined": om_quarantined_total,
            "error_count": len(om_errors),
            "errors": om_errors,
        },
        "conagua_smn": smn_summary,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run_daily_ingest(
        include_all=args.include_all,
        limit_municipalities=args.limit_municipalities,
        days=args.days,
        delay_seconds=args.delay,
        skip_smn=args.skip_smn,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
