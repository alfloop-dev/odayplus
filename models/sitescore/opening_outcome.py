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
    prediction_coverage_ratio: float = 0.0
    interval_bounds_coverage_ratio: float = 0.0
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
        return self.prediction_coverage_ratio >= self.min_coverage_threshold

    @property
    def is_coverage_passed(self) -> bool:
        return (
            self.m6_coverage_ratio >= self.min_coverage_threshold
            and self.m12_coverage_ratio >= self.min_coverage_threshold
            and self.p80_coverage >= self.min_coverage_threshold
            and self.is_prediction_coverage_passed
        )

    @property
    def is_interval_bounds_passed(self) -> bool:
        return self.interval_bounds_coverage_ratio >= self.min_coverage_threshold

    @property
    def is_mae_passed(self) -> bool:
        return self.normalized_mae <= self.max_mae_threshold

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
            if self.normalized_mae > self.max_mae_threshold:
                reasons.append(
                    f"Normalized MAE ({self.normalized_mae:.3f}) exceeds maximum threshold ({self.max_mae_threshold:.3f})"
                )
            handback_action = (
                f"Provide >= {self.activation_threshold} mature opening outcome labels with complete "
                f"M6 (180d) and M12 (365d) post-opening transaction history, actual p10/p90 interval bounds, and model predictions."
            )

        return {
            "handback_required": True,
            "reason_code": self.reason_code,
            "governed_disabled": True,
            "provenance": self.provenance,
            "observed_count": self.observed_count,
            "eligible_count": self.eligible_count,
            "mature_label_count": self.mature_label_count,
            "activation_threshold": self.activation_threshold,
            "missing_labels_delta": missing_labels,
            "m6_coverage_ratio": self.m6_coverage_ratio,
            "m12_coverage_ratio": self.m12_coverage_ratio,
            "prediction_coverage_ratio": self.prediction_coverage_ratio,
            "interval_bounds_coverage_ratio": self.interval_bounds_coverage_ratio,
            "normalized_mae": self.normalized_mae,
            "p80_coverage": self.p80_coverage,
            "reasons": reasons,
            "handback_action": handback_action,
            "backfill_owner": "Human/Ops",
            "backfill_task_id": "ODP-SITESCORE-AUTHORITATIVE-OUTCOME-BACKFILL-001",
            "backfill_query": "SELECT entity_id, store_id, target_format_code, opened_on, is_training_eligible, realized_90d_net_revenue, (CURRENT_DATE - opened_on)::integer AS m6_days, (CURRENT_DATE - opened_on)::integer AS m12_days FROM model_ready.candidate_site_view;",
            "backfill_receipt_required": True,
        }

    def to_dict(self) -> dict[str, Any]:
        res = {
            "provenance": self.provenance,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "model_version": self.model_version,
            "artifact_lineage_id": self.artifact_lineage_id,
            "observed_count": self.observed_count,
            "eligible_count": self.eligible_count,
            "mature_label_count": self.mature_label_count,
            "prediction_coverage_ratio": round(self.prediction_coverage_ratio, 4),
            "interval_bounds_coverage_ratio": round(self.interval_bounds_coverage_ratio, 4),
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

    def _is_valid_realized_outcome(val: Any) -> bool:
        """Check if a realized outcome value is non-null and valid.

        Negative-value policy:
        Legitimate zero net revenue outcomes (val == 0.0 or val >= 0.0) are valid mature labels.
        Negative values (val < 0.0) represent corrupted or unverified net revenue data
        (e.g., net refund adjustments without sales) and are excluded from mature label evaluation.
        """
        if val is None:
            return False
        try:
            return float(val) >= 0.0
        except (ValueError, TypeError):
            return False

    observed_count = len(records)
    eligible_count = sum(1 for r in records if r.get("is_training_eligible") or r.get("eligible"))

    mature_records = [
        r for r in records
        if (r.get("is_training_eligible") or r.get("eligible"))
        and _is_valid_realized_outcome(r.get("realized_90d_net_revenue"))
    ]
    mature_label_count = len(mature_records)

    ref_date = datetime.now(UTC).date()
    if isinstance(observed_at, str):
        try:
            ref_date = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).date()
        except Exception:
            pass
    elif isinstance(observed_at, datetime):
        ref_date = observed_at.date()

    def get_days_elapsed(r: dict[str, Any], key: str) -> int | None:
        val = r.get(key)
        if val is not None and isinstance(val, (int, float)):
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

    predicted_records = [
        r for r in mature_records if r.get("predicted_revenue") is not None
    ]
    prediction_count = len(predicted_records)
    prediction_coverage_ratio = (prediction_count / mature_label_count) if mature_label_count > 0 else 0.0

    errors = []
    in_p80_count = 0
    interval_bounds_count = 0

    for r in mature_records:
        pred_val = r.get("predicted_revenue")
        if pred_val is None:
            continue
        try:
            y_true = float(r.get("realized_90d_net_revenue", 0))
            y_pred = float(pred_val)
            errors.append(abs(y_true - y_pred))

            p10_raw = r.get("p10")
            p90_raw = r.get("p90")
            if p10_raw is not None and p90_raw is not None:
                p10 = float(p10_raw)
                p90 = float(p90_raw)
                if p10 <= p90:
                    interval_bounds_count += 1
                    if p10 <= y_true <= p90:
                        in_p80_count += 1
        except (ValueError, TypeError):
            continue

    mean_y = (sum(float(r.get("realized_90d_net_revenue", 0)) for r in mature_records) / mature_label_count) if mature_label_count > 0 else 1.0
    mae = (sum(errors) / len(errors)) if errors else 0.0
    normalized_mae = (mae / mean_y) if (mean_y > 0 and errors) else 0.0
    p80_coverage = (in_p80_count / mature_label_count) if mature_label_count > 0 else 0.0
    interval_bounds_coverage_ratio = (interval_bounds_count / mature_label_count) if mature_label_count > 0 else 0.0

    calibration_summary = {
        "measured_90d_mae": round(mae, 2) if errors else None,
        "prediction_coverage_ratio": round(prediction_coverage_ratio, 4),
        "interval_bounds_coverage_ratio": round(interval_bounds_coverage_ratio, 4),
        "p80_coverage_ratio": round(p80_coverage, 4),
        "mean_realized_revenue": round(mean_y, 2),
    }

    segments: dict[str, list[dict[str, Any]]] = {}
    for r in mature_records:
        fmt = str(r.get("target_format_code", "UNKNOWN"))
        segments.setdefault(fmt, []).append(r)

    segment_metrics = []
    for fmt, seg_records in sorted(segments.items()):
        seg_count = len(seg_records)
        seg_pred_records = [r for r in seg_records if r.get("predicted_revenue") is not None]
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
    )


def build_sitescore_opening_outcome_model_card(
    benchmark: SiteScoreOpeningOutcomeBenchmarkResult,
    *,
    version: str = "candidate-site-view-v2",
) -> ModelCard:
    """Build a canonical ModelCard carrying SiteScore opening outcome benchmark calibration results."""
    return ModelCard(
        model_name="sitescore_propensity",
        model_version=benchmark.model_version or version,
        owner="sitescore-platform-team",
        risk_level=ModelRiskLevel.R4,
        intended_use="Human-reviewed candidate site opening revenue & propensity prioritization",
        not_intended_use="Automatic site lease execution, store opening without human approval",
        dataset_snapshot_id=benchmark.dataset_snapshot_id or "snapshot_sitescore_opening_outcome_v2",
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
            "prediction_coverage_ratio": float(benchmark.prediction_coverage_ratio),
            "interval_bounds_coverage_ratio": float(benchmark.interval_bounds_coverage_ratio),
            "normalized_mae": float(benchmark.normalized_mae),
            "p80_coverage": float(benchmark.p80_coverage),
        },
        segment_metrics=benchmark.segment_metrics,
        calibration_summary=benchmark.calibration_summary,
        explainability_method="shap_values",
        limitations=[
            "Requires at least 200 mature opening outcome labels with complete M6/M12 post-opening transactions.",
            "Governed-disabled when label count, M6/M12 window coverage, or interval bound coverage thresholds fail.",
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
