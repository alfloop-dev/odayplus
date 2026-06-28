from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from modules.learninghub.domain.dataset_snapshot import DatasetSnapshot


class ValidationStatus(StrEnum):
    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"


@dataclass(frozen=True)
class MetricThreshold:
    metric_name: str
    min_value: float | None = None
    max_value: float | None = None
    warning_min_value: float | None = None
    warning_max_value: float | None = None

    def evaluate(self, value: float) -> tuple[ValidationStatus, str | None]:
        if self.min_value is not None and value < self.min_value:
            return ValidationStatus.FAILED, f"{self.metric_name} below minimum {self.min_value}"
        if self.max_value is not None and value > self.max_value:
            return ValidationStatus.FAILED, f"{self.metric_name} above maximum {self.max_value}"
        if self.warning_min_value is not None and value < self.warning_min_value:
            return ValidationStatus.WARNING, f"{self.metric_name} below warning {self.warning_min_value}"
        if self.warning_max_value is not None and value > self.warning_max_value:
            return ValidationStatus.WARNING, f"{self.metric_name} above warning {self.warning_max_value}"
        return ValidationStatus.PASSED, None


@dataclass(frozen=True)
class SegmentMetric:
    segment_name: str
    segment_value: str
    metrics: Mapping[str, float]
    record_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_name": self.segment_name,
            "segment_value": self.segment_value,
            "metrics": dict(self.metrics),
            "record_count": self.record_count,
        }


@dataclass(frozen=True)
class ValidationRuleFailure:
    rule_name: str
    severity: ValidationStatus
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "rule_name": self.rule_name,
            "severity": self.severity.value,
            "message": self.message,
        }


@dataclass(frozen=True)
class ValidationRun:
    validation_run_id: str
    model_name: str
    model_version: str
    dataset_snapshot_id: str
    status: ValidationStatus
    metrics: Mapping[str, float]
    baseline_metrics: Mapping[str, float]
    segment_metrics: Sequence[SegmentMetric] = ()
    calibration_summary: Mapping[str, Any] = field(default_factory=dict)
    failed_rules: Sequence[ValidationRuleFailure] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def passed(self) -> bool:
        return self.status is ValidationStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_run_id": self.validation_run_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "status": self.status.value,
            "metrics": dict(self.metrics),
            "baseline_metrics": dict(self.baseline_metrics),
            "segment_metrics": [metric.to_dict() for metric in self.segment_metrics],
            "calibration_summary": dict(self.calibration_summary),
            "failed_rules": [failure.to_dict() for failure in self.failed_rules],
            "created_at": self.created_at.isoformat(),
        }


def validate_model_candidate(
    *,
    model_name: str,
    model_version: str,
    dataset_snapshot: DatasetSnapshot,
    metrics: Mapping[str, float],
    baseline_metrics: Mapping[str, float],
    thresholds: Sequence[MetricThreshold],
    segment_metrics: Sequence[SegmentMetric] = (),
    calibration_summary: Mapping[str, Any] | None = None,
    min_training_records: int = 1,
    validation_run_id: str | None = None,
) -> ValidationRun:
    failures: list[ValidationRuleFailure] = []
    worst_status = ValidationStatus.PASSED

    if dataset_snapshot.training_record_count < min_training_records:
        failures.append(
            ValidationRuleFailure(
                "min_training_records",
                ValidationStatus.FAILED,
                f"training record count below minimum {min_training_records}",
            )
        )
        worst_status = ValidationStatus.FAILED

    for threshold in thresholds:
        if threshold.metric_name not in metrics:
            failures.append(
                ValidationRuleFailure(
                    threshold.metric_name,
                    ValidationStatus.FAILED,
                    f"{threshold.metric_name} missing from metrics",
                )
            )
            worst_status = ValidationStatus.FAILED
            continue
        status, message = threshold.evaluate(float(metrics[threshold.metric_name]))
        if status is ValidationStatus.PASSED:
            continue
        failures.append(
            ValidationRuleFailure(threshold.metric_name, status, message or threshold.metric_name)
        )
        if status is ValidationStatus.FAILED:
            worst_status = ValidationStatus.FAILED
        elif worst_status is ValidationStatus.PASSED:
            worst_status = ValidationStatus.WARNING

    return ValidationRun(
        validation_run_id=validation_run_id or f"validation-{uuid4()}",
        model_name=model_name,
        model_version=model_version,
        dataset_snapshot_id=dataset_snapshot.dataset_snapshot_id,
        status=worst_status,
        metrics=dict(metrics),
        baseline_metrics=dict(baseline_metrics),
        segment_metrics=tuple(segment_metrics),
        calibration_summary=dict(calibration_summary or {}),
        failed_rules=tuple(failures),
    )


__all__ = [
    "MetricThreshold",
    "SegmentMetric",
    "ValidationRuleFailure",
    "ValidationRun",
    "ValidationStatus",
    "validate_model_candidate",
]
