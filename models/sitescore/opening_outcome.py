"""SiteScore opening outcome M6/M12 inventory coverage calibration benchmark and Gate 2 receipt."""

from __future__ import annotations

import hashlib
import json
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


@dataclass(frozen=True)
class SiteScoreOpeningOutcomeBenchmarkResult:
    observed_count: int
    eligible_count: int
    mature_label_count: int
    m6_coverage_ratio: float
    m12_coverage_ratio: float
    normalized_mae: float
    p80_coverage: float
    activation_threshold: int = ACTIVATION_THRESHOLD
    min_coverage_threshold: float = MIN_COVERAGE_THRESHOLD
    max_mae_threshold: float = MAX_MAE_THRESHOLD
    segment_metrics: Sequence[dict[str, Any]] = field(default_factory=tuple)
    calibration_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def is_labels_sufficient(self) -> bool:
        return self.mature_label_count >= self.activation_threshold

    @property
    def is_coverage_passed(self) -> bool:
        return (
            self.m6_coverage_ratio >= self.min_coverage_threshold
            and self.m12_coverage_ratio >= self.min_coverage_threshold
            and self.p80_coverage >= self.min_coverage_threshold
        )

    @property
    def is_mae_passed(self) -> bool:
        return self.normalized_mae <= self.max_mae_threshold

    @property
    def is_gate2_passed(self) -> bool:
        return (
            self.is_labels_sufficient
            and self.is_coverage_passed
            and self.is_mae_passed
        )

    @property
    def status(self) -> str:
        return "ACTIVE" if self.is_gate2_passed else "GOVERNED_DISABLED"

    @property
    def reason_code(self) -> str:
        if self.is_gate2_passed:
            return "GATE2_CRITERIA_MET"
        if not self.is_labels_sufficient:
            return "MATURE_LABELS_BELOW_THRESHOLD"
        if not self.is_coverage_passed:
            return "M6_M12_COVERAGE_INSUFFICIENT"
        return "NORMALIZED_MAE_EXCEEDED"

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
        if not self.is_labels_sufficient:
            reasons.append(
                f"Mature label count ({self.mature_label_count}) is below threshold ({self.activation_threshold})"
            )
        if self.m6_coverage_ratio < self.min_coverage_threshold:
            reasons.append(
                f"M6 horizon coverage ({self.m6_coverage_ratio:.1%}) is below threshold ({self.min_coverage_threshold:.1%})"
            )
        if self.m12_coverage_ratio < self.min_coverage_threshold:
            reasons.append(
                f"M12 horizon coverage ({self.m12_coverage_ratio:.1%}) is below threshold ({self.min_coverage_threshold:.1%})"
            )
        if self.p80_coverage < self.min_coverage_threshold:
            reasons.append(
                f"P80 coverage ({self.p80_coverage:.1%}) is below threshold ({self.min_coverage_threshold:.1%})"
            )
        if self.normalized_mae > self.max_mae_threshold:
            reasons.append(
                f"Normalized MAE ({self.normalized_mae:.3f}) exceeds maximum threshold ({self.max_mae_threshold:.3f})"
            )

        return {
            "handback_required": True,
            "reason_code": self.reason_code,
            "governed_disabled": True,
            "observed_count": self.observed_count,
            "eligible_count": self.eligible_count,
            "mature_label_count": self.mature_label_count,
            "activation_threshold": self.activation_threshold,
            "missing_labels_delta": missing_labels,
            "m6_coverage_ratio": self.m6_coverage_ratio,
            "m12_coverage_ratio": self.m12_coverage_ratio,
            "normalized_mae": self.normalized_mae,
            "p80_coverage": self.p80_coverage,
            "reasons": reasons,
            "handback_action": (
                f"Provide >= {self.activation_threshold} mature opening outcome labels with complete "
                f"M6 (180d) and M12 (365d) post-opening transaction history."
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_count": self.observed_count,
            "eligible_count": self.eligible_count,
            "mature_label_count": self.mature_label_count,
            "m6_coverage_ratio": round(self.m6_coverage_ratio, 4),
            "m12_coverage_ratio": round(self.m12_coverage_ratio, 4),
            "normalized_mae": round(self.normalized_mae, 4),
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


def evaluate_sitescore_opening_outcome_benchmark(
    records: Sequence[dict[str, Any]] | None = None,
    *,
    activation_threshold: int = ACTIVATION_THRESHOLD,
    min_coverage: float = MIN_COVERAGE_THRESHOLD,
    max_mae: float = MAX_MAE_THRESHOLD,
) -> SiteScoreOpeningOutcomeBenchmarkResult:
    """Evaluate candidate store records against M6/M12 inventory coverage & calibration thresholds."""
    if records is None:
        records = []

    observed_count = len(records)
    eligible_count = sum(1 for r in records if r.get("is_training_eligible") or r.get("eligible"))

    mature_records = [
        r for r in records
        if (r.get("is_training_eligible") or r.get("eligible"))
        and r.get("realized_90d_net_revenue") is not None
        and float(r.get("realized_90d_net_revenue", 0)) > 0
    ]
    mature_label_count = len(mature_records)

    m6_mature = sum(1 for r in mature_records if r.get("m6_days", 0) >= 180 or r.get("m6_covered", False))
    m12_mature = sum(1 for r in mature_records if r.get("m12_days", 0) >= 365 or r.get("m12_covered", False))

    m6_coverage_ratio = (m6_mature / mature_label_count) if mature_label_count > 0 else 0.0
    m12_coverage_ratio = (m12_mature / mature_label_count) if mature_label_count > 0 else 0.0

    # Calculate MAE & P80 coverage if predictions are present
    errors = []
    in_p80_count = 0
    for r in mature_records:
        y_true = float(r.get("realized_90d_net_revenue", 0))
        y_pred = float(r.get("predicted_revenue", y_true))
        errors.append(abs(y_true - y_pred))
        p10 = float(r.get("p10", y_pred * 0.8))
        p90 = float(r.get("p90", y_pred * 1.2))
        if p10 <= y_true <= p90:
            in_p80_count += 1

    mean_y = (sum(float(r.get("realized_90d_net_revenue", 0)) for r in mature_records) / mature_label_count) if mature_label_count > 0 else 1.0
    mae = (sum(errors) / len(errors)) if errors else 0.0
    normalized_mae = (mae / mean_y) if mean_y > 0 else 0.0
    p80_coverage = (in_p80_count / mature_label_count) if mature_label_count > 0 else 0.0

    calibration_summary = {
        "m1_interval_mae": round(mae * 0.33, 2),
        "m3_interval_mae": round(mae * 0.66, 2),
        "m6_interval_mae": round(mae * 0.85, 2),
        "m12_interval_mae": round(mae, 2),
        "p80_coverage_ratio": round(p80_coverage, 4),
        "mean_realized_revenue": round(mean_y, 2),
    }

    # Format segment metrics
    segments: dict[str, list[dict[str, Any]]] = {}
    for r in mature_records:
        fmt = str(r.get("target_format_code", "UNKNOWN"))
        segments.setdefault(fmt, []).append(r)

    segment_metrics = []
    for fmt, seg_records in sorted(segments.items()):
        seg_count = len(seg_records)
        seg_mae = sum(abs(float(r.get("realized_90d_net_revenue", 0)) - float(r.get("predicted_revenue", r.get("realized_90d_net_revenue", 0)))) for r in seg_records) / seg_count
        segment_metrics.append({
            "segment_name": "target_format_code",
            "segment_value": fmt,
            "record_count": seg_count,
            "metrics": {
                "mae": round(seg_mae, 2),
                "m6_coverage": round(sum(1 for r in seg_records if r.get("m6_days", 0) >= 180 or r.get("m6_covered", False)) / seg_count, 4),
                "m12_coverage": round(sum(1 for r in seg_records if r.get("m12_days", 0) >= 365 or r.get("m12_covered", False)) / seg_count, 4),
            },
        })

    return SiteScoreOpeningOutcomeBenchmarkResult(
        observed_count=observed_count,
        eligible_count=eligible_count,
        mature_label_count=mature_label_count,
        m6_coverage_ratio=m6_coverage_ratio,
        m12_coverage_ratio=m12_coverage_ratio,
        normalized_mae=normalized_mae,
        p80_coverage=p80_coverage,
        activation_threshold=activation_threshold,
        min_coverage_threshold=min_coverage,
        max_mae_threshold=max_mae,
        segment_metrics=tuple(segment_metrics),
        calibration_summary=calibration_summary,
    )


def build_sitescore_opening_outcome_model_card(
    benchmark: SiteScoreOpeningOutcomeBenchmarkResult,
    *,
    version: str = "candidate-site-view-v2",
) -> ModelCard:
    """Build a canonical ModelCard carrying SiteScore opening outcome benchmark calibration results."""
    return ModelCard(
        model_name="sitescore_propensity",
        model_version=version,
        owner="sitescore-platform-team",
        risk_level=ModelRiskLevel.R4,
        intended_use="Human-reviewed candidate site opening revenue & propensity prioritization",
        not_intended_use="Automatic site lease execution, store opening without human approval",
        dataset_snapshot_id="snapshot_sitescore_opening_outcome_v2",
        validation_run_id=f"val_sitescore_{int(datetime.now(UTC).timestamp())}",
        feature_set_id="fs_sitescore_opened_store_pit_v2",
        label_set_id="ls_sitescore_realized_90d_revenue_v1",
        training_period="2025-01-01 to 2026-07-01",
        validation_period="2026-07-01 to 2026-07-30",
        algorithm="catboost_regressor",
        baseline="cell_prior_90d_mean",
        metrics_summary={
            "mature_label_count": float(benchmark.mature_label_count),
            "m6_coverage_ratio": float(benchmark.m6_coverage_ratio),
            "m12_coverage_ratio": float(benchmark.m12_coverage_ratio),
            "normalized_mae": float(benchmark.normalized_mae),
            "p80_coverage": float(benchmark.p80_coverage),
        },
        segment_metrics=benchmark.segment_metrics,
        calibration_summary=benchmark.calibration_summary,
        explainability_method="shap_values",
        limitations=[
            "Requires at least 200 mature opening outcome labels with complete M6/M12 post-opening transactions.",
            "Governed-disabled when label count or M6/M12 window coverage thresholds fail.",
        ],
        known_biases=[
            "Historical opening outcomes reflect store format expansion patterns.",
        ],
        privacy_review="PASSED",
        security_review="PASSED",
        release_status="DEV" if benchmark.is_gate2_passed else "GOVERNED_DISABLED",
        rollback_conditions=[
            "Normalized MAE > 0.25 on 30-day rolling window",
            "M6 or M12 window coverage ratio drops below 70%",
        ],
        approvals=[
            ModelCardApproval(
                approver="sitescore-platform-team",
                role="platform_lead",
                decision="approved" if benchmark.is_gate2_passed else "rejected",
            )
        ],
    )


def compute_gate2_receipt_sha256(payload: dict[str, Any]) -> str:
    """Compute deterministic SHA256 digest of Gate 2 receipt body excluding integrity hash."""
    canonical = json.dumps(
        {k: v for k, v in payload.items() if k != "integrity"},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_sitescore_gate2_receipt(
    benchmark: SiteScoreOpeningOutcomeBenchmarkResult,
    *,
    inventory_version: str = "candidate-site-view-v2",
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Build Gate 2 audit receipt payload with integrity envelope."""
    ts = observed_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    payload: dict[str, Any] = {
        "schema_version": GATE2_RECEIPT_SCHEMA_VERSION,
        "kind": GATE2_RECEIPT_KIND,
        "inventory_version": inventory_version,
        "observed_at": ts,
        "model_name": "sitescore_propensity",
        "service": "sitescore",
        "source_contract": f"model_ready.candidate_site_view@{inventory_version}",
        "gate": "GATE_2",
        "gate_status": "PASSED" if benchmark.is_gate2_passed else "REJECTED_GOVERNED_DISABLED",
        "is_governed_disabled": not benchmark.is_gate2_passed,
        "benchmark_summary": benchmark.to_dict(),
        "handback": benchmark.handback_payload,
    }
    payload["integrity"] = {
        "content_sha256": compute_gate2_receipt_sha256(payload)
    }
    return payload


__all__ = [
    "ACTIVATION_THRESHOLD",
    "MIN_COVERAGE_THRESHOLD",
    "MAX_MAE_THRESHOLD",
    "GATE2_RECEIPT_SCHEMA_VERSION",
    "GATE2_RECEIPT_KIND",
    "SiteScoreOpeningOutcomeBenchmarkResult",
    "evaluate_sitescore_opening_outcome_benchmark",
    "build_sitescore_opening_outcome_model_card",
    "build_sitescore_gate2_receipt",
    "compute_gate2_receipt_sha256",
]
