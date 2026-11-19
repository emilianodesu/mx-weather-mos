"""Idempotently seed dim_state and dim_municipality from fixtures/mx_municipalities.json."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text

from mxweather.config import settings
from mxweather.db.session import engine


def seed_catalog(catalog_path: Path | None = None) -> tuple[int, int]:
    path = catalog_path or (settings.mxweather_fixtures_dir / "mx_municipalities.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    states = payload["states"]
    municipalities = payload["municipalities"]

    with engine.begin() as conn:
        for st in states:
            conn.execute(
                text(
                    """
                    INSERT INTO dim_state (state_id, state_name)
                    VALUES (:state_id, :state_name)
                    ON CONFLICT (state_id) DO UPDATE SET state_name = EXCLUDED.state_name
                    """
                ),
                st,
            )

        for mun in municipalities:
            conn.execute(
                text(
                    """
                    INSERT INTO dim_municipality (
                        state_id, municipality_id, municipality_name,
                        lat, lon, utc_offset_hours, is_ml_cohort, geom
                    )
                    VALUES (
                        :state_id, :municipality_id, :municipality_name,
                        :lat, :lon, :utc_offset_hours, :is_ml_cohort,
                        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
                    )
                    ON CONFLICT (state_id, municipality_id) DO UPDATE SET
                        municipality_name = EXCLUDED.municipality_name,
                        lat = EXCLUDED.lat,
                        lon = EXCLUDED.lon,
                        utc_offset_hours = EXCLUDED.utc_offset_hours,
                        is_ml_cohort = EXCLUDED.is_ml_cohort,
                        geom = EXCLUDED.geom
                    """
                ),
                mun,
            )

    return len(states), len(municipalities)


if __name__ == "__main__":
    n_states, n_muns = seed_catalog()
    print(f"Seeded {n_states} states and {n_muns} municipalities.")
