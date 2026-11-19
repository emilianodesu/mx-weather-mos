"""Deterministic local model registry with SHA-256 manifest verification."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib

from mxweather.config import settings


class ModelIntegrityError(ValueError):
    """Raised when a model bundle fails SHA-256 manifest verification."""


BUNDLE_FILES: tuple[str, ...] = (
    "calibration_curve.json",
    "feature_schema.json",
    "metrics.json",
    "model_bundle.joblib",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _models_root(artifacts_dir: Path | None = None) -> Path:
    base = artifacts_dir if artifacts_dir is not None else settings.mxweather_artifacts_dir
    return Path(base) / "models"


def save_model_bundle(
    *,
    model_version: str,
    model_bundle: Any,
    feature_schema: dict[str, Any],
    metrics: dict[str, Any],
    calibration_curve: dict[str, Any],
    artifacts_dir: Path | None = None,
    update_latest: bool = True,
) -> Path:
    """Persist a model bundle and deterministic SHA-256 manifest."""
    root = _models_root(artifacts_dir)
    bundle_dir = root / model_version
    bundle_dir.mkdir(parents=True, exist_ok=True)

    joblib.dump(model_bundle, bundle_dir / "model_bundle.joblib", compress=3)

    (bundle_dir / "feature_schema.json").write_text(
        json.dumps(feature_schema, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "calibration_curve.json").write_text(
        json.dumps(calibration_curve, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    manifest_lines: list[str] = []
    for filename in sorted(BUNDLE_FILES):
        file_path = bundle_dir / filename
        digest = _sha256_file(file_path)
        manifest_lines.append(f"{digest}  {filename}")
    (bundle_dir / "manifest.sha256").write_text(
        "\n".join(manifest_lines) + "\n",
        encoding="utf-8",
    )

    if update_latest:
        latest_payload = {
            "model_version": model_version,
            "saved_at": datetime.now(UTC).isoformat(),
            "bundle_dir": str(bundle_dir),
            "manifest_sha256": _sha256_file(bundle_dir / "manifest.sha256"),
        }
        (root / "latest.json").write_text(
            json.dumps(latest_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return bundle_dir


def verify_manifest(bundle_dir: Path) -> dict[str, str]:
    """Verify SHA-256 digests of all files listed in `manifest.sha256`."""
    manifest_path = bundle_dir / "manifest.sha256"
    if not manifest_path.exists():
        raise ModelIntegrityError(f"Missing manifest.sha256 in {bundle_dir}")

    expected: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) != 2:
            raise ModelIntegrityError(f"Malformed manifest line in {manifest_path}: {stripped!r}")
        digest, filename = parts
        expected[filename] = digest

    for required in BUNDLE_FILES:
        if required not in expected:
            raise ModelIntegrityError(f"Manifest {manifest_path} missing required entry: {required}")

    for filename, exp_digest in expected.items():
        target = bundle_dir / filename
        if not target.exists():
            raise ModelIntegrityError(f"Missing bundle file referenced in manifest: {target}")
        actual = _sha256_file(target)
        if actual != exp_digest:
            raise ModelIntegrityError(
                f"SHA-256 mismatch for {target}: expected {exp_digest}, got {actual}"
            )

    return expected


def load_model_bundle(
    model_version: str,
    *,
    artifacts_dir: Path | None = None,
) -> dict[str, Any]:
    """Verify SHA-256 manifest and load a versioned model bundle."""
    bundle_dir = _models_root(artifacts_dir) / model_version
    if not bundle_dir.exists():
        raise FileNotFoundError(f"Model bundle directory does not exist: {bundle_dir}")

    digests = verify_manifest(bundle_dir)
    model_bundle = joblib.load(bundle_dir / "model_bundle.joblib")
    feature_schema = json.loads((bundle_dir / "feature_schema.json").read_text(encoding="utf-8"))
    metrics = json.loads((bundle_dir / "metrics.json").read_text(encoding="utf-8"))
    calibration_curve = json.loads(
        (bundle_dir / "calibration_curve.json").read_text(encoding="utf-8")
    )

    return {
        "model_version": model_version,
        "bundle_dir": bundle_dir,
        "model_bundle": model_bundle,
        "feature_schema": feature_schema,
        "metrics": metrics,
        "calibration_curve": calibration_curve,
        "manifest": digests,
    }


def load_latest_model_bundle(*, artifacts_dir: Path | None = None) -> dict[str, Any]:
    """Load the latest model bundle pointed to by `latest.json` with SHA-256 verification."""
    latest_path = _models_root(artifacts_dir) / "latest.json"
    if not latest_path.exists():
        raise FileNotFoundError(f"Latest model pointer not found: {latest_path}")

    pointer = json.loads(latest_path.read_text(encoding="utf-8"))
    model_version = pointer["model_version"]
    return load_model_bundle(model_version, artifacts_dir=artifacts_dir)
