# ADR-0002: Revision-Aware Temporal & PostGIS Schema

- **Status:** Accepted (2026-11-18)
- **Context:** A naive primary key `(state_id, municipality_id, valid_date)` overwrites earlier forecasts when new runs arrive, destroying lead-time forecast revisions and preventing point-in-time ML verification.
- **Decision:** Model `fact_forecast_revision` with composite PK `(source_provider, state_id, municipality_id, issued_at, valid_date)`, integer `lead_day` (`0..7`), and PostgreSQL `CHECK (valid_date >= (issued_at AT TIME ZONE 'UTC')::date)`. Store municipal coordinates as PostGIS `GEOGRAPHY(POINT, 4326)` with a GiST spatial index to support exact spherical `ST_DWithin` / `ST_Distance` radius queries.
- **Consequences:** Full revision history, database-level temporal leakage prevention, and native geospatial API queries with zero external GIS servers.
