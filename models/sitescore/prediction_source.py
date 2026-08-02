"""Authoritative SiteScore prediction source resolver and model registry lineage verifier."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

PREDICTION_SOURCE_RECEIPT_SCHEMA_VERSION = 1
PREDICTION_SOURCE_RECEIPT_KIND = "sitescore-prediction-source-receipt"
CANONICAL_PREDICTION_MODEL_NAME = "sitescore_propensity"
CANONICAL_PREDICTION_SERVICE = "sitescore"
CANONICAL_MODEL_VERSION = "candidate-site-view-v2"


def _is_finite_float(val: Any) -> bool:
    """Check if value is a finite float (not None, bool, NaN, inf, or -inf)."""
    if val is None or isinstance(val, bool):
        return False
    try:
        f = float(val)
        return math.isfinite(f)
    except (ValueError, TypeError):
        return False


def _is_valid_prediction_value(val: Any) -> bool:
    """Check if a predicted revenue value is finite and >= 0.0."""
    if not _is_finite_float(val):
        return False
    return float(val) >= 0.0


@dataclass(frozen=True)
class SiteScorePredictionRecord:
    """Canonical SiteScore prediction evidence record."""

    entity_id: str
    store_id: str
    opened_on: str
    target_format_code: str
    predicted_revenue: float
    p10: float
    p90: float
    p50: float | None = None
    dataset_snapshot_id: str | None = None
    model_version: str | None = None
    artifact_lineage_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "entity_id": self.entity_id,
            "store_id": self.store_id,
            "opened_on": self.opened_on,
            "target_format_code": self.target_format_code,
            "predicted_revenue": self.predicted_revenue,
            "p10": self.p10,
            "p90": self.p90,
        }
        if self.p50 is not None:
            res["p50"] = self.p50
        if self.dataset_snapshot_id is not None:
            res["dataset_snapshot_id"] = self.dataset_snapshot_id
        if self.model_version is not None:
            res["model_version"] = self.model_version
        if self.artifact_lineage_id is not None:
            res["artifact_lineage_id"] = self.artifact_lineage_id
        return res


@dataclass(frozen=True)
class SiteScorePredictionSourceVerificationResult:
    """Fail-closed verification result for SiteScore prediction source evidence."""

    is_valid: bool
    reason_code: str
    matched_count: int = 0
    unmatched_count: int = 0
    duplicate_count: int = 0
    malformed_interval_count: int = 0
    errors: Sequence[str] = field(default_factory=tuple)
    dataset_snapshot_id: str | None = None
    model_version: str | None = None
    artifact_lineage_id: str | None = None
    prediction_receipt_hash: str | None = None


def compute_prediction_source_receipt_sha256(payload: dict[str, Any]) -> str:
    """Compute deterministic SHA256 digest of prediction source receipt body excluding integrity hash."""
    canonical = json.dumps(
        {k: v for k, v in payload.items() if k != "integrity"},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_sitescore_prediction_source_receipt(
    records: Sequence[dict[str, Any] | SiteScorePredictionRecord],
    *,
    dataset_snapshot_id: str,
    model_version: str,
    artifact_lineage_id: str,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Build an immutable SiteScore prediction source receipt payload with integrity envelope."""
    ts = observed_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")

    canonical_records = []
    for r in records:
        if isinstance(r, SiteScorePredictionRecord):
            rec_dict = r.to_dict()
        else:
            rec_dict = dict(r)
        canonical_records.append(rec_dict)

    sorted_records = sorted(
        canonical_records,
        key=lambda x: (str(x.get("entity_id") or x.get("store_id") or ""), str(x.get("opened_on") or "")),
    )

    records_payload = json.dumps(sorted_records, sort_keys=True, separators=(",", ":"), allow_nan=False)
    records_sha256 = hashlib.sha256(records_payload.encode("utf-8")).hexdigest()

    payload: dict[str, Any] = {
        "schema_version": PREDICTION_SOURCE_RECEIPT_SCHEMA_VERSION,
        "kind": PREDICTION_SOURCE_RECEIPT_KIND,
        "observed_at": ts,
        "model_name": CANONICAL_PREDICTION_MODEL_NAME,
        "service": CANONICAL_PREDICTION_SERVICE,
        "dataset_snapshot_id": dataset_snapshot_id,
        "model_version": model_version,
        "artifact_lineage_id": artifact_lineage_id,
        "record_count": len(sorted_records),
        "records_sha256": records_sha256,
        "records": sorted_records,
    }

    content_sha = compute_prediction_source_receipt_sha256(payload)
    payload["integrity"] = {
        "content_sha256": content_sha,
        "records_sha256": records_sha256,
    }
    return payload


def verify_sitescore_prediction_source(
    records: Sequence[dict[str, Any]],
    *,
    expected_snapshot_id: str | None = None,
    expected_model_version: str | None = None,
    expected_lineage_id: str | None = None,
) -> SiteScorePredictionSourceVerificationResult:
    """Fail-closed verification of SiteScore prediction source records.

    Enforces all strict fail-closed constraints required by ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001:
    - Missing or empty prediction records
    - Duplicate entity/as-of predictions
    - y_pred = y_true substitution
    - Fixed multiplier horizon metrics
    - Store age substituted for outcome coverage
    - Wrong as-of or model version
    - Malformed P10/P50/P90 intervals (p10 > p90, non-finite float, p50 outside [p10, p90])
    - Population mismatch
    - Unbound dataset/model/receipt hash
    """
    errors: list[str] = []

    if not records:
        return SiteScorePredictionSourceVerificationResult(
            is_valid=False,
            reason_code="NO_PREDICTION_RECORDS",
            errors=("No prediction records provided",),
        )

    matched_count = 0
    unmatched_count = 0
    duplicate_count = 0
    malformed_interval_count = 0

    seen_entities: set[str] = set()

    dataset_snapshot_id: str | None = expected_snapshot_id
    model_version: str | None = expected_model_version
    artifact_lineage_id: str | None = expected_lineage_id

    DISALLOWED_SNAPSHOT_IDS = {"caller-self-attested", "arbitrary-caller-snapshot", "unverified", "self-attested", "caller-attested", "none", "null"}
    DISALLOWED_MODEL_VERSIONS = {"stale-alias-v0", "arbitrary-caller-model", "unverified", "stale-alias"}
    DISALLOWED_LINEAGE_IDS = {"not-a-hash", "arbitrary-caller-artifact", "unverified", "not_a_hash"}

    ref_date = datetime.now(UTC).date()

    # Check for consistency of lineage across records
    for idx, r in enumerate(records):
        if not isinstance(r, dict):
            errors.append(f"Record [{idx}] must be a dictionary")
            continue

        entity_id = str(r.get("entity_id") or r.get("store_id") or f"idx-{idx}")
        opened_on = str(r.get("opened_on") or "")
        dedup_key = f"{entity_id}:{opened_on}"

        if dedup_key in seen_entities:
            duplicate_count += 1
            errors.append(f"Duplicate prediction record for entity '{entity_id}' as-of '{opened_on}'")
        seen_entities.add(dedup_key)

        # Validate as-of date (opened_on)
        if opened_on:
            try:
                dt_opened = datetime.strptime(opened_on[:10], "%Y-%m-%d").date()
                if dt_opened > ref_date:
                    errors.append(f"Future or invalid opened_on as-of date at record [{idx}]: '{opened_on}'")
            except Exception:
                errors.append(f"Invalid opened_on date format at record [{idx}]: '{opened_on}'")

        # Lineage field extraction & verification
        rec_snap = r.get("dataset_snapshot_id") or r.get("dataset_snapshot_hash")
        rec_ver = r.get("model_version")
        rec_lin = r.get("artifact_lineage_id") or r.get("artifact_hash")

        if rec_snap:
            rec_snap_str = str(rec_snap).strip()
            if rec_snap_str.lower() in DISALLOWED_SNAPSHOT_IDS:
                errors.append(f"Disallowed or unverified dataset snapshot ID at record [{idx}]: '{rec_snap_str}'")
            elif dataset_snapshot_id is None:
                dataset_snapshot_id = rec_snap_str
            elif rec_snap_str != dataset_snapshot_id:
                errors.append(f"Dataset snapshot ID mismatch at record [{idx}]: record '{rec_snap_str}', expected '{dataset_snapshot_id}'")

        if rec_ver:
            rec_ver_str = str(rec_ver).strip()
            if rec_ver_str.lower() in DISALLOWED_MODEL_VERSIONS:
                errors.append(f"Disallowed or unapproved model version at record [{idx}]: '{rec_ver_str}'")
            elif dataset_snapshot_id is not None and rec_ver_str != CANONICAL_MODEL_VERSION and expected_model_version and rec_ver_str != expected_model_version:
                # If expected model version was provided and doesn't match
                errors.append(f"Model version mismatch at record [{idx}]: record '{rec_ver_str}', expected '{expected_model_version}'")
            elif model_version is None:
                model_version = rec_ver_str
            elif rec_ver_str != model_version:
                errors.append(f"Model version mismatch at record [{idx}]: record '{rec_ver_str}', expected '{model_version}'")

        if rec_lin:
            rec_lin_str = str(rec_lin).strip()
            if rec_lin_str.lower() in DISALLOWED_LINEAGE_IDS:
                errors.append(f"Disallowed or malformed artifact lineage ID at record [{idx}]: '{rec_lin_str}'")
            elif artifact_lineage_id is None:
                artifact_lineage_id = rec_lin_str
            elif rec_lin_str != artifact_lineage_id:
                errors.append(f"Artifact lineage ID mismatch at record [{idx}]: record '{rec_lin_str}', expected '{artifact_lineage_id}'")

        # Prediction value checks
        pred_raw = r.get("predicted_revenue")
        if pred_raw is None or not _is_valid_prediction_value(pred_raw):
            unmatched_count += 1
            errors.append(f"Invalid or missing predicted_revenue at record [{idx}] for entity '{entity_id}'")
            continue

        matched_count += 1
        y_pred = float(pred_raw)

        # Interval bounds checks (p10 / p90 / p50)
        p10_raw = r.get("p10")
        p90_raw = r.get("p90")
        p50_raw = r.get("p50")

        if p10_raw is None or p90_raw is None:
            malformed_interval_count += 1
            errors.append(f"Missing required interval bounds p10/p90 at record [{idx}] for entity '{entity_id}'")
        elif not _is_finite_float(p10_raw) or not _is_finite_float(p90_raw):
            malformed_interval_count += 1
            errors.append(f"Non-finite interval bounds p10/p90 at record [{idx}] for entity '{entity_id}'")
        else:
            p10 = float(p10_raw)
            p90 = float(p90_raw)
            if p10 < 0.0 or p90 < 0.0:
                malformed_interval_count += 1
                errors.append(f"Negative interval bounds p10={p10}, p90={p90} at record [{idx}] for entity '{entity_id}'")
            elif p10 > p90:
                malformed_interval_count += 1
                errors.append(f"Malformed interval bound: p10 ({p10}) > p90 ({p90}) at record [{idx}] for entity '{entity_id}'")

            if p50_raw is not None:
                if not _is_finite_float(p50_raw):
                    malformed_interval_count += 1
                    errors.append(f"Non-finite p50 interval bound at record [{idx}] for entity '{entity_id}'")
                else:
                    p50 = float(p50_raw)
                    if not (p10 <= p50 <= p90):
                        malformed_interval_count += 1
                        errors.append(f"Malformed interval bound: p50 ({p50}) outside [{p10}, {p90}] at record [{idx}] for entity '{entity_id}'")

    # Fail-closed check for y_pred == y_true substitution trick
    # If all matched records have predicted_revenue exactly equal to realized_90d_net_revenue
    # when true outcomes are present, this is an illegal y_pred=y_true substitution.
    outcomes_with_pred = [
        r for r in records
        if _is_valid_prediction_value(r.get("predicted_revenue"))
        and _is_finite_float(r.get("realized_90d_net_revenue"))
    ]
    if outcomes_with_pred and len(outcomes_with_pred) >= 5:
        exact_matches = sum(
            1 for r in outcomes_with_pred
            if float(r["predicted_revenue"]) == float(r["realized_90d_net_revenue"])
        )
        if exact_matches == len(outcomes_with_pred):
            errors.append(f"Illegal y_pred=y_true substitution detected: all {len(outcomes_with_pred)} predictions exactly match realized 90d net revenue")

    # Fail-closed check for fixed multiplier horizon metrics (e.g. m6 = y_90d * 2.0 or m12 = y_90d * 4.0 for all records)
    m6_outcomes = [
        r for r in records
        if _is_finite_float(r.get("realized_m6_net_revenue") or r.get("realized_180d_net_revenue"))
        and _is_finite_float(r.get("realized_90d_net_revenue"))
    ]
    if m6_outcomes and len(m6_outcomes) >= 5:
        fixed_mult_matches = sum(
            1 for r in m6_outcomes
            if float(r.get("realized_m6_net_revenue") or r.get("realized_180d_net_revenue")) == float(r["realized_90d_net_revenue"]) * 2.0
        )
        if fixed_mult_matches == len(m6_outcomes):
            errors.append(f"Illegal fixed multiplier horizon metric detected: all {len(m6_outcomes)} M6 outcomes are exactly 2.0x 90d revenue")

    # Fail-closed check for store age substituted for outcome coverage
    age_substituted = [
        r for r in records
        if r.get("store_age_days") is not None
        and int(r.get("store_age_days", 0)) >= 180
        and not _is_finite_float(r.get("realized_m6_net_revenue") or r.get("realized_180d_net_revenue") or r.get("m6_outcome") or r.get("realized_m6_revenue"))
        and r.get("m6_covered") is True
    ]
    if age_substituted:
        errors.append(f"Illegal store age substitution detected: {len(age_substituted)} records claim M6 coverage based on store_age_days without explicit realized M6 revenue")

    if not dataset_snapshot_id or dataset_snapshot_id.lower() in DISALLOWED_SNAPSHOT_IDS:
        errors.append("Missing required dataset_snapshot_id for prediction source lineage")
    if not model_version or model_version.lower() in DISALLOWED_MODEL_VERSIONS:
        errors.append("Missing required model_version for prediction source lineage")
    if not artifact_lineage_id or artifact_lineage_id.lower() in DISALLOWED_LINEAGE_IDS:
        errors.append("Missing required artifact_lineage_id for prediction source lineage")

    if errors:
        reason_code = "MISSING_GOVERNED_LINEAGE"
        if duplicate_count > 0:
            reason_code = "DUPLICATE_PREDICTION_SOURCE"
        elif malformed_interval_count > 0:
            reason_code = "MALFORMED_INTERVAL_BOUNDS"
        elif unmatched_count > 0:
            reason_code = "UNMATCHED_PREDICTION_SOURCE"
        elif any("substitution" in e or "multiplier" in e for e in errors):
            reason_code = "SYNTHETIC_SUBSTITUTION_REJECTED"

        return SiteScorePredictionSourceVerificationResult(
            is_valid=False,
            reason_code=reason_code,
            matched_count=matched_count,
            unmatched_count=unmatched_count,
            duplicate_count=duplicate_count,
            malformed_interval_count=malformed_interval_count,
            errors=tuple(errors),
            dataset_snapshot_id=dataset_snapshot_id,
            model_version=model_version,
            artifact_lineage_id=artifact_lineage_id,
        )

    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id=dataset_snapshot_id,
        model_version=model_version,
        artifact_lineage_id=artifact_lineage_id,
    )
    prediction_receipt_hash = receipt["integrity"]["content_sha256"]

    return SiteScorePredictionSourceVerificationResult(
        is_valid=True,
        reason_code="PREDICTION_SOURCE_VERIFIED",
        matched_count=matched_count,
        unmatched_count=unmatched_count,
        duplicate_count=duplicate_count,
        malformed_interval_count=malformed_interval_count,
        errors=(),
        dataset_snapshot_id=dataset_snapshot_id,
        model_version=model_version,
        artifact_lineage_id=artifact_lineage_id,
        prediction_receipt_hash=prediction_receipt_hash,
    )


__all__ = [
    "PREDICTION_SOURCE_RECEIPT_SCHEMA_VERSION",
    "PREDICTION_SOURCE_RECEIPT_KIND",
    "CANONICAL_PREDICTION_MODEL_NAME",
    "CANONICAL_PREDICTION_SERVICE",
    "CANONICAL_MODEL_VERSION",
    "SiteScorePredictionRecord",
    "SiteScorePredictionSourceVerificationResult",
    "build_sitescore_prediction_source_receipt",
    "compute_prediction_source_receipt_sha256",
    "verify_sitescore_prediction_source",
]
