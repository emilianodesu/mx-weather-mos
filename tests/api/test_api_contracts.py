"""Offline FastAPI contract tests using get_db dependency override (no live Postgres required)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from mxweather.api.main import create_app
from mxweather.config import settings
from mxweather.db.session import get_db


class FakeScalars:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return list(self._items)


class FakeResult:
    def __init__(
        self,
        *,
        rows: list[Any] | None = None,
        scalar_value: Any = None,
        one_or_none_value: Any = None,
    ) -> None:
        self._rows = rows if rows is not None else []
        self._scalar_value = scalar_value
        self._one_or_none_value = one_or_none_value

    def scalar(self) -> Any:
        return self._scalar_value

    def scalar_one_or_none(self) -> Any:
        return self._scalar_value

    def one_or_none(self) -> Any:
        if self._one_or_none_value is not None:
            return self._one_or_none_value
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalars(self) -> FakeScalars:
        return FakeScalars(self._rows)


class FakeSession:
    """Deterministic fake SQLAlchemy Session returning queued FakeResult responses."""

    def __init__(self, responses: list[FakeResult | Exception]) -> None:
        self._responses = list(responses)
        self.executed_statements: list[Any] = []

    def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> FakeResult:
        self.executed_statements.append(stmt)
        if not self._responses:
            raise AssertionError("FakeSession ran out of queued responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _make_client(fake_db: FakeSession) -> TestClient:
    app = create_app()

    def _override_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def _sample_municipality() -> SimpleNamespace:
    return SimpleNamespace(
        state_id=9,
        municipality_id=15,
        municipality_name="Cuauhtémoc",
        lat=Decimal("19.432600"),
        lon=Decimal("-99.133200"),
        elevation_m=Decimal("2240.00"),
        utc_offset_hours=-6,
        is_ml_cohort=True,
    )


def test_health_ok_with_model_and_ingestion_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "latest.json").write_text(
        json.dumps({"model_version": "mos_v1_20261118"}), encoding="utf-8"
    )
    monkeypatch.setattr(settings, "mxweather_artifacts_dir", tmp_path)

    run_obj = SimpleNamespace(
        run_id=uuid.UUID("11111111-2222-3333-4444-555555555555"),
        source_provider="open_meteo_forecast",
        status="SUCCESS",
        started_at=datetime(2026, 11, 18, 12, 0, tzinfo=UTC),
        completed_at=datetime(2026, 11, 18, 12, 1, tzinfo=UTC),
        records_loaded=128,
        records_quarantined=0,
    )
    db = FakeSession(
        [
            FakeResult(scalar_value="3.4 USE_GEOS=1 USE_PROJ=1"),
            FakeResult(scalar_value=run_obj),
        ]
    )
    client = _make_client(db)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database_ok"] is True
    assert "3.4" in body["postgis_version"]
    assert body["active_model_version"] == "mos_v1_20261118"
    assert body["latest_ingestion_run"]["status"] == "SUCCESS"
    assert body["latest_ingestion_run"]["records_loaded"] == 128


def test_health_degraded_when_db_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mxweather_artifacts_dir", tmp_path)
    db = FakeSession([RuntimeError("connection refused")])
    client = _make_client(db)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["database_ok"] is False
    assert body["active_model_version"] is None


def test_states_and_municipalities_endpoints() -> None:
    states = [
        SimpleNamespace(state_id=9, state_name="Ciudad de México"),
        SimpleNamespace(state_id=31, state_name="Yucatán"),
    ]
    mun = _sample_municipality()

    db = FakeSession(
        [
            FakeResult(rows=states),
            FakeResult(rows=[(mun, "Ciudad de México", 4.215)]),
        ]
    )
    client = _make_client(db)

    st_resp = client.get("/v1/states")
    assert st_resp.status_code == 200
    assert st_resp.json()["count"] == 2
    assert st_resp.json()["items"][0]["state_name"] == "Ciudad de México"

    mun_resp = client.get(
        "/v1/municipalities",
        params={"near_lat": 19.43, "near_lon": -99.13, "radius_km": 25.0, "cohort_only": True},
    )
    assert mun_resp.status_code == 200
    mun_body = mun_resp.json()
    assert mun_body["count"] == 1
    assert mun_body["items"][0]["municipality_name"] == "Cuauhtémoc"
    assert mun_body["items"][0]["distance_km"] == pytest.approx(4.215)

    # 422 when near_lat is provided without near_lon
    bad_resp = client.get("/v1/municipalities", params={"near_lat": 19.43})
    assert bad_resp.status_code == 422


def test_municipality_forecast_and_404() -> None:
    mun = _sample_municipality()
    issued = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    rev_rows = [
        SimpleNamespace(
            valid_date=date(2026, 11, 18),
            lead_day=0,
            issued_at=issued,
            source_provider="open_meteo_forecast",
            tmax_c=Decimal("24.50"),
            tmin_c=Decimal("10.00"),
            precip_mm=Decimal("18.20"),
            precip_prob_pct=70,
            wind_speed_kmh=Decimal("14.00"),
            wind_gust_kmh=Decimal("32.00"),
            cloud_cover_pct=Decimal("65.00"),
            sky_desc="Partly cloudy",
        )
    ]
    ml_rows = [
        SimpleNamespace(
            valid_date=date(2026, 11, 18),
            model_version="mos_v1",
            pred_tmax_p10=Decimal("21.80"),
            pred_tmax_p50=Decimal("23.20"),
            pred_tmax_p90=Decimal("24.90"),
            pred_tmin_p10=Decimal("8.50"),
            pred_tmin_p50=Decimal("9.60"),
            pred_tmin_p90=Decimal("10.80"),
            calibrated_rain_prob=Decimal("0.6400"),
            alert_level="ADVISORY",
        )
    ]
    db = FakeSession(
        [
            FakeResult(one_or_none_value=(mun, "Ciudad de México")),
            FakeResult(rows=rev_rows),
            FakeResult(rows=ml_rows),
            FakeResult(one_or_none_value=None),
        ]
    )
    client = _make_client(db)

    ok_resp = client.get("/v1/forecasts/9/15")
    assert ok_resp.status_code == 200
    body = ok_resp.json()
    assert body["municipality"]["municipality_name"] == "Cuauhtémoc"
    assert len(body["days"]) == 1
    day0 = body["days"][0]
    assert day0["pred_tmax_p10"] <= day0["pred_tmax_p50"] <= day0["pred_tmax_p90"]
    assert day0["ml_bias_delta_c"] == pytest.approx(-1.3)
    assert day0["alert_level"] == "ADVISORY"

    not_found = client.get("/v1/forecasts/9/999")
    assert not_found.status_code == 404


def test_forecast_map_snapshot() -> None:
    mun = _sample_municipality()
    rev_rows = [
        (
            mun,
            "Ciudad de México",
            date(2026, 11, 18),
            0,
            Decimal("25.00"),
            Decimal("10.50"),
            Decimal("4.00"),
            Decimal("22.00"),
        )
    ]
    ml_rows = [
        SimpleNamespace(
            state_id=9,
            municipality_id=15,
            valid_date=date(2026, 11, 18),
            pred_tmax_p50=Decimal("23.80"),
            pred_tmin_p50=Decimal("10.10"),
            calibrated_rain_prob=Decimal("0.3200"),
            alert_level="NORMAL",
        )
    ]
    db = FakeSession([FakeResult(rows=rev_rows), FakeResult(rows=ml_rows)])
    client = _make_client(db)

    resp = client.get("/v1/forecasts/map")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    pt = body["items"][0]
    assert pt["ml_bias_delta_c"] == pytest.approx(-1.2)
    assert pt["alert_level"] == "NORMAL"


def test_alerts_ordering_and_scope_note_honesty() -> None:
    issued = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    ml_rows = [
        SimpleNamespace(
            state_id=31,
            municipality_id=50,
            municipality_name="Mérida",
            state_name="Yucatán",
            valid_date=date(2026, 11, 18),
            lead_day=0,
            issued_at=issued,
            alert_level="ADVISORY",
            raw_tmax_c=Decimal("39.00"),
            pred_tmax_p50=Decimal("38.50"),
            raw_tmin_c=Decimal("24.00"),
            pred_tmin_p50=Decimal("23.80"),
            calibrated_rain_prob=Decimal("0.2000"),
        ),
        SimpleNamespace(
            state_id=9,
            municipality_id=15,
            municipality_name="Cuauhtémoc",
            state_name="Ciudad de México",
            valid_date=date(2026, 11, 18),
            lead_day=0,
            issued_at=issued,
            alert_level="WARNING_P0",
            raw_tmax_c=Decimal("22.00"),
            pred_tmax_p50=Decimal("21.50"),
            raw_tmin_c=Decimal("10.00"),
            pred_tmin_p50=Decimal("9.80"),
            calibrated_rain_prob=Decimal("0.8800"),
        ),
    ]
    rev_rows = [
        SimpleNamespace(
            state_id=9,
            municipality_id=15,
            valid_date=date(2026, 11, 18),
            precip_mm=Decimal("32.50"),
            wind_gust_kmh=Decimal("45.00"),
        )
    ]
    db = FakeSession([FakeResult(rows=ml_rows), FakeResult(rows=rev_rows)])
    client = _make_client(db)

    resp = client.get("/v1/alerts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert body["warning_p0_count"] == 1
    assert body["advisory_count"] == 1
    # WARNING_P0 must be sorted first
    assert body["items"][0]["alert_level"] == "WARNING_P0"
    assert body["items"][1]["alert_level"] == "ADVISORY"
    # Scope honesty check: must explicitly state municipal forecast thresholds and not C5 sensors
    note_lower = body["scope_note"].lower()
    assert "forecast threshold" in note_lower
    assert "not" in note_lower and "c5" in note_lower


def test_trends_series_and_validation() -> None:
    mun = _sample_municipality()
    obs_rows = [
        SimpleNamespace(
            obs_date=date(2026, 11, 16),
            tmax_c=Decimal("23.00"),
            tmin_c=Decimal("9.50"),
            precip_mm=Decimal("0.00"),
        )
    ]
    rev_rows = [
        SimpleNamespace(
            valid_date=date(2026, 11, 16),
            tmax_c=Decimal("24.20"),
            tmin_c=Decimal("10.10"),
        )
    ]
    ml_rows = [
        SimpleNamespace(
            valid_date=date(2026, 11, 16),
            pred_tmax_p50=Decimal("23.10"),
            pred_tmin_p50=Decimal("9.60"),
        )
    ]
    db = FakeSession(
        [
            FakeResult(one_or_none_value=(mun, "Ciudad de México")),
            FakeResult(rows=obs_rows),
            FakeResult(rows=rev_rows),
            FakeResult(rows=ml_rows),
        ]
    )
    client = _make_client(db)

    resp = client.get(
        "/v1/trends/9/15",
        params={"start_date": "2026-11-01", "end_date": "2026-11-18"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    pt = body["series"][0]
    assert pt["date"] == "2026-11-16"
    assert pt["obs_tmax_c"] == pytest.approx(23.0)
    assert pt["raw_tmax_c"] == pytest.approx(24.2)
    assert pt["ml_tmax_p50"] == pytest.approx(23.1)

    bad_dates = client.get(
        "/v1/trends/9/15",
        params={"start_date": "2026-11-20", "end_date": "2026-11-10"},
    )
    assert bad_dates.status_code == 422


def test_ml_skill_grouped_metrics_and_calibration_curve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_dir = tmp_path / "models" / "mos_v1"
    model_dir.mkdir(parents=True)
    (tmp_path / "models" / "latest.json").write_text(
        json.dumps({"model_version": "mos_v1"}), encoding="utf-8"
    )
    (model_dir / "calibration_curve.json").write_text(
        json.dumps(
            [
                {
                    "bin_index": 0,
                    "bin_lower": 0.0,
                    "bin_upper": 0.1,
                    "mean_predicted_prob": 0.04,
                    "fraction_of_positives": 0.05,
                    "count": 120,
                },
                {
                    "bin_index": 9,
                    "bin_lower": 0.9,
                    "bin_upper": 1.0,
                    "mean_predicted_prob": 0.93,
                    "fraction_of_positives": 0.91,
                    "count": 45,
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "mxweather_artifacts_dir", tmp_path)

    now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    eval_rows = [
        SimpleNamespace(
            split_name="test",
            segment_key="overall",
            metric_name="tmax_mae",
            raw_baseline_val=1.85,
            ml_model_val=1.22,
            persistence_val=2.40,
            climatology_val=3.10,
            sample_count=2880,
            evaluated_at=now,
        ),
        SimpleNamespace(
            split_name="test",
            segment_key="elevation:highland",
            metric_name="tmax_mae",
            raw_baseline_val=2.10,
            ml_model_val=1.35,
            persistence_val=2.50,
            climatology_val=3.00,
            sample_count=900,
            evaluated_at=now,
        ),
        SimpleNamespace(
            split_name="test",
            segment_key="season:rainy",
            metric_name="rain_brier",
            raw_baseline_val=0.19,
            ml_model_val=0.14,
            persistence_val=None,
            climatology_val=0.22,
            sample_count=1440,
            evaluated_at=now,
        ),
    ]
    db = FakeSession([FakeResult(rows=eval_rows)])
    client = _make_client(db)

    resp = client.get("/v1/ml/skill")
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_version"] == "mos_v1"
    assert len(body["metrics"]["overall"]) == 1
    assert len(body["metrics"]["by_elevation_band"]) == 1
    assert len(body["metrics"]["by_season"]) == 1
    assert len(body["calibration_curve"]) == 2
    assert body["calibration_curve"][0]["mean_predicted_prob"] == pytest.approx(0.04)


def _export_fake_db() -> FakeSession:
    mun = _sample_municipality()
    obs_rows = [
        SimpleNamespace(
            state_id=9,
            municipality_id=15,
            obs_date=date(2026, 11, 17),
            tmax_c=Decimal("23.40"),
            tmin_c=Decimal("9.80"),
            precip_mm=Decimal("1.20"),
        ),
        SimpleNamespace(
            state_id=9,
            municipality_id=15,
            obs_date=date(2026, 11, 18),
            tmax_c=Decimal("22.90"),
            tmin_c=Decimal("9.10"),
            precip_mm=Decimal("0.00"),
        ),
    ]
    rev_rows = [
        SimpleNamespace(
            state_id=9,
            municipality_id=15,
            valid_date=date(2026, 11, 17),
            tmax_c=Decimal("24.60"),
            tmin_c=Decimal("10.20"),
            precip_mm=Decimal("2.00"),
        )
    ]
    ml_rows = [
        SimpleNamespace(
            state_id=9,
            municipality_id=15,
            valid_date=date(2026, 11, 17),
            pred_tmax_p50=Decimal("23.50"),
            pred_tmin_p50=Decimal("9.70"),
            calibrated_rain_prob=Decimal("0.5500"),
            alert_level="NORMAL",
        )
    ]
    return FakeSession(
        [
            FakeResult(rows=[(mun, "Ciudad de México")]),
            FakeResult(rows=obs_rows),
            FakeResult(rows=rev_rows),
            FakeResult(rows=ml_rows),
        ]
    )


def test_export_csv_sha256_and_headers() -> None:
    client = _make_client(_export_fake_db())
    resp = client.get(
        "/v1/export",
        params={
            "state_id": 9,
            "municipality_id": 15,
            "start_date": "2026-11-17",
            "end_date": "2026-11-18",
            "format": "csv",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["X-Row-Count"] == "2"
    assert resp.headers["X-Content-SHA256"] == hashlib.sha256(resp.content).hexdigest()
    assert "mx-weather-intel" in resp.headers["X-Data-Provenance"]
    assert "attachment; filename=" in resp.headers["Content-Disposition"]
    assert resp.headers["Content-Disposition"].endswith('.csv"')

    parsed = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8"))))
    assert len(parsed) == 2
    assert parsed[0]["municipality_name"] == "Cuauhtémoc"
    assert parsed[0]["date"] == "2026-11-17"


def test_export_parquet_sha256_and_headers() -> None:
    client = _make_client(_export_fake_db())
    resp = client.get(
        "/v1/export",
        params={
            "state_id": 9,
            "municipality_id": 15,
            "start_date": "2026-11-17",
            "end_date": "2026-11-18",
            "format": "parquet",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["X-Row-Count"] == "2"
    assert resp.headers["X-Content-SHA256"] == hashlib.sha256(resp.content).hexdigest()
    assert "mx-weather-intel" in resp.headers["X-Data-Provenance"]
    assert resp.headers["Content-Disposition"].endswith('.parquet"')

    table = pq.read_table(io.BytesIO(resp.content))
    assert table.num_rows == 2
    assert "ml_tmax_p50" in table.column_names


def test_export_error_handling() -> None:
    db = FakeSession([FakeResult(rows=[])])
    client = _make_client(db)

    not_found = client.get("/v1/export", params={"state_id": 9, "municipality_id": 999, "format": "csv"})
    assert not_found.status_code == 404

    bad_format = client.get("/v1/export", params={"state_id": 9, "format": "xlsx"})
    assert bad_format.status_code == 422

    bad_dates = client.get(
        "/v1/export",
        params={"state_id": 9, "start_date": "2026-11-20", "end_date": "2026-11-10"},
    )
    assert bad_dates.status_code == 422
