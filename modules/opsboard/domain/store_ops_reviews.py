"""Typed operator submissions; these records do not certify measured outcomes or send replies."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class OutcomeSubmission(_Submission):
    outcome: Literal["effective", "ineffective", "inconclusive"]
    impactSummary: str = Field(min_length=1)
    evidenceSummary: str
    closeIssue: bool
    followUpTarget: Literal["storeOps", "growth", "network", "govern"] | None = None
    followUpAction: str | None = None

    @model_validator(mode="after")
    def validate_follow_up(self) -> Self:
        if self.outcome != "effective":
            if self.closeIssue or not self.followUpTarget or not self.followUpAction:
                raise ValueError("Ineffective or inconclusive outcomes require an open issue and follow-up")
        return self


class ReplyReviewSubmission(_Submission):
    channel: Literal["google", "customerService"]
    decision: Literal["approve", "return", "reject"]
    draftReply: str = Field(min_length=1)
    reviewerNote: str | None = None
    publishAfterApproval: bool

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.decision != "approve" and not self.reviewerNote:
            raise ValueError("Returned or rejected replies require a reviewer note")
        if self.publishAfterApproval and self.decision != "approve":
            raise ValueError("Only an approved reply can request publication")
        return self


class _ReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: str
    issueId: str
    storeId: str
    actorSubjectId: str | None
    actorName: str
    actorRoleId: str
    actorEvidence: Literal["authenticated_principal", "caller_supplied"]
    recordedAt: str
    correlationId: str
    workflowAuditId: str
    auditEventId: str
    sourceIssueUpdatedAt: str
    evidenceIds: list[str]

    @field_validator("recordedAt", "sourceIssueUpdatedAt")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("Review source timestamps require an explicit offset")
        return value

    @model_validator(mode="after")
    def validate_actor_evidence(self) -> Self:
        if self.actorEvidence == "authenticated_principal":
            if not self.actorSubjectId or not self.actorSubjectId.strip():
                raise ValueError("Authenticated actor evidence requires a subject")
        elif self.actorSubjectId is not None:
            raise ValueError("Caller-supplied actor labels cannot certify a subject")
        return self


class OutcomeReviewRecord(_ReviewRecord):
    submission: OutcomeSubmission


class ReplyReviewRecord(_ReviewRecord):
    submission: ReplyReviewSubmission
    deliveryStatus: Literal["not_sent"] = "not_sent"


class TriageSubmission(_Submission):
    severity: Literal["low", "medium", "high", "critical"] | None = None
    category: Literal["service", "cleanliness", "staffing", "device", "payment", "multiSignal"] | None = None
    evidenceStrength: Literal["weak", "usable", "strong"] | None = None
    decision: Literal["accept", "needEvidence", "fastForward"] | None = None
    observationWindow: str | None = None
    needEvidence: bool | None = None
    demoFastForward: bool | None = None
    notes: str | None = None


class AssignSubmission(_Submission):
    ownerRoleId: str | None = None
    ownerName: str | None = None
    slaDueAt: str | None = None
    handoffNote: str | None = None

    @field_validator("slaDueAt")
    @classmethod
    def validate_due_time(cls, value: str | None) -> str | None:
        if value is not None and datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("An explicit due time requires an offset")
        return value


class ActionSubmission(_Submission):
    actionType: Literal["staffBriefing", "cleaningCheck", "customerCallback", "iotRestart", "approvalRequest", "remoteRestart"] | None = None
    title: str | None = None
    instructions: str | None = None
    checklistItems: list[str] | None = None
    needEvidence: bool | None = None
    requiresApproval: bool | None = None
    observationWindow: str | None = None
    remoteRestartAuditNote: str | None = None


class FieldReportSubmission(_Submission):
    reportedBy: str | None = None
    observedAt: str | None = None
    summary: str | None = None
    checklistStatus: Literal["complete", "partial", "blocked"] | None = None
    attachmentNames: list[str] | None = None
    blocker: str | None = None

    @field_validator("observedAt")
    @classmethod
    def validate_observation_time(cls, value: str | None) -> str | None:
        if value is not None and datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("An explicit observation time requires an offset")
        return value


class EscalationSubmission(_Submission):
    target: Literal["growth", "network", "govern"] | None = None
    urgency: Literal["normal", "high", "critical"] | None = None
    reason: str | None = None
    requestedOutcome: str | None = None
    notifyOwner: bool | None = None


class TransferSubmission(_Submission):
    targetRoleId: str | None = None
    targetOwnerName: str | None = None
    reason: str | None = None
    handoffNote: str | None = None
    keepWatching: bool | None = None


class CameraPurposeSubmission(_Submission):
    purpose: str = Field(min_length=1)
    cameraLocation: str | None = None
    timeWindow: str | None = None
    retentionHours: int | None = Field(default=None, ge=1, le=72)
    privacyAcknowledged: bool | None = None
    auditNote: str | None = None


class TriageRecord(_ReviewRecord):
    action: Literal["triage"] = "triage"
    submission: TriageSubmission
    clockAdvanced: Literal[False] = False


class AssignRecord(_ReviewRecord):
    action: Literal["assign"] = "assign"
    submission: AssignSubmission
    assigneeIdentityStatus: Literal["not_verified"] = "not_verified"


class ActionRecord(_ReviewRecord):
    action: Literal["actions"] = "actions"
    submission: ActionSubmission
    executionStatus: Literal["not_executed"] = "not_executed"
    approvalRequestStatus: Literal["not_created"] = "not_created"


class FieldReportRecord(_ReviewRecord):
    action: Literal["field-report"] = "field-report"
    submission: FieldReportSubmission
    reporterIdentityStatus: Literal["not_verified"] = "not_verified"
    attachmentEvidenceStatus: Literal["not_verified"] = "not_verified"


class EscalationRecord(_ReviewRecord):
    action: Literal["escalate"] = "escalate"
    submission: EscalationSubmission
    notificationStatus: Literal["not_sent"] = "not_sent"
    routingStatus: Literal["not_dispatched"] = "not_dispatched"


class TransferRecord(_ReviewRecord):
    action: Literal["transfer"] = "transfer"
    submission: TransferSubmission
    assigneeIdentityStatus: Literal["not_verified"] = "not_verified"
    watchSubscriptionStatus: Literal["not_created"] = "not_created"


class CameraPurposeRecord(_ReviewRecord):
    action: Literal["camera-purpose"] = "camera-purpose"
    submission: CameraPurposeSubmission


WORKFLOW_RECORD_TYPES = {
    "triage": TriageRecord,
    "assign": AssignRecord,
    "actions": ActionRecord,
    "field-report": FieldReportRecord,
    "escalate": EscalationRecord,
    "transfer": TransferRecord,
    "camera-purpose": CameraPurposeRecord,
}


def record_contract(history_key: str, record: dict) -> tuple[type[_ReviewRecord], str]:
    if not isinstance(record, dict):
        raise ValueError("Store Ops persisted record must be an object")
    if history_key == "outcomeReviews":
        return OutcomeReviewRecord, "issue.outcome"
    if history_key == "replyReviews":
        return ReplyReviewRecord, "issue.reply-review"
    action = record.get("action")
    if history_key != "workflowRecords" or action not in WORKFLOW_RECORD_TYPES:
        raise ValueError("Unknown Store Ops persisted workflow record")
    return WORKFLOW_RECORD_TYPES[action], (
        "evidence.camera_purpose.recorded" if action == "camera-purpose" else "issue." + action
    )
