"""FastAPI application entrypoint for mx-weather-intel."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mxweather.api.routes import (
    alerts,
    export,
    forecasts,
    health,
    ml_skill,
    municipalities,
    trends,
)

OPENAPI_TAGS = [
    {"name": "health", "description": "Database, PostGIS, model registry, and ingestion run health."},
    {"name": "municipalities", "description": "Mexican state and municipality catalog with PostGIS proximity search."},
    {"name": "forecasts", "description": "Raw NWP vs. LightGBM MOS 4-day municipal forecasts and map snapshots."},
    {"name": "alerts", "description": "Active municipal forecast-threshold alerts (WARNING_P0 / ADVISORY)."},
    {"name": "trends", "description": "Paired daily observed ERA5, raw NWP, and ML-corrected time series."},
    {"name": "ml", "description": "Walk-forward ML skill evaluation vs. baselines and rain calibration curve."},
    {"name": "export", "description": "Verified CSV and Parquet dataset streaming with SHA-256 checksum headers."},
]


def create_app() -> FastAPI:
    """Create and configure the mx-weather-intel FastAPI application."""
    app = FastAPI(
        title="mx-weather-intel API",
        version="0.1.0",
        description=(
            "Mexico Weather Forecast Verification & ML Bias-Correction Platform API. "
            "Serves revision-aware municipal forecasts, LightGBM MOS predictions, "
            "walk-forward skill metrics, and SHA-256 verified CSV/Parquet exports."
        ),
        openapi_tags=OPENAPI_TAGS,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[
            "X-Content-SHA256",
            "X-Row-Count",
            "X-Data-Provenance",
            "Content-Disposition",
        ],
    )

    app.include_router(health.router)
    app.include_router(municipalities.router)
    app.include_router(forecasts.router)
    app.include_router(alerts.router)
    app.include_router(trends.router)
    app.include_router(ml_skill.router)
    app.include_router(export.router)

    return app


app = create_app()
