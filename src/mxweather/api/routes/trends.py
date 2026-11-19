"""Historical paired observed vs. raw forecast vs. ML-corrected trends endpoint."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from mxweather.api.schemas import MunicipalityItem, TrendPoint, TrendsResponse
from mxweather.db.models import (
    DimMunicipality,
    DimState,
    FactForecastRevision,
    FactMlForecast,
    FactObservedDaily,
)
from mxweather.db.session import get_db

router = APIRouter(prefix="/v1/trends", tags=["trends"])


@router.get(
    "/{state_id}/{municipality_id}",
    response_model=TrendsResponse,
    summary="Paired daily observed, raw forecast, and ML-corrected time series",
)
def get_municipality_trends(
    state_id: int,
    municipality_id: int,
    start_date: date | None = Query(default=None, description="Inclusive start date (YYYY-MM-DD)"),
    end_date: date | None = Query(default=None, description="Inclusive end date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
) -> TrendsResponse:
    """Return paired daily series (default last 90 days) for researcher trend visualization."""
    effective_end = end_date if end_date is not None else datetime.now(UTC).date()
    effective_start = start_date if start_date is not None else effective_end - timedelta(days=90)

    if effective_start > effective_end:
        raise HTTPException(
            status_code=422,
            detail="start_date must be on or before end_date.",
        )

    mun_row = db.execute(
        select(DimMunicipality, DimState.state_name)
        .join(DimState, DimState.state_id == DimMunicipality.state_id)
        .where(
            DimMunicipality.state_id == state_id,
            DimMunicipality.municipality_id == municipality_id,
        )
    ).one_or_none()

    if mun_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Municipality ({state_id}, {municipality_id}) not found.",
        )

    mun, state_name = mun_row
    municipality_dto = MunicipalityItem(
        state_id=int(mun.state_id),
        municipality_id=int(mun.municipality_id),
        municipality_name=mun.municipality_name,
        state_name=state_name,
        lat=float(mun.lat),
        lon=float(mun.lon),
        elevation_m=float(mun.elevation_m) if mun.elevation_m is not None else None,
        utc_offset_hours=int(mun.utc_offset_hours),
        is_ml_cohort=bool(mun.is_ml_cohort),
    )

    obs_rows = db.execute(
        select(
            FactObservedDaily.obs_date,
            FactObservedDaily.tmax_c,
            FactObservedDaily.tmin_c,
            FactObservedDaily.precip_mm,
        )
        .where(
            FactObservedDaily.state_id == state_id,
            FactObservedDaily.municipality_id == municipality_id,
            FactObservedDaily.obs_date >= effective_start,
            FactObservedDaily.obs_date <= effective_end,
        )
        .order_by(asc(FactObservedDaily.obs_date))
    ).all()
    obs_by_date = {r.obs_date: r for r in obs_rows}

    rev_rn = (
        func.row_number()
        .over(
            partition_by=FactForecastRevision.valid_date,
            order_by=desc(FactForecastRevision.issued_at),
        )
        .label("rn")
    )
    rev_subq = (
        select(FactForecastRevision, rev_rn)
        .where(
            FactForecastRevision.state_id == state_id,
            FactForecastRevision.municipality_id == municipality_id,
            FactForecastRevision.valid_date >= effective_start,
            FactForecastRevision.valid_date <= effective_end,
        )
        .subquery()
    )
    rev_rows = db.execute(
        select(rev_subq.c.valid_date, rev_subq.c.tmax_c, rev_subq.c.tmin_c)
        .where(rev_subq.c.rn == 1)
        .order_by(asc(rev_subq.c.valid_date))
    ).all()
    rev_by_date = {r.valid_date: r for r in rev_rows}

    ml_rn = (
        func.row_number()
        .over(
            partition_by=FactMlForecast.valid_date,
            order_by=(desc(FactMlForecast.scored_at), desc(FactMlForecast.issued_at)),
        )
        .label("rn")
    )
    ml_subq = (
        select(FactMlForecast, ml_rn)
        .where(
            FactMlForecast.state_id == state_id,
            FactMlForecast.municipality_id == municipality_id,
            FactMlForecast.valid_date >= effective_start,
            FactMlForecast.valid_date <= effective_end,
        )
        .subquery()
    )
    ml_rows = db.execute(
        select(ml_subq.c.valid_date, ml_subq.c.pred_tmax_p50, ml_subq.c.pred_tmin_p50)
        .where(ml_subq.c.rn == 1)
        .order_by(asc(ml_subq.c.valid_date))
    ).all()
    ml_by_date = {r.valid_date: r for r in ml_rows}

    all_dates = sorted(set(obs_by_date.keys()) | set(rev_by_date.keys()) | set(ml_by_date.keys()))
    series: list[TrendPoint] = []
    for dt in all_dates:
        obs = obs_by_date.get(dt)
        rev = rev_by_date.get(dt)
        ml = ml_by_date.get(dt)
        series.append(
            TrendPoint(
                date=dt,
                obs_tmax_c=float(obs.tmax_c) if obs is not None else None,
                obs_tmin_c=float(obs.tmin_c) if obs is not None else None,
                obs_precip_mm=(
                    float(obs.precip_mm)
                    if (obs is not None and obs.precip_mm is not None)
                    else None
                ),
                raw_tmax_c=float(rev.tmax_c) if rev is not None else None,
                raw_tmin_c=float(rev.tmin_c) if rev is not None else None,
                ml_tmax_p50=float(ml.pred_tmax_p50) if ml is not None else None,
                ml_tmin_p50=float(ml.pred_tmin_p50) if ml is not None else None,
            )
        )

    return TrendsResponse(
        municipality=municipality_dto,
        start_date=effective_start,
        end_date=effective_end,
        count=len(series),
        series=series,
    )
