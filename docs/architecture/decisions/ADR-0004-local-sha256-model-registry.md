# ADR-0004: Local SHA-256 Model Registry vs. Heavy MLOps Servers

- **Status:** Accepted (2026-11-18)
- **Context:** Running a separate MLflow/MinIO/artifact server adds container memory overhead without improving reproducibility on a single-machine local Docker stack.
- **Decision:** Store immutable versioned bundles under `artifacts/models/<model_version>/` containing `model_bundle.joblib`, `feature_schema.json`, `metrics.json`, `calibration_curve.json`, and `manifest.sha256`, referenced by `latest.json`. `registry.load_model_bundle()` verifies SHA-256 checksums before deserializing.
- **Consequences:** Lightweight, deterministic, tamper-evident model loading in FastAPI and scoring scripts with zero extra infrastructure.
