.PHONY: up down migrate seed backfill ingest train score test-unit test-api test-int web-build ci

up:
	docker compose up -d --build

down:
	docker compose down

migrate:
	docker compose run --rm pipeline alembic upgrade head

seed:
	docker compose run --rm pipeline python scripts/seed_catalog.py

backfill:
	docker compose run --rm pipeline python scripts/run_backfill.py

ingest:
	docker compose run --rm pipeline python scripts/run_daily_ingest.py

train:
	docker compose run --rm pipeline python scripts/train_models.py

score:
	docker compose run --rm pipeline python scripts/score_forecasts.py

test-unit:
	docker compose run --rm pipeline pytest tests/unit -q

test-api:
	docker compose run --rm pipeline pytest tests/api -q

test-int:
	docker compose run --rm pipeline pytest tests/integration -q

web-build:
	cd web && npm ci && npm run build && npm test

ci: test-unit test-api web-build
