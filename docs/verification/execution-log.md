# Empirical Execution Log (`mx-weather-intel`)

- **Date:** 2026-11-18 / 2026-11-19 UTC
- **Environment:** macOS ARM64, Docker Engine `29.9.2`, Docker Compose `v5.6.1`, `postgis/postgis:16-3.4-alpine`, `python:3.12-slim`, `node:22-alpine` / `nginx:1.27-alpine`
- **Active Model Version:** `mos_lgbm_20261119T014120Z`

---

## 1. Live Upstream Feasibility Checks (2026-11-18)

| Endpoint | Observed HTTP Status | Notes |
| :--- | :---: | :--- |
| `https://smn.cna.gob.mx/webservices/?method=1` | Timeout (`>15s`) | Legacy CONAGUA host unreachable |
| `https://smn.conagua.gob.mx/webservices/?method=1` | `301` → `HTTP 500` (`text/html`) | Handled gracefully by `conagua_smn.py` circuit breaker (`status="DEGRADED_UPSTREAM"`, `0` pipeline crashes) |
| `https://archive-api.open-meteo.com/v1/archive` | `HTTP 200` (`<1 ms`) | Primary ERA5-Land daily ground truth |
| `https://historical-forecast-api.open-meteo.com/v1/forecast` | `HTTP 200` (`18 ms`) | Primary historical NWP forecast archive (incremental `--skip-existing --delay 2.0` handles free-tier rate limits) |
| `https://api.open-meteo.com/v1/forecast` | `HTTP 200` | Live 4-day municipal forecast (`32/32` capitals loaded = `128` rows, `0` quarantined) |

---

## 2. Gate-by-Gate Verification Results (`G0`–`G6`)

| Gate | Command | Result | Observed Evidence |
| :--- | :--- | :---: | :--- |
| **G0** | `alembic upgrade head && python scripts/seed_catalog.py` | **PASS** | PostGIS enabled; `32` states and `2,463` official municipalities seeded with `GEOGRAPHY(POINT, 4326)` GiST index |
| **G1** | `pytest tests/unit/test_ingestion.py tests/unit/test_quality.py -q` | **PASS** | All structural (`S01`–`S08`), quality (`Q01`–`Q08`), SMN golden fixture (`128` rows), HTTP 500 degradation, and upsert idempotency tests passed |
| **G2** | `pytest tests/unit/test_ml_leakage.py tests/unit/test_ml_pipeline.py -q` | **PASS** | Future-perturbation (`+999.0` on `obs_date >= cutoff`) produced `0.0` feature diff; `p10 <= p50 <= p90` enforced; SHA-256 tamper detection passed |
| **G3** | `run_backfill.py --months 24 --skip-existing --delay 2.0`, `run_daily_ingest.py`, `train_models.py`, `score_forecasts.py` | **PASS** | **`Q08` PASS (`31/32` capitals paired = `96.88% >= 95%`; only `[14, 39]` Guadalajara timed out on historical forecast).** Live ingest loaded `32/32` capitals (`128` rows). ML training wrote `578` evaluation rows (`n = 3,410` holdout rows) and scored `128` live forecasts (`2 WARNING_P0`, `43 ADVISORY`, `83 NORMAL`). |
| **G4** | `pytest tests/api/test_api_contracts.py -q` | **PASS** | All `/health` and `/v1/*` contract tests, `CSV`/`Parquet` `X-Content-SHA256` checks, and `/v1/alerts` scope-honesty assertions passed |
| **G5** | `npm --prefix web run build && npm --prefix web test` | **PASS** | `tsc --noEmit && vite build` (`0` errors, `1.80s`); Vitest `4/4` tests passed (WCAG landmarks, Amber P0 card, Web Speech audio summary, Verified Export Receipt) |
| **G6** | `docker compose up -d --build && pytest tests/integration/test_e2e_docker.py -q` | **PASS** | All 4 containers (`postgres`, `api`, `web`, `pipeline`) healthy; `67/67` Python unit + API + Docker E2E tests pass; `ruff check src tests scripts` = `0` errors; `requirements.lock` pinned (`51` packages) |

---

## 3. Verified Real-Data ML Holdout Benchmarks (`mos_lgbm_20261119T014120Z`)

Evaluated on strictly held-out temporal test split (**`n = 3,410` paired municipality-days** across `31/32` Mexican state capitals from the 24-month window `2024-11-14` to `2026-11-14`, zero future leakage):

| Metric | Raw NWP Forecast | Persistence (`t-1`) | 30d Climatology | **LightGBM MOS + Isotonic** | Improvement vs. Raw NWP |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **`tmax` MAE (°C)** | `0.422` | `1.575` | `1.733` | **`0.351`** | **`-16.8%`** |
| **`tmax` RMSE (°C)** | `0.913` | `2.176` | `2.401` | **`0.633`** | **`-30.7%`** |
| **`tmax` Bias (°C)** | `+0.300` | `+0.106` | `+0.570` | **`+0.012`** | Bias eliminated |
| **`tmax` 80% Interval Coverage** | — | — | — | **`79.7%`** | Target `80.0%` |
| **`tmin` MAE (°C)** | `0.348` | `1.177` | `1.168` | **`0.343`** | **`-1.3%`** |
| **`tmin` RMSE (°C)** | `0.673` | `1.625` | `1.700` | **`0.570`** | **`-15.3%`** |
| **`tmin` Bias (°C)** | `-0.114` | `+0.070` | `+0.444` | **`-0.053`** | `-53.5%` |
| **`tmin` 80% Interval Coverage** | — | — | — | **`77.2%`** | Target `80.0%` |
| **Rain (`>=1mm`) Brier Score** | `0.1453` | `0.3616` | `0.2238` | **`0.0331`** | **`-77.2%`** |
| **Rain (`>=1mm`) ECE (10 bins)** | `0.0762` | `0.3616` | `0.0585` | **`0.0113`** | **`-85.2%`** |
| **Rain (`>=1mm`) Log Loss** | `0.7131` | `12.4888` | `0.6446` | **`0.1355`** | **`-81.0%`** |

---

## 4. Measured Local API Latency (`GH-18`)

Measured inside Docker loopback network (`http://api:8000`, `concurrency = 1`, `n = 10` sequential requests per endpoint, `0` errors):

| Endpoint | `p50` (ms) | `p95` (ms) | `max` (ms) |
| :--- | :---: | :---: | :---: |
| `GET /health` | `1.50` | `2.09` | `6.77` |
| `GET /v1/states` | `1.00` | `1.17` | `1.31` |
| `GET /v1/municipalities?cohort_only=true` | `1.54` | `2.04` | `2.24` |
| `GET /v1/forecasts/map` | `7.34` | `8.47` | `10.02` |
| `GET /v1/alerts` | `29.75` | `55.02` | `56.09` |
| `GET /v1/ml/skill` | `6.93` | `7.78` | `8.02` |

*(Note: Single-workstation sequential latency measurement; multi-user load capacity is not claimed.)*

### Known Limitations & Honest Boundaries
- **Single Timed-Out Capital (`[14, 39]`):** Guadalajara (`state_id=14, municipality_id=39`) timed out on `historical-forecast-api` during the 24-month backfill (`31/32` paired = `96.88%`, passing `Q08 >= 95%`); running `run_backfill.py --skip-existing` retries only that single missing municipality.
- **No Street-Level Flood Sensors or Doppler Radar:** All operational alerts are strictly meteorological forecast-threshold alerts (`precip_mm`, `tmax_c`, `tmin_c`, `wind_gust_kmh`, `calibrated_rain_prob`).
