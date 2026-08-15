"""Release monitoring for the Learning Hub.

A release is approved with a ``monitoring_window`` plus ``success_criteria`` /
``fail_criteria``. This module evaluates guardrail metrics observed during that
window against structured thresholds and produces an audited assessment.

Consistent with the platform's "never optimistic" governance stance, a breach
*recommends* a rollback (surfaced to the Rollback Console for human approval); it
never auto-executes a stage change. The actual rollback stays an explicit,
approved :meth:`LearningHubService.request_release` call.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from models.shared_ml.validation import MetricThreshold, ValidationStatus


class MonitorStatus(StrEnum):
    HEALTHY = "HEALTHY"
    BREACHED = "BREACHED"


class RecommendedAction(StrEnum):
    NONE = "NONE"
    ROLLBACK = "ROLLBACK"


@dataclass(frozen=True)
class GuardrailBreach:
    metric_name: str
    observed: float
    status: ValidationStatus
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "observed": self.observed,
            "status": self.status.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ReleaseMonitorAssessment:
    release_id: str
    model_name: str
    version: str
    status: MonitorStatus
    recommended_action: RecommendedAction
    observed_metrics: Mapping[str, float]
    breaches: tuple[GuardrailBreach, ...]
    monitoring_window: str
    rollback_target: str | None
    evaluated_at: datetime
    audit_event_id: str | None = None

    @property
    def is_healthy(self) -> bool:
        return self.status is MonitorStatus.HEALTHY

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "model_name": self.model_name,
            "version": self.version,
            "status": self.status.value,
            "recommended_action": self.recommended_action.value,
            "observed_metrics": dict(self.observed_metrics),
            "breaches": [breach.to_dict() for breach in self.breaches],
            "monitoring_window": self.monitoring_window,
            "rollback_target": self.rollback_target,
            "evaluated_at": self.evaluated_at.isoformat(),
            "audit_event_id": self.audit_event_id,
        }


def evaluate_guardrails(
    observed_metrics: Mapping[str, float],
    guardrails: Sequence[MetricThreshold],
) -> tuple[GuardrailBreach, ...]:
    """Return one breach per guardrail whose observed metric fails its threshold.

    Guardrails whose metric is absent from ``observed_metrics`` are skipped (the
    window has not produced that signal yet); WARNING-only bands are not treated
    as breaches so the monitor stays fail-closed only on hard limits.
    """

    breaches: list[GuardrailBreach] = []
    for guardrail in guardrails:
        if guardrail.metric_name not in observed_metrics:
            continue
        value = float(observed_metrics[guardrail.metric_name])
        status, detail = guardrail.evaluate(value)
        if status is ValidationStatus.FAILED:
            breaches.append(
                GuardrailBreach(
                    metric_name=guardrail.metric_name,
                    observed=value,
                    status=status,
                    detail=detail or f"{guardrail.metric_name} breached guardrail",
                )
            )
    return tuple(breaches)


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "GuardrailBreach",
    "MonitorStatus",
    "RecommendedAction",
    "ReleaseMonitorAssessment",
    "evaluate_guardrails",
    "utcnow",
]
