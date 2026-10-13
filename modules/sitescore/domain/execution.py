"""Mechanical command identity for a fresh score; no human approval vocabulary."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreExecutionCommand:
    candidate_site_id: str
    expected_report_id: str
    expected_report_version: int
    expected_job_version: int
    reason: str
    idempotency_key: str
    correlation_id: str

    def validate(self) -> None:
        from modules.listing.application.intake_score_delivery import ScoreDeliveryValidationError
        if (not all(isinstance(v, str) and v.strip() for v in (
                self.candidate_site_id, self.expected_report_id, self.reason,
                self.idempotency_key, self.correlation_id))
                or type(self.expected_report_version) is not int or self.expected_report_version < 1
                or type(self.expected_job_version) is not int or self.expected_job_version < 1):
            raise ScoreDeliveryValidationError("SITESCORE_EXECUTION_COMMAND_REQUIRED")
