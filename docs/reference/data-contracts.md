# Data & Quality Contracts (`S01`–`S08`, `Q01`–`Q08`)

Machine-readable definitions live in [`contracts/data_contracts.json`](../../contracts/data_contracts.json) and are enforced deterministically in [`src/mxweather/quality/gate.py`](../../src/mxweather/quality/gate.py) and PostgreSQL `CHECK` constraints in [`alembic/versions/0001_initial_schema.py`](../../alembic/versions/0001_initial_schema.py).

## Structural Contracts (`S01`–`S08`)

| ID | Rule | Enforcement |
| :--- | :--- | :--- |
| `S01` | Payload integrity (UTF-8 JSON / GZip + SHA-256 digest) | `validate_raw_payload()` + `raw_ingestion_run.payload_sha256` |
| `S02` | Required identifiers & core meteorological fields | `validate_batch()` quarantines missing keys |
| `S03` | Municipality FK exists in `dim_municipality` | `validate_batch()` + Postgres composite FK |
| `S04` | Mexico coordinate bounds (`14.0 <= lat <= 33.0`, `-119.0 <= lon <= -86.0`) | Postgres `CHECK` + `validate_batch()` |
| `S05` | Timezone-aware UTC `issued_at` & ISO-8601 `valid_date`/`obs_date` | `validate_batch()` + `TIMESTAMPTZ` / `DATE` |
| `S06` | `lead_day` in `[0, 7]` consistent with `valid_date - issued_at::date` | `validate_batch()` + Postgres `CHECK` |
| `S07` | Numeric strictness (no `NaN`/`Inf`, explicit `NULL`, zero silent coercion) | `validate_batch()` |
| `S08` | `source_provider` enum membership | `validate_batch()` |

## Semantic, Quality & Leakage Contracts (`Q01`–`Q08`)

| ID | Rule | Enforcement |
| :--- | :--- | :--- |
| `Q01` | `tmax_c >= tmin_c` | `validate_batch()` + Postgres `CHECK` |
| `Q02` | Physical temperature bounds `[-25.0, 55.0] °C` | `validate_batch()` + Postgres `CHECK` |
| `Q03` | `0.0 <= precip_mm <= 600.0` | `validate_batch()` + Postgres `CHECK` |
| `Q04` | `precip_prob_pct` is `NULL` or in `[0, 100]` | `validate_batch()` + Postgres `CHECK` |
| `Q05` | Non-negative wind speeds; flag warning if `wind_gust_kmh < wind_speed_kmh` | `validate_batch()` (quarantine negative, warn on gust inversion) |
| `Q06` | Idempotent replay yields zero duplicates | Composite PK + `ON CONFLICT DO UPDATE` + batch dedup check |
| `Q07` | Zero temporal leakage (`issued_at::date <= valid_date`; ML lags strictly `< valid_date - max(1, lead_day)`) | Postgres `CHECK` + `test_ml_leakage.py` future-perturbation test |
| `Q08` | ML cohort completeness `>= 95%` across 32 Mexican state capitals | `check_cohort_completeness()` in `run_backfill.py` |
