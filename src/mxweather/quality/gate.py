"""Deterministic data contract validation gate for rules S01-S08 and Q01-Q08."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from mxweather.config import settings

CONTRACT_VERSION = "1.0.0"

ALLOWED_SOURCE_PROVIDERS: frozenset[str] = frozenset(
    {
        "open_meteo_forecast",
        "open_meteo_historical_forecast",
        "open_meteo_era5",
        "conagua_smn",
    }
)

FORECAST_REQUIRED_FIELDS: tuple[str, ...] = (
    "source_provider",
    "state_id",
    "municipality_id",
    "issued_at",
    "valid_date",
    "lead_day",
    "tmax_c",
    "tmin_c",
)

OBSERVED_REQUIRED_FIELDS: tuple[str, ...] = (
    "source_provider",
    "state_id",
    "municipality_id",
    "obs_date",
    "tmax_c",
    "tmin_c",
)

NUMERIC_FIELDS: tuple[str, ...] = (
    "tmax_c",
    "tmin_c",
    "precip_mm",
    "precip_prob_pct",
    "wind_speed_kmh",
    "wind_gust_kmh",
    "cloud_cover_pct",
    "lat",
    "lon",
    "elevation_m",
)

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PayloadIntegrityError(ValueError):
    """Raised when a raw payload fails rule S01 (JSON/GZip integrity)."""

    def __init__(self, message: str, rule_id: str = "S01") -> None:
        super().__init__(message)
        self.rule_id = rule_id


def load_data_contracts(path: Path | None = None) -> dict[str, Any]:
    """Load the canonical machine-readable data contracts JSON."""
    contracts_path = path or settings.mxweather_contracts_path
    with Path(contracts_path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def validate_raw_payload(
    raw_bytes: bytes, *, expect_gzip: bool = False
) -> tuple[Any, str]:
    """Validate rule S01: UTF-8 JSON (or GZip-compressed UTF-8 JSON) + SHA-256 digest.

    Returns ``(parsed_json, sha256_hex)`` or raises :class:`PayloadIntegrityError`.
    """
    if not isinstance(raw_bytes, (bytes, bytearray)) or len(raw_bytes) == 0:
        raise PayloadIntegrityError("Empty or non-bytes raw payload")

    payload_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    data_bytes = bytes(raw_bytes)

    is_gzip = len(data_bytes) >= 2 and data_bytes[:2] == b"\x1f\x8b"
    if expect_gzip and not is_gzip:
        raise PayloadIntegrityError("Expected GZip-compressed payload (missing magic header)")

    if is_gzip:
        try:
            data_bytes = gzip.decompress(data_bytes)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise PayloadIntegrityError(f"Corrupt GZip payload: {exc}") from exc

    try:
        text = data_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PayloadIntegrityError(f"Payload is not valid UTF-8: {exc}") from exc

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PayloadIntegrityError(f"Payload is not valid JSON: {exc}") from exc

    return parsed, payload_sha256


def _is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_strict_decimal(value: Any) -> Decimal | None:
    """Parse a finite numeric value strictly without coercing invalid values to 0.

    Returns ``Decimal`` on success, or ``None`` if invalid (NaN, Inf, bool, unparseable string).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(str(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            dec = Decimal(stripped)
        except InvalidOperation:
            return None
        if not dec.is_finite():
            return None
        return dec
    return None


def _parse_strict_date(value: Any) -> date | None:
    """Parse a strict ISO-8601 date (rejecting datetime instances)."""
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _ISO_DATE_RE.match(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _parse_strict_utc_datetime(value: Any) -> datetime | None:
    """Parse a timezone-aware UTC datetime (naive datetimes fail S05)."""
    dt: datetime | None = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None

    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    if dt.utcoffset() != timedelta(0):
        return None
    return dt.astimezone(UTC)


def check_cohort_completeness(
    observed_municipalities: set[tuple[int, int]],
    forecast_municipalities: set[tuple[int, int]],
    cohort_municipalities: set[tuple[int, int]],
    *,
    min_coverage: float = 0.95,
) -> dict[str, Any]:
    """Evaluate rule Q08: at least 95% of the ML cohort has paired observed + forecast coverage."""
    total_cohort = len(cohort_municipalities)
    if total_cohort == 0:
        return {
            "rule_id": "Q08",
            "passed": False,
            "cohort_size": 0,
            "paired_count": 0,
            "coverage_ratio": 0.0,
            "min_coverage": min_coverage,
            "missing_municipalities": [],
        }

    paired = cohort_municipalities & observed_municipalities & forecast_municipalities
    missing = sorted(cohort_municipalities - paired)
    coverage_ratio = len(paired) / total_cohort
    return {
        "rule_id": "Q08",
        "passed": coverage_ratio >= min_coverage,
        "cohort_size": total_cohort,
        "paired_count": len(paired),
        "coverage_ratio": round(coverage_ratio, 6),
        "min_coverage": min_coverage,
        "missing_municipalities": missing,
    }


def validate_batch(
    rows: list[dict[str, Any]],
    *,
    record_type: str = "forecast",
    valid_municipalities: set[tuple[int, int]] | None = None,
    payload_sha256: str | None = None,
    expected_cohort_municipalities: set[tuple[int, int]] | None = None,
    cohort_min_coverage: float = 0.95,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Deterministically validate candidate rows against rules S01-S08 and Q01-Q08.

    Invalid values are never silently coerced; failing rows are quarantined with rule IDs.

    Returns ``(valid_rows, quarantined_rows, summary_dict)``.
    """
    if record_type not in {"forecast", "observed"}:
        raise ValueError(f"Unsupported record_type: {record_type!r}")

    required_fields = (
        FORECAST_REQUIRED_FIELDS if record_type == "forecast" else OBSERVED_REQUIRED_FIELDS
    )
    date_field = "valid_date" if record_type == "forecast" else "obs_date"

    valid_rows: list[dict[str, Any]] = []
    quarantined_rows: list[dict[str, Any]] = []
    rule_failure_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    warnings: list[dict[str, Any]] = []
    seen_pks: set[tuple[Any, ...]] = set()
    valid_mun_keys: set[tuple[int, int]] = set()

    # S01 batch-level check when payload_sha256 is explicitly provided
    batch_s01_failed = False
    if payload_sha256 is not None and not (
        isinstance(payload_sha256, str) and _SHA256_RE.match(payload_sha256)
    ):
        batch_s01_failed = True

    for idx, row in enumerate(rows):
        reasons: set[str] = set()
        details: list[str] = []

        if batch_s01_failed:
            reasons.add("S01")
            details.append("S01: invalid or missing payload_sha256 digest")

        if not isinstance(row, dict):
            reasons.add("S01")
            details.append("S01: row is not a JSON object/dict")
            quarantined_rows.append(
                {"index": idx, "row": row, "reasons": sorted(reasons), "details": details}
            )
            for r in reasons:
                rule_failure_counts[r] += 1
            continue

        # S02: Required fields present and non-null
        missing_fields = [f for f in required_fields if f not in row or row[f] is None]
        if missing_fields:
            reasons.add("S02")
            details.append(f"S02: missing required fields {missing_fields}")

        # S08: source_provider enum
        source_provider = row.get("source_provider")
        if source_provider is not None and source_provider not in ALLOWED_SOURCE_PROVIDERS:
            reasons.add("S08")
            details.append(f"S08: invalid source_provider {source_provider!r}")

        # Identifiers & S03: municipality FK existence
        state_id = row.get("state_id")
        municipality_id = row.get("municipality_id")
        mun_key: tuple[int, int] | None = None
        if state_id is not None and municipality_id is not None:
            if not (_is_strict_int(state_id) and _is_strict_int(municipality_id)):
                reasons.add("S07")
                details.append("S07: state_id and municipality_id must be integers")
            else:
                mun_key = (int(state_id), int(municipality_id))
                if valid_municipalities is not None and mun_key not in valid_municipalities:
                    reasons.add("S03")
                    details.append(f"S03: municipality {mun_key} not found in dim_municipality")

        # S07: Numeric type strictness across all numeric fields
        parsed_nums: dict[str, Decimal] = {}
        for field in NUMERIC_FIELDS:
            if field in row and row[field] is not None:
                parsed = _parse_strict_decimal(row[field])
                if parsed is None:
                    reasons.add("S07")
                    details.append(f"S07: invalid numeric value for {field}: {row[field]!r}")
                else:
                    parsed_nums[field] = parsed

        # S04: Mexico coordinate bounds (14.0 <= lat <= 33.0, -119.0 <= lon <= -86.0)
        if "lat" in parsed_nums:
            lat_val = parsed_nums["lat"]
            if lat_val < Decimal("14.0") or lat_val > Decimal("33.0"):
                reasons.add("S04")
                details.append(f"S04: lat {lat_val} out of Mexico bounds [14.0, 33.0]")
        if "lon" in parsed_nums:
            lon_val = parsed_nums["lon"]
            if lon_val < Decimal("-119.0") or lon_val > Decimal("-86.0"):
                reasons.add("S04")
                details.append(f"S04: lon {lon_val} out of Mexico bounds [-119.0, -86.0]")

        # S05: Timestamp and date validity
        parsed_date: date | None = None
        if date_field in row and row[date_field] is not None:
            parsed_date = _parse_strict_date(row[date_field])
            if parsed_date is None:
                reasons.add("S05")
                details.append(f"S05: invalid ISO-8601 date for {date_field}: {row[date_field]!r}")

        parsed_issued_at: datetime | None = None
        if record_type == "forecast" and "issued_at" in row and row["issued_at"] is not None:
            parsed_issued_at = _parse_strict_utc_datetime(row["issued_at"])
            if parsed_issued_at is None:
                reasons.add("S05")
                details.append(
                    f"S05: issued_at must be timezone-aware UTC datetime: {row['issued_at']!r}"
                )

        # Q07: Zero temporal leakage (issued_at::date <= valid_date)
        if record_type == "forecast" and parsed_issued_at is not None and parsed_date is not None:
            issued_utc_date = parsed_issued_at.date()
            if parsed_date < issued_utc_date:
                reasons.add("Q07")
                details.append(
                    f"Q07: temporal leakage valid_date ({parsed_date}) < issued_at::date ({issued_utc_date})"
                )

        # S06: lead_day domain (0..7 and consistent with valid_date - issued_at::date)
        if record_type == "forecast" and "lead_day" in row and row["lead_day"] is not None:
            lead_day = row["lead_day"]
            if not _is_strict_int(lead_day) or lead_day < 0 or lead_day > 7:
                reasons.add("S06")
                details.append(f"S06: lead_day must be integer in [0, 7], got {lead_day!r}")
            elif parsed_issued_at is not None and parsed_date is not None:
                expected_lead = (parsed_date - parsed_issued_at.date()).days
                if lead_day != expected_lead:
                    reasons.add("S06")
                    details.append(
                        f"S06: lead_day ({lead_day}) != valid_date - issued_at::date ({expected_lead})"
                    )

        # Q01: Temperature ordering (tmax_c >= tmin_c)
        tmax = parsed_nums.get("tmax_c")
        tmin = parsed_nums.get("tmin_c")
        if tmax is not None and tmin is not None and tmax < tmin:
            reasons.add("Q01")
            details.append(f"Q01: tmax_c ({tmax}) < tmin_c ({tmin})")

        # Q02: Physical temperature bounds [-25.0, 55.0]
        if tmax is not None and (tmax < Decimal("-25.0") or tmax > Decimal("55.0")):
            reasons.add("Q02")
            details.append(f"Q02: tmax_c ({tmax}) out of physical bounds [-25.0, 55.0]")
        if tmin is not None and (tmin < Decimal("-25.0") or tmin > Decimal("55.0")):
            reasons.add("Q02")
            details.append(f"Q02: tmin_c ({tmin}) out of physical bounds [-25.0, 55.0]")

        # Q03: Precipitation non-negative and <= 600.0 mm/day
        precip = parsed_nums.get("precip_mm")
        if precip is not None and (precip < Decimal("0") or precip > Decimal("600.0")):
            reasons.add("Q03")
            details.append(f"Q03: precip_mm ({precip}) out of bounds [0.0, 600.0]")

        # Q04: Precipitation probability range [0, 100]
        precip_prob = parsed_nums.get("precip_prob_pct")
        if precip_prob is not None:
            if precip_prob < Decimal("0") or precip_prob > Decimal("100"):
                reasons.add("Q04")
                details.append(f"Q04: precip_prob_pct ({precip_prob}) out of bounds [0, 100]")

        # Q05: Wind non-negative; flag warning if wind_gust_kmh < wind_speed_kmh
        wind_speed = parsed_nums.get("wind_speed_kmh")
        wind_gust = parsed_nums.get("wind_gust_kmh")
        if wind_speed is not None and wind_speed < Decimal("0"):
            reasons.add("Q05")
            details.append(f"Q05: wind_speed_kmh ({wind_speed}) < 0")
        if wind_gust is not None and wind_gust < Decimal("0"):
            reasons.add("Q05")
            details.append(f"Q05: wind_gust_kmh ({wind_gust}) < 0")
        if (
            wind_speed is not None
            and wind_gust is not None
            and wind_speed >= Decimal("0")
            and wind_gust >= Decimal("0")
            and wind_gust < wind_speed
        ):
            warning_counts["Q05"] += 1
            warnings.append(
                {
                    "rule_id": "Q05",
                    "index": idx,
                    "detail": f"wind_gust_kmh ({wind_gust}) < wind_speed_kmh ({wind_speed})",
                }
            )

        # Q06: Idempotent replay / duplicate primary key check within batch
        pk: tuple[Any, ...] | None = None
        if not reasons and mun_key is not None and parsed_date is not None:
            if record_type == "forecast" and parsed_issued_at is not None:
                pk = (source_provider, mun_key[0], mun_key[1], parsed_issued_at, parsed_date)
            elif record_type == "observed":
                pk = (source_provider, mun_key[0], mun_key[1], parsed_date)

        if pk is not None:
            if pk in seen_pks:
                reasons.add("Q06")
                details.append(f"Q06: duplicate primary key in batch {pk}")
            else:
                seen_pks.add(pk)

        if reasons:
            for r in reasons:
                rule_failure_counts[r] += 1
            quarantined_rows.append(
                {
                    "index": idx,
                    "row": row,
                    "reasons": sorted(reasons),
                    "details": details,
                }
            )
        else:
            valid_rows.append(row)
            if mun_key is not None:
                valid_mun_keys.add(mun_key)

    # Q08: Optional batch-level cohort completeness check
    cohort_coverage_pct: float | None = None
    q08_passed: bool | None = None
    if expected_cohort_municipalities is not None:
        total_expected = len(expected_cohort_municipalities)
        covered = len(expected_cohort_municipalities & valid_mun_keys)
        ratio = (covered / total_expected) if total_expected > 0 else 0.0
        cohort_coverage_pct = round(ratio * 100.0, 2)
        q08_passed = ratio >= cohort_min_coverage
        if not q08_passed:
            warning_counts["Q08"] += 1
            warnings.append(
                {
                    "rule_id": "Q08",
                    "index": None,
                    "detail": (
                        f"cohort completeness {cohort_coverage_pct:.2f}% "
                        f"({covered}/{total_expected}) < {cohort_min_coverage * 100:.1f}%"
                    ),
                }
            )

    summary_dict: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "record_type": record_type,
        "payload_sha256": payload_sha256,
        "total_rows": len(rows),
        "valid_count": len(valid_rows),
        "quarantined_count": len(quarantined_rows),
        "rule_failure_counts": dict(sorted(rule_failure_counts.items())),
        "warning_counts": dict(sorted(warning_counts.items())),
        "warnings": warnings,
        "cohort_coverage_pct": cohort_coverage_pct,
        "q08_passed": q08_passed,
    }
    return valid_rows, quarantined_rows, summary_dict
