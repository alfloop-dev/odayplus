"""R4 Operator Console DTOs.

These Pydantic models form the stable public contract between the Operator API
layer and its consumers (frontend, tests, openapi-client).

Rules:
- Every write DTO must include actorRoleId + actorName (audit identity).
- High-risk write bodies must require reason (non-empty string).
- Idempotency-Key is handled at the HTTP layer; not included in these models.
- All status fields use string literals matching the TypeScript type unions in
  apps/web/features/operator/types.ts to guarantee cross-layer consistency.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# Shared audit identity fields (mixed into every write body)
# ---------------------------------------------------------------------------

class ActorIdentity(BaseModel):
    """Audit identity fields required on every write action."""

    actorRoleId: str
    actorName: str | None = None


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

ISSUE_ACTION_TYPES = Literal["triage", "assign", "actions", "outcome"]

ISSUE_STATUS_BY_ACTION: dict[str, str] = {
    "triage": "triaged",
    "assign": "assigned",
    "actions": "inprogress",
    "outcome": "closed",
}


class IssueTransitionRequest(ActorIdentity):
    """Write body for POST /operator/issues/{issue_id}/{action_type}."""

    issueId: str | None = None
    status: str | None = None
    note: str | None = None

    @field_validator("note")
    @classmethod
    def note_stripped(cls, v: str | None) -> str | None:
        return v.strip() if v else v


class IssueTransitionResponse(BaseModel):
    """Response envelope for issue transition writes."""

    issueId: str
    newStatus: str
    auditEventId: str
    correlationId: str | None = None


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

APPROVAL_DECISION_STATUSES = Literal["approved", "returned", "rejected"]


class ApprovalDecisionRequest(ActorIdentity):
    """Write body for POST /operator/approvals/{approval_id}/decision.

    reason is required for all decisions to enforce audit traceability.
    High-risk approvals additionally validate non-empty reason at the
    service layer.
    """

    status: APPROVAL_DECISION_STATUSES
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("reason must not be empty for approval decisions")
        return v


class ApprovalDecisionResponse(BaseModel):
    """Response envelope for approval decision writes."""

    approvalId: str
    newStatus: str
    auditEventId: str
    correlationId: str | None = None


# ---------------------------------------------------------------------------
# Evidence purpose unlock
# ---------------------------------------------------------------------------


class EvidencePurposeRequest(ActorIdentity):
    """Write body for POST /operator/evidence/{evidence_id}/purpose.

    privacyAcknowledged must be True for camera evidence kinds.
    retentionHours must not exceed the policy ceiling (72 h default).
    """

    purpose: str
    cameraLocation: str | None = None
    timeWindow: str | None = None
    retentionHours: int | None = None
    privacyAcknowledged: bool | None = None
    auditNote: str | None = None

    @field_validator("purpose")
    @classmethod
    def purpose_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("purpose must not be empty")
        return v

    @field_validator("retentionHours")
    @classmethod
    def retention_within_policy(cls, v: int | None) -> int | None:
        if v is not None and v > 72:
            raise ValueError("retentionHours must not exceed the 72-hour policy ceiling")
        return v


class EvidencePurposeResponse(BaseModel):
    """Response envelope for evidence purpose unlock."""

    evidenceId: str
    purpose: str
    auditEventId: str
    correlationId: str | None = None


__all__ = [
    "ActorIdentity",
    "ISSUE_ACTION_TYPES",
    "ISSUE_STATUS_BY_ACTION",
    "IssueTransitionRequest",
    "IssueTransitionResponse",
    "APPROVAL_DECISION_STATUSES",
    "ApprovalDecisionRequest",
    "ApprovalDecisionResponse",
    "EvidencePurposeRequest",
    "EvidencePurposeResponse",
]
