from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models.shared_ml.validation import MetricThreshold
from modules.learninghub.application import (
    LearningHubService,
    ModelReleaseDecision,
    ReleaseMonitorAssessment,
    ReleaseType,
)


@dataclass
class LearningHubReleaseWorker:
    service: LearningHubService

    def run_release(self, payload: dict[str, Any]) -> ModelReleaseDecision:
        return self.service.request_release(
            model_name=str(payload["model_name"]),
            version=str(payload["version"]),
            release_type=ReleaseType(str(payload["release_type"])),
            reason=str(payload["reason"]),
            approval_id=str(payload["approval_id"]),
            rollback_target=payload.get("rollback_target"),
            monitoring_window=str(payload.get("monitoring_window", "24h")),
            success_criteria=tuple(payload.get("success_criteria", ())),
            fail_criteria=tuple(payload.get("fail_criteria", ())),
            affected_modules=tuple(payload.get("affected_modules", ())),
            requested_by=str(payload.get("requested_by", "system")),
            approved_by=str(payload.get("approved_by", "model-review-board")),
            correlation_id=str(payload.get("correlation_id", "learninghub-release")),
        )

    def run_monitor(self, payload: dict[str, Any]) -> ReleaseMonitorAssessment:
        guardrails = tuple(
            MetricThreshold(
                metric_name=str(item["metric_name"]),
                min_value=item.get("min_value"),
                max_value=item.get("max_value"),
                warning_min_value=item.get("warning_min_value"),
                warning_max_value=item.get("warning_max_value"),
            )
            for item in payload.get("guardrails", ())
        )
        return self.service.monitor_release(
            release_id=str(payload["release_id"]),
            observed_metrics=dict(payload.get("observed_metrics", {})),
            guardrails=guardrails,
            evaluated_by=str(payload.get("evaluated_by", "release-monitor")),
            correlation_id=str(payload.get("correlation_id", "learninghub-monitor")),
        )


def run_learninghub_release(
    payload: dict[str, Any], *, service: LearningHubService
) -> ModelReleaseDecision:
    return LearningHubReleaseWorker(service=service).run_release(payload)


def run_learninghub_release_monitor(
    payload: dict[str, Any], *, service: LearningHubService
) -> ReleaseMonitorAssessment:
    return LearningHubReleaseWorker(service=service).run_monitor(payload)


__all__ = [
    "LearningHubReleaseWorker",
    "run_learninghub_release",
    "run_learninghub_release_monitor",
]
