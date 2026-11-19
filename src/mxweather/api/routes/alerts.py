"""Active meteorological forecast-threshold alerts endpoint."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from mxweather.api.schemas import ActiveAlertLevel, AlertItem, AlertsResponse
from mxweather.db.models import (
    DimMunicipality,
    DimState,
    FactForecastRevision,
    FactMlForecast,
)
from mxweather.db.session import get_db

router = APIRouter(prefix="/v1", tags=["alerts"])

ALERTS_SCOPE_NOTE = (
    "Alerts are derived strictly from municipal meteorological forecast thresholds "
    "(precipitation, extreme heat/cold, and wind gusts) and ML-calibrated forecasts — "
    "not from street-level C5 flood sensors, live Doppler radar, or WhatsApp dispatch."
)


def _build_trigger_reasons(
    tmax_c: float,
    tmin_c: float,
    precip_mm: float | None,
    wind_gust_kmh: float | None,
    calibrated_rain_prob: float | None,
) -> list[str]:
    reasons: list[str] = []
    if precip_mm is not None and precip_mm >= 25.0:
        reasons.append(f"Heavy precipitation forecast ({precip_mm:.1f} mm >= 25 mm)")
    elif precip_mm is not None and precip_mm >= 15.0:
        reasons.append(f"Elevated precipitation forecast ({precip_mm:.1f} mm >= 15 mm)")
    if tmax_c >= 40.0:
        reasons.append(f"Severe heat forecast ({tmax_c:.1f} °C >= 40 °C)")
    elif tmax_c >= 38.0:
        reasons.append(f"High heat advisory ({tmax_c:.1f} °C >= 38 °C)")
    if tmin_c <= 0.0:
        reasons.append(f"Freezing minimum temperature ({tmin_c:.1f} °C <= 0 °C)")
    elif tmin_c <= 2.0:
        reasons.append(f"Near-freezing minimum temperature ({tmin_c:.1f} °C <= 2 °C)")
    if wind_gust_kmh is not None and wind_gust_kmh >= 60.0:
        reasons.append(f"Damaging wind gusts ({wind_gust_kmh:.1f} km/h >= 60 km/h)")
    elif wind_gust_kmh is not None and wind_gust_kmh >= 50.0:
        reasons.append(f"Strong wind gusts ({wind_gust_kmh:.1f} km/h >= 50 km/h)")
    if calibrated_rain_prob is not None and calibrated_rain_prob >= 0.75:
        reasons.append(f"High calibrated rain probability ({calibrated_rain_prob * 100:.0f}%)")
    if not reasons:
        reasons.append("Municipal meteorological forecast threshold exceeded")
    return reasons


@router.get(
    "/alerts",
    response_model=AlertsResponse,
    summary="Active WARNING_P0 and ADVISORY municipal forecast-threshold alerts",
)
def get_active_alerts(
    state_id: int | None = Query(default=None, ge=1, le=32, description="Optional state filter"),
    limit: int = Query(default=100, ge=1, le=500, description="Maximum alerts to return"),
    db: Session = Depends(get_db),
) -> AlertsResponse:
    """Return active WARNING_P0 and ADVISORY municipalities with explicit scope honesty note."""
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
    ml_subq = select(FactMlForecast, ml_rn).subquery()

    stmt = (
        select(
            DimMunicipality.state_id,
            DimMunicipality.municipality_id,
            DimMunicipality.municipality_name,
            DimState.state_name,
            ml_subq.c.valid_date,
            ml_subq.c.lead_day,
            ml_subq.c.issued_at,
            ml_subq.c.alert_level,
            ml_subq.c.raw_tmax_c,
            ml_subq.c.pred_tmax_p50,
            ml_subq.c.raw_tmin_c,
            ml_subq.c.pred_tmin_p50,
            ml_subq.c.calibrated_rain_prob,
        )
        .join(
            DimMunicipality,
            (DimMunicipality.state_id == ml_subq.c.state_id)
            & (DimMunicipality.municipality_id == ml_subq.c.municipality_id),
        )
        .join(DimState, DimState.state_id == DimMunicipality.state_id)
        .where(
            ml_subq.c.rn == 1,
            ml_subq.c.alert_level.in_(["WARNING_P0", "ADVISORY"]),
        )
    )
    if state_id is not None:
        stmt = stmt.where(DimMunicipality.state_id == state_id)

    rows = db.execute(
        stmt.order_by(
            asc(ml_subq.c.valid_date),
            asc(DimMunicipality.state_id),
            asc(DimMunicipality.municipality_id),
        ).limit(limit)
    ).all()

    # Fetch matching raw forecast precip_mm & wind_gust_kmh if available.
    rev_lookup: dict[tuple[int, int, date], tuple[float | None, float | None]] = {}
    if rows:
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
        rev_subq = select(FactForecastRevision, rev_rn).subquery()
        rev_rows = db.execute(
            select(
                rev_subq.c.state_id,
                rev_subq.c.municipality_id,
                rev_subq.c.valid_date,
                rev_subq.c.precip_mm,
                rev_subq.c.wind_gust_kmh,
            ).where(rev_subq.c.rn == 1)
        ).all()
        rev_lookup = {
            (int(r.state_id), int(r.municipality_id), r.valid_date): (
                float(r.precip_mm) if r.precip_mm is not None else None,
                float(r.wind_gust_kmh) if r.wind_gust_kmh is not None else None,
            )
            for r in rev_rows
        }

    items: list[AlertItem] = []
    for r in rows:
        precip_mm, wind_gust_kmh = rev_lookup.get(
            (int(r.state_id), int(r.municipality_id), r.valid_date), (None, None)
        )
        raw_tmax = float(r.raw_tmax_c)
        pred_tmax = float(r.pred_tmax_p50) if r.pred_tmax_p50 is not None else None
        raw_tmin = float(r.raw_tmin_c)
        pred_tmin = float(r.pred_tmin_p50) if r.pred_tmin_p50 is not None else None
        cal_rain = float(r.calibrated_rain_prob) if r.calibrated_rain_prob is not None else None
        level: ActiveAlertLevel = (
            "WARNING_P0" if str(r.alert_level) == "WARNING_P0" else "ADVISORY"
        )
        effective_tmax = pred_tmax if pred_tmax is not None else raw_tmax
        effective_tmin = pred_tmin if pred_tmin is not None else raw_tmin

        items.append(
            AlertItem(
                state_id=int(r.state_id),
                municipality_id=int(r.municipality_id),
                municipality_name=r.municipality_name,
                state_name=r.state_name,
                valid_date=r.valid_date,
                lead_day=int(r.lead_day),
                issued_at=r.issued_at,
                alert_level=level,
                raw_tmax_c=raw_tmax,
                pred_tmax_p50=pred_tmax,
                raw_tmin_c=raw_tmin,
                pred_tmin_p50=pred_tmin,
                precip_mm=precip_mm,
                wind_gust_kmh=wind_gust_kmh,
                calibrated_rain_prob=cal_rain,
                trigger_reasons=_build_trigger_reasons(
                    effective_tmax, effective_tmin, precip_mm, wind_gust_kmh, cal_rain
                ),
            )
        )

    # Prioritize WARNING_P0 before ADVISORY.
    items.sort(key=lambda item: (0 if item.alert_level == "WARNING_P0" else 1, item.valid_date, item.state_id, item.municipality_id))
    warning_count = sum(1 for item in items if item.alert_level == "WARNING_P0")
    advisory_count = sum(1 for item in items if item.alert_level == "ADVISORY")

    return AlertsResponse(
        count=len(items),
        warning_p0_count=warning_count,
        advisory_count=advisory_count,
        scope_note=ALERTS_SCOPE_NOTE,
        items=items,
    )
