# mx-weather-intel — Project Engineering & Teamwork Rules

This repository implements **Mexico Weather Forecast Verification & ML Bias-Correction Platform (`mx-weather-intel`)**, a solo-authored, 100% local Docker-reproducible portfolio flagship covering **Junior Data Engineering** and **Junior AI/ML Engineering** tracks.

## Source of Truth & Scope Honesty

- Follow `docs/IMPLEMENTATION-PLAN.md` for architecture, schema, ML evaluation, API contracts, GoogleUX WCAG 2.1 AAA tokens, and Gates `G0`–`G6`.
- **No fabricated sensors or fake metrics:** Never simulate C5 street-level flood sensors, live Doppler radar feeds, or WhatsApp alerts. All alerts must be grounded in verifiable meteorological forecast thresholds (`precip_mm`, `precip_prob_pct`, `tmax_c`, `tmin_c`, `wind_gust_kmh`).
- **Dual-source resilience:** Open-Meteo (Forecast, Historical Forecast, ERA5 Archive) is the primary reliable source. CONAGUA SMN GZip JSON is a secondary adapter with graceful circuit-breaker handling (`DEGRADED_UPSTREAM`) and offline verification against `fixtures/smn_golden_sample.json`.
- **Zero temporal leakage:** ML features must strictly use observations with `obs_date < issued_at::date` (and lag windows strictly preceding `valid_date - max(1, lead_day)`). Every ML run must pass automated future-perturbation leakage tests and report honest comparisons against `raw_nwp`, `persistence`, and `climatology_30d`.
- **Attribution boundary:** `Projects/weather/Proyecto_Final` was an academic 4-person project and is referenced only as domain inspiration and sanitized municipal catalog provenance; do not copy legacy Pentaho XML or hardcoded credentials.

## Teamwork File Ownership

- **WS0 (Architect/Foundation):** `docker-compose.yml`, `pyproject.toml`, `Makefile`, `contracts/`, `alembic/`, `src/mxweather/db/`, `src/mxweather/config.py`, `fixtures/`
- **WS1 (DE Ingestion & Quality):** `src/mxweather/ingestion/`, `src/mxweather/quality/`, `scripts/run_backfill.py`, `scripts/run_daily_ingest.py`, `tests/unit/test_ingestion*.py`, `tests/unit/test_quality*.py`
- **WS2 (AI/ML Engineering):** `src/mxweather/ml/`, `scripts/train_models.py`, `scripts/score_forecasts.py`, `tests/unit/test_ml*.py`
- **WS3 (FastAPI Backend):** `src/mxweather/api/`, `tests/api/`
- **WS4 (GoogleUX Frontend):** `web/`
- **WS5 (QA, Docs & CI):** `tests/integration/`, `.github/workflows/ci.yml`, `docs/`, `README.md`

## Quality Standard

- All code, comments, commit messages, docs, and API payloads are in **English** (Spanish municipality/state names preserved verbatim as domain data).
- No hardcoded database passwords or secrets; load all configuration from environment variables (`.env.example`).
- Every gate `G0`–`G6` requires executed, reproducible command evidence logged in `docs/verification/execution-log.md`.
