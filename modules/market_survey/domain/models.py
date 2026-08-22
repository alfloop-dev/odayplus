"""Market Survey Domain Models and Contracts.

Contract: `odayplus.survey-workflow.v2`.
Task ID: `ODP-SURVEY-001`.

Core Responsibilities:
1. Model field survey assignments, evidence records, reviews, corrections, and promotions.
2. Invariant: Platform survey ingestion provides evidence snapshots, NOT automatic ground truth.
3. Invariant: Segregation of duties / Reviewer separation (submitter cannot review own survey).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

SURVEY_WORKFLOW_CONTRACT = "odayplus.survey-workflow.v2"
SURVEY_WORKFLOW_VERSION = "2.0.0"
REQUIRED_FACADE_CONTRACT = "odayplus.market-data-facade.v2"
REQUIRED_EVIDENCE_CONTRACT = "emgi.field-survey.v1"


class AssignmentStatus(StrEnum):
    """Lifecycle state of a field-survey assignment."""

    UNASSIGNED = "UNASSIGNED"
    ASSIGNED = "ASSIGNED"
    CLAIMED = "CLAIMED"
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class EvidenceReviewStatus(StrEnum):
    """Governance review status of a field survey observation/evidence."""

    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_REVISION = "NEEDS_REVISION"


class SurveyLifecycleKind(StrEnum):
    """Evidentiary lifecycle action represented by this observation."""

    INITIAL = "INITIAL"
    CORRECTION = "CORRECTION"
    RESURVEY = "RESURVEY"


class SurveyType(StrEnum):
    """Functional classification of the survey campaign or questionnaire."""

    PHYSICAL_FEASIBILITY = "PHYSICAL_FEASIBILITY"
    STORE_AUDIT = "STORE_AUDIT"
    CANDIDATE_SITE = "CANDIDATE_SITE"
    COMPETITOR_POLL = "COMPETITOR_POLL"
    FOOT_TRAFFIC = "FOOT_TRAFFIC"
    MERCHANT_VERIFICATION = "MERCHANT_VERIFICATION"
    CUSTOM = "CUSTOM"


class TargetEntityKind(StrEnum):
    """Category of entity targeted by the field survey."""

    CANDIDATE_SITE = "CANDIDATE_SITE"
    STORE = "STORE"
    POI = "POI"
    PROPERTY = "PROPERTY"
    STREET_SEGMENT = "STREET_SEGMENT"
    COMPETITOR = "COMPETITOR"
    CUSTOM = "CUSTOM"


class MediaKind(StrEnum):
    """Kind of media attachment associated with the survey."""

    PHOTO = "PHOTO"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    DOCUMENT = "DOCUMENT"
    SIGNATURE = "SIGNATURE"
    OTHER = "OTHER"


class PromotionStatus(StrEnum):
    """Status of promoting survey evidence into canonical operational truth."""

    NOT_PROMOTED = "NOT_PROMOTED"
    PROMOTION_REQUESTED = "PROMOTION_REQUESTED"
    VALIDATING = "VALIDATING"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class SurveyErrorCode(StrEnum):
    """Machine-readable error codes for domain and authorization failures."""

    SELF_REVIEW_DENIED = "SELF_REVIEW_DENIED"
    UNAUTHORIZED_REVIEWER = "UNAUTHORIZED_REVIEWER"
    ASSIGNMENT_EXPIRED = "ASSIGNMENT_EXPIRED"
    ASSIGNMENT_NOT_ACTIVE = "ASSIGNMENT_NOT_ACTIVE"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    TENANT_SCOPE_MISMATCH = "TENANT_SCOPE_MISMATCH"
    NOT_APPROVED_FOR_PROMOTION = "NOT_APPROVED_FOR_PROMOTION"
    SUPERSEDED_EVIDENCE = "SUPERSEDED_EVIDENCE"
    RETRACTED_EVIDENCE = "RETRACTED_EVIDENCE"
    EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"
    ASSIGNMENT_NOT_FOUND = "ASSIGNMENT_NOT_FOUND"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    VALIDATION_FAILED = "VALIDATION_FAILED"


class SurveyDomainError(Exception):
    """Base exception for market survey domain errors."""

    def __init__(
        self,
        code: SurveyErrorCode,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"[{code.value}] {message}")
        self.code = code
        self.message = message
        self.details = dict(details) if details else {}


class SurveyValidationError(SurveyDomainError):
    """Raised when domain invariant or payload validation fails."""

    def __init__(
        self,
        message: str,
        code: SurveyErrorCode = SurveyErrorCode.VALIDATION_FAILED,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, details=details)


class SurveyAuthorizationError(SurveyDomainError, PermissionError):
    """Raised when reviewer separation, role check or tenant isolation fails."""

    def __init__(
        self,
        message: str,
        code: SurveyErrorCode = SurveyErrorCode.UNAUTHORIZED_REVIEWER,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, details=details)


class SurveyNotFoundError(SurveyDomainError, LookupError):
    """Raised when an assignment or survey evidence entity cannot be found."""

    def __init__(
        self,
        message: str,
        code: SurveyErrorCode = SurveyErrorCode.EVIDENCE_NOT_FOUND,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, details=details)


class SurveyStateConflictError(SurveyDomainError):
    """Raised when an operation conflicts with the current lifecycle state."""

    def __init__(
        self,
        message: str,
        code: SurveyErrorCode = SurveyErrorCode.INVALID_STATE_TRANSITION,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, details=details)


@dataclass(frozen=True, slots=True)
class SurveyLocation:
    """Spatial coordinate and address anchor for the survey."""

    latitude: float
    longitude: float
    address: str | None = None
    h3_index: str | None = None
    srid: int = 4326

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SurveyLocation:
        return cls(
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"]),
            address=data.get("address"),
            h3_index=data.get("h3_index"),
            srid=int(data.get("srid", 4326)),
        )

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "srid": self.srid,
        }
        if self.address is not None:
            res["address"] = self.address
        if self.h3_index is not None:
            res["h3_index"] = self.h3_index
        return res


@dataclass(frozen=True, slots=True)
class MediaAttachment:
    """Evidentiary media artifact attached to the survey submission."""

    blob_id: str
    captured_at: str
    media_id: str
    media_kind: MediaKind
    sha256: str
    storage_uri: str
    caption: str | None = None
    media_type: str = "image/jpeg"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MediaAttachment:
        return cls(
            blob_id=str(data["blob_id"]),
            captured_at=str(data["captured_at"]),
            media_id=str(data["media_id"]),
            media_kind=MediaKind(data["media_kind"]),
            sha256=str(data["sha256"]),
            storage_uri=str(data["storage_uri"]),
            caption=data.get("caption"),
            media_type=str(data.get("media_type", "image/jpeg")),
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "blob_id": self.blob_id,
            "captured_at": self.captured_at,
            "media_id": self.media_id,
            "media_kind": self.media_kind.value,
            "sha256": self.sha256,
            "storage_uri": self.storage_uri,
            "media_type": self.media_type,
            "metadata": self.metadata,
        }
        if self.caption is not None:
            res["caption"] = self.caption
        return res


@dataclass(frozen=True, slots=True)
class SurveyReviewRecord:
    """Independent review and governance lineage record."""

    review_status: EvidenceReviewStatus
    reviewer_id: str
    reviewed_at: str
    review_checklist: dict[str, Any] = field(default_factory=dict)
    review_comment: str | None = None
    conditions: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SurveyReviewRecord:
        return cls(
            review_status=EvidenceReviewStatus(data["review_status"]),
            reviewer_id=str(data["reviewer_id"]),
            reviewed_at=str(data["reviewed_at"]),
            review_checklist=dict(data.get("review_checklist", {})),
            review_comment=data.get("review_comment"),
            conditions=list(data.get("conditions", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "review_status": self.review_status.value,
            "reviewer_id": self.reviewer_id,
            "reviewed_at": self.reviewed_at,
            "review_checklist": self.review_checklist,
            "conditions": self.conditions,
        }
        if self.review_comment is not None:
            res["review_comment"] = self.review_comment
        return res


@dataclass(slots=True)
class SurveyAssignment:
    """Assigned field-survey task with SLA, assignee and lifecycle state."""

    assignment_id: str
    tenant_id: str
    campaign_id: str
    target_entity_id: str
    target_entity_kind: TargetEntityKind
    survey_type: SurveyType
    status: AssignmentStatus
    expires_at: str
    created_at: str
    created_by: str
    assigned_to: str | None = None
    assigned_by: str | None = None
    assigned_at: str | None = None
    claimed_at: str | None = None
    submitted_at: str | None = None
    survey_id: str | None = None
    instructions: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SurveyAssignment:
        return cls(
            assignment_id=str(data["assignment_id"]),
            tenant_id=str(data["tenant_id"]),
            campaign_id=str(data["campaign_id"]),
            target_entity_id=str(data["target_entity_id"]),
            target_entity_kind=TargetEntityKind(data["target_entity_kind"]),
            survey_type=SurveyType(data["survey_type"]),
            status=AssignmentStatus(data["status"]),
            expires_at=str(data["expires_at"]),
            created_at=str(data["created_at"]),
            created_by=str(data["created_by"]),
            assigned_to=data.get("assigned_to"),
            assigned_by=data.get("assigned_by"),
            assigned_at=data.get("assigned_at"),
            claimed_at=data.get("claimed_at"),
            submitted_at=data.get("submitted_at"),
            survey_id=data.get("survey_id"),
            instructions=dict(data.get("instructions", {})),
            metadata=dict(data.get("metadata", {})),
            updated_at=data.get("updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment_id": self.assignment_id,
            "tenant_id": self.tenant_id,
            "campaign_id": self.campaign_id,
            "target_entity_id": self.target_entity_id,
            "target_entity_kind": self.target_entity_kind.value,
            "survey_type": self.survey_type.value,
            "status": self.status.value,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "assigned_to": self.assigned_to,
            "assigned_by": self.assigned_by,
            "assigned_at": self.assigned_at,
            "claimed_at": self.claimed_at,
            "submitted_at": self.submitted_at,
            "survey_id": self.survey_id,
            "instructions": self.instructions,
            "metadata": self.metadata,
            "updated_at": self.updated_at or self.created_at,
        }


@dataclass(slots=True)
class FieldSurveyEvidence:
    """Field-survey observation treated strictly as evidence under odayplus governance."""

    survey_id: str
    tenant_id: str
    blob_id: str
    campaign_id: str
    target_entity_id: str
    target_entity_kind: TargetEntityKind
    survey_type: SurveyType
    lifecycle_kind: SurveyLifecycleKind
    submitter_id: str
    surveyed_at: str
    submitted_at: str
    location: SurveyLocation
    observation_id: str
    attributes: dict[str, Any] = field(default_factory=dict)
    media_attachments: list[MediaAttachment] = field(default_factory=list)
    confidence: float | None = 1.0
    assignment_id: str | None = None
    review_status: EvidenceReviewStatus = EvidenceReviewStatus.PENDING_REVIEW
    review_record: SurveyReviewRecord | None = None
    replaces_survey_id: str | None = None
    replaces_observation_id: str | None = None
    is_superseded: bool = False
    is_retracted: bool = False
    retraction_reason: str | None = None
    promotion_status: PromotionStatus = PromotionStatus.NOT_PROMOTED
    promoted_at: str | None = None
    promoted_by: str | None = None
    promoted_target_ref: str | None = None
    promotion_result: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FieldSurveyEvidence:
        review_record = None
        if data.get("review_record"):
            review_record = SurveyReviewRecord.from_dict(data["review_record"])
        elif data.get("review"):
            review_record = SurveyReviewRecord.from_dict(data["review"])

        return cls(
            survey_id=str(data["survey_id"]),
            tenant_id=str(data["tenant_id"]),
            blob_id=str(data["blob_id"]),
            campaign_id=str(data["campaign_id"]),
            target_entity_id=str(data["target_entity_id"]),
            target_entity_kind=TargetEntityKind(data["target_entity_kind"]),
            survey_type=SurveyType(data["survey_type"]),
            lifecycle_kind=SurveyLifecycleKind(data["lifecycle_kind"]),
            submitter_id=str(data["submitter_id"]),
            surveyed_at=str(data["surveyed_at"]),
            submitted_at=str(data["submitted_at"]),
            location=SurveyLocation.from_dict(data["location"]),
            observation_id=str(data["observation_id"]),
            attributes=dict(data.get("attributes", {})),
            media_attachments=[MediaAttachment.from_dict(m) for m in data.get("media_attachments", [])],
            confidence=float(data["confidence"]) if data.get("confidence") is not None else 1.0,
            assignment_id=data.get("assignment_id"),
            review_status=EvidenceReviewStatus(data.get("review_status", EvidenceReviewStatus.PENDING_REVIEW)),
            review_record=review_record,
            replaces_survey_id=data.get("replaces_survey_id"),
            replaces_observation_id=data.get("replaces_observation_id"),
            is_superseded=bool(data.get("is_superseded", False)),
            is_retracted=bool(data.get("is_retracted", False)),
            retraction_reason=data.get("retraction_reason"),
            promotion_status=PromotionStatus(data.get("promotion_status", PromotionStatus.NOT_PROMOTED)),
            promoted_at=data.get("promoted_at"),
            promoted_by=data.get("promoted_by"),
            promoted_target_ref=data.get("promoted_target_ref"),
            promotion_result=dict(data.get("promotion_result", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", datetime.now(UTC).isoformat())),
            updated_at=str(data.get("updated_at", datetime.now(UTC).isoformat())),
        )

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "survey_id": self.survey_id,
            "tenant_id": self.tenant_id,
            "blob_id": self.blob_id,
            "campaign_id": self.campaign_id,
            "target_entity_id": self.target_entity_id,
            "target_entity_kind": self.target_entity_kind.value,
            "survey_type": self.survey_type.value,
            "lifecycle_kind": self.lifecycle_kind.value,
            "submitter_id": self.submitter_id,
            "surveyed_at": self.surveyed_at,
            "submitted_at": self.submitted_at,
            "location": self.location.to_dict(),
            "observation_id": self.observation_id,
            "attributes": self.attributes,
            "media_attachments": [m.to_dict() for m in self.media_attachments],
            "confidence": self.confidence,
            "assignment_id": self.assignment_id,
            "review_status": self.review_status.value,
            "review_record": self.review_record.to_dict() if self.review_record else None,
            "replaces_survey_id": self.replaces_survey_id,
            "replaces_observation_id": self.replaces_observation_id,
            "is_superseded": self.is_superseded,
            "is_retracted": self.is_retracted,
            "retraction_reason": self.retraction_reason,
            "promotion_status": self.promotion_status.value,
            "promoted_at": self.promoted_at,
            "promoted_by": self.promoted_by,
            "promoted_target_ref": self.promoted_target_ref,
            "promotion_result": self.promotion_result,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        return res


@dataclass(frozen=True, slots=True)
class SurveyCorrection:
    """Correction or resurvey audit linkage."""

    correction_id: str
    tenant_id: str
    original_survey_id: str
    new_survey_id: str
    reason: str
    corrected_by: str
    corrected_at: str
    delta_attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "correction_id": self.correction_id,
            "tenant_id": self.tenant_id,
            "original_survey_id": self.original_survey_id,
            "new_survey_id": self.new_survey_id,
            "reason": self.reason,
            "corrected_by": self.corrected_by,
            "corrected_at": self.corrected_at,
            "delta_attributes": self.delta_attributes,
        }


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    """Record of promoting survey evidence into canonical operational entities."""

    promotion_id: str
    tenant_id: str
    survey_id: str
    target_entity_id: str
    target_entity_kind: TargetEntityKind
    promoted_by: str
    promoted_at: str
    target_entity_type: str
    target_entity_ref: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "promotion_id": self.promotion_id,
            "tenant_id": self.tenant_id,
            "survey_id": self.survey_id,
            "target_entity_id": self.target_entity_id,
            "target_entity_kind": self.target_entity_kind.value,
            "promoted_by": self.promoted_by,
            "promoted_at": self.promoted_at,
            "target_entity_type": self.target_entity_type,
            "target_entity_ref": self.target_entity_ref,
            "payload": self.payload,
        }


__all__ = [
    "REQUIRED_EVIDENCE_CONTRACT",
    "REQUIRED_FACADE_CONTRACT",
    "SURVEY_WORKFLOW_CONTRACT",
    "SURVEY_WORKFLOW_VERSION",
    "AssignmentStatus",
    "EvidenceReviewStatus",
    "FieldSurveyEvidence",
    "MediaAttachment",
    "MediaKind",
    "PromotionRecord",
    "PromotionStatus",
    "SurveyAssignment",
    "SurveyCorrection",
    "SurveyDomainError",
    "SurveyErrorCode",
    "SurveyLifecycleKind",
    "SurveyLocation",
    "SurveyNotFoundError",
    "SurveyReviewRecord",
    "SurveyStateConflictError",
    "SurveyType",
    "SurveyValidationError",
    "TargetEntityKind",
]
