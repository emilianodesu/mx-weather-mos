"""Verified CSV and Parquet dataset streaming export with SHA-256 provenance headers."""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from mxweather.db.models import (
    DimMunicipality,
    DimState,
    FactForecastRevision,
    FactMlForecast,
    FactObservedDaily,
)
from mxweather.db.session import get_db

router = APIRouter(prefix="/v1", tags=["export"])

DATA_PROVENANCE = (
    "mx-weather-intel; sources=open_meteo_era5,open_meteo_forecast,mos_ml; license=CC-BY-4.0"
)

EXPORT_COLUMNS: list[str] = [
    "state_id",
    "state_name",
    "municipality_id",
    "municipality_name",
    "date",
    "obs_tmax_c",
    "obs_tmin_c",
    "obs_precip_mm",
    "raw_tmax_c",
    "raw_tmin_c",
    "raw_precip_mm",
    "ml_tmax_p50",
    "ml_tmin_p50",
    "calibrated_rain_prob",
    "alert_level",
]

PARQUET_SCHEMA = pa.schema(
    [
        ("state_id", pa.int16()),
        ("state_name", pa.string()),
        ("municipality_id", pa.int16()),
        ("municipality_name", pa.string()),
        ("date", pa.date32()),
        ("obs_tmax_c", pa.float64()),
        ("obs_tmin_c", pa.float64()),
        ("obs_precip_mm", pa.float64()),
        ("raw_tmax_c", pa.float64()),
        ("raw_tmin_c", pa.float64()),
        ("raw_precip_mm", pa.float64()),
        ("ml_tmax_p50", pa.float64()),
        ("ml_tmin_p50", pa.float64()),
        ("calibrated_rain_prob", pa.float64()),
        ("alert_level", pa.string()),
    ]
)


def _serialize_csv(records: list[dict[str, object]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for rec in records:
        row_out = {
            k: (v.isoformat() if isinstance(v, date) else ("" if v is None else v))
            for k, v in rec.items()
        }
        writer.writerow(row_out)
    return buf.getvalue().encode("utf-8")


def _serialize_parquet(records: list[dict[str, object]]) -> bytes:
    columns = {col: [rec[col] for rec in records] for col in EXPORT_COLUMNS}
    table = pa.table(columns, schema=PARQUET_SCHEMA)
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="snappy")
    return sink.getvalue()


@router.get(
    "/export",
    summary="Stream verified municipal weather dataset as CSV or Parquet with SHA-256 header",
)
def export_dataset(
    state_id: int = Query(..., ge=1, le=32, description="State ID (1..32)"),
    municipality_id: int | None = Query(
        default=None, ge=1, description="Optional municipality ID within state"
    ),
    start_date: date | None = Query(default=None, description="Inclusive start date (YYYY-MM-DD)"),
    end_date: date | None = Query(default=None, description="Inclusive end date (YYYY-MM-DD)"),
    format: Literal["csv", "parquet"] = Query(
        default="csv", description="Export file format: csv or parquet"
    ),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Stream paired observed/raw/ML rows with X-Content-SHA256, X-Row-Count, and X-Data-Provenance."""
    effective_end = end_date if end_date is not None else datetime.now(UTC).date()
    effective_start = start_date if start_date is not None else effective_end - timedelta(days=90)

    if effective_start > effective_end:
        raise HTTPException(
            status_code=422,
            detail="start_date must be on or before end_date.",
        )

    mun_stmt = (
        select(DimMunicipality, DimState.state_name)
        .join(DimState, DimState.state_id == DimMunicipality.state_id)
        .where(DimMunicipality.state_id == state_id)
    )
    if municipality_id is not None:
        mun_stmt = mun_stmt.where(DimMunicipality.municipality_id == municipality_id)

    mun_rows = db.execute(
        mun_stmt.order_by(asc(DimMunicipality.municipality_id))
    ).all()
    if not mun_rows:
        target = (
            f"Municipality ({state_id}, {municipality_id})"
            if municipality_id is not None
            else f"State {state_id}"
        )
        raise HTTPException(status_code=404, detail=f"{target} not found.")

    mun_info = {
        (int(m.state_id), int(m.municipality_id)): (state_name, m.municipality_name)
        for m, state_name in mun_rows
    }

    obs_stmt = select(
        FactObservedDaily.state_id,
        FactObservedDaily.municipality_id,
        FactObservedDaily.obs_date,
        FactObservedDaily.tmax_c,
        FactObservedDaily.tmin_c,
        FactObservedDaily.precip_mm,
    ).where(
        FactObservedDaily.state_id == state_id,
        FactObservedDaily.obs_date >= effective_start,
        FactObservedDaily.obs_date <= effective_end,
    )
    if municipality_id is not None:
        obs_stmt = obs_stmt.where(FactObservedDaily.municipality_id == municipality_id)
    obs_by_key = {
        (int(r.state_id), int(r.municipality_id), r.obs_date): r
        for r in db.execute(obs_stmt).all()
    }

    rev_rn = (
        func.row_number()
        .over(
            partition_by=(
                FactForecastRevision.state_id,
                FactForecastRevision.municipality_id,
                FactForecastRevision.valid_date,
            ),
            order_by=desc(FactForecastRevision.issued_at),
        )
        .label("rn")
    )
    rev_base = select(FactForecastRevision, rev_rn).where(
        FactForecastRevision.state_id == state_id,
        FactForecastRevision.valid_date >= effective_start,
        FactForecastRevision.valid_date <= effective_end,
    )
    if municipality_id is not None:
        rev_base = rev_base.where(FactForecastRevision.municipality_id == municipality_id)
    rev_subq = rev_base.subquery()
    rev_by_key = {
        (int(r.state_id), int(r.municipality_id), r.valid_date): r
        for r in db.execute(
            select(
                rev_subq.c.state_id,
                rev_subq.c.municipality_id,
                rev_subq.c.valid_date,
                rev_subq.c.tmax_c,
                rev_subq.c.tmin_c,
                rev_subq.c.precip_mm,
            ).where(rev_subq.c.rn == 1)
        ).all()
    }

    ml_rn = (
        func.row_number()
        .over(
            partition_by=(
                FactMlForecast.state_id,
                FactMlForecast.municipality_id,
                FactMlForecast.valid_date,
            ),
            order_by=(desc(FactMlForecast.scored_at), desc(FactMlForecast.issued_at)),
        )
        .label("rn")
    )
    ml_base = select(FactMlForecast, ml_rn).where(
        FactMlForecast.state_id == state_id,
        FactMlForecast.valid_date >= effective_start,
        FactMlForecast.valid_date <= effective_end,
    )
    if municipality_id is not None:
        ml_base = ml_base.where(FactMlForecast.municipality_id == municipality_id)
    ml_subq = ml_base.subquery()
    ml_by_key = {
        (int(r.state_id), int(r.municipality_id), r.valid_date): r
        for r in db.execute(
            select(
                ml_subq.c.state_id,
                ml_subq.c.municipality_id,
                ml_subq.c.valid_date,
                ml_subq.c.pred_tmax_p50,
                ml_subq.c.pred_tmin_p50,
                ml_subq.c.calibrated_rain_prob,
                ml_subq.c.alert_level,
            ).where(ml_subq.c.rn == 1)
        ).all()
    }

    all_keys = sorted(
        set(obs_by_key.keys()) | set(rev_by_key.keys()) | set(ml_by_key.keys()),
        key=lambda k: (k[0], k[1], k[2]),
    )

    records: list[dict[str, object]] = []
    for st_id, mun_id, dt in all_keys:
        if (st_id, mun_id) not in mun_info:
            continue
        st_name, mun_name = mun_info[(st_id, mun_id)]
        obs = obs_by_key.get((st_id, mun_id, dt))
        rev = rev_by_key.get((st_id, mun_id, dt))
        ml = ml_by_key.get((st_id, mun_id, dt))

        records.append(
            {
                "state_id": st_id,
                "state_name": st_name,
                "municipality_id": mun_id,
                "municipality_name": mun_name,
                "date": dt,
                "obs_tmax_c": float(obs.tmax_c) if obs is not None else None,
                "obs_tmin_c": float(obs.tmin_c) if obs is not None else None,
                "obs_precip_mm": (
                    float(obs.precip_mm) if (obs is not None and obs.precip_mm is not None) else None
                ),
                "raw_tmax_c": float(rev.tmax_c) if rev is not None else None,
                "raw_tmin_c": float(rev.tmin_c) if rev is not None else None,
                "raw_precip_mm": (
                    float(rev.precip_mm) if (rev is not None and rev.precip_mm is not None) else None
                ),
                "ml_tmax_p50": float(ml.pred_tmax_p50) if ml is not None else None,
                "ml_tmin_p50": float(ml.pred_tmin_p50) if ml is not None else None,
                "calibrated_rain_prob": (
                    float(ml.calibrated_rain_prob) if ml is not None else None
                ),
                "alert_level": str(ml.alert_level) if ml is not None else None,
            }
        )

    if format == "parquet":
        payload = _serialize_parquet(records)
        media_type = "application/vnd.apache.parquet"
        ext = "parquet"
    else:
        payload = _serialize_csv(records)
        media_type = "text/csv; charset=utf-8"
        ext = "csv"

    sha256_hex = hashlib.sha256(payload).hexdigest()
    mun_part = f"_mun{municipality_id:03d}" if municipality_id is not None else "_all"
    filename = (
        f"mxweather_state{state_id:02d}{mun_part}_{effective_start.isoformat()}_{effective_end.isoformat()}.{ext}"
    )

    return StreamingResponse(
        io.BytesIO(payload),
        media_type=media_type,
        headers={
            "X-Content-SHA256": sha256_hex,
            "X-Row-Count": str(len(records)),
            "X-Data-Provenance": DATA_PROVENANCE,
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
