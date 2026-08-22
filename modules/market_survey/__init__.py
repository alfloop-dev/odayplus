"""Market Survey Module Public API.

Contract: `odayplus.survey-workflow.v2`.
Task ID: `ODP-SURVEY-001`.

Provides field survey assignment, reviewer separation governance, observation ingestion
as evidence snapshots (not ground truth), corrections, SLA expiry, and promotion to canonical truth.
"""

from __future__ import annotations

from modules.market_survey.application import (
    MarketSurveyService,
    PlatformSurveyFacadeAdapter,
)
from modules.market_survey.domain import (
    ALLOWED_REVIEW_ROLES,
    REQUIRED_EVIDENCE_CONTRACT,
    REQUIRED_FACADE_CONTRACT,
    SURVEY_WORKFLOW_CONTRACT,
    SURVEY_WORKFLOW_VERSION,
    AssignmentStatus,
    EvidenceReviewStatus,
    FieldSurveyEvidence,
    MediaAttachment,
    MediaKind,
    PromotionRecord,
    PromotionStatus,
    SurveyAssignment,
    SurveyAssignmentStateMachine,
    SurveyAuthorizationError,
    SurveyCorrection,
    SurveyDomainError,
    SurveyErrorCode,
    SurveyLifecycleKind,
    SurveyLocation,
    SurveyNotFoundError,
    SurveyPromotionStateMachine,
    SurveyReviewRecord,
    SurveyReviewStateMachine,
    SurveyStateConflictError,
    SurveyType,
    SurveyValidationError,
    TargetEntityKind,
)
from modules.market_survey.infrastructure import (
    InMemorySurveyRepository,
    SurveyRepository,
)
from modules.market_survey.workers import (
    SurveyExpirySweepResult,
    SurveyExpiryWorker,
    run_survey_expiry_sweep,
)

__all__ = [
    "ALLOWED_REVIEW_ROLES",
    "REQUIRED_EVIDENCE_CONTRACT",
    "REQUIRED_FACADE_CONTRACT",
    "SURVEY_WORKFLOW_CONTRACT",
    "SURVEY_WORKFLOW_VERSION",
    "AssignmentStatus",
    "EvidenceReviewStatus",
    "FieldSurveyEvidence",
    "InMemorySurveyRepository",
    "MarketSurveyService",
    "MediaAttachment",
    "MediaKind",
    "PlatformSurveyFacadeAdapter",
    "PromotionRecord",
    "PromotionStatus",
    "SurveyAssignment",
    "SurveyAssignmentStateMachine",
    "SurveyAuthorizationError",
    "SurveyCorrection",
    "SurveyDomainError",
    "SurveyErrorCode",
    "SurveyExpirySweepResult",
    "SurveyExpiryWorker",
    "SurveyLifecycleKind",
    "SurveyLocation",
    "SurveyNotFoundError",
    "SurveyPromotionStateMachine",
    "SurveyRepository",
    "SurveyReviewRecord",
    "SurveyReviewStateMachine",
    "SurveyStateConflictError",
    "SurveyType",
    "SurveyValidationError",
    "TargetEntityKind",
    "run_survey_expiry_sweep",
]
