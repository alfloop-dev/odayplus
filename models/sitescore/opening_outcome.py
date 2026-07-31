"""SiteScore opening outcome M6/M12 inventory coverage calibration benchmark and Gate 2 receipt."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from models.shared_ml.model_card import ModelCard, ModelCardApproval, ModelRiskLevel

ACTIVATION_THRESHOLD = 200
MIN_COVERAGE_THRESHOLD = 0.70
MAX_MAE_THRESHOLD = 0.25
GATE2_RECEIPT_SCHEMA_VERSION = 1
GATE2_RECEIPT_KIND = "sitescore-opening-outcome-gate2-receipt"
CANONICAL_INVENTORY_VERSION = "candidate-site-view-v2"
CANONICAL_SOURCE_CONTRACT = f"model_ready.candidate_site_view@{CANONICAL_INVENTORY_VERSION}"



def _is_finite_float(val: Any) -> bool:
    """Check if value is a finite float (not None, bool, NaN, inf, or -inf)."""
    if val is None or isinstance(val, bool):
        return False
    try:
        f = float(val)
        return math.isfinite(f)
    except (ValueError, TypeError):
        return False


def _is_valid_realized_outcome(val: Any) -> bool:
    """Check if a realized outcome value is finite and >= 0.0.

    Negative-value policy:
    Legitimate zero net revenue outcomes (val == 0.0 or val >= 0.0) are valid mature labels.
    Negative or non-finite values represent corrupted/unverified net revenue data and are excluded.
    """
    if not _is_finite_float(val):
        return False
    return float(val) >= 0.0


@dataclass(frozen=True)
class SiteScoreOpeningOutcomeBenchmarkResult:
    observed_count: int
    eligible_count: int
    mature_label_count: int
    m6_coverage_ratio: float
    m12_coverage_ratio: float
    normalized_mae: float
    p80_coverage: float
    prediction_coverage_ratio: float = 0.0
    interval_bounds_coverage_ratio: float = 0.0
    matched_prediction_count: int = 0
    m6_mature_count: int = 0
    m12_mature_count: int = 0
    interval_bounds_count: int = 0
    in_p80_count: int = 0
    matched_mean_y: float = 0.0
    unmatched_mean_y: float = 0.0
    realized_revenue_sum: float = 0.0
    mean_realized_revenue: float = 0.0
    dataset_snapshot_id: str | None = None
    model_version: str | None = None
    artifact_lineage_id: str | None = None
    provenance: str = "no_source"
    db_error: str | None = None
    activation_threshold: int = ACTIVATION_THRESHOLD
    min_coverage_threshold: float = MIN_COVERAGE_THRESHOLD
    max_mae_threshold: float = MAX_MAE_THRESHOLD
    segment_metrics: Sequence[dict[str, Any]] = field(default_factory=tuple)
    calibration_summary: dict[str, Any] = field(default_factory=dict)
    observed_at: str | None = None

    @property
    def is_lineage_governed(self) -> bool:
        # Until ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001 provides an authoritative prediction-source resolver,
        # caller-supplied lineage strings and pg16_query records are unverified self-attestations and must remain GOVERNED_DISABLED.
        return False

    @property
    def is_labels_sufficient(self) -> bool:
        return self.mature_label_count >= self.activation_threshold

    @property
    def is_prediction_coverage_passed(self) -> bool:
        return _is_finite_float(self.prediction_coverage_ratio) and self.prediction_coverage_ratio >= self.min_coverage_threshold

    @property
    def is_coverage_passed(self) -> bool:
        return (
            _is_finite_float(self.m6_coverage_ratio) and self.m6_coverage_ratio >= self.min_coverage_threshold
            and _is_finite_float(self.m12_coverage_ratio) and self.m12_coverage_ratio >= self.min_coverage_threshold
            and _is_finite_float(self.p80_coverage) and self.p80_coverage >= self.min_coverage_threshold
            and self.is_prediction_coverage_passed
        )

    @property
    def is_interval_bounds_passed(self) -> bool:
        return _is_finite_float(self.interval_bounds_coverage_ratio) and self.interval_bounds_coverage_ratio >= self.min_coverage_threshold

    @property
    def is_mae_passed(self) -> bool:
        return _is_finite_float(self.normalized_mae) and self.normalized_mae <= self.max_mae_threshold

    @property
    def is_gate2_passed(self) -> bool:
        return (
            self.is_lineage_governed
            and self.is_labels_sufficient
            and self.is_coverage_passed
            and self.is_interval_bounds_passed
            and self.is_mae_passed
        )

    @property
    def status(self) -> str:
        return "ACTIVE" if self.is_gate2_passed else "GOVERNED_DISABLED"

    @property
    def reason_code(self) -> str:
        if self.provenance == "unreachable_db":
            return "DB_INVENTORY_UNREACHABLE"
        if self.provenance == "no_source":
            return "NO_SOURCE_INVENTORY"
        if self.provenance == "provided_records":
            return "UNAUTHENTICATED_PROVENANCE"
        if not self.is_lineage_governed:
            return "MISSING_GOVERNED_LINEAGE"
        if not self.is_labels_sufficient:
            return "MATURE_LABELS_BELOW_THRESHOLD"
        if not self.is_prediction_coverage_passed:
            return "PREDICTION_EVIDENCE_MISSING"
        if (
            self.m6_coverage_ratio < self.min_coverage_threshold
            or self.m12_coverage_ratio < self.min_coverage_threshold
        ):
            return "M6_M12_COVERAGE_INSUFFICIENT"
        if not self.is_interval_bounds_passed:
            return "INTERVAL_BOUNDS_MISSING"
        if not self.is_mae_passed:
            return "NORMALIZED_MAE_EXCEEDED"
        if self.is_gate2_passed:
            return "GATE2_CRITERIA_MET"
        return "GOVERNED_DISABLED"

    @property
    def handback_payload(self) -> dict[str, Any]:
        if self.is_gate2_passed:
            return {
                "handback_required": False,
                "reason_code": self.reason_code,
                "message": "SiteScore opening outcome M6/M12 coverage calibration benchmark passed Gate 2.",
            }

        missing_labels = max(0, self.activation_threshold - self.mature_label_count)
        reasons = []

        if self.provenance == "unreachable_db":
            err_msg = f": {self.db_error}" if self.db_error else ""
            reasons.append(f"PostgreSQL model-ready inventory database query failed{err_msg}")
            handback_action = "Restore PostgreSQL database connection and provide authoritative outcome backfill (ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001) and prediction-source (ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001) receipts."
        elif self.provenance == "no_source":
            reasons.append("No database connection or candidate site records were provided")
            handback_action = "Provide authoritative outcome backfill receipt (ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001) and prediction-source receipt (ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001) with true M6/M12 realized net revenue, interval bounds, and lineage."
        elif self.provenance == "provided_records":
            reasons.append("Provided records are unauthenticated / non-governed activation input")
            handback_action = "Provide authenticated governed PostgreSQL inventory records with outcome backfill receipt (ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001) and prediction-source receipt (ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001)."
        elif not self.is_lineage_governed:
            reasons.append(
                f"Missing governed dataset snapshot or model/artifact lineage (snapshot={self.dataset_snapshot_id}, model_version={self.model_version}, artifact_lineage_id={self.artifact_lineage_id}; requires authoritative prediction-source resolver ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001)"
            )
            if not math.isfinite(self.normalized_mae) or self.normalized_mae > self.max_mae_threshold:
                reasons.append(
                    f"Normalized MAE ({self.normalized_mae:.3f}) exceeds maximum threshold ({self.max_mae_threshold:.3f})"
                )
            handback_action = "Provide complete governed dataset snapshot ID/hash and model/artifact lineage resolved via outcome backfill receipt (ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001) and prediction-source receipt (ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001)."
        else:
            if not self.is_labels_sufficient:
                reasons.append(
                    f"Mature label count ({self.mature_label_count}) is below threshold ({self.activation_threshold})"
                )
            if not self.is_prediction_coverage_passed:
                reasons.append(
                    f"Prediction coverage ({self.prediction_coverage_ratio:.1%}) is below threshold ({self.min_coverage_threshold:.1%})"
                )
            if self.m6_coverage_ratio < self.min_coverage_threshold:
                reasons.append(
                    f"M6 horizon coverage ({self.m6_coverage_ratio:.1%}) is below threshold ({self.min_coverage_threshold:.1%})"
                )
            if self.m12_coverage_ratio < self.min_coverage_threshold:
                reasons.append(
                    f"M12 horizon coverage ({self.m12_coverage_ratio:.1%}) is below threshold ({self.min_coverage_threshold:.1%})"
                )
            if not self.is_interval_bounds_passed:
                reasons.append(
                    f"Interval bounds coverage ({self.interval_bounds_coverage_ratio:.1%}) is below threshold ({self.min_coverage_threshold:.1%})"
                )
            if self.p80_coverage < self.min_coverage_threshold:
                reasons.append(
                    f"P80 coverage ({self.p80_coverage:.1%}) is below threshold ({self.min_coverage_threshold:.1%})"
                )
            if not math.isfinite(self.normalized_mae) or self.normalized_mae > self.max_mae_threshold:
                reasons.append(
                    f"Normalized MAE ({self.normalized_mae:.3f}) exceeds maximum threshold ({self.max_mae_threshold:.3f})"
                )
            handback_action = (
                f"Provide >= {self.activation_threshold} mature opening outcome labels with complete "
                f"M6 (180d) and M12 (365d) post-opening transaction history, actual p10/p90 interval bounds, and model predictions "
                f"via outcome backfill (ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001) and prediction-source (ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001) receipts."
            )

        executable_query = (
            "SELECT entity_id, store_id, target_format_code, opened_on, is_training_eligible, "
            "realized_90d_net_revenue, (CURRENT_DATE - opened_on)::integer AS store_age_days "
            "FROM model_ready.candidate_site_view;"
        )
        return {
            "handback_required": True,
            "reason_code": self.reason_code,
            "governed_disabled": True,
            "provenance": self.provenance,
            "observed_count": self.observed_count,
            "eligible_count": self.eligible_count,
            "mature_label_count": self.mature_label_count,
            "matched_prediction_count": self.matched_prediction_count,
            "m6_mature_count": self.m6_mature_count,
            "m12_mature_count": self.m12_mature_count,
            "interval_bounds_count": self.interval_bounds_count,
            "in_p80_count": self.in_p80_count,
            "matched_mean_y": round(self.matched_mean_y, 2),
            "unmatched_mean_y": round(self.unmatched_mean_y, 2),
            "realized_revenue_sum": round(self.realized_revenue_sum, 2),
            "mean_realized_revenue": round(self.mean_realized_revenue, 2),
            "activation_threshold": self.activation_threshold,
            "missing_labels_delta": missing_labels,
            "m6_coverage_ratio": self.m6_coverage_ratio,
            "m12_coverage_ratio": self.m12_coverage_ratio,
            "prediction_coverage_ratio": self.prediction_coverage_ratio,
            "interval_bounds_coverage_ratio": self.interval_bounds_coverage_ratio,
            "normalized_mae": self.normalized_mae if math.isfinite(self.normalized_mae) else 999.0,
            "p80_coverage": self.p80_coverage,
            "reasons": reasons,
            "handback_action": handback_action,
            "outcome_backfill_contract": {
                "owner": "Human/Ops",
                "task_id": "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001",
                "scope": "Provide authoritative M6 (180d) and M12 (365d) post-opening net revenue labels for historical opened candidate sites.",
                "discovery_source_identity": "model_ready.candidate_site_view",
                "discovery_query_id": "sitescore_opening_outcome_discovery_query_v1",
                "required_source_identity": "authoritative_opening_outcome_m6_m12_store_ledger",
                "required_query_id": "sitescore_authoritative_m6_m12_outcome_query_v1",
                "required_evidence_owner": "Human/Ops",
                "source_identity": "UNVERIFIED",
                "query_id": "UNVERIFIED",
                "dataset_snapshot_hash": "UNVERIFIED",
                "lineage_id": "UNVERIFIED",
                "freshness_timestamp": "UNVERIFIED",
                "evidence_owner": "UNVERIFIED",
                "eligibility_definition": "is_training_eligible IS True or eligible IS True",
                "maturity_definition": "realized_90d_net_revenue IS NOT NULL AND realized_90d_net_revenue >= 0",
                "m6_maturity_definition": "store_age_days >= 180 AND realized_180d_net_revenue IS NOT NULL AND realized_180d_net_revenue >= 0",
                "m12_maturity_definition": "store_age_days >= 365 AND realized_365d_net_revenue IS NOT NULL AND realized_365d_net_revenue >= 0",
                "observed_count": self.observed_count,
                "eligible_count": self.eligible_count,
                "mature_count": self.mature_label_count,
                "m6_mature_count": self.m6_mature_count,
                "m12_mature_count": self.m12_mature_count,
                "matched_prediction_count": self.matched_prediction_count,
                "interval_bounds_count": self.interval_bounds_count,
                "in_p80_count": self.in_p80_count,
                "required_fields": [
                    "authoritative_source_identity",
                    "query_id",
                    "dataset_snapshot_hash",
                    "artifact_lineage_id",
                    "evidence_owner",
                    "source_freshness_timestamp",
                    "m6_maturity_definition",
                    "m12_maturity_definition",
                    "realized_180d_net_revenue",
                    "realized_365d_net_revenue",
                    "observed_count",
                    "eligible_count",
                    "m6_mature_count",
                    "m12_mature_count",
                    "matched_prediction_count",
                    "interval_bounds_count",
                    "in_p80_count",
                ],
                "discovery_inventory_query": executable_query,
                "note": "Store age (store_age_days) and 90-day discovery inventory are preconditions/discovery only; they are not M6/M12 outcome evidence. ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001 must supply realized_180d_net_revenue and realized_365d_net_revenue.",
                "receipt_required": True,
            },
            "prediction_source_contract": {
                "owner": "SiteScore Platform Team",
                "task_id": "ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001",
                "scope": "Provide authoritative prediction-source resolver joining model predictions, p10/p90 interval bounds, and governed dataset snapshot / model / artifact lineage.",
                "required_fields": [
                    "predicted_revenue",
                    "p10",
                    "p90",
                    "dataset_snapshot_id",
                    "model_version",
                    "artifact_lineage_id",
                ],
                "receipt_required": True,
            },
            "backfill_owner": "Human/Ops",
            "backfill_task_id": "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001",
            "prediction_source_task_id": "ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001",
            "discovery_inventory_query": executable_query,
            "backfill_receipt_required": True,
        }

    def to_dict(self) -> dict[str, Any]:
        norm_mae = round(self.normalized_mae, 4) if math.isfinite(self.normalized_mae) else 999.0
        is_governed_active = self.is_gate2_passed and self.is_lineage_governed
        res = {
            "provenance": self.provenance,
            "dataset_snapshot_id": self.dataset_snapshot_id if is_governed_active else None,
            "model_version": self.model_version if is_governed_active else None,
            "artifact_lineage_id": self.artifact_lineage_id if is_governed_active else None,
            "observed_count": self.observed_count,
            "eligible_count": self.eligible_count,
            "mature_label_count": self.mature_label_count,
            "m6_mature_count": self.m6_mature_count,
            "m12_mature_count": self.m12_mature_count,
            "matched_prediction_count": self.matched_prediction_count,
            "interval_bounds_count": self.interval_bounds_count,
            "in_p80_count": self.in_p80_count,
            "matched_mean_y": round(self.matched_mean_y, 2),
            "unmatched_mean_y": round(self.unmatched_mean_y, 2),
            "realized_revenue_sum": round(self.realized_revenue_sum, 2),
            "mean_realized_revenue": round(self.mean_realized_revenue, 2),
            "prediction_coverage_ratio": round(self.prediction_coverage_ratio, 4),
            "interval_bounds_coverage_ratio": round(self.interval_bounds_coverage_ratio, 4),
            "m6_coverage_ratio": round(self.m6_coverage_ratio, 4),
            "m12_coverage_ratio": round(self.m12_coverage_ratio, 4),
            "normalized_mae": norm_mae,
            "p80_coverage": round(self.p80_coverage, 4),
            "activation_threshold": self.activation_threshold,
            "min_coverage_threshold": self.min_coverage_threshold,
            "max_mae_threshold": self.max_mae_threshold,
            "is_gate2_passed": self.is_gate2_passed,
            "status": self.status,
            "reason_code": self.reason_code,
            "handback_payload": self.handback_payload,
            "calibration_summary": self.calibration_summary,
            "segment_metrics": list(self.segment_metrics),
            "observed_at": self.observed_at,
        }
        if self.db_error:
            res["db_error"] = self.db_error
        return res


def evaluate_sitescore_opening_outcome_benchmark(
    records: Sequence[dict[str, Any]] | None = None,
    *,
    provenance: str = "provided_records",
    db_error: str | None = None,
    dataset_snapshot_id: str | None = None,
    model_version: str | None = None,
    artifact_lineage_id: str | None = None,
    activation_threshold: int = ACTIVATION_THRESHOLD,
    min_coverage: float = MIN_COVERAGE_THRESHOLD,
    max_mae: float = MAX_MAE_THRESHOLD,
    observed_at: str | datetime | None = None,
) -> SiteScoreOpeningOutcomeBenchmarkResult:
    """Evaluate candidate store records against M6/M12 inventory coverage & calibration thresholds."""
    if records is None:
        records = []

    if db_error is not None:
        provenance = "unreachable_db"

    # Extract dataset snapshot and model lineage from records if not explicitly passed
    if not dataset_snapshot_id:
        for r in records:
            snap = r.get("dataset_snapshot_id") or r.get("dataset_snapshot_hash")
            if snap:
                dataset_snapshot_id = str(snap)
                break

    if not model_version:
        for r in records:
            ver = r.get("model_version")
            if ver:
                model_version = str(ver)
                break

    if not artifact_lineage_id:
        for r in records:
            lin = r.get("artifact_lineage_id") or r.get("artifact_hash")
            if lin:
                artifact_lineage_id = str(lin)
                break

    observed_count = len(records)
    eligible_count = sum(1 for r in records if r.get("is_training_eligible") or r.get("eligible"))

    mature_records = [
        r for r in records
        if (r.get("is_training_eligible") or r.get("eligible"))
        and _is_valid_realized_outcome(r.get("realized_90d_net_revenue"))
    ]
    mature_label_count = len(mature_records)

    ref_date = datetime.now(UTC).date()
    observed_at_iso: str | None = None
    if isinstance(observed_at, str):
        observed_at_iso = observed_at
        try:
            ref_date = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).date()
        except Exception:
            pass
    elif isinstance(observed_at, datetime):
        observed_at_iso = observed_at.isoformat().replace("+00:00", "Z")
        ref_date = observed_at.date()
    else:
        observed_at_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def get_days_elapsed(r: dict[str, Any], key: str) -> int | None:
        val = r.get(key)
        if val is not None and isinstance(val, (int, float)) and _is_finite_float(val):
            return int(val)
        val = r.get("store_age_days")
        if val is not None and isinstance(val, (int, float)) and _is_finite_float(val):
            return int(val)
        opened_on = r.get("opened_on")
        if opened_on:
            try:
                if isinstance(opened_on, str):
                    d = datetime.strptime(opened_on[:10], "%Y-%m-%d").date()
                elif hasattr(opened_on, "year"):
                    d = opened_on
                else:
                    return None
                return (ref_date - d).days
            except Exception:
                pass
        return None

    def has_explicit_m6_outcome(r: dict[str, Any]) -> bool:
        # Must have explicit realized M6 outcome data AND store age >= 180 days
        m6_val = r.get("realized_m6_net_revenue")
        if m6_val is None:
            m6_val = r.get("m6_outcome")
        if m6_val is None:
            m6_val = r.get("realized_180d_net_revenue")
        if m6_val is None:
            m6_val = r.get("realized_m6_revenue")
        if not _is_valid_realized_outcome(m6_val):
            return False

        if r.get("m6_covered") is False:
            return False

        days = get_days_elapsed(r, "m6_days")
        if days is None or days < 180:
            return False
        return True

    def has_explicit_m12_outcome(r: dict[str, Any]) -> bool:
        # Must have explicit realized M12 outcome data AND store age >= 365 days
        m12_val = r.get("realized_m12_net_revenue")
        if m12_val is None:
            m12_val = r.get("m12_outcome")
        if m12_val is None:
            m12_val = r.get("realized_365d_net_revenue")
        if m12_val is None:
            m12_val = r.get("realized_m12_revenue")
        if not _is_valid_realized_outcome(m12_val):
            return False

        if r.get("m12_covered") is False:
            return False

        days = get_days_elapsed(r, "m12_days")
        if days is None or days < 365:
            return False
        return True

    m6_mature = sum(1 for r in mature_records if has_explicit_m6_outcome(r))
    m12_mature = sum(1 for r in mature_records if has_explicit_m12_outcome(r))

    m6_coverage_ratio = (m6_mature / mature_label_count) if mature_label_count > 0 else 0.0
    m12_coverage_ratio = (m12_mature / mature_label_count) if mature_label_count > 0 else 0.0

    # B2 fix: Matched population contains records that have BOTH valid finite outcome AND valid finite prediction
    matched_records = [
        r for r in mature_records if _is_finite_float(r.get("predicted_revenue"))
    ]
    matched_prediction_count = len(matched_records)
    prediction_coverage_ratio = (matched_prediction_count / mature_label_count) if mature_label_count > 0 else 0.0

    errors = []
    in_p80_count = 0
    interval_bounds_count = 0

    for r in mature_records:
        pred_val = r.get("predicted_revenue")
        if not _is_finite_float(pred_val):
            continue

        y_true = float(r.get("realized_90d_net_revenue", 0))
        y_pred = float(pred_val)
        errors.append(abs(y_true - y_pred))

        p10_raw = r.get("p10")
        p90_raw = r.get("p90")
        # B1 fix: Verify both interval bounds are finite floats
        if _is_finite_float(p10_raw) and _is_finite_float(p90_raw):
            p10 = float(p10_raw)
            p90 = float(p90_raw)
            if p10 <= p90:
                interval_bounds_count += 1
                if p10 <= y_true <= p90:
                    in_p80_count += 1

    # B2 fix: Compute mean_y and MAE over the exact same matched population!
    if matched_prediction_count > 0:
        matched_mean_y = sum(float(r.get("realized_90d_net_revenue", 0)) for r in matched_records) / matched_prediction_count
        mae = sum(errors) / matched_prediction_count
        if matched_mean_y > 0.0:
            normalized_mae = mae / matched_mean_y
        elif mae == 0.0:
            normalized_mae = 0.0
        else:
            normalized_mae = 999.0  # Fail-closed zero-denominator with non-zero MAE
    else:
        matched_mean_y = 0.0
        mae = 0.0
        normalized_mae = 0.0

    # B1 fix: Compute mean_y for unmatched mature population and reconcile overall realized_revenue_sum
    unmatched_records = [r for r in mature_records if not _is_finite_float(r.get("predicted_revenue"))]
    unmatched_count = len(unmatched_records)
    if unmatched_count > 0:
        unmatched_mean_y = sum(float(r.get("realized_90d_net_revenue", 0)) for r in unmatched_records) / unmatched_count
    else:
        unmatched_mean_y = 0.0

    realized_revenue_sum = sum(float(r.get("realized_90d_net_revenue", 0)) for r in mature_records)
    overall_mean_y = (realized_revenue_sum / mature_label_count) if mature_label_count > 0 else 0.0

    p80_coverage = (in_p80_count / mature_label_count) if mature_label_count > 0 else 0.0
    interval_bounds_coverage_ratio = (interval_bounds_count / mature_label_count) if mature_label_count > 0 else 0.0

    calibration_summary = {
        "measured_90d_mae": round(mae, 2) if errors else None,
        "matched_prediction_count": matched_prediction_count,
        "matched_mean_realized_revenue": round(matched_mean_y, 2),
        "unmatched_mean_realized_revenue": round(unmatched_mean_y, 2),
        "prediction_coverage_ratio": round(prediction_coverage_ratio, 4),
        "interval_bounds_coverage_ratio": round(interval_bounds_coverage_ratio, 4),
        "p80_coverage_ratio": round(p80_coverage, 4),
        "mean_realized_revenue": round(overall_mean_y, 2),
    }

    segments: dict[str, list[dict[str, Any]]] = {}
    for r in mature_records:
        fmt = str(r.get("target_format_code", "UNKNOWN"))
        segments.setdefault(fmt, []).append(r)

    segment_metrics = []
    for fmt, seg_records in sorted(segments.items()):
        seg_count = len(seg_records)
        seg_pred_records = [r for r in seg_records if _is_finite_float(r.get("predicted_revenue"))]
        if seg_pred_records:
            seg_mae = sum(abs(float(r.get("realized_90d_net_revenue", 0)) - float(r["predicted_revenue"])) for r in seg_pred_records) / len(seg_pred_records)
        else:
            seg_mae = 0.0
        segment_metrics.append({
            "segment_name": "target_format_code",
            "segment_value": fmt,
            "record_count": seg_count,
            "metrics": {
                "mae": round(seg_mae, 2),
                "m6_coverage": round(sum(1 for r in seg_records if has_explicit_m6_outcome(r)) / seg_count, 4),
                "m12_coverage": round(sum(1 for r in seg_records if has_explicit_m12_outcome(r)) / seg_count, 4),
                "prediction_coverage": round(len(seg_pred_records) / seg_count, 4),
            },
        })

    return SiteScoreOpeningOutcomeBenchmarkResult(
        observed_count=observed_count,
        eligible_count=eligible_count,
        mature_label_count=mature_label_count,
        matched_prediction_count=matched_prediction_count,
        m6_mature_count=m6_mature,
        m12_mature_count=m12_mature,
        interval_bounds_count=interval_bounds_count,
        in_p80_count=in_p80_count,
        matched_mean_y=matched_mean_y,
        unmatched_mean_y=unmatched_mean_y,
        realized_revenue_sum=realized_revenue_sum,
        mean_realized_revenue=overall_mean_y,
        m6_coverage_ratio=m6_coverage_ratio,
        m12_coverage_ratio=m12_coverage_ratio,
        normalized_mae=normalized_mae,
        p80_coverage=p80_coverage,
        prediction_coverage_ratio=prediction_coverage_ratio,
        interval_bounds_coverage_ratio=interval_bounds_coverage_ratio,
        dataset_snapshot_id=dataset_snapshot_id,
        model_version=model_version,
        artifact_lineage_id=artifact_lineage_id,
        provenance=provenance,
        db_error=db_error,
        activation_threshold=activation_threshold,
        min_coverage_threshold=min_coverage,
        max_mae_threshold=max_mae,
        segment_metrics=tuple(segment_metrics),
        calibration_summary=calibration_summary,
        observed_at=observed_at_iso,
    )


def build_sitescore_opening_outcome_model_card(
    benchmark: SiteScoreOpeningOutcomeBenchmarkResult,
    *,
    version: str = "candidate-site-view-v2",
    validation_run_id: str | None = None,
    feature_set_id: str | None = None,
    label_set_id: str | None = None,
    training_period: str | None = None,
    validation_period: str | None = None,
    algorithm: str | None = None,
    baseline: str | None = None,
    explainability_method: str | None = None,
    privacy_review: str | None = None,
    security_review: str | None = None,
    approvals: Sequence[ModelCardApproval] | None = None,
    created_at: datetime | str | None = None,
) -> ModelCard:
    """Build a canonical ModelCard carrying SiteScore opening outcome benchmark calibration results."""
    is_governed_active = benchmark.is_gate2_passed and benchmark.is_lineage_governed

    # B2 fix: Reject unverified free caller arguments (approvals, privacy/security reviews, periods, algorithm, baseline, etc.)
    # regardless of is_governed_active status. Sourced facts must come exclusively from verified benchmark attributes or fail closed.
    if not is_governed_active:
        dataset_snapshot_id = "UNAVAILABLE"
        model_version = "UNVERIFIED"
        resolved_val_id = "UNVERIFIED"
    else:
        dataset_snapshot_id = benchmark.dataset_snapshot_id if benchmark.dataset_snapshot_id else "UNAVAILABLE"
        model_version = benchmark.model_version if benchmark.model_version else version
        resolved_val_id = benchmark.artifact_lineage_id if benchmark.artifact_lineage_id else "UNVERIFIED"

    # Governance facts and approvals are ALWAYS forced to UNVERIFIED / UNAVAILABLE / () unless populated by an authoritative receipt
    resolved_feature_set_id = "UNVERIFIED"
    resolved_label_set_id = "UNVERIFIED"
    resolved_training_period = "UNAVAILABLE"
    resolved_validation_period = "UNAVAILABLE"
    resolved_algorithm = "UNAVAILABLE"
    resolved_baseline = "UNAVAILABLE"
    resolved_explainability_method = "UNAVAILABLE"
    resolved_privacy_review = "UNVERIFIED"
    resolved_security_review = "UNVERIFIED"
    resolved_approvals: tuple[ModelCardApproval, ...] = ()

    norm_mae = float(benchmark.normalized_mae) if math.isfinite(benchmark.normalized_mae) else 999.0

    if created_at is None:
        if benchmark.observed_at:
            try:
                card_created_at = datetime.fromisoformat(benchmark.observed_at.replace("Z", "+00:00"))
            except Exception:
                card_created_at = datetime.now(UTC)
        else:
            card_created_at = datetime.now(UTC)
    elif isinstance(created_at, str):
        try:
            card_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except Exception:
            card_created_at = datetime.now(UTC)
    elif isinstance(created_at, datetime):
        card_created_at = created_at
    else:
        card_created_at = datetime.now(UTC)

    return ModelCard(
        model_name="sitescore_propensity",
        model_version=model_version,
        owner="sitescore-platform-team",
        risk_level=ModelRiskLevel.R4,
        intended_use="Human-reviewed candidate site opening revenue & propensity prioritization",
        not_intended_use="Automatic site lease execution, store opening without human approval",
        dataset_snapshot_id=dataset_snapshot_id,
        validation_run_id=resolved_val_id,
        feature_set_id=resolved_feature_set_id,
        label_set_id=resolved_label_set_id,
        training_period=resolved_training_period,
        validation_period=resolved_validation_period,
        algorithm=resolved_algorithm,
        baseline=resolved_baseline,
        metrics_summary={
            "mature_label_count": float(benchmark.mature_label_count),
            "matched_prediction_count": float(benchmark.matched_prediction_count),
            "m6_coverage_ratio": float(benchmark.m6_coverage_ratio),
            "m12_coverage_ratio": float(benchmark.m12_coverage_ratio),
            "prediction_coverage_ratio": float(benchmark.prediction_coverage_ratio),
            "interval_bounds_coverage_ratio": float(benchmark.interval_bounds_coverage_ratio),
            "normalized_mae": norm_mae,
            "p80_coverage": float(benchmark.p80_coverage),
        },
        segment_metrics=benchmark.segment_metrics,
        calibration_summary=benchmark.calibration_summary,
        explainability_method=resolved_explainability_method,
        limitations=[
            "Requires at least 200 mature opening outcome labels with complete M6/M12 post-opening transactions.",
            "Governed-disabled when label count, M6/M12 window coverage, or interval bound coverage thresholds fail.",
        ],
        known_biases=[
            "Historical opening outcomes reflect store format expansion patterns.",
        ],
        privacy_review=resolved_privacy_review,
        security_review=resolved_security_review,
        release_status="DEV" if is_governed_active else "GOVERNED_DISABLED",
        rollback_conditions=[
            "Normalized MAE > 0.25 on 30-day rolling window",
            "M6 or M12 window coverage ratio drops below 70%",
        ],
        approvals=resolved_approvals,
        created_at=card_created_at,
    )


def compute_handback_sha256(payload: dict[str, Any]) -> str:
    """Compute deterministic SHA256 digest of handback payload."""
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_model_card_sha256(card_dict: dict[str, Any]) -> str:
    """Compute deterministic SHA256 digest of model card dict excluding integrity envelope."""
    canonical = json.dumps(
        {k: v for k, v in card_dict.items() if k != "integrity"},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_gate2_receipt_sha256(payload: dict[str, Any]) -> str:
    """Compute deterministic SHA256 digest of Gate 2 receipt body excluding integrity hash."""
    canonical = json.dumps(
        {k: v for k, v in payload.items() if k != "integrity"},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_sitescore_gate2_receipt(
    benchmark: SiteScoreOpeningOutcomeBenchmarkResult,
    *,
    inventory_version: str = "candidate-site-view-v2",
    observed_at: str | None = None,
    model_card: ModelCard | dict[str, Any] | None = None,
    model_card_hash: str | None = None,
) -> dict[str, Any]:
    """Build Gate 2 audit receipt payload with integrity envelope and artifact hash bindings."""
    ts = observed_at or benchmark.observed_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    handback_hash = compute_handback_sha256(benchmark.handback_payload)
    if model_card_hash is None:
        if model_card is not None:
            mc_dict = model_card.to_dict() if hasattr(model_card, "to_dict") else model_card
            model_card_hash = compute_model_card_sha256(mc_dict)
        else:
            mc = build_sitescore_opening_outcome_model_card(benchmark, version=inventory_version, created_at=ts)
            model_card_hash = compute_model_card_sha256(mc.to_dict())

    payload: dict[str, Any] = {
        "schema_version": GATE2_RECEIPT_SCHEMA_VERSION,
        "kind": GATE2_RECEIPT_KIND,
        "inventory_version": inventory_version,
        "observed_at": ts,
        "model_name": "sitescore_propensity",
        "service": "sitescore",
        "provenance": benchmark.provenance,
        "source_contract": f"model_ready.candidate_site_view@{inventory_version}",
        "gate": "GATE_2",
        "gate_status": "PASSED" if benchmark.is_gate2_passed else "REJECTED_GOVERNED_DISABLED",
        "is_governed_disabled": not benchmark.is_gate2_passed,
        "benchmark_summary": benchmark.to_dict(),
        "handback": benchmark.handback_payload,
        "artifact_hashes": {
            "handback_hash": handback_hash,
            "model_card_hash": model_card_hash,
        },
    }
    if benchmark.db_error:
        payload["db_error"] = benchmark.db_error
    payload["integrity"] = {
        "content_sha256": compute_gate2_receipt_sha256(payload),
        "handback_hash": handback_hash,
        "model_card_hash": model_card_hash,
    }
    return payload


@dataclass(frozen=True)
class Gate2ReceiptVerificationResult:
    is_valid: bool
    reason_code: str
    errors: Sequence[str] = field(default_factory=tuple)


def verify_sitescore_gate2_receipt(
    receipt: dict[str, Any],
    *,
    model_card_artifact: dict[str, Any] | ModelCard | None = None,
) -> Gate2ReceiptVerificationResult:
    """Fail-closed verifier for Gate 2 receipt content, duplicate drift, and integrity (Fix for B1-B3)."""
    import re
    HEX64_PATTERN = r"^[0-9a-f]{64}$"
    errors: list[str] = []

    if not isinstance(receipt, dict):
        return Gate2ReceiptVerificationResult(False, "INVALID_RECEIPT_TYPE", ("Receipt must be a JSON dictionary",))

    # B1 requirement: model_card_artifact is MANDATORY for receipt verification!
    if model_card_artifact is None:
        errors.append("Missing required model_card_artifact for receipt verification; model card artifact must be provided and substantiated")
        mc_dict: dict[str, Any] | None = None
    else:
        mc_dict = model_card_artifact.to_dict() if hasattr(model_card_artifact, "to_dict") else model_card_artifact
        if not isinstance(mc_dict, dict):
            errors.append("model_card_artifact must be a dictionary or ModelCard instance")
            mc_dict = None

    try:
        # 1. Integrity hash envelope check
        integrity = receipt.get("integrity")
        if not isinstance(integrity, dict) or not integrity.get("content_sha256"):
            errors.append("Missing integrity.content_sha256 envelope")
        else:
            try:
                expected_sha = compute_gate2_receipt_sha256(receipt)
                if integrity["content_sha256"] != expected_sha:
                    errors.append(f"Integrity hash mismatch: declared {integrity['content_sha256']}, recomputed {expected_sha}")
            except Exception as exc:
                errors.append(f"Integrity hash computation failed: {exc}")

        # 2. Schema version and kind validation
        if receipt.get("schema_version") != GATE2_RECEIPT_SCHEMA_VERSION:
            errors.append(f"Invalid schema_version: expected {GATE2_RECEIPT_SCHEMA_VERSION}, got {receipt.get('schema_version')}")
        if receipt.get("kind") != GATE2_RECEIPT_KIND:
            errors.append(f"Invalid receipt kind: expected {GATE2_RECEIPT_KIND}, got {receipt.get('kind')}")

        summary = receipt.get("benchmark_summary")
        handback = receipt.get("handback")

        if not isinstance(summary, dict):
            errors.append("benchmark_summary must be a dictionary")
            return Gate2ReceiptVerificationResult(False, "MALFORMED_RECEIPT_SUMMARY", tuple(errors))
        if not isinstance(handback, dict):
            errors.append("handback must be a dictionary")
            return Gate2ReceiptVerificationResult(False, "MALFORMED_RECEIPT_HANDBACK", tuple(errors))

        handback_in_summary = summary.get("handback_payload")
        if not isinstance(handback_in_summary, dict):
            errors.append("benchmark_summary.handback_payload must be a dictionary")
            return Gate2ReceiptVerificationResult(False, "MALFORMED_RECEIPT_SUMMARY", tuple(errors))

        # Helper type assertions that reject booleans and non-numeric types
        def _check_strict_int(val: Any, name: str) -> int | None:
            if isinstance(val, bool) or not isinstance(val, int):
                errors.append(f"Field {name} must be an integer (got {type(val).__name__}: {val!r})")
                return None
            return val

        def _check_strict_float(val: Any, name: str) -> float | None:
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                errors.append(f"Field {name} must be a real number (got {type(val).__name__}: {val!r})")
                return None
            f = float(val)
            if not math.isfinite(f):
                errors.append(f"Field {name} must be finite (got {f})")
                return None
            return f

        def _check_strict_bool(val: Any, name: str) -> bool | None:
            if not isinstance(val, bool):
                errors.append(f"Field {name} must be a boolean (got {type(val).__name__}: {val!r})")
                return None
            return val

        # Closed key set checks for all dictionaries to reject arbitrary or renamed synthetic metric fields (B3)
        REQUIRED_RECEIPT_KEYS = {
            "schema_version", "kind", "inventory_version", "observed_at",
            "model_name", "service", "provenance", "source_contract",
            "gate", "gate_status", "is_governed_disabled", "benchmark_summary",
            "handback", "artifact_hashes", "integrity",
        }
        ALLOWED_RECEIPT_KEYS = REQUIRED_RECEIPT_KEYS | {"db_error"}
        for k in receipt.keys():
            if k not in ALLOWED_RECEIPT_KEYS:
                errors.append(f"Forbidden or unknown field in top-level receipt: {k!r}")
        for k in REQUIRED_RECEIPT_KEYS:
            if k not in receipt:
                errors.append(f"Missing required field in top-level receipt: {k!r}")

        if mc_dict is not None:
            REQUIRED_MODEL_CARD_KEYS = {
                "model_name", "model_version", "owner", "risk_level", "intended_use",
                "not_intended_use", "dataset_snapshot_id", "validation_run_id",
                "feature_set_id", "label_set_id", "training_period", "validation_period",
                "algorithm", "baseline", "metrics_summary", "segment_metrics",
                "calibration_summary", "explainability_method", "limitations",
                "known_biases", "privacy_review", "security_review", "release_status",
                "rollback_conditions", "approvals", "created_at",
            }
            ALLOWED_MODEL_CARD_KEYS = REQUIRED_MODEL_CARD_KEYS
            for k in mc_dict.keys():
                if k not in ALLOWED_MODEL_CARD_KEYS:
                    errors.append(f"Forbidden or unknown field in top-level model_card: {k!r}")
            for k in REQUIRED_MODEL_CARD_KEYS:
                if k not in mc_dict:
                    errors.append(f"Missing required field in top-level model_card: {k!r}")

        REQUIRED_HANDBACK_KEYS = {
            "handback_required", "reason_code", "governed_disabled", "provenance",
            "observed_count", "eligible_count", "mature_label_count",
            "matched_prediction_count", "m6_mature_count", "m12_mature_count",
            "interval_bounds_count", "in_p80_count", "matched_mean_y",
            "realized_revenue_sum", "mean_realized_revenue",
            "activation_threshold", "missing_labels_delta", "m6_coverage_ratio",
            "m12_coverage_ratio", "prediction_coverage_ratio",
            "interval_bounds_coverage_ratio", "normalized_mae", "p80_coverage",
            "reasons", "handback_action", "outcome_backfill_contract",
            "prediction_source_contract", "backfill_task_id", "prediction_source_task_id",
            "backfill_receipt_required",
        }
        ALLOWED_HANDBACK_KEYS = REQUIRED_HANDBACK_KEYS | {"unmatched_mean_y", "calibration_summary", "segment_metrics", "message", "backfill_owner", "discovery_inventory_query"}
        for k in handback.keys():
            if k not in ALLOWED_HANDBACK_KEYS:
                errors.append(f"Forbidden or unknown field in handback: {k!r}")
        for k in REQUIRED_HANDBACK_KEYS:
            if k not in handback:
                errors.append(f"Missing required field in handback: {k!r}")
        for k in handback_in_summary.keys():
            if k not in ALLOWED_HANDBACK_KEYS:
                errors.append(f"Forbidden or unknown field in benchmark_summary.handback_payload: {k!r}")
        for k in REQUIRED_HANDBACK_KEYS:
            if k not in handback_in_summary:
                errors.append(f"Missing required field in benchmark_summary.handback_payload: {k!r}")

        bc = handback.get("outcome_backfill_contract")
        if isinstance(bc, dict):
            REQUIRED_OUTCOME_BACKFILL_CONTRACT_KEYS = {
                "owner", "task_id", "scope", "discovery_source_identity", "discovery_query_id",
                "required_source_identity", "required_query_id", "required_evidence_owner",
                "source_identity", "query_id", "dataset_snapshot_hash", "lineage_id",
                "freshness_timestamp", "evidence_owner", "eligibility_definition",
                "maturity_definition", "m6_maturity_definition", "m12_maturity_definition",
                "observed_count", "eligible_count", "mature_count", "m6_mature_count",
                "m12_mature_count", "matched_prediction_count", "interval_bounds_count",
                "in_p80_count", "required_fields", "discovery_inventory_query", "note",
                "receipt_required",
            }
            ALLOWED_OUTCOME_BACKFILL_CONTRACT_KEYS = REQUIRED_OUTCOME_BACKFILL_CONTRACT_KEYS
            for k in bc.keys():
                if k not in ALLOWED_OUTCOME_BACKFILL_CONTRACT_KEYS:
                    errors.append(f"Forbidden or unknown field in outcome_backfill_contract: {k!r}")
            for k in REQUIRED_OUTCOME_BACKFILL_CONTRACT_KEYS:
                if k not in bc:
                    errors.append(f"Missing required field in outcome_backfill_contract: {k!r}")

        psc = handback.get("prediction_source_contract")
        if isinstance(psc, dict):
            REQUIRED_PREDICTION_SOURCE_CONTRACT_KEYS = {
                "owner", "task_id", "scope", "required_fields", "receipt_required",
            }
            ALLOWED_PREDICTION_SOURCE_CONTRACT_KEYS = REQUIRED_PREDICTION_SOURCE_CONTRACT_KEYS
            for k in psc.keys():
                if k not in ALLOWED_PREDICTION_SOURCE_CONTRACT_KEYS:
                    errors.append(f"Forbidden or unknown field in prediction_source_contract: {k!r}")
            for k in REQUIRED_PREDICTION_SOURCE_CONTRACT_KEYS:
                if k not in psc:
                    errors.append(f"Missing required field in prediction_source_contract: {k!r}")

        # B1 & B3: Closed allow-list check for benchmark_summary
        REQUIRED_BENCHMARK_SUMMARY_KEYS = {
            "provenance", "dataset_snapshot_id", "model_version", "artifact_lineage_id",
            "observed_count", "eligible_count", "mature_label_count", "m6_mature_count",
            "m12_mature_count", "matched_prediction_count", "interval_bounds_count",
            "in_p80_count", "matched_mean_y", "realized_revenue_sum", "mean_realized_revenue",
            "prediction_coverage_ratio",
            "interval_bounds_coverage_ratio", "m6_coverage_ratio", "m12_coverage_ratio",
            "normalized_mae", "p80_coverage", "activation_threshold",
            "min_coverage_threshold", "max_mae_threshold", "is_gate2_passed",
            "status", "reason_code", "handback_payload", "calibration_summary",
            "segment_metrics", "observed_at",
        }
        ALLOWED_BENCHMARK_SUMMARY_KEYS = REQUIRED_BENCHMARK_SUMMARY_KEYS | {"unmatched_mean_y", "db_error"}
        for k in summary.keys():
            if k not in ALLOWED_BENCHMARK_SUMMARY_KEYS:
                errors.append(f"Forbidden or unknown metric field in benchmark_summary: {k!r}")
        for k in REQUIRED_BENCHMARK_SUMMARY_KEYS:
            if k not in summary:
                errors.append(f"Missing required field in benchmark_summary: {k!r}")

        # Top-level boolean & enum checks
        rec_gov_disabled = _check_strict_bool(receipt.get("is_governed_disabled"), "is_governed_disabled")
        rec_gate_status = receipt.get("gate_status")
        rec_prov = receipt.get("provenance")
        if rec_gate_status not in {"PASSED", "REJECTED_GOVERNED_DISABLED"}:
            errors.append(f"Invalid top-level gate_status: {rec_gate_status!r}")

        # B1: Check pinned identity (gate, model_name, service)
        rec_gate = receipt.get("gate")
        if rec_gate != "GATE_2":
            errors.append(f"Forbidden or unauthenticated gate: {rec_gate!r} (expected 'GATE_2')")

        rec_model_name = receipt.get("model_name")
        if rec_model_name != "sitescore_propensity":
            errors.append(f"Forbidden or unauthenticated model_name: {rec_model_name!r} (expected 'sitescore_propensity')")

        rec_service = receipt.get("service")
        if rec_service != "sitescore":
            errors.append(f"Forbidden or unauthenticated service: {rec_service!r} (expected 'sitescore')")

        if mc_dict is not None:
            mc_model_name = mc_dict.get("model_name")
            if mc_model_name != "sitescore_propensity":
                errors.append(f"model_card.model_name ({mc_model_name!r}) drifts from governed model identity ('sitescore_propensity')")

        # B1 & B2: Check pinned inventory_version, source_contract, and timestamp freshness/reconciliation
        rec_inv_ver = receipt.get("inventory_version")
        if rec_inv_ver != CANONICAL_INVENTORY_VERSION:
            errors.append(f"Forbidden or unauthenticated inventory_version: {rec_inv_ver!r} (expected {CANONICAL_INVENTORY_VERSION!r})")

        rec_src_contract = receipt.get("source_contract")
        if rec_src_contract != CANONICAL_SOURCE_CONTRACT:
            errors.append(f"source_contract mismatch: declared {rec_src_contract!r}, expected {CANONICAL_SOURCE_CONTRACT!r}")

        MAX_EVIDENCE_AGE_DAYS = 30
        now_utc = datetime.now(UTC)
        rec_obs_at = receipt.get("observed_at")
        obs_dt: datetime | None = None
        if not isinstance(rec_obs_at, str):
            errors.append(f"Top-level observed_at must be a string (got {type(rec_obs_at).__name__}: {rec_obs_at!r})")
        else:
            try:
                obs_dt = datetime.fromisoformat(rec_obs_at.replace("Z", "+00:00"))
                if obs_dt.tzinfo is None:
                    errors.append(f"observed_at timestamp must be timezone-aware (got {rec_obs_at!r})")
                elif obs_dt > now_utc + timedelta(seconds=300):
                    errors.append(f"observed_at timestamp is in the future: {rec_obs_at!r}")
                elif obs_dt < now_utc - timedelta(days=MAX_EVIDENCE_AGE_DAYS):
                    errors.append(f"observed_at timestamp is older than maximum evidence age ({MAX_EVIDENCE_AGE_DAYS} days): {rec_obs_at!r}")
            except Exception:
                errors.append(f"Invalid observed_at timestamp format: {rec_obs_at!r}")

        # B1: Validate benchmark_summary.observed_at requirement, freshness, and reconciliation
        sum_obs_at = summary.get("observed_at")
        if not isinstance(sum_obs_at, str):
            errors.append(f"benchmark_summary.observed_at must be a string (got {type(sum_obs_at).__name__}: {sum_obs_at!r})")
        else:
            try:
                sum_dt = datetime.fromisoformat(sum_obs_at.replace("Z", "+00:00"))
                if sum_dt.tzinfo is None:
                    errors.append(f"benchmark_summary.observed_at timestamp must be timezone-aware (got {sum_obs_at!r})")
                elif sum_dt > now_utc + timedelta(seconds=300):
                    errors.append(f"benchmark_summary.observed_at timestamp is in the future: {sum_obs_at!r}")
                elif sum_dt < now_utc - timedelta(days=MAX_EVIDENCE_AGE_DAYS):
                    errors.append(f"benchmark_summary.observed_at timestamp is older than maximum evidence age ({MAX_EVIDENCE_AGE_DAYS} days): {sum_obs_at!r}")
            except Exception:
                errors.append(f"Invalid benchmark_summary.observed_at timestamp format: {sum_obs_at!r}")

            if obs_dt is not None:
                if sum_obs_at != rec_obs_at:
                    errors.append(f"benchmark_summary.observed_at ({sum_obs_at!r}) drifts from top-level observed_at ({rec_obs_at!r})")

        if mc_dict is not None:
            mc_created_str = mc_dict.get("created_at")
            mc_dt: datetime | None = None
            if not isinstance(mc_created_str, str):
                errors.append(f"model_card.created_at must be a string (got {type(mc_created_str).__name__}: {mc_created_str!r})")
            else:
                try:
                    mc_dt = datetime.fromisoformat(mc_created_str.replace("Z", "+00:00"))
                    if mc_dt.tzinfo is None:
                        errors.append(f"model_card.created_at timestamp must be timezone-aware (got {mc_created_str!r})")
                    elif mc_dt > now_utc + timedelta(seconds=300):
                        errors.append(f"model_card.created_at timestamp is in the future: {mc_created_str!r}")
                    elif mc_dt < now_utc - timedelta(days=MAX_EVIDENCE_AGE_DAYS):
                        errors.append(f"model_card.created_at timestamp is older than maximum evidence age ({MAX_EVIDENCE_AGE_DAYS} days): {mc_created_str!r}")
                except Exception:
                    errors.append(f"Invalid model_card.created_at timestamp format: {mc_created_str!r}")

            if obs_dt is not None and mc_dt is not None:
                if abs((obs_dt - mc_dt).total_seconds()) > 300:
                    errors.append(f"observed_at ({rec_obs_at}) drifts from model_card.created_at ({mc_created_str})")

        # Validate numeric types in summary
        obs = _check_strict_int(summary.get("observed_count"), "benchmark_summary.observed_count")
        elg = _check_strict_int(summary.get("eligible_count"), "benchmark_summary.eligible_count")
        mat = _check_strict_int(summary.get("mature_label_count"), "benchmark_summary.mature_label_count")
        match_cnt = _check_strict_int(summary.get("matched_prediction_count"), "benchmark_summary.matched_prediction_count")
        m6_mat = _check_strict_int(summary.get("m6_mature_count"), "benchmark_summary.m6_mature_count")
        m12_mat = _check_strict_int(summary.get("m12_mature_count"), "benchmark_summary.m12_mature_count")
        bounds_cnt = _check_strict_int(summary.get("interval_bounds_count"), "benchmark_summary.interval_bounds_count")
        p80_cnt = _check_strict_int(summary.get("in_p80_count"), "benchmark_summary.in_p80_count")

        m6_cov = _check_strict_float(summary.get("m6_coverage_ratio"), "benchmark_summary.m6_coverage_ratio")
        m12_cov = _check_strict_float(summary.get("m12_coverage_ratio"), "benchmark_summary.m12_coverage_ratio")
        pred_cov = _check_strict_float(summary.get("prediction_coverage_ratio"), "benchmark_summary.prediction_coverage_ratio")
        bounds_cov = _check_strict_float(summary.get("interval_bounds_coverage_ratio"), "benchmark_summary.interval_bounds_coverage_ratio")
        p80_cov = _check_strict_float(summary.get("p80_coverage"), "benchmark_summary.p80_coverage")
        norm_mae = _check_strict_float(summary.get("normalized_mae"), "benchmark_summary.normalized_mae")

        # B3: Strict type checking and drift validation for matched_mean_y, unmatched_mean_y, realized_revenue_sum, and mean_realized_revenue
        sum_matched_mean_y = _check_strict_float(summary.get("matched_mean_y"), "benchmark_summary.matched_mean_y")
        hb_matched_mean_y = _check_strict_float(handback.get("matched_mean_y"), "handback.matched_mean_y")
        if hb_matched_mean_y is not None and sum_matched_mean_y is not None:
            if hb_matched_mean_y != round(sum_matched_mean_y, 2):
                errors.append(f"handback.matched_mean_y ({hb_matched_mean_y}) drifts from summary.matched_mean_y ({round(sum_matched_mean_y, 2)})")
        if match_cnt == 0 and sum_matched_mean_y is not None and sum_matched_mean_y != 0.0:
            errors.append(f"matched_mean_y must be 0.0 when matched_prediction_count is 0 (got {sum_matched_mean_y})")

        sum_unmatched_raw = summary.get("unmatched_mean_y")
        sum_unmatched_mean_y = _check_strict_float(sum_unmatched_raw, "benchmark_summary.unmatched_mean_y") if sum_unmatched_raw is not None else None
        hb_unmatched_raw = handback.get("unmatched_mean_y")
        hb_unmatched_mean_y = _check_strict_float(hb_unmatched_raw, "handback.unmatched_mean_y") if hb_unmatched_raw is not None else None
        if hb_unmatched_mean_y is not None and sum_unmatched_mean_y is not None:
            if hb_unmatched_mean_y != round(sum_unmatched_mean_y, 2):
                errors.append(f"handback.unmatched_mean_y ({hb_unmatched_mean_y}) drifts from summary.unmatched_mean_y ({round(sum_unmatched_mean_y, 2)})")
        unmatched_cnt = (mat - match_cnt) if (mat is not None and match_cnt is not None) else 0
        if unmatched_cnt == 0 and sum_unmatched_mean_y is not None and sum_unmatched_mean_y != 0.0:
            errors.append(f"unmatched_mean_y must be 0.0 when unmatched record count is 0 (got {sum_unmatched_mean_y})")

        sum_rev_sum = _check_strict_float(summary.get("realized_revenue_sum"), "benchmark_summary.realized_revenue_sum")
        sum_mean_rev = _check_strict_float(summary.get("mean_realized_revenue"), "benchmark_summary.mean_realized_revenue")
        hb_rev_sum = _check_strict_float(handback.get("realized_revenue_sum"), "handback.realized_revenue_sum")
        hb_mean_rev = _check_strict_float(handback.get("mean_realized_revenue"), "handback.mean_realized_revenue")

        if hb_rev_sum is not None and sum_rev_sum is not None and hb_rev_sum != sum_rev_sum:
            errors.append(f"handback.realized_revenue_sum ({hb_rev_sum}) drifts from summary.realized_revenue_sum ({sum_rev_sum})")
        if hb_mean_rev is not None and sum_mean_rev is not None and hb_mean_rev != sum_mean_rev:
            errors.append(f"handback.mean_realized_revenue ({hb_mean_rev}) drifts from summary.mean_realized_revenue ({sum_mean_rev})")

        if mat == 0:
            if sum_rev_sum is not None and sum_rev_sum != 0.0:
                errors.append(f"benchmark_summary.realized_revenue_sum must be 0.0 when mature_label_count is 0 (got {sum_rev_sum})")
            if sum_mean_rev is not None and sum_mean_rev != 0.0:
                errors.append(f"benchmark_summary.mean_realized_revenue must be 0.0 when mature_label_count is 0 (got {sum_mean_rev})")
        elif mat is not None and mat > 0:
            if sum_rev_sum is not None and sum_mean_rev is not None:
                exp_mean_rev = round(sum_rev_sum / mat, 2)
                if sum_mean_rev != exp_mean_rev:
                    errors.append(f"benchmark_summary.mean_realized_revenue ({sum_mean_rev}) drifts from expected mean ({exp_mean_rev}) derived from realized_revenue_sum ({sum_rev_sum}) and mature_label_count ({mat})")
            if sum_matched_mean_y is not None and sum_unmatched_mean_y is not None and match_cnt is not None and sum_rev_sum is not None:
                exp_rev_sum = round(sum_matched_mean_y * match_cnt + sum_unmatched_mean_y * (mat - match_cnt), 2)
                if abs(sum_rev_sum - exp_rev_sum) > 0.05:
                    errors.append(f"benchmark_summary.realized_revenue_sum ({sum_rev_sum}) drifts from expected sum ({exp_rev_sum}) derived from matched and unmatched population means")
            if match_cnt == mat and sum_matched_mean_y is not None and sum_mean_rev is not None:
                if sum_mean_rev != round(sum_matched_mean_y, 2):
                    errors.append(f"benchmark_summary.mean_realized_revenue ({sum_mean_rev}) drifts from matched_mean_realized_revenue ({round(sum_matched_mean_y, 2)}) when all mature records are matched")

        # B1: Check model card digest and governed-disabled semantics if model_card_artifact is present
        art_hashes = receipt.get("artifact_hashes")
        if mc_dict is not None and isinstance(art_hashes, dict):
            recomputed_mc_hash = compute_model_card_sha256(mc_dict)
            declared_mc_hash = art_hashes.get("model_card_hash")
            if declared_mc_hash != recomputed_mc_hash:
                errors.append(f"Model card artifact hash mismatch: declared {declared_mc_hash}, recomputed {recomputed_mc_hash}")

        # Check model card governed-disabled semantics
        is_rec_disabled = (rec_gov_disabled is True) or (rec_gate_status != "PASSED")
        if is_rec_disabled:
            sum_snap = summary.get("dataset_snapshot_id")
            if sum_snap not in (None, "UNAVAILABLE"):
                errors.append(f"Governed-disabled receipt requires summary.dataset_snapshot_id to be None or 'UNAVAILABLE' (got {sum_snap!r})")
            sum_ver = summary.get("model_version")
            if sum_ver not in (None, "UNVERIFIED"):
                errors.append(f"Governed-disabled receipt requires summary.model_version to be None or 'UNVERIFIED' (got {sum_ver!r})")
            sum_lin = summary.get("artifact_lineage_id")
            if sum_lin not in (None, "UNVERIFIED"):
                errors.append(f"Governed-disabled receipt requires summary.artifact_lineage_id to be None or 'UNVERIFIED' (got {sum_lin!r})")

        if mc_dict is not None and is_rec_disabled:
            mc_rel = mc_dict.get("release_status")
            if mc_rel != "GOVERNED_DISABLED":
                errors.append(f"Governed-disabled receipt requires model_card_artifact release_status to be 'GOVERNED_DISABLED' (got {mc_rel!r})")
            if mc_dict.get("validation_run_id") != "UNVERIFIED":
                errors.append(f"Governed-disabled model card validation_run_id must be 'UNVERIFIED' (got {mc_dict.get('validation_run_id')!r})")
            if mc_dict.get("dataset_snapshot_id") != "UNAVAILABLE":
                errors.append(f"Governed-disabled model card dataset_snapshot_id must be 'UNAVAILABLE' (got {mc_dict.get('dataset_snapshot_id')!r})")
            if mc_dict.get("model_version") != "UNVERIFIED":
                errors.append(f"Governed-disabled model card model_version must be 'UNVERIFIED' (got {mc_dict.get('model_version')!r})")
            if mc_dict.get("privacy_review") != "UNVERIFIED":
                errors.append(f"Governed-disabled model card privacy_review must be 'UNVERIFIED' (got {mc_dict.get('privacy_review')!r})")
            if mc_dict.get("security_review") != "UNVERIFIED":
                errors.append(f"Governed-disabled model card security_review must be 'UNVERIFIED' (got {mc_dict.get('security_review')!r})")
            mc_apps = mc_dict.get("approvals")
            if mc_apps and len(mc_apps) > 0:
                errors.append(f"Governed-disabled model card cannot contain approval records (got {len(mc_apps)} approvals)")
            if mc_dict.get("feature_set_id") != "UNVERIFIED":
                errors.append(f"Governed-disabled model card feature_set_id must be 'UNVERIFIED' (got {mc_dict.get('feature_set_id')!r})")
            if mc_dict.get("label_set_id") != "UNVERIFIED":
                errors.append(f"Governed-disabled model card label_set_id must be 'UNVERIFIED' (got {mc_dict.get('label_set_id')!r})")
            if mc_dict.get("training_period") != "UNAVAILABLE":
                errors.append(f"Governed-disabled model card training_period must be 'UNAVAILABLE' (got {mc_dict.get('training_period')!r})")
            if mc_dict.get("validation_period") != "UNAVAILABLE":
                errors.append(f"Governed-disabled model card validation_period must be 'UNAVAILABLE' (got {mc_dict.get('validation_period')!r})")
            if mc_dict.get("algorithm") != "UNAVAILABLE":
                errors.append(f"Governed-disabled model card algorithm must be 'UNAVAILABLE' (got {mc_dict.get('algorithm')!r})")
            if mc_dict.get("baseline") != "UNAVAILABLE":
                errors.append(f"Governed-disabled model card baseline must be 'UNAVAILABLE' (got {mc_dict.get('baseline')!r})")
            if mc_dict.get("explainability_method") != "UNAVAILABLE":
                errors.append(f"Governed-disabled model card explainability_method must be 'UNAVAILABLE' (got {mc_dict.get('explainability_method')!r})")

        # B1: Reconcile model card metrics, calibration, and segment metrics against benchmark summary
        REQUIRED_METRIC_KEYS = [
            "mature_label_count", "matched_prediction_count", "m6_coverage_ratio",
            "m12_coverage_ratio", "prediction_coverage_ratio", "interval_bounds_coverage_ratio",
            "normalized_mae", "p80_coverage"
        ]
        if mc_dict is not None:
            mc_metrics = mc_dict.get("metrics_summary")
            if not isinstance(mc_metrics, dict):
                errors.append("model_card.metrics_summary must be a dictionary")
            else:
                for k in mc_metrics.keys():
                    if k not in set(REQUIRED_METRIC_KEYS):
                        errors.append(f"Forbidden or unknown metric field in model_card.metrics_summary: {k!r}")
                for k in REQUIRED_METRIC_KEYS:
                    if k not in mc_metrics:
                        errors.append(f"model_card.metrics_summary missing required metric key: {k!r}")
                    elif k in summary:
                        mc_val = _check_strict_float(mc_metrics[k], f"model_card.metrics_summary.{k}")
                        sum_val = _check_strict_float(summary[k], f"benchmark_summary.{k}")
                        if mc_val is not None and sum_val is not None and mc_val != sum_val:
                            errors.append(f"model_card.metrics_summary.{k} ({mc_val}) drifts from summary.{k} ({sum_val})")

            mc_cal = mc_dict.get("calibration_summary")
            if not isinstance(mc_cal, dict):
                errors.append("model_card.calibration_summary must be a dictionary")
            elif mc_cal != summary.get("calibration_summary"):
                errors.append("model_card.calibration_summary drifts from summary.calibration_summary")

            mc_seg = mc_dict.get("segment_metrics")
            if not isinstance(mc_seg, (list, tuple)):
                errors.append("model_card.segment_metrics must be a sequence")
            elif mc_seg != summary.get("segment_metrics"):
                errors.append("model_card.segment_metrics drifts from summary.segment_metrics")


        # Range checks for ratios and MAE
        for r_val, r_name in [
            (m6_cov, "benchmark_summary.m6_coverage_ratio"),
            (m12_cov, "benchmark_summary.m12_coverage_ratio"),
            (pred_cov, "benchmark_summary.prediction_coverage_ratio"),
            (bounds_cov, "benchmark_summary.interval_bounds_coverage_ratio"),
            (p80_cov, "benchmark_summary.p80_coverage"),
        ]:
            if r_val is not None and not (0.0 <= r_val <= 1.0):
                errors.append(f"Ratio {r_name} must be in range [0.0, 1.0] (got {r_val})")

        if norm_mae is not None and norm_mae < 0.0:
            errors.append(f"benchmark_summary.normalized_mae cannot be negative (got {norm_mae})")

        # Finiteness scan across all dictionary fields
        def _scan_finiteness(d: Any, path: str) -> None:
            if isinstance(d, dict):
                for k, v in d.items():
                    _scan_finiteness(v, f"{path}.{k}")
            elif isinstance(d, list):
                for idx, elem in enumerate(d):
                    _scan_finiteness(elem, f"{path}[{idx}]")
            elif isinstance(d, float):
                if not math.isfinite(d):
                    errors.append(f"Non-finite float value at {path}: {d}")

        _scan_finiteness(summary, "benchmark_summary")
        _scan_finiteness(handback, "handback")

        # Non-negative checks for all 8 count fields
        if obs is not None and obs < 0:
            errors.append("benchmark_summary.observed_count cannot be negative")
        if elg is not None and elg < 0:
            errors.append("benchmark_summary.eligible_count cannot be negative")
        if mat is not None and mat < 0:
            errors.append("benchmark_summary.mature_label_count cannot be negative")
        if match_cnt is not None and match_cnt < 0:
            errors.append("benchmark_summary.matched_prediction_count cannot be negative")
        if m6_mat is not None and m6_mat < 0:
            errors.append("benchmark_summary.m6_mature_count cannot be negative")
        if m12_mat is not None and m12_mat < 0:
            errors.append("benchmark_summary.m12_mature_count cannot be negative")
        if bounds_cnt is not None and bounds_cnt < 0:
            errors.append("benchmark_summary.interval_bounds_count cannot be negative")
        if p80_cnt is not None and p80_cnt < 0:
            errors.append("benchmark_summary.in_p80_count cannot be negative")

        # Natural subset hierarchy checks
        if obs is not None and elg is not None and elg > obs:
            errors.append(f"Eligible count ({elg}) cannot exceed observed count ({obs})")
        if elg is not None and mat is not None and mat > elg:
            errors.append(f"Mature label count ({mat}) cannot exceed eligible count ({elg})")
        if mat is not None and match_cnt is not None and match_cnt > mat:
            errors.append(f"Matched prediction count ({match_cnt}) cannot exceed mature label count ({mat})")
        if mat is not None and m6_mat is not None and m6_mat > mat:
            errors.append(f"M6 mature count ({m6_mat}) cannot exceed mature label count ({mat})")
        if mat is not None and m12_mat is not None and m12_mat > mat:
            errors.append(f"M12 mature count ({m12_mat}) cannot exceed mature label count ({mat})")
        if match_cnt is not None and bounds_cnt is not None and bounds_cnt > match_cnt:
            errors.append(f"Interval bounds count ({bounds_cnt}) cannot exceed matched prediction count ({match_cnt})")
        if mat is not None and bounds_cnt is not None and bounds_cnt > mat:
            errors.append(f"Interval bounds count ({bounds_cnt}) cannot exceed mature label count ({mat})")
        if bounds_cnt is not None and p80_cnt is not None and p80_cnt > bounds_cnt:
            errors.append(f"In P80 count ({p80_cnt}) cannot exceed interval bounds count ({bounds_cnt})")
        if mat is not None and p80_cnt is not None and p80_cnt > mat:
            errors.append(f"In P80 count ({p80_cnt}) cannot exceed mature label count ({mat})")

        # Re-derive ratios from numerators and denominator
        if mat is not None and mat > 0:
            if m6_mat is not None and m6_cov is not None and m6_cov != round(m6_mat / mat, 4):
                errors.append(f"m6_coverage_ratio ({m6_cov}) drifts from re-derived ({round(m6_mat / mat, 4)})")
            if m12_mat is not None and m12_cov is not None and m12_cov != round(m12_mat / mat, 4):
                errors.append(f"m12_coverage_ratio ({m12_cov}) drifts from re-derived ({round(m12_mat / mat, 4)})")
            if match_cnt is not None and pred_cov is not None and pred_cov != round(match_cnt / mat, 4):
                errors.append(f"prediction_coverage_ratio ({pred_cov}) drifts from re-derived ({round(match_cnt / mat, 4)})")
            if bounds_cnt is not None and bounds_cov is not None and bounds_cov != round(bounds_cnt / mat, 4):
                errors.append(f"interval_bounds_coverage_ratio ({bounds_cov}) drifts from re-derived ({round(bounds_cnt / mat, 4)})")
            if p80_cnt is not None and p80_cov is not None and p80_cov != round(p80_cnt / mat, 4):
                errors.append(f"p80_coverage ({p80_cov}) drifts from re-derived ({round(p80_cnt / mat, 4)})")
        elif mat == 0:
            for r_val, r_name in [(m6_cov, "m6_coverage_ratio"), (m12_cov, "m12_coverage_ratio"), (pred_cov, "prediction_coverage_ratio"), (bounds_cov, "interval_bounds_coverage_ratio"), (p80_cov, "p80_coverage")]:
                if r_val is not None and r_val != 0.0:
                    errors.append(f"Ratio {r_name} must be 0.0 when mature_label_count is 0 (got {r_val})")

        # Cross-validation of duplicated fields in top-level handback and benchmark_summary.handback_payload
        hb_obs = _check_strict_int(handback.get("observed_count"), "handback.observed_count")
        hb_elg = _check_strict_int(handback.get("eligible_count"), "handback.eligible_count")
        hb_mat = _check_strict_int(handback.get("mature_label_count"), "handback.mature_label_count")
        hb_match = _check_strict_int(handback.get("matched_prediction_count"), "handback.matched_prediction_count")
        hb_m6_mat = _check_strict_int(handback.get("m6_mature_count"), "handback.m6_mature_count")
        hb_m12_mat = _check_strict_int(handback.get("m12_mature_count"), "handback.m12_mature_count")
        hb_bounds_cnt = _check_strict_int(handback.get("interval_bounds_count"), "handback.interval_bounds_count")
        hb_p80_cnt = _check_strict_int(handback.get("in_p80_count"), "handback.in_p80_count")

        hb_m6 = _check_strict_float(handback.get("m6_coverage_ratio"), "handback.m6_coverage_ratio")
        hb_m12 = _check_strict_float(handback.get("m12_coverage_ratio"), "handback.m12_coverage_ratio")
        hb_pred = _check_strict_float(handback.get("prediction_coverage_ratio"), "handback.prediction_coverage_ratio")
        hb_bounds = _check_strict_float(handback.get("interval_bounds_coverage_ratio"), "handback.interval_bounds_coverage_ratio")
        hb_p80 = _check_strict_float(handback.get("p80_coverage"), "handback.p80_coverage")
        hb_mae = _check_strict_float(handback.get("normalized_mae"), "handback.normalized_mae")
        hb_gov_disabled = _check_strict_bool(handback.get("governed_disabled"), "handback.governed_disabled")

        if hb_obs != obs:
            errors.append(f"handback.observed_count ({hb_obs}) drifts from summary.observed_count ({obs})")
        if hb_elg != elg:
            errors.append(f"handback.eligible_count ({hb_elg}) drifts from summary.eligible_count ({elg})")
        if hb_mat != mat:
            errors.append(f"handback.mature_label_count ({hb_mat}) drifts from summary.mature_label_count ({mat})")
        if hb_match != match_cnt:
            errors.append(f"handback.matched_prediction_count ({hb_match}) drifts from summary.matched_prediction_count ({match_cnt})")
        if hb_m6_mat != m6_mat:
            errors.append(f"handback.m6_mature_count ({hb_m6_mat}) drifts from summary.m6_mature_count ({m6_mat})")
        if hb_m12_mat != m12_mat:
            errors.append(f"handback.m12_mature_count ({hb_m12_mat}) drifts from summary.m12_mature_count ({m12_mat})")
        if hb_bounds_cnt != bounds_cnt:
            errors.append(f"handback.interval_bounds_count ({hb_bounds_cnt}) drifts from summary.interval_bounds_count ({bounds_cnt})")
        if hb_p80_cnt != p80_cnt:
            errors.append(f"handback.in_p80_count ({hb_p80_cnt}) drifts from summary.in_p80_count ({p80_cnt})")

        if hb_m6 != m6_cov:
            errors.append(f"handback.m6_coverage_ratio ({hb_m6}) drifts from summary.m6_coverage_ratio ({m6_cov})")
        if hb_m12 != m12_cov:
            errors.append(f"handback.m12_coverage_ratio ({hb_m12}) drifts from summary.m12_coverage_ratio ({m12_cov})")
        if hb_pred != pred_cov:
            errors.append(f"handback.prediction_coverage_ratio ({hb_pred}) drifts from summary.prediction_coverage_ratio ({pred_cov})")
        if hb_bounds != bounds_cov:
            errors.append(f"handback.interval_bounds_coverage_ratio ({hb_bounds}) drifts from summary.interval_bounds_coverage_ratio ({bounds_cov})")
        if hb_p80 != p80_cov:
            errors.append(f"handback.p80_coverage ({hb_p80}) drifts from summary.p80_coverage ({p80_cov})")
        if hb_mae != norm_mae:
            errors.append(f"handback.normalized_mae ({hb_mae}) drifts from summary.normalized_mae ({norm_mae})")

        # B2: Complete deep equality check between top-level handback and benchmark_summary.handback_payload
        if handback != handback_in_summary:
            errors.append("benchmark_summary.handback_payload drifts from handback")
            for k in sorted(set(handback.keys()) | set(handback_in_summary.keys())):
                if handback.get(k) != handback_in_summary.get(k):
                    errors.append(f"benchmark_summary.handback_payload.{k} ({handback_in_summary.get(k)}) drifts from handback.{k} ({handback.get(k)})")

        # B2: Validate strict True for all handback/receipt-required booleans in governed-disabled state
        if is_rec_disabled:
            if handback.get("handback_required") is not True:
                errors.append(f"Governed-disabled receipt requires handback.handback_required to be True (got {handback.get('handback_required')!r})")
            if handback.get("governed_disabled") is not True:
                errors.append(f"Governed-disabled receipt requires handback.governed_disabled to be True (got {handback.get('governed_disabled')!r})")
            if handback.get("backfill_receipt_required") is not True:
                errors.append(f"Governed-disabled receipt requires handback.backfill_receipt_required to be True (got {handback.get('backfill_receipt_required')!r})")

        # B4 & B3: Validate missing_labels_delta, reasons, and handback_action semantics
        if mat is not None:
            exp_missing_delta = max(0, ACTIVATION_THRESHOLD - mat)
            hb_missing_delta = _check_strict_int(handback.get("missing_labels_delta"), "handback.missing_labels_delta")
            if hb_missing_delta is not None and hb_missing_delta != exp_missing_delta:
                errors.append(f"handback.missing_labels_delta ({hb_missing_delta}) drifts from re-derived ({exp_missing_delta})")

        hb_reasons = handback.get("reasons")
        if is_rec_disabled:
            if not isinstance(hb_reasons, (list, tuple)) or len(hb_reasons) == 0:
                errors.append("Governed-disabled receipt requires a non-empty handback.reasons list")
            else:
                for r_idx, r_item in enumerate(hb_reasons):
                    if not isinstance(r_item, str) or not r_item.strip():
                        errors.append(f"handback.reasons[{r_idx}] must be a non-empty string")
                    elif "Gate 2 passed" in r_item or "no work is required" in r_item or "evidence is active" in r_item:
                        errors.append(f"handback.reasons[{r_idx}] contains contradictory active status text: {r_item!r}")

        hb_action = handback.get("handback_action")
        if not isinstance(hb_action, str) or not hb_action.strip():
            errors.append("handback.handback_action must be a non-empty string")
        elif is_rec_disabled:
            if "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001" not in hb_action or "ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001" not in hb_action:
                errors.append("handback.handback_action must identify both governed task IDs 'ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001' and 'ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001'")
            if "because no work is required" in hb_action or "Gate 2 passed" in hb_action:
                errors.append(f"handback.handback_action contains contradictory active status text: {hb_action!r}")

        hb_backfill_task = handback.get("backfill_task_id")
        if hb_backfill_task != "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001":
            errors.append(f"handback.backfill_task_id must be 'ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001' (got {hb_backfill_task!r})")

        hb_pred_task = handback.get("prediction_source_task_id")
        if hb_pred_task != "ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001":
            errors.append(f"handback.prediction_source_task_id must be 'ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001' (got {hb_pred_task!r})")

        # Check duplicate counts and fields in outcome_backfill_contract
        bc = handback.get("outcome_backfill_contract")
        if not isinstance(bc, dict):
            errors.append("handback.outcome_backfill_contract must be a dictionary")
        else:
            if is_rec_disabled:
                if bc.get("receipt_required") is not True:
                    errors.append(f"Governed-disabled receipt requires outcome_backfill_contract.receipt_required to be True (got {bc.get('receipt_required')!r})")
                for placeholder_key, expected_placeholder in [
                    ("source_identity", "UNVERIFIED"),
                    ("query_id", "UNVERIFIED"),
                    ("dataset_snapshot_hash", "UNVERIFIED"),
                    ("lineage_id", "UNVERIFIED"),
                    ("freshness_timestamp", "UNVERIFIED"),
                    ("evidence_owner", "UNVERIFIED"),
                    ("required_source_identity", "authoritative_opening_outcome_m6_m12_store_ledger"),
                    ("required_query_id", "sitescore_authoritative_m6_m12_outcome_query_v1"),
                ]:
                    val = bc.get(placeholder_key)
                    if val != expected_placeholder:
                        errors.append(f"Governed-disabled outcome_backfill_contract.{placeholder_key} must be {expected_placeholder!r} (got {val!r})")
            if bc.get("task_id") != "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001":
                errors.append(f"outcome_backfill_contract.task_id must be 'ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001' (got {bc.get('task_id')!r})")
            if bc.get("owner") != "Human/Ops":
                errors.append(f"outcome_backfill_contract.owner must be 'Human/Ops' (got {bc.get('owner')!r})")

            # B2: Pin and validate authoritative outcome backfill contract definitions
            EXPECTED_ELIGIBILITY_DEF = "is_training_eligible IS True or eligible IS True"
            EXPECTED_MATURITY_DEF = "realized_90d_net_revenue IS NOT NULL AND realized_90d_net_revenue >= 0"
            EXPECTED_M6_MATURITY_DEF = "store_age_days >= 180 AND realized_180d_net_revenue IS NOT NULL AND realized_180d_net_revenue >= 0"
            EXPECTED_M12_MATURITY_DEF = "store_age_days >= 365 AND realized_365d_net_revenue IS NOT NULL AND realized_365d_net_revenue >= 0"

            if bc.get("eligibility_definition") != EXPECTED_ELIGIBILITY_DEF:
                errors.append(f"outcome_backfill_contract.eligibility_definition mismatch: got {bc.get('eligibility_definition')!r}, expected {EXPECTED_ELIGIBILITY_DEF!r}")
            if bc.get("maturity_definition") != EXPECTED_MATURITY_DEF:
                errors.append(f"outcome_backfill_contract.maturity_definition mismatch: got {bc.get('maturity_definition')!r}, expected {EXPECTED_MATURITY_DEF!r}")
            if bc.get("m6_maturity_definition") != EXPECTED_M6_MATURITY_DEF:
                errors.append(f"outcome_backfill_contract.m6_maturity_definition mismatch: got {bc.get('m6_maturity_definition')!r}, expected {EXPECTED_M6_MATURITY_DEF!r}")
            if bc.get("m12_maturity_definition") != EXPECTED_M12_MATURITY_DEF:
                errors.append(f"outcome_backfill_contract.m12_maturity_definition mismatch: got {bc.get('m12_maturity_definition')!r}, expected {EXPECTED_M12_MATURITY_DEF!r}")

            req_bc_fields = {
                "authoritative_source_identity", "query_id", "dataset_snapshot_hash",
                "artifact_lineage_id", "evidence_owner", "source_freshness_timestamp",
                "m6_maturity_definition", "m12_maturity_definition", "realized_180d_net_revenue",
                "realized_365d_net_revenue", "observed_count", "eligible_count",
                "m6_mature_count", "m12_mature_count", "matched_prediction_count",
                "interval_bounds_count", "in_p80_count"
            }
            bc_req = bc.get("required_fields")
            if not isinstance(bc_req, (list, tuple)) or not req_bc_fields.issubset(set(bc_req if isinstance(bc_req, (list, tuple)) else [])):
                errors.append(f"outcome_backfill_contract.required_fields must contain all required fields (missing: {req_bc_fields - set(bc_req if isinstance(bc_req, (list, tuple)) else [])})")

            if bc.get("observed_count") != obs:
                errors.append(f"outcome_backfill_contract.observed_count ({bc.get('observed_count')}) drifts from summary.observed_count ({obs})")
            if bc.get("eligible_count") != elg:
                errors.append(f"outcome_backfill_contract.eligible_count ({bc.get('eligible_count')}) drifts from summary.eligible_count ({elg})")
            if bc.get("mature_count") != mat:
                errors.append(f"outcome_backfill_contract.mature_count ({bc.get('mature_count')}) drifts from summary.mature_label_count ({mat})")
            if bc.get("matched_prediction_count") != match_cnt:
                errors.append(f"outcome_backfill_contract.matched_prediction_count ({bc.get('matched_prediction_count')}) drifts from summary.matched_prediction_count ({match_cnt})")
            if bc.get("m6_mature_count") != m6_mat:
                errors.append(f"outcome_backfill_contract.m6_mature_count ({bc.get('m6_mature_count')}) drifts from summary.m6_mature_count ({m6_mat})")
            if bc.get("m12_mature_count") != m12_mat:
                errors.append(f"outcome_backfill_contract.m12_mature_count ({bc.get('m12_mature_count')}) drifts from summary.m12_mature_count ({m12_mat})")
            if bc.get("interval_bounds_count") != bounds_cnt:
                errors.append(f"outcome_backfill_contract.interval_bounds_count ({bc.get('interval_bounds_count')}) drifts from summary.interval_bounds_count ({bounds_cnt})")
            if bc.get("in_p80_count") != p80_cnt:
                errors.append(f"outcome_backfill_contract.in_p80_count ({bc.get('in_p80_count')}) drifts from summary.in_p80_count ({p80_cnt})")

        # Check prediction_source_contract
        psc = handback.get("prediction_source_contract")
        if not isinstance(psc, dict):
            errors.append("handback.prediction_source_contract must be a dictionary")
        else:
            if is_rec_disabled and psc.get("receipt_required") is not True:
                errors.append(f"Governed-disabled receipt requires prediction_source_contract.receipt_required to be True (got {psc.get('receipt_required')!r})")
            if psc.get("task_id") != "ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001":
                errors.append(f"prediction_source_contract.task_id must be 'ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001' (got {psc.get('task_id')!r})")
            if psc.get("owner") != "SiteScore Platform Team":
                errors.append(f"prediction_source_contract.owner must be 'SiteScore Platform Team' (got {psc.get('owner')!r})")
            req_psc_fields = {"predicted_revenue", "p10", "p90", "dataset_snapshot_id", "model_version", "artifact_lineage_id"}
            psc_req = psc.get("required_fields")
            if not isinstance(psc_req, (list, tuple)) or not req_psc_fields.issubset(set(psc_req if isinstance(psc_req, (list, tuple)) else [])):
                errors.append(f"prediction_source_contract.required_fields must contain all required fields (missing: {req_psc_fields - set(psc_req if isinstance(psc_req, (list, tuple)) else [])})")

        # B3: Universal scan for forbidden synthetic horizon calibration fields across all structures
        FORBIDDEN_HORIZON_KEYS = {
            "m1_interval_mae", "m3_interval_mae", "m6_interval_mae", "m12_interval_mae",
            "m1_mae", "m3_mae", "m6_mae", "m12_mae"
        }
        def _scan_forbidden_horizon_keys(obj: Any, path: str) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in FORBIDDEN_HORIZON_KEYS or (isinstance(k, str) and re.search(r"^m\d+_(?:interval_)?mae$", k)):
                        errors.append(f"Forbidden or unsupported synthetic horizon calibration field at {path}.{k}: {k!r}")
                    _scan_forbidden_horizon_keys(v, f"{path}.{k}")
            elif isinstance(obj, (list, tuple)):
                for idx, item in enumerate(obj):
                    _scan_forbidden_horizon_keys(item, f"{path}[{idx}]")

        _scan_forbidden_horizon_keys(receipt, "receipt")
        if mc_dict is not None:
            _scan_forbidden_horizon_keys(mc_dict, "model_card")

        FORBIDDEN_CALIBRATION_KEYS = FORBIDDEN_HORIZON_KEYS
        REQUIRED_CALIBRATION_KEYS = {
            "measured_90d_mae",
            "matched_prediction_count",
            "matched_mean_realized_revenue",
            "prediction_coverage_ratio",
            "interval_bounds_coverage_ratio",
            "p80_coverage_ratio",
            "mean_realized_revenue",
        }
        ALLOWED_CALIBRATION_KEYS = REQUIRED_CALIBRATION_KEYS | {"unmatched_mean_realized_revenue"}
        def _check_calibration_summary(cal_dict: Any, location_name: str) -> None:
            if not isinstance(cal_dict, dict):
                errors.append(f"{location_name} must be a dictionary (got {type(cal_dict).__name__}: {cal_dict!r})")
                return
            if not REQUIRED_CALIBRATION_KEYS.issubset(set(cal_dict.keys())) or not set(cal_dict.keys()).issubset(ALLOWED_CALIBRATION_KEYS):
                errors.append(
                    f"{location_name} keys must match allowed set (missing: {REQUIRED_CALIBRATION_KEYS - set(cal_dict.keys())!r}, extra: {set(cal_dict.keys()) - ALLOWED_CALIBRATION_KEYS!r})"
                )
            for k, v in cal_dict.items():
                if k in FORBIDDEN_CALIBRATION_KEYS:
                    errors.append(f"Forbidden or unsupported synthetic horizon calibration field in {location_name}: {k!r}")
                elif k == "unmatched_mean_realized_revenue":
                    flt = _check_strict_float(v, f"{location_name}.{k}")
                    if flt is not None and sum_unmatched_mean_y is not None and flt != round(sum_unmatched_mean_y, 2):
                        errors.append(f"{location_name}.unmatched_mean_realized_revenue ({flt}) drifts from summary.unmatched_mean_y ({round(sum_unmatched_mean_y, 2)})")
                if k == "measured_90d_mae":
                    if match_cnt == 0:
                        if v is not None:
                            errors.append(f"{location_name}.measured_90d_mae must be None when matched_prediction_count is 0 (got {v})")
                    else:
                        flt = _check_strict_float(v, f"{location_name}.{k}")
                        if flt is not None:
                            if flt < 0.0:
                                errors.append(f"{location_name}.measured_90d_mae cannot be negative (got {flt})")
                            if sum_matched_mean_y is not None and sum_matched_mean_y > 0 and norm_mae is not None:
                                expected_mae = norm_mae * sum_matched_mean_y
                                if abs(flt - expected_mae) > max(0.05, 0.02 * expected_mae):
                                    errors.append(f"{location_name}.measured_90d_mae ({flt}) drifts from expected MAE ({round(expected_mae, 2)}) derived from normalized_mae and matched_mean_y")
                            elif sum_matched_mean_y == 0.0 and flt != 0.0:
                                errors.append(f"{location_name}.measured_90d_mae ({flt}) must be 0.0 when matched_mean_y is 0.0")
                elif k == "matched_prediction_count":
                    cal_cnt = _check_strict_int(v, f"{location_name}.{k}")
                    if cal_cnt is not None and match_cnt is not None and cal_cnt != match_cnt:
                        errors.append(f"{location_name}.matched_prediction_count ({cal_cnt}) drifts from summary.matched_prediction_count ({match_cnt})")
                elif k == "prediction_coverage_ratio":
                    flt = _check_strict_float(v, f"{location_name}.{k}")
                    if flt is not None and not (0.0 <= flt <= 1.0):
                        errors.append(f"{location_name}.{k} ratio must be in range [0.0, 1.0] (got {flt})")
                    if flt is not None and pred_cov is not None and flt != pred_cov:
                        errors.append(f"{location_name}.prediction_coverage_ratio ({flt}) drifts from summary.prediction_coverage_ratio ({pred_cov})")
                elif k == "interval_bounds_coverage_ratio":
                    flt = _check_strict_float(v, f"{location_name}.{k}")
                    if flt is not None and not (0.0 <= flt <= 1.0):
                        errors.append(f"{location_name}.{k} ratio must be in range [0.0, 1.0] (got {flt})")
                    if flt is not None and bounds_cov is not None and flt != bounds_cov:
                        errors.append(f"{location_name}.interval_bounds_coverage_ratio ({flt}) drifts from summary.interval_bounds_coverage_ratio ({bounds_cov})")
                elif k == "p80_coverage_ratio":
                    flt = _check_strict_float(v, f"{location_name}.{k}")
                    if flt is not None and not (0.0 <= flt <= 1.0):
                        errors.append(f"{location_name}.{k} ratio must be in range [0.0, 1.0] (got {flt})")
                    if flt is not None and p80_cov is not None and flt != p80_cov:
                        errors.append(f"{location_name}.p80_coverage_ratio ({flt}) drifts from summary.p80_coverage ({p80_cov})")
                elif k == "matched_mean_realized_revenue":
                    flt = _check_strict_float(v, f"{location_name}.{k}")
                    if flt is not None and sum_matched_mean_y is not None and flt != round(sum_matched_mean_y, 2):
                        errors.append(f"{location_name}.matched_mean_realized_revenue ({flt}) drifts from summary.matched_mean_y ({round(sum_matched_mean_y, 2)})")
                elif k == "mean_realized_revenue":
                    flt = _check_strict_float(v, f"{location_name}.{k}")
                    if flt is not None:
                        if flt < 0.0:
                            errors.append(f"{location_name}.mean_realized_revenue cannot be negative (got {flt})")
                        if mat == 0 and flt != 0.0:
                            errors.append(f"{location_name}.mean_realized_revenue must be 0.0 when mature_label_count is 0 (got {flt})")
                        elif mat is not None and mat > 0 and match_cnt == mat and sum_matched_mean_y is not None:
                            if flt != round(sum_matched_mean_y, 2):
                                errors.append(f"{location_name}.mean_realized_revenue ({flt}) drifts from matched_mean_realized_revenue ({round(sum_matched_mean_y, 2)}) when all mature records are matched")

        _check_calibration_summary(summary.get("calibration_summary"), "summary.calibration_summary")
        if handback.get("calibration_summary") is not None:
            _check_calibration_summary(handback.get("calibration_summary"), "handback.calibration_summary")
        if handback_in_summary.get("calibration_summary") is not None:
            _check_calibration_summary(handback_in_summary.get("calibration_summary"), "handback_payload.calibration_summary")
        if mc_dict is not None:
            _check_calibration_summary(mc_dict.get("calibration_summary"), "model_card.calibration_summary")

        REQUIRED_SEGMENT_KEYS = {"segment_name", "segment_value", "record_count", "metrics"}
        REQUIRED_SEGMENT_METRIC_KEYS = {"mae", "m6_coverage", "m12_coverage", "prediction_coverage"}

        def _validate_segment_metrics(seg_list: Any, location_name: str) -> None:
            if not isinstance(seg_list, (list, tuple)):
                errors.append(f"{location_name} must be a sequence (got {type(seg_list).__name__}: {seg_list!r})")
                return

            if mat is not None and mat > 0 and not seg_list:
                errors.append(f"{location_name} cannot be empty when mature_label_count is {mat} (> 0)")
                return

            partitions: dict[str, list[dict[str, Any]]] = {}
            for idx, seg in enumerate(seg_list):
                if not isinstance(seg, dict):
                    errors.append(f"{location_name}[{idx}] must be a dictionary (got {type(seg).__name__}: {seg!r})")
                    continue
                if set(seg.keys()) != REQUIRED_SEGMENT_KEYS:
                    errors.append(f"{location_name}[{idx}] keys must match required set (got {set(seg.keys())!r})")
                seg_name = seg.get("segment_name")
                if not isinstance(seg_name, str) or not seg_name:
                    errors.append(f"{location_name}[{idx}].segment_name must be a non-empty string")
                    seg_name = "UNKNOWN"
                if not isinstance(seg.get("segment_value"), str) or not seg.get("segment_value"):
                    errors.append(f"{location_name}[{idx}].segment_value must be a non-empty string")
                seg_cnt = _check_strict_int(seg.get("record_count"), f"{location_name}[{idx}].record_count")
                if seg_cnt is not None:
                    if seg_cnt <= 0:
                        errors.append(f"{location_name}[{idx}].record_count ({seg_cnt}) must be positive (> 0)")
                    if mat is not None and seg_cnt > mat:
                        errors.append(f"{location_name}[{idx}].record_count ({seg_cnt}) exceeds mature_label_count ({mat})")
                    if mat == 0 and seg_cnt > 0:
                        errors.append(f"{location_name}[{idx}].record_count ({seg_cnt}) > 0 when mature_label_count is 0")

                metrics = seg.get("metrics")
                if not isinstance(metrics, dict):
                    errors.append(f"{location_name}[{idx}].metrics must be a dictionary (got {type(metrics).__name__}: {metrics!r})")
                    continue
                if set(metrics.keys()) != REQUIRED_SEGMENT_METRIC_KEYS:
                    errors.append(f"{location_name}[{idx}].metrics keys must match required set (got {set(metrics.keys())!r})")
                for mk, mv in metrics.items():
                    if mk == "mae":
                        flt = _check_strict_float(mv, f"{location_name}[{idx}].metrics.{mk}")
                        if flt is not None and flt < 0.0:
                            errors.append(f"{location_name}[{idx}].metrics.{mk} cannot be negative (got {flt})")
                    elif mk in {"m6_coverage", "m12_coverage", "prediction_coverage"}:
                        flt = _check_strict_float(mv, f"{location_name}[{idx}].metrics.{mk}")
                        if flt is not None and not (0.0 <= flt <= 1.0):
                            errors.append(f"{location_name}[{idx}].metrics.{mk} must be in range [0.0, 1.0] (got {flt})")

                partitions.setdefault(seg_name, []).append(seg)

            if mat is not None and mat > 0 and "target_format_code" not in partitions:
                errors.append(f"{location_name} must contain canonical partition 'target_format_code' when mature_label_count > 0")

            for seg_name, segs in partitions.items():
                seen_vals = set()
                tot_seg_cnt = 0
                for seg in segs:
                    v_str = str(seg.get("segment_value"))
                    if v_str in seen_vals:
                        errors.append(f"Duplicate segment_value {v_str!r} in {location_name} for partition {seg_name!r}")
                    seen_vals.add(v_str)
                    tot_seg_cnt += int(seg.get("record_count", 0))

                if mat is not None:
                    if tot_seg_cnt != mat:
                        errors.append(f"{location_name} partition {seg_name!r} total segment record_count ({tot_seg_cnt}) does not match mature_label_count ({mat})")

                if mat is not None and mat > 0:
                    w_m6 = sum(int(s.get("record_count", 0)) * float(s.get("metrics", {}).get("m6_coverage", 0.0)) for s in segs) / mat
                    if m6_cov is not None and abs(round(w_m6, 4) - m6_cov) > 0.001:
                        errors.append(f"{location_name} partition {seg_name!r} weighted m6_coverage ({round(w_m6, 4)}) drifts from summary.m6_coverage_ratio ({m6_cov})")

                    w_m12 = sum(int(s.get("record_count", 0)) * float(s.get("metrics", {}).get("m12_coverage", 0.0)) for s in segs) / mat
                    if m12_cov is not None and abs(round(w_m12, 4) - m12_cov) > 0.001:
                        errors.append(f"{location_name} partition {seg_name!r} weighted m12_coverage ({round(w_m12, 4)}) drifts from summary.m12_coverage_ratio ({m12_cov})")

                    w_pred = sum(int(s.get("record_count", 0)) * float(s.get("metrics", {}).get("prediction_coverage", 0.0)) for s in segs) / mat
                    if pred_cov is not None and abs(round(w_pred, 4) - pred_cov) > 0.001:
                        errors.append(f"{location_name} partition {seg_name!r} weighted prediction_coverage ({round(w_pred, 4)}) drifts from summary.prediction_coverage_ratio ({pred_cov})")

                    seg_matched_counts = [round(int(s.get("record_count", 0)) * float(s.get("metrics", {}).get("prediction_coverage", 0.0))) for s in segs]
                    tot_matched = sum(seg_matched_counts)
                    if tot_matched > 0:
                        w_mae = sum(c * float(s.get("metrics", {}).get("mae", 0.0)) for c, s in zip(seg_matched_counts, segs, strict=True)) / tot_matched
                        exp_main_mae = (norm_mae * sum_matched_mean_y) if (norm_mae is not None and sum_matched_mean_y is not None) else 0.0
                        if abs(round(w_mae, 2) - round(exp_main_mae, 2)) > 0.05:
                            errors.append(f"{location_name} partition {seg_name!r} weighted segment MAE ({round(w_mae, 2)}) drifts from main MAE ({round(exp_main_mae, 2)})")
                    else:
                        for s in segs:
                            if s.get("metrics", {}).get("mae") != 0.0:
                                errors.append(f"{location_name} partition {seg_name!r} segment MAE must be 0.0 when prediction_coverage is 0")

        _validate_segment_metrics(summary.get("segment_metrics"), "summary.segment_metrics")
        if handback.get("segment_metrics") is not None:
            _validate_segment_metrics(handback.get("segment_metrics"), "handback.segment_metrics")
        if handback_in_summary.get("segment_metrics") is not None:
            _validate_segment_metrics(handback_in_summary.get("segment_metrics"), "handback_payload.segment_metrics")
        if mc_dict is not None:
            _validate_segment_metrics(mc_dict.get("segment_metrics"), "model_card.segment_metrics")

        # Check artifact_hashes dictionary & hashes
        ALLOWED_ARTIFACT_HASHES_KEYS = {"handback_hash", "model_card_hash"}
        REQUIRED_ARTIFACT_HASHES_KEYS = ALLOWED_ARTIFACT_HASHES_KEYS

        art_hashes = receipt.get("artifact_hashes")
        if not isinstance(art_hashes, dict):
            errors.append("Missing or invalid artifact_hashes dictionary")
        else:
            for k in art_hashes.keys():
                if k not in ALLOWED_ARTIFACT_HASHES_KEYS:
                    errors.append(f"Forbidden or unknown field in artifact_hashes: {k!r}")
            for k in REQUIRED_ARTIFACT_HASHES_KEYS:
                if k not in art_hashes:
                    errors.append(f"Missing required field in artifact_hashes: {k!r}")

            hb_hash = art_hashes.get("handback_hash")
            mc_hash = art_hashes.get("model_card_hash")
            if not isinstance(hb_hash, str) or not re.fullmatch(HEX64_PATTERN, hb_hash):
                errors.append(f"Invalid artifact_hashes.handback_hash format: {hb_hash!r}")
            if not isinstance(mc_hash, str) or not re.fullmatch(HEX64_PATTERN, mc_hash):
                errors.append(f"Invalid artifact_hashes.model_card_hash format: {mc_hash!r}")

            expected_hb_hash = compute_handback_sha256(handback)
            if hb_hash != expected_hb_hash:
                errors.append(f"Artifact handback hash mismatch: declared {hb_hash}, recomputed {expected_hb_hash}")

        # Check integrity envelope & cross-check with artifact_hashes
        ALLOWED_INTEGRITY_KEYS = {"content_sha256", "handback_hash", "model_card_hash"}
        REQUIRED_INTEGRITY_KEYS = ALLOWED_INTEGRITY_KEYS

        if not isinstance(integrity, dict):
            errors.append("Missing or invalid integrity dictionary")
        else:
            for k in integrity.keys():
                if k not in ALLOWED_INTEGRITY_KEYS:
                    errors.append(f"Forbidden or unknown field in integrity: {k!r}")
            for k in REQUIRED_INTEGRITY_KEYS:
                if k not in integrity:
                    errors.append(f"Missing required field in integrity: {k!r}")

            int_hb_hash = integrity.get("handback_hash")
            int_mc_hash = integrity.get("model_card_hash")

            if not isinstance(int_hb_hash, str) or not re.fullmatch(HEX64_PATTERN, int_hb_hash):
                errors.append(f"Invalid integrity.handback_hash format: {int_hb_hash!r}")
            if not isinstance(int_mc_hash, str) or not re.fullmatch(HEX64_PATTERN, int_mc_hash):
                errors.append(f"Invalid integrity.model_card_hash format: {int_mc_hash!r}")

            if isinstance(art_hashes, dict):
                if int_hb_hash != art_hashes.get("handback_hash"):
                    errors.append(f"Integrity handback_hash drift: integrity {int_hb_hash}, artifact_hashes {art_hashes.get('handback_hash')}")
                if int_mc_hash != art_hashes.get("model_card_hash"):
                    errors.append(f"Integrity model_card_hash drift: integrity {int_mc_hash}, artifact_hashes {art_hashes.get('model_card_hash')}")

        # Check model_card_artifact if passed to verifier
        if model_card_artifact is not None:
            mc_dict = model_card_artifact.to_dict() if hasattr(model_card_artifact, "to_dict") else model_card_artifact
            if not isinstance(mc_dict, dict):
                errors.append("model_card_artifact must be a dictionary or ModelCard instance")
            else:
                recomputed_mc_hash = compute_model_card_sha256(mc_dict)
                declared_mc_hash = art_hashes.get("model_card_hash") if isinstance(art_hashes, dict) else None
                if declared_mc_hash != recomputed_mc_hash:
                    errors.append(f"Model card artifact hash mismatch: declared {declared_mc_hash}, recomputed {recomputed_mc_hash}")

        # Provenance, reason_code, status, and threshold validation & cross-checks
        ALLOWED_PROVENANCES = {"no_source", "unreachable_db", "provided_records", "pg16_query", "authenticated_governed_records"}
        rec_prov = receipt.get("provenance")
        sum_prov = summary.get("provenance")
        hb_prov = handback.get("provenance")
        if rec_prov not in ALLOWED_PROVENANCES:
            errors.append(f"Invalid top-level provenance: {rec_prov!r}")
        if sum_prov not in ALLOWED_PROVENANCES:
            errors.append(f"Invalid summary provenance: {sum_prov!r}")
        if rec_prov != sum_prov:
            errors.append(f"top-level provenance ({rec_prov}) drifts from summary.provenance ({sum_prov})")
        if hb_prov is not None and hb_prov != rec_prov:
            errors.append(f"handback.provenance ({hb_prov}) drifts from top-level provenance ({rec_prov})")

        ALLOWED_REASON_CODES = {
            "DB_INVENTORY_UNREACHABLE",
            "NO_SOURCE_INVENTORY",
            "UNAUTHENTICATED_PROVENANCE",
            "MISSING_GOVERNED_LINEAGE",
            "MATURE_LABELS_BELOW_THRESHOLD",
            "PREDICTION_EVIDENCE_MISSING",
            "M6_M12_COVERAGE_INSUFFICIENT",
            "INTERVAL_BOUNDS_MISSING",
            "NORMALIZED_MAE_EXCEEDED",
            "GATE2_CRITERIA_MET",
            "GOVERNED_DISABLED",
        }
        sum_reason = summary.get("reason_code")
        hb_reason = handback.get("reason_code")
        if sum_reason not in ALLOWED_REASON_CODES:
            errors.append(f"Invalid summary.reason_code: {sum_reason!r}")
        if hb_reason not in ALLOWED_REASON_CODES:
            errors.append(f"Invalid handback.reason_code: {hb_reason!r}")
        if sum_reason != hb_reason:
            errors.append(f"handback.reason_code ({hb_reason}) drifts from summary.reason_code ({sum_reason})")

        ALLOWED_STATUSES = {"ACTIVE", "GOVERNED_DISABLED"}
        sum_status = summary.get("status")
        if sum_status not in ALLOWED_STATUSES:
            errors.append(f"Invalid summary.status: {sum_status!r}")

        act_thresh = _check_strict_int(summary.get("activation_threshold"), "benchmark_summary.activation_threshold")
        if act_thresh is not None and act_thresh != ACTIVATION_THRESHOLD:
            errors.append(f"benchmark_summary.activation_threshold ({act_thresh}) drifts from governed constant ({ACTIVATION_THRESHOLD})")

        hb_act_thresh = _check_strict_int(handback.get("activation_threshold"), "handback.activation_threshold")
        if hb_act_thresh is not None and hb_act_thresh != ACTIVATION_THRESHOLD:
            errors.append(f"handback.activation_threshold ({hb_act_thresh}) drifts from governed constant ({ACTIVATION_THRESHOLD})")

        min_cov_thresh = _check_strict_float(summary.get("min_coverage_threshold"), "benchmark_summary.min_coverage_threshold")
        if min_cov_thresh is not None and min_cov_thresh != MIN_COVERAGE_THRESHOLD:
            errors.append(f"benchmark_summary.min_coverage_threshold ({min_cov_thresh}) drifts from governed constant ({MIN_COVERAGE_THRESHOLD})")

        max_mae_thresh = _check_strict_float(summary.get("max_mae_threshold"), "benchmark_summary.max_mae_threshold")
        if max_mae_thresh is not None and max_mae_thresh != MAX_MAE_THRESHOLD:
            errors.append(f"benchmark_summary.max_mae_threshold ({max_mae_thresh}) drifts from governed constant ({MAX_MAE_THRESHOLD})")

        act_thresh_val = ACTIVATION_THRESHOLD
        min_cov_thresh_val = MIN_COVERAGE_THRESHOLD
        max_mae_thresh_val = MAX_MAE_THRESHOLD

        # Re-derive reason code from provenance & metrics
        lineage_governed = False # Stub: Assumed controlled externally

        labels_sufficient = (mat is not None and mat >= act_thresh_val)
        pred_cov_passed = (pred_cov is not None and pred_cov >= min_cov_thresh_val)
        m6_cov_passed = (m6_cov is not None and m6_cov >= min_cov_thresh_val)
        m12_cov_passed = (m12_cov is not None and m12_cov >= min_cov_thresh_val)
        bounds_cov_passed = (bounds_cov is not None and bounds_cov >= min_cov_thresh_val)
        p80_cov_passed = (p80_cov is not None and p80_cov >= min_cov_thresh_val)
        mae_passed = (norm_mae is not None and norm_mae <= max_mae_thresh_val)

        expected_gate2_passed = (
            lineage_governed
            and labels_sufficient
            and pred_cov_passed
            and m6_cov_passed
            and m12_cov_passed
            and bounds_cov_passed
            and p80_cov_passed
            and mae_passed
        )

        if rec_prov == "unreachable_db":
            expected_reason_code = "DB_INVENTORY_UNREACHABLE"
        elif rec_prov == "no_source":
            expected_reason_code = "NO_SOURCE_INVENTORY"
        elif rec_prov == "provided_records":
            expected_reason_code = "UNAUTHENTICATED_PROVENANCE"
        elif not lineage_governed:
            expected_reason_code = "MISSING_GOVERNED_LINEAGE"
        elif not labels_sufficient:
            expected_reason_code = "MATURE_LABELS_BELOW_THRESHOLD"
        elif not pred_cov_passed:
            expected_reason_code = "PREDICTION_EVIDENCE_MISSING"
        elif not m6_cov_passed or not m12_cov_passed:
            expected_reason_code = "M6_M12_COVERAGE_INSUFFICIENT"
        elif not bounds_cov_passed:
            expected_reason_code = "INTERVAL_BOUNDS_MISSING"
        elif not mae_passed:
            expected_reason_code = "NORMALIZED_MAE_EXCEEDED"
        elif expected_gate2_passed:
            expected_reason_code = "GATE2_CRITERIA_MET"
        else:
            expected_reason_code = "GOVERNED_DISABLED"

        if sum_reason != expected_reason_code:
            errors.append(f"summary.reason_code mismatch: declared {sum_reason!r}, re-derived {expected_reason_code!r}")
        if hb_reason != expected_reason_code:
            errors.append(f"handback.reason_code mismatch: declared {hb_reason!r}, re-derived {expected_reason_code!r}")

        expected_gov_disabled = not expected_gate2_passed
        if rec_gov_disabled != expected_gov_disabled:
            errors.append(f"is_governed_disabled mismatch: declared {rec_gov_disabled}, re-derived {expected_gov_disabled}")
        if hb_gov_disabled != expected_gov_disabled:
            errors.append(f"handback.governed_disabled mismatch: declared {hb_gov_disabled}, re-derived {expected_gov_disabled}")

        expected_gate_status = "PASSED" if expected_gate2_passed else "REJECTED_GOVERNED_DISABLED"
        if rec_gate_status != expected_gate_status:
            errors.append(f"gate_status mismatch: declared {rec_gate_status!r}, re-derived {expected_gate_status!r}")

        expected_sum_status = "ACTIVE" if expected_gate2_passed else "GOVERNED_DISABLED"
        if sum_status != expected_sum_status:
            errors.append(f"summary.status mismatch: declared {sum_status!r}, re-derived {expected_sum_status!r}")

        is_gate2_passed = _check_strict_bool(summary.get("is_gate2_passed"), "benchmark_summary.is_gate2_passed")
        if is_gate2_passed is not None and expected_gate2_passed != is_gate2_passed:
            errors.append(f"is_gate2_passed mismatch: declared {is_gate2_passed}, re-derived {expected_gate2_passed}")

        if not expected_gate2_passed:
            if rec_gate_status == "PASSED" or rec_gov_disabled is False or sum_status == "ACTIVE":
                errors.append("Forged ACTIVE or PASSED verdict detected on unverified/failing receipt")

    except Exception as exc:
        errors.append(f"Unhandled exception during receipt verification: {exc}")

    if errors:
        reason = "INTEGRITY_HASH_MISMATCH" if any("Integrity hash mismatch:" in e for e in errors) else "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"
        return Gate2ReceiptVerificationResult(False, reason, tuple(errors))

    return Gate2ReceiptVerificationResult(True, "RECEIPT_VALIDATED", ())


__all__ = [
    "ACTIVATION_THRESHOLD",
    "MIN_COVERAGE_THRESHOLD",
    "MAX_MAE_THRESHOLD",
    "GATE2_RECEIPT_SCHEMA_VERSION",
    "GATE2_RECEIPT_KIND",
    "SiteScoreOpeningOutcomeBenchmarkResult",
    "Gate2ReceiptVerificationResult",
    "evaluate_sitescore_opening_outcome_benchmark",
    "build_sitescore_opening_outcome_model_card",
    "build_sitescore_gate2_receipt",
    "verify_sitescore_gate2_receipt",
    "compute_gate2_receipt_sha256",
    "compute_handback_sha256",
    "compute_model_card_sha256",
]
