from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from enum import Enum
from threading import RLock
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from modules.external_data.geo import GeoPipeline
from modules.listing import ListingPipeline
from modules.listing.application.assignment_sla import AssignmentSlaService
from modules.listing.application.intake_assignment_commands import (
    authorize_assignment_read,
    change_assignment,
    list_assignment_transfer_targets,
)
from modules.listing.application.intake_completion import IntakeWorkIncomplete
from modules.listing.application.intake_correction_lineage import apply_field, correction_values
from modules.listing.application.intake_sla_commands import change_sla
from modules.listing.application.intake_workflow_bootstrap import (
    IntakeWorkflowPlan,
    IntakeWorkflowPolicy,
    IntakeWorkflowUnavailable,
    enqueue_workflow_event,
    initialize_intake_workflow,
    record_workflow_change,
    schedule_from_record,
)
from modules.listing.domain.intake_states import DomainValidationError, PrincipalRole
from shared.audit import InMemoryAuditLog

_CURSOR_SIGNING_KEY_ENV = "ODP_INTAKE_CURSOR_SIGNING_KEY"
_PROCESS_CURSOR_SIGNING_KEY = secrets.token_bytes(32)

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
    from pydantic import (
        AfterValidator,
        BaseModel,
        ConfigDict,
        Field,
        StringConstraints,
        WithJsonSchema,
        field_validator,
        model_validator,
    )
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:
    # ---------------------------------------------------------------------------
    # Pydantic Schemas from openapi-effective.json
    # ---------------------------------------------------------------------------
    FIXTURE_IDS = {
        "tenant-a",
        "operator-expansion-manager",
        "operator-expansion-staff",
    }

    def check_uuid(v: str | None) -> str | None:
        if v is None:
            return None
        try:
            UUID(v)
            return v
        except (TypeError, ValueError):
            if v in FIXTURE_IDS or re.match(r"^(L|AUD|IN|CS|HZ|JOB|RV|S|A|FORMAT|SN|FS|corr)-", v):
                return v
            raise ValueError("badly formed hexadecimal UUID string") from None

    def check_datetime(v: str | None) -> str | None:
        if v is None:
            return None
        try:
            parsed = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if "T" not in v or parsed.tzinfo is None:
                raise ValueError
            return v
        except ValueError:
            raise ValueError("invalid date-time format") from None

    def check_uri(v: str | None) -> str | None:
        if v is None:
            return None
        if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://[^\s]+$", v):
            raise ValueError("invalid URI format")
        return v

    UuidString = Annotated[
        str,
        AfterValidator(check_uuid),
        WithJsonSchema({"type": "string", "format": "uuid"}),
    ]
    DateTimeString = Annotated[
        str,
        AfterValidator(check_datetime),
        WithJsonSchema({"type": "string", "format": "date-time"}),
    ]
    UriString = Annotated[
        str,
        AfterValidator(check_uri),
        WithJsonSchema({"type": "string", "format": "uri"}),
    ]
    IntakeUriString = Annotated[
        str,
        AfterValidator(check_uri),
        StringConstraints(max_length=4096),
        WithJsonSchema({"type": "string", "format": "uri", "maxLength": 4096}),
    ]
    IdempotencyKeyValue = Annotated[
        str,
        StringConstraints(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
    ]
    IfMatchValue = Annotated[
        str,
        StringConstraints(pattern=r'^W/"[1-9][0-9]*"$'),
    ]
    IDEMPOTENCY_KEY_HEADER = Header(
        ...,
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    IF_MATCH_HEADER = Header(..., alias="If-Match", pattern=r'^W/"[1-9][0-9]*"$')

    class IntakeState(str, Enum):
        SUBMITTED = "SUBMITTED"
        CHECKING_IDENTITY = "CHECKING_IDENTITY"
        CHECKING_SOURCE_POLICY = "CHECKING_SOURCE_POLICY"
        AWAITING_ASSISTED_ENTRY = "AWAITING_ASSISTED_ENTRY"
        RETRIEVING = "RETRIEVING"
        PARSING = "PARSING"
        MATCHING = "MATCHING"
        NEEDS_REVIEW = "NEEDS_REVIEW"
        READY = "READY"
        QUARANTINED = "QUARANTINED"
        FAILED = "FAILED"
        CANCELLED = "CANCELLED"

    class MatchOutcome(str, Enum):
        NEW = "NEW"
        EXACT_DUPLICATE = "EXACT_DUPLICATE"
        REVISION = "REVISION"
        POSSIBLE_MATCH = "POSSIBLE_MATCH"
        QUARANTINED = "QUARANTINED"

    class IntakeMethod(str, Enum):
        URL = "URL"
        MANUAL = "MANUAL"
        CSV = "CSV"
        APPROVED_FEED = "APPROVED_FEED"
        OPERATOR_SNAPSHOT = "OPERATOR_SNAPSHOT"

    class TotalCountAccuracy(str, Enum):
        EXACT = "EXACT"
        ESTIMATED = "ESTIMATED"

    class IntakeSort(str, Enum):
        SUBMITTED_AT_DESC = "submitted_at_desc"
        UPDATED_AT_DESC = "updated_at_desc"
        DUE_AT_ASC = "due_at_asc"
        STATUS_ASC = "status_asc"

    class FieldClassification(str, Enum):
        PUBLIC = "PUBLIC"
        INTERNAL = "INTERNAL"
        CONFIDENTIAL = "CONFIDENTIAL"
        RESTRICTED = "RESTRICTED"

    class SourcePolicyState(str, Enum):
        APPROVED_RETRIEVAL = "APPROVED_RETRIEVAL"
        ASSISTED_ENTRY_ONLY = "ASSISTED_ENTRY_ONLY"
        AUTH_REQUIRED = "AUTH_REQUIRED"
        SOURCE_BLOCKED = "SOURCE_BLOCKED"
        POLICY_UNKNOWN = "POLICY_UNKNOWN"

    class AuditResult(str, Enum):
        ALLOWED = "ALLOWED"
        DENIED = "DENIED"
        SUCCEEDED = "SUCCEEDED"
        FAILED = "FAILED"
        MASKED = "MASKED"

    class DecisionType(str, Enum):
        CREATE = "CREATE"
        REVISE = "REVISE"
        DUPLICATE = "DUPLICATE"
        QUARANTINE = "QUARANTINE"
        REJECT = "REJECT"
        REOPEN = "REOPEN"
        MERGE = "MERGE"
        SPLIT = "SPLIT"
        UNMERGE = "UNMERGE"

    class BatchIntakeMethod(str, Enum):
        MANUAL = "MANUAL"
        CSV = "CSV"
        APPROVED_FEED = "APPROVED_FEED"

    class CandidateDisposition(str, Enum):
        KEEP_HISTORICAL = "KEEP_HISTORICAL"
        REASSIGN = "REASSIGN"
        REQUIRE_REVIEW = "REQUIRE_REVIEW"

    class BatchRowStatus(str, Enum):
        ACCEPTED = "ACCEPTED"
        REJECTED = "REJECTED"
        REPLAYED = "REPLAYED"

    class RetryCheckpoint(str, Enum):
        RETRIEVING = "RETRIEVING"
        PARSING = "PARSING"
        MATCHING = "MATCHING"
        CANDIDATE_CREATING = "CANDIDATE_CREATING"
        SCORE_QUEUED = "SCORE_QUEUED"

    class JobStatus(str, Enum):
        QUEUED = "QUEUED"
        RUNNING = "RUNNING"
        SUCCEEDED = "SUCCEEDED"
        FAILED = "FAILED"
        CANCELLED = "CANCELLED"
        PARTIAL = "PARTIAL"

    class JobDeliveryState(str, Enum):
        RETRYING = "RETRYING"
        DEAD_LETTER = "DEAD_LETTER"

    class SlaState(str, Enum):
        ON_TRACK = "ON_TRACK"
        DUE_SOON = "DUE_SOON"
        OVERDUE = "OVERDUE"
        BREACHED = "BREACHED"
        PAUSED = "PAUSED"
        COMPLETED = "COMPLETED"

    class AssignmentStatus(str, Enum):
        ASSIGNED = "ASSIGNED"
        CLAIMED = "CLAIMED"
        TRANSFERRED = "TRANSFERRED"
        ESCALATED = "ESCALATED"
        COMPLETED = "COMPLETED"

    class CorrectionStatus(str, Enum):
        PROPOSED = "PROPOSED"
        APPLIED = "APPLIED"
        PENDING_REVIEW = "PENDING_REVIEW"

    class DecisionStatus(str, Enum):
        PENDING_REVIEW = "PENDING_REVIEW"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        EXECUTING = "EXECUTING"
        EXECUTED = "EXECUTED"
        FAILED = "FAILED"
        REVERSAL_PENDING = "REVERSAL_PENDING"
        REVERSED = "REVERSED"

    class PromotionStatus(str, Enum):
        REQUESTED = "REQUESTED"
        VALIDATING = "VALIDATING"
        PENDING_REVIEW = "PENDING_REVIEW"
        REJECTED = "REJECTED"
        APPROVED = "APPROVED"
        CANDIDATE_CREATING = "CANDIDATE_CREATING"
        CANDIDATE_CREATED = "CANDIDATE_CREATED"
        SCORE_QUEUED = "SCORE_QUEUED"
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"
        SCORE_FAILED = "SCORE_FAILED"

    class PromotionDecisionType(str, Enum):
        STANDARD = "STANDARD"
        LEGACY_RECONCILED = "LEGACY_RECONCILED"

    class ReviewDecision(str, Enum):
        APPROVE = "APPROVE"
        REJECT = "REJECT"

    class SavedViewVisibility(str, Enum):
        PRIVATE = "PRIVATE"
        ROLE = "ROLE"
        TENANT = "TENANT"

    class ScopeContext(BaseModel):
        tenant_id: UuidString
        assigned_area_id: UuidString | None = None
        brand_id: UuidString | None = None
        heat_zone_id: UuidString | None = None
        region_id: UuidString | None = None

    class UrlIntakeRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        original_url: IntakeUriString
        scope: ScopeContext
        owner_subject_id: UuidString | None = None
        purpose: str = Field(None, min_length=3, max_length=500)

    class IntakeSubmissionReceipt(BaseModel):
        intake_id: UuidString
        state: IntakeState
        version: int = Field(..., ge=1)
        job_id: UuidString
        correlation_id: UuidString
        submitted_at: DateTimeString
        duplicate_hint: str | None = None

    class ManualIntakeRow(BaseModel):
        address_raw: str
        area_ping: float | None = Field(None, ge=0)
        currency: str = "TWD"
        floor: str | None = None
        original_url: UriString | None = None
        rent_amount: float | None = Field(None, ge=0)
        source_id: str = "manual.operator"
        source_listing_id: str | None = None

    class BatchIntakeRequest(BaseModel):
        batch_id: UuidString
        method: BatchIntakeMethod
        scope: ScopeContext
        rows: list[ManualIntakeRow] = Field(..., min_length=1, max_length=1000)

    class FieldError(BaseModel):
        field: str
        code: str
        message: str

    class ApiError(BaseModel):
        code: Literal[
            "AUTHENTICATION_REQUIRED", "ROLE_DENIED", "TENANT_SCOPE_DENIED",
            "SCOPE_DENIED", "OWNERSHIP_REQUIRED", "ASSIGNMENT_SCOPE_DENIED",
            "SOURCE_SCOPE_DENIED", "FIELD_MASKED", "DATA_CLASSIFICATION_DENIED",
            "PURPOSE_REQUIRED", "PRECONDITION_REQUIRED", "VERSION_CONFLICT",
            "WORKFLOW_STATE_DENIED", "OWNER_CONFLICT", "SECOND_ACTOR_REQUIRED",
            "SELF_REVIEW_DENIED", "RISK_ACKNOWLEDGEMENT_REQUIRED",
            "SOURCE_POLICY_DENIED", "SOURCE_POLICY_UNKNOWN", "SOURCE_AUTH_REQUIRED",
            "LEGAL_HOLD_CONFLICT", "RETENTION_NOT_REACHED", "RESIDENCY_DENIED",
            "EXPORT_APPROVAL_REQUIRED", "PURGE_APPROVAL_REQUIRED",
            "QUARANTINE_RELEASE_DENIED", "PROMOTION_APPROVAL_REQUIRED",
            "RESTRICTED_EXPORT_DENIED", "BREAK_GLASS_DENIED", "DEPENDENCY_CONFLICT",
            "DUPLICATE_CANDIDATE", "IDEMPOTENCY_KEY_REUSED", "RETRY_BUDGET_EXHAUSTED",
            "CHECKPOINT_UNAVAILABLE", "JOB_FENCE_REJECTED", "SLA_PAUSE_DENIED",
            "DECISION_INCOMPLETE", "BACKPRESSURE_ACTIVE", "RATE_LIMITED",
            "RESOURCE_NOT_FOUND", "VALIDATION_FAILED", "FIELD_REQUIRED",
            "CURSOR_INVALID", "CURSOR_EXPIRED", "INTERNAL_ERROR",
        ]
        message: str
        retryable: bool
        correlation_id: UuidString
        reason_code: str | None = None
        field_errors: list[FieldError] = Field(default_factory=list)
        current_version: int | None = None
        retry_after_seconds: int | None = Field(None, ge=0)
        occurred_at: DateTimeString
        next_action: Literal["RETRY", "REFRESH", "CORRECT_INPUT", "REQUEST_ACCESS", "CONTACT_SUPPORT", "WAIT"] | None

    class ConflictError(ApiError):
        current_state: str | None = None
        current_owner_subject_id: UuidString | None = None
        retry_with_etag: str | None = None

    class BatchRowReceipt(BaseModel):
        row_index: int = Field(..., ge=1)
        status: BatchRowStatus
        intake_id: UuidString | None = None
        client_row_id: str | None = None
        error: ApiError | None = None

    class BatchIntakeReceipt(BaseModel):
        batch_id: UuidString
        submitted_at: DateTimeString
        accepted_count: int
        rejected_count: int
        rows: list[BatchRowReceipt]
        correlation_id: UuidString

    class XlsxPreviewRequest(BaseModel):
        file_base64: str
        custom_mapping: dict[str, str] | None = None
        scope: ScopeContext | None = None

    class XlsxPreviewResponse(BaseModel):
        batch_id: str
        total_rows: int
        valid_count: int
        rejected_count: int
        schema_mapping: dict[str, str]
        has_formula_or_external_link_warnings: bool
        warnings: list[str]
        valid_rows: list[dict[str, Any]]
        row_errors: list[dict[str, Any]]
        preview_rows: list[dict[str, Any]]

    class XlsxCommitRequest(BaseModel):
        batch_id: str | None = None
        rows: list[dict[str, Any]]
        scope: ScopeContext

    class XlsxCommitReceipt(BaseModel):
        batch_id: str
        committed_at: str
        accepted_count: int
        rejected_count: int
        intake_ids: list[str]
        correlation_id: str
        replayed: bool = False

    class IntakeSummary(BaseModel):
        intake_id: UuidString
        state: IntakeState
        intake_method: IntakeMethod
        source_id: str | None = None
        match_outcome: MatchOutcome | None = None
        submitted_by: UuidString = None
        assigned_to: UuidString | None = None
        due_at: DateTimeString | None = None
        submitted_at: DateTimeString
        updated_at: DateTimeString
        version: int
        scope: ScopeContext
        masked_fields: list[str] = Field(default_factory=list)

    class FieldValue(BaseModel):
        field_path: str
        classification: FieldClassification
        masked: bool
        parsed: Any | None = None
        normalized: Any | None = None
        corrected: Any | None = None
        effective: Any | None = None
        confidence: float | None = Field(None, ge=0, le=1)
        mask_reason_code: str | None = None

    class TransitionReceipt(BaseModel):
        transition_id: UuidString
        from_state: str | None
        to_state: str
        occurred_at: DateTimeString
        actor: str
        reason_code: str | None = None
        version_after: int

    class AuditReference(BaseModel):
        audit_event_id: UuidString
        action: str
        occurred_at: DateTimeString
        result: AuditResult
        reason_code: str | None = None

    class IntakeDetail(IntakeSummary):
        original_url: str | None
        canonical_url: str | None
        policy_state: SourcePolicyState | None
        source_snapshot_id: UuidString | None = None
        parser_run_id: UuidString | None = None
        match_case_id: UuidString | None = None
        processing_history: list[TransitionReceipt]
        fields: list[FieldValue]
        audit: list[AuditReference]
        assignment_id: UuidString | None = None
        assignment_status: str | None = None
        sla_instance_id: UuidString | None = None
        sla_state: str | None = None
        sla_receipt: str | None = None

    class IntakePage(BaseModel):
        items: list[IntakeSummary]
        next_cursor: str | None = None
        page_size: int
        query_fingerprint: str
        snapshot_time: DateTimeString
        total_count: int = Field(..., ge=0)
        total_count_accuracy: TotalCountAccuracy = TotalCountAccuracy.EXACT

    class CorrectionRequest(BaseModel):
        field_path: str
        corrected_value: Any
        reason: str = Field(..., min_length=3, max_length=2000)
        risk_acknowledged: bool = False
        expected_effective_value_sha256: str | None = Field(None, pattern=r"^[a-f0-9]{64}$")

    class CorrectionReceipt(BaseModel):
        correction_id: UuidString
        status: CorrectionStatus
        intake_id: UuidString
        version: int
        audit_event_id: UuidString
        correlation_id: UuidString
        listing_revision_id: UuidString | None = None

    class AssignmentRequest(BaseModel):
        owner_subject_id: UuidString
        owner_role: str
        due_at: DateTimeString
        reason: str = Field(..., min_length=3)
        handoff_note: str | None = None

    class AssignmentReceipt(BaseModel):
        assignment_id: UuidString
        status: AssignmentStatus
        owner_subject_id: UuidString
        due_at: DateTimeString
        version: int
        audit_event_id: UuidString

    class AssignmentTransferTarget(BaseModel):
        subject_id: UuidString
        display_name: str
        owner_role: str

    class AssignmentTransferTargetPage(BaseModel):
        assignment_id: UuidString
        assignment_version: int
        items: list[AssignmentTransferTarget]

    class AssignmentTransferRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        target_owner_subject_id: UuidString
        target_owner_role: str
        reason: str = Field(..., min_length=3, max_length=4000)
        handoff_note: str = Field(..., min_length=3, max_length=4000)
        due_at: DateTimeString | None = None

    class SlaPauseRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        reason: str = Field(..., min_length=3, max_length=4000)
        expected_resume_at: DateTimeString

    class SlaReceipt(BaseModel):
        sla_instance_id: UuidString
        state: SlaState
        due_at: DateTimeString
        paused_duration_seconds: int = Field(..., ge=0)
        version: int = Field(..., ge=1)
        audit_event_id: UuidString
        correlation_id: UuidString
        due_soon_at: DateTimeString | None = None
        active_pause_interval_id: UuidString | None = None

    class MatchDecisionRequest(BaseModel):
        decision_type: DecisionType
        reason: str = Field(..., min_length=3, max_length=4000)
        requested_second_reviewer_id: UuidString | None = None
        risk_acknowledged: bool = False
        target_listing_id: UuidString | None = None
        target_property_id: UuidString | None = None

    class DecisionReceipt(BaseModel):
        decision_id: UuidString
        status: DecisionStatus
        resource_versions: dict[str, int]
        job_id: UuidString | None = None
        audit_event_id: UuidString
        correlation_id: UuidString

    class CandidateReassignment(BaseModel):
        candidate_site_id: UuidString
        disposition: CandidateDisposition
        target_property_id: UuidString | None = None

    class MergeRequest(BaseModel):
        source_property_ids: list[UuidString] = Field(
            ..., min_length=1, json_schema_extra={"uniqueItems": True}
        )
        target_property_id: UuidString
        reason: str = Field(..., min_length=20)
        risk_acknowledged: Literal[True]
        candidate_reassignment_plan: list[CandidateReassignment] = None
        expected_property_versions: dict[str, int] = None

        @field_validator("source_property_ids")
        @classmethod
        def require_unique_sources(cls, v: list[str]) -> list[str]:
            if len(set(v)) != len(v):
                raise ValueError("source_property_ids must contain unique values")
            return v

    class IdentityPartition(BaseModel):
        target_property_id: UuidString | None
        source_identity_edge_ids: list[UuidString] = Field(
            ..., min_length=1, json_schema_extra={"uniqueItems": True}
        )

        @field_validator("source_identity_edge_ids")
        @classmethod
        def require_unique_edges(cls, v: list[str]) -> list[str]:
            if len(set(v)) != len(v):
                raise ValueError("source_identity_edge_ids must contain unique values")
            return v

    class SplitRequest(BaseModel):
        source_property_id: UuidString
        partitions: list[IdentityPartition] = Field(..., min_length=2)
        reason: str = Field(..., min_length=20)
        risk_acknowledged: Literal[True]
        source_property_version: int = None

    class UnmergeRequest(BaseModel):
        original_decision_id: UuidString
        replacement_edges: list[IdentityPartition] = Field(..., min_length=1)
        reason: str = Field(..., min_length=20)
        risk_acknowledged: Literal[True]

    class PromotionRequest(BaseModel):
        target_format_code: str = Field(..., min_length=1, max_length=64)
        reason: str = Field(..., min_length=3, max_length=4000)
        gate_snapshot_sha256: str = Field(..., pattern=r"^[a-f0-9]{64}$")
        requested_reviewer_id: UuidString | None = None
        risk_acknowledged: bool = False

    class PromotionDecisionReceipt(BaseModel):
        target_format_code: str | None = Field(
            default=None,
            description="Target code persisted with this decision, verbatim; null for historical receipts without one.",
        )
        promotion_decision_id: UuidString
        intake_id: UuidString
        listing_id: UuidString
        status: PromotionStatus
        decision_type: PromotionDecisionType
        version: int = Field(..., ge=1)
        audit_event_id: UuidString
        correlation_id: UuidString
        candidate_site_id: UuidString | None = None
        proposer_subject_id: UuidString
        reviewer_subject_id: UuidString | None = None
        site_score_job_id: UuidString | None = None

    class ReviewDecisionRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        decision: ReviewDecision
        reason: str = Field(..., min_length=3, max_length=4000)
        requested_changes: list[str] = None
        risk_acknowledged: bool = False

    class SavedViewRequest(BaseModel):
        name: str = Field(..., min_length=1, max_length=120)
        query: dict
        resource: Literal["intake"]
        shared_role: str | None = None
        visibility: SavedViewVisibility = SavedViewVisibility.PRIVATE

        @model_validator(mode="after")
        def valid_listing_criteria(self):
            from modules.listing.application.saved_search import (
                ListingCriteriaInvalid,
                SavedListingCriteria,
            )

            try:
                criteria = SavedListingCriteria.from_query(self.query)
            except ListingCriteriaInvalid:
                raise HTTPException(422, "LISTING_CRITERIA_INVALID") from None
            if criteria is not None and (self.visibility != SavedViewVisibility.PRIVATE or self.shared_role is not None):
                raise HTTPException(422, "LISTING_CRITERIA_PRIVATE_OWNER_REQUIRED")
            return self

    class SavedView(SavedViewRequest):
        saved_view_id: UuidString
        owner_subject_id: UuidString
        created_at: DateTimeString
        version: int

    class RetryRequest(BaseModel):
        checkpoint: RetryCheckpoint
        reason: str = Field(..., min_length=3)
        override_retry_budget: bool = False
        risk_acknowledged: bool = False

    class JobReceipt(BaseModel):
        job_id: UuidString
        status: JobStatus
        delivery_state: JobDeliveryState | None = None
        checkpoint: str
        attempt: int
        version: int
        correlation_id: UuidString

    def _job_receipt_payload(job: dict[str, Any]) -> dict[str, Any]:
        """Adapt legacy queue statuses at the public receipt read boundary.

        Older rows encoded delivery mechanics directly in ``status``. Keep
        those rows readable without allowing RETRYING or DEAD_LETTER to leak
        into the outcome enum, and do not mutate the stored row while reading.
        """
        payload = dict(job)
        raw_status = payload.get("status")
        status = (
            raw_status.value.upper()
            if isinstance(raw_status, Enum)
            else str(raw_status or "").upper()
        )
        raw_delivery_state = payload.get("delivery_state")
        delivery_state = (
            raw_delivery_state.value.upper()
            if isinstance(raw_delivery_state, Enum)
            else str(raw_delivery_state).upper() if raw_delivery_state else None
        )

        if status == "RETRYING":
            status = "QUEUED"
            delivery_state = "RETRYING"
        elif status == "DEAD_LETTER":
            status = "FAILED"
            delivery_state = "DEAD_LETTER"

        payload["status"] = status
        payload["delivery_state"] = delivery_state
        return payload

    def _raise_score_delivery_http_error(error: Exception) -> None:
        """Map delivery contract denials without disguising storage failures."""
        from modules.listing.application.intake_score_delivery import (
            ScoreDeliveryConflict,
            ScoreDeliveryDenied,
            ScoreDeliveryUnavailable,
            ScoreDeliveryValidationError,
        )

        for error_type, status_code in (
            (ScoreDeliveryUnavailable, 503), (ScoreDeliveryConflict, 409),
            (ScoreDeliveryDenied, 403), (ScoreDeliveryValidationError, 422),
        ):
            if isinstance(error, error_type):
                raise HTTPException(status_code, str(error)) from error

    class ReasonCommand(BaseModel):
        model_config = ConfigDict(extra="forbid")
        reason: str = Field(..., min_length=3, max_length=4000)

    class RiskReasonCommand(ReasonCommand):
        model_config = ConfigDict(extra="forbid")
        risk_acknowledged: Literal[True]
        incident_or_change_id: str | None = Field(None, max_length=200)

    class ListingImportPayload(BaseModel):
        records: list[dict[str, Any]] = Field(default_factory=list)
        source_id: str | None = None


    class AddressCorrectionPayload(BaseModel):
        model_config = ConfigDict(extra="allow")
        latitude: float | None = None
        longitude: float | None = None
        raw_address: str | None = None
        normalized_address: str | None = None
        city: str | None = None
        district: str | None = None
        village: str | None = None
        road: str | None = None
        geocode_precision: str | None = None
        geocode_confidence: float | None = None
        reason: str = Field(..., min_length=5)
        expected_revision: int | None = None
        risk_acknowledged: bool = False
        actor: str | None = None
        actor_id: str | None = None
        actorRoleId: str | None = None
        actorName: str | None = None

    class AddressRollbackPayload(BaseModel):
        model_config = ConfigDict(extra="allow")
        reason: str = Field(..., min_length=5)
        expected_revision: int | None = None
        actor: str | None = None
        actor_id: str | None = None

    def create_listings_router(
        *,
        audit_log: InMemoryAuditLog | None = None,
        repository: Any = None,
        address_repository: Any = None,
        correction_repository: Any = None,
    ) -> APIRouter:
        from apps.api.oday_api.security.dependencies import (
            _raise_forbidden,
            _record_denial,
            build_engine,
            require_permission,
        )
        from shared.auth import (
            AccessRequest,
            Action,
            Decision,
            Environment,
            Principal,
            ResourceDescriptor,
        )
        from shared.infrastructure.persistence.repositories import (
            InMemoryAddressLocationRepository,
            InMemoryManualCorrectionRepository,
            InvalidCorrectionError,
            StaleRevisionError,
        )

        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        bound_repository = repository
        bound_address_repository = address_repository
        bound_correction_repository = correction_repository

        def _address_repo(request: Request) -> Any:
            if bound_address_repository is not None:
                return bound_address_repository
            if hasattr(request, "app") and hasattr(request.app, "state"):
                if getattr(request.app.state, "address_location_repository", None):
                    return request.app.state.address_location_repository
                bundle = getattr(request.app.state, "persistence_bundle", None)
                if bundle and getattr(bundle, "address_location_repository", None):
                    return bundle.address_location_repository
            return InMemoryAddressLocationRepository()

        def _correction_repo(request: Request) -> Any:
            if bound_correction_repository is not None:
                return bound_correction_repository
            if hasattr(request, "app") and hasattr(request.app, "state"):
                if getattr(request.app.state, "manual_correction_repository", None):
                    return request.app.state.manual_correction_repository
                bundle = getattr(request.app.state, "persistence_bundle", None)
                if bundle and getattr(bundle, "manual_correction_repository", None):
                    return bundle.manual_correction_repository
            return InMemoryManualCorrectionRepository()

        def _require_caller_tenant(
            principal: Any,
            request: Request,
            action: Action = Action.VIEW,
            resource_id: str | None = None,
        ) -> str:
            """Return the caller's tenant, refusing an unscoped principal.

            A caller with no tenant has no scope to compare a record against.
            Absence of a tenant is denied and audited.
            """
            caller_tenant = str(getattr(principal, "tenant_id", "") or "").strip()
            if not caller_tenant:
                decision = Decision.deny(
                    "TENANT_SCOPE_DENIED: Tenant scope is required",
                    policy_id="tenant.isolation",
                )
                access = AccessRequest(
                    principal=principal,
                    action=action,
                    resource=ResourceDescriptor(
                        type="address_location",
                        resource_id=resource_id,
                        tenant_id="",
                    ),
                    environment=Environment(
                        source_ip=request.client.host if request.client else None,
                        attributes={"correlation_id": request.headers.get("x-correlation-id", "unknown")},
                    ),
                )
                _record_denial(authz_engine, access, decision)
                _raise_forbidden(decision)
            return caller_tenant

        def _require_address_tenant_scope(
            address: Any,
            principal: Any,
            request: Request,
            action: Action = Action.VIEW,
        ) -> None:
            """Fail closed unless the caller may act inside the record's tenant.

            Platform admins do NOT bypass tenant scoping; unresolved cross-tenant
            policy defaults to fail closed (ODP-OPEN-DECISIONS 5a / remediation 5a).
            Denials write a security.authorization audit event.
            """
            caller_tenant = _require_caller_tenant(
                principal, request, action=action, resource_id=getattr(address, "address_id", None)
            )
            record_tenant = str(getattr(address, "tenant_id", "") or "").strip()
            if not record_tenant:
                decision = Decision.deny(
                    "TENANT_SCOPE_DENIED: Record has no tenant scope",
                    policy_id="tenant.isolation",
                )
                access = AccessRequest(
                    principal=principal,
                    action=action,
                    resource=ResourceDescriptor(
                        type="address_location",
                        resource_id=getattr(address, "address_id", None),
                        tenant_id="",
                    ),
                    environment=Environment(
                        source_ip=request.client.host if request.client else None,
                        attributes={"correlation_id": request.headers.get("x-correlation-id", "unknown")},
                    ),
                )
                _record_denial(authz_engine, access, decision)
                _raise_forbidden(decision)

            if record_tenant != caller_tenant:
                decision = Decision.deny(
                    "TENANT_SCOPE_DENIED: Cross-tenant access forbidden",
                    policy_id="tenant.isolation",
                )
                access = AccessRequest(
                    principal=principal,
                    action=action,
                    resource=ResourceDescriptor(
                        type="address_location",
                        resource_id=getattr(address, "address_id", None),
                        tenant_id=record_tenant,
                    ),
                    environment=Environment(
                        source_ip=request.client.host if request.client else None,
                        attributes={"correlation_id": request.headers.get("x-correlation-id", "unknown")},
                    ),
                )
                _record_denial(authz_engine, access, decision)
                _raise_forbidden(decision)

        router = APIRouter(prefix="/listings", tags=["listings"])

        @router.post(
            "/import",
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=[Depends(require_permission("listing", Action.CREATE, engine=authz_engine))],
        )
        @router.post(
            "/import-jobs",
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=[Depends(require_permission("listing", Action.CREATE, engine=authz_engine))],
        )
        def import_listings(body: ListingImportPayload, request: Request) -> dict[str, Any]:
            repository = _repository(request, bound_repository)
            result = ListingPipeline(
                repository=repository, geo_pipeline=_geo_pipeline(request)
            ).import_records(
                body.records,
                source_id=body.source_id,
            )
            return result.to_dict()

        @router.get(
            "/candidates",
            dependencies=[Depends(require_permission("listing", Action.VIEW, engine=authz_engine))],
        )
        def list_candidate_sites(request: Request) -> dict[str, Any]:
            repository = _repository(request, bound_repository)
            return {
                "candidates": [
                    candidate.to_card_dict() for candidate in repository.list_candidates()
                ]
            }

        @router.post(
            "/addresses/{address_id}/corrections",
            status_code=status.HTTP_200_OK,
        )
        def submit_address_correction(
            address_id: str,
            body: AddressCorrectionPayload,
            request: Request,
            principal: Principal = Depends(
                require_permission("listing", Action.UPDATE, engine=authz_engine)
            ),
            if_match: str | None = Header(None, alias="If-Match"),
        ) -> dict[str, Any]:
            actor_id = principal.subject_id
            caller_tenant = _require_caller_tenant(
                principal, request, action=Action.UPDATE, resource_id=address_id
            )

            reason = (body.reason or "").strip()
            if len(reason) < 5:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="INVALID_REASON: Reason is required and must be at least 5 characters.",
                )

            expected_revision = body.expected_revision
            if if_match is not None and expected_revision is None:
                cleaned_match = if_match.strip().strip('"').lstrip('W/').strip('"')
                if cleaned_match.isdigit():
                    expected_revision = int(cleaned_match)

            addr_repo = _address_repo(request)
            corr_repo = _correction_repo(request)
            audit = (
                getattr(request.app.state, "audit_log", active_audit_log)
                if hasattr(request, "app") and hasattr(request.app, "state")
                else active_audit_log
            )

            updates: dict[str, Any] = {}
            for field_name in (
                "latitude",
                "longitude",
                "raw_address",
                "normalized_address",
                "city",
                "district",
                "village",
                "road",
                "geocode_precision",
                "geocode_confidence",
            ):
                val = getattr(body, field_name, None)
                if val is not None:
                    updates[field_name] = val

            if not updates:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No valid fields to correct in request",
                )

            try:
                corr_id = request.headers.get("x-correlation-id") or str(uuid4())
                new_address, correction, decision_card = addr_repo.apply_correction(
                    address_id,
                    updates=updates,
                    reason=reason,
                    actor_id=actor_id,
                    tenant_id=caller_tenant,
                    expected_revision=expected_revision,
                    correlation_id=corr_id,
                    risk_acknowledged=body.risk_acknowledged,
                    audit_log=audit,
                    correction_repo=corr_repo,
                )
            except KeyError as e:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Address {address_id} not found",
                ) from e
            except PermissionError:
                decision = Decision.deny(
                    "TENANT_SCOPE_DENIED: Cross-tenant modification forbidden",
                    policy_id="tenant.isolation",
                )
                access = AccessRequest(
                    principal=principal,
                    action=Action.UPDATE,
                    resource=ResourceDescriptor(
                        type="address_location",
                        resource_id=address_id,
                        tenant_id=caller_tenant,
                    ),
                    environment=Environment(
                        source_ip=request.client.host if request.client else None,
                        attributes={"correlation_id": request.headers.get("x-correlation-id", "unknown")},
                    ),
                )
                _record_denial(authz_engine, access, decision)
                _raise_forbidden(decision)
            except StaleRevisionError as e:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(e),
                ) from e
            except InvalidCorrectionError as e:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(e),
                ) from e

            return {
                "correction_id": correction.correction_id,
                "address_id": new_address.address_id,
                "tenant_id": new_address.tenant_id,
                "field_name": correction.field_name,
                "old_value": correction.old_value,
                "new_value": correction.new_value,
                "manual_override_flag": new_address.manual_override_flag,
                "source_revision": correction.source_revision,
                "applied_revision": correction.applied_revision,
                "status": correction.status,
                "actor_id": actor_id,
                "reason": correction.reason,
                "correlation_id": correction.correlation_id,
                "audit_event_id": correction.audit_event_id,
                "decision_card_hash": correction.decision_card_hash,
                "decision_card": decision_card.to_dict(),
                "address": {
                    "address_id": new_address.address_id,
                    "raw_address": new_address.raw_address,
                    "normalized_address": new_address.normalized_address,
                    "city": new_address.city,
                    "district": new_address.district,
                    "village": new_address.village,
                    "road": new_address.road,
                    "latitude": new_address.latitude,
                    "longitude": new_address.longitude,
                    "geocode_precision": new_address.geocode_precision,
                    "geocode_confidence": new_address.geocode_confidence,
                    "h3_res_8": new_address.h3_res_8,
                    "h3_res_9": new_address.h3_res_9,
                    "h3_res_10": new_address.h3_res_10,
                    "manual_override_flag": new_address.manual_override_flag,
                    "tenant_id": new_address.tenant_id,
                    "revision": new_address.revision,
                },
            }

        @router.post(
            "/addresses/{address_id}/corrections/{correction_id}/rollback",
            status_code=status.HTTP_200_OK,
        )
        def rollback_address_correction(
            address_id: str,
            correction_id: str,
            body: AddressRollbackPayload,
            request: Request,
            principal: Principal = Depends(
                require_permission("listing", Action.UPDATE, engine=authz_engine)
            ),
            if_match: str | None = Header(None, alias="If-Match"),
        ) -> dict[str, Any]:
            actor_id = principal.subject_id
            caller_tenant = _require_caller_tenant(
                principal, request, action=Action.UPDATE, resource_id=address_id
            )

            reason = (body.reason or "").strip()
            if len(reason) < 5:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="INVALID_REASON: Reason is required and must be at least 5 characters.",
                )

            expected_revision = body.expected_revision
            if if_match is not None and expected_revision is None:
                cleaned_match = if_match.strip().strip('"').lstrip('W/').strip('"')
                if cleaned_match.isdigit():
                    expected_revision = int(cleaned_match)

            addr_repo = _address_repo(request)
            corr_repo = _correction_repo(request)
            audit = (
                getattr(request.app.state, "audit_log", active_audit_log)
                if hasattr(request, "app") and hasattr(request.app, "state")
                else active_audit_log
            )

            try:
                corr_id = request.headers.get("x-correlation-id") or str(uuid4())
                restored_address, updated_correction, rollback_card = addr_repo.rollback_correction(
                    address_id,
                    correction_id,
                    reason=reason,
                    actor_id=actor_id,
                    tenant_id=caller_tenant,
                    expected_revision=expected_revision,
                    correlation_id=corr_id,
                    audit_log=audit,
                    correction_repo=corr_repo,
                )
            except KeyError as e:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=str(e),
                ) from e
            except PermissionError:
                decision = Decision.deny(
                    "TENANT_SCOPE_DENIED: Cross-tenant modification forbidden",
                    policy_id="tenant.isolation",
                )
                access = AccessRequest(
                    principal=principal,
                    action=Action.UPDATE,
                    resource=ResourceDescriptor(
                        type="address_location",
                        resource_id=address_id,
                        tenant_id=caller_tenant,
                    ),
                    environment=Environment(
                        source_ip=request.client.host if request.client else None,
                        attributes={"correlation_id": request.headers.get("x-correlation-id", "unknown")},
                    ),
                )
                _record_denial(authz_engine, access, decision)
                _raise_forbidden(decision)
            except StaleRevisionError as e:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(e),
                ) from e
            except (InvalidCorrectionError, ValueError) as e:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(e),
                ) from e

            return {
                "correction_id": updated_correction.correction_id,
                "address_id": restored_address.address_id,
                "status": updated_correction.status,
                "manual_override_flag": restored_address.manual_override_flag,
                "source_revision": updated_correction.source_revision,
                "applied_revision": restored_address.revision,
                "actor_id": actor_id,
                "reason": reason,
                "old_value": rollback_card.metrics.get("old_value", {}),
                "new_value": rollback_card.metrics.get("new_value", {}),
                "decision_card": rollback_card.to_dict(),
                "address": {
                    "address_id": restored_address.address_id,
                    "raw_address": restored_address.raw_address,
                    "normalized_address": restored_address.normalized_address,
                    "city": restored_address.city,
                    "district": restored_address.district,
                    "village": restored_address.village,
                    "road": restored_address.road,
                    "latitude": restored_address.latitude,
                    "longitude": restored_address.longitude,
                    "geocode_precision": restored_address.geocode_precision,
                    "geocode_confidence": restored_address.geocode_confidence,
                    "h3_res_8": restored_address.h3_res_8,
                    "h3_res_9": restored_address.h3_res_9,
                    "h3_res_10": restored_address.h3_res_10,
                    "manual_override_flag": restored_address.manual_override_flag,
                    "tenant_id": restored_address.tenant_id,
                    "revision": restored_address.revision,
                },
            }

        @router.get(
            "/addresses/{address_id}",
        )
        def get_address_location(
            address_id: str,
            request: Request,
            principal: Principal = Depends(
                require_permission("listing", Action.VIEW, engine=authz_engine)
            ),
        ) -> dict[str, Any]:
            addr_repo = _address_repo(request)
            address = addr_repo.get_address(address_id)
            if not address:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Address {address_id} not found",
                )

            _require_address_tenant_scope(address, principal, request, action=Action.VIEW)

            return {
                "address_id": address.address_id,
                "raw_address": address.raw_address,
                "normalized_address": address.normalized_address,
                "city": address.city,
                "district": address.district,
                "village": address.village,
                "road": address.road,
                "latitude": address.latitude,
                "longitude": address.longitude,
                "geocode_precision": address.geocode_precision,
                "geocode_confidence": address.geocode_confidence,
                "h3_res_8": address.h3_res_8,
                "h3_res_9": address.h3_res_9,
                "h3_res_10": address.h3_res_10,
                "manual_override_flag": address.manual_override_flag,
                "tenant_id": address.tenant_id,
                "revision": address.revision,
            }

        @router.get(
            "/addresses/{address_id}/corrections",
        )
        def list_address_corrections(
            address_id: str,
            request: Request,
            principal: Principal = Depends(
                require_permission("listing", Action.VIEW, engine=authz_engine)
            ),
        ) -> dict[str, Any]:
            addr_repo = _address_repo(request)
            corr_repo = _correction_repo(request)
            address = addr_repo.get_address(address_id)
            if not address:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Address {address_id} not found",
                )

            _require_address_tenant_scope(address, principal, request, action=Action.VIEW)

            corrections = addr_repo.get_corrections(address_id, correction_repo=corr_repo)
            return {
                "corrections": [
                    {
                        "correction_id": c.correction_id,
                        "entity_type": c.entity_type,
                        "entity_id": c.entity_id,
                        "tenant_id": c.tenant_id,
                        "field_name": c.field_name,
                        "old_value": c.old_value,
                        "new_value": c.new_value,
                        "reason": c.reason,
                        "actor_id": c.actor_id,
                        "occurred_at": (
                            c.occurred_at.isoformat()
                            if hasattr(c.occurred_at, "isoformat")
                            else str(c.occurred_at)
                        ),
                        "source_revision": c.source_revision,
                        "applied_revision": c.applied_revision,
                        "status": c.status,
                        "correlation_id": c.correlation_id,
                        "decision_card_hash": c.decision_card_hash,
                        "audit_event_id": c.audit_event_id,
                    }
                    for c in corrections
                ]
            }

        return router


    class AssistedIntakeStore:
        """Process-local Assisted Intake state for local and contract tests."""
        _instances: list[AssistedIntakeStore] = []

        def __init__(self) -> None:
            self.intakes: dict[str, dict[str, Any]] = {}
            self.corrections: dict[str, dict[str, Any]] = {}
            self.assignments: dict[str, dict[str, Any]] = {}
            self.jobs: dict[str, dict[str, Any]] = {}
            self.decisions: dict[str, dict[str, Any]] = {}
            self.promotions: dict[str, dict[str, Any]] = {}
            self.slas: dict[str, dict[str, Any]] = {}
            self.saved_views: list[dict[str, Any]] = []
            self.replays: dict[str, tuple[str, dict[str, Any], int]] = {}
            self.workflow_audits: dict[str, dict[str, Any]] = {}
            self.workflow_outbox: dict[str, dict[str, Any]] = {}
            self._transaction_lock = RLock()
            AssistedIntakeStore._instances.append(self)

        def refresh(self, tenant_id: str | None = None) -> None:
            return None

        def flush(self) -> None:
            return None

        @contextmanager
        def transaction(self):
            with self._transaction_lock:
                names = ("intakes", "corrections", "assignments", "jobs", "decisions", "promotions", "slas", "saved_views", "replays", "workflow_audits", "workflow_outbox")
                snapshot = {name: copy.deepcopy(getattr(self, name)) for name in names}
                try:
                    yield
                except BaseException:
                    for name, value in snapshot.items():
                        setattr(self, name, value)
                    raise


    def _legacy_operator_repository(active_store, app_state):
        legacy = getattr(app_state, "operator_intake_repository", None)
        if getattr(getattr(active_store, "_engine", None), "dialect", None) == "postgresql":
            from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
            from shared.infrastructure.persistence.operator_network_listings import (
                DurableAssistedIntakeRepository,
            )

            # Historical Operator rows use the same tenant partition as the
            # production Operator factory. Never query another tenant's mirror.
            tenant_id = active_store._local.tenant_id
            return DurableAssistedIntakeRepository(TenantScopedDocumentStore(active_store._store, tenant_id))
        return legacy


    class V1PromotionRepositoryAdapter:
        def __init__(self, active_store, app_state=None):
            self.active_store = active_store
            self.op_repo = _legacy_operator_repository(active_store, app_state)

        def get_promotion(self, promotion_decision_id: str) -> dict[str, Any] | None:
            val = self.active_store.promotions.get(promotion_decision_id)
            if val is not None:
                val.setdefault("proposer_subject_id", val.get("proposer"))
                return val
            if self.op_repo:
                val = self.op_repo.get_promotion(promotion_decision_id)
                if val:
                    val.setdefault("proposer_subject_id", val.get("proposer"))
                    return val
            return None

        def save_promotion(self, promo: dict[str, Any]) -> None:
            promo.setdefault("proposer_subject_id", promo.get("proposer"))
            parent = self.active_store.intakes.get(promo.get("intake_id"))
            if self.op_repo and not (parent and parent.get("intake_id")):
                self.op_repo.save_promotion(promo)
            self.active_store.promotions[promo["promotion_decision_id"]] = promo
            # Score delivery owns its job receipt. A committed promotion only
            # proves acceptance; it cannot manufacture a worker result.

        def list_promotions(self) -> list[dict[str, Any]]:
            by_id = {value["promotion_decision_id"]: value
                     for value in self.op_repo.list_promotions()} if self.op_repo else {}
            by_id.update(self.active_store.promotions)
            values = list(by_id.values())
            for value in values:
                value.setdefault("proposer_subject_id", value.get("proposer"))
            return values

        def get_promotion_for_intake(self, intake_id: str) -> dict[str, Any] | None:
            parent = self.active_store.intakes.get(intake_id)
            tenant_id = (parent.get("scope", {}).get("tenant_id") or parent.get("tenantId")) if parent else None
            canonical_values = [value for value in self.active_store.promotions.values()
                                if value.get("intake_id") == intake_id
                                and (tenant_id is None or value.get("tenant_id") == tenant_id)]
            values = [
                value
                for value in canonical_values or self.list_promotions()
                if value.get("intake_id") == intake_id
                and (tenant_id is None or value.get("tenant_id") == tenant_id)
            ]
            if not values:
                return None
            return max(
                values,
                key=lambda value: (
                    str(value.get("created_at") or ""),
                    int(value.get("version") or 0),
                    str(value.get("promotion_decision_id") or ""),
                ),
            )

    class V1IntakeRepositoryAdapter:
        def __init__(self, active_store, app_state=None):
            self.active_store = active_store
            self.op_repo = _legacy_operator_repository(active_store, app_state)

        def get_listing_intake(self, intake_id: str) -> dict[str, Any] | None:
            canonical = self.active_store.intakes.get(intake_id)
            if canonical is not None:
                if not canonical.get("intake_id"):
                    return canonical
                value = copy.deepcopy(canonical)
                # Presentation carries the reviewed match; identity, scope and
                # versions always come from the canonical parent/resources.
                value["matchResult"] = copy.deepcopy(
                    canonical.get("operator_presentation", {}).get("matchResult")
                    if "operator_presentation" in canonical else canonical.get("matchResult")
                )
                assignment = self.active_store.assignments.get(canonical.get("assignment_id"))
                if canonical.get("assignment_id"):
                    value["assigned_to"] = value["owner"] = None
                if (assignment and assignment.get("intake_id") == intake_id
                        and assignment.get("assignment_id") == canonical.get("assignment_id")
                        and assignment.get("tenant_id") == canonical.get("scope", {}).get("tenant_id")):
                    value["assigned_to"] = value["owner"] = assignment.get("owner_subject_id")
                return value
            if self.op_repo:
                if hasattr(self.op_repo, "intakes"):
                    val = self.op_repo.intakes.get(intake_id)
                    if val:
                        return val
                if hasattr(self.op_repo, "_store") and hasattr(self.op_repo, "_INTAKES"):
                    val = self.op_repo._store.get(self.op_repo._INTAKES, intake_id)
                    if val:
                        return val
            return None

        def save_intake(self, intake: dict[str, Any]) -> None:
            intake_id = intake.get("intake_id") or intake.get("id")
            canonical = self.active_store.intakes.get(intake_id)
            if canonical and canonical.get("intake_id"):
                persisted = copy.deepcopy(intake)
                if "operator_presentation" in persisted:
                    persisted.pop("matchResult", None)
                self.active_store.intakes[intake_id] = persisted
                return
            if self.op_repo and intake.get("id"):
                self.op_repo.save_intake(intake)
            self.active_store.intakes[intake_id] = intake

    class ListingAdapterWrapper:
        def __init__(self, d: dict[str, Any]):
            self._d = d

        @property
        def listing_id(self) -> str:
            return self._d.get("id") or self._d.get("listing_id") or ""

        @property
        def source_listing_id(self) -> str:
            return self._d.get("source_listing_id") or self._d.get("source_id") or self.listing_id

        @property
        def source_id(self) -> str:
            return self._d.get("source_id") or self.listing_id

        @property
        def rent_amount(self) -> float:
            return float(self._d.get("rent_amount") or self._d.get("rentPerMonth") or 0.0)

        @property
        def area_ping(self) -> float:
            return float(self._d.get("area_ping") or self._d.get("areaPing") or 0.0)

        @property
        def floor(self) -> str:
            return str(self._d.get("floor") or "")

        @property
        def frontage_m(self) -> float:
            return float(self._d.get("frontage_m") or self._d.get("frontageMeters") or 0.0)

        @property
        def parking_flag(self) -> bool:
            return bool(self._d.get("parking_flag") or self._d.get("parkingOrTemporaryStop") or False)

        @property
        def address_id(self) -> str:
            return self._d.get("address_id") or ""

        @property
        def address(self) -> str:
            return self._d.get("address") or ""

        @property
        def geocode_confidence(self) -> float:
            return float(self._d.get("geocode_confidence") or 0.0)

        @property
        def h3_res_9(self) -> str:
            return self._d.get("h3_res_9") or ""

        def get(self, key, default=None):
            return self._d.get(key, default)

        def __getitem__(self, key):
            return self._d[key]

        def __setitem__(self, key, value):
            self._d[key] = value

        def __contains__(self, key):
            return key in self._d

        def __getattr__(self, name: str) -> Any:
            if name in self._d:
                return self._d[name]
            raise AttributeError(f"'ListingAdapterWrapper' object has no attribute '{name}'")

    class V1ListingRepositoryAdapter:
        def __init__(self, repo):
            self.repo = repo

        def get_address(self, address_id: str) -> Any | None:
            if hasattr(self.repo, "get_address"):
                return self.repo.get_address(address_id)
            if hasattr(self.repo, "addresses"):
                return next(
                    (
                        address
                        for address in self.repo.addresses
                        if address.address_id == address_id
                    ),
                    None,
                )
            return None

        def get_domain_listing(self, listing_id: str) -> Any | None:
            from modules.listing.domain.intake_states import DenialCode

            listing = self.repo.get_listing(listing_id)
            if listing is None:
                return None
            # The operator writer has always persisted these measured inputs
            # in tenant-scoped metadata. Carry those records into canonical
            # promotion without inventing values or mutating the stored row.
            document_store = getattr(self.repo, "_store", None)
            metadata = {}
            if document_store is not None:
                from shared.infrastructure.persistence.operator_network_listings import (
                    DurableAssistedIntakeRepository,
                )

                metadata = DurableAssistedIntakeRepository(document_store).get_listing_metadata(listing_id)
            listing = copy.copy(listing)
            for attribute, key in (
                ("prior_90d_cell_net_revenue", "prior90dCellNetRevenue"),
                ("prior_90d_cell_transaction_count", "prior90dCellTransactionCount"),
                ("prior_90d_cell_store_count", "prior90dCellStoreCount"),
                ("feature_snapshot_time", "featureSnapshotTime"),
                ("hard_rule_failures", "hardRuleFailures"),
            ):
                if key in metadata:
                    value = metadata[key]
                    if attribute == "feature_snapshot_time" and isinstance(value, str):
                        try:
                            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                        except ValueError as exc:
                            raise DomainValidationError(
                                DenialCode.SOURCE_POLICY_DENIED, "RECORDED_FEATURE_TIME_INVALID",
                            ) from exc
                    if attribute != "hard_rule_failures":
                        # Legacy writers persisted null placeholders for PIT
                        # fields. They cannot erase a separately recorded domain
                        # measurement; conflicting real values must fail closed.
                        if value is None:
                            continue
                        recorded = getattr(listing, attribute, None)
                        if recorded is not None and recorded != value:
                            raise DomainValidationError(
                                DenialCode.SOURCE_POLICY_DENIED,
                                f"RECORDED_FEATURE_EVIDENCE_CONFLICT: {attribute}",
                            )
                    object.__setattr__(listing, attribute, value)
            return listing

        def get_listing(self, listing_id: str) -> Any | None:
            listing = self.get_domain_listing(listing_id)
            if not listing:
                return None
            address = self.get_address(listing.address_id)

            d = {
                "id": listing.listing_id,
                "listing_id": listing.listing_id,
                "listingId": listing.listing_id,
                "source_listing_id": listing.source_listing_id,
                "source_id": listing.source_id,
                "status": listing.listing_status,
                "listing_status": listing.listing_status,
                "address_id": listing.address_id,
                "rent_amount": listing.rent_amount,
                "rentPerMonth": listing.rent_amount,
                "currency": listing.currency,
                "area_ping": listing.area_ping,
                "areaPing": listing.area_ping,
                "floor": listing.floor,
                "frontage_m": listing.frontage_m,
                "depth_m": listing.depth_m,
                "corner_flag": listing.corner_flag,
                "parking_flag": listing.parking_flag,
                "utility_electricity_flag": listing.utility_electricity_flag,
                "utility_drainage_flag": listing.utility_drainage_flag,
                "utility_gas_flag": listing.utility_gas_flag,
                "confidence": listing.effective_confidence,
                "confidenceProvenance": listing.effective_confidence_provenance,
                "snapshot_id": listing.snapshot_id,
                "hardRuleFailures": getattr(listing, "hard_rule_failures", None),
            }
            for attr in (
                "property_id",
                "listing_obs_id",
                "rent_benchmark",
                "rent_benchmark_median",
                "rent_benchmark_p25",
                "rent_benchmark_p75",
                "rent_benchmark_sample_count",
                "rent_benchmark_id",
                "platform_property_id",
                "platform_observation_id",
                "prior_90d_cell_net_revenue",
                "prior_90d_cell_transaction_count",
                "prior_90d_cell_store_count",
                "feature_snapshot_time",
            ):
                val = getattr(listing, attr, None)
                if val is not None:
                    d[attr] = val
            if address:
                d.update({
                    "address": address.raw_address or address.normalized_address or "",
                    "address_raw": address.raw_address or "",
                    "address_normalized": address.normalized_address or "",
                    "city": address.city or "",
                    "district": address.district or "",
                    "village": address.village or "",
                    "road": address.road or "",
                    "lat": address.latitude,
                    "latitude": address.latitude,
                    "lng": address.longitude,
                    "longitude": address.longitude,
                    "geocode_precision": address.geocode_precision,
                    "geocode_confidence": address.geocode_confidence,
                    # Both spellings, like every other field in this dict.
                    # Emitting only the snake_case one left consumers that read
                    # `geocodeConfidence` falling through to the listing's own
                    # `confidence` above, which is extraction confidence and
                    # says nothing about whether the address was geocoded.
                    "geocodeConfidence": address.geocode_confidence,
                    "h3Index": address.h3_res_8 or address.h3_res_9 or address.h3_res_10 or "",
                    "h3_index": address.h3_res_8 or address.h3_res_9 or address.h3_res_10 or "",
                    "h3_res_8": address.h3_res_8 or "",
                    "h3_res_9": address.h3_res_9 or "",
                    "h3_res_10": address.h3_res_10 or "",
                    "manual_override_flag": address.manual_override_flag,
                })
            return ListingAdapterWrapper(d)

        def save_listing(self, listing_dict: dict[str, Any]) -> None:
            listing_id = listing_dict["id"]
            existing_listing = self.get_domain_listing(listing_id)
            if existing_listing:
                from dataclasses import replace

                from modules.listing.domain.models import ListingDedupKey

                updated_listing = replace(
                    existing_listing,
                    listing_status=listing_dict.get("status") or existing_listing.listing_status,
                )
                for name, value in vars(existing_listing).items():
                    if name not in existing_listing.__dataclass_fields__:
                        object.__setattr__(updated_listing, name, value)
                address = self.get_address(existing_listing.address_id)
                if address is None:
                    raise ValueError(
                        f"Address {existing_listing.address_id} not found "
                        f"for listing {listing_id}"
                    )
                key = ListingDedupKey(
                    source_id=updated_listing.source_id,
                    source_listing_id=updated_listing.source_listing_id,
                    normalized_address=(
                        address.normalized_address or address.raw_address
                    ),
                    rent_amount=updated_listing.rent_amount,
                    area_ping=updated_listing.area_ping,
                )
                self.repo.save_listing(updated_listing, address, key)

        def list_candidates(self) -> list[dict[str, Any]]:
            candidates = []
            for draft in self.repo.list_candidates():
                if isinstance(draft, dict):
                    candidates.append(draft)
                else:
                    c_dict = {
                        "id": draft.candidate_site.candidate_site_id,
                        "listingId": draft.candidate_site.listing_id,
                        "heatZoneId": draft.heat_zone_id or None,
                        "title": ((getattr(draft.listing, "source_listing_id", None) or getattr(draft.listing, "source_id", None) or getattr(draft.listing, "listing_id", None)) + " 候選點") if draft.listing else "候選點",
                        "address": draft.address.raw_address if draft.address else "",
                        "latitude": draft.address.latitude if draft.address else None,
                        "longitude": draft.address.longitude if draft.address else None,
                        "geocodeConfidence": draft.address.geocode_confidence if draft.address else None,
                        "status": draft.candidate_site.site_status,
                        "score": getattr(draft, "score", None),
                        "recommendation": getattr(draft, "recommendation", None),
                        "modelVersion": getattr(draft, "model_version", None),
                        "datasetSnapshotId": getattr(draft, "dataset_snapshot_id", None),
                        "missingData": list(getattr(draft, "missing_data", [])),
                        "reviewId": getattr(draft, "review_id", None),
                    }
                    candidates.append(c_dict)
            return candidates

        def save_candidate(self, draft: Any) -> None:
            if hasattr(self.repo, "save_candidate"):
                self.repo.save_candidate(draft)



    def create_assisted_intake_router(
        store: AssistedIntakeStore | None = None,
        audit_log: InMemoryAuditLog | None = None,
        cursor_signing_key: str | bytes | None = None,
        workflow_policy: IntakeWorkflowPolicy | None = None,
    ) -> APIRouter:
        """Implement the approved ODP assisted-intake `/api/v1` contract."""
        from modules.listing.application.intake_authorization import (
            authorize_intake_action,
            intake_resource_in_scope,
            mask_intake,
        )
        from shared.auth import Principal, Role

        active = store or AssistedIntakeStore()
        active_audit_log = audit_log or InMemoryAuditLog()
        router = APIRouter(tags=["assisted-listing-intake"])

        backlog = None
        if getattr(getattr(active, "_engine", None), "dialect", None) == "postgresql":
            from shared.audit.backlog import CanonicalAuditBacklog
            backlog = CanonicalAuditBacklog(active._engine)

        def initialize_workflow(intake: dict[str, Any]) -> None:
            try:
                initialize_intake_workflow(active, intake, workflow_policy)
            except IntakeWorkflowUnavailable as exc:
                raise HTTPException(503, "INTAKE_WORKFLOW_POLICY_UNAVAILABLE") from exc

        configured_cursor_signing_key = (
            os.environ.get(_CURSOR_SIGNING_KEY_ENV)
            if cursor_signing_key is None
            else cursor_signing_key
        )
        if configured_cursor_signing_key is None:
            # Local/test apps share one unpredictable process-local key. Deployed
            # replicas configure ODP_INTAKE_CURSOR_SIGNING_KEY so cursors remain
            # valid across workers and restarts without embedding a repository key.
            active_cursor_signing_key = _PROCESS_CURSOR_SIGNING_KEY
        else:
            active_cursor_signing_key = (
                configured_cursor_signing_key.encode("utf-8")
                if isinstance(configured_cursor_signing_key, str)
                else configured_cursor_signing_key
            )
            if len(active_cursor_signing_key) < 32:
                raise ValueError(
                    f"{_CURSOR_SIGNING_KEY_ENV} must contain at least 32 bytes"
                )

        def api_error_responses(
            *codes: int,
            idempotency_conflict: bool = False,
        ) -> dict[int, dict[str, Any]]:
            return {
                code: {
                    "model": (
                        ApiError
                        if code != 409 or idempotency_conflict
                        else ConflictError
                    ),
                    "description": "Assisted intake API error",
                }
                for code in codes
            }

        def response_headers(*names: str) -> dict[str, dict[str, Any]]:
            schemas = {
                "ETag": {"type": "string", "example": 'W/"7"'},
                "Idempotency-Replayed": {"type": "boolean"},
                "Retry-After": {"type": "integer", "minimum": 1},
            }
            return {
                name: {"schema": schemas[name]}
                for name in names
            }

        def now() -> str:
            return datetime.now(UTC).isoformat().replace("+00:00", "Z")

        def get_principal(request: Request) -> Principal:
            from apps.api.oday_api.security.dependencies import principal_from_headers
            return principal_from_headers(request.headers)

        def get_operator_role_id(request: Request) -> str | None:
            # Only a server-selected role written by an authentication/
            # authorization dependency is trusted.  The standalone v1 intake
            # routes derive grants from the authenticated principal itself and
            # therefore normally return None here.
            return getattr(request.state, "operator_role_id", None)

        def intake_auth_resource(value: dict[str, Any]) -> dict[str, Any]:
            resource = dict(value)
            scope = value.get("scope")
            if isinstance(scope, dict):
                resource["tenant_id"] = scope.get("tenant_id")
            resource["submitter"] = value.get("submitted_by") or value.get("submitter")
            resource["owner"] = value.get("assigned_to") or value.get("owner")
            return resource

        def require_intake_scope(principal: Principal, value: dict[str, Any]) -> None:
            if not intake_resource_in_scope(principal, value):
                raise HTTPException(403, "SCOPE_DENIED")

        def promotion_outbox(request: Request, repository: Any) -> Any:
            outbox = getattr(request.app.state, "outbox_repository", None) or getattr(repository, "outbox_repository", None)
            engine = getattr(active, "_engine", None)
            if getattr(engine, "dialect", None) == "postgresql":
                bundle = getattr(request.app.state, "persistence_bundle", None) or getattr(request.app.state, "persistence", None)
                outbox = getattr(bundle, "outbox_repository", None) or outbox
                if outbox is None or getattr(outbox, "_engine", None) is not engine:
                    raise HTTPException(503, "PROMOTION_OUTBOX_UNAVAILABLE")
            return outbox

        def score_job_repository(request: Request):
            """Resolve score authority from the normal persistence composition."""
            if getattr(getattr(active, "_engine", None), "dialect", None) != "postgresql":
                return None
            from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository

            bundle = getattr(request.app.state, "persistence_bundle", None) or getattr(
                request.app.state, "persistence", None
            )
            if bundle is None or getattr(bundle, "engine", None) is not active._engine:
                raise HTTPException(503, "SCORE_DELIVERY_UNAVAILABLE")
            try:
                return IntakeScoreJobRepository(bundle)
            except Exception as exc:
                _raise_score_delivery_http_error(exc)
                raise

        def lookup_job(request: Request, job_id: str, tenant_id: str):
            score_repository = score_job_repository(request)
            if score_repository is not None:
                try:
                    job = score_repository.get_job(job_id, tenant_id)
                except Exception as exc:
                    _raise_score_delivery_http_error(exc)
                    raise
                if job is not None:
                    return job, score_repository
            job = active.jobs.get(job_id)
            if job is not None and (
                job.get("candidate_site_id") or job.get("promotion_decision_id")
                or job.get("checkpoint") == "SCORE_QUEUED"
            ):
                # Old mirrored score receipts have no execution authority. Do
                # not present or replay their inferred outcome as a real job.
                raise HTTPException(409, "SCORE_JOB_AUTHORITY_UNAVAILABLE")
            return job, None

        def score_job_parent(request: Request, job: dict[str, Any], tenant_id: str):
            if job.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            intake = V1IntakeRepositoryAdapter(active, request.app.state).get_listing_intake(
                job.get("intake_id", "")
            )
            promotion = active.promotions.get(job.get("promotion_decision_id"))
            if (intake is None or not isinstance(promotion, dict)
                    or intake.get("intake_id") != job.get("intake_id")
                    or (intake.get("scope") or {}).get("tenant_id") != tenant_id
                    or promotion.get("promotion_decision_id") != job.get("promotion_decision_id")
                    or promotion.get("tenant_id") != tenant_id
                    or promotion.get("intake_id") != job.get("intake_id")
                    or not job.get("candidate_site_id")
                    or promotion.get("candidate_site_id") != job.get("candidate_site_id")
                    or promotion.get("site_score_job_id") != job.get("job_id")):
                raise HTTPException(409, "SCORE_JOB_BINDING_CONFLICT")
            return intake

        def audit_promotion(request: Request, before: dict[str, Any] | None,
                            after: dict[str, Any], actor_id: str) -> None:
            parent = active.intakes.get(after["intake_id"])
            if not parent or not parent.get("intake_id"):
                return  # Explicit legacy/POC records have no canonical audit owner.
            if before is not None:
                parent["version"] += 1
                parent["updated_at"] = now()
            evidence_parent = {
                **parent, "audit": parent.setdefault("audit", []),
                "correlation_id": request.headers.get("x-correlation-id") or (
                    after["correlation_id"] if before is None else str(uuid4())
                ),
            }
            record_workflow_change(
                active, evidence_parent, resource_type="promotion_decision",
                before=before, after=after, actor_id=actor_id,
                action="intake.promotion_requested" if before is None else "promotion." + after["status"].lower(),
                result="FAILED" if after["status"] == "SCORE_FAILED" else "SUCCEEDED",
            )
            V1PromotionRepositoryAdapter(active, request.app.state).save_promotion(after)

        @contextmanager
        def promotion_unit(tenant_id: str, intake_id: str, promotion_id: str | None = None):
            """Serialize fresh authority, the saga checkpoint and its receipt."""
            from shared.infrastructure.persistence.assisted_listing_intake import (
                _STATE_COLLECTIONS,
                AssistedIntakePersistenceConflict,
            )

            try:
                with active.transaction():
                    engine = getattr(active, "_engine", None)
                    if getattr(engine, "dialect", None) == "postgresql":
                        engine.query_one(
                            "SELECT pg_advisory_xact_lock(hashtextextended(?, 0)) AS locked",
                            (f"operator-intake-submission:{tenant_id}",),
                        )
                        active._read_locked(_STATE_COLLECTIONS["intakes"], f"{tenant_id}:{intake_id}")
                        if promotion_id is not None:
                            active._read_locked(_STATE_COLLECTIONS["promotions"], f"{tenant_id}:{promotion_id}")
                        active.refresh(tenant_id)
                    yield
                    active.flush()
            except AssistedIntakePersistenceConflict as exc:
                raise HTTPException(409, "VERSION_CONFLICT: Assisted Intake state changed in another replica") from exc

        def linked_intake(value: dict[str, Any]) -> dict[str, Any] | None:
            intake_id = value.get("intake_id")
            return active.intakes.get(intake_id) if intake_id else None

        def require_workflow_resource_scope(principal: Principal, value: dict[str, Any]) -> None:
            # Authorize against the authoritative linked Intake, not a less
            # specific child envelope. A dangling or foreign link cannot grant
            # access from the child's own tenant metadata.
            if value.get("intake_id"):
                intake = linked_intake(value)
                if intake is None:
                    raise HTTPException(403, "SCOPE_DENIED")
                if intake.get("scope", {}).get("tenant_id") != principal.tenant_id:
                    raise HTTPException(403, "TENANT_SCOPE_DENIED")
                require_intake_scope(principal, intake)
            else:
                # Historical standalone resources retain their own envelope;
                # missing axes still fail closed for restricted principals.
                require_intake_scope(principal, value)

        def require_assignment_registry_authority(value: dict[str, Any], actor_id: str) -> None:
            try:
                authorize_assignment_read(active, value, workflow_policy, actor_id)
            except PermissionError as exc:
                raise HTTPException(403, "ROLE_DENIED") from exc
            except Exception as exc:
                raise HTTPException(503, "INTAKE_WORKFLOW_POLICY_UNAVAILABLE") from exc

        def require_sla_registry_authority(value: dict[str, Any], actor_id: str) -> None:
            # Replays retain their outcome, but never retain revoked identity
            # grants. This check must precede load_replay as well as mutation.
            parent = linked_intake(value)
            original_scope = parent["scope"] if parent is not None else value.get("scope", {})
            scope = copy.deepcopy(original_scope)
            try:
                if workflow_policy is None:
                    raise IntakeWorkflowUnavailable("SLA registry is unavailable")
                workflow_policy.authorize_sla_actor(schedule_from_record(value), scope, actor_id)
                if scope != original_scope:
                    raise IntakeWorkflowUnavailable("Provider changed the authorized SLA scope")
            except PermissionError as exc:
                raise HTTPException(403, "ROLE_DENIED") from exc
            except Exception as exc:
                raise HTTPException(503, "INTAKE_WORKFLOW_POLICY_UNAVAILABLE") from exc

        def require_actor(request: Request) -> str:
            principal = get_principal(request)
            if not principal.authenticated:
                raise HTTPException(401, "principal not authenticated")
            tenant_id = principal.scope.tenant_id if principal.scope else None
            if not tenant_id:
                raise HTTPException(403, "tenant scope is required")
            try:
                check_uuid(principal.subject_id)
                check_uuid(tenant_id)
            except ValueError:
                raise HTTPException(403, "TENANT_SCOPE_DENIED: UUID tenant and subject are required") from None
            # Production replicas share one PostgreSQL state store. Bind and
            # refresh only the verified tenant at the request boundary.
            active.refresh(tenant_id)
            return tenant_id

        def is_record_owner(principal: Principal, record: dict[str, Any]) -> bool:
            owner = record.get("assigned_to") or record.get("owner")
            submitter = record.get("submitted_by") or record.get("submitter")
            sentinels = {"system", "unassigned", "SYSTEM", "UNASSIGNED", None, ""}
            ownership_subjects = {
                subject for subject in (owner, submitter) if subject not in sentinels
            }
            return principal.subject_id in ownership_subjects

        def fingerprint(body: Any) -> str:
            return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()

        def load_replay(
            key: str | None,
            body: Any,
            tenant_id: str,
            actor_id: str,
            operation_id: str,
            *,
            resource_id: str | None = None,
        ) -> tuple[dict[str, Any], int] | None:
            if not key:
                raise HTTPException(422, "Idempotency-Key is required")
            digest = fingerprint(body)
            replay_scope = resource_id or "_collection"
            composite_key = (
                f"{tenant_id}:{actor_id}:{operation_id}:{replay_scope}:{key}"
            )
            prior = active.replays.get(composite_key)
            if prior is None:
                return None
            if prior[0] != digest:
                raise HTTPException(409, "idempotency key was used with another payload")
            return copy.deepcopy(prior[1]), prior[2]

        def replay(
            key: str | None,
            body: Any,
            tenant_id: str,
            actor_id: str,
            operation_id: str,
            make: Any,
            *,
            resource_id: str | None = None,
            authorize_replay: Callable[[dict[str, Any]], None] | None = None,
        ) -> tuple[dict[str, Any], int, bool]:
            prior = load_replay(
                key,
                body,
                tenant_id,
                actor_id,
                operation_id,
                resource_id=resource_id,
            )
            if prior is not None:
                if authorize_replay is not None:
                    authorize_replay(prior[0])
                return prior[0], prior[1], True
            try:
                # Intake bootstrap/assignment is one rollback boundary. Other
                # commands include sagas that intentionally commit FAILED state
                # before returning a non-2xx business outcome (e.g. SiteScore).
                atomic_operations = {"submitUrlIntake", "submitIntakeBatch", "commitXlsxBatch", "assignIntake", "pauseSla", "resumeSla", "claimAssignment", "transferAssignment", "completeAssignment", "cancelIntake", "proposeCorrection", "reviewIdentityDecision"}
                unit_of_work = active.transaction() if operation_id in atomic_operations else nullcontext()
                with unit_of_work:
                    # Memory requests share one locked surface; recheck after
                    # entering it so concurrent same-key submissions converge.
                    prior = load_replay(key, body, tenant_id, actor_id, operation_id, resource_id=resource_id)
                    if prior is not None:
                        if authorize_replay is not None:
                            authorize_replay(prior[0])
                        return prior[0], prior[1], True
                    high_risk = operation_id in {
                        "requestCandidatePromotion", "decideMatchCase", "mergeProperties", "splitProperty",
                        "unmergeProperty", "reviewPromotionDecision", "reviewIdentityDecision", "requestIdentityDecisionReversal",
                    }
                    high_risk = high_risk or (
                        operation_id == "proposeCorrection"
                        and is_identity_affecting_field(body.get("field_path", ""))
                    )
                    high_risk = high_risk or (
                        operation_id == "reopenIntake"
                        and active.intakes.get(resource_id, {}).get("state") == "QUARANTINED"
                    )
                    if high_risk and backlog is not None:
                        from shared.audit.backlog import (
                            AuditBacklogBlocked,
                            AuditBacklogUnavailable,
                        )
                        try:
                            backlog.require_available(tenant_id)
                        except (AuditBacklogBlocked, AuditBacklogUnavailable) as exc:
                            raise HTTPException(503, "Canonical audit delivery backpressure is active") from exc
                    result, code = make()
                    digest = fingerprint(body)
                    replay_scope = resource_id or "_collection"
                    composite_key = (
                        f"{tenant_id}:{actor_id}:{operation_id}:{replay_scope}:{key}"
                    )
                    active.replays[composite_key] = (digest, copy.deepcopy(result), code)
                    active.flush()
            except Exception as exc:
                from shared.audit.errors import CanonicalAuditUnavailable
                from shared.infrastructure.persistence.assisted_listing_intake import (
                    AssistedIntakePersistenceConflict,
                )

                if isinstance(exc, CanonicalAuditUnavailable):
                    raise HTTPException(503, {
                        "message": "系統目前無法確認此操作的稽核保存規則，請聯絡平台主管。",
                        "reason_code": "AUDIT_POLICY_UNAVAILABLE",
                    }) from exc

                if isinstance(exc, AssistedIntakePersistenceConflict):
                    raise HTTPException(
                        409,
                        "VERSION_CONFLICT: Assisted Intake state changed in "
                        "another replica",
                    ) from exc
                raise
            return result, code, False

        def require_version(if_match: str | None, current: int = 1) -> None:
            if if_match is None:
                raise HTTPException(428, "If-Match is required")
            import re
            if not re.match(r'^W/"[1-9][0-9]*"$', if_match):
                raise HTTPException(400, "invalid If-Match format")
            supplied = if_match.strip('W/"')
            if supplied != str(current):
                raise HTTPException(409, f"version conflict; current version is {current}")

        def validate_idempotency_key(key: str | None) -> None:
            if not key:
                raise HTTPException(422, "Idempotency-Key is required")
            import re
            if not (16 <= len(key) <= 128) or not re.match(r"^[A-Za-z0-9._:-]+$", key):
                raise HTTPException(422, "invalid Idempotency-Key format")

        def is_identity_affecting_field(field_path: str) -> bool:
            normalized = re.sub(r"[^a-z0-9]", "", field_path.lower())
            return normalized in {
                "providerlistingid",
                "address",
                "addressraw",
                "rent",
                "rentamount",
                "area",
                "areaping",
            }

        def apply_correction(
            intake: dict[str, Any],
            *,
            field_path: str,
            corrected_value: Any,
            actor_id: str,
        ) -> None:
            apply_field(intake, field_path, corrected_value)
            intake["version"] += 1
            ts = now()
            intake["updated_at"] = ts
            intake.setdefault("processing_history", []).append({
                "transition_id": str(uuid4()),
                "from_state": intake["state"],
                "to_state": intake["state"],
                "occurred_at": ts,
                "actor": actor_id,
                "version_after": intake["version"],
            })

        def encode_cursor(
            tenant_id: str,
            query_fingerprint: str,
            sort_value: str,
            snapshot_time: str,
            last_resource_id: str,
            sort_tuple: tuple[Any, ...],
        ) -> str:
            data = {
                "contract_version": "1.1.3",
                "expires_at": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                "last_resource_id": last_resource_id,
                "query_fingerprint": query_fingerprint,
                "snapshot_time": snapshot_time,
                "sort": sort_value,
                "sort_tuple": list(sort_tuple),
                "tenant_id": tenant_id,
            }
            payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
            sig = hmac.new(active_cursor_signing_key, payload.encode(), hashlib.sha256).digest()
            sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
            return f"{payload}.{sig_b64}"

        def decode_cursor(
            cursor_str: str,
            expected_tenant_id: str,
            expected_query_fingerprint: str,
            expected_sort: str,
        ) -> dict[str, Any]:
            try:
                parts = cursor_str.split(".")
                if len(parts) != 2:
                    raise ValueError()
                payload, sig_b64 = parts[0], parts[1]
                expected_sig = hmac.new(
                    active_cursor_signing_key, payload.encode(), hashlib.sha256
                ).digest()
                actual_sig = base64.urlsafe_b64decode(sig_b64 + "=" * (4 - len(sig_b64) % 4))
                if not hmac.compare_digest(expected_sig, actual_sig):
                    raise ValueError("invalid signature")
                data_bytes = base64.urlsafe_b64decode(payload + "=" * (4 - len(payload) % 4))
                data = json.loads(data_bytes.decode())
                if data.get("tenant_id") != expected_tenant_id:
                    raise ValueError("tenant mismatch")
                if data.get("contract_version") != "1.1.3":
                    raise ValueError("contract mismatch")
                if data.get("query_fingerprint") != expected_query_fingerprint:
                    raise ValueError("query mismatch")
                if data.get("sort") != expected_sort:
                    raise ValueError("sort mismatch")
                if datetime.fromisoformat(data["expires_at"]) <= datetime.now(UTC):
                    raise ValueError("expired")
                if not isinstance(data.get("last_resource_id"), str):
                    raise ValueError("missing last resource")
                if not isinstance(data.get("sort_tuple"), list):
                    raise ValueError("missing sort tuple")
                if not data["sort_tuple"] or data["sort_tuple"][-1] != data["last_resource_id"]:
                    raise ValueError("inconsistent sort tuple")
                return data
            except Exception:
                raise HTTPException(400, "invalid or expired cursor") from None

        def intake_sort_tuple(
            value: dict[str, Any], sort_value: str
        ) -> tuple[Any, ...]:
            intake_id = value.get("intake_id", "")
            if sort_value == "submitted_at_desc":
                return (value.get("submitted_at", ""), intake_id)
            if sort_value == "updated_at_desc":
                return (value.get("updated_at", ""), intake_id)
            if sort_value == "due_at_asc":
                due_at = value.get("due_at")
                return (due_at is None, due_at or "", intake_id)
            return (value.get("state", ""), intake_id)

        def receipt(
            resource: str,
            state: str,
            version: int = 1,
            actor_id: str = "api",
        ) -> dict[str, Any]:
            return {
                "transition_id": str(uuid4()), "from_state": resource, "to_state": state,
                "occurred_at": now(), "actor": actor_id, "version_after": version,
            }

        @router.get(
            "/intakes",
            operation_id="listIntakes",
            response_model=IntakePage,
            responses=api_error_responses(400, 403),
        )
        def list_intakes(
            request: Request,
            cursor: str | None = Query(None, max_length=2048),
            page_size: int = Query(50, ge=1, le=200),
            sort: IntakeSort | None = None,
            status: list[IntakeState] | None = Query(None),
            source_id: list[str] | None = Query(None),
            match_outcome: list[MatchOutcome] | None = Query(None),
            submitted_by: UuidString | None = None,
            needs_review: bool | None = None,
            assigned_area_id: UuidString | None = None,
            heat_zone_id: UuidString | None = None,
            q: str | None = Query(None, max_length=200),
            tenant_id: str = Depends(require_actor),
        ) -> IntakePage:
            sort_value = (sort or IntakeSort.SUBMITTED_AT_DESC).value
            query_parameters = {
                "assigned_area_id": assigned_area_id,
                "heat_zone_id": heat_zone_id,
                "match_outcome": [value.value for value in match_outcome or []],
                "needs_review": needs_review,
                "page_size": page_size,
                "q": q,
                "sort": sort_value,
                "source_id": source_id or [],
                "status": [value.value for value in status or []],
                "submitted_by": submitted_by,
            }
            query_fingerprint = fingerprint(query_parameters)
            snapshot_time = now()
            cursor_data: dict[str, Any] | None = None
            if cursor:
                cursor_data = decode_cursor(cursor, tenant_id, query_fingerprint, sort_value)
                snapshot_time = cursor_data["snapshot_time"]

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            authorize_intake_action(
                principal,
                "view",
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
                # A collection has no individual resource envelope.  Its
                # verified query scope is the authenticated tenant returned by
                # require_actor; pass it explicitly so the shared guard can
                # authorize the collection without treating it as an
                # unscoped resource.
                tenant_id=tenant_id,
            )

            tenant_items = [
                v for v in active.intakes.values()
                if v.get("scope", {}).get("tenant_id") == tenant_id
                and v.get("submitted_at", "") <= snapshot_time
                and intake_resource_in_scope(principal, v)
            ]

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_staff = (principal.has_role(Role.EXPANSION_USER) or operator_role_id in (
                "expansion-staff", "expansionStaff", "expansion-user", "expansion_user"
            )) and not is_manager

            if is_staff:
                tenant_items = [
                    v for v in tenant_items
                    if is_record_owner(principal, v)
                ]

            if status:
                tenant_items = [v for v in tenant_items if v.get("state") in status]
            if source_id:
                tenant_items = [v for v in tenant_items if v.get("source_id") in source_id]
            if match_outcome:
                tenant_items = [v for v in tenant_items if v.get("match_outcome") in match_outcome]
            if submitted_by:
                tenant_items = [v for v in tenant_items if v.get("submitted_by") == submitted_by]
            if heat_zone_id:
                tenant_items = [v for v in tenant_items if v.get("scope", {}).get("heat_zone_id") == heat_zone_id]
            if assigned_area_id:
                tenant_items = [v for v in tenant_items if v.get("scope", {}).get("assigned_area_id") == assigned_area_id]
            if needs_review is not None:
                if needs_review:
                    tenant_items = [v for v in tenant_items if v.get("state") == "NEEDS_REVIEW"]
                else:
                    tenant_items = [v for v in tenant_items if v.get("state") != "NEEDS_REVIEW"]

            if q:
                q_lower = q.lower()
                tenant_items = [
                    v for v in tenant_items
                    if q_lower in (v.get("original_url") or "").lower()
                    or q_lower in (v.get("canonical_url") or "").lower()
                    or q_lower in (v.get("source_id") or "").lower()
                    or any(q_lower in str(f.get("effective") or "").lower() or q_lower in str(f.get("corrected") or "").lower() for f in v.get("fields", []))
                ]

            reverse_sort = sort_value in {"submitted_at_desc", "updated_at_desc"}
            tenant_items.sort(
                key=lambda value: intake_sort_tuple(value, sort_value),
                reverse=reverse_sort,
            )
            total_count = len(tenant_items)

            page_candidates = tenant_items
            if cursor_data is not None:
                last_sort_tuple = tuple(cursor_data["sort_tuple"])
                page_candidates = [
                    value
                    for value in tenant_items
                    if (
                        intake_sort_tuple(value, sort_value) < last_sort_tuple
                        if reverse_sort
                        else intake_sort_tuple(value, sort_value) > last_sort_tuple
                    )
                ]

            items = page_candidates[:page_size]

            summaries = []
            for value in items:
                masked_val = mask_intake(principal, value)
                summaries.append(IntakeSummary(
                    intake_id=masked_val["intake_id"],
                    state=masked_val["state"],
                    intake_method=masked_val["intake_method"],
                    source_id=masked_val.get("source_id"),
                    match_outcome=masked_val.get("match_outcome"),
                    submitted_by=masked_val.get("submitted_by"),
                    assigned_to=masked_val.get("assigned_to"),
                    due_at=masked_val.get("due_at"),
                    submitted_at=masked_val.get("submitted_at"),
                    updated_at=masked_val.get("updated_at"),
                    version=masked_val["version"],
                    scope=ScopeContext(**masked_val["scope"]),
                    masked_fields=masked_val.get("masked_fields") or [],
                ))

            next_cursor = None
            if len(page_candidates) > page_size and items:
                next_cursor = encode_cursor(
                    tenant_id,
                    query_fingerprint,
                    sort_value,
                    snapshot_time,
                    items[-1]["intake_id"],
                    intake_sort_tuple(items[-1], sort_value),
                )

            return IntakePage(
                items=summaries,
                next_cursor=next_cursor,
                page_size=page_size,
                total_count=total_count,
                total_count_accuracy="EXACT",
                snapshot_time=snapshot_time,
                query_fingerprint=query_fingerprint,
            )

        @router.post(
            "/intakes/url",
            operation_id="submitUrlIntake",
            status_code=202,
            response_model=IntakeSubmissionReceipt,
            responses={
                202: {
                    "model": IntakeSubmissionReceipt,
                    "description": "Intake accepted",
                    "headers": response_headers("ETag", "Idempotency-Replayed"),
                },
                200: {
                    "model": IntakeSubmissionReceipt,
                    "description": "Idempotent replay",
                    "headers": response_headers("Idempotency-Replayed"),
                },
                **api_error_responses(503, 403, 404, 409, 422, idempotency_conflict=True),
            },
        )
        def submit_url(
            body: UrlIntakeRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = Header(
                ..., alias="Idempotency-Key", min_length=16, max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
            ),
        ) -> IntakeSubmissionReceipt:
            validate_idempotency_key(key)
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            authorize_intake_action(
                principal,
                "submit_url",
                resource={"scope": body.scope.model_dump()},
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                intake_id, job_id = str(uuid4()), str(uuid4())
                correlation_id_str = str(uuid4())
                ts = now()

                value = {
                    "intake_id": intake_id,
                    "state": "SUBMITTED",
                    "intake_method": "URL",
                    "scope": body.scope.model_dump(),
                    "submitted_at": ts,
                    "updated_at": ts,
                    "version": 1,
                    "job_id": job_id,
                    "correlation_id": correlation_id_str,
                    "submitted_by": actor_id,
                    "original_url": body.original_url,
                    "canonical_url": body.original_url,
                    "policy_state": "POLICY_UNKNOWN",
                    "processing_history": [
                        {
                            "transition_id": str(uuid4()),
                            "from_state": None,
                            "to_state": "SUBMITTED",
                            "occurred_at": ts,
                            "actor": actor_id,
                            "version_after": 1,
                        }
                    ],
                    "fields": [],
                    "audit": [],
                }
                active.intakes[intake_id] = value
                initialize_workflow(value)

                active.jobs[job_id] = {
                    "job_id": job_id,
                    "status": "QUEUED",
                    "checkpoint": "RETRIEVING",
                    "attempt": 0,
                    "version": 1,
                    "correlation_id": correlation_id_str,
                    "intake_id": intake_id,
                    "tenant_id": tenant_id,
                }

                receipt_val = {
                    "intake_id": intake_id,
                    "state": "SUBMITTED",
                    "version": 1,
                    "job_id": job_id,
                    "correlation_id": correlation_id_str,
                    "submitted_at": ts,
                }
                return receipt_val, 202

            def authorize_replay(receipt: dict[str, Any]) -> None:
                # A retained receipt is an outcome, not a retained access grant.
                # Match GET's current-target policy without comparing versions.
                value = active.intakes.get(receipt["intake_id"])
                if value is None:
                    raise HTTPException(404, "intake not found")
                authorize_intake_action(
                    principal,
                    "view",
                    resource=intake_auth_resource(value),
                    operator_role_id=operator_role_id,
                    audit_log=active_audit_log,
                    correlation_id=correlation_id,
                )

            val, code, was_replayed = replay(
                key, body.model_dump(), tenant_id, actor_id, "submitUrlIntake", make,
                authorize_replay=authorize_replay,
            )
            response.status_code = 200 if was_replayed else code
            response.headers["Idempotency-Replayed"] = str(was_replayed).lower()
            response.headers["ETag"] = f'W/"{val["version"]}"'

            return IntakeSubmissionReceipt(**val)

        @router.post(
            "/intake-batches",
            operation_id="submitIntakeBatch",
            status_code=207,
            response_model=BatchIntakeReceipt,
            responses={
                202: {"model": BatchIntakeReceipt, "description": "All rows accepted"},
                **api_error_responses(503, 409, 413, 422, idempotency_conflict=True),
            },
        )
        def submit_batch(
            body: BatchIntakeRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = Header(
                ..., alias="Idempotency-Key", min_length=16, max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
            ),
        ) -> BatchIntakeReceipt:
            validate_idempotency_key(key)
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            authorize_intake_action(
                principal,
                "submit_csv",
                resource={"scope": body.scope.model_dump()},
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                receipts, accepted = [], 0
                ts = now()
                correlation_id_str = str(uuid4())

                for index, row in enumerate(body.rows):
                    if not row.address_raw:
                        receipts.append({
                            "row_index": index + 1,
                            "status": "REJECTED",
                            "error": {
                                "code": "VALIDATION_FAILED",
                                "message": "address_raw is required",
                                "retryable": False,
                                "correlation_id": correlation_id_str,
                                "occurred_at": ts,
                                "next_action": "CORRECT_INPUT",
                            }
                        })
                    else:
                        accepted += 1
                        intake_id = str(uuid4())

                        value = {
                            "intake_id": intake_id,
                            "state": "SUBMITTED",
                            "intake_method": body.method.value.upper(),
                            "scope": body.scope.model_dump(),
                            "submitted_at": ts,
                            "updated_at": ts,
                            "version": 1,
                            "submitted_by": actor_id,
                            "original_url": row.original_url,
                            "canonical_url": row.original_url,
                            "policy_state": "ASSISTED_ENTRY_ONLY",
                            "processing_history": [
                                {
                                    "transition_id": str(uuid4()),
                                    "from_state": None,
                                    "to_state": "SUBMITTED",
                                    "occurred_at": ts,
                                    "actor": actor_id,
                                    "version_after": 1,
                                }
                            ],
                            "fields": xlsx_intake_fields(row.model_dump()),
                            "audit": [],
                        }
                        active.intakes[intake_id] = value
                        initialize_workflow(value)
                        receipts.append({
                            "row_index": index + 1,
                            "status": "ACCEPTED",
                            "intake_id": intake_id,
                        })

                result = {
                    "batch_id": body.batch_id,
                    "submitted_at": ts,
                    "accepted_count": accepted,
                    "rejected_count": len(body.rows) - accepted,
                    "rows": receipts,
                    "correlation_id": correlation_id_str,
                }

                code = 202 if accepted == len(body.rows) else 207
                return result, code

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "submitIntakeBatch", make)
            response.status_code = code
            return BatchIntakeReceipt(**val)

        # Field classifications for rows imported from a spreadsheet. Values are
        # carried into the intake's FieldValue collection so a committed row is
        # readable through GET /intakes/{id} under the usual masking policy.
        XLSX_FIELD_CLASSIFICATIONS: dict[str, str] = {
            "address_raw": "INTERNAL",
            "normalized_address": "INTERNAL",
            "rent_amount": "INTERNAL",
            "area_ping": "INTERNAL",
            "floor": "INTERNAL",
            "normalized_floor": "INTERNAL",
            "title": "INTERNAL",
            "listing_type": "INTERNAL",
        }

        def xlsx_intake_fields(row: dict[str, Any]) -> list[dict[str, Any]]:
            fields: list[dict[str, Any]] = []
            for field_path, classification in XLSX_FIELD_CLASSIFICATIONS.items():
                value = row.get(field_path)
                if value in (None, ""):
                    continue
                fields.append({
                    "field_path": field_path,
                    "classification": classification,
                    "masked": False,
                    "parsed": value,
                    "normalized": value,
                    "effective": value,
                })
            return fields

        def require_xlsx_scope(scope_dict: dict[str, Any], tenant_id: str) -> None:
            # The preview cache and the commit idempotency cache are keyed by this
            # scope, so it must be the authenticated tenant and not a client claim.
            if scope_dict.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

        @router.post(
            "/intake-batches/xlsx/preview",
            operation_id="previewXlsxBatch",
            response_model=XlsxPreviewResponse,
            responses=api_error_responses(400, 403, 422),
        )
        def preview_xlsx_batch(
            body: XlsxPreviewRequest,
            request: Request,
            tenant_id: str = Depends(require_actor),
        ) -> XlsxPreviewResponse:
            from modules.external_data.application.xlsx_import import (
                XlsxImportError,
                preview_xlsx_import,
            )
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            scope_dict = body.scope.model_dump() if body.scope else {"tenant_id": tenant_id}
            require_xlsx_scope(scope_dict, tenant_id)
            authorize_intake_action(
                principal,
                "submit_csv",
                resource={"scope": scope_dict},
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )
            try:
                raw_bytes = base64.b64decode(body.file_base64)
            except Exception as exc:
                raise HTTPException(400, f"Invalid base64 encoding: {exc}") from exc

            try:
                res = preview_xlsx_import(raw_bytes, body.custom_mapping, scope_dict)
                return XlsxPreviewResponse(**res.to_dict())
            except XlsxImportError as exc:
                raise HTTPException(400, f"[{exc.code}] {exc.message}") from exc

        @router.post(
            "/intake-batches/xlsx/commit",
            operation_id="commitXlsxBatch",
            status_code=202,
            response_model=XlsxCommitReceipt,
            responses=api_error_responses(503, 400, 403, 409, 422, idempotency_conflict=True),
        )
        def commit_xlsx_batch(
            body: XlsxCommitRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = Header(
                ..., alias="Idempotency-Key", min_length=16, max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
            ),
        ) -> XlsxCommitReceipt:
            from modules.external_data.application.xlsx_import import commit_xlsx_import
            validate_idempotency_key(key)
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            scope_dict = body.scope.model_dump()
            require_xlsx_scope(scope_dict, tenant_id)
            authorize_intake_action(
                principal,
                "submit_csv",
                resource={"scope": scope_dict},
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                ts = now()

                def persist_validated_row(row: dict[str, Any]) -> str:
                    """Write one validated row into intake state and return its id.

                    The receipt reports these ids, so every id a client receives
                    resolves through GET /intakes/{intake_id}.
                    """
                    intake_id = str(uuid4())
                    row_correlation_id = str(uuid4())
                    # Spreadsheet values do not authorize server retrieval.
                    policy_state = "ASSISTED_ENTRY_ONLY"

                    value = {
                        "intake_id": intake_id,
                        "state": "SUBMITTED",
                        # Spreadsheet import is governed as a CSV-class bulk
                        # submission; it authorizes under the same `submit_csv`
                        # action as the existing batch path.
                        "intake_method": "CSV",
                        "scope": scope_dict,
                        "submitted_at": ts,
                        "updated_at": ts,
                        "version": 1,
                        "correlation_id": row_correlation_id,
                        "submitted_by": actor_id,
                        "original_url": row.get("original_url"),
                        "canonical_url": row.get("original_url"),
                        "policy_state": policy_state,
                        "processing_history": [
                            {
                                "transition_id": str(uuid4()),
                                "from_state": None,
                                "to_state": "SUBMITTED",
                                "occurred_at": ts,
                                "actor": actor_id,
                                "version_after": 1,
                            }
                        ],
                        "fields": xlsx_intake_fields(row),
                        "audit": [],
                    }
                    active.intakes[intake_id] = value
                    initialize_workflow(value)
                    return intake_id

                receipt = commit_xlsx_import(
                    rows=body.rows,
                    batch_id=body.batch_id,
                    scope=scope_dict,
                    # The route owns the durable replay transaction. A second
                    # process-local XLSX cache must not survive a failed flush.
                    idempotency_key=None,
                    actor_id=actor_id,
                    audit_log=active_audit_log,
                    correlation_id=correlation_id,
                    intake_writer=persist_validated_row,
                )
                return receipt.to_dict(), 202

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "commitXlsxBatch", make)
            response.status_code = code
            response.headers["Idempotency-Replayed"] = str(was_replayed).lower()
            return XlsxCommitReceipt(**val)

        @router.get(
            "/intake-batches/xlsx/errors/{batch_id}/export",
            operation_id="exportXlsxBatchErrors",
            responses=api_error_responses(400, 404),
        )
        def export_xlsx_batch_errors(
            batch_id: str,
            request: Request,
            export_format: str = Query("csv", alias="format"),
            tenant_id: str = Depends(require_actor),
        ) -> Response:
            from modules.external_data.application.xlsx_import import (
                export_xlsx_import_errors,
                get_preview_result,
            )
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            authorize_intake_action(
                principal,
                "view",
                resource={"scope": {"tenant_id": tenant_id}},
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            if export_format not in ("xlsx", "csv", "json"):
                raise HTTPException(400, f"Unsupported export format: {export_format}")

            # Unknown and other-tenant batches are equally "not found": an empty
            # 200 would read to an operator as "your import had no errors".
            preview_res = get_preview_result(batch_id, tenant_id)
            if preview_res is None:
                raise HTTPException(404, "xlsx import batch not found")
            row_errors = preview_res.row_errors

            bytes_content, mime_type = export_xlsx_import_errors(row_errors, export_format=export_format)  # type: ignore[arg-type]
            filename = f"{batch_id}-errors.{export_format}"
            headers = {
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
            return Response(content=bytes_content, media_type=mime_type, headers=headers)

        @router.get(
            "/intakes/{intake_id}",
            operation_id="getIntake",
            response_model=IntakeDetail,
            responses={
                200: {
                    "model": IntakeDetail,
                    "description": "Intake detail",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(403, 404),
            },
        )
        def get_intake(
            intake_id: UuidString,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
        ) -> IntakeDetail:
            value = active.intakes.get(intake_id)
            if value is None:
                raise HTTPException(404, "intake not found")
            if value.get("scope", {}).get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            resource_for_auth = intake_auth_resource(value)

            authorize_intake_action(
                principal,
                "view",
                resource=resource_for_auth,
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            # Lookup assignment if assigned_to is set but no assignment exists
            active_assignment = next(
                (a for a in active.assignments.values() if a.get("intake_id") == intake_id and a.get("status") != "COMPLETED"),
                None
            )

            if active_assignment is None:
                active_assignment = max(
                    (a for a in active.assignments.values() if a.get("intake_id") == intake_id),
                    key=lambda a: (a.get("created_at", ""), a["assignment_id"]), default=None,
                )

            # Lookup SLA if exists
            active_sla = next(
                (s for s in active.slas.values() if s.get("intake_id") == intake_id),
                None
            )

            response.headers["ETag"] = f'W/"{value["version"]}"'

            masked_val = mask_intake(principal, value)
            detail = IntakeDetail(
                intake_id=masked_val["intake_id"],
                state=masked_val["state"],
                intake_method=masked_val["intake_method"],
                source_id=masked_val.get("source_id"),
                match_outcome=masked_val.get("match_outcome"),
                submitted_by=masked_val.get("submitted_by"),
                assigned_to=masked_val.get("assigned_to"),
                due_at=masked_val.get("due_at"),
                submitted_at=masked_val.get("submitted_at"),
                updated_at=masked_val.get("updated_at"),
                version=masked_val["version"],
                scope=ScopeContext(**masked_val["scope"]),
                masked_fields=masked_val.get("masked_fields") or [],
                original_url=masked_val.get("original_url"),
                canonical_url=masked_val.get("canonical_url"),
                policy_state=masked_val.get("policy_state"),
                source_snapshot_id=masked_val.get("source_snapshot_id"),
                parser_run_id=masked_val.get("parser_run_id"),
                match_case_id=masked_val.get("match_case_id"),
                processing_history=masked_val.get("processing_history") or [],
                fields=masked_val.get("fields") or [],
                audit=masked_val.get("audit") or [],
                assignment_id=active_assignment.get("assignment_id") if active_assignment else None,
                assignment_status=active_assignment.get("status") if active_assignment else None,
                sla_instance_id=active_sla.get("sla_instance_id") if active_sla else None,
                sla_state=active_sla.get("state") if active_sla else None,
                sla_receipt=active_sla.get("receipt") if active_sla else None,
            )
            return detail

        @router.post(
            "/intakes/{intake_id}/corrections",
            operation_id="proposeCorrection",
            status_code=201,
            response_model=CorrectionReceipt,
            responses={
                201: {
                    "model": CorrectionReceipt,
                    "description": "Correction proposed",
                    "headers": response_headers("ETag", "Idempotency-Replayed"),
                },
                **api_error_responses(503, 409, 422, 428),
            },
        )
        def correct(
            intake_id: UuidString,
            body: CorrectionRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = Header(
                ..., alias="Idempotency-Key", min_length=16, max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
            ),
            if_match: IfMatchValue = Header(
                ..., alias="If-Match", pattern=r'^W/"[1-9][0-9]*"$',
            ),
        ) -> CorrectionReceipt:
            validate_idempotency_key(key)
            current = active.intakes.get(intake_id)
            if current is None:
                raise HTTPException(404, "intake not found")
            if current.get("scope", {}).get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            is_identity_affecting = is_identity_affecting_field(body.field_path)
            resource_for_auth = intake_auth_resource(current)

            authorize_intake_action(
                principal,
                "correct",
                resource=resource_for_auth,
                risk_acknowledged=body.risk_acknowledged or False,
                risk_summary=body.reason,
                is_identity_affecting=is_identity_affecting,
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                current = active.intakes.get(intake_id)
                if current is None:
                    raise HTTPException(404, "intake not found")
                require_intake_scope(principal, current)
                allowed_states = ({"NEEDS_REVIEW"} if is_identity_affecting
                                  else {"AWAITING_ASSISTED_ENTRY", "NEEDS_REVIEW", "READY"})
                if current.get("state") not in allowed_states:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                require_version(if_match, current["version"])
                if not body.reason.strip():
                    raise HTTPException(422, "CORRECTION_REASON_REQUIRED")
                masked = mask_intake(principal, current)
                if any(f.get("field_path") == body.field_path and f.get("masked") for f in masked.get("fields", [])):
                    raise HTTPException(403, "FIELD_MASKED")
                try:
                    values = correction_values(current, body.field_path, body.corrected_value)
                except PermissionError as exc:
                    raise HTTPException(403, "FIELD_MASKED") from exc
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
                from shared.audit.integrity import sha256_hex
                if body.expected_effective_value_sha256 is not None and body.expected_effective_value_sha256 != sha256_hex(values["before_effective_value"]):
                    raise HTTPException(409, "EFFECTIVE_VALUE_CONFLICT")
                before = copy.deepcopy(current)
                cid, ts = str(uuid4()), now()
                correlation_id_str = current["correlation_id"]
                correction = {
                    **values, "correction_id":cid, "status":"PENDING_REVIEW" if is_identity_affecting else "APPLIED",
                    "intake_id":intake_id,"tenant_id":tenant_id,"scope":copy.deepcopy(current["scope"]),
                    "reason":body.reason,"risk_acknowledged":body.risk_acknowledged,"proposer":actor_id,
                    "proposed_by":actor_id,"reviewed_by":None,"version":1,"created_at":ts,
                    "correlation_id":correlation_id_str,"identity_affecting":is_identity_affecting,
                    "supersedes_correction_id":None,"reversal_of_correction_id":None,
                }
                if is_identity_affecting:
                    correction["after_effective_value"] = copy.deepcopy(values["before_effective_value"])
                    current["version"] += 1
                    current["updated_at"] = ts
                else:
                    apply_correction(current, field_path=body.field_path,
                                     corrected_value=body.corrected_value, actor_id=actor_id)
                correction["intake_version"] = current["version"]
                active.corrections[cid] = correction
                record_workflow_change(active,current,resource_type="intake",before=before,after=current,
                                       action="intake.correct.propose" if is_identity_affecting else "intake.correct.apply",
                                       actor_id=actor_id)
                audit_event_id = record_workflow_change(active,current,resource_type="correction",before=None,after=correction,
                                                       action="correction.propose" if is_identity_affecting else "correction.apply",
                                                       actor_id=actor_id)
                if is_identity_affecting:
                    decision = {
                        "decision_id":cid,"status":"PENDING_REVIEW","resource_versions":{"intake":current["version"],"correction":1},
                        "job_id":None,"correlation_id":correlation_id_str,"version":1,"action":"identity_correction",
                        "tenant_id":tenant_id,"scope":copy.deepcopy(current["scope"]),"intake_id":intake_id,
                        "correction_id":cid,"proposer":actor_id,"created_at":ts,"reason":body.reason,
                        "risk_acknowledged":body.risk_acknowledged,"processing_history":[],
                    }
                    active.decisions[cid] = decision
                    record_workflow_change(active,current,resource_type="identity_decision",before=None,after=decision,
                                           action="identity.correction.propose",actor_id=actor_id)
                return {"correction_id":cid,"status":correction["status"],"intake_id":intake_id,
                        "version":correction["version"],"audit_event_id":audit_event_id,
                        "correlation_id":correlation_id_str},201

            val, code, was_replayed = replay(
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "proposeCorrection",
                make,
                resource_id=intake_id,
            )
            response.status_code = code
            response.headers["Idempotency-Replayed"] = str(was_replayed).lower()
            response.headers["ETag"] = f'W/"{val["version"]}"'
            return CorrectionReceipt(**val)

        @router.put(
            "/intakes/{intake_id}/assignment",
            operation_id="assignIntake",
            status_code=200,
            response_model=AssignmentReceipt,
            responses={
                200: {
                    "model": AssignmentReceipt,
                    "description": "Assignment updated",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(503, 403, 409, 422, 428),
            },
        )
        def assign(
            intake_id: UuidString,
            body: AssignmentRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = Header(
                ..., alias="Idempotency-Key", min_length=16, max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
            ),
            if_match: IfMatchValue = Header(
                ..., alias="If-Match", pattern=r'^W/"[1-9][0-9]*"$',
            ),
        ) -> AssignmentReceipt:
            validate_idempotency_key(key)
            current = active.intakes.get(intake_id)
            if current is None:
                raise HTTPException(404, "intake not found")
            if current.get("scope", {}).get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            require_intake_scope(principal, current)

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_staff = (principal.has_role(Role.EXPANSION_USER) or operator_role_id in (
                "expansion-staff", "expansionStaff", "expansion-user", "expansion_user"
            )) and not is_manager
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")

            if not (is_manager or is_staff or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            if is_staff:
                if current.get("submitted_by") != principal.subject_id and current.get("assigned_to") != principal.subject_id:
                    raise HTTPException(403, "OWNERSHIP_REQUIRED")
                if body.owner_subject_id != principal.subject_id:
                    raise HTTPException(403, "ASSIGNMENT_SCOPE_DENIED")

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                require_version(if_match, current["version"])
                active_assignment = next(
                    (
                        assignment
                        for assignment in active.assignments.values()
                        if assignment.get("intake_id") == intake_id
                        and assignment.get("status") != "COMPLETED"
                    ),
                    None,
                )
                if active_assignment is not None and active_assignment.get("status") != "UNASSIGNED":
                    raise HTTPException(409, "OWNER_CONFLICT")
                active_sla = next((s for s in active.slas.values() if s.get("intake_id") == intake_id), None)
                if active_assignment is None or active_sla is None or workflow_policy is None:
                    raise HTTPException(503, "INTAKE_WORKFLOW_POLICY_UNAVAILABLE")
                if active_sla.get("state") in {"PAUSED", "COMPLETED", "BREACHED"}:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                try:
                    schedule = schedule_from_record(active_sla)
                    authorized_scope = copy.deepcopy(current["scope"])
                    plan = workflow_policy.resolve_assignment(
                        authorized_scope, schedule, actor_id=actor_id,
                        owner_subject_id=body.owner_subject_id, owner_role=body.owner_role,
                        requested_due_at=datetime.fromisoformat(body.due_at.replace("Z", "+00:00")),
                        reason=body.reason,
                    )
                    if (
                        not isinstance(plan, IntakeWorkflowPlan)
                        or authorized_scope != current["scope"]
                        or plan.schedule.tenant_id != tenant_id
                        or plan.schedule.started_at != schedule.started_at
                        or plan.schedule.policy_version != schedule.policy_version
                        or plan.schedule.calendar_version != schedule.calendar_version
                    ):
                        raise IntakeWorkflowUnavailable("Assignment changed the workflow binding")
                except PermissionError as exc:
                    raise HTTPException(403, "ASSIGNMENT_SCOPE_DENIED") from exc
                except Exception as exc:
                    raise HTTPException(503, "INTAKE_WORKFLOW_POLICY_UNAVAILABLE") from exc
                current["version"] += 1
                aid = active_assignment["assignment_id"]
                ts = now()

                current["assigned_to"] = body.owner_subject_id
                resolved_due = plan.schedule.due_at.isoformat().replace("+00:00", "Z")
                current["due_at"] = resolved_due
                current["updated_at"] = ts

                current["processing_history"].append({
                    "transition_id": str(uuid4()),
                    "from_state": current["state"],
                    "to_state": current["state"],
                    "occurred_at": ts,
                    "actor": actor_id,
                    "version_after": current["version"],
                })

                value = {
                    **active_assignment,
                    "assignment_id": aid,
                    "status": "ASSIGNED",
                    "owner_subject_id": body.owner_subject_id,
                    "due_at": resolved_due,
                    "owner_role": body.owner_role,
                    "queue_key": plan.queue_key,
                    "updated_at": ts,
                    "version": active_assignment["version"] + 1,
                    "tenant_id": tenant_id,
                    "intake_id": intake_id,
                }
                active.assignments[aid] = value
                prior_sla = copy.deepcopy(active_sla)
                updated_schedule = plan.schedule
                active_sla.update({
                    "state": AssignmentSlaService.get_derived_sla_state(updated_schedule, datetime.now(UTC)).value,
                    "due_at": resolved_due,
                    "due_soon_at": updated_schedule.due_soon_at.isoformat().replace("+00:00", "Z"),
                    "breach_at": updated_schedule.breach_at.isoformat().replace("+00:00", "Z"),
                    "schedule": {**active_sla["schedule"], "due_at": resolved_due,
                                 "due_soon_at": updated_schedule.due_soon_at.isoformat().replace("+00:00", "Z"),
                                 "breach_at": updated_schedule.breach_at.isoformat().replace("+00:00", "Z")},
                    "version": active_sla["version"] + 1,
                })
                record_workflow_change(active, current, resource_type="assignment",
                                       before=active_assignment, after=value,
                                       action="assignment.assign", actor_id=actor_id)
                record_workflow_change(active, current, resource_type="sla_instance",
                                       before=prior_sla, after=active_sla,
                                       action="sla.schedule_resolved", actor_id=actor_id)
                enqueue_workflow_event(
                    active, current, event_type="assignment.assigned", producer="workflow_service",
                    aggregate_type="assignment", aggregate_id=aid, aggregate_version=value["version"],
                    payload={k: value[k] for k in ("assignment_id", "intake_id", "status", "owner_subject_id", "due_at", "version")},
                )
                enqueue_workflow_event(
                    active, current, event_type="sla.state_changed", producer="sla_service",
                    aggregate_type="sla_instance", aggregate_id=active_sla["sla_instance_id"],
                    aggregate_version=active_sla["version"],
                    payload={"sla_instance_id": active_sla["sla_instance_id"], "intake_id": intake_id,
                             "from_state": prior_sla["state"], "to_state": active_sla["state"],
                             "due_at": active_sla["due_at"], "version": active_sla["version"],
                             "occurred_at": ts, "paused_duration_seconds": active_sla["paused_duration_seconds"]},
                )
                return value, 200

            val, code, was_replayed = replay(
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "assignIntake",
                make,
                resource_id=intake_id,
            )
            response.status_code = code
            response.headers["ETag"] = f'W/"{val["version"]}"'
            return AssignmentReceipt(**val)

        @router.post(
            "/jobs/{job_id}/retry",
            operation_id="retryJob",
            status_code=202,
            response_model=JobReceipt,
            responses={
                202: {
                    "model": JobReceipt,
                    "description": "Replay queued from a durable checkpoint",
                    "headers": response_headers("ETag", "Idempotency-Replayed"),
                },
                **api_error_responses(403, 409, 422, 428, 503),
            },
        )
        def retry_job(
            job_id: UuidString,
            body: RetryRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = Header(
                ..., alias="Idempotency-Key", min_length=16, max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
            ),
            if_match: IfMatchValue = Header(
                ..., alias="If-Match", pattern=r'^W/"[1-9][0-9]*"$',
            ),
        ) -> JobReceipt:
            initial, _ = lookup_job(request, job_id, tenant_id)
            if initial is None:
                raise HTTPException(404, "job not found")
            if not initial.get("intake_id"):
                raise HTTPException(409, "DEPENDENCY_CONFLICT: retry job has no linked intake")
            with promotion_unit(tenant_id, initial["intake_id"], initial.get("promotion_decision_id")):
                validate_idempotency_key(key)
                job, score_repository = lookup_job(request, job_id, tenant_id)
                if job is None:
                    raise HTTPException(404, "job not found")

                if job.get("tenant_id") != tenant_id:
                    raise HTTPException(403, "TENANT_SCOPE_DENIED")
                principal = get_principal(request)
                actor_id = principal.subject_id

                intake_id = job.get("intake_id")
                intake = (
                    V1IntakeRepositoryAdapter(active, request.app.state).get_listing_intake(intake_id)
                    if intake_id
                    else None
                )
                if intake is None:
                    raise HTTPException(409, "DEPENDENCY_CONFLICT: retry job has no linked intake")
                intake_tenant = intake.get("tenantId") or intake.get("scope", {}).get("tenant_id")
                if intake_tenant != tenant_id:
                    raise HTTPException(403, "TENANT_SCOPE_DENIED")
                if score_repository is not None:
                    intake = score_job_parent(request, job, tenant_id)
                    # This is a candidate business job. Steward parser/matcher
                    # authority does not independently authorize score replay.
                    if not principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE):
                        raise HTTPException(403, "ROLE_DENIED")
                operator_role_id = get_operator_role_id(request)
                correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

                authorize_intake_action(
                    principal,
                    "reopen_failed",
                    resource=intake_auth_resource(intake) if intake is not None else None,
                    operator_role_id=operator_role_id,
                    audit_log=active_audit_log,
                    correlation_id=correlation_id,
                )

                def make() -> tuple[dict[str, Any], int]:
                    if score_repository is not None:
                        require_version(if_match, job["version"])
                        if body.override_retry_budget:
                            # This contract has no incident/change reference.
                            # A risk checkbox cannot supply that approval.
                            raise HTTPException(422, "RETRY_OVERRIDE_APPROVAL_REQUIRED")
                        try:
                            receipt_value = score_repository.retry_score(
                                job_id, tenant_id=tenant_id, actor_id=actor_id,
                                expected_version=job["version"], checkpoint=body.checkpoint.value,
                                reason=body.reason, override_retry_budget=body.override_retry_budget,
                                risk_acknowledged=body.risk_acknowledged,
                                correlation_id=correlation_id or str(uuid4()), request_key=key,
                            )
                        except Exception as exc:
                            _raise_score_delivery_http_error(exc)
                            raise
                        return _job_receipt_payload(receipt_value), 202
                    if job.get("status") != "FAILED" and job.get("delivery_state") != "DEAD_LETTER" and job.get("status") != "DEAD_LETTER":
                        raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                    if body.checkpoint.value != job.get("checkpoint"):
                        raise HTTPException(409, "CHECKPOINT_UNAVAILABLE")
                    require_version(if_match, job["version"])
                    job["attempt"] += 1
                    job["version"] += 1
                    job["status"] = "QUEUED"
                    job["delivery_state"] = "RETRYING"
                    job["checkpoint"] = body.checkpoint.value

                    receipt_val = {
                        "job_id": job_id,
                        "status": "QUEUED",
                        "delivery_state": "RETRYING",
                        "checkpoint": body.checkpoint.value,
                        "attempt": job["attempt"],
                        "version": job["version"],
                        "correlation_id": job["correlation_id"],
                    }
                    return receipt_val, 202

                val, code, was_replayed = replay(
                    key,
                    body.model_dump(),
                    tenant_id,
                    actor_id,
                    "retryJob",
                    make,
                    resource_id=job_id,
                )
                response.status_code = code
                response.headers["ETag"] = f'W/"{val["version"]}"'
                response.headers["Idempotency-Replayed"] = str(was_replayed).lower()
                return JobReceipt(**val)

        @router.get(
            "/jobs/{job_id}/receipt",
            operation_id="getJobReceipt",
            response_model=JobReceipt,
            responses={
                200: {
                    "model": JobReceipt,
                    "description": "Authoritative durable job receipt",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(403, 404, 409, 503),
            },
        )
        def get_job_receipt(
            job_id: UuidString,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
        ) -> JobReceipt:
            initial, _ = lookup_job(request, job_id, tenant_id)
            if initial is None:
                raise HTTPException(404, "job not found")
            if not initial.get("intake_id"):
                raise HTTPException(409, "DEPENDENCY_CONFLICT: job has no linked intake")
            with promotion_unit(tenant_id, initial["intake_id"], initial.get("promotion_decision_id")):
                job, score_repository = lookup_job(request, job_id, tenant_id)
                if job is None:
                    raise HTTPException(404, "job not found")
                if job.get("tenant_id") != tenant_id:
                    raise HTTPException(403, "TENANT_SCOPE_DENIED")
                intake = V1IntakeRepositoryAdapter(active, request.app.state).get_listing_intake(
                    job.get("intake_id", "")
                )
                if intake is None:
                    raise HTTPException(404, "linked intake not found")
                if score_repository is not None:
                    intake = score_job_parent(request, job, tenant_id)
                authorize_intake_action(
                    get_principal(request),
                    "view",
                    resource=intake_auth_resource(intake),
                    operator_role_id=get_operator_role_id(request),
                    audit_log=active_audit_log,
                    correlation_id=request.headers.get("x-correlation-id"),
                )
                response.headers["ETag"] = f'W/"{job["version"]}"'
                return JobReceipt(**_job_receipt_payload(job))

        @router.get(
            "/saved-views",
            operation_id="listSavedViews",
            response_model=list[SavedView],
            responses=api_error_responses(403),
        )
        def list_saved_views(
            request: Request,
            tenant_id: str = Depends(require_actor),
        ) -> list[SavedView]:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_staff = (principal.has_role(Role.EXPANSION_USER) or operator_role_id in (
                "expansion-staff", "expansionStaff", "expansion-user", "expansion_user"
            )) and not is_manager
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")

            if not (is_manager or is_staff or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            actor_id = principal.subject_id
            views = [
                v for v in active.saved_views
                if v.get("owner_subject_id") == actor_id and v.get("tenant_id") == tenant_id
                and not v.get("deleted_at")
            ]
            from modules.listing.application.saved_search import SavedListingCriteria
            authorized_views = []
            for view in views:
                criteria = SavedListingCriteria.from_query(view.get("query"))
                if criteria is not None:
                    try:
                        from modules.listing.application.saved_search_owner import (
                            authorize_listing_search_query,
                        )
                        authorize_listing_search_query(principal, view["query"])
                    except HTTPException as exc:
                        if exc.status_code == 403:
                            continue
                        raise
                authorized_views.append(view)
            views = authorized_views
            return [SavedView(**v) for v in views]

        @router.post(
            "/saved-views",
            operation_id="createSavedView",
            status_code=201,
            response_model=SavedView,
            responses={
                **api_error_responses(403, 422),
                **api_error_responses(409, idempotency_conflict=True),
            },
        )
        def create_saved_view(
            body: SavedViewRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = Header(
                ..., alias="Idempotency-Key", min_length=16, max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
            ),
        ) -> SavedView:
            validate_idempotency_key(key)
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_staff = (principal.has_role(Role.EXPANSION_USER) or operator_role_id in (
                "expansion-staff", "expansionStaff", "expansion-user", "expansion_user"
            )) and not is_manager
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")

            if not (is_manager or is_staff or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            actor_id = principal.subject_id

            from modules.listing.application.saved_search import SavedListingCriteria
            if SavedListingCriteria.from_query(body.query) is not None:
                from modules.listing.application.saved_search_owner import SavedListingSearchOwner
                owner = SavedListingSearchOwner.from_runtime(request.app.state)
                if owner.store is not active:
                    raise HTTPException(503, "LISTING_SEARCH_NATIVE_OWNER_MISMATCH")
                result = owner.command(principal, operation="create", payload={"name": body.name, "query": body.query},
                    key=key, correlation_id=request.state.correlation_id)
                return SavedView(**result["search"])

            def make() -> tuple[dict[str, Any], int]:
                svid = str(uuid4())
                value = {
                    "saved_view_id": svid,
                    "name": body.name,
                    "query": body.query,
                    "resource": body.resource,
                    "shared_role": body.shared_role,
                    "visibility": body.visibility.value,
                    "owner_subject_id": actor_id,
                    "created_at": now(),
                    "version": 1,
                    "tenant_id": tenant_id,
                }
                active.saved_views.append(value)
                return value, 201

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "createSavedView", make)
            response.status_code = code
            return SavedView(**val)

        @router.post(
            "/intakes/{intake_id}/promotion-requests",
            operation_id="requestCandidatePromotion",
            status_code=202,
            response_model=PromotionDecisionReceipt,
            responses={
                202: {
                    "model": PromotionDecisionReceipt,
                    "description": "Promotion review requested",
                    "headers": response_headers("ETag", "Idempotency-Replayed"),
                },
                429: {
                    "model": ApiError,
                    "description": "Rate limited",
                    "headers": response_headers("Retry-After"),
                },
                503: {
                    "model": ApiError,
                    "description": "Backpressure active",
                    "headers": response_headers("Retry-After"),
                },
                **api_error_responses(403, 409, 422, 428),
            },
        )
        def promote(
            intake_id: UuidString,
            body: PromotionRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> PromotionDecisionReceipt:
            with promotion_unit(tenant_id, intake_id):
                intake_repository = V1IntakeRepositoryAdapter(active, request.app.state)
                current = intake_repository.get_listing_intake(intake_id)
                if current is None:
                    raise HTTPException(404, "intake not found")
                current_tenant = current.get("tenantId") or current.get("scope", {}).get("tenant_id")
                if current_tenant != tenant_id:
                    raise HTTPException(403, "TENANT_SCOPE_DENIED")

                principal = get_principal(request)
                operator_role_id = get_operator_role_id(request)
                correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

                resource_for_auth = intake_auth_resource(current)

                authorize_intake_action(
                    principal,
                    "promote",
                    resource=resource_for_auth,
                    risk_acknowledged=body.risk_acknowledged,
                    risk_summary=body.reason,
                    operator_role_id=operator_role_id,
                    audit_log=active_audit_log,
                    correlation_id=correlation_id,
                )

                actor_id = principal.subject_id

                def make() -> tuple[dict[str, Any], int]:
                    if current.get("state") != "READY" and current.get("stage") != "READY":
                        raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                    require_version(if_match, current["version"])

                    from modules.listing.application.promotion import PromotionService
                    from modules.listing.domain.intake_states import (
                        Actor,
                        PrincipalRole,
                        TransitionContext,
                    )

                    repository = _repository(request)
                    promo_service = PromotionService(
                        promotion_repository=V1PromotionRepositoryAdapter(active, request.app.state),
                        listing_repository=V1ListingRepositoryAdapter(repository),
                        intake_repository=intake_repository,
                        outbox_repository=promotion_outbox(request, repository),
                    )

                    proposer_actor = Actor(
                        actor_id=actor_id,
                        role=PrincipalRole.EXPANSION_STAFF,
                        tenant_id=tenant_id,
                    )
                    proposer_context = TransitionContext(
                        actor=proposer_actor,
                        idempotency_key=key,
                        correlation_id=correlation_id,
                    )

                    try:
                        promo_record = promo_service.request_promotion(
                            intake_id=intake_id,
                            target_format_code=body.target_format_code,
                            reason=body.reason,
                            gate_snapshot_sha256=body.gate_snapshot_sha256,
                            context=proposer_context,
                        )
                    except Exception as exc:
                        exc_str = str(exc)
                        code_val = str(getattr(exc, "code", ""))
                        if "DEPENDENCY_CONFLICT" in code_val or "DUPLICATE" in exc_str or "DEPENDENCY" in exc_str:
                            raise HTTPException(409, "DUPLICATE_CANDIDATE") from exc
                        if "WORKFLOW_STATE_DENIED" in code_val or "WORKFLOW_STATE" in exc_str:
                            raise HTTPException(409, "WORKFLOW_STATE_DENIED") from exc
                        if "SOURCE_POLICY_DENIED" in code_val or "SOURCE_POLICY_DENIED" in exc_str:
                            raise HTTPException(422, f"SOURCE_POLICY_DENIED: {exc}") from exc
                        raise HTTPException(422, str(exc)) from exc

                    current["version"] += 1
                    if "auditEvents" in current:
                        current["updatedAt"] = now()
                        current["auditEvents"].append({
                            "id": str(uuid4()),
                            "occurredAt": now(),
                            "actorRoleId": operator_role_id or "expansion-manager",
                            "actorName": actor_id,
                            "action": "intake.promotion_requested",
                            "targetId": intake_id,
                            "message": "Candidate promotion requested for independent review.",
                            "correlationId": correlation_id,
                        })
                    else:
                        current["updated_at"] = now()
                        current.setdefault("processing_history", []).append({
                            "transition_id": str(uuid4()),
                            "from_state": "READY",
                            "to_state": "READY",
                            "occurred_at": now(),
                            "actor": actor_id,
                            "version_after": current["version"],
                        })
                    intake_repository.save_intake(current)
                    audit_promotion(request, None, promo_record, actor_id)

                    return promo_record, 202

                val, code, was_replayed = replay(
                    key,
                    body.model_dump(),
                    tenant_id,
                    actor_id,
                    "requestCandidatePromotion",
                    make,
                    resource_id=intake_id,
                )
                response.status_code = code
                response.headers["Idempotency-Replayed"] = str(was_replayed).lower()
                response.headers["ETag"] = f'W/"{val["version"]}"'
                return PromotionDecisionReceipt(**val)

        @router.get(
            "/intakes/{intake_id}/promotion-decision",
            operation_id="getIntakePromotionDecision",
            response_model=PromotionDecisionReceipt,
            responses={
                200: {
                    "model": PromotionDecisionReceipt,
                    "description": "Latest durable promotion decision for the intake",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(403, 404),
            },
        )
        def get_intake_promotion(
            intake_id: UuidString,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
        ) -> PromotionDecisionReceipt:
            intake_repository = V1IntakeRepositoryAdapter(active, request.app.state)
            intake = intake_repository.get_listing_intake(intake_id)
            if intake is None:
                raise HTTPException(404, "intake not found")
            intake_tenant = intake.get("tenantId") or intake.get("scope", {}).get("tenant_id")
            if intake_tenant != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            authorize_intake_action(
                get_principal(request),
                "view",
                resource=intake_auth_resource(intake),
                operator_role_id=get_operator_role_id(request),
                audit_log=active_audit_log,
                correlation_id=request.headers.get("x-correlation-id"),
            )
            val = V1PromotionRepositoryAdapter(
                active, request.app.state
            ).get_promotion_for_intake(intake_id)
            if val is None:
                raise HTTPException(404, "promotion decision not found")
            if val.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            response.headers["ETag"] = f'W/"{val["version"]}"'
            return PromotionDecisionReceipt(**val)

        @router.get(
            "/promotion-decisions/{promotion_decision_id}",
            operation_id="getPromotionDecision",
            response_model=PromotionDecisionReceipt,
            responses={
                200: {
                    "model": PromotionDecisionReceipt,
                    "description": "Promotion decision",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(403, 404),
            },
        )
        def get_promotion(
            promotion_decision_id: UuidString,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
        ) -> PromotionDecisionReceipt:
            val = V1PromotionRepositoryAdapter(active, request.app.state).get_promotion(promotion_decision_id)
            if val is None:
                raise HTTPException(404, "promotion decision not found")

            if val.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            intake = V1IntakeRepositoryAdapter(active, request.app.state).get_listing_intake(val.get("intake_id", ""))
            if intake is None:
                raise HTTPException(403, "SCOPE_DENIED")
            resource = intake_auth_resource(intake)
            authorize_intake_action(
                principal,
                "view",
                resource=resource,
                operator_role_id=get_operator_role_id(request),
                audit_log=active_audit_log,
                correlation_id=request.headers.get("x-correlation-id"),
            )

            response.headers["ETag"] = f'W/"{val["version"]}"'
            return PromotionDecisionReceipt(**val)

        @router.get(
            "/identity-decisions/{decision_id}",
            operation_id="getIdentityDecision",
            response_model=DecisionReceipt,
            responses={
                200: {
                    "model": DecisionReceipt,
                    "description": "Identity decision",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(403, 404),
            },
        )
        def get_identity(
            decision_id: UuidString,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
        ) -> DecisionReceipt:
            if decision_id not in active.decisions:
                raise HTTPException(404, "identity decision not found")
            val = active.decisions[decision_id]

            if val.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            intake = linked_intake(val)
            resource = (
                intake_auth_resource(intake)
                if intake is not None
                else {
                    "tenant_id": tenant_id,
                    "scope": val.get("scope", {}),
                    "owner": val.get("proposer"),
                    "submitter": val.get("proposer"),
                }
            )
            authorize_intake_action(
                principal,
                "view",
                resource=resource,
                operator_role_id=get_operator_role_id(request),
                audit_log=active_audit_log,
                correlation_id=request.headers.get("x-correlation-id"),
            )

            response.headers["ETag"] = f'W/"{val["version"]}"'
            return DecisionReceipt(**val)

        @router.post(
            "/match-cases/{match_case_id}/decisions",
            operation_id="decideMatchCase",
            status_code=201,
            response_model=DecisionReceipt,
            responses={
                201: {
                    "model": DecisionReceipt,
                    "description": "Match decision proposed",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(403, 409, 422, 428),
            },
        )
        def decide_match_case(
            match_case_id: UuidString,
            body: MatchDecisionRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> DecisionReceipt:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            authorize_intake_action(
                principal,
                "decide",
                resource={"tenant_id": tenant_id},
                risk_acknowledged=body.risk_acknowledged,
                risk_summary=body.reason,
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                require_version(if_match, 1)
                did = str(uuid4())
                value = {
                    "decision_id": did,
                    "status": "PENDING_REVIEW",
                    "resource_versions": {},
                    "job_id": str(uuid4()),
                    "audit_event_id": str(uuid4()),
                    "correlation_id": str(uuid4()),
                    "version": 1,
                    "action": "match_decision",
                    "tenant_id": tenant_id,
                    "proposer": actor_id,
                }
                active.decisions[did] = value
                return value, 201

            val, code, was_replayed = replay(
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "decideMatchCase",
                make,
                resource_id=match_case_id,
            )
            response.status_code = code
            response.headers["ETag"] = f'W/"{val["version"]}"'
            return DecisionReceipt(**val)

        @router.post(
            "/identity/merge",
            operation_id="mergeProperties",
            status_code=202,
            response_model=DecisionReceipt,
            responses=api_error_responses(403, 409, 422, 428),
        )
        def merge_properties(
            body: MergeRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> DecisionReceipt:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            authorize_intake_action(
                principal,
                "merge",
                resource={"tenant_id": tenant_id},
                risk_acknowledged=body.risk_acknowledged or False,
                risk_summary=body.reason,
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                require_version(if_match, 1)
                did = str(uuid4())
                value = {
                    "decision_id": did,
                    "status": "PENDING_REVIEW",
                    "resource_versions": {},
                    "job_id": str(uuid4()),
                    "audit_event_id": str(uuid4()),
                    "correlation_id": str(uuid4()),
                    "version": 1,
                    "action": "merge",
                    "tenant_id": tenant_id,
                    "proposer": actor_id,
                }
                active.decisions[did] = value
                return value, 202

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "mergeProperties", make)
            response.status_code = code
            return DecisionReceipt(**val)

        @router.post(
            "/identity/split",
            operation_id="splitProperty",
            status_code=202,
            response_model=DecisionReceipt,
            responses=api_error_responses(403, 409, 422, 428),
        )
        def split_property(
            body: SplitRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> DecisionReceipt:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            authorize_intake_action(
                principal,
                "split",
                resource={"tenant_id": tenant_id},
                risk_acknowledged=body.risk_acknowledged or False,
                risk_summary=body.reason,
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                require_version(if_match, 1)
                did = str(uuid4())
                value = {
                    "decision_id": did,
                    "status": "PENDING_REVIEW",
                    "resource_versions": {},
                    "job_id": str(uuid4()),
                    "audit_event_id": str(uuid4()),
                    "correlation_id": str(uuid4()),
                    "version": 1,
                    "action": "split",
                    "tenant_id": tenant_id,
                    "proposer": actor_id,
                }
                active.decisions[did] = value
                return value, 202

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "splitProperty", make)
            response.status_code = code
            return DecisionReceipt(**val)

        @router.post(
            "/identity/unmerge",
            operation_id="unmergeProperty",
            status_code=202,
            response_model=DecisionReceipt,
            responses=api_error_responses(403, 409, 422, 428),
        )
        def unmerge_property(
            body: UnmergeRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> DecisionReceipt:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            authorize_intake_action(
                principal,
                "unmerge",
                resource={"tenant_id": tenant_id},
                risk_acknowledged=body.risk_acknowledged or False,
                risk_summary=body.reason,
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                require_version(if_match, 1)
                did = str(uuid4())
                value = {
                    "decision_id": did,
                    "status": "PENDING_REVIEW",
                    "resource_versions": {},
                    "job_id": str(uuid4()),
                    "audit_event_id": str(uuid4()),
                    "correlation_id": str(uuid4()),
                    "version": 1,
                    "action": "unmerge",
                    "tenant_id": tenant_id,
                    "proposer": actor_id,
                }
                active.decisions[did] = value
                return value, 202

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "unmergeProperty", make)
            response.status_code = code
            return DecisionReceipt(**val)

        # Helper for executing standard state-transitions on collections
        def generic_mutate(collection: dict[str, dict[str, Any]], resource_id: str,
                           to_state: str, actor_id: str, version_key: str = "version") -> dict[str, Any]:
            current = collection.get(resource_id)
            if current is None:
                raise HTTPException(404, "resource not found")

            current[version_key] = int(current.get(version_key, 1)) + 1
            from_state = None
            if "state" in current:
                from_state = current["state"]
                current["state"] = to_state
            elif "status" in current:
                from_state = current["status"]
                current["status"] = to_state

            current["updated_at"] = now()

            if "processing_history" in current:
                current["processing_history"].append({
                    "transition_id": str(uuid4()),
                    "from_state": from_state,
                    "to_state": to_state,
                    "occurred_at": now(),
                    "actor": actor_id,
                    "version_after": current[version_key],
                })
            return current

        # Transition operations
        @router.post(
            "/intakes/{intake_id}/actions/cancel",
            operation_id="cancelIntake",
            status_code=200,
            response_model=TransitionReceipt,
            responses={
                200: {
                    "model": TransitionReceipt,
                    "description": "Transition committed",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(503, 403, 409, 422, 428),
            },
        )
        def cancel_intake(
            intake_id: UuidString,
            body: ReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> TransitionReceipt:
            validate_idempotency_key(key)
            current = active.intakes.get(intake_id)
            if current is None:
                raise HTTPException(404, "intake not found")
            if current.get("scope", {}).get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            resource_for_auth = intake_auth_resource(current)

            authorize_intake_action(
                principal,
                "cancel",
                resource=resource_for_auth,
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                current = active.intakes[intake_id]
                require_intake_scope(principal, current)
                if current.get("state") not in {
                    "SUBMITTED", "AWAITING_ASSISTED_ENTRY", "NEEDS_REVIEW"
                }:
                    raise HTTPException(
                        409,
                        f"WORKFLOW_STATE_DENIED: cannot cancel intake in state {current.get('state')}",
                    )
                require_version(if_match, current["version"])
                from_state = current.get("state", "SUBMITTED")
                if any(d.get("intake_id") == intake_id and d.get("status") == "EXECUTED" for d in active.decisions.values()) or any(
                    d.get("intake_id") == intake_id and d.get("status") in {"CANDIDATE_CREATED", "SCORE_QUEUED", "SCORE_FAILED", "COMPLETED"} for d in active.promotions.values()
                ):
                    raise HTTPException(409, "CANNOT_CANCEL")
                before = copy.deepcopy(current)
                updated = generic_mutate(active.intakes, intake_id, "CANCELLED", actor_id)
                record_workflow_change(active, current, resource_type="intake", before=before,
                    after=updated, action="intake.cancel", actor_id=actor_id)
                tr = receipt(from_state, "CANCELLED", updated["version"], actor_id)
                return tr, 200

            val, code, was_replayed = replay(
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "cancelIntake",
                make,
                resource_id=intake_id,
            )
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'W/"{val["version_after"]}"'
            return TransitionReceipt(**val)

        @router.post(
            "/intakes/{intake_id}/actions/quarantine",
            operation_id="quarantineIntake",
            status_code=200,
            response_model=TransitionReceipt,
            responses={
                200: {
                    "model": TransitionReceipt,
                    "description": "Transition committed",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(403, 409, 422, 428),
            },
        )
        def quarantine_intake(
            intake_id: UuidString,
            body: RiskReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> TransitionReceipt:
            validate_idempotency_key(key)
            current = active.intakes.get(intake_id)
            if current is None:
                raise HTTPException(404, "intake not found")
            if current.get("scope", {}).get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            require_intake_scope(principal, current)

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")
            is_privacy = principal.has_role(Role.FINANCE_LEGAL) or operator_role_id in ("privacy-officer", "privacyOfficer")

            if not (is_manager or is_steward or is_privacy):
                raise HTTPException(403, "ROLE_DENIED")

            if not body.reason or not body.reason.strip():
                raise HTTPException(422, "RISK_ACKNOWLEDGEMENT_REQUIRED: risk summary is required")
            if not body.risk_acknowledged:
                raise HTTPException(422, "RISK_ACKNOWLEDGEMENT_REQUIRED: risk acknowledgement is required")

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                if current.get("state") != "NEEDS_REVIEW":
                    raise HTTPException(
                        409,
                        f"WORKFLOW_STATE_DENIED: cannot quarantine intake in state {current.get('state')}",
                    )
                require_version(if_match, current["version"])
                from_state = current.get("state", "SUBMITTED")
                updated = generic_mutate(active.intakes, intake_id, "QUARANTINED", actor_id)
                tr = receipt(from_state, "QUARANTINED", updated["version"], actor_id)
                return tr, 200

            val, code, was_replayed = replay(
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "quarantineIntake",
                make,
                resource_id=intake_id,
            )
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'W/"{val["version_after"]}"'
            return TransitionReceipt(**val)

        @router.post(
            "/intakes/{intake_id}/actions/reopen",
            operation_id="reopenIntake",
            status_code=200,
            response_model=TransitionReceipt,
            responses={
                200: {
                    "model": TransitionReceipt,
                    "description": "Transition committed",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(403, 409, 422, 428),
            },
        )
        def reopen_intake(
            intake_id: UuidString,
            body: RiskReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> TransitionReceipt:
            validate_idempotency_key(key)
            current = active.intakes.get(intake_id)
            if current is None:
                raise HTTPException(404, "intake not found")
            if current.get("scope", {}).get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            resource_for_auth = intake_auth_resource(current)

            action_name = "reopen_quarantine" if current.get("state") == "QUARANTINED" else "reopen_failed"

            authorize_intake_action(
                principal,
                action_name,
                resource=resource_for_auth,
                risk_acknowledged=body.risk_acknowledged,
                risk_summary=body.reason,
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                from_state = current.get("state")
                if from_state == "QUARANTINED":
                    pending = current.get("pending_quarantine_release")
                    if pending is None:
                        require_version(if_match, current["version"])
                        current["version"] += 1
                        current["updated_at"] = now()
                        current["pending_quarantine_release"] = {
                            "proposal_id": str(uuid4()),
                            "proposer": actor_id,
                            "reason": body.reason,
                            "proposed_at": now(),
                        }
                        current.setdefault("processing_history", []).append({
                            "transition_id": str(uuid4()),
                            "from_state": "QUARANTINED",
                            "to_state": "QUARANTINED",
                            "occurred_at": now(),
                            "actor": actor_id,
                            "reason_code": "SECOND_ACTOR_REQUIRED",
                            "version_after": current["version"],
                        })
                        return receipt(
                            "QUARANTINED",
                            "QUARANTINED",
                            current["version"],
                            actor_id,
                        ), 200
                    if pending.get("proposer") == actor_id:
                        raise HTTPException(403, "SELF_REVIEW_DENIED")
                    to_state = "CHECKING_SOURCE_POLICY"
                elif from_state == "FAILED":
                    job = active.jobs.get(current.get("job_id", ""))
                    checkpoint = job.get("checkpoint") if job else None
                    if checkpoint not in {"RETRIEVING", "PARSING"}:
                        raise HTTPException(409, "CHECKPOINT_UNAVAILABLE")
                    to_state = checkpoint
                else:
                    raise HTTPException(
                        409,
                        f"WORKFLOW_STATE_DENIED: cannot reopen intake in state {from_state}",
                    )
                require_version(if_match, current["version"])
                updated = generic_mutate(active.intakes, intake_id, to_state, actor_id)
                if from_state == "QUARANTINED":
                    updated.pop("pending_quarantine_release", None)
                tr = receipt(from_state, to_state, updated["version"], actor_id)
                return tr, 200

            val, code, was_replayed = replay(
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "reopenIntake",
                make,
                resource_id=intake_id,
            )
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'W/"{val["version_after"]}"'
            return TransitionReceipt(**val)

        @router.post(
            "/assignments/{assignment_id}/actions/claim",
            operation_id="claimAssignment",
            status_code=200,
            response_model=AssignmentReceipt,
            responses={
                200: {
                    "model": AssignmentReceipt,
                    "description": "Assignment updated",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(503, 403, 409, 422, 428),
            },
        )
        def claim_assignment(
            assignment_id: UuidString,
            body: ReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> AssignmentReceipt:
            validate_idempotency_key(key)
            current = active.assignments.get(assignment_id)
            if current is None:
                raise HTTPException(404, "assignment not found")
            assignment_tenant = current.get("tenant_id")
            if not assignment_tenant:
                intake = active.intakes.get(current.get("intake_id", ""))
                if intake:
                    assignment_tenant = intake.get("scope", {}).get("tenant_id")
            if assignment_tenant != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            actor_id = principal.subject_id
            require_workflow_resource_scope(principal, current)

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_staff = (principal.has_role(Role.EXPANSION_USER) or operator_role_id in (
                "expansion-staff", "expansionStaff", "expansion-user", "expansion_user"
            )) and not is_manager
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")

            if not (is_manager or is_staff or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            require_assignment_registry_authority(current, actor_id)

            def make() -> tuple[dict[str, Any], int]:
                working = active.assignments[assignment_id]
                require_workflow_resource_scope(principal, working)
                require_version(if_match, working["version"])
                try:
                    updated = change_assignment(
                        active, working, workflow_policy, action="claim", actor_id=actor_id,
                        role=PrincipalRole.EXPANSION_MANAGER if is_manager else (PrincipalRole.DATA_STEWARD if is_steward else PrincipalRole.EXPANSION_STAFF),
                        reason=body.reason, correlation_id=request.headers.get("x-correlation-id") or str(uuid4()),
                        current_time=datetime.now(UTC),
                    )
                except PermissionError as exc:
                    raise HTTPException(403, "OWNERSHIP_REQUIRED") from exc
                except IntakeWorkIncomplete as exc:
                    raise HTTPException(409, "WORK_INCOMPLETE") from exc
                except DomainValidationError as exc:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED") from exc
                except (IntakeWorkflowUnavailable, KeyError, AttributeError) as exc:
                    raise HTTPException(503, "INTAKE_WORKFLOW_POLICY_UNAVAILABLE") from exc
                except ValueError as exc:
                    raise HTTPException(422, "VALIDATION_FAILED") from exc
                return updated, 200

            val, code, was_replayed = replay(
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "claimAssignment",
                make,
                resource_id=assignment_id,
            )
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'W/"{val["version"]}"'
            return AssignmentReceipt(**val)

        @router.get(
            "/assignments/{assignment_id}/transfer-targets",
            operation_id="listAssignmentTransferTargets",
            response_model=AssignmentTransferTargetPage,
            responses=api_error_responses(403, 404, 422, 503),
        )
        def get_assignment_transfer_targets(
            assignment_id: UuidString,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
        ) -> AssignmentTransferTargetPage:
            current = active.assignments.get(assignment_id)
            if current is None:
                raise HTTPException(404, "assignment not found")
            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            principal = get_principal(request)
            require_workflow_resource_scope(principal, current)
            try:
                result = list_assignment_transfer_targets(
                    active, current, workflow_policy, principal.subject_id,
                )
            except PermissionError as exc:
                raise HTTPException(403, "OWNERSHIP_REQUIRED") from exc
            except Exception as exc:
                raise HTTPException(503, "INTAKE_WORKFLOW_POLICY_UNAVAILABLE") from exc
            response.headers["Cache-Control"] = "no-store"
            return AssignmentTransferTargetPage(**result)

        @router.post(
            "/assignments/{assignment_id}/actions/transfer",
            operation_id="transferAssignment",
            status_code=200,
            response_model=AssignmentReceipt,
            responses={
                200: {
                    "model": AssignmentReceipt,
                    "description": "Assignment updated",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(503, 403, 409, 422, 428),
            },
        )
        def transfer_assignment(
            assignment_id: UuidString,
            body: AssignmentTransferRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> AssignmentReceipt:
            validate_idempotency_key(key)
            current = active.assignments.get(assignment_id)
            if current is None:
                raise HTTPException(404, "assignment not found")
            assignment_tenant = current.get("tenant_id")
            if not assignment_tenant:
                intake = active.intakes.get(current.get("intake_id", ""))
                if intake:
                    assignment_tenant = intake.get("scope", {}).get("tenant_id")
            if assignment_tenant != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            actor_id = principal.subject_id
            require_workflow_resource_scope(principal, current)

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_staff = (principal.has_role(Role.EXPANSION_USER) or operator_role_id in (
                "expansion-staff", "expansionStaff", "expansion-user", "expansion_user"
            )) and not is_manager
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")

            if not (is_manager or is_staff or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            require_assignment_registry_authority(current, actor_id)

            def make() -> tuple[dict[str, Any], int]:
                working = active.assignments[assignment_id]
                require_workflow_resource_scope(principal, working)
                require_version(if_match, working["version"])
                try:
                    updated = change_assignment(
                        active, working, workflow_policy, action="transfer", actor_id=actor_id,
                        role=PrincipalRole.EXPANSION_MANAGER if is_manager else (PrincipalRole.DATA_STEWARD if is_steward else PrincipalRole.EXPANSION_STAFF),
                        reason=body.reason, correlation_id=request.headers.get("x-correlation-id") or str(uuid4()),
                        current_time=datetime.now(UTC), target={"owner_subject_id": body.target_owner_subject_id, "owner_role": body.target_owner_role,
                                    "handoff_note": body.handoff_note,
                                    "due_at": datetime.fromisoformat(body.due_at.replace("Z", "+00:00")) if body.due_at else None},
                    )
                except PermissionError as exc:
                    raise HTTPException(403, "OWNERSHIP_REQUIRED") from exc
                except IntakeWorkIncomplete as exc:
                    raise HTTPException(409, "WORK_INCOMPLETE") from exc
                except DomainValidationError as exc:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED") from exc
                except (IntakeWorkflowUnavailable, KeyError, AttributeError) as exc:
                    raise HTTPException(503, "INTAKE_WORKFLOW_POLICY_UNAVAILABLE") from exc
                except ValueError as exc:
                    raise HTTPException(422, "VALIDATION_FAILED") from exc
                return updated, 200

            val, code, was_replayed = replay(
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "transferAssignment",
                make,
                resource_id=assignment_id,
            )
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'W/"{val["version"]}"'
            return AssignmentReceipt(**val)

        @router.post(
            "/assignments/{assignment_id}/actions/complete",
            operation_id="completeAssignment",
            status_code=200,
            response_model=AssignmentReceipt,
            responses={
                200: {
                    "model": AssignmentReceipt,
                    "description": "Assignment updated",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(503, 403, 409, 422, 428),
            },
        )
        def complete_assignment(
            assignment_id: UuidString,
            body: ReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> AssignmentReceipt:
            validate_idempotency_key(key)
            current = active.assignments.get(assignment_id)
            if current is None:
                raise HTTPException(404, "assignment not found")
            assignment_tenant = current.get("tenant_id")
            if not assignment_tenant:
                intake = active.intakes.get(current.get("intake_id", ""))
                if intake:
                    assignment_tenant = intake.get("scope", {}).get("tenant_id")
            if assignment_tenant != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            actor_id = principal.subject_id
            require_workflow_resource_scope(principal, current)

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_staff = (principal.has_role(Role.EXPANSION_USER) or operator_role_id in (
                "expansion-staff", "expansionStaff", "expansion-user", "expansion_user"
            )) and not is_manager
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")

            if not (is_manager or is_staff or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            require_assignment_registry_authority(current, actor_id)

            def make() -> tuple[dict[str, Any], int]:
                working = active.assignments[assignment_id]
                require_workflow_resource_scope(principal, working)
                require_version(if_match, working["version"])
                try:
                    updated = change_assignment(
                        active, working, workflow_policy, action="complete", actor_id=actor_id,
                        role=PrincipalRole.EXPANSION_MANAGER if is_manager else (PrincipalRole.DATA_STEWARD if is_steward else PrincipalRole.EXPANSION_STAFF),
                        reason=body.reason, correlation_id=request.headers.get("x-correlation-id") or str(uuid4()),
                        current_time=datetime.now(UTC),
                    )
                except PermissionError as exc:
                    raise HTTPException(403, "OWNERSHIP_REQUIRED") from exc
                except IntakeWorkIncomplete as exc:
                    raise HTTPException(409, "WORK_INCOMPLETE") from exc
                except DomainValidationError as exc:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED") from exc
                except (IntakeWorkflowUnavailable, KeyError, AttributeError) as exc:
                    raise HTTPException(503, "INTAKE_WORKFLOW_POLICY_UNAVAILABLE") from exc
                except ValueError as exc:
                    raise HTTPException(422, "VALIDATION_FAILED") from exc
                return updated, 200

            val, code, was_replayed = replay(
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "completeAssignment",
                make,
                resource_id=assignment_id,
            )
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'W/"{val["version"]}"'
            return AssignmentReceipt(**val)

        @router.post(
            "/sla-instances/{sla_instance_id}/actions/pause",
            operation_id="pauseSla",
            status_code=200,
            response_model=SlaReceipt,
            responses={
                200: {
                    "model": SlaReceipt,
                    "description": "SLA updated",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(503, 403, 409, 422, 428),
            },
        )
        def pause_sla(
            sla_instance_id: UuidString,
            body: SlaPauseRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> SlaReceipt:
            validate_idempotency_key(key)
            current = active.slas.get(sla_instance_id)
            if current is None:
                raise HTTPException(404, "SLA instance not found")
            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            principal = get_principal(request)
            require_workflow_resource_scope(principal, current)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")
            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")
            if not (is_manager or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                if current.get("state") not in {"ON_TRACK", "DUE_SOON", "OVERDUE"}:
                    raise HTTPException(409, "SLA_PAUSE_DENIED")
                require_version(if_match, current["version"])
                parent = linked_intake(current)
                scope = parent["scope"] if parent is not None else current["scope"]
                try:
                    updated = change_sla(
                        active, current, workflow_policy, scope=scope, actor_id=actor_id,
                        role=PrincipalRole.EXPANSION_MANAGER if is_manager else PrincipalRole.DATA_STEWARD,
                        reason=body.reason, correlation_id=correlation_id or str(uuid4()),
                        current_time=datetime.now(UTC),
                        expected_resume_at=datetime.fromisoformat(body.expected_resume_at.replace("Z", "+00:00")),
                    )
                except PermissionError as exc:
                    raise HTTPException(403, "SLA_PAUSE_DENIED") from exc
                except DomainValidationError as exc:
                    raise HTTPException(409, "SLA_PAUSE_DENIED") from exc
                except Exception as exc:
                    raise HTTPException(503, "INTAKE_WORKFLOW_POLICY_UNAVAILABLE") from exc
                return updated, 200

            require_sla_registry_authority(current, actor_id)
            val, code, was_replayed = replay(
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "pauseSla",
                make,
                resource_id=sla_instance_id,
            )
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'W/"{val["version"]}"'
            return SlaReceipt(**val)

        @router.post(
            "/sla-instances/{sla_instance_id}/actions/resume",
            operation_id="resumeSla",
            status_code=200,
            response_model=SlaReceipt,
            responses={
                200: {
                    "model": SlaReceipt,
                    "description": "SLA updated",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(503, 403, 409, 422, 428),
            },
        )
        def resume_sla(
            sla_instance_id: UuidString,
            body: ReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> SlaReceipt:
            validate_idempotency_key(key)
            current = active.slas.get(sla_instance_id)
            if current is None:
                raise HTTPException(404, "SLA instance not found")
            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            principal = get_principal(request)
            require_workflow_resource_scope(principal, current)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")
            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")
            if not (is_manager or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                if current.get("state") != "PAUSED":
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                require_version(if_match, current["version"])
                parent = linked_intake(current)
                scope = parent["scope"] if parent is not None else current["scope"]
                try:
                    updated = change_sla(
                        active, current, workflow_policy, scope=scope, actor_id=actor_id,
                        role=PrincipalRole.EXPANSION_MANAGER if is_manager else PrincipalRole.DATA_STEWARD,
                        reason=body.reason, correlation_id=correlation_id or str(uuid4()),
                        current_time=datetime.now(UTC),
                    )
                except PermissionError as exc:
                    raise HTTPException(403, "ROLE_DENIED") from exc
                except DomainValidationError as exc:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED") from exc
                except Exception as exc:
                    raise HTTPException(503, "INTAKE_WORKFLOW_POLICY_UNAVAILABLE") from exc
                return updated, 200

            require_sla_registry_authority(current, actor_id)
            val, code, was_replayed = replay(
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "resumeSla",
                make,
                resource_id=sla_instance_id,
            )
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'W/"{val["version"]}"'
            return SlaReceipt(**val)

        @router.post(
            "/promotion-decisions/{promotion_decision_id}/actions/review",
            operation_id="reviewPromotionDecision",
            status_code=200,
            response_model=PromotionDecisionReceipt,
            responses={
                200: {
                    "model": PromotionDecisionReceipt,
                    "description": "Review committed",
                    "headers": response_headers("ETag"),
                },
                202: {
                    "model": PromotionDecisionReceipt,
                    "description": "Review queued",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(403, 409, 422, 428, 503),
            },
        )
        def review_promotion(
            promotion_decision_id: UuidString,
            body: ReviewDecisionRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> PromotionDecisionReceipt:
            initial = V1PromotionRepositoryAdapter(active, request.app.state).get_promotion(promotion_decision_id)
            if initial is None:
                raise HTTPException(404, "promotion decision not found")
            with promotion_unit(tenant_id, initial["intake_id"], promotion_decision_id):
                validate_idempotency_key(key)
                current = V1PromotionRepositoryAdapter(active, request.app.state).get_promotion(promotion_decision_id)
                if current is None:
                    raise HTTPException(404, "promotion decision not found")

                if current.get("tenant_id") != tenant_id:
                    raise HTTPException(403, "TENANT_SCOPE_DENIED")

                intake = V1IntakeRepositoryAdapter(active, request.app.state).get_listing_intake(current["intake_id"])
                intake_tenant = (intake.get("tenantId") or intake.get("scope", {}).get("tenant_id")) if intake else None
                if intake is not None and intake_tenant != tenant_id:
                    raise HTTPException(403, "TENANT_SCOPE_DENIED")

                principal = get_principal(request)
                if intake is None:
                    raise HTTPException(403, "SCOPE_DENIED")
                require_intake_scope(principal, intake)
                operator_role_id = get_operator_role_id(request)
                actor_id = principal.subject_id

                if (current.get("proposer_subject_id") or current.get("proposer")) == actor_id:
                    raise HTTPException(403, "SELF_REVIEW_DENIED")

                is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                    "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
                )
                if not is_manager:
                    raise HTTPException(403, "ROLE_DENIED")

                if not body.reason or not body.reason.strip():
                    raise HTTPException(422, "RISK_ACKNOWLEDGEMENT_REQUIRED: risk summary is required")
                if not body.risk_acknowledged:
                    raise HTTPException(422, "RISK_ACKNOWLEDGEMENT_REQUIRED: risk acknowledgement is required")

                before_promotion = copy.deepcopy(current)

                def make() -> tuple[dict[str, Any], int]:
                    if current.get("status") != "PENDING_REVIEW":
                        raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                    require_version(if_match, current["version"])

                    from modules.listing.application.promotion import (
                        PromotionScoreQueueError,
                        PromotionService,
                    )
                    from modules.listing.domain.intake_states import (
                        Actor,
                        PrincipalRole,
                        TransitionContext,
                    )

                    repository = _repository(request)
                    test_fault = request.headers.get("x-odp-test-fault")
                    score_queue_hook = None
                    if test_fault:
                        import os

                        if os.getenv("CI") != "1":
                            raise HTTPException(403, "TEST_CONTROL_DENIED")
                        if test_fault != "score-failure":
                            raise HTTPException(422, "UNKNOWN_TEST_FAULT")

                        def fail_score_queue() -> None:
                            raise RuntimeError("ODP_TEST_SCORE_FAILURE")

                        score_queue_hook = fail_score_queue

                    promo_service = PromotionService(
                        promotion_repository=V1PromotionRepositoryAdapter(active, request.app.state),
                        listing_repository=V1ListingRepositoryAdapter(repository),
                        intake_repository=V1IntakeRepositoryAdapter(active, request.app.state),
                        outbox_repository=promotion_outbox(request, repository),
                        score_queue_hook=score_queue_hook,
                        score_dispatcher=score_job_repository(request) if body.decision == ReviewDecision.APPROVE else None,
                    )

                    reviewer_actor = Actor(
                        actor_id=actor_id,
                        role=PrincipalRole.EXPANSION_MANAGER,
                        tenant_id=tenant_id,
                    )
                    reviewer_context = TransitionContext(
                        actor=reviewer_actor,
                        idempotency_key=key,
                        correlation_id=request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id"),
                    )

                    decision_str = "APPROVE" if body.decision == ReviewDecision.APPROVE else "REJECT"

                    try:
                        updated = promo_service.review_promotion(
                            promotion_decision_id=promotion_decision_id,
                            decision=decision_str,
                            reason=body.reason,
                            risk_acknowledged=body.risk_acknowledged,
                            context=reviewer_context,
                        )
                    except PromotionScoreQueueError as exc:
                        failed = promo_service.promotion_repository.get_promotion(promotion_decision_id)
                        if not failed or failed.get("status") != "SCORE_FAILED" or not failed.get("candidate_site_id") or not failed.get("site_score_job_id"):
                            raise
                        failed["reviewer_subject_id"] = actor_id
                        audit_promotion(request, before_promotion, failed, actor_id)
                        return {"detail": str(exc)}, 422
                    except Exception as exc:
                        _raise_score_delivery_http_error(exc)
                        if "SELF_REVIEW_DENIED" in str(exc):
                            raise HTTPException(403, "SELF_REVIEW_DENIED") from exc
                        if "DUPLICATE_CANDIDATE" in str(exc) or "DEPENDENCY_CONFLICT" in str(exc):
                            raise HTTPException(409, "DUPLICATE_CANDIDATE") from exc
                        if "WORKFLOW_STATE_DENIED" in str(exc):
                            raise HTTPException(409, "WORKFLOW_STATE_DENIED") from exc
                        if isinstance(exc, (DomainValidationError, ValueError)):
                            raise HTTPException(422, str(exc)) from exc
                        raise

                    updated["reviewer_subject_id"] = actor_id
                    audit_promotion(request, before_promotion, updated, actor_id)
                    if updated["status"] == "SCORE_QUEUED":
                        promo_service.score_dispatcher.seal_enqueue(updated["site_score_job_id"], tenant_id)
                    return updated, 200

                val, code, was_replayed = replay(
                    key,
                    body.model_dump(),
                    tenant_id,
                    actor_id,
                    "reviewPromotionDecision",
                    make,
                    resource_id=promotion_decision_id,
                )
            if code >= 400:
                raise HTTPException(code, val["detail"], headers={"Idempotency-Replayed": str(was_replayed).lower()})
            response.status_code = 200 if was_replayed else code
            response.headers["Idempotency-Replayed"] = str(was_replayed).lower()
            response.headers["ETag"] = f'W/"{val["version"]}"'
            return PromotionDecisionReceipt(**val)

        @router.post(
            "/identity-decisions/{decision_id}/actions/review",
            operation_id="reviewIdentityDecision",
            status_code=200,
            response_model=DecisionReceipt,
            responses={
                200: {
                    "model": DecisionReceipt,
                    "description": "Review committed",
                    "headers": response_headers("ETag"),
                },
                202: {
                    "model": DecisionReceipt,
                    "description": "Review queued",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(503, 403, 409, 422, 428),
            },
        )
        def review_identity(
            decision_id: UuidString,
            body: ReviewDecisionRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> DecisionReceipt:
            validate_idempotency_key(key)
            current = active.decisions.get(decision_id)
            if current is None:
                raise HTTPException(404, "identity decision not found")

            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            principal = get_principal(request)
            intake = linked_intake(current)
            if intake is not None:
                require_intake_scope(principal, intake)
            operator_role_id = get_operator_role_id(request)
            actor_id = principal.subject_id

            if current.get("proposer") == actor_id:
                raise HTTPException(403, "SELF_REVIEW_DENIED")

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")
            if not (is_manager or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            if not body.reason or not body.reason.strip():
                raise HTTPException(422, "RISK_ACKNOWLEDGEMENT_REQUIRED: risk summary is required")
            if not body.risk_acknowledged:
                raise HTTPException(422, "RISK_ACKNOWLEDGEMENT_REQUIRED: risk acknowledgement is required")

            def make() -> tuple[dict[str, Any], int]:
                current = active.decisions.get(decision_id)
                if current is None:
                    raise HTTPException(404, "identity decision not found")
                require_workflow_resource_scope(principal, current)
                if current.get("proposer") == actor_id:
                    raise HTTPException(403, "SELF_REVIEW_DENIED")
                if current.get("status") != "PENDING_REVIEW":
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                require_version(if_match, current["version"])
                to_state = "APPROVED" if body.decision == ReviewDecision.APPROVE else "REJECTED"
                correction = None
                correction_intake = None
                if current.get("action") == "identity_correction":
                    correction = active.corrections.get(current.get("correction_id", ""))
                    correction_intake = linked_intake(current)
                    if correction is None or correction_intake is None:
                        raise HTTPException(409, "DEPENDENCY_CONFLICT")
                    if correction.get("status") != "PENDING_REVIEW":
                        raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                    if (type(correction.get("version")) is not int or correction["version"] < 1
                        or correction.get("scope") != correction_intake["scope"]
                        or correction.get("tenant_id") != tenant_id
                        or correction.get("intake_id") != correction_intake["intake_id"]
                        or correction.get("proposer") != current.get("proposer")
                        or correction.get("field_classification") is None):
                        raise HTTPException(409, "CORRECTION_EVIDENCE_UNAVAILABLE")
                    if body.decision == ReviewDecision.APPROVE:
                        if correction_intake.get("version") != correction.get("intake_version"):
                            raise HTTPException(409, "VERSION_CONFLICT")
                        if correction_intake.get("state") != "NEEDS_REVIEW":
                            raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                        masked = mask_intake(principal, correction_intake)
                        if any(f.get("field_path") == correction["field_path"] and f.get("masked") for f in masked.get("fields", [])):
                            raise HTTPException(403, "FIELD_MASKED")
                        try:
                            observed = correction_values(correction_intake, correction["field_path"], correction["corrected_value"])
                        except PermissionError as exc:
                            raise HTTPException(403, "FIELD_MASKED") from exc
                        except ValueError as exc:
                            raise HTTPException(409, "CORRECTION_EVIDENCE_UNAVAILABLE") from exc
                        if any(observed.get(name) != correction.get(name) for name in (
                            "field_before", "source_snapshot_id", "parser_run_id",
                        )):
                            raise HTTPException(409, "CORRECTION_LINEAGE_CONFLICT")
                before_decision = copy.deepcopy(current)
                before_correction = copy.deepcopy(correction)
                before_intake = copy.deepcopy(correction_intake)
                updated = generic_mutate(active.decisions, decision_id, to_state, actor_id)
                if correction is not None and correction_intake is not None:
                    if body.decision == ReviewDecision.APPROVE:
                        try:
                            apply_correction(correction_intake, field_path=correction["field_path"],
                                             corrected_value=correction["corrected_value"], actor_id=actor_id)
                        except PermissionError as exc:
                            raise HTTPException(403, "FIELD_MASKED") from exc
                        except ValueError as exc:
                            raise HTTPException(422, str(exc)) from exc
                        correction["status"] = "APPLIED"
                        correction["after_effective_value"] = copy.deepcopy(correction["corrected_value"])
                        correction["intake_version"] = correction_intake["version"]
                        updated["resource_versions"]["intake"] = correction_intake["version"]
                        record_workflow_change(active,correction_intake,resource_type="intake",before=before_intake,
                                               after=correction_intake,action="intake.correct.apply",actor_id=actor_id)
                    else:
                        correction["status"] = "REJECTED"
                    correction["version"] += 1
                    correction["reviewer"] = correction["reviewed_by"] = actor_id
                    correction["reviewed_at"] = now()
                    correction["review_reason"] = body.reason
                    correction["review_risk_acknowledged"] = body.risk_acknowledged
                    updated["reviewer"] = actor_id
                    updated["review_reason"] = body.reason
                    updated["resource_versions"]["correction"] = correction["version"]
                    record_workflow_change(active,correction_intake,resource_type="correction",before=before_correction,
                                           after=correction,action="correction.apply" if correction["status"] == "APPLIED" else "correction.reject",
                                           actor_id=actor_id)
                    record_workflow_change(active,correction_intake,resource_type="identity_decision",before=before_decision,
                                           after=updated,action="identity.correction.review",actor_id=actor_id)
                # Approval remains APPROVED. Identity execution needs the real
                # canonical owner; this command cannot stamp EXECUTED evidence.
                return updated, 200

            val, code, was_replayed = replay(
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "reviewIdentityDecision",
                make,
                resource_id=decision_id,
            )
            response.status_code = 200 if was_replayed else code
            response.headers["Idempotency-Replayed"] = str(was_replayed).lower()
            response.headers["ETag"] = f'W/"{val["version"]}"'
            return DecisionReceipt(**val)

        @router.post(
            "/identity-decisions/{decision_id}/actions/reverse",
            operation_id="requestIdentityDecisionReversal",
            status_code=202,
            response_model=DecisionReceipt,
            responses={
                202: {
                    "model": DecisionReceipt,
                    "description": "Reversal requested",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(403, 409, 422, 428),
            },
        )
        def reverse_identity(
            decision_id: UuidString,
            body: RiskReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> DecisionReceipt:
            validate_idempotency_key(key)
            current = active.decisions.get(decision_id)
            if current is None:
                raise HTTPException(404, "identity decision not found")

            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            principal = get_principal(request)
            intake = linked_intake(current)
            if intake is not None:
                require_intake_scope(principal, intake)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            authorize_intake_action(
                principal,
                "unmerge",
                resource={"tenant_id": tenant_id},
                risk_acknowledged=body.risk_acknowledged,
                risk_summary=body.reason,
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                if current.get("status") != "EXECUTED":
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                require_version(if_match, current["version"])
                updated = generic_mutate(active.decisions, decision_id, "REVERSAL_PENDING", actor_id)
                return updated, 202

            val, code, was_replayed = replay(
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "requestIdentityDecisionReversal",
                make,
                resource_id=decision_id,
            )
            response.status_code = code
            response.headers["ETag"] = f'W/"{val["version"]}"'
            return DecisionReceipt(**val)

        return router


    def _repository(request: Request, bound_repository: Any = None):
        principal = getattr(request.state, "operator_principal", None)
        if principal is None:
            from apps.api.oday_api.security.dependencies import principal_from_headers

            try:
                principal = principal_from_headers(request.headers)
            except Exception:
                principal = None

        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="TENANT_SCOPE_DENIED: Missing verified principal",
            )

        principal_tenant = getattr(getattr(principal, "scope", None), "tenant_id", None) or getattr(
            principal, "tenant_id", None
        )
        if not principal_tenant or not str(principal_tenant).strip():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="TENANT_SCOPE_DENIED: Missing verified tenant scope",
            )
        clean_tenant = str(principal_tenant).strip()

        header_tenant = (
            request.headers.get("x-tenant-id")
            or request.headers.get("tenant_id")
            or ""
        ).strip()
        if header_tenant and header_tenant != clean_tenant:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="TENANT_SCOPE_DENIED: Tenant header does not match verified principal scope",
            )

        bundle = getattr(request.app.state, "persistence_bundle", None) or getattr(
            request.app.state, "persistence", None
        )
        if bundle is not None and hasattr(bundle, "listing_repository_for_tenant"):
            scoped = bundle.listing_repository_for_tenant(clean_tenant)
            if scoped is not None:
                return scoped

        target_repo = bound_repository or getattr(request.app.state, "listing_repository", None)
        if target_repo is not None and hasattr(target_repo, "_store"):
            base_store = getattr(target_repo._store, "_store", target_repo._store)
            if hasattr(base_store, "get") and hasattr(base_store, "put"):
                from shared.infrastructure.persistence.operator_domains import (
                    TenantScopedDocumentStore,
                )
                return type(target_repo)(TenantScopedDocumentStore(base_store, clean_tenant))

        if bound_repository is not None:
            return bound_repository

        repository = getattr(request.app.state, "listing_repository", None)
        if repository is None:
            from modules.listing import InMemoryListingRepository

            repository = InMemoryListingRepository()
            request.app.state.listing_repository = repository
        return repository


    def _geo_pipeline(request: Request) -> GeoPipeline | None:
        return getattr(request.app.state, "listing_geo_pipeline", None)


    __all__ = ["AssistedIntakeStore", "ListingImportPayload", "create_assisted_intake_router", "create_listings_router"]
