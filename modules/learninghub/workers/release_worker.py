from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from modules.learninghub.application import LearningHubService, ModelReleaseDecision, ReleaseType


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


def run_learninghub_release(
    payload: dict[str, Any], *, service: LearningHubService
) -> ModelReleaseDecision:
    return LearningHubReleaseWorker(service=service).run_release(payload)


__all__ = ["LearningHubReleaseWorker", "run_learninghub_release"]
