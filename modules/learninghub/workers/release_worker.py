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
from modules.learninghub.domain import MonitoringEvaluation


@dataclass
class LearningHubReleaseWorker:
    service: LearningHubService

    def run_release(self, payload: dict[str, Any]) -> ModelReleaseDecision:
        if "expected_release_revision" not in payload:
            raise ValueError("release worker requires expected_release_revision")
        if not str(payload.get("idempotency_key") or "").strip():
            raise ValueError("release worker requires idempotency_key")
        # Actors are never defaulted: a queued release carries the identities
        # that the API bound from its authenticated caller and approval record.
        if not str(payload.get("requested_by") or "").strip():
            raise ValueError("release worker requires requested_by")
        if not str(payload.get("approved_by") or "").strip():
            raise ValueError("release worker requires approved_by")
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
            requested_by=str(payload["requested_by"]),
            approved_by=str(payload["approved_by"]),
            correlation_id=str(payload.get("correlation_id", "learninghub-release")),
            expected_release_revision=int(payload["expected_release_revision"]),
            idempotency_key=str(payload["idempotency_key"]),
            release_scope=str(payload.get("release_scope", "global")),
            tenant_id=(
                str(payload["tenant_id"]) if payload.get("tenant_id") is not None else None
            ),
        )

    def recover_releases(self, payload: dict[str, Any]) -> tuple[Any, ...]:
        return self.service.recover_incomplete_releases(
            model_name=(
                str(payload["model_name"])
                if payload.get("model_name") is not None
                else None
            ),
            release_id=(
                str(payload["release_id"])
                if payload.get("release_id") is not None
                else None
            ),
            requested_by=str(payload.get("requested_by", "release-recovery")),
        )

    def run_monitor(self, payload: dict[str, Any]) -> ReleaseMonitorAssessment:
        guardrails = tuple(
            MetricThreshold(
                metric_name=str(item["metric_name"]),
                min_value=item.get("min_value"),
                max_value=item.get("max_value"),
                warning_min_value=item.get("warning_min_value"),
                warning_max_value=item.get("warning_max_value"),
                max_degradation=item.get("max_degradation"),
                max_relative_degradation=item.get("max_relative_degradation"),
                warning_max_degradation=item.get("warning_max_degradation"),
                warning_max_relative_degradation=item.get("warning_max_relative_degradation"),
                higher_is_better=item.get("higher_is_better"),
            )
            for item in payload.get("guardrails", ())
        )
        return self.service.monitor_release(
            release_id=str(payload["release_id"]),
            observed_metrics=dict(payload.get("observed_metrics", {})),
            guardrails=guardrails,
            baseline_metrics=(
                dict(payload["baseline_metrics"])
                if payload.get("baseline_metrics") is not None
                else None
            ),
            evaluated_by=str(payload.get("evaluated_by", "release-monitor")),
            correlation_id=str(payload.get("correlation_id", "learninghub-monitor")),
        )

    def run_prediction_drift(self, payload: dict[str, Any]) -> MonitoringEvaluation:
        """Production worker entry for prediction-output drift monitoring."""

        policy = payload.get("decision_policy") or payload.get("policy")
        if policy is None:
            raise ValueError("prediction drift worker requires decision_policy")
        return self.service.monitor_prediction_drift(
            model_name=str(payload["model_name"]),
            model_version=str(payload["model_version"]),
            reference_rows=tuple(payload.get("reference_rows", ())),
            current_rows=tuple(payload.get("current_rows", ())),
            reference_snapshot_id=str(payload["reference_snapshot_id"]),
            current_snapshot_id=str(payload["current_snapshot_id"]),
            cohort_key=str(payload["cohort_key"]),
            prediction_columns=tuple(payload.get("prediction_columns", ())),
            output_types=(
                dict(payload["output_types"])
                if payload.get("output_types") is not None
                else None
            ),
            policy=policy,
            requested_by=str(payload.get("requested_by", "system")),
            reason=(str(payload["reason"]) if payload.get("reason") is not None else None),
        )


def run_learninghub_release(
    payload: dict[str, Any], *, service: LearningHubService
) -> ModelReleaseDecision:
    return LearningHubReleaseWorker(service=service).run_release(payload)


def run_learninghub_release_monitor(
    payload: dict[str, Any], *, service: LearningHubService
) -> ReleaseMonitorAssessment:
    return LearningHubReleaseWorker(service=service).run_monitor(payload)


def run_learninghub_prediction_drift(
    payload: dict[str, Any], *, service: LearningHubService
) -> MonitoringEvaluation:
    return LearningHubReleaseWorker(service=service).run_prediction_drift(payload)


def run_learninghub_release_recovery(
    payload: dict[str, Any], *, service: LearningHubService
) -> tuple[Any, ...]:
    return LearningHubReleaseWorker(service=service).recover_releases(payload)


__all__ = [
    "LearningHubReleaseWorker",
    "run_learninghub_release",
    "run_learninghub_release_recovery",
    "run_learninghub_release_monitor",
    "run_learninghub_prediction_drift",
]
