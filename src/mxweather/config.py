"""Runtime settings loaded strictly from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = (
        "postgresql+psycopg://mxweather:mxweather_local_dev@localhost:5432/mxweather"
    )
    mxweather_data_dir: Path = Path("data")
    mxweather_artifacts_dir: Path = Path("artifacts")
    mxweather_fixtures_dir: Path = Path("fixtures")
    mxweather_contracts_path: Path = Path("contracts/data_contracts.json")

    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_historical_forecast_url: str = (
        "https://historical-forecast-api.open-meteo.com/v1/forecast"
    )
    open_meteo_live_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    conagua_smn_url: str = "https://smn.conagua.gob.mx/webservices/?method=1"
    conagua_smn_timeout_seconds: float = 15.0

    api_host: str = "0.0.0.0"
    api_port: int = 8000


settings = Settings()
