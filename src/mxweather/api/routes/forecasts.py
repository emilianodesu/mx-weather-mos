"""Forecast endpoints serving municipality 4-day forecasts and interactive map snapshots."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from mxweather.api.schemas import (
    AlertLevel,
    ForecastDayItem,
    MapForecastPoint,
    MapForecastResponse,
    MunicipalityForecastResponse,
    MunicipalityItem,
)
from mxweather.db.models import (
    DimMunicipality,
    DimState,
    FactForecastRevision,
    FactMlForecast,
)
from mxweather.db.session import get_db

router = APIRouter(prefix="/v1/forecasts", tags=["forecasts"])

FORECAST_SCOPE_NOTE = (
    "Municipal forecasts combine Open-Meteo NWP revisions with LightGBM MOS bias correction "
    "and calibrated rain probabilities. Alert tiers are grounded in verifiable municipal "
    "forecast thresholds (not street-level C5 flood sensors or live Doppler radar)."
)


def derive_alert_level(
    tmax_c: float,
    tmin_c: float,
    precip_mm: float | None,
    wind_gust_kmh: float | None,
) -> AlertLevel:
    """Fallback threshold classifier when ML-scored alert_level is not yet available."""
    if (
        (precip_mm is not None and precip_mm >= 25.0)
        or tmax_c >= 40.0
        or tmin_c <= 0.0
        or (wind_gust_kmh is not None and wind_gust_kmh >= 60.0)
    ):
        return "WARNING_P0"
    if (
        (precip_mm is not None and precip_mm >= 15.0)
        or tmax_c >= 38.0
        or tmin_c <= 2.0
        or (wind_gust_kmh is not None and wind_gust_kmh >= 50.0)
    ):
        return "ADVISORY"
    return "NORMAL"


def _to_municipality_item(mun: DimMunicipality, state_name: str) -> MunicipalityItem:
    return MunicipalityItem(
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


@router.get(
    "/map",
    response_model=MapForecastResponse,
    summary="Latest day-0/day-1 forecast snapshot for interactive map",
)
def get_forecast_map(
    cohort_only: bool = Query(
        default=False, description="If true, restrict map points to the 32-capital ML cohort"
    ),
    db: Session = Depends(get_db),
) -> MapForecastResponse:
    """Return latest day-0/day-1 forecast snapshot across cohort/scored municipalities."""
    rev_rn = (
        func.row_number()
        .over(
            partition_by=(FactForecastRevision.state_id, FactForecastRevision.municipality_id),
            order_by=(
                desc(FactForecastRevision.issued_at),
                asc(FactForecastRevision.lead_day),
                desc(FactForecastRevision.valid_date),
            ),
        )
        .label("rn")
    )
    rev_subq = (
        select(FactForecastRevision, rev_rn)
        .where(FactForecastRevision.lead_day.in_([0, 1]))
        .subquery()
    )

    stmt = (
        select(
            DimMunicipality,
            DimState.state_name,
            rev_subq.c.valid_date,
            rev_subq.c.lead_day,
            rev_subq.c.tmax_c,
            rev_subq.c.tmin_c,
            rev_subq.c.precip_mm,
            rev_subq.c.wind_gust_kmh,
        )
        .join(DimState, DimState.state_id == DimMunicipality.state_id)
        .join(
            rev_subq,
            (rev_subq.c.state_id == DimMunicipality.state_id)
            & (rev_subq.c.municipality_id == DimMunicipality.municipality_id)
            & (rev_subq.c.rn == 1),
        )
    )
    if cohort_only:
        stmt = stmt.where(DimMunicipality.is_ml_cohort.is_(True))
    stmt = stmt.order_by(asc(DimMunicipality.state_id), asc(DimMunicipality.municipality_id))
    rev_rows = db.execute(stmt).all()

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
    ml_subq = select(FactMlForecast, ml_rn).where(FactMlForecast.lead_day.in_([0, 1])).subquery()
    ml_rows = db.execute(
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
    ml_by_key: dict[tuple[int, int, date], tuple[float, float, float, str]] = {
        (int(r.state_id), int(r.municipality_id), r.valid_date): (
            float(r.pred_tmax_p50),
            float(r.pred_tmin_p50),
            float(r.calibrated_rain_prob),
            str(r.alert_level),
        )
        for r in ml_rows
    }

    points: list[MapForecastPoint] = []
    for row in rev_rows:
        mun, state_name, valid_dt, lead_day, tmax_c, tmin_c, precip_mm, wind_gust_kmh = row
        raw_tmax = float(tmax_c)
        raw_tmin = float(tmin_c)
        precip_val = float(precip_mm) if precip_mm is not None else None
        gust_val = float(wind_gust_kmh) if wind_gust_kmh is not None else None

        ml_match = ml_by_key.get((int(mun.state_id), int(mun.municipality_id), valid_dt))
        if ml_match is not None:
            pred_tmax_p50, pred_tmin_p50, cal_rain, alert_lvl = ml_match
            bias_delta = round(pred_tmax_p50 - raw_tmax, 2)
            effective_alert: AlertLevel = (
                alert_lvl if alert_lvl in ("NORMAL", "ADVISORY", "WARNING_P0") else "NORMAL"  # type: ignore[assignment]
            )
        else:
            pred_tmax_p50 = None
            pred_tmin_p50 = None
            cal_rain = None
            bias_delta = None
            effective_alert = derive_alert_level(raw_tmax, raw_tmin, precip_val, gust_val)

        points.append(
            MapForecastPoint(
                state_id=int(mun.state_id),
                municipality_id=int(mun.municipality_id),
                municipality_name=mun.municipality_name,
                state_name=state_name,
                lat=float(mun.lat),
                lon=float(mun.lon),
                elevation_m=float(mun.elevation_m) if mun.elevation_m is not None else None,
                valid_date=valid_dt,
                lead_day=int(lead_day),
                raw_tmax_c=raw_tmax,
                pred_tmax_p50=pred_tmax_p50,
                raw_tmin_c=raw_tmin,
                pred_tmin_p50=pred_tmin_p50,
                precip_mm=precip_val,
                calibrated_rain_prob=cal_rain,
                ml_bias_delta_c=bias_delta,
                alert_level=effective_alert,
            )
        )

    return MapForecastResponse(count=len(points), items=points, scope_note=FORECAST_SCOPE_NOTE)


@router.get(
    "/{state_id}/{municipality_id}",
    response_model=MunicipalityForecastResponse,
    summary="Latest 4-day Raw vs. ML-Calibrated forecast for a municipality",
)
def get_municipality_forecast(
    state_id: int,
    municipality_id: int,
    db: Session = Depends(get_db),
) -> MunicipalityForecastResponse:
    """Return municipality metadata and latest 4-day forecast combining NWP and ML MOS output."""
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
    municipality_dto = _to_municipality_item(mun, state_name)

    # Latest revision per valid_date, then take the 4 most recent forecast dates in chronological order.
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
        )
        .subquery()
    )
    latest_revs = db.execute(
        select(
            rev_subq.c.valid_date,
            rev_subq.c.lead_day,
            rev_subq.c.issued_at,
            rev_subq.c.source_provider,
            rev_subq.c.tmax_c,
            rev_subq.c.tmin_c,
            rev_subq.c.precip_mm,
            rev_subq.c.precip_prob_pct,
            rev_subq.c.wind_speed_kmh,
            rev_subq.c.wind_gust_kmh,
            rev_subq.c.cloud_cover_pct,
            rev_subq.c.sky_desc,
        )
        .where(rev_subq.c.rn == 1)
        .order_by(desc(rev_subq.c.valid_date))
        .limit(4)
    ).all()
    latest_revs = sorted(latest_revs, key=lambda r: r.valid_date)

    valid_dates = [r.valid_date for r in latest_revs]
    ml_by_date: dict[date, object] = {}
    if valid_dates:
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
                FactMlForecast.valid_date.in_(valid_dates),
            )
            .subquery()
        )
        ml_rows = db.execute(
            select(
                ml_subq.c.valid_date,
                ml_subq.c.model_version,
                ml_subq.c.pred_tmax_p10,
                ml_subq.c.pred_tmax_p50,
                ml_subq.c.pred_tmax_p90,
                ml_subq.c.pred_tmin_p10,
                ml_subq.c.pred_tmin_p50,
                ml_subq.c.pred_tmin_p90,
                ml_subq.c.calibrated_rain_prob,
                ml_subq.c.alert_level,
            ).where(ml_subq.c.rn == 1)
        ).all()
        ml_by_date = {r.valid_date: r for r in ml_rows}

    days: list[ForecastDayItem] = []
    for rev in latest_revs:
        raw_tmax = float(rev.tmax_c)
        raw_tmin = float(rev.tmin_c)
        precip_val = float(rev.precip_mm) if rev.precip_mm is not None else None
        gust_val = float(rev.wind_gust_kmh) if rev.wind_gust_kmh is not None else None

        ml = ml_by_date.get(rev.valid_date)
        if ml is not None:
            pred_tmax_p50 = float(ml.pred_tmax_p50)
            ml_bias_delta = round(pred_tmax_p50 - raw_tmax, 2)
            raw_alert = str(ml.alert_level)
            alert_level: AlertLevel = (
                raw_alert if raw_alert in ("NORMAL", "ADVISORY", "WARNING_P0") else "NORMAL"  # type: ignore[assignment]
            )
            days.append(
                ForecastDayItem(
                    valid_date=rev.valid_date,
                    lead_day=int(rev.lead_day),
                    issued_at=rev.issued_at,
                    source_provider=rev.source_provider,
                    raw_tmax_c=raw_tmax,
                    raw_tmin_c=raw_tmin,
                    precip_mm=precip_val,
                    raw_precip_prob_pct=int(rev.precip_prob_pct) if rev.precip_prob_pct is not None else None,
                    wind_speed_kmh=float(rev.wind_speed_kmh) if rev.wind_speed_kmh is not None else None,
                    wind_gust_kmh=gust_val,
                    cloud_cover_pct=float(rev.cloud_cover_pct) if rev.cloud_cover_pct is not None else None,
                    sky_desc=rev.sky_desc,
                    model_version=ml.model_version,
                    pred_tmax_p10=float(ml.pred_tmax_p10),
                    pred_tmax_p50=pred_tmax_p50,
                    pred_tmax_p90=float(ml.pred_tmax_p90),
                    pred_tmin_p10=float(ml.pred_tmin_p10),
                    pred_tmin_p50=float(ml.pred_tmin_p50),
                    pred_tmin_p90=float(ml.pred_tmin_p90),
                    calibrated_rain_prob=float(ml.calibrated_rain_prob),
                    ml_bias_delta_c=ml_bias_delta,
                    alert_level=alert_level,
                )
            )
        else:
            days.append(
                ForecastDayItem(
                    valid_date=rev.valid_date,
                    lead_day=int(rev.lead_day),
                    issued_at=rev.issued_at,
                    source_provider=rev.source_provider,
                    raw_tmax_c=raw_tmax,
                    raw_tmin_c=raw_tmin,
                    precip_mm=precip_val,
                    raw_precip_prob_pct=int(rev.precip_prob_pct) if rev.precip_prob_pct is not None else None,
                    wind_speed_kmh=float(rev.wind_speed_kmh) if rev.wind_speed_kmh is not None else None,
                    wind_gust_kmh=gust_val,
                    cloud_cover_pct=float(rev.cloud_cover_pct) if rev.cloud_cover_pct is not None else None,
                    sky_desc=rev.sky_desc,
                    alert_level=derive_alert_level(raw_tmax, raw_tmin, precip_val, gust_val),
                )
            )

    return MunicipalityForecastResponse(
        municipality=municipality_dto,
        days=days,
        scope_note=FORECAST_SCOPE_NOTE,
    )
