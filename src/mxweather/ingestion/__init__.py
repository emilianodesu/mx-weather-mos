"""Dual-source weather ingestion adapters (Open-Meteo primary + CONAGUA SMN resilient secondary)."""

from mxweather.ingestion.conagua_smn import (
    SmnFetchResult,
    decode_smn_payload,
    fetch_live_smn,
    load_smn_fixture,
    parse_smn_records,
)
from mxweather.ingestion.open_meteo import (
    OpenMeteoFetchResult,
    archive_raw_payload,
    create_http_client,
    fetch_era5_archive,
    fetch_historical_forecast,
    fetch_live_forecast,
    finish_ingestion_run,
    parse_era5_payload,
    parse_historical_forecast_payload,
    parse_live_forecast_payload,
    start_ingestion_run,
    update_municipality_elevation,
    upsert_forecast_revisions,
    upsert_observed_daily,
)

__all__ = [
    "OpenMeteoFetchResult",
    "SmnFetchResult",
    "archive_raw_payload",
    "create_http_client",
    "decode_smn_payload",
    "fetch_era5_archive",
    "fetch_historical_forecast",
    "fetch_live_forecast",
    "fetch_live_smn",
    "finish_ingestion_run",
    "load_smn_fixture",
    "parse_era5_payload",
    "parse_historical_forecast_payload",
    "parse_live_forecast_payload",
    "parse_smn_records",
    "start_ingestion_run",
    "update_municipality_elevation",
    "upsert_forecast_revisions",
    "upsert_observed_daily",
]
