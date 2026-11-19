"""Offline unit tests for deterministic data contract quality gate (S01-S08, Q01-Q08)."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from mxweather.quality.gate import (
    PayloadIntegrityError,
    check_cohort_completeness,
    validate_batch,
    validate_raw_payload,
)

VALID_SHA256 = hashlib.sha256(b"test-payload").hexdigest()


def _valid_forecast_row(**overrides):
    row = {
        "source_provider": "open_meteo_forecast",
        "state_id": 9,
        "municipality_id": 15,
        "issued_at": datetime(2026, 5, 10, 6, 0, tzinfo=UTC),
        "valid_date": date(2026, 5, 11),
        "lead_day": 1,
        "tmax_c": Decimal("26.5"),
        "tmin_c": Decimal("12.3"),
        "precip_mm": Decimal("4.2"),
        "precip_prob_pct": 60,
        "wind_speed_kmh": Decimal("12.0"),
        "wind_gust_kmh": Decimal("24.5"),
        "cloud_cover_pct": Decimal("45.0"),
        "sky_desc": "Medio nublado",
        "lat": Decimal("19.4326"),
        "lon": Decimal("-99.1332"),
    }
    row.update(overrides)
    return row


def _valid_observed_row(**overrides):
    row = {
        "source_provider": "open_meteo_era5",
        "state_id": 9,
        "municipality_id": 15,
        "obs_date": date(2026, 5, 9),
        "tmax_c": Decimal("25.8"),
        "tmin_c": Decimal("11.9"),
        "precip_mm": Decimal("0.0"),
        "wind_speed_kmh": Decimal("10.5"),
        "wind_gust_kmh": Decimal("19.0"),
        "lat": Decimal("19.4326"),
        "lon": Decimal("-99.1332"),
    }
    row.update(overrides)
    return row


class TestS01PayloadIntegrity:
    def test_valid_utf8_json(self):
        raw = json.dumps({"ok": True}).encode("utf-8")
        parsed, digest = validate_raw_payload(raw)
        assert parsed == {"ok": True}
        assert digest == hashlib.sha256(raw).hexdigest()

    def test_valid_gzip_json(self):
        raw = gzip.compress(json.dumps([{"a": 1}]).encode("utf-8"))
        parsed, digest = validate_raw_payload(raw, expect_gzip=True)
        assert parsed == [{"a": 1}]
        assert digest == hashlib.sha256(raw).hexdigest()

    def test_html_payload_raises_s01(self):
        with pytest.raises(PayloadIntegrityError) as exc_info:
            validate_raw_payload(b"<html><body>500 Error</body></html>")
        assert exc_info.value.rule_id == "S01"

    def test_corrupt_gzip_raises_s01(self):
        with pytest.raises(PayloadIntegrityError) as exc_info:
            validate_raw_payload(b"\x1f\x8bnot-actually-gzip", expect_gzip=True)
        assert exc_info.value.rule_id == "S01"

    def test_invalid_batch_sha256_quarantines_s01(self):
        valid, quarantined, summary = validate_batch(
            [_valid_forecast_row()],
            record_type="forecast",
            payload_sha256="not-a-sha256",
        )
        assert len(valid) == 0
        assert len(quarantined) == 1
        assert "S01" in quarantined[0]["reasons"]
        assert summary["rule_failure_counts"]["S01"] == 1


class TestStructuralRulesS02ToS08:
    def test_valid_forecast_and_observed_pass(self):
        valid_f, quar_f, summary_f = validate_batch(
            [_valid_forecast_row()],
            record_type="forecast",
            valid_municipalities={(9, 15)},
            payload_sha256=VALID_SHA256,
        )
        assert len(valid_f) == 1
        assert len(quar_f) == 0
        assert summary_f["valid_count"] == 1
        assert summary_f["quarantined_count"] == 0

        valid_o, quar_o, summary_o = validate_batch(
            [_valid_observed_row()],
            record_type="observed",
            valid_municipalities={(9, 15)},
            payload_sha256=VALID_SHA256,
        )
        assert len(valid_o) == 1
        assert len(quar_o) == 0
        assert summary_o["valid_count"] == 1

    def test_s02_missing_required_fields(self):
        row = _valid_forecast_row(tmax_c=None)
        valid, quar, summary = validate_batch([row], record_type="forecast")
        assert len(valid) == 0
        assert "S02" in quar[0]["reasons"]
        assert summary["rule_failure_counts"]["S02"] == 1

    def test_s03_unknown_municipality_fk(self):
        row = _valid_forecast_row(state_id=99, municipality_id=999)
        valid, quar, _ = validate_batch(
            [row], record_type="forecast", valid_municipalities={(9, 15)}
        )
        assert len(valid) == 0
        assert "S03" in quar[0]["reasons"]

    @pytest.mark.parametrize(
        "lat,lon",
        [
            (Decimal("13.9"), Decimal("-99.1")),
            (Decimal("33.5"), Decimal("-99.1")),
            (Decimal("19.4"), Decimal("-120.0")),
            (Decimal("19.4"), Decimal("-85.0")),
        ],
    )
    def test_s04_coordinate_out_of_mexico_bounds(self, lat, lon):
        row = _valid_forecast_row(lat=lat, lon=lon)
        valid, quar, _ = validate_batch([row], record_type="forecast")
        assert len(valid) == 0
        assert "S04" in quar[0]["reasons"]

    def test_s05_naive_and_non_utc_issued_at_rejected(self):
        naive_row = _valid_forecast_row(issued_at=datetime(2026, 5, 10, 6, 0))
        non_utc_tz = timezone(timedelta(hours=-6))
        offset_row = _valid_forecast_row(issued_at=datetime(2026, 5, 10, 6, 0, tzinfo=non_utc_tz))
        bad_date_row = _valid_forecast_row(valid_date="2026/05/11")

        valid, quar, summary = validate_batch(
            [naive_row, offset_row, bad_date_row], record_type="forecast"
        )
        assert len(valid) == 0
        assert len(quar) == 3
        assert summary["rule_failure_counts"]["S05"] == 3

    def test_s06_lead_day_domain_and_consistency(self):
        too_high = _valid_forecast_row(lead_day=8, valid_date=date(2026, 5, 18))
        mismatch = _valid_forecast_row(lead_day=2, valid_date=date(2026, 5, 11))
        valid, quar, summary = validate_batch([too_high, mismatch], record_type="forecast")
        assert len(valid) == 0
        assert summary["rule_failure_counts"]["S06"] == 2

    @pytest.mark.parametrize(
        "bad_val",
        [float("nan"), float("inf"), float("-inf"), "N/A", "", True],
    )
    def test_s07_numeric_strictness_no_silent_coercion(self, bad_val):
        row = _valid_forecast_row(precip_mm=bad_val)
        valid, quar, _ = validate_batch([row], record_type="forecast")
        assert len(valid) == 0
        assert "S07" in quar[0]["reasons"]
        # Verify original row was NOT mutated or coerced to 0
        assert quar[0]["row"]["precip_mm"] is bad_val or quar[0]["row"]["precip_mm"] == bad_val

    def test_s08_invalid_source_provider(self):
        row = _valid_forecast_row(source_provider="unknown_vendor")
        valid, quar, _ = validate_batch([row], record_type="forecast")
        assert len(valid) == 0
        assert "S08" in quar[0]["reasons"]


class TestQualityRulesQ01ToQ08:
    def test_q01_tmax_less_than_tmin(self):
        row = _valid_forecast_row(tmax_c=Decimal("10.0"), tmin_c=Decimal("15.0"))
        valid, quar, _ = validate_batch([row], record_type="forecast")
        assert len(valid) == 0
        assert "Q01" in quar[0]["reasons"]

    def test_q02_physical_temperature_bounds(self):
        too_hot = _valid_forecast_row(tmax_c=Decimal("58.0"))
        too_cold = _valid_forecast_row(tmin_c=Decimal("-30.0"))
        valid, quar, summary = validate_batch([too_hot, too_cold], record_type="forecast")
        assert len(valid) == 0
        assert summary["rule_failure_counts"]["Q02"] == 2

    def test_q03_precipitation_bounds(self):
        neg = _valid_forecast_row(precip_mm=Decimal("-0.1"))
        extreme = _valid_forecast_row(precip_mm=Decimal("650.0"))
        valid, quar, summary = validate_batch([neg, extreme], record_type="forecast")
        assert len(valid) == 0
        assert summary["rule_failure_counts"]["Q03"] == 2

    def test_q04_precip_prob_range(self):
        neg = _valid_forecast_row(precip_prob_pct=-5)
        over = _valid_forecast_row(precip_prob_pct=105)
        valid, quar, summary = validate_batch([neg, over], record_type="forecast")
        assert len(valid) == 0
        assert summary["rule_failure_counts"]["Q04"] == 2

    def test_q05_wind_negative_quarantined_and_gust_less_than_speed_warned(self):
        neg_wind = _valid_forecast_row(wind_speed_kmh=Decimal("-2.0"))
        gust_lt_speed = _valid_forecast_row(
            valid_date=date(2026, 5, 12),
            lead_day=2,
            wind_speed_kmh=Decimal("20.0"),
            wind_gust_kmh=Decimal("15.0"),
        )
        valid, quar, summary = validate_batch([neg_wind, gust_lt_speed], record_type="forecast")
        assert len(quar) == 1
        assert "Q05" in quar[0]["reasons"]
        assert len(valid) == 1
        assert summary["warning_counts"]["Q05"] == 1

    def test_q06_duplicate_pk_in_batch_quarantined(self):
        r1 = _valid_forecast_row()
        r2 = _valid_forecast_row()
        valid, quar, summary = validate_batch([r1, r2], record_type="forecast")
        assert len(valid) == 1
        assert len(quar) == 1
        assert "Q06" in quar[0]["reasons"]
        assert summary["rule_failure_counts"]["Q06"] == 1

    def test_q07_temporal_leakage_quarantined(self):
        leaked = _valid_forecast_row(
            issued_at=datetime(2026, 5, 10, 6, 0, tzinfo=UTC),
            valid_date=date(2026, 5, 9),
            lead_day=0,
        )
        valid, quar, _ = validate_batch([leaked], record_type="forecast")
        assert len(valid) == 0
        assert "Q07" in quar[0]["reasons"]

    def test_q08_cohort_completeness(self):
        cohort = {(i, 1) for i in range(1, 33)}
        # 31/32 = 96.875% >= 95% -> PASS
        obs_ok = {(i, 1) for i in range(1, 32)}
        fcst_ok = {(i, 1) for i in range(1, 32)}
        res_pass = check_cohort_completeness(obs_ok, fcst_ok, cohort)
        assert res_pass["passed"] is True
        assert res_pass["paired_count"] == 31

        # 29/32 = 90.625% < 95% -> FAIL
        obs_low = {(i, 1) for i in range(1, 30)}
        res_fail = check_cohort_completeness(obs_low, fcst_ok, cohort)
        assert res_fail["passed"] is False
        assert len(res_fail["missing_municipalities"]) == 3
