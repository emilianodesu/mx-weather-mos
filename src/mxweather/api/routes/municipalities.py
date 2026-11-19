"""State and municipality lookup endpoints with PostGIS proximity search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from geoalchemy2 import Geography
from sqlalchemy import asc, cast, func, or_, select
from sqlalchemy.orm import Session

from mxweather.api.schemas import (
    MunicipalityItem,
    MunicipalityListResponse,
    StateItem,
    StateListResponse,
)
from mxweather.db.models import DimMunicipality, DimState
from mxweather.db.session import get_db

router = APIRouter(prefix="/v1", tags=["municipalities"])


@router.get("/states", response_model=StateListResponse, summary="List all 32 Mexican states")
def list_states(db: Session = Depends(get_db)) -> StateListResponse:
    """Return all Mexican states ordered by state_id for filter dropdowns."""
    rows = db.execute(select(DimState).order_by(asc(DimState.state_id))).scalars().all()
    items = [StateItem(state_id=int(r.state_id), state_name=r.state_name) for r in rows]
    return StateListResponse(count=len(items), items=items)


@router.get(
    "/municipalities",
    response_model=MunicipalityListResponse,
    summary="Search municipalities with optional PostGIS proximity filter",
)
def list_municipalities(
    state_id: int | None = Query(default=None, ge=1, le=32, description="Filter by state ID (1..32)"),
    search: str | None = Query(
        default=None,
        min_length=1,
        max_length=120,
        description="Case-insensitive substring search on municipality or state name",
    ),
    cohort_only: bool = Query(default=False, description="Return only the 32-capital ML cohort"),
    near_lat: float | None = Query(
        default=None, ge=14.0, le=33.0, description="Latitude for PostGIS proximity search"
    ),
    near_lon: float | None = Query(
        default=None, ge=-119.0, le=-86.0, description="Longitude for PostGIS proximity search"
    ),
    radius_km: float | None = Query(
        default=None, gt=0.0, le=2000.0, description="Search radius in kilometers (default 50 km)"
    ),
    limit: int = Query(default=100, ge=1, le=2500, description="Maximum rows to return"),
    db: Session = Depends(get_db),
) -> MunicipalityListResponse:
    """Query municipalities with state, text, cohort, and PostGIS ST_DWithin radius filtering."""
    has_lat = near_lat is not None
    has_lon = near_lon is not None
    if has_lat != has_lon:
        raise HTTPException(
            status_code=422,
            detail="Both near_lat and near_lon must be provided together for proximity search.",
        )
    if radius_km is not None and not (has_lat and has_lon):
        raise HTTPException(
            status_code=422,
            detail="radius_km requires both near_lat and near_lon.",
        )

    use_proximity = has_lat and has_lon
    effective_radius_km = radius_km if radius_km is not None else 50.0

    if use_proximity:
        ref_point = cast(
            func.ST_SetSRID(func.ST_MakePoint(near_lon, near_lat), 4326),
            Geography(geometry_type="POINT", srid=4326),
        )
        distance_expr = (func.ST_Distance(DimMunicipality.geom, ref_point) / 1000.0).label("distance_km")
        stmt = (
            select(DimMunicipality, DimState.state_name, distance_expr)
            .join(DimState, DimState.state_id == DimMunicipality.state_id)
            .where(func.ST_DWithin(DimMunicipality.geom, ref_point, effective_radius_km * 1000.0))
        )
    else:
        stmt = select(DimMunicipality, DimState.state_name).join(
            DimState, DimState.state_id == DimMunicipality.state_id
        )

    if state_id is not None:
        stmt = stmt.where(DimMunicipality.state_id == state_id)
    if cohort_only:
        stmt = stmt.where(DimMunicipality.is_ml_cohort.is_(True))
    if search is not None and search.strip():
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                DimMunicipality.municipality_name.ilike(pattern),
                DimState.state_name.ilike(pattern),
            )
        )

    if use_proximity:
        stmt = stmt.order_by(asc("distance_km"), asc(DimMunicipality.state_id), asc(DimMunicipality.municipality_id))
    else:
        stmt = stmt.order_by(asc(DimMunicipality.state_id), asc(DimMunicipality.municipality_id))

    stmt = stmt.limit(limit)
    rows = db.execute(stmt).all()

    items: list[MunicipalityItem] = []
    for row in rows:
        if use_proximity:
            mun, state_name, dist_km = row
            distance_val = round(float(dist_km), 3) if dist_km is not None else None
        else:
            mun, state_name = row
            distance_val = None

        items.append(
            MunicipalityItem(
                state_id=int(mun.state_id),
                municipality_id=int(mun.municipality_id),
                municipality_name=mun.municipality_name,
                state_name=state_name,
                lat=float(mun.lat),
                lon=float(mun.lon),
                elevation_m=float(mun.elevation_m) if mun.elevation_m is not None else None,
                utc_offset_hours=int(mun.utc_offset_hours),
                is_ml_cohort=bool(mun.is_ml_cohort),
                distance_km=distance_val,
            )
        )

    return MunicipalityListResponse(count=len(items), items=items)
