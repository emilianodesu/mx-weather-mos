# Acceptance Plan & Verification Matrix (`G0`–`G6`)

This matrix mirrors the empirical verification standard of `hvfhv`. Every gate records exact commands, pass/fail status, and raw outputs in [`execution-log.md`](execution-log.md).

| Gate | Scope | Exact Command | Pass Criterion |
| :--- | :--- | :--- | :--- |
| **G0** | Environment, PostGIS & Alembic Migrations | `docker compose up -d postgres && docker compose run --rm pipeline alembic upgrade head && docker compose run --rm pipeline python scripts/seed_catalog.py` | PostGIS extension active; 32 states and 2,463 municipalities seeded with `GEOGRAPHY(POINT, 4326)` GiST index |
| **G1** | Ingestion & Data Contracts (`S01`–`S08`, `Q01`–`Q08`) | `docker compose run --rm pipeline pytest tests/unit/test_ingestion.py tests/unit/test_quality.py -q` | 100% pass; SMN golden fixture parses cleanly; HTTP 500/timeout degrades to `DEGRADED_UPSTREAM`; replay produces `0` duplicates |
| **G2** | Zero-Leakage & ML Unit Tests | `docker compose run --rm pipeline pytest tests/unit/test_ml_leakage.py tests/unit/test_ml_pipeline.py -q` | Future-perturbation test yields `0.0` feature diff; `p10 <= p50 <= p90` on 100% of predictions; SHA-256 tamper detection passes |
| **G3** | Real-Data 32-Capital × 24-Month Backfill & ML Training | `docker compose run --rm pipeline python scripts/run_backfill.py --months 24 && docker compose run --rm pipeline python scripts/run_daily_ingest.py && docker compose run --rm pipeline python scripts/train_models.py && docker compose run --rm pipeline python scripts/score_forecasts.py` | `Q08 >= 95%` cohort coverage; deterministic SHA-256 model bundle saved; honest walk-forward metrics logged against `raw_nwp`, `persistence`, and `climatology_30d` |
| **G4** | FastAPI Contract & Export Integrity | `docker compose run --rm pipeline pytest tests/api/test_api_contracts.py -q` | 100% pass; CSV and Parquet exports match `X-Content-SHA256`; `/v1/alerts` includes scope honesty notice |
| **G5** | GoogleUX Frontend Build & Accessibility Tests | `npm --prefix web run build && npm --prefix web test` | Zero TypeScript errors; Vite production bundle succeeds; WCAG landmark, P0 alert, Web Speech audio, and Export Receipt tests pass |
| **G6** | Full 4-Container Docker E2E Smoke | `docker compose up -d --build && docker compose run --rm pipeline pytest tests/integration/test_e2e_docker.py -q` | All 4 containers (`postgres`, `api`, `web`, `pipeline`) healthy; live API, PostGIS proximity, ML skill, verified CSV/Parquet exports, and Nginx `/api/` proxy pass end-to-end |
