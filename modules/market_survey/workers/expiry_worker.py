"""Market Survey SLA and Expiry Background Worker.

Contract: `odayplus.survey-workflow.v2`.
Task ID: `ODP-SURVEY-001`.

Periodically evaluates active survey assignments against their expiration deadlines (SLA)
and transitions overdue assignments to EXPIRED status while emitting audit events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from modules.market_survey.application.survey_service import MarketSurveyService


@dataclass(frozen=True, slots=True)
class SurveyExpirySweepResult:
    """Result of a survey expiry sweep operation."""

    checked_at: str
    expired_count: int
    expired_assignment_ids: list[str] = field(default_factory=list)
    tenant_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "expired_count": self.expired_count,
            "expired_assignment_ids": self.expired_assignment_ids,
            "tenant_id": self.tenant_id,
        }


def run_survey_expiry_sweep(
    service: MarketSurveyService,
    *,
    tenant_id: str | None = None,
    now: datetime | None = None,
) -> SurveyExpirySweepResult:
    """Execute a single sweep across assignments to expire overdue tasks."""
    current_time = now or datetime.now(UTC)
    expired_assignments = service.check_and_expire_assignments(tenant_id=tenant_id, now=current_time)

    return SurveyExpirySweepResult(
        checked_at=current_time.isoformat(),
        expired_count=len(expired_assignments),
        expired_assignment_ids=[a.assignment_id for a in expired_assignments],
        tenant_id=tenant_id,
    )


class SurveyExpiryWorker:
    """Worker for recurring survey SLA expiry sweeps."""

    def __init__(self, service: MarketSurveyService) -> None:
        self.service = service

    def run_once(
        self,
        *,
        tenant_id: str | None = None,
        now: datetime | None = None,
    ) -> SurveyExpirySweepResult:
        return run_survey_expiry_sweep(self.service, tenant_id=tenant_id, now=now)


__all__ = [
    "SurveyExpirySweepResult",
    "SurveyExpiryWorker",
    "run_survey_expiry_sweep",
]
