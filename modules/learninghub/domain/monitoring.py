from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class MonitoringSignalType(StrEnum):
    DRIFT = "DRIFT"
    PREDICTION_DRIFT = "PREDICTION_DRIFT"
    OUTCOME = "OUTCOME"


@dataclass(frozen=True)
class MonitoringBreach:
    metric_name: str
    observed_value: float
    threshold_message: str
    severity: str
    baseline_value: float | None = None
    degradation: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "metric_name": self.metric_name,
            "observed_value": self.observed_value,
            "threshold_message": self.threshold_message,
            "severity": self.severity,
        }
        if self.baseline_value is not None:
            data["baseline_value"] = self.baseline_value
        if self.degradation is not None:
            data["degradation"] = self.degradation
        return data


@dataclass(frozen=True)
class MonitoringEvaluation:
    evaluation_id: str
    model_name: str
    model_version: str
    dataset_snapshot_id: str
    signal_type: MonitoringSignalType
    observed_metrics: Mapping[str, float]
    baseline_metrics: Mapping[str, float]
    breaches: Sequence[MonitoringBreach] = ()
    requested_by: str = "system"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decision_policy_version_id: str | None = None
    # Prediction-drift evaluations carry both immutable population snapshot
    # identities. ``dataset_snapshot_id`` remains the legacy/current snapshot
    # field used by generic monitoring evaluations.
    reference_snapshot_id: str | None = None
    current_snapshot_id: str | None = None
    cohort_key: str | None = None
    prediction_columns: tuple[str, ...] = ()
    prediction_output_types: Mapping[str, str] = field(default_factory=dict)
    drift_detected: bool | None = None
    drifted_columns: tuple[str, ...] = ()
    drift_report_json: str | None = None
    drift_engine: str | None = None
    alert_id: str | None = None
    audit_event_id: str | None = None

    def __post_init__(self) -> None:
        if self.signal_type is not MonitoringSignalType.PREDICTION_DRIFT:
            return
        required = {
            "reference_snapshot_id": self.reference_snapshot_id,
            "current_snapshot_id": self.current_snapshot_id,
            "cohort_key": self.cohort_key,
            "decision_policy_version_id": self.decision_policy_version_id,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise ValueError(
                "prediction drift evaluation requires " + ", ".join(missing)
            )
        if self.reference_snapshot_id == self.current_snapshot_id:
            raise ValueError("prediction drift snapshot ids must differ")
        if not self.prediction_columns:
            raise ValueError("prediction drift evaluation requires prediction columns")
        if self.drift_detected is None:
            raise ValueError("prediction drift evaluation requires drift_detected")

    @property
    def triggered(self) -> bool:
        return any(breach.severity == "FAILED" for breach in self.breaches)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "evaluation_id": self.evaluation_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "signal_type": self.signal_type.value,
            "observed_metrics": dict(self.observed_metrics),
            "baseline_metrics": dict(self.baseline_metrics),
            "breaches": [breach.to_dict() for breach in self.breaches],
            "triggered": self.triggered,
            "requested_by": self.requested_by,
            "created_at": self.created_at.isoformat(),
        }
        if self.decision_policy_version_id is not None:
            data["decision_policy_version_id"] = self.decision_policy_version_id
        if self.reference_snapshot_id is not None:
            data["reference_snapshot_id"] = self.reference_snapshot_id
        if self.current_snapshot_id is not None:
            data["current_snapshot_id"] = self.current_snapshot_id
        if self.cohort_key is not None:
            data["cohort_key"] = self.cohort_key
        if self.prediction_columns:
            data["prediction_columns"] = list(self.prediction_columns)
        if self.prediction_output_types:
            data["prediction_output_types"] = dict(self.prediction_output_types)
        if self.drift_detected is not None:
            data["drift_detected"] = self.drift_detected
        if self.drifted_columns:
            data["drifted_columns"] = list(self.drifted_columns)
        if self.drift_report_json is not None:
            data["drift_report_json"] = self.drift_report_json
        if self.drift_engine is not None:
            data["drift_engine"] = self.drift_engine
        if self.alert_id is not None:
            data["alert_id"] = self.alert_id
        if self.audit_event_id is not None:
            data["audit_event_id"] = self.audit_event_id
        return data


@dataclass(frozen=True)
class RetrainingRequest:
    request_id: str
    model_name: str
    source_model_version: str
    trigger_evaluation_id: str
    trigger_type: MonitoringSignalType
    reason: str
    dataset_snapshot_id: str
    observed_metrics: Mapping[str, float]
    baseline_metrics: Mapping[str, float]
    requested_by: str
    status: str = "OPEN"
    auto_promotion: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decision_policy_version_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "request_id": self.request_id,
            "model_name": self.model_name,
            "source_model_version": self.source_model_version,
            "trigger_evaluation_id": self.trigger_evaluation_id,
            "trigger_type": self.trigger_type.value,
            "reason": self.reason,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "observed_metrics": dict(self.observed_metrics),
            "baseline_metrics": dict(self.baseline_metrics),
            "requested_by": self.requested_by,
            "status": self.status,
            "auto_promotion": self.auto_promotion,
            "created_at": self.created_at.isoformat(),
        }
        if self.decision_policy_version_id is not None:
            data["decision_policy_version_id"] = self.decision_policy_version_id
        return data


__all__ = [
    "MonitoringBreach",
    "MonitoringEvaluation",
    "MonitoringSignalType",
    "RetrainingRequest",
]
