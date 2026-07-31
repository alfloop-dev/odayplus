"""SiteScore opening outcome M6/M12 inventory coverage calibration benchmark and Gate 2 receipt."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from models.shared_ml.model_card import ModelCard, ModelCardApproval, ModelRiskLevel

ACTIVATION_THRESHOLD = 200
MIN_COVERAGE_THRESHOLD = 0.70
MAX_MAE_THRESHOLD = 0.25
GATE2_RECEIPT_SCHEMA_VERSION = 1
GATE2_RECEIPT_KIND = "sitescore-opening-outcome-gate2-receipt"


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
    matched_mean_y: float = 0.0
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
            handback_action = "Restore PostgreSQL database connection and verify model_ready.candidate_site_view table accessibility."
        elif self.provenance == "no_source":
            reasons.append("No database connection or candidate site records were provided")
            handback_action = "Provide a valid PostgreSQL database URL (ODAY_DATABASE_URL / --db-url) or candidate site records."
        elif self.provenance == "provided_records":
            reasons.append("Provided records are unauthenticated / non-governed activation input")
            handback_action = "Provide authenticated governed PostgreSQL inventory records with immutable dataset snapshot and model lineage."
        elif not self.is_lineage_governed:
            reasons.append(
                f"Missing governed dataset snapshot or model/artifact lineage (snapshot={self.dataset_snapshot_id}, model_version={self.model_version}, artifact_lineage_id={self.artifact_lineage_id}; requires authoritative prediction-source resolver ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001)"
            )
            if not math.isfinite(self.normalized_mae) or self.normalized_mae > self.max_mae_threshold:
                reasons.append(
                    f"Normalized MAE ({self.normalized_mae:.3f}) exceeds maximum threshold ({self.max_mae_threshold:.3f})"
                )
            handback_action = "Provide complete governed dataset snapshot ID/hash and model/artifact lineage resolved via authoritative prediction source (ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001)."
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
                f"M6 (180d) and M12 (365d) post-opening transaction history, actual p10/p90 interval bounds, and model predictions."
            )

        executable_query = (
            "SELECT entity_id, store_id, target_format_code, opened_on, is_training_eligible, "
            "realized_90d_net_revenue, (CURRENT_DATE - opened_on)::integer AS store_age_days "
            "FROM model_ready.candidate_site_view;"
        )
        observed_at_str = self.observed_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return {
            "handback_required": True,
            "reason_code": self.reason_code,
            "governed_disabled": True,
            "provenance": self.provenance,
            "observed_count": self.observed_count,
            "eligible_count": self.eligible_count,
            "mature_label_count": self.mature_label_count,
            "matched_prediction_count": self.matched_prediction_count,
            "matched_mean_y": round(self.matched_mean_y, 2),
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
                "source_identity": "model_ready.candidate_site_view",
                "query_id": "sitescore_opening_outcome_inventory_query_v1",
                "dataset_snapshot_hash": self.dataset_snapshot_id or "UNVERIFIED",
                "lineage_id": self.artifact_lineage_id or "UNVERIFIED",
                "freshness_timestamp": observed_at_str,
                "eligibility_definition": "is_training_eligible IS True or eligible IS True",
                "maturity_definition": "realized_90d_net_revenue IS NOT NULL AND realized_90d_net_revenue >= 0",
                "observed_count": self.observed_count,
                "eligible_count": self.eligible_count,
                "mature_count": self.mature_label_count,
                "matched_prediction_count": self.matched_prediction_count,
                "required_fields": ["realized_180d_net_revenue", "realized_365d_net_revenue"],
                "baseline_inventory_query": executable_query,
                "note": "Store age (store_age_days) is a maturity precondition; it is not M6/M12 outcome evidence. ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001 must supply realized_180d_net_revenue and realized_365d_net_revenue.",
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
            "backfill_query": executable_query,
            "backfill_receipt_required": True,
        }

    def to_dict(self) -> dict[str, Any]:
        norm_mae = round(self.normalized_mae, 4) if math.isfinite(self.normalized_mae) else 999.0
        res = {
            "provenance": self.provenance,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "model_version": self.model_version,
            "artifact_lineage_id": self.artifact_lineage_id,
            "observed_count": self.observed_count,
            "eligible_count": self.eligible_count,
            "mature_label_count": self.mature_label_count,
            "matched_prediction_count": self.matched_prediction_count,
            "matched_mean_y": round(self.matched_mean_y, 2),
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

        days = get_days_elapsed(r, "m6_days")
        if days is not None and days >= 180:
            return True
        return r.get("m6_covered", False) is True

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

        days = get_days_elapsed(r, "m12_days")
        if days is not None and days >= 365:
            return True
        return r.get("m12_covered", False) is True

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

    overall_mean_y = (sum(float(r.get("realized_90d_net_revenue", 0)) for r in mature_records) / mature_label_count) if mature_label_count > 0 else 1.0

    p80_coverage = (in_p80_count / mature_label_count) if mature_label_count > 0 else 0.0
    interval_bounds_coverage_ratio = (interval_bounds_count / mature_label_count) if mature_label_count > 0 else 0.0

    calibration_summary = {
        "measured_90d_mae": round(mae, 2) if errors else None,
        "matched_prediction_count": matched_prediction_count,
        "matched_mean_realized_revenue": round(matched_mean_y, 2),
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
        matched_mean_y=matched_mean_y,
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
) -> ModelCard:
    """Build a canonical ModelCard carrying SiteScore opening outcome benchmark calibration results."""
    is_governed_active = benchmark.is_gate2_passed and benchmark.is_lineage_governed

    # B4 fix: Unless benchmark is governance-passed with authentic lineage, reject caller-invented facts and force UNVERIFIED/UNAVAILABLE
    if not is_governed_active:
        dataset_snapshot_id = "UNAVAILABLE"
        model_version = "UNVERIFIED"
        resolved_val_id = "UNVERIFIED"
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
    else:
        dataset_snapshot_id = benchmark.dataset_snapshot_id or "UNAVAILABLE"
        model_version = benchmark.model_version or version
        resolved_val_id = validation_run_id or benchmark.artifact_lineage_id or "UNVERIFIED"
        resolved_feature_set_id = feature_set_id or "UNVERIFIED"
        resolved_label_set_id = label_set_id or "UNVERIFIED"
        resolved_training_period = training_period or "UNAVAILABLE"
        resolved_validation_period = validation_period or "UNAVAILABLE"
        resolved_algorithm = algorithm or "UNAVAILABLE"
        resolved_baseline = baseline or "UNAVAILABLE"
        resolved_explainability_method = explainability_method or "UNAVAILABLE"
        resolved_privacy_review = privacy_review or "UNVERIFIED"
        resolved_security_review = security_review or "UNVERIFIED"
        resolved_approvals = tuple(approvals) if approvals is not None else ()

    norm_mae = float(benchmark.normalized_mae) if math.isfinite(benchmark.normalized_mae) else 999.0

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
    )


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
) -> dict[str, Any]:
    """Build Gate 2 audit receipt payload with integrity envelope."""
    ts = observed_at or benchmark.observed_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
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
    }
    if benchmark.db_error:
        payload["db_error"] = benchmark.db_error
    payload["integrity"] = {
        "content_sha256": compute_gate2_receipt_sha256(payload)
    }
    return payload


@dataclass(frozen=True)
class Gate2ReceiptVerificationResult:
    is_valid: bool
    reason_code: str
    errors: Sequence[str] = field(default_factory=tuple)


def verify_sitescore_gate2_receipt(receipt: dict[str, Any]) -> Gate2ReceiptVerificationResult:
    """Fail-closed verifier for Gate 2 receipt content and integrity (Fix for B3)."""
    errors: list[str] = []

    if not isinstance(receipt, dict):
        return Gate2ReceiptVerificationResult(False, "INVALID_RECEIPT_TYPE", ("Receipt must be a JSON dictionary",))

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

    summary = receipt.get("benchmark_summary", {})
    handback = receipt.get("handback", {})

    if not isinstance(summary, dict):
        errors.append("benchmark_summary must be a dictionary")
        return Gate2ReceiptVerificationResult(False, "MALFORMED_RECEIPT_SUMMARY", tuple(errors))

    # 3. Numeric finiteness validation across all receipt fields
    def _check_finite_dict(d: dict[str, Any], path: str) -> None:
        for k, v in d.items():
            if isinstance(v, float):
                if not math.isfinite(v):
                    errors.append(f"Non-finite float value in {path}.{k}: {v}")
            elif isinstance(v, dict):
                _check_finite_dict(v, f"{path}.{k}")

    _check_finite_dict(summary, "benchmark_summary")
    _check_finite_dict(handback, "handback")

    # 4. Count and ratio cross-field validation
    obs = summary.get("observed_count", 0)
    elg = summary.get("eligible_count", 0)
    mat = summary.get("mature_label_count", 0)
    match_cnt = summary.get("matched_prediction_count", 0)

    if not (isinstance(obs, int) and isinstance(elg, int) and isinstance(mat, int) and isinstance(match_cnt, int)):
        errors.append("Counts in benchmark_summary must be integers")
    else:
        if obs < 0 or elg < 0 or mat < 0 or match_cnt < 0:
            errors.append("Counts cannot be negative")
        if elg > obs:
            errors.append(f"Eligible count ({elg}) cannot exceed observed count ({obs})")
        if mat > elg:
            errors.append(f"Mature label count ({mat}) cannot exceed eligible count ({elg})")
        if match_cnt > mat:
            errors.append(f"Matched prediction count ({match_cnt}) cannot exceed mature label count ({mat})")

    # 5. Verdict re-derivation (fail closed on forged ACTIVE / PASSED or count drift)
    gate_status = receipt.get("gate_status")
    is_gov_disabled = receipt.get("is_governed_disabled")
    benchmark_status = summary.get("status")
    is_gate2_passed = summary.get("is_gate2_passed")

    # Currently lineage governance is unverified until ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001
    lineage_governed = False
    labels_sufficient = mat >= summary.get("activation_threshold", ACTIVATION_THRESHOLD)
    pred_cov = summary.get("prediction_coverage_ratio", 0.0) >= summary.get("min_coverage_threshold", MIN_COVERAGE_THRESHOLD)
    m6_cov = summary.get("m6_coverage_ratio", 0.0) >= summary.get("min_coverage_threshold", MIN_COVERAGE_THRESHOLD)
    m12_cov = summary.get("m12_coverage_ratio", 0.0) >= summary.get("min_coverage_threshold", MIN_COVERAGE_THRESHOLD)
    bounds_cov = summary.get("interval_bounds_coverage_ratio", 0.0) >= summary.get("min_coverage_threshold", MIN_COVERAGE_THRESHOLD)
    p80_cov = summary.get("p80_coverage", 0.0) >= summary.get("min_coverage_threshold", MIN_COVERAGE_THRESHOLD)
    norm_mae = summary.get("normalized_mae", 999.0)
    mae_passed = math.isfinite(norm_mae) and norm_mae <= summary.get("max_mae_threshold", MAX_MAE_THRESHOLD)

    expected_gate2_passed = (
        lineage_governed
        and labels_sufficient
        and pred_cov
        and m6_cov
        and m12_cov
        and bounds_cov
        and p80_cov
        and mae_passed
    )

    if expected_gate2_passed != is_gate2_passed:
        errors.append(f"is_gate2_passed mismatch: declared {is_gate2_passed}, re-derived {expected_gate2_passed}")

    if not expected_gate2_passed:
        if gate_status == "PASSED" or is_gov_disabled is False or benchmark_status == "ACTIVE":
            errors.append("Forged ACTIVE or PASSED verdict detected on unverified/failing receipt")

    if errors:
        reason = "INTEGRITY_HASH_MISMATCH" if any("Integrity hash mismatch" in e for e in errors) else "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"
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
]
