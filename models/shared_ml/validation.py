from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from modules.learninghub.domain.dataset_snapshot import DatasetSnapshot
    from shared.governance.decision_policy import DecisionPolicy


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
    max_degradation: float | None = None
    max_relative_degradation: float | None = None
    warning_max_degradation: float | None = None
    warning_max_relative_degradation: float | None = None
    higher_is_better: bool | None = None

    def evaluate(
        self,
        value: float,
        baseline_value: float | None = None,
    ) -> tuple[ValidationStatus, str | None]:
        # 1. Absolute hard thresholds
        if self.min_value is not None and value < self.min_value:
            return ValidationStatus.FAILED, f"{self.metric_name} below minimum {self.min_value}"
        if self.max_value is not None and value > self.max_value:
            return ValidationStatus.FAILED, f"{self.metric_name} above maximum {self.max_value}"

        higher = (
            self.higher_is_better
            if self.higher_is_better is not None
            else (self.min_value is not None or self.warning_min_value is not None or self.max_value is None)
        )

        # 2. Hard baseline degradation thresholds (ODP-FR-LH-005 performance drift)
        if baseline_value is not None:
            degradation = (baseline_value - value) if higher else (value - baseline_value)
            if self.max_degradation is not None and degradation > self.max_degradation:
                return (
                    ValidationStatus.FAILED,
                    f"{self.metric_name} degradation {degradation:.4f} exceeds maximum allowed {self.max_degradation}",
                )
            if self.max_relative_degradation is not None and baseline_value != 0:
                rel_degradation = degradation / abs(baseline_value)
                if rel_degradation > self.max_relative_degradation:
                    return (
                        ValidationStatus.FAILED,
                        f"{self.metric_name} relative degradation {rel_degradation:.4f} exceeds maximum allowed {self.max_relative_degradation}",
                    )

        # 3. Absolute warning thresholds
        if self.warning_min_value is not None and value < self.warning_min_value:
            return (
                ValidationStatus.WARNING,
                f"{self.metric_name} below warning {self.warning_min_value}",
            )
        if self.warning_max_value is not None and value > self.warning_max_value:
            return (
                ValidationStatus.WARNING,
                f"{self.metric_name} above warning {self.warning_max_value}",
            )

        # 4. Warning baseline degradation thresholds
        if baseline_value is not None:
            degradation = (baseline_value - value) if higher else (value - baseline_value)
            if self.warning_max_degradation is not None and degradation > self.warning_max_degradation:
                return (
                    ValidationStatus.WARNING,
                    f"{self.metric_name} degradation {degradation:.4f} exceeds warning {self.warning_max_degradation}",
                )
            if self.warning_max_relative_degradation is not None and baseline_value != 0:
                rel_degradation = degradation / abs(baseline_value)
                if rel_degradation > self.warning_max_relative_degradation:
                    return (
                        ValidationStatus.WARNING,
                        f"{self.metric_name} relative degradation {rel_degradation:.4f} exceeds warning {self.warning_max_relative_degradation}",
                    )

        return ValidationStatus.PASSED, None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "warning_min_value": self.warning_min_value,
            "warning_max_value": self.warning_max_value,
            "max_degradation": self.max_degradation,
            "max_relative_degradation": self.max_relative_degradation,
            "warning_max_degradation": self.warning_max_degradation,
            "warning_max_relative_degradation": self.warning_max_relative_degradation,
            "higher_is_better": self.higher_is_better,
        }


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
class SegmentMetricThreshold:
    segment_name: str
    metric_name: str
    segment_value: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    warning_min_value: float | None = None
    warning_max_value: float | None = None
    max_degradation: float | None = None
    max_relative_degradation: float | None = None
    warning_max_degradation: float | None = None
    warning_max_relative_degradation: float | None = None
    higher_is_better: bool | None = None

    @property
    def rule_name(self) -> str:
        segment = self.segment_value if self.segment_value is not None else "*"
        return f"segment:{self.segment_name}:{segment}:{self.metric_name}"

    def applies_to(self, segment_metric: SegmentMetric) -> bool:
        if segment_metric.segment_name != self.segment_name:
            return False
        return self.segment_value in (None, segment_metric.segment_value)

    def evaluate(
        self,
        segment_metric: SegmentMetric,
        baseline_segment_metric: SegmentMetric | None = None,
    ) -> tuple[ValidationStatus, str | None]:
        if self.metric_name not in segment_metric.metrics:
            return (
                ValidationStatus.FAILED,
                f"{self.metric_name} missing for {segment_metric.segment_name}="
                f"{segment_metric.segment_value}",
            )
        baseline_value = (
            float(baseline_segment_metric.metrics[self.metric_name])
            if baseline_segment_metric and self.metric_name in baseline_segment_metric.metrics
            else None
        )
        threshold = MetricThreshold(
            metric_name=self.metric_name,
            min_value=self.min_value,
            max_value=self.max_value,
            warning_min_value=self.warning_min_value,
            warning_max_value=self.warning_max_value,
            max_degradation=self.max_degradation,
            max_relative_degradation=self.max_relative_degradation,
            warning_max_degradation=self.warning_max_degradation,
            warning_max_relative_degradation=self.warning_max_relative_degradation,
            higher_is_better=self.higher_is_better,
        )
        status, message = threshold.evaluate(
            float(segment_metric.metrics[self.metric_name]),
            baseline_value=baseline_value,
        )
        if message:
            message = f"{segment_metric.segment_name}={segment_metric.segment_value}: {message}"
        return status, message

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_name": self.segment_name,
            "metric_name": self.metric_name,
            "segment_value": self.segment_value,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "warning_min_value": self.warning_min_value,
            "warning_max_value": self.warning_max_value,
            "max_degradation": self.max_degradation,
            "max_relative_degradation": self.max_relative_degradation,
            "warning_max_degradation": self.warning_max_degradation,
            "warning_max_relative_degradation": self.warning_max_relative_degradation,
            "higher_is_better": self.higher_is_better,
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
    thresholds: Sequence[MetricThreshold] = ()
    segment_metrics: Sequence[SegmentMetric] = ()
    calibration_summary: Mapping[str, Any] = field(default_factory=dict)
    failed_rules: Sequence[ValidationRuleFailure] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decision_policy_version_id: str | None = None

    @property
    def passed(self) -> bool:
        return self.status is ValidationStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "validation_run_id": self.validation_run_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "status": self.status.value,
            "metrics": dict(self.metrics),
            "baseline_metrics": dict(self.baseline_metrics),
            "thresholds": [
                threshold.to_dict() if hasattr(threshold, "to_dict") else threshold
                for threshold in self.thresholds
            ],
            "segment_metrics": [metric.to_dict() for metric in self.segment_metrics],
            "calibration_summary": dict(self.calibration_summary),
            "failed_rules": [failure.to_dict() for failure in self.failed_rules],
            "created_at": self.created_at.isoformat(),
        }
        if self.decision_policy_version_id is not None:
            data["decision_policy_version_id"] = self.decision_policy_version_id
        return data


def thresholds_from_decision_policy(
    policy: DecisionPolicy,
    *,
    model_name: str | None = None,
) -> list[MetricThreshold]:
    """Extract MetricThreshold list from a governed DecisionPolicy.

    ODP-FR-LH-005: Performance drift and validation regression limits are governed
    by versioned decision policies rather than hardcoded magic numbers.
    """
    if not (policy.reads("observed_metrics") or policy.reads("metrics") or policy.reads("baseline_metrics")):
        raise ValueError(
            f"policy {policy.policy_version_id} does not declare reading 'observed_metrics', 'metrics' or 'baseline_metrics'"
        )
    thresholds: list[MetricThreshold] = []
    default_max_deg = policy.parameters.get("default_max_degradation")
    default_max_rel_deg = policy.parameters.get("default_max_relative_degradation")
    default_warning_max_deg = policy.parameters.get("default_warning_max_degradation")
    default_warning_max_rel_deg = policy.parameters.get("default_warning_max_relative_degradation")

    metric_configs = policy.parameters.get("metric_thresholds", {})
    by_model = policy.parameters.get("metric_thresholds_by_model")
    if by_model is not None:
        if not isinstance(by_model, Mapping) or not model_name:
            raise ValueError(
                f"policy {policy.policy_version_id} requires a model name for threshold resolution"
            )
        if model_name not in by_model:
            raise ValueError(
                f"policy {policy.policy_version_id} has no threshold rows for model {model_name}"
            )
        metric_configs = by_model[model_name]
    if not isinstance(metric_configs, Mapping):
        raise ValueError(
            f"policy {policy.policy_version_id} metric thresholds must be an object"
        )
    for metric_name, cfg in metric_configs.items():
        if isinstance(cfg, dict):
            thresholds.append(
                MetricThreshold(
                    metric_name=metric_name,
                    min_value=cfg.get("min_value"),
                    max_value=cfg.get("max_value"),
                    warning_min_value=cfg.get("warning_min_value"),
                    warning_max_value=cfg.get("warning_max_value"),
                    max_degradation=cfg.get("max_degradation", default_max_deg),
                    max_relative_degradation=cfg.get("max_relative_degradation", default_max_rel_deg),
                    warning_max_degradation=cfg.get("warning_max_degradation", default_warning_max_deg),
                    warning_max_relative_degradation=cfg.get("warning_max_relative_degradation", default_warning_max_rel_deg),
                    higher_is_better=cfg.get("higher_is_better"),
                )
            )
    return thresholds


def effective_thresholds(
    thresholds: Sequence[MetricThreshold],
    decision_policy: DecisionPolicy | None,
    *,
    model_name: str | None = None,
) -> list[MetricThreshold]:
    """Resolve validation thresholds with the governing policy as authority.

    A caller may still provide model-specific threshold arguments while a
    migration is being completed, but it must not be able to weaken a
    threshold from a recorded policy.  Policy rows are authoritative for each
    matching metric; a caller may only contribute a stricter bound or an
    additional metric gate.
    """
    if decision_policy is None:
        return list(thresholds)
    governed = thresholds_from_decision_policy(decision_policy, model_name=model_name)
    caller_by_metric: dict[str, MetricThreshold] = {
        threshold.metric_name: threshold for threshold in thresholds
    }
    effective: list[MetricThreshold] = []
    for policy_threshold in governed:
        caller_threshold = caller_by_metric.get(policy_threshold.metric_name)
        if caller_threshold is None:
            effective.append(policy_threshold)
            continue
        effective.append(
            MetricThreshold(
                metric_name=policy_threshold.metric_name,
                min_value=_stricter_min(
                    policy_threshold.min_value, caller_threshold.min_value
                ),
                max_value=_stricter_max(
                    policy_threshold.max_value, caller_threshold.max_value
                ),
                warning_min_value=_stricter_min(
                    policy_threshold.warning_min_value,
                    caller_threshold.warning_min_value,
                ),
                warning_max_value=_stricter_max(
                    policy_threshold.warning_max_value,
                    caller_threshold.warning_max_value,
                ),
                max_degradation=_stricter_max_degradation(
                    policy_threshold.max_degradation,
                    caller_threshold.max_degradation,
                ),
                max_relative_degradation=_stricter_max_degradation(
                    policy_threshold.max_relative_degradation,
                    caller_threshold.max_relative_degradation,
                ),
                warning_max_degradation=_stricter_max_degradation(
                    policy_threshold.warning_max_degradation,
                    caller_threshold.warning_max_degradation,
                ),
                warning_max_relative_degradation=_stricter_max_degradation(
                    policy_threshold.warning_max_relative_degradation,
                    caller_threshold.warning_max_relative_degradation,
                ),
                higher_is_better=(
                    policy_threshold.higher_is_better
                    if policy_threshold.higher_is_better is not None
                    else caller_threshold.higher_is_better
                ),
            )
        )
    governed_names = {threshold.metric_name for threshold in governed}
    effective.extend(
        threshold for threshold in thresholds if threshold.metric_name not in governed_names
    )
    return effective


def _stricter_min(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _stricter_max(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _stricter_max_degradation(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def validate_model_candidate(
    *,
    model_name: str,
    model_version: str,
    dataset_snapshot: DatasetSnapshot,
    metrics: Mapping[str, float],
    baseline_metrics: Mapping[str, float],
    thresholds: Sequence[MetricThreshold],
    segment_metrics: Sequence[SegmentMetric] = (),
    segment_thresholds: Sequence[SegmentMetricThreshold] = (),
    baseline_segment_metrics: Sequence[SegmentMetric] = (),
    calibration_summary: Mapping[str, Any] | None = None,
    min_training_records: int = 1,
    validation_run_id: str | None = None,
    decision_policy: DecisionPolicy | None = None,
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

    effective = effective_thresholds(
        thresholds,
        decision_policy,
        model_name=model_name,
    )

    for threshold in effective:
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
        baseline_value = (
            float(baseline_metrics[threshold.metric_name])
            if threshold.metric_name in baseline_metrics
            else None
        )
        status, message = threshold.evaluate(
            float(metrics[threshold.metric_name]),
            baseline_value=baseline_value,
        )
        if status is ValidationStatus.PASSED:
            continue
        failures.append(
            ValidationRuleFailure(threshold.metric_name, status, message or threshold.metric_name)
        )
        if status is ValidationStatus.FAILED:
            worst_status = ValidationStatus.FAILED
        elif worst_status is ValidationStatus.PASSED:
            worst_status = ValidationStatus.WARNING

    for segment_threshold in segment_thresholds:
        matched = False
        for segment_metric in segment_metrics:
            if not segment_threshold.applies_to(segment_metric):
                continue
            matched = True
            baseline_segment_metric = next(
                (
                    bm
                    for bm in baseline_segment_metrics
                    if bm.segment_name == segment_metric.segment_name
                    and bm.segment_value == segment_metric.segment_value
                ),
                None,
            )
            status, message = segment_threshold.evaluate(
                segment_metric,
                baseline_segment_metric=baseline_segment_metric,
            )
            if status is ValidationStatus.PASSED:
                continue
            failures.append(
                ValidationRuleFailure(
                    segment_threshold.rule_name,
                    status,
                    message or segment_threshold.rule_name,
                )
            )
            if status is ValidationStatus.FAILED:
                worst_status = ValidationStatus.FAILED
            elif worst_status is ValidationStatus.PASSED:
                worst_status = ValidationStatus.WARNING
        if not matched:
            failures.append(
                ValidationRuleFailure(
                    segment_threshold.rule_name,
                    ValidationStatus.FAILED,
                    f"missing segment metric for {segment_threshold.segment_name}",
                )
            )
            worst_status = ValidationStatus.FAILED

    return ValidationRun(
        validation_run_id=validation_run_id or f"validation-{uuid4()}",
        model_name=model_name,
        model_version=model_version,
        dataset_snapshot_id=dataset_snapshot.dataset_snapshot_id,
        status=worst_status,
        metrics=dict(metrics),
        baseline_metrics=dict(baseline_metrics),
        thresholds=tuple(effective),
        segment_metrics=tuple(segment_metrics),
        calibration_summary=dict(calibration_summary or {}),
        failed_rules=tuple(failures),
        decision_policy_version_id=decision_policy.policy_version_id if decision_policy else None,
    )


__all__ = [
    "MetricThreshold",
    "SegmentMetric",
    "SegmentMetricThreshold",
    "ValidationRuleFailure",
    "ValidationRun",
    "ValidationStatus",
    "effective_thresholds",
    "thresholds_from_decision_policy",
    "validate_model_candidate",
]
