"""Initial PostGIS-enabled revision-aware weather schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-18
"""

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    op.execute(
        """
        CREATE TABLE dim_state (
            state_id SMALLINT PRIMARY KEY,
            state_name TEXT NOT NULL
        );

        CREATE TABLE dim_municipality (
            state_id SMALLINT NOT NULL REFERENCES dim_state(state_id) ON DELETE CASCADE,
            municipality_id SMALLINT NOT NULL,
            municipality_name TEXT NOT NULL,
            lat NUMERIC(9, 6) NOT NULL CHECK (lat >= 14.0 AND lat <= 33.0),
            lon NUMERIC(9, 6) NOT NULL CHECK (lon >= -119.0 AND lon <= -86.0),
            elevation_m NUMERIC(7, 2),
            utc_offset_hours SMALLINT NOT NULL DEFAULT -6,
            is_ml_cohort BOOLEAN NOT NULL DEFAULT FALSE,
            geom GEOGRAPHY(POINT, 4326) NOT NULL,
            PRIMARY KEY (state_id, municipality_id)
        );
        CREATE INDEX ix_dim_municipality_geom ON dim_municipality USING GIST (geom);
        CREATE INDEX ix_dim_municipality_cohort ON dim_municipality (is_ml_cohort);

        CREATE TABLE raw_ingestion_run (
            run_id UUID PRIMARY KEY,
            source_provider TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            status TEXT NOT NULL,
            payload_sha256 TEXT,
            records_read INT NOT NULL DEFAULT 0,
            records_loaded INT NOT NULL DEFAULT 0,
            records_quarantined INT NOT NULL DEFAULT 0,
            error_detail TEXT
        );

        CREATE TABLE fact_forecast_revision (
            source_provider TEXT NOT NULL,
            state_id SMALLINT NOT NULL,
            municipality_id SMALLINT NOT NULL,
            issued_at TIMESTAMPTZ NOT NULL,
            valid_date DATE NOT NULL,
            lead_day SMALLINT NOT NULL CHECK (lead_day >= 0 AND lead_day <= 7),
            tmax_c NUMERIC(5, 2) NOT NULL CHECK (tmax_c >= -25.0 AND tmax_c <= 55.0),
            tmin_c NUMERIC(5, 2) NOT NULL CHECK (tmin_c >= -25.0 AND tmin_c <= 55.0),
            precip_mm NUMERIC(7, 2) CHECK (precip_mm IS NULL OR (precip_mm >= 0 AND precip_mm <= 600)),
            precip_prob_pct SMALLINT CHECK (precip_prob_pct IS NULL OR (precip_prob_pct >= 0 AND precip_prob_pct <= 100)),
            wind_speed_kmh NUMERIC(6, 2) CHECK (wind_speed_kmh IS NULL OR wind_speed_kmh >= 0),
            wind_gust_kmh NUMERIC(6, 2) CHECK (wind_gust_kmh IS NULL OR wind_gust_kmh >= 0),
            cloud_cover_pct NUMERIC(5, 2) CHECK (cloud_cover_pct IS NULL OR (cloud_cover_pct >= 0 AND cloud_cover_pct <= 100)),
            sky_desc TEXT,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            run_id UUID NOT NULL REFERENCES raw_ingestion_run(run_id),
            PRIMARY KEY (source_provider, state_id, municipality_id, issued_at, valid_date),
            FOREIGN KEY (state_id, municipality_id)
                REFERENCES dim_municipality(state_id, municipality_id) ON DELETE CASCADE,
            CONSTRAINT chk_fcst_tmax_ge_tmin CHECK (tmax_c >= tmin_c),
            CONSTRAINT chk_fcst_no_leakage CHECK (valid_date >= (issued_at AT TIME ZONE 'UTC')::date)
        );
        CREATE INDEX ix_fcst_mun_valid_issued
            ON fact_forecast_revision (state_id, municipality_id, valid_date DESC, issued_at DESC);

        CREATE TABLE fact_observed_daily (
            source_provider TEXT NOT NULL,
            state_id SMALLINT NOT NULL,
            municipality_id SMALLINT NOT NULL,
            obs_date DATE NOT NULL,
            tmax_c NUMERIC(5, 2) NOT NULL,
            tmin_c NUMERIC(5, 2) NOT NULL,
            precip_mm NUMERIC(7, 2) CHECK (precip_mm IS NULL OR precip_mm >= 0),
            wind_speed_kmh NUMERIC(6, 2) CHECK (wind_speed_kmh IS NULL OR wind_speed_kmh >= 0),
            wind_gust_kmh NUMERIC(6, 2) CHECK (wind_gust_kmh IS NULL OR wind_gust_kmh >= 0),
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            run_id UUID NOT NULL REFERENCES raw_ingestion_run(run_id),
            PRIMARY KEY (source_provider, state_id, municipality_id, obs_date),
            FOREIGN KEY (state_id, municipality_id)
                REFERENCES dim_municipality(state_id, municipality_id) ON DELETE CASCADE,
            CONSTRAINT chk_obs_tmax_ge_tmin CHECK (tmax_c >= tmin_c)
        );
        CREATE INDEX ix_obs_mun_date
            ON fact_observed_daily (state_id, municipality_id, obs_date DESC);

        CREATE TABLE fact_ml_forecast (
            model_version TEXT NOT NULL,
            state_id SMALLINT NOT NULL,
            municipality_id SMALLINT NOT NULL,
            issued_at TIMESTAMPTZ NOT NULL,
            valid_date DATE NOT NULL,
            lead_day SMALLINT NOT NULL,
            raw_tmax_c NUMERIC(5, 2) NOT NULL,
            pred_tmax_p10 NUMERIC(5, 2) NOT NULL,
            pred_tmax_p50 NUMERIC(5, 2) NOT NULL,
            pred_tmax_p90 NUMERIC(5, 2) NOT NULL,
            raw_tmin_c NUMERIC(5, 2) NOT NULL,
            pred_tmin_p10 NUMERIC(5, 2) NOT NULL,
            pred_tmin_p50 NUMERIC(5, 2) NOT NULL,
            pred_tmin_p90 NUMERIC(5, 2) NOT NULL,
            raw_precip_prob_pct SMALLINT,
            calibrated_rain_prob NUMERIC(5, 4) NOT NULL CHECK (calibrated_rain_prob >= 0.0 AND calibrated_rain_prob <= 1.0),
            alert_level TEXT NOT NULL CHECK (alert_level IN ('NORMAL', 'ADVISORY', 'WARNING_P0')),
            scored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (model_version, state_id, municipality_id, issued_at, valid_date),
            FOREIGN KEY (state_id, municipality_id)
                REFERENCES dim_municipality(state_id, municipality_id) ON DELETE CASCADE,
            CONSTRAINT chk_ml_tmax_quantiles CHECK (pred_tmax_p10 <= pred_tmax_p50 AND pred_tmax_p50 <= pred_tmax_p90),
            CONSTRAINT chk_ml_tmin_quantiles CHECK (pred_tmin_p10 <= pred_tmin_p50 AND pred_tmin_p50 <= pred_tmin_p90)
        );
        CREATE INDEX ix_ml_fcst_mun_valid
            ON fact_ml_forecast (state_id, municipality_id, valid_date DESC);

        CREATE TABLE ml_model_evaluation (
            id SERIAL PRIMARY KEY,
            model_version TEXT NOT NULL,
            split_name TEXT NOT NULL,
            segment_key TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            raw_baseline_val DOUBLE PRECISION,
            ml_model_val DOUBLE PRECISION NOT NULL,
            persistence_val DOUBLE PRECISION,
            climatology_val DOUBLE PRECISION,
            sample_count INT NOT NULL,
            evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX ix_ml_eval_version_split
            ON ml_model_evaluation (model_version, split_name, segment_key);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS ml_model_evaluation;
        DROP TABLE IF EXISTS fact_ml_forecast;
        DROP TABLE IF EXISTS fact_observed_daily;
        DROP TABLE IF EXISTS fact_forecast_revision;
        DROP TABLE IF EXISTS raw_ingestion_run;
        DROP TABLE IF EXISTS dim_municipality;
        DROP TABLE IF EXISTS dim_state;
        """
    )
