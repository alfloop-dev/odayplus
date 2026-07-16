from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class MonitoringSignalType(StrEnum):
    DRIFT = "DRIFT"
    OUTCOME = "OUTCOME"


@dataclass(frozen=True)
class MonitoringBreach:
    metric_name: str
    observed_value: float
    threshold_message: str
    severity: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "observed_value": self.observed_value,
            "threshold_message": self.threshold_message,
            "severity": self.severity,
        }


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

    @property
    def triggered(self) -> bool:
        return any(breach.severity == "FAILED" for breach in self.breaches)

    def to_dict(self) -> dict[str, Any]:
        return {
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

    def to_dict(self) -> dict[str, Any]:
        return {
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


__all__ = [
    "MonitoringBreach",
    "MonitoringEvaluation",
    "MonitoringSignalType",
    "RetrainingRequest",
]
