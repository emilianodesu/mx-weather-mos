"""Offline unit tests for Open-Meteo and CONAGUA SMN ingestion adapters."""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy.dialects import postgresql

from mxweather.db.models import RawIngestionRun
from mxweather.ingestion.conagua_smn import (
    decode_smn_payload,
    fetch_live_smn,
    load_smn_fixture,
    parse_smn_records,
)
from mxweather.ingestion.open_meteo import (
    archive_raw_payload,
    fetch_era5_archive,
    fetch_historical_forecast,
    fetch_live_forecast,
    upsert_forecast_revisions,
    upsert_observed_daily,
)
from mxweather.quality.gate import validate_batch

REPO_ROOT = Path(__file__).resolve().parents[2]
SMN_GOLDEN_FIXTURE = REPO_ROOT / "fixtures" / "smn_golden_sample.json"


class _FakeSession:
    """Minimal SQLAlchemy Session stand-in for offline unit tests."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.executed_stmts: list[object] = []
        self.flushed = False

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flushed = True

    def execute(self, stmt: object) -> None:
        self.executed_stmts.append(stmt)


class TestConaguaSmnAdapter:
    def test_parse_smn_golden_sample_fixture(self):
        assert SMN_GOLDEN_FIXTURE.exists(), "Expected fixtures/smn_golden_sample.json to exist"
        rows, sha256 = load_smn_fixture(SMN_GOLDEN_FIXTURE)
        assert len(rows) > 0
        assert len(sha256) == 64

        first = rows[0]
        assert first["source_provider"] == "conagua_smn"
        assert first["state_id"] == 29
        assert first["municipality_id"] == 26
        assert first["valid_date"] == date(2024, 10, 29)
        assert first["lead_day"] == 0
        assert isinstance(first["lead_day"], int)
        # Verify raf (wind gust) is explicitly preserved
        assert first["wind_gust_kmh"] == Decimal("25.9")
        assert first["wind_speed_kmh"] == Decimal("6.0")
        assert first["tmax_c"] == Decimal("20.9")
        assert first["tmin_c"] == Decimal("8.9")
        assert first["precip_mm"] == Decimal("9.4")
        assert first["precip_prob_pct"] == 90
        assert first["cloud_cover_pct"] == Decimal("66.59")
        assert first["sky_desc"] == "Poco nuboso"

        # Verify all ndia values are integers in 0..3
        lead_days = {r["lead_day"] for r in rows}
        assert lead_days.issubset({0, 1, 2, 3})
        assert {0, 1, 2, 3}.issubset(lead_days)

        # Validate golden sample through the S01-S08 / Q01-Q08 gate
        valid, quarantined, summary = validate_batch(
            rows, record_type="forecast", payload_sha256=sha256
        )
        assert len(valid) > 0
        assert summary["valid_count"] == len(valid)
        assert summary["quarantined_count"] == len(quarantined)

    def test_decode_gzip_smn_payload(self):
        sample = [
            {
                "ides": "9",
                "idmun": "15",
                "dloc": "20260510T00",
                "ndia": "0",
                "tmax": "25.0",
                "tmin": "11.5",
                "prec": "2.1",
                "probprec": "40",
                "cc": "50.0",
                "velvien": "10.0",
                "raf": "22.5",
                "desciel": "Medio nublado",
                "lat": "19.4326",
                "lon": "-99.1332",
            }
        ]
        gz_bytes = gzip.compress(json.dumps(sample).encode("utf-8"))
        records, digest = decode_smn_payload(gz_bytes)
        assert len(records) == 1
        assert digest == hashlib.sha256(gz_bytes).hexdigest()
        parsed = parse_smn_records(records)
        assert parsed[0]["wind_gust_kmh"] == Decimal("22.5")
        assert parsed[0]["lead_day"] == 0

    def test_live_smn_http_500_degrades_gracefully(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                500,
                content=b"<html><body>Internal Server Error</body></html>",
                headers={"content-type": "text/html"},
            )

        session = _FakeSession()
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            res = fetch_live_smn(session=session, url="https://smn.conagua.gob.mx/test", client=client)

        assert res.status == "DEGRADED_UPSTREAM"
        assert res.rows == []
        assert res.error_detail is not None and "HTTP 500" in res.error_detail
        assert len(session.added) == 1
        audit_row = session.added[0]
        assert isinstance(audit_row, RawIngestionRun)
        assert audit_row.source_provider == "conagua_smn"
        assert audit_row.status == "DEGRADED_UPSTREAM"

    def test_live_smn_timeout_degrades_gracefully(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Upstream timed out")

        session = _FakeSession()
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            res = fetch_live_smn(session=session, url="https://smn.conagua.gob.mx/test", client=client)

        assert res.status == "DEGRADED_UPSTREAM"
        assert res.rows == []
        assert res.error_detail is not None and "ReadTimeout" in res.error_detail
        assert len(session.added) == 1
        assert session.added[0].status == "DEGRADED_UPSTREAM"

    def test_live_smn_html_on_200_degrades_gracefully(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<!DOCTYPE html><html>Maintenance</html>")

        session = _FakeSession()
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            res = fetch_live_smn(session=session, url="https://smn.conagua.gob.mx/test", client=client)

        assert res.status == "DEGRADED_UPSTREAM"
        assert res.rows == []
        assert len(session.added) == 1
        assert session.added[0].status == "DEGRADED_UPSTREAM"

    def test_live_smn_success_archives_gzip(self, tmp_path: Path):
        sample = [
            {
                "ides": "9",
                "idmun": "15",
                "dloc": "20260510T00",
                "ndia": "0",
                "tmax": "26.0",
                "tmin": "12.0",
                "prec": "0.0",
                "probprec": "10",
                "cc": "20.0",
                "velvien": "8.0",
                "raf": "18.0",
                "desciel": "Despejado",
            }
        ]
        gz_bytes = gzip.compress(json.dumps(sample).encode("utf-8"))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=gz_bytes)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            res = fetch_live_smn(
                url="https://smn.conagua.gob.mx/test",
                client=client,
                data_dir=tmp_path,
            )

        assert res.status == "SUCCESS"
        assert len(res.rows) == 1
        assert res.raw_path is not None and res.raw_path.exists()
        assert res.raw_path.name == f"{res.payload_sha256}.json.gz"


class TestOpenMeteoAdapter:
    def test_fetch_era5_archive_parses_rows_and_elevation(self, tmp_path: Path):
        payload = {
            "latitude": 19.43,
            "longitude": -99.13,
            "elevation": 2240.0,
            "daily": {
                "time": ["2026-05-01", "2026-05-02"],
                "temperature_2m_max": [27.1, 26.4],
                "temperature_2m_min": [12.0, 11.5],
                "precipitation_sum": [0.0, 3.8],
                "wind_speed_10m_max": [11.2, 14.0],
                "wind_gusts_10m_max": [24.5, 29.1],
            },
        }
        raw = json.dumps(payload).encode("utf-8")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=raw)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            res = fetch_era5_archive(
                19.4326,
                -99.1332,
                date(2026, 5, 1),
                date(2026, 5, 2),
                state_id=9,
                municipality_id=15,
                client=client,
                data_dir=tmp_path,
            )

        assert res.source_provider == "open_meteo_era5"
        assert res.elevation_m == Decimal("2240.0")
        assert len(res.rows) == 2
        assert res.rows[0]["obs_date"] == date(2026, 5, 1)
        assert res.rows[0]["tmax_c"] == Decimal("27.1")
        assert res.raw_path is not None and res.raw_path.exists()
        assert res.payload_sha256 == hashlib.sha256(raw).hexdigest()

        valid, quar, _ = validate_batch(
            res.rows,
            record_type="observed",
            valid_municipalities={(9, 15)},
            payload_sha256=res.payload_sha256,
        )
        assert len(valid) == 2
        assert len(quar) == 0

    def test_fetch_historical_forecast_sets_lead_day_zero(self, tmp_path: Path):
        payload = {
            "latitude": 19.43,
            "longitude": -99.13,
            "elevation": 2240.0,
            "daily": {
                "time": ["2026-05-01"],
                "temperature_2m_max": [26.8],
                "temperature_2m_min": [11.9],
                "precipitation_sum": [1.2],
                "precipitation_probability_max": [35],
                "wind_speed_10m_max": [10.0],
                "wind_gusts_10m_max": [21.0],
            },
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=json.dumps(payload).encode("utf-8"))

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            res = fetch_historical_forecast(
                19.4326,
                -99.1332,
                date(2026, 5, 1),
                date(2026, 5, 1),
                state_id=9,
                municipality_id=15,
                client=client,
                data_dir=tmp_path,
            )

        assert res.source_provider == "open_meteo_historical_forecast"
        assert len(res.rows) == 1
        row = res.rows[0]
        assert row["lead_day"] == 0
        assert row["issued_at"] == datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
        assert row["valid_date"] == date(2026, 5, 1)
        assert row["precip_prob_pct"] == 35

    def test_fetch_live_forecast_filters_past_dates_and_computes_lead_day(self, tmp_path: Path):
        issued = datetime(2026, 5, 10, 14, 37, tzinfo=UTC)
        payload = {
            "latitude": 19.43,
            "longitude": -99.13,
            "elevation": 2240.0,
            "daily": {
                # Includes a stale yesterday date that must be excluded to uphold Q07
                "time": ["2026-05-09", "2026-05-10", "2026-05-11", "2026-05-12", "2026-05-13"],
                "temperature_2m_max": [25.0, 26.0, 27.0, 28.0, 26.5],
                "temperature_2m_min": [11.0, 12.0, 12.5, 13.0, 12.0],
                "precipitation_sum": [0.0, 0.5, 2.0, 5.0, 1.0],
                "precipitation_probability_max": [10, 20, 45, 70, 30],
                "wind_speed_10m_max": [9.0, 10.0, 11.0, 12.0, 10.5],
                "wind_gusts_10m_max": [18.0, 20.0, 22.0, 25.0, 21.0],
            },
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=json.dumps(payload).encode("utf-8"))

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            res = fetch_live_forecast(
                19.4326,
                -99.1332,
                days=4,
                state_id=9,
                municipality_id=15,
                issued_at=issued,
                client=client,
                data_dir=tmp_path,
            )

        assert len(res.rows) == 4
        assert [r["lead_day"] for r in res.rows] == [0, 1, 2, 3]
        assert all(r["issued_at"] == datetime(2026, 5, 10, 14, 0, tzinfo=UTC) for r in res.rows)
        assert all(r["valid_date"] >= date(2026, 5, 10) for r in res.rows)

        valid, quar, _ = validate_batch(
            res.rows,
            record_type="forecast",
            valid_municipalities={(9, 15)},
            payload_sha256=res.payload_sha256,
        )
        assert len(valid) == 4
        assert len(quar) == 0

    def test_backoff_retries_transient_503_then_succeeds(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("mxweather.ingestion.open_meteo.time.sleep", lambda _: None)
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            if calls["count"] == 1:
                return httpx.Response(503, content=b"Service Unavailable")
            return httpx.Response(
                200,
                content=json.dumps({"elevation": 10.0, "daily": {"time": []}}).encode("utf-8"),
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            res = fetch_era5_archive(
                20.97,
                -89.62,
                date(2026, 5, 1),
                date(2026, 5, 1),
                state_id=31,
                municipality_id=50,
                client=client,
                data_dir=tmp_path,
            )

        assert calls["count"] == 2
        assert res.elevation_m == Decimal("10.0")

    def test_content_addressed_archive_is_idempotent(self, tmp_path: Path):
        raw = b'{"hello":"world"}'
        sha1, path1 = archive_raw_payload(raw, "open_meteo_era5", data_dir=tmp_path)
        sha2, path2 = archive_raw_payload(raw, "open_meteo_era5", data_dir=tmp_path)
        assert sha1 == sha2
        assert path1 == path2
        assert path1.read_bytes() == raw

    def test_idempotent_postgres_upsert_sql_generation(self):
        session = _FakeSession()
        run_id = uuid.uuid4()

        obs_rows = [
            {
                "source_provider": "open_meteo_era5",
                "state_id": 9,
                "municipality_id": 15,
                "obs_date": date(2026, 5, 1),
                "tmax_c": Decimal("25.0"),
                "tmin_c": Decimal("11.0"),
                "precip_mm": Decimal("0.0"),
                "wind_speed_kmh": Decimal("10.0"),
                "wind_gust_kmh": Decimal("20.0"),
                "lat": Decimal("19.43"),  # extra key stripped before DB insert
                "lon": Decimal("-99.13"),
            }
        ]
        count_obs = upsert_observed_daily(session, obs_rows, run_id)
        assert count_obs == 1
        sql_obs = str(session.executed_stmts[-1].compile(dialect=postgresql.dialect()))
        assert "INSERT INTO fact_observed_daily" in sql_obs
        assert "ON CONFLICT (source_provider, state_id, municipality_id, obs_date) DO UPDATE" in sql_obs
        assert "lat" not in sql_obs

        fcst_rows = [
            {
                "source_provider": "open_meteo_forecast",
                "state_id": 9,
                "municipality_id": 15,
                "issued_at": datetime(2026, 5, 10, 6, 0, tzinfo=UTC),
                "valid_date": date(2026, 5, 10),
                "lead_day": 0,
                "tmax_c": Decimal("26.0"),
                "tmin_c": Decimal("12.0"),
                "precip_mm": Decimal("1.0"),
                "precip_prob_pct": 30,
                "wind_speed_kmh": Decimal("10.0"),
                "wind_gust_kmh": Decimal("22.0"),
                "cloud_cover_pct": Decimal("40.0"),
                "sky_desc": None,
                "lat": Decimal("19.43"),
            }
        ]
        count_fcst = upsert_forecast_revisions(session, fcst_rows, run_id)
        assert count_fcst == 1
        sql_fcst = str(session.executed_stmts[-1].compile(dialect=postgresql.dialect()))
        assert "INSERT INTO fact_forecast_revision" in sql_fcst
        assert (
            "ON CONFLICT (source_provider, state_id, municipality_id, issued_at, valid_date) DO UPDATE"
            in sql_fcst
        )
