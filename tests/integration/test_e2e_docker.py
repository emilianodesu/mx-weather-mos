"""Gate G6 end-to-end integration tests against the running Docker Compose stack."""

from __future__ import annotations

import hashlib
import os

import httpx
import pytest

API_URL = os.environ.get("MXWEATHER_API_URL", "http://api:8000")
WEB_URL = os.environ.get("MXWEATHER_WEB_URL", "http://web:80")


@pytest.fixture(scope="module")
def api_client() -> httpx.Client:
    with httpx.Client(base_url=API_URL, timeout=15.0) as client:
        yield client


def test_health_postgis_and_model(api_client: httpx.Client) -> None:
    resp = api_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database_ok"] is True
    assert body["postgis_version"] is not None
    assert body["active_model_version"] is not None


def test_states_and_cohort_municipalities(api_client: httpx.Client) -> None:
    states = api_client.get("/v1/states").json()
    assert states["count"] == 32

    cohort = api_client.get("/v1/municipalities", params={"cohort_only": True}).json()
    assert cohort["count"] == 32

    # PostGIS proximity query near Mexico City Zocalo
    near = api_client.get(
        "/v1/municipalities",
        params={"near_lat": 19.4326, "near_lon": -99.1332, "radius_km": 25.0, "limit": 5},
    ).json()
    assert near["count"] >= 1
    assert near["items"][0]["distance_km"] is not None


def test_map_forecasts_and_municipality_forecast(api_client: httpx.Client) -> None:
    map_data = api_client.get("/v1/forecasts/map").json()
    assert map_data["count"] >= 30
    sample = map_data["items"][0]

    fcst = api_client.get(
        f"/v1/forecasts/{sample['state_id']}/{sample['municipality_id']}"
    ).json()
    assert len(fcst["days"]) >= 1
    for day in fcst["days"]:
        if day["pred_tmax_p50"] is not None:
            assert day["pred_tmax_p10"] <= day["pred_tmax_p50"] <= day["pred_tmax_p90"]
            assert day["pred_tmin_p10"] <= day["pred_tmin_p50"] <= day["pred_tmin_p90"]
            assert 0.0 <= day["calibrated_rain_prob"] <= 1.0


def test_alerts_scope_honesty(api_client: httpx.Client) -> None:
    alerts = api_client.get("/v1/alerts").json()
    assert "threshold" in alerts["scope_note"].lower()
    assert "c5" in alerts["scope_note"].lower() or "street" in alerts["scope_note"].lower()


def test_trends_and_ml_skill(api_client: httpx.Client) -> None:
    cohort = api_client.get("/v1/municipalities", params={"cohort_only": True}).json()["items"]
    first = cohort[0]
    trends = api_client.get(
        f"/v1/trends/{first['state_id']}/{first['municipality_id']}"
    ).json()
    assert trends["count"] >= 30

    skill = api_client.get("/v1/ml/skill").json()
    assert skill["model_version"] is not None
    assert len(skill["metrics"]["overall"]) >= 4
    assert len(skill["calibration_curve"]) >= 1


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_verified_export_sha256(api_client: httpx.Client, fmt: str) -> None:
    cohort = api_client.get("/v1/municipalities", params={"cohort_only": True}).json()["items"]
    first = cohort[0]
    resp = api_client.get(
        "/v1/export",
        params={
            "state_id": first["state_id"],
            "municipality_id": first["municipality_id"],
            "format": fmt,
        },
    )
    assert resp.status_code == 200
    expected_sha = resp.headers["X-Content-SHA256"]
    actual_sha = hashlib.sha256(resp.content).hexdigest()
    assert actual_sha == expected_sha
    assert int(resp.headers["X-Row-Count"]) > 0


def test_web_spa_and_proxy() -> None:
    with httpx.Client(base_url=WEB_URL, timeout=10.0) as web:
        root = web.get("/")
        assert root.status_code == 200
        assert "<!doctype html>" in root.text.lower()

        proxied = web.get("/api/health")
        assert proxied.status_code == 200
        assert proxied.json()["database_ok"] is True
