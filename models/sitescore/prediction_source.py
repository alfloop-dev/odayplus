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
APPROVED_MODEL_VERSIONS = {CANONICAL_MODEL_VERSION, "candidate-site-view-v1", "candidate-site-view-v2"}


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
    prediction_as_of: str | None = None
    model_version: str | None = None
    horizon_code: str = "90d"
    dataset_snapshot_id: str | None = None
    artifact_lineage_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "entity_id": self.entity_id,
            "store_id": self.store_id,
            "opened_on": self.opened_on,
            "prediction_as_of": self.prediction_as_of or self.opened_on,
            "model_version": self.model_version or CANONICAL_MODEL_VERSION,
            "horizon_code": self.horizon_code,
            "target_format_code": self.target_format_code,
            "predicted_revenue": self.predicted_revenue,
            "p10": self.p10,
            "p90": self.p90,
        }
        if self.p50 is not None:
            res["p50"] = self.p50
        if self.dataset_snapshot_id is not None:
            res["dataset_snapshot_id"] = self.dataset_snapshot_id
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
    m6_matched_count: int = 0
    m12_matched_count: int = 0
    errors: Sequence[str] = field(default_factory=tuple)
    dataset_snapshot_id: str | None = None
    model_version: str | None = None
    artifact_lineage_id: str | None = None
    prediction_receipt_hash: str | None = None
    population_report: dict[str, Any] = field(default_factory=dict)


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
        if "prediction_as_of" not in rec_dict:
            rec_dict["prediction_as_of"] = str(rec_dict.get("opened_on") or "")
        if "model_version" not in rec_dict:
            rec_dict["model_version"] = model_version
        if "horizon_code" not in rec_dict:
            rec_dict["horizon_code"] = "90d"
        rec_dict["dataset_snapshot_id"] = dataset_snapshot_id
        rec_dict["artifact_lineage_id"] = artifact_lineage_id
        canonical_records.append(rec_dict)

    sorted_records = sorted(
        canonical_records,
        key=lambda x: (
            str(x.get("entity_id") or x.get("store_id") or ""),
            str(x.get("prediction_as_of") or x.get("opened_on") or ""),
            str(x.get("model_version") or ""),
            str(x.get("horizon_code") or "90d"),
        ),
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
    prediction_receipt: dict[str, Any] | None = None,
    model_registry_evidence: Any | None = None,
    expected_snapshot_id: str | None = None,
    expected_model_version: str | None = None,
    expected_lineage_id: str | None = None,
    provenance: str = "authenticated_governed_records",
) -> SiteScorePredictionSourceVerificationResult:
    """Fail-closed verification of SiteScore prediction source evidence."""
    errors: list[str] = []

    if not records:
        return SiteScorePredictionSourceVerificationResult(
            is_valid=False,
            reason_code="NO_PREDICTION_RECORDS",
            errors=("No prediction records provided",),
        )

    receipt_to_verify = prediction_receipt
    if receipt_to_verify is None:
        if isinstance(records, dict) and "prediction_receipt" in records:
            receipt_to_verify = records["prediction_receipt"]
        elif records and isinstance(records[0], dict) and "_prediction_receipt" in records[0]:
            receipt_to_verify = records[0]["_prediction_receipt"]

    verified_receipt_hash: str | None = None
    dataset_snapshot_id: str | None = expected_snapshot_id
    model_version: str | None = expected_model_version
    artifact_lineage_id: str | None = expected_lineage_id

    DISALLOWED_SNAPSHOT_SUBSTRINGS = (
        "caller-self-attested", "arbitrary-caller-snapshot", "fresh-caller-snapshot",
        "unverified", "self-attested", "caller-attested", "none", "null"
    )
    DISALLOWED_MODEL_VERSIONS = (
        "stale-alias-v0", "arbitrary-caller-model", "forged-approved-v42",
        "unverified", "stale-alias", "none", "null"
    )
    DISALLOWED_LINEAGE_SUBSTRINGS = (
        "not-a-hash", "arbitrary-caller-artifact", "opaque-caller-lineage",
        "unverified", "not_a_hash", "none", "null"
    )

    if provenance not in ("authenticated_governed_records", "pg16_prediction_query", "authenticated_prediction_registry"):
        return SiteScorePredictionSourceVerificationResult(
            is_valid=False,
            reason_code="UNAUTHENTICATED_PREDICTION_PROVENANCE",
            errors=(
                f"Unauthenticated prediction provenance: '{provenance}'. "
                "Self-attestation from caller records is unauthenticated.",
            ),
        )

    receipt_records_lookup: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    if receipt_to_verify is not None:
        if not isinstance(receipt_to_verify, dict):
            return SiteScorePredictionSourceVerificationResult(
                is_valid=False,
                reason_code="INTEGRITY_HASH_MISMATCH",
                errors=("Provided prediction_receipt is not a dictionary",),
            )

        if receipt_to_verify.get("schema_version") != PREDICTION_SOURCE_RECEIPT_SCHEMA_VERSION:
            errors.append(f"Invalid prediction receipt schema_version: expected {PREDICTION_SOURCE_RECEIPT_SCHEMA_VERSION}, got {receipt_to_verify.get('schema_version')}")
        if receipt_to_verify.get("kind") != PREDICTION_SOURCE_RECEIPT_KIND:
            errors.append(f"Invalid prediction receipt kind: expected {PREDICTION_SOURCE_RECEIPT_KIND}, got {receipt_to_verify.get('kind')}")
        if receipt_to_verify.get("model_name") != CANONICAL_PREDICTION_MODEL_NAME:
            errors.append(f"Invalid prediction receipt model_name: expected {CANONICAL_PREDICTION_MODEL_NAME}, got {receipt_to_verify.get('model_name')}")
        if receipt_to_verify.get("service") != CANONICAL_PREDICTION_SERVICE:
            errors.append(f"Invalid prediction receipt service: expected {CANONICAL_PREDICTION_SERVICE}, got {receipt_to_verify.get('service')}")

        integrity = receipt_to_verify.get("integrity")
        if not isinstance(integrity, dict) or not integrity.get("content_sha256"):
            errors.append("Missing integrity.content_sha256 envelope in prediction receipt")
        else:
            calc_content_sha = compute_prediction_source_receipt_sha256(receipt_to_verify)
            if integrity["content_sha256"] != calc_content_sha:
                errors.append(f"Prediction receipt content_sha256 mismatch: declared {integrity['content_sha256']}, recomputed {calc_content_sha}")

        rec_list = receipt_to_verify.get("records")
        if not isinstance(rec_list, list):
            errors.append("Prediction receipt records field must be a list")
        else:
            rec_payload = json.dumps(rec_list, sort_keys=True, separators=(",", ":"), allow_nan=False)
            calc_rec_sha = hashlib.sha256(rec_payload.encode("utf-8")).hexdigest()
            declared_rec_sha = receipt_to_verify.get("records_sha256") or (integrity.get("records_sha256") if isinstance(integrity, dict) else None)
            if declared_rec_sha != calc_rec_sha:
                errors.append(f"Prediction receipt records_sha256 mismatch: declared {declared_rec_sha}, recomputed {calc_rec_sha}")

        rec_snap = str(receipt_to_verify.get("dataset_snapshot_id") or "").strip()
        rec_ver = str(receipt_to_verify.get("model_version") or "").strip()
        rec_lin = str(receipt_to_verify.get("artifact_lineage_id") or "").strip()

        if not rec_snap or any(sub in rec_snap.lower() for sub in DISALLOWED_SNAPSHOT_SUBSTRINGS):
            errors.append(f"Invalid or disallowed dataset_snapshot_id in prediction receipt: '{rec_snap}'")
        if not rec_ver or rec_ver.lower() in DISALLOWED_MODEL_VERSIONS or rec_ver not in APPROVED_MODEL_VERSIONS:
            errors.append(f"Invalid or disallowed model_version in prediction receipt: '{rec_ver}'")
        if not rec_lin or any(sub in rec_lin.lower() for sub in DISALLOWED_LINEAGE_SUBSTRINGS):
            errors.append(f"Invalid or disallowed artifact_lineage_id in prediction receipt: '{rec_lin}'")

        if expected_snapshot_id and rec_snap != expected_snapshot_id:
            errors.append(f"Prediction receipt dataset_snapshot_id '{rec_snap}' mismatch with expected '{expected_snapshot_id}'")
        if expected_model_version and rec_ver != expected_model_version:
            errors.append(f"Prediction receipt model_version '{rec_ver}' mismatch with expected '{expected_model_version}'")
        if expected_lineage_id and rec_lin != expected_lineage_id:
            errors.append(f"Prediction receipt artifact_lineage_id '{rec_lin}' mismatch with expected '{expected_lineage_id}'")

        dataset_snapshot_id = rec_snap
        model_version = rec_ver
        artifact_lineage_id = rec_lin
        verified_receipt_hash = integrity.get("content_sha256") if isinstance(integrity, dict) else None

        if isinstance(rec_list, list):
            for item in rec_list:
                if isinstance(item, dict):
                    eid = str(item.get("entity_id") or item.get("store_id") or "")
                    as_of = str(item.get("prediction_as_of") or item.get("opened_on") or "")
                    ver = str(item.get("model_version") or model_version or "")
                    hor = str(item.get("horizon_code") or "90d")
                    receipt_records_lookup[(eid, as_of, ver, hor)] = item
                    sid = str(item.get("store_id") or "")
                    if sid and sid != eid:
                        receipt_records_lookup[(sid, as_of, ver, hor)] = item

    if model_registry_evidence is not None:
        reg_model_name = getattr(model_registry_evidence, "model_name", None) or (model_registry_evidence.get("model_name") if isinstance(model_registry_evidence, dict) else None)
        if reg_model_name != CANONICAL_PREDICTION_MODEL_NAME:
            errors.append(f"Model registry evidence model_name ({reg_model_name!r}) mismatch with canonical '{CANONICAL_PREDICTION_MODEL_NAME}'")

    if receipt_to_verify is None:
        if records and isinstance(records[0], dict):
            r0 = records[0]
            if not dataset_snapshot_id:
                dataset_snapshot_id = str(r0.get("dataset_snapshot_id") or r0.get("dataset_snapshot_hash") or "").strip() or None
            if not model_version:
                model_version = str(r0.get("model_version") or "").strip() or None
            if not artifact_lineage_id:
                artifact_lineage_id = str(r0.get("artifact_lineage_id") or r0.get("artifact_hash") or "").strip() or None

        if not dataset_snapshot_id or any(sub in dataset_snapshot_id.lower() for sub in DISALLOWED_SNAPSHOT_SUBSTRINGS):
            errors.append(f"Invalid or disallowed dataset_snapshot_id: '{dataset_snapshot_id}'")
        if not model_version or model_version.lower() in DISALLOWED_MODEL_VERSIONS or model_version not in APPROVED_MODEL_VERSIONS:
            errors.append(f"Invalid or disallowed model_version: '{model_version}'")
        if not artifact_lineage_id or any(sub in artifact_lineage_id.lower() for sub in DISALLOWED_LINEAGE_SUBSTRINGS):
            errors.append(f"Invalid or disallowed artifact_lineage_id: '{artifact_lineage_id}'")

        if expected_snapshot_id and dataset_snapshot_id != expected_snapshot_id:
            errors.append(f"Dataset snapshot ID '{dataset_snapshot_id}' mismatch with expected '{expected_snapshot_id}'")
        if expected_model_version and model_version != expected_model_version:
            errors.append(f"Model version '{model_version}' mismatch with expected '{expected_model_version}'")
        if expected_lineage_id and artifact_lineage_id != expected_lineage_id:
            errors.append(f"Artifact lineage ID '{artifact_lineage_id}' mismatch with expected '{expected_lineage_id}'")

    if errors:
        reason_code = "MISSING_GOVERNED_LINEAGE"
        if any("content_sha256" in e or "records_sha256" in e for e in errors):
            reason_code = "INTEGRITY_HASH_MISMATCH"
        return SiteScorePredictionSourceVerificationResult(
            is_valid=False,
            reason_code=reason_code,
            errors=tuple(errors),
        )

    matched_count = 0
    unmatched_count = 0
    duplicate_count = 0
    malformed_interval_count = 0
    m6_matched_count = 0
    m12_matched_count = 0

    seen_entity_asof_version_horizon: set[tuple[str, str, str, str]] = set()

    ref_date = datetime.now(UTC).date()

    for idx, r in enumerate(records):
        if not isinstance(r, dict):
            errors.append(f"Record [{idx}] must be a dictionary")
            continue

        entity_id = str(r.get("entity_id") or r.get("store_id") or f"idx-{idx}")
        store_id = str(r.get("store_id") or entity_id)
        opened_on = str(r.get("opened_on") or r.get("prediction_as_of") or "")
        pred_as_of = str(r.get("prediction_as_of") or opened_on)
        rec_ver = str(r.get("model_version") or model_version or "")
        rec_hor = str(r.get("horizon_code") or "90d")

        join_key = (entity_id, pred_as_of, rec_ver, rec_hor)
        if join_key in seen_entity_asof_version_horizon:
            duplicate_count += 1
            errors.append(f"Duplicate prediction record for entity '{entity_id}' as-of '{pred_as_of}' version '{rec_ver}' horizon '{rec_hor}'")
        seen_entity_asof_version_horizon.add(join_key)

        if pred_as_of:
            try:
                dt_opened = datetime.strptime(pred_as_of[:10], "%Y-%m-%d").date()
                if dt_opened > ref_date:
                    errors.append(f"Future or invalid opened_on as-of date at record [{idx}]: '{pred_as_of}'")
            except Exception:
                errors.append(f"Invalid opened_on date format at record [{idx}]: '{pred_as_of}'")

        rec_from_receipt = None
        if receipt_records_lookup:
            rec_from_receipt = receipt_records_lookup.get((entity_id, pred_as_of, rec_ver, rec_hor))
            if rec_from_receipt is None and store_id != entity_id:
                rec_from_receipt = receipt_records_lookup.get((store_id, pred_as_of, rec_ver, rec_hor))
            if rec_from_receipt is None:
                rec_from_receipt = receipt_records_lookup.get((entity_id, pred_as_of, rec_ver, "90d"))

            if rec_from_receipt is None:
                unmatched_count += 1
                errors.append(f"Unmatched prediction for entity '{entity_id}' as-of '{pred_as_of}' in verified receipt")
                continue

        source_rec = rec_from_receipt or r
        pred_raw = source_rec.get("predicted_revenue")
        p10_raw = source_rec.get("p10")
        p90_raw = source_rec.get("p90")
        p50_raw = source_rec.get("p50")

        if pred_raw is None or not _is_valid_prediction_value(pred_raw):
            unmatched_count += 1
            errors.append(f"Invalid or missing predicted_revenue at record [{idx}] for entity '{entity_id}'")
            continue

        matched_count += 1

        if r.get("predicted_m6_revenue") is not None or rec_hor == "M6":
            m6_matched_count += 1
        if r.get("predicted_m12_revenue") is not None or rec_hor == "M12":
            m12_matched_count += 1

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

    age_substituted = [
        r for r in records
        if r.get("store_age_days") is not None
        and int(r.get("store_age_days", 0)) >= 180
        and not _is_finite_float(r.get("realized_m6_net_revenue") or r.get("realized_180d_net_revenue") or r.get("m6_outcome") or r.get("realized_m6_revenue"))
        and r.get("m6_covered") is True
    ]
    if age_substituted:
        errors.append(f"Illegal store age substitution detected: {len(age_substituted)} records claim M6 coverage based on store_age_days without explicit realized M6 revenue")

    if verified_receipt_hash is None and not errors and dataset_snapshot_id and model_version and artifact_lineage_id:
        gen_receipt = build_sitescore_prediction_source_receipt(
            records,
            dataset_snapshot_id=dataset_snapshot_id,
            model_version=model_version,
            artifact_lineage_id=artifact_lineage_id,
        )
        verified_receipt_hash = gen_receipt["integrity"]["content_sha256"]

    population_report = {
        "observed_records": len(records),
        "matched_predictions": matched_count,
        "unmatched_predictions": unmatched_count,
        "duplicate_predictions": duplicate_count,
        "malformed_intervals": malformed_interval_count,
        "m6_matched": m6_matched_count,
        "m12_matched": m12_matched_count,
        "verified_receipt_hash": verified_receipt_hash,
    }

    if errors:
        reason_code = "MISSING_GOVERNED_LINEAGE"
        if duplicate_count > 0:
            reason_code = "DUPLICATE_PREDICTION_SOURCE"
        elif malformed_interval_count > 0:
            reason_code = "MALFORMED_INTERVAL_BOUNDS"
        elif unmatched_count > 0:
            reason_code = "UNMATCHED_PREDICTION_SOURCE"
        elif any("content_sha256" in e or "records_sha256" in e for e in errors):
            reason_code = "INTEGRITY_HASH_MISMATCH"
        elif any("substitution" in e or "multiplier" in e for e in errors):
            reason_code = "SYNTHETIC_SUBSTITUTION_REJECTED"
        elif any("unauthenticated" in e.lower() for e in errors):
            reason_code = "UNAUTHENTICATED_PREDICTION_PROVENANCE"

        return SiteScorePredictionSourceVerificationResult(
            is_valid=False,
            reason_code=reason_code,
            matched_count=matched_count,
            unmatched_count=unmatched_count,
            duplicate_count=duplicate_count,
            malformed_interval_count=malformed_interval_count,
            m6_matched_count=m6_matched_count,
            m12_matched_count=m12_matched_count,
            errors=tuple(errors),
            dataset_snapshot_id=dataset_snapshot_id,
            model_version=model_version,
            artifact_lineage_id=artifact_lineage_id,
            population_report=population_report,
        )

    return SiteScorePredictionSourceVerificationResult(
        is_valid=True,
        reason_code="PREDICTION_SOURCE_VERIFIED",
        matched_count=matched_count,
        unmatched_count=unmatched_count,
        duplicate_count=duplicate_count,
        malformed_interval_count=malformed_interval_count,
        m6_matched_count=m6_matched_count,
        m12_matched_count=m12_matched_count,
        errors=(),
        dataset_snapshot_id=dataset_snapshot_id,
        model_version=model_version,
        artifact_lineage_id=artifact_lineage_id,
        prediction_receipt_hash=verified_receipt_hash,
        population_report=population_report,
    )


__all__ = [
    "PREDICTION_SOURCE_RECEIPT_SCHEMA_VERSION",
    "PREDICTION_SOURCE_RECEIPT_KIND",
    "CANONICAL_PREDICTION_MODEL_NAME",
    "CANONICAL_PREDICTION_SERVICE",
    "CANONICAL_MODEL_VERSION",
    "APPROVED_MODEL_VERSIONS",
    "SiteScorePredictionRecord",
    "SiteScorePredictionSourceVerificationResult",
    "build_sitescore_prediction_source_receipt",
    "compute_prediction_source_receipt_sha256",
    "verify_sitescore_prediction_source",
]
