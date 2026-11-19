# Architecture Overview (`mx-weather-intel`)

## 1. Purpose & System Boundary

`mx-weather-intel` is a single-workstation, Docker-reproducible **operational weather data platform and ML forecast verification/post-processing system** for Mexico. It combines:

- **Revision-aware Data Engineering:** Scheduled ingestion of municipal weather forecasts (`issued_at` × `valid_date` × `lead_day`) paired with ERA5-Land daily reanalysis ground truth in **PostgreSQL 16 + PostGIS**, guarded by 16 declarative data contracts (`S01`–`S08`, `Q01`–`Q08`).
- **Model Output Statistics (MOS) & Probability Calibration:** Point-in-time LightGBM quantile regressors (`p10`/`p50`/`p90` for `tmax`/`tmin`) and Isotonic-calibrated rain probability (`>= 1.0 mm`), validated via 4-fold expanding temporal walk-forward CV against `raw_nwp`, `persistence`, and `climatology_30d`.
- **Typed Serving & Accessible Presentation:** A **FastAPI** REST/export backend powering a **React + TypeScript + Tailwind + Leaflet + Recharts** web portal built directly on a **WCAG 2.1 AAA** design system (Deep Navy `#0B2545`, Amber P0 `#FFFBEB`/`#D97706`, `48×48px` touch targets, Web Speech API audio summary).

## 2. Container Topology (`docker-compose.yml`)

| Service | Base Image | Responsibility | Port (Loopback) |
| :--- | :--- | :--- | :---: |
| `postgres` | `postgis/postgis:16-3.4-alpine` | Normalized + geospatial storage (`GEOGRAPHY(POINT, 4326)`, GiST index) | `127.0.0.1:5432` |
| `pipeline` | `python:3.12-slim` | Alembic migrations, catalog seed, backfill, daily ingest, ML train/score, pytest | On-demand (`tools` profile) |
| `api` | `python:3.12-slim` | FastAPI + Uvicorn serving `/health` and `/v1/*` + verified `CSV`/`Parquet` export | `127.0.0.1:8000` |
| `web` | `node:22-alpine` → `nginx:1.27-alpine` | Responsive WCAG 2.1 AAA SPA + reverse proxy `/api/` → `api:8000` | `127.0.0.1:5173` |

## 3. Architecture Decision Records (ADRs)

- [ADR-0001: Dual-Source Resilient Ingestion](decisions/ADR-0001-dual-source-resilience.md)
- [ADR-0002: Revision-Aware Temporal & PostGIS Schema](decisions/ADR-0002-revision-aware-postgis-schema.md)
- [ADR-0003: LightGBM MOS Quantile Regressors & Isotonic Rain Calibration](decisions/ADR-0003-mos-lightgbm-and-calibration.md)
- [ADR-0004: Local SHA-256 Model Registry vs. Heavy MLOps Servers](decisions/ADR-0004-local-sha256-model-registry.md)
- [ADR-0005: GoogleUX Design System Adaptation & Honesty Boundaries](decisions/ADR-0005-googleux-scope-honesty.md)
