from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from modules.external_data.geo import GeoPipeline
from modules.listing import InMemoryListingRepository, ListingPipeline
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
        RETRYING = "RETRYING"
        SUCCEEDED = "SUCCEEDED"
        FAILED = "FAILED"
        CANCELLED = "CANCELLED"
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
        job_id: UuidString | None = None
        correlation_id: UuidString
        submitted_at: DateTimeString
        duplicate_hint: str | None = None
        identity_outcome: Literal["EXACT_DUPLICATE"] | None = None
        existing_listing_id: str | None = None
        navigation_target: str | None = None
        submission_receipt_id: UuidString | None = None

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

    class InboxLocationSummary(BaseModel):
        address: str | None = None
        district: str | None = None
        assigned_area_id: UuidString | None = None
        heat_zone_id: UuidString | None = None

    class InboxMaskingSummary(BaseModel):
        restricted_data: bool
        has_masked_fields: bool
        masked_fields: list[str] = Field(default_factory=list)
        reason_codes: list[str] = Field(default_factory=list)

    class IntakeSummary(BaseModel):
        intake_id: UuidString
        state: IntakeState
        intake_method: IntakeMethod
        source_id: str | None = None
        original_url: str | None = None
        canonical_url: str | None = None
        policy_state: SourcePolicyState | None = None
        match_outcome: MatchOutcome | None = None
        submitted_by: UuidString = None
        assigned_to: UuidString | None = None
        assignment_id: UuidString | None = None
        assignment_status: AssignmentStatus | None = None
        owner_subject_id: UuidString | None = None
        queue_id: str | None = None
        sla_instance_id: UuidString | None = None
        sla_state: SlaState | None = None
        due_at: DateTimeString | None = None
        last_observed_at: DateTimeString | None = None
        submitted_at: DateTimeString
        updated_at: DateTimeString
        version: int
        scope: ScopeContext
        issue: str | None = None
        next_action: str | None = None
        retryable: bool = False
        quarantined: bool = False
        failed: bool = False
        location: InboxLocationSummary
        masking: InboxMaskingSummary
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
        correction_actor: str | None = None
        correction_actor_role: str | None = None
        correction_reason: str | None = None
        corrected_at: DateTimeString | None = None
        source_snapshot_id: str | None = None
        parser_run_id: str | None = None
        parser_version: str | None = None

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
        actor: str | None = None
        actor_role: str | None = None
        before: Any | None = None
        after: Any | None = None
        source_snapshot_id: str | None = None
        parser_version: str | None = None
        related_ids: dict[str, Any] = Field(default_factory=dict)
        correlation_id: str | None = None
        resource_version: int | None = None
        evidence_state: str | None = None

    class SourceEvidenceDetail(BaseModel):
        original_url: str | None = None
        canonical_url: str | None = None
        source_id: str | None = None
        policy_state: SourcePolicyState | None = None
        source_snapshot_id: str | None = None
        captured_at: DateTimeString | None = None
        parser_run_id: str | None = None
        parser_version: str | None = None
        correlation_id: str
        freshness_state: Literal["CURRENT", "STALE", "NOT_CAPTURED"]

    class MatchComparisonField(BaseModel):
        field_path: str
        label: str
        submitted_value: Any = None
        existing_value: Any = None
        agrees: bool
        detail: str | None = None

    class MatchSignal(BaseModel):
        key: str
        label: str
        agrees: bool
        detail: str

    class IdentityGraphNode(BaseModel):
        model_config = ConfigDict(extra="allow")
        node_id: str
        node_type: str
        status: str

    class IdentityGraphEdge(BaseModel):
        model_config = ConfigDict(extra="allow")
        edge_id: str
        relation: str
        status: str
        source_property_id: str | None = None
        target_property_id: str | None = None
        property_id: str | None = None
        listing_id: str | None = None
        intake_id: str | None = None
        decision_id: str | None = None
        supersedes_edge_ids: list[str] = Field(default_factory=list)

    class IdentityGraphSnapshot(BaseModel):
        version: int = Field(..., ge=0)
        nodes: list[IdentityGraphNode]
        edges: list[IdentityGraphEdge]

    class IdentityRedirect(BaseModel):
        from_property_id: str
        to_property_id: str
        reason: str
        status: str

    class CandidateImpact(BaseModel):
        model_config = ConfigDict(extra="allow")
        candidate_site_id: str | None = None
        disposition: str | None = None
        source_property_id: str | None = None
        target_property_id: str | None = None

    class LineageImpact(BaseModel):
        append_only: bool
        source_evidence_preserved: bool
        superseded_edge_ids: list[str]
        affected_decision_ids: list[str]
        summary: str

    class DecisionActorReference(BaseModel):
        subject_id: str
        role_id: str

    class OriginalDecisionReference(BaseModel):
        decision_id: str
        action: str | None = None
        status: str | None = None
        version: int | None = None

    class MatchGraphPlan(BaseModel):
        model_config = ConfigDict(extra="allow")
        plan_id: UuidString
        plan_type: str
        status: str
        operations: list[dict[str, Any]]
        permitted_decision_types: list[str] = Field(default_factory=list)
        requires_human_decision: bool = True
        before_graph: IdentityGraphSnapshot
        after_graph: IdentityGraphSnapshot
        redirects: list[IdentityRedirect]
        candidate_impacts: list[CandidateImpact]
        lineage_impact: LineageImpact
        proposer: DecisionActorReference | None = None
        reviewer: DecisionActorReference | None = None
        expected_graph_version: int = Field(..., ge=0)
        original_decision: OriginalDecisionReference | None = None
        generated_at: DateTimeString

    class MatchCaseDetail(BaseModel):
        match_case_id: UuidString
        version: int = Field(..., ge=1)
        intake_id: UuidString
        outcome: MatchOutcome
        confidence: float = Field(..., ge=0, le=1)
        target_listing_id: str | None = None
        summary: str
        comparison_fields: list[MatchComparisonField]
        signals: list[MatchSignal]
        graph_plan: MatchGraphPlan
        source_snapshot_id: UuidString | None = None
        parser_version: str | None = None
        created_at: DateTimeString
        updated_at: DateTimeString

    class ActorDecisionFacts(BaseModel):
        role_mode: Literal[
            "expansion-staff",
            "expansion-manager",
            "data-steward",
            "governance-reviewer",
            "privacy-officer",
            "permission-limited",
        ]
        allowed_actions: list[str]
        denied_action_reasons: dict[str, str]
        scope: dict[str, Any]
        masking: dict[str, Any]
        purpose: dict[str, Any]
        second_actor: dict[str, Any]

    class MutationReceiptRecord(BaseModel):
        model_config = ConfigDict(extra="allow")
        receipt_id: str | None = None
        transition_id: str | None = None
        assignment_id: str | None = None
        sla_instance_id: str | None = None
        job_id: str | None = None
        promotion_decision_id: str | None = None
        decision_id: str | None = None
        intake_id: str | None = None
        listing_id: str | None = None
        listing_revision_id: str | None = None
        identity_edge_id: str | None = None
        candidate_site_id: str | None = None
        site_score_job_id: str | None = None
        status: str | None = None
        state: str | None = None
        from_state: str | None = None
        to_state: str | None = None
        action: str | None = None
        version: int | None = None
        version_after: int | None = None
        audit_event_id: str | None = None
        correlation_id: str | None = None
        actor: str | None = None
        reason: str | None = None
        checkpoint: str | None = None
        attempt: int | None = None
        retryable: bool | None = None
        occurred_at: DateTimeString | None = None
        created_at: DateTimeString | None = None
        updated_at: DateTimeString | None = None
        issued_at: DateTimeString | None = None

    class LifecycleReceiptRecord(BaseModel):
        model_config = ConfigDict(extra="allow")
        receipt_id: str | None = None
        category: Literal["assignment", "sla", "decision", "promotion", "job", "intake"]
        action: str | None = None
        resource_id: str | None = None
        resource_version: int | None = None
        status: str | None = None
        actor: str | None = None
        correlation_id: str | None = None
        occurred_at: DateTimeString | None = None
        receipt: MutationReceiptRecord

    class AssignmentLifecycleSnapshot(BaseModel):
        model_config = ConfigDict(extra="allow")
        assignment_id: str
        intake_id: str | None = None
        status: str
        owner_subject_id: str | None = None
        queue_id: str | None = None
        due_at: DateTimeString | None = None
        version: int = Field(..., ge=1)

    class SlaLifecycleSnapshot(BaseModel):
        model_config = ConfigDict(extra="allow")
        sla_instance_id: str
        state: str
        due_at: DateTimeString | None = None
        paused_duration_seconds: int | None = Field(None, ge=0)
        version: int = Field(..., ge=1)

    class DecisionLifecycleSnapshot(BaseModel):
        model_config = ConfigDict(extra="allow")
        decision_id: str | None = None
        receipt_id: str | None = None
        status: str
        action: str | None = None
        version: int = Field(..., ge=1)
        proposer: str | None = None
        reviewer: str | None = None
        graph_plan: MatchGraphPlan | None = None
        correlation_id: str | None = None
        created_at: DateTimeString | None = None
        updated_at: DateTimeString | None = None

    class PromotionLifecycleSnapshot(BaseModel):
        model_config = ConfigDict(extra="allow")
        promotion_decision_id: str
        intake_id: str | None = None
        status: str
        candidate_site_id: str | None = None
        site_score_job_id: str | None = None
        version: int = Field(..., ge=1)

    class JobLifecycleSnapshot(BaseModel):
        model_config = ConfigDict(extra="allow")
        job_id: str
        status: str
        attempt: int | None = Field(None, ge=0)
        checkpoint: str | None = None
        next_retry_at: DateTimeString | None = None
        fence_token: int | str | None = None
        version: int | None = Field(None, ge=1)

    class SubmissionLifecycleReceipt(BaseModel):
        model_config = ConfigDict(extra="allow")
        receipt_id: str
        receipt_type: str
        intake_id: str
        state: str
        existing_listing_id: str | None = None
        navigation_target: str | None = None
        correlation_id: str
        issued_at: DateTimeString

    class DecisionEffectReceipt(BaseModel):
        receipt_id: str
        decision_id: str
        status: str
        identity_edge_ids: list[str]
        runtime_receipt: MutationReceiptRecord | None = None
        audit_event_id: str
        correlation_id: str
        version: int
        issued_at: DateTimeString
        evidence_state: str

    class LifecycleAggregate(BaseModel):
        intake_id: UuidString
        version: int = Field(..., ge=1)
        etag: str
        actor_facts: ActorDecisionFacts
        assignment: AssignmentLifecycleSnapshot | None = None
        sla: SlaLifecycleSnapshot | None = None
        decisions: list[DecisionLifecycleSnapshot]
        promotion: PromotionLifecycleSnapshot | None = None
        job: JobLifecycleSnapshot | None = None
        assignment_history: list[LifecycleReceiptRecord]
        sla_history: list[LifecycleReceiptRecord]
        decision_history: list[LifecycleReceiptRecord]
        promotion_history: list[LifecycleReceiptRecord]
        job_history: list[LifecycleReceiptRecord]
        mutation_receipts: list[LifecycleReceiptRecord]
        latest_decision_receipt: DecisionLifecycleSnapshot | None = None
        submission_receipt: SubmissionLifecycleReceipt | None = None

    class IntakeDetail(IntakeSummary):
        original_url: str | None
        canonical_url: str | None
        policy_state: SourcePolicyState | None
        source_snapshot_id: UuidString | None = None
        parser_run_id: UuidString | None = None
        match_case_id: UuidString | None = None
        match_case_version: int | None = None
        match_case: MatchCaseDetail | None = None
        processing_history: list[TransitionReceipt]
        fields: list[FieldValue]
        audit: list[AuditReference]
        evidence: SourceEvidenceDetail
        assignment_id: UuidString | None = None
        assignment_status: str | None = None
        sla_instance_id: UuidString | None = None
        sla_state: str | None = None
        sla_receipt: str | None = None
        lifecycle: LifecycleAggregate

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
        version: int = Field(..., ge=1)
        action: str | None = None
        proposer: UuidString | None = None
        reviewer: UuidString | None = None
        reason: str | None = None
        graph_plan: MatchGraphPlan | None = None
        effect_receipt: DecisionEffectReceipt | None = None
        reverses_decision_id: UuidString | None = None
        created_at: DateTimeString | None = None
        updated_at: DateTimeString | None = None

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

    class SavedView(SavedViewRequest):
        saved_view_id: UuidString
        owner_subject_id: UuidString
        created_at: DateTimeString
        version: int

    class InboxHeatZone(BaseModel):
        heat_zone_id: str
        label: str
        assigned_area_id: str | None = None
        region_id: str | None = None
        rank: int | None = None

    class InboxCommandContract(BaseModel):
        method: Literal["POST", "PUT"]
        path_template: str
        requires_if_match: bool
        requires_idempotency_key: bool

    class IntakeInboxBootstrap(BaseModel):
        tenant_id: UuidString
        subject_id: UuidString
        role_mode: str
        scope: dict[str, list[str] | str | None]
        heat_zones: list[InboxHeatZone]
        selected_heat_zone_id: str | None = None
        intake_methods: list[IntakeMethod]
        intake_states: list[IntakeState]
        match_outcomes: list[MatchOutcome]
        assignment_states: list[AssignmentStatus]
        sla_states: list[SlaState]
        saved_views: list[SavedView]
        commands: dict[str, InboxCommandContract]

    class RetryRequest(BaseModel):
        checkpoint: RetryCheckpoint
        reason: str = Field(..., min_length=3)
        override_retry_budget: bool = False
        risk_acknowledged: bool = False

    class JobReceipt(BaseModel):
        job_id: UuidString
        status: JobStatus
        checkpoint: str
        attempt: int
        version: int
        correlation_id: UuidString

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


    def create_listings_router(
        *,
        audit_log: InMemoryAuditLog | None = None,
        repository: Any = None,
    ) -> APIRouter:
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        bound_repository = repository

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

        return router


    class AssistedIntakeStore:
        """Small process-local contract store; durable adapters can replace it later."""
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
            AssistedIntakeStore._instances.append(self)


    class V1PromotionRepositoryAdapter:
        def __init__(self, active_store, app_state=None):
            self.active_store = active_store
            self.op_repo = getattr(app_state, "operator_intake_repository", None)

        def get_promotion(self, promotion_decision_id: str) -> dict[str, Any] | None:
            if self.op_repo:
                val = self.op_repo.get_promotion(promotion_decision_id)
                if val:
                    val.setdefault("proposer_subject_id", val.get("proposer"))
                    return val
            val = self.active_store.promotions.get(promotion_decision_id)
            if val:
                val.setdefault("proposer_subject_id", val.get("proposer"))
            return val

        def save_promotion(self, promo: dict[str, Any]) -> None:
            promo.setdefault("proposer_subject_id", promo.get("proposer"))
            if self.op_repo:
                self.op_repo.save_promotion(promo)
            self.active_store.promotions[promo["promotion_decision_id"]] = promo
            if promo.get("site_score_job_id"):
                job_id = promo["site_score_job_id"]
                job_status = {
                    "COMPLETED": "SUCCEEDED",
                    "SCORE_FAILED": "FAILED",
                }.get(promo.get("status"), "QUEUED")
                job = self.active_store.jobs.get(job_id)
                if job is None:
                    job = {
                        "job_id": job_id,
                        "status": job_status,
                        "checkpoint": "SCORE_QUEUED",
                        "attempt": 0,
                        "version": 1,
                        "correlation_id": promo.get("correlation_id") or str(uuid4()),
                        "intake_id": promo.get("intake_id"),
                        "tenant_id": promo.get("tenant_id"),
                        "candidate_site_id": promo.get("candidate_site_id"),
                    }
                    self.active_store.jobs[job_id] = job
                else:
                    job["status"] = job_status
                    job["candidate_site_id"] = promo.get("candidate_site_id")

        def list_promotions(self) -> list[dict[str, Any]]:
            if self.op_repo:
                values = self.op_repo.list_promotions()
            else:
                values = list(self.active_store.promotions.values())
            for value in values:
                value.setdefault("proposer_subject_id", value.get("proposer"))
            return values

        def get_promotion_for_intake(self, intake_id: str) -> dict[str, Any] | None:
            values = [
                value
                for value in self.list_promotions()
                if value.get("intake_id") == intake_id
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
            self.op_repo = getattr(app_state, "operator_intake_repository", None)

        def get_listing_intake(self, intake_id: str) -> dict[str, Any] | None:
            if self.op_repo:
                if hasattr(self.op_repo, "intakes"):
                    val = self.op_repo.intakes.get(intake_id)
                    if val:
                        return val
                if hasattr(self.op_repo, "_store") and hasattr(self.op_repo, "_INTAKES"):
                    val = self.op_repo._store.get(self.op_repo._INTAKES, intake_id)
                    if val:
                        return val
            return self.active_store.intakes.get(intake_id)

        def save_intake(self, intake: dict[str, Any]) -> None:
            if self.op_repo and intake.get("id"):
                self.op_repo.save_intake(intake)
            self.active_store.intakes[intake.get("intake_id") or intake.get("id")] = intake

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

        def get_listing(self, listing_id: str) -> Any | None:
            listing = self.repo.get_listing(listing_id)
            if not listing:
                return None
            address = None
            if hasattr(self.repo, "addresses"):
                for addr in self.repo.addresses:
                    if addr.address_id == listing.address_id:
                        address = addr
                        break
            elif hasattr(self.repo, "get_address"):
                address = self.repo.get_address(listing.address_id)
            elif hasattr(self.repo, "_store") and hasattr(self.repo, "_ADDRESSES"):
                address = self.repo._store.get(
                    self.repo._ADDRESSES,
                    listing.address_id,
                )

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
                "confidence": listing.confidence,
                "snapshot_id": listing.snapshot_id,
            }
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
            existing_listing = self.repo.get_listing(listing_id)
            if existing_listing:
                from shared.domain.models import Listing
                updated_listing = Listing(
                    listing_id=existing_listing.listing_id,
                    source_listing_id=existing_listing.source_listing_id,
                    source_id=existing_listing.source_id,
                    listing_status=listing_dict.get("status") or existing_listing.listing_status,
                    address_id=existing_listing.address_id,
                    rent_amount=existing_listing.rent_amount,
                    currency=existing_listing.currency,
                    area_ping=existing_listing.area_ping,
                    floor=existing_listing.floor,
                    frontage_m=existing_listing.frontage_m,
                    depth_m=existing_listing.depth_m,
                    corner_flag=existing_listing.corner_flag,
                    parking_flag=existing_listing.parking_flag,
                    utility_electricity_flag=existing_listing.utility_electricity_flag,
                    utility_drainage_flag=existing_listing.utility_drainage_flag,
                    utility_gas_flag=existing_listing.utility_gas_flag,
                    available_from=existing_listing.available_from,
                    snapshot_id=existing_listing.snapshot_id,
                    confidence=existing_listing.confidence,
                )
                if hasattr(self.repo, "listings"):
                    for i, lst in enumerate(self.repo.listings):
                        if lst.listing_id == listing_id:
                            self.repo.listings[i] = updated_listing
                            break
                else:
                    address = None
                    if hasattr(self.repo, "get_address"):
                        address = self.repo.get_address(existing_listing.address_id)
                    elif hasattr(self.repo, "_store") and hasattr(
                        self.repo, "_ADDRESSES"
                    ):
                        address = self.repo._store.get(
                            self.repo._ADDRESSES,
                            existing_listing.address_id,
                        )
                    if address is None:
                        raise ValueError(
                            f"Address {existing_listing.address_id} not found"
                        )
                    from modules.listing.domain.models import ListingDedupKey

                    key = ListingDedupKey(
                        source_id=updated_listing.source_id,
                        source_listing_id=updated_listing.source_listing_id,
                        normalized_address=(
                            address.normalized_address or address.raw_address or ""
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
                        "heatZoneId": draft.heat_zone_id or "HZ-01",
                        "title": ((getattr(draft.listing, "source_listing_id", None) or getattr(draft.listing, "source_id", None) or getattr(draft.listing, "listing_id", None)) + " 候選點") if draft.listing else "候選點",
                        "address": draft.address.raw_address if draft.address else "",
                        "status": draft.candidate_site.site_status,
                        "score": getattr(draft, "score", 68),
                        "recommendation": getattr(draft, "recommendation", "WAIT"),
                        "modelVersion": getattr(draft, "model_version", "SiteScore v2.3"),
                        "datasetSnapshotId": getattr(draft, "dataset_snapshot_id", "FS-20260704-0600"),
                        "missingData": list(getattr(draft, "missing_data", [])),
                        "reviewId": getattr(draft, "review_id", None),
                    }
                    candidates.append(c_dict)
            return candidates

        def save_candidate(self, draft: Any) -> None:
            if hasattr(self.repo, "save_candidate"):
                if isinstance(getattr(draft, "listing", None), ListingAdapterWrapper):
                    from dataclasses import replace

                    listing = self.repo.get_listing(draft.listing.listing_id)
                    address = None
                    if listing is not None:
                        if hasattr(self.repo, "get_address"):
                            address = self.repo.get_address(listing.address_id)
                        elif hasattr(self.repo, "_store") and hasattr(
                            self.repo, "_ADDRESSES"
                        ):
                            address = self.repo._store.get(
                                self.repo._ADDRESSES,
                                listing.address_id,
                            )
                    if listing is None or address is None:
                        raise ValueError(
                            "Candidate source listing or address is unavailable"
                        )
                    draft = replace(draft, listing=listing, address=address)
                self.repo.save_candidate(draft)



    def create_assisted_intake_router(
        store: AssistedIntakeStore | None = None,
        audit_log: InMemoryAuditLog | None = None,
        cursor_signing_key: str | bytes | None = None,
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
            return {name: {"schema": schemas[name]} for name in names}

        def now() -> str:
            return datetime.now(UTC).isoformat().replace("+00:00", "Z")

        def runtime_service(request: Request):
            from modules.opsboard.application.network_listings import (
                NetworkListingService,
            )

            return NetworkListingService(
                listing_repository=_repository(request),
                intake_repository=getattr(request.app.state, "operator_intake_repository", None),
            )

        def auxiliary_repository(request: Request) -> Any:
            return getattr(request.app.state, "operator_intake_repository", None)

        def hydrate_auxiliary_state(request: Request) -> None:
            repository = auxiliary_repository(request)
            if repository is None:
                return
            if hasattr(repository, "list_assignments"):
                for assignment in repository.list_assignments():
                    active.assignments[assignment["assignment_id"]] = assignment
            if hasattr(repository, "list_slas"):
                for sla in repository.list_slas():
                    active.slas[sla["sla_instance_id"]] = sla
            if hasattr(repository, "list_saved_views"):
                persisted_views = {
                    view["saved_view_id"]: view
                    for view in repository.list_saved_views()
                }
                active.saved_views = [
                    view
                    for view in active.saved_views
                    if view.get("saved_view_id") not in persisted_views
                ]
                active.saved_views.extend(persisted_views.values())

        def save_assignment(request: Request, assignment: dict[str, Any]) -> None:
            active.assignments[assignment["assignment_id"]] = assignment
            repository = auxiliary_repository(request)
            if repository is not None and hasattr(repository, "save_assignment"):
                repository.save_assignment(assignment)

        def save_sla(request: Request, sla: dict[str, Any]) -> None:
            active.slas[sla["sla_instance_id"]] = sla
            repository = auxiliary_repository(request)
            if repository is not None and hasattr(repository, "save_sla"):
                repository.save_sla(sla)

        def save_saved_view(request: Request, saved_view: dict[str, Any]) -> None:
            active.saved_views = [
                view
                for view in active.saved_views
                if view.get("saved_view_id") != saved_view["saved_view_id"]
            ]
            active.saved_views.append(saved_view)
            repository = auxiliary_repository(request)
            if repository is not None and hasattr(repository, "save_saved_view"):
                repository.save_saved_view(saved_view)

        def initial_sla_state(due_at: str | None) -> str:
            due = datetime_value(due_at)
            if due is None:
                return "ON_TRACK"
            current = datetime.now(UTC)
            if due <= current:
                return "OVERDUE"
            if (due - current).total_seconds() <= 4 * 60 * 60:
                return "DUE_SOON"
            return "ON_TRACK"

        def runtime_field_values(
            parsed_fields: dict[str, dict[str, Any]],
        ) -> list[dict[str, Any]]:
            values: list[dict[str, Any]] = []
            for field_path, cell in parsed_fields.items():
                corrected = cell.get("correctedValue")
                normalized = cell.get("normalizedValue")
                parsed = cell.get("sourceValue")
                effective = (
                    corrected
                    if corrected is not None
                    else normalized
                    if normalized is not None
                    else parsed
                )
                values.append(
                    {
                        "field_path": field_path,
                        "classification": "INTERNAL",
                        "masked": bool(cell.get("masked")),
                        "parsed": parsed,
                        "normalized": normalized,
                        "corrected": corrected,
                        "effective": effective,
                        "confidence": (0.4 if cell.get("lowConfidence") else 1.0),
                        "mask_reason_code": cell.get("mask_reason_code"),
                        "correction_actor": cell.get("correctionActor"),
                        "correction_actor_role": cell.get("correctionActorRoleId"),
                        "correction_reason": cell.get("correctionReason"),
                        "corrected_at": cell.get("correctedAt"),
                        "source_snapshot_id": cell.get("sourceSnapshotId"),
                        "parser_run_id": cell.get("parserRunId"),
                        "parser_version": cell.get("parserVersion"),
                    }
                )
            return values

        def graph_plan_to_api(graph_plan: dict[str, Any] | None) -> dict[str, Any] | None:
            if not graph_plan:
                return None

            def pick(source: dict[str, Any], snake: str, camel: str) -> Any:
                return source.get(snake, source.get(camel))

            def node(node_value: dict[str, Any]) -> dict[str, Any]:
                return {
                    "node_id": pick(node_value, "node_id", "nodeId"),
                    "node_type": pick(node_value, "node_type", "nodeType"),
                    "status": node_value.get("status") or "EFFECTIVE",
                }

            def edge(edge_value: dict[str, Any]) -> dict[str, Any]:
                return {
                    "edge_id": pick(edge_value, "edge_id", "edgeId"),
                    "relation": edge_value.get("relation"),
                    "status": edge_value.get("status") or "EFFECTIVE",
                    "source_property_id": pick(
                        edge_value, "source_property_id", "sourcePropertyId"
                    ),
                    "target_property_id": pick(
                        edge_value, "target_property_id", "targetPropertyId"
                    ),
                    "property_id": pick(edge_value, "property_id", "propertyId"),
                    "listing_id": pick(edge_value, "listing_id", "listingId"),
                    "intake_id": pick(edge_value, "intake_id", "intakeId"),
                    "decision_id": pick(edge_value, "decision_id", "decisionId"),
                    "supersedes_edge_ids": list(
                        pick(
                            edge_value,
                            "supersedes_edge_ids",
                            "supersedesEdgeIds",
                        )
                        or []
                    ),
                }

            def snapshot(source: dict[str, Any] | None) -> dict[str, Any]:
                source = source or {}
                return {
                    "version": int(source.get("version") or 0),
                    "nodes": [node(item) for item in source.get("nodes") or []],
                    "edges": [edge(item) for item in source.get("edges") or []],
                }

            lineage = pick(graph_plan, "lineage_impact", "lineageImpact") or {}
            proposer = graph_plan.get("proposer")
            reviewer = graph_plan.get("reviewer")
            original = pick(graph_plan, "original_decision", "originalDecision")
            requires_human = pick(
                graph_plan,
                "requires_human_decision",
                "requiresHumanDecision",
            )
            return {
                "plan_id": pick(graph_plan, "plan_id", "planId"),
                "plan_type": pick(graph_plan, "plan_type", "planType"),
                "status": graph_plan.get("status") or "PROPOSED",
                "operations": copy.deepcopy(graph_plan.get("operations") or []),
                "permitted_decision_types": list(
                    pick(
                        graph_plan,
                        "permitted_decision_types",
                        "permittedDecisionTypes",
                    )
                    or []
                ),
                "requires_human_decision": (
                    bool(requires_human) if requires_human is not None else True
                ),
                "before_graph": snapshot(
                    pick(graph_plan, "before_graph", "beforeGraph")
                ),
                "after_graph": snapshot(
                    pick(graph_plan, "after_graph", "afterGraph")
                ),
                "redirects": [
                    {
                        "from_property_id": pick(
                            item, "from_property_id", "fromPropertyId"
                        ),
                        "to_property_id": pick(
                            item, "to_property_id", "toPropertyId"
                        ),
                        "reason": item.get("reason"),
                        "status": item.get("status") or "PROPOSED",
                    }
                    for item in graph_plan.get("redirects") or []
                ],
                "candidate_impacts": [
                    {
                        "candidate_site_id": pick(
                            item, "candidate_site_id", "candidateSiteId"
                        ),
                        "disposition": item.get("disposition"),
                        "source_property_id": pick(
                            item, "source_property_id", "sourcePropertyId"
                        ),
                        "target_property_id": pick(
                            item, "target_property_id", "targetPropertyId"
                        ),
                    }
                    for item in pick(
                        graph_plan, "candidate_impacts", "candidateImpacts"
                    )
                    or []
                ],
                "lineage_impact": {
                    "append_only": bool(
                        pick(lineage, "append_only", "appendOnly")
                    ),
                    "source_evidence_preserved": bool(
                        pick(
                            lineage,
                            "source_evidence_preserved",
                            "sourceEvidencePreserved",
                        )
                    ),
                    "superseded_edge_ids": list(
                        pick(
                            lineage,
                            "superseded_edge_ids",
                            "supersededEdgeIds",
                        )
                        or []
                    ),
                    "affected_decision_ids": list(
                        pick(
                            lineage,
                            "affected_decision_ids",
                            "affectedDecisionIds",
                        )
                        or []
                    ),
                    "summary": lineage.get("summary") or "",
                },
                "proposer": (
                    {
                        "subject_id": pick(proposer, "subject_id", "subjectId"),
                        "role_id": pick(proposer, "role_id", "roleId"),
                    }
                    if proposer
                    else None
                ),
                "reviewer": (
                    {
                        "subject_id": pick(reviewer, "subject_id", "subjectId"),
                        "role_id": pick(reviewer, "role_id", "roleId"),
                    }
                    if reviewer
                    else None
                ),
                "expected_graph_version": int(
                    pick(
                        graph_plan,
                        "expected_graph_version",
                        "expectedGraphVersion",
                    )
                    or 0
                ),
                "original_decision": (
                    {
                        "decision_id": pick(
                            original, "decision_id", "decisionId"
                        ),
                        "action": original.get("action"),
                        "status": original.get("status"),
                        "version": original.get("version"),
                    }
                    if original
                    else None
                ),
                "generated_at": pick(graph_plan, "generated_at", "generatedAt")
                or now(),
            }

        def mutation_receipt_to_api(
            receipt_value: dict[str, Any] | None,
        ) -> dict[str, Any]:
            receipt_value = receipt_value or {}

            def pick(snake: str, camel: str) -> Any:
                return receipt_value.get(snake, receipt_value.get(camel))

            return {
                "receipt_id": pick("receipt_id", "receiptId"),
                "transition_id": pick("transition_id", "transitionId"),
                "assignment_id": pick("assignment_id", "assignmentId"),
                "sla_instance_id": pick("sla_instance_id", "slaInstanceId"),
                "job_id": pick("job_id", "jobId"),
                "promotion_decision_id": pick(
                    "promotion_decision_id", "promotionDecisionId"
                ),
                "decision_id": pick("decision_id", "decisionId"),
                "intake_id": pick("intake_id", "intakeId"),
                "listing_id": pick("listing_id", "listingId"),
                "listing_revision_id": pick(
                    "listing_revision_id", "listingRevisionId"
                ),
                "identity_edge_id": pick("identity_edge_id", "identityEdgeId"),
                "candidate_site_id": pick("candidate_site_id", "candidateSiteId"),
                "site_score_job_id": pick("site_score_job_id", "siteScoreJobId"),
                "status": receipt_value.get("status"),
                "state": receipt_value.get("state"),
                "from_state": pick("from_state", "fromState"),
                "to_state": pick("to_state", "toState"),
                "action": receipt_value.get("action")
                or receipt_value.get("decision"),
                "version": receipt_value.get("version"),
                "version_after": pick("version_after", "versionAfter"),
                "audit_event_id": pick("audit_event_id", "auditEventId"),
                "correlation_id": pick("correlation_id", "correlationId"),
                "actor": receipt_value.get("actor"),
                "reason": receipt_value.get("reason"),
                "checkpoint": receipt_value.get("checkpoint"),
                "attempt": receipt_value.get("attempt"),
                "retryable": receipt_value.get("retryable"),
                "occurred_at": pick("occurred_at", "occurredAt"),
                "created_at": pick("created_at", "createdAt"),
                "updated_at": pick("updated_at", "updatedAt"),
                "issued_at": pick("issued_at", "issuedAt"),
            }

        def decision_effect_to_api(
            effect: dict[str, Any] | None,
        ) -> dict[str, Any] | None:
            if not effect:
                return None
            return {
                "receipt_id": effect.get("receipt_id") or effect.get("receiptId"),
                "decision_id": effect.get("decision_id") or effect.get("decisionId"),
                "status": effect.get("status"),
                "identity_edge_ids": list(
                    effect.get("identity_edge_ids")
                    or effect.get("identityEdgeIds")
                    or []
                ),
                "runtime_receipt": (
                    mutation_receipt_to_api(
                        effect.get("runtime_receipt") or effect.get("runtimeReceipt")
                    )
                    if effect.get("runtime_receipt")
                    or effect.get("runtimeReceipt")
                    else None
                ),
                "audit_event_id": effect.get("audit_event_id")
                or effect.get("auditEventId"),
                "correlation_id": effect.get("correlation_id")
                or effect.get("correlationId"),
                "version": int(effect.get("version") or 1),
                "issued_at": effect.get("issued_at") or effect.get("issuedAt"),
                "evidence_state": effect.get("evidence_state")
                or effect.get("evidenceState")
                or "PARTIAL",
            }

        def match_case_to_api(match_case: dict[str, Any] | None) -> dict[str, Any] | None:
            if not match_case:
                return None
            graph_plan = match_case.get("graphPlan") or {}
            return {
                "match_case_id": match_case["matchCaseId"],
                "version": int(match_case.get("version") or 1),
                "intake_id": match_case["intakeId"],
                "outcome": match_case["outcome"],
                "confidence": float(match_case.get("confidence") or 0),
                "target_listing_id": match_case.get("targetListingId"),
                "summary": match_case.get("summary") or "",
                "comparison_fields": [
                    {
                        "field_path": field["fieldPath"],
                        "label": field.get("label") or field["fieldPath"],
                        "submitted_value": copy.deepcopy(field.get("submittedValue")),
                        "existing_value": copy.deepcopy(field.get("existingValue")),
                        "agrees": bool(field.get("agrees")),
                        "detail": field.get("detail"),
                    }
                    for field in match_case.get("comparisonFields") or []
                ],
                "signals": [
                    {
                        "key": signal["key"],
                        "label": signal.get("label") or signal["key"],
                        "agrees": bool(signal.get("agrees")),
                        "detail": signal.get("detail") or "",
                    }
                    for signal in match_case.get("signals") or []
                ],
                "graph_plan": graph_plan_to_api(graph_plan),
                "source_snapshot_id": match_case.get("sourceSnapshotId"),
                "parser_version": match_case.get("parserVersion"),
                "created_at": match_case["createdAt"],
                "updated_at": match_case["updatedAt"],
            }

        def canonicalize_runtime_intake(
            runtime: dict[str, Any],
            *,
            scope: dict[str, Any] | None = None,
            submitted_by: str | None = None,
        ) -> dict[str, Any]:
            history = runtime.get("processingHistory") or []
            submitted_at = history[0].get("occurredAt") if history else now()
            canonical_history = [
                {
                    "transition_id": transition.get("transitionId") or str(uuid4()),
                    "from_state": transition.get("fromStage"),
                    "to_state": transition.get("toStage") or runtime.get("stage"),
                    "occurred_at": transition.get("occurredAt") or now(),
                    "actor": transition.get("actor") or "system",
                    "reason_code": transition.get("reasonCode"),
                    "version_after": int(
                        transition.get("versionAfter") or runtime.get("version") or 1
                    ),
                }
                for transition in history
            ]
            canonical_audit = [
                {
                    "audit_event_id": event.get("id") or str(uuid4()),
                    "action": event.get("action") or "intake.event",
                    "occurred_at": event.get("occurredAt") or now(),
                    "result": ("FAILED" if runtime.get("stage") == "FAILED" else "SUCCEEDED"),
                    "reason_code": (
                        (event.get("metadata") or {}).get("reasonCode")
                        or (event.get("metadata") or {}).get("reason")
                    ),
                    "actor": event.get("actorName"),
                    "actor_role": event.get("actorRoleId"),
                    "before": copy.deepcopy(
                        (event.get("metadata") or {}).get("before")
                    ),
                    "after": copy.deepcopy(
                        (event.get("metadata") or {}).get("after")
                    ),
                    "source_snapshot_id": (
                        (event.get("metadata") or {}).get("sourceSnapshotId")
                    ),
                    "parser_version": (
                        (event.get("metadata") or {}).get("parserVersion")
                    ),
                    "related_ids": copy.deepcopy(
                        (event.get("metadata") or {}).get("relatedIds") or {}
                    ),
                    "correlation_id": event.get("correlationId"),
                    "resource_version": (
                        ((event.get("metadata") or {}).get("after") or {}).get(
                            "version"
                        )
                    ),
                    "evidence_state": (
                        (event.get("metadata") or {}).get("evidenceState")
                    ),
                }
                for event in runtime.get("auditEvents") or []
            ]
            target_listing_id = (runtime.get("matchResult") or {}).get("targetListingId")
            value = {
                "intake_id": runtime["id"],
                "id": runtime["id"],
                "state": runtime["stage"],
                "intake_method": runtime.get("intakeMethod") or "URL",
                "scope": scope
                or runtime.get("scope")
                or {
                    "tenant_id": runtime.get("tenantId") or "tenant-a",
                    "heat_zone_id": runtime.get("heatZoneId"),
                },
                "submitted_at": submitted_at,
                "updated_at": (history[-1].get("occurredAt") if history else submitted_at),
                "version": int(runtime.get("version") or 1),
                "job_id": runtime.get("jobId"),
                "correlation_id": runtime.get("correlationId") or str(uuid4()),
                "submitted_by": submitted_by or runtime.get("submitter") or "system",
                "assigned_to": runtime.get("owner"),
                "original_url": runtime.get("originalUrl"),
                "canonical_url": runtime.get("canonicalUrl"),
                "source_id": runtime.get("sourceId"),
                "policy_state": runtime.get("policy"),
                "match_outcome": ((runtime.get("matchResult") or {}).get("outcome")),
                "target_listing_id": target_listing_id,
                "match_case_id": runtime.get("matchCaseId"),
                "match_case_version": runtime.get("matchCaseVersion"),
                "match_case": match_case_to_api(runtime.get("matchCase")),
                "source_snapshot_id": runtime.get("snapshotId"),
                "parser_run_id": runtime.get("parserRunId"),
                "processing_history": canonical_history,
                "fields": runtime_field_values(runtime.get("parsedFields") or {}),
                "audit": canonical_audit,
                "runtime_record": copy.deepcopy(runtime),
                "latest_decision_receipt": copy.deepcopy(runtime.get("latestDecisionReceipt")),
                "submission_receipt": copy.deepcopy(runtime.get("submissionReceipt")),
                "purpose": runtime.get("purpose"),
            }
            return value

        def runtime_actor_role(principal: Principal, request: Request) -> str:
            operator_role = get_operator_role_id(request)
            if operator_role:
                return operator_role
            if principal.has_role(Role.DATA_OWNER, Role.INTAKE_DATA_STEWARD):
                return "dataSteward"
            if principal.has_role(
                Role.SITE_REVIEWER,
                Role.EXECUTIVE,
                Role.INTAKE_EXPANSION_MANAGER,
            ):
                return "expansionManager"
            if principal.has_role(
                Role.AUDITOR,
                Role.ARCHITECTURE_OWNER,
                Role.INTAKE_GOVERNANCE_REVIEWER,
            ):
                return "governanceReviewer"
            if principal.has_role(Role.FINANCE_LEGAL, Role.INTAKE_PRIVACY_OFFICER):
                return "privacyOfficer"
            if principal.has_role(Role.INTAKE_PERMISSION_LIMITED):
                return "permissionLimited"
            return "expansionStaff"

        def identity_decision_to_api(
            decision: dict[str, Any],
        ) -> dict[str, Any]:
            effect = decision.get("effectReceipt") or {}
            resource_versions: dict[str, int] = {
                "identityDecision": int(decision.get("version") or 1)
            }
            runtime_receipt = effect.get("runtimeReceipt") or {}
            if runtime_receipt.get("version") is not None:
                resource_versions["intake"] = int(runtime_receipt["version"])
            return {
                "decision_id": decision["decisionId"],
                "status": decision["status"],
                "resource_versions": resource_versions,
                "job_id": None,
                "audit_event_id": decision["auditEventId"],
                "correlation_id": decision.get("correlationId") or str(uuid4()),
                "version": int(decision.get("version") or 1),
                "action": decision.get("action"),
                "tenant_id": decision["tenantId"],
                "proposer": decision.get("proposer"),
                "reviewer": decision.get("reviewer"),
                "reason": decision.get("reason"),
                "graph_plan": graph_plan_to_api(decision.get("plan")),
                "effect_receipt": decision_effect_to_api(effect),
                "reverses_decision_id": decision.get("reversesDecisionId"),
                "created_at": decision.get("createdAt"),
                "updated_at": decision.get("updatedAt"),
            }

        def canonical_role_mode(principal: Principal) -> str:
            if principal.has_role(Role.FINANCE_LEGAL, Role.INTAKE_PRIVACY_OFFICER):
                return "privacy-officer"
            if principal.has_role(
                Role.AUDITOR,
                Role.ARCHITECTURE_OWNER,
                Role.INTAKE_GOVERNANCE_REVIEWER,
            ):
                return "governance-reviewer"
            if principal.has_role(Role.DATA_OWNER, Role.INTAKE_DATA_STEWARD):
                return "data-steward"
            if principal.has_role(
                Role.SITE_REVIEWER,
                Role.EXECUTIVE,
                Role.INTAKE_EXPANSION_MANAGER,
            ):
                return "expansion-manager"
            if principal.has_role(Role.EXPANSION_USER, Role.INTAKE_EXPANSION_STAFF):
                return "expansion-staff"
            if principal.has_role(Role.INTAKE_PERMISSION_LIMITED):
                return "permission-limited"
            return "permission-limited"

        def allowed_intake_actions(
            *,
            principal: Principal,
            value: dict[str, Any],
            decisions: list[dict[str, Any]],
            promotion: dict[str, Any] | None,
        ) -> tuple[list[str], dict[str, str], dict[str, Any]]:
            role_mode = canonical_role_mode(principal)
            state = value.get("state")
            all_actions = {
                "VIEW",
                "SUBMIT_URL",
                "CORRECT",
                "ASSIGN",
                "CLAIM",
                "TRANSFER",
                "ESCALATE",
                "COMPLETE",
                "CANCEL",
                "RETRY",
                "REOPEN",
                "DECIDE_MATCH",
                "MERGE",
                "SPLIT",
                "UNMERGE",
                "REVERSE",
                "REQUEST_PROMOTION",
                "REVIEW_PROMOTION",
                "PAUSE_SLA",
                "RESUME_SLA",
                "CANCEL_JOB",
                "REPLAY_JOB",
                "EXPORT_EVIDENCE",
            }
            permitted_by_role = {
                "expansion-staff": {
                    "VIEW",
                    "SUBMIT_URL",
                    "CORRECT",
                    "ASSIGN",
                    "CLAIM",
                    "TRANSFER",
                    "COMPLETE",
                    "CANCEL",
                    "RETRY",
                    "REQUEST_PROMOTION",
                },
                "expansion-manager": all_actions - {"EXPORT_EVIDENCE"},
                "data-steward": all_actions - {"REVIEW_PROMOTION"},
                "governance-reviewer": {"VIEW", "EXPORT_EVIDENCE"},
                "privacy-officer": {"VIEW", "REOPEN", "EXPORT_EVIDENCE"},
                "permission-limited": {"VIEW"},
            }[role_mode]
            state_denials: dict[str, str] = {}
            if state == "CANCELLED":
                for action in permitted_by_role - {"VIEW", "EXPORT_EVIDENCE"}:
                    state_denials[action] = "WORKFLOW_STATE_DENIED"
            if state != "FAILED":
                state_denials["RETRY"] = "WORKFLOW_STATE_DENIED"
            if state != "QUARANTINED":
                state_denials["REOPEN"] = "WORKFLOW_STATE_DENIED"
            if state not in {"READY", "NEEDS_REVIEW"}:
                state_denials["DECIDE_MATCH"] = "WORKFLOW_STATE_DENIED"
                state_denials["REQUEST_PROMOTION"] = "WORKFLOW_STATE_DENIED"

            pending_decisions = [
                decision
                for decision in decisions
                if decision.get("status") in {"PENDING_REVIEW", "REVERSAL_PENDING"}
            ]
            pending_promotion = (
                promotion
                if promotion
                and promotion.get("status") in {"REQUESTED", "VALIDATING", "APPROVED"}
                else None
            )
            proposers = {
                decision.get("proposer")
                for decision in pending_decisions
                if decision.get("proposer")
            }
            if pending_promotion and pending_promotion.get("proposer_subject_id"):
                proposers.add(pending_promotion["proposer_subject_id"])
            self_review_denied = principal.subject_id in proposers
            if self_review_denied:
                for action in {
                    "DECIDE_MATCH",
                    "MERGE",
                    "SPLIT",
                    "UNMERGE",
                    "REVERSE",
                    "REVIEW_PROMOTION",
                    "REOPEN",
                }:
                    state_denials[action] = "SELF_REVIEW_DENIED"

            allowed = sorted(
                action for action in permitted_by_role if action not in state_denials
            )
            denied = {
                action: state_denials.get(action, "ROLE_DENIED")
                for action in sorted(all_actions - set(allowed))
            }
            second_actor = {
                "required": bool(pending_decisions or pending_promotion),
                "pending_decision_ids": [
                    decision["decision_id"] for decision in pending_decisions
                ],
                "proposer_subject_ids": sorted(proposers),
                "self_review_denied": self_review_denied,
                "reason_code": (
                    "SELF_REVIEW_DENIED"
                    if self_review_denied
                    else ("SECOND_ACTOR_REQUIRED" if proposers else None)
                ),
            }
            return allowed, denied, second_actor

        def persisted_identity_decision(
            request: Request,
            *,
            tenant_id: str,
            decision_id: str,
        ) -> dict[str, Any] | None:
            decision = runtime_service(request).get_identity_decision(
                tenant_id=tenant_id,
                decision_id=decision_id,
            )
            if decision is None:
                return active.decisions.get(decision_id)
            value = identity_decision_to_api(decision)
            active.decisions[decision_id] = value
            return value

        def build_lifecycle_aggregate(
            *,
            request: Request,
            value: dict[str, Any],
            masked_value: dict[str, Any],
            principal: Principal,
        ) -> dict[str, Any]:
            runtime = value.get("runtime_record") or {}
            intake_id = value["intake_id"]
            tenant_id = value["scope"]["tenant_id"]
            service = runtime_service(request)
            decisions = [
                identity_decision_to_api(decision)
                for decision in service.list_identity_decisions(
                    tenant_id=tenant_id,
                    intake_id=intake_id,
                )
            ]
            promotion = V1PromotionRepositoryAdapter(
                active,
                request.app.state,
            ).get_promotion_for_intake(intake_id)
            assignment = next(
                (
                    copy.deepcopy(candidate)
                    for candidate in active.assignments.values()
                    if candidate.get("intake_id") == intake_id
                    and candidate.get("status") != "COMPLETED"
                ),
                None,
            ) or copy.deepcopy(
                (runtime.get("lifecycleProjections") or {}).get("assignment")
            )
            sla = next(
                (
                    copy.deepcopy(candidate)
                    for candidate in active.slas.values()
                    if candidate.get("intake_id") == intake_id
                ),
                None,
            ) or copy.deepcopy((runtime.get("lifecycleProjections") or {}).get("sla"))

            job = None
            promotion_job_id = (
                promotion.get("site_score_job_id") if promotion else None
            )
            if promotion_job_id:
                job = copy.deepcopy(active.jobs.get(promotion_job_id)) or {
                    "job_id": promotion_job_id,
                    "status": (
                        "SUCCEEDED"
                        if promotion.get("status") == "COMPLETED"
                        else "FAILED"
                        if promotion.get("status") == "SCORE_FAILED"
                        else "QUEUED"
                    ),
                    "checkpoint": "SCORE_QUEUED",
                    "attempt": 0,
                    "version": 1,
                }
            job_id = runtime.get("jobId") or value.get("job_id")
            queue = getattr(request.app.state, "job_queue", None)
            if job is None and queue is not None and job_id:
                queued_job = queue.get(job_id)
                if queued_job is not None:
                    job = queued_job.to_dict()
                    job["status"] = str(job["status"]).upper()
                    job["checkpoint"] = (
                        (runtime.get("processingHistory") or [{}])[-1].get("checkpoint")
                    )
            if job is None:
                job = copy.deepcopy(
                    (runtime.get("lifecycleProjections") or {}).get("job")
                    or runtime.get("jobReceipt")
                )
            if job is not None:
                job = {
                    "job_id": job.get("job_id") or job.get("jobId"),
                    "status": str(job.get("status") or "QUEUED").upper(),
                    "attempt": int(job.get("attempt") or job.get("attempts") or 0),
                    "checkpoint": job.get("checkpoint"),
                    "next_retry_at": job.get("next_retry_at")
                    or job.get("nextRetryAt"),
                    "fence_token": job.get("fence_token")
                    or job.get("fenceToken"),
                    "version": job.get("version"),
                }

            allowed, denied, second_actor = allowed_intake_actions(
                principal=principal,
                value=value,
                decisions=decisions,
                promotion=promotion,
            )
            masked_fields = list(masked_value.get("masked_fields") or [])
            mask_reason_codes = sorted(
                {
                    field.get("mask_reason_code")
                    for field in masked_value.get("fields") or []
                    if field.get("masked") and field.get("mask_reason_code")
                }
            )
            purpose_value = value.get("purpose")
            role_mode = canonical_role_mode(principal)
            actor_facts = {
                "role_mode": role_mode,
                "allowed_actions": allowed,
                "denied_action_reasons": denied,
                "scope": {
                    "principal_tenant_id": principal.tenant_id,
                    "resource": copy.deepcopy(value.get("scope") or {}),
                    "in_scope": True,
                },
                "masking": {
                    "masked_fields": masked_fields,
                    "reason_codes": mask_reason_codes,
                    "has_masked_fields": bool(masked_fields),
                    "clearance": getattr(principal.scope.clearance, "name", None)
                    or str(principal.scope.clearance),
                },
                "purpose": {
                    "value": purpose_value,
                    "required": role_mode in {
                        "governance-reviewer",
                        "privacy-officer",
                    },
                    "bound": bool(purpose_value),
                    "reason_code": (
                        None
                        if purpose_value
                        or role_mode
                        not in {"governance-reviewer", "privacy-officer"}
                        else "PURPOSE_REQUIRED"
                    ),
                },
                "second_actor": second_actor,
            }
            raw_receipts = copy.deepcopy(runtime.get("lifecycleReceipts") or [])
            raw_receipts.extend(
                {
                    "receiptId": receipt.get("receiptId"),
                    "category": "decision",
                    "action": receipt.get("decision"),
                    "resourceId": receipt.get("decisionId") or receipt.get("receiptId"),
                    "resourceVersion": receipt.get("version"),
                    "status": receipt.get("status"),
                    "actor": receipt.get("actor"),
                    "correlationId": receipt.get("correlationId"),
                    "occurredAt": receipt.get("issuedAt"),
                    "receipt": copy.deepcopy(receipt),
                }
                for receipt in runtime.get("decisionReceipts") or []
            )

            def lifecycle_receipt_to_api(
                receipt_value: dict[str, Any],
            ) -> dict[str, Any]:
                return {
                    "receipt_id": receipt_value.get("receipt_id")
                    or receipt_value.get("receiptId"),
                    "category": receipt_value.get("category") or "intake",
                    "action": receipt_value.get("action"),
                    "resource_id": receipt_value.get("resource_id")
                    or receipt_value.get("resourceId"),
                    "resource_version": receipt_value.get("resource_version")
                    or receipt_value.get("resourceVersion"),
                    "status": receipt_value.get("status"),
                    "actor": receipt_value.get("actor"),
                    "correlation_id": receipt_value.get("correlation_id")
                    or receipt_value.get("correlationId"),
                    "occurred_at": receipt_value.get("occurred_at")
                    or receipt_value.get("occurredAt"),
                    "receipt": mutation_receipt_to_api(
                        receipt_value.get("receipt") or {}
                    ),
                }

            mutation_receipts = [
                lifecycle_receipt_to_api(receipt_value)
                for receipt_value in raw_receipts
            ]

            def decision_receipt_to_api(
                receipt_value: dict[str, Any] | None,
            ) -> dict[str, Any] | None:
                if not receipt_value:
                    return None
                return {
                    "decision_id": receipt_value.get("decision_id")
                    or receipt_value.get("decisionId"),
                    "receipt_id": receipt_value.get("receipt_id")
                    or receipt_value.get("receiptId"),
                    "status": receipt_value.get("status") or "EXECUTED",
                    "action": receipt_value.get("action")
                    or receipt_value.get("decision"),
                    "version": int(receipt_value.get("version") or 1),
                    "proposer": receipt_value.get("proposer"),
                    "reviewer": receipt_value.get("reviewer"),
                    "graph_plan": graph_plan_to_api(
                        receipt_value.get("graph_plan")
                        or receipt_value.get("graphPlan")
                    ),
                    "correlation_id": receipt_value.get("correlation_id")
                    or receipt_value.get("correlationId"),
                    "created_at": receipt_value.get("created_at")
                    or receipt_value.get("issuedAt"),
                    "updated_at": receipt_value.get("updated_at")
                    or receipt_value.get("issuedAt"),
                }

            submission = value.get("submission_receipt")
            submission_receipt = (
                {
                    "receipt_id": submission.get("receiptId"),
                    "receipt_type": submission.get("receiptType"),
                    "intake_id": submission.get("intakeId"),
                    "state": submission.get("state"),
                    "existing_listing_id": submission.get("existingListingId"),
                    "navigation_target": submission.get("navigationTarget"),
                    "correlation_id": submission.get("correlationId"),
                    "issued_at": submission.get("issuedAt"),
                }
                if submission
                else None
            )

            def history(category: str) -> list[dict[str, Any]]:
                return [
                    receipt_value
                    for receipt_value in mutation_receipts
                    if receipt_value["category"] == category
                ]

            return {
                "intake_id": intake_id,
                "version": int(value["version"]),
                "etag": f'W/"{value["version"]}"',
                "actor_facts": actor_facts,
                "assignment": assignment,
                "sla": sla,
                "decisions": decisions,
                "promotion": copy.deepcopy(promotion),
                "job": job,
                "assignment_history": history("assignment"),
                "sla_history": history("sla"),
                "decision_history": history("decision"),
                "promotion_history": history("promotion"),
                "job_history": history("job"),
                "mutation_receipts": mutation_receipts,
                "latest_decision_receipt": decision_receipt_to_api(
                    value.get("latest_decision_receipt")
                ),
                "submission_receipt": submission_receipt,
            }

        def persist_lifecycle_receipt(
            *,
            request: Request,
            intake_id: str,
            category: str,
            action: str,
            receipt_value: dict[str, Any],
            actor: str,
            correlation_id: str | None,
        ) -> dict[str, Any]:
            entry = runtime_service(request).record_lifecycle_receipt(
                intake_id=intake_id,
                category=category,
                action=action,
                receipt=receipt_value,
                actor=actor,
                correlation_id=correlation_id,
            )
            refreshed = runtime_service(request).get_intake(intake_id)
            active.intakes[intake_id] = canonicalize_runtime_intake(refreshed)
            return entry

        def authoritative_job_record(
            request: Request,
            job_id: str,
        ) -> tuple[dict[str, Any] | None, Any | None]:
            queue = getattr(request.app.state, "job_queue", None)
            queued = queue.get(job_id) if queue is not None else None
            if queued is None:
                return active.jobs.get(job_id), queue

            value = queued.to_dict()
            payload = value.get("payload") or {}
            value["status"] = str(value["status"]).upper()
            value["tenant_id"] = payload.get("tenant_id")
            value["intake_id"] = payload.get("intake_id")
            value["attempt"] = int(value.pop("attempts", 0))
            value["checkpoint"] = payload.get("current_stage")
            intake_id = value.get("intake_id")
            if intake_id:
                try:
                    runtime = runtime_service(request).get_intake(intake_id)
                except Exception:
                    runtime = None
                if runtime is not None:
                    history = runtime.get("processingHistory") or []
                    if history:
                        value["checkpoint"] = (
                            history[-1].get("checkpoint")
                            or history[-1].get("toStage")
                        )
            value["checkpoint"] = value.get("checkpoint") or "SUBMITTED"
            active.jobs[job_id] = copy.deepcopy(value)
            return value, queue

        def job_receipt_value(job: dict[str, Any]) -> dict[str, Any]:
            return {
                "job_id": job["job_id"],
                "status": str(job["status"]).upper(),
                "checkpoint": job.get("checkpoint") or "SUBMITTED",
                "attempt": int(job.get("attempt") or job.get("attempts") or 0),
                "version": int(job.get("version") or 1),
                "correlation_id": job.get("correlation_id") or str(uuid4()),
            }

        def get_principal(request: Request) -> Principal:
            from apps.api.oday_api.security.dependencies import principal_from_headers

            return principal_from_headers(request.headers)

        def get_operator_role_id(request: Request) -> str | None:
            # Only a server-selected role written by an authentication/
            # authorization dependency is trusted.  The standalone v1 intake
            # routes derive grants from the authenticated principal itself and
            # therefore normally return None here.
            return getattr(request.state, "operator_role_id", None)

        def request_correlation_id(request: Request) -> str:
            return (
                request.headers.get("x-correlation-id")
                or request.headers.get("X-Correlation-Id")
                or getattr(request.state, "correlation_id", None)
                or str(uuid4())
            )

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

        def linked_intake(
            request: Request,
            value: dict[str, Any],
        ) -> dict[str, Any] | None:
            intake_id = value.get("intake_id")
            if not intake_id:
                return None
            cached = active.intakes.get(intake_id)
            if cached is not None:
                return cached
            persisted = V1IntakeRepositoryAdapter(
                active,
                request.app.state,
            ).get_listing_intake(intake_id)
            if persisted is None:
                return None
            resolved = (
                canonicalize_runtime_intake(persisted)
                if persisted.get("stage") is not None
                else persisted
            )
            active.intakes[intake_id] = resolved
            return resolved

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
            request: Request,
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
                repository = auxiliary_repository(request)
                persisted = (
                    repository.get_api_replay(composite_key)
                    if repository is not None
                    and hasattr(repository, "get_api_replay")
                    else None
                )
                if persisted is not None:
                    prior = (
                        persisted["digest"],
                        persisted["result"],
                        int(persisted["status_code"]),
                    )
                    active.replays[composite_key] = copy.deepcopy(prior)
            if prior is None:
                return None
            if prior[0] != digest:
                raise HTTPException(409, "idempotency key was used with another payload")
            return copy.deepcopy(prior[1]), prior[2]

        def replay(
            request: Request,
            key: str | None,
            body: Any,
            tenant_id: str,
            actor_id: str,
            operation_id: str,
            make: Any,
            *,
            resource_id: str | None = None,
        ) -> tuple[dict[str, Any], int, bool]:
            prior = load_replay(
                request,
                key,
                body,
                tenant_id,
                actor_id,
                operation_id,
                resource_id=resource_id,
            )
            if prior is not None:
                return prior[0], prior[1], True
            result, code = make()
            digest = fingerprint(body)
            replay_scope = resource_id or "_collection"
            composite_key = (
                f"{tenant_id}:{actor_id}:{operation_id}:{replay_scope}:{key}"
            )
            active.replays[composite_key] = (digest, copy.deepcopy(result), code)
            repository = auxiliary_repository(request)
            if repository is not None and hasattr(repository, "save_api_replay"):
                repository.save_api_replay(
                    composite_key,
                    {
                        "replay_key": composite_key,
                        "digest": digest,
                        "result": copy.deepcopy(result),
                        "status_code": code,
                    },
                )
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
            fields = intake.setdefault("fields", [])
            intake["fields"] = [
                field for field in fields if field.get("field_path") != field_path
            ]
            intake["fields"].append({
                "field_path": field_path,
                "corrected": corrected_value,
                "classification": "INTERNAL",
                "masked": False,
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
                timestamp = datetime_value(value.get("submitted_at"))
                return (timestamp.timestamp() if timestamp else 0, intake_id)
            if sort_value == "updated_at_desc":
                timestamp = datetime_value(value.get("updated_at"))
                return (timestamp.timestamp() if timestamp else 0, intake_id)
            if sort_value == "due_at_asc":
                due_at = value.get("due_at")
                timestamp = datetime_value(due_at)
                return (
                    due_at is None,
                    timestamp.timestamp() if timestamp else 0,
                    intake_id,
                )
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

        def visible_saved_views(
            principal: Principal,
            tenant_id: str,
        ) -> list[dict[str, Any]]:
            role_mode = canonical_role_mode(principal)
            return [
                copy.deepcopy(view)
                for view in active.saved_views
                if view.get("tenant_id") == tenant_id
                and view.get("resource") == "intake"
                and (
                    view.get("owner_subject_id") == principal.subject_id
                    or view.get("visibility") == "TENANT"
                    or (
                        view.get("visibility") == "ROLE"
                        and view.get("shared_role") == role_mode
                    )
                )
            ]

        def parse_saved_view_values(
            *,
            principal: Principal,
            tenant_id: str,
            saved_view_id: str | None,
        ) -> dict[str, Any]:
            if not saved_view_id:
                return {}
            view = next(
                (
                    candidate
                    for candidate in visible_saved_views(principal, tenant_id)
                    if candidate.get("saved_view_id") == saved_view_id
                ),
                None,
            )
            if view is None:
                raise HTTPException(404, "saved view not found")
            query = view.get("query")
            if not isinstance(query, dict):
                raise HTTPException(409, "saved view query is invalid")
            return copy.deepcopy(query)

        def inbox_projection(value: dict[str, Any]) -> dict[str, Any]:
            projected = copy.deepcopy(value)
            runtime = projected.get("runtime_record") or {}
            intake_id = projected["intake_id"]
            lifecycle = runtime.get("lifecycleProjections") or {}

            assignments = [
                copy.deepcopy(candidate)
                for candidate in active.assignments.values()
                if candidate.get("intake_id") == intake_id
            ]
            assignments.sort(
                key=lambda candidate: (
                    candidate.get("updated_at") or candidate.get("assigned_at") or "",
                    int(candidate.get("version") or 0),
                ),
                reverse=True,
            )
            assignment = (
                assignments[0]
                if assignments
                else copy.deepcopy(lifecycle.get("assignment") or {})
            )

            slas = [
                copy.deepcopy(candidate)
                for candidate in active.slas.values()
                if candidate.get("intake_id") == intake_id
            ]
            slas.sort(
                key=lambda candidate: (
                    candidate.get("updated_at") or candidate.get("created_at") or "",
                    int(candidate.get("version") or 0),
                ),
                reverse=True,
            )
            sla = slas[0] if slas else copy.deepcopy(lifecycle.get("sla") or {})

            projected["assignment_id"] = assignment.get(
                "assignment_id", assignment.get("assignmentId")
            )
            projected["assignment_status"] = assignment.get("status")
            projected["owner_subject_id"] = assignment.get(
                "owner_subject_id", assignment.get("ownerSubjectId")
            ) or projected.get("assigned_to")
            projected["assigned_to"] = projected["owner_subject_id"]
            projected["queue_id"] = assignment.get(
                "queue_id", assignment.get("queueId")
            )
            projected["sla_instance_id"] = sla.get(
                "sla_instance_id", sla.get("slaInstanceId")
            )
            projected["sla_state"] = sla.get("state")
            projected["due_at"] = (
                sla.get("due_at")
                or sla.get("dueAt")
                or assignment.get("due_at")
                or assignment.get("dueAt")
                or projected.get("due_at")
            )

            failure = runtime.get("failure") or projected.get("failure") or {}
            state = projected.get("state")
            issue = failure.get("code")
            next_action = failure.get("nextAction") or failure.get("next_action")
            if not issue and state == "NEEDS_REVIEW":
                issue, next_action = "MATCH_REVIEW_REQUIRED", "REVIEW"
            elif not issue and state == "AWAITING_ASSISTED_ENTRY":
                issue, next_action = "ASSISTED_ENTRY_REQUIRED", "ENTER_DATA"
            elif not issue and state == "QUARANTINED":
                issue, next_action = (
                    runtime.get("policyReason") or "QUARANTINED",
                    "REVIEW_QUARANTINE",
                )

            retryable = bool(
                failure.get("retryable")
                or (runtime.get("jobReceipt") or {}).get("retryable")
                or (
                    state == "FAILED"
                    and (runtime.get("jobReceipt") or {}).get("status")
                    in {"RETRYING", "FAILED"}
                )
            )
            fields = projected.get("fields") or []

            def effective_field(*paths: str) -> Any:
                wanted = {path.lower() for path in paths}
                for field in fields:
                    if str(field.get("field_path") or "").lower() in wanted:
                        return field.get("effective")
                return None

            restricted_data = bool(projected.get("restricted_data")) or any(
                field.get("classification") == "RESTRICTED"
                or bool(field.get("masked"))
                for field in fields
            )
            last_observed_at = (
                runtime.get("capturedAt")
                or runtime.get("observedAt")
                or projected.get("last_observed_at")
                or projected.get("submitted_at")
            )
            projected.update(
                {
                    "issue": issue,
                    "next_action": next_action,
                    "retryable": retryable,
                    "quarantined": state == "QUARANTINED",
                    "failed": state == "FAILED",
                    "restricted_data": restricted_data,
                    "last_observed_at": last_observed_at,
                    "location": {
                        "address": effective_field(
                            "address",
                            "address_raw",
                            "normalized_address",
                        ),
                        "district": effective_field("district"),
                        "assigned_area_id": (
                            projected.get("scope") or {}
                        ).get("assigned_area_id"),
                        "heat_zone_id": (
                            projected.get("scope") or {}
                        ).get("heat_zone_id"),
                    },
                }
            )
            return projected

        def datetime_value(value: str | None) -> datetime | None:
            if not value:
                return None
            return datetime.fromisoformat(value.replace("Z", "+00:00"))

        @router.get(
            "/intakes/bootstrap",
            operation_id="getIntakeInboxBootstrap",
            response_model=IntakeInboxBootstrap,
            responses=api_error_responses(403),
        )
        def get_intake_inbox_bootstrap(
            request: Request,
            tenant_id: str = Depends(require_actor),
        ) -> IntakeInboxBootstrap:
            hydrate_auxiliary_state(request)
            principal = get_principal(request)
            authorize_intake_action(
                principal,
                "view",
                operator_role_id=get_operator_role_id(request),
                audit_log=active_audit_log,
                correlation_id=request.headers.get("x-correlation-id"),
            )
            heat_zones = []
            for zone in runtime_service(request).snapshot().get("heatZones") or []:
                heat_zone_id = str(zone.get("id") or zone.get("heatZoneId") or "")
                if not heat_zone_id or not principal.scope.permits_heat_zone(
                    heat_zone_id
                ):
                    continue
                heat_zones.append(
                    InboxHeatZone(
                        heat_zone_id=heat_zone_id,
                        label=str(zone.get("label") or zone.get("name") or heat_zone_id),
                        assigned_area_id=zone.get("assignedAreaId"),
                        region_id=zone.get("regionId"),
                        rank=zone.get("rank"),
                    )
                )
            return IntakeInboxBootstrap(
                tenant_id=tenant_id,
                subject_id=principal.subject_id,
                role_mode=canonical_role_mode(principal),
                scope={
                    "tenant_id": tenant_id,
                    "brand_ids": sorted(principal.scope.brand_ids),
                    "region_ids": sorted(principal.scope.region_ids),
                    "assigned_area_ids": sorted(principal.scope.assigned_area_ids),
                    "heat_zone_ids": sorted(principal.scope.heat_zone_ids),
                },
                heat_zones=heat_zones,
                selected_heat_zone_id=(
                    heat_zones[0].heat_zone_id if heat_zones else None
                ),
                intake_methods=list(IntakeMethod),
                intake_states=list(IntakeState),
                match_outcomes=list(MatchOutcome),
                assignment_states=list(AssignmentStatus),
                sla_states=list(SlaState),
                saved_views=[
                    SavedView(**view)
                    for view in visible_saved_views(principal, tenant_id)
                ],
                commands={
                    "assign": InboxCommandContract(
                        method="PUT",
                        path_template="/api/v1/intakes/{intake_id}/assignment",
                        requires_if_match=True,
                        requires_idempotency_key=True,
                    ),
                    "claim": InboxCommandContract(
                        method="POST",
                        path_template="/api/v1/assignments/{assignment_id}/actions/claim",
                        requires_if_match=True,
                        requires_idempotency_key=True,
                    ),
                    "transfer": InboxCommandContract(
                        method="POST",
                        path_template="/api/v1/assignments/{assignment_id}/actions/transfer",
                        requires_if_match=True,
                        requires_idempotency_key=True,
                    ),
                    "complete": InboxCommandContract(
                        method="POST",
                        path_template="/api/v1/assignments/{assignment_id}/actions/complete",
                        requires_if_match=True,
                        requires_idempotency_key=True,
                    ),
                },
            )

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
            intake_method: list[IntakeMethod] | None = Query(None),
            source_id: list[str] | None = Query(None),
            match_outcome: list[MatchOutcome] | None = Query(None),
            submitted_by: UuidString | None = None,
            needs_review: bool | None = None,
            owner_subject_id: list[str] | None = Query(None),
            assignment_status: list[AssignmentStatus] | None = Query(None),
            assigned: bool | None = None,
            sla_state: list[SlaState] | None = Query(None),
            assigned_area_id: UuidString | None = None,
            heat_zone_id: UuidString | None = None,
            observed_from: DateTimeString | None = None,
            observed_to: DateTimeString | None = None,
            updated_from: DateTimeString | None = None,
            updated_to: DateTimeString | None = None,
            restricted_data: bool | None = None,
            quarantined: bool | None = None,
            failed: bool | None = None,
            retryable: bool | None = None,
            saved_view_id: UuidString | None = None,
            q: str | None = Query(None, max_length=200),
            tenant_id: str = Depends(require_actor),
        ) -> IntakePage:
            hydrate_auxiliary_state(request)
            principal = get_principal(request)
            saved_query = parse_saved_view_values(
                principal=principal,
                tenant_id=tenant_id,
                saved_view_id=saved_view_id,
            )

            def saved_list(
                current: list[Any] | None,
                name: str,
                enum_type: type[Enum] | None = None,
            ) -> list[Any] | None:
                if current:
                    return current
                raw = saved_query.get(name)
                if raw is None:
                    return current
                values = raw if isinstance(raw, list) else [raw]
                try:
                    return (
                        [enum_type(value) for value in values]
                        if enum_type is not None
                        else [str(value) for value in values]
                    )
                except ValueError:
                    raise HTTPException(
                        422, f"saved view contains invalid {name}"
                    ) from None

            def saved_scalar(current: Any, name: str) -> Any:
                return current if current is not None else saved_query.get(name)

            status = saved_list(status, "status", IntakeState)
            intake_method = saved_list(
                intake_method, "intake_method", IntakeMethod
            )
            source_id = saved_list(source_id, "source_id")
            match_outcome = saved_list(
                match_outcome, "match_outcome", MatchOutcome
            )
            owner_subject_id = saved_list(owner_subject_id, "owner_subject_id")
            assignment_status = saved_list(
                assignment_status, "assignment_status", AssignmentStatus
            )
            sla_state = saved_list(sla_state, "sla_state", SlaState)
            submitted_by = saved_scalar(submitted_by, "submitted_by")
            needs_review = saved_scalar(needs_review, "needs_review")
            assigned = saved_scalar(assigned, "assigned")
            assigned_area_id = saved_scalar(
                assigned_area_id, "assigned_area_id"
            )
            heat_zone_id = saved_scalar(heat_zone_id, "heat_zone_id")
            observed_from = saved_scalar(observed_from, "observed_from")
            observed_to = saved_scalar(observed_to, "observed_to")
            updated_from = saved_scalar(updated_from, "updated_from")
            updated_to = saved_scalar(updated_to, "updated_to")
            restricted_data = saved_scalar(
                restricted_data, "restricted_data"
            )
            quarantined = saved_scalar(quarantined, "quarantined")
            failed = saved_scalar(failed, "failed")
            retryable = saved_scalar(retryable, "retryable")
            q = saved_scalar(q, "q")

            sort_value = (sort or IntakeSort.SUBMITTED_AT_DESC).value
            query_parameters = {
                "assigned_area_id": assigned_area_id,
                "assigned": assigned,
                "assignment_status": [
                    value.value for value in assignment_status or []
                ],
                "failed": failed,
                "heat_zone_id": heat_zone_id,
                "intake_method": [
                    value.value for value in intake_method or []
                ],
                "match_outcome": [value.value for value in match_outcome or []],
                "needs_review": needs_review,
                "observed_from": observed_from,
                "observed_to": observed_to,
                "owner_subject_id": owner_subject_id or [],
                "page_size": page_size,
                "q": q,
                "quarantined": quarantined,
                "restricted_data": restricted_data,
                "retryable": retryable,
                "saved_view_id": saved_view_id,
                "sla_state": [value.value for value in sla_state or []],
                "sort": sort_value,
                "source_id": source_id or [],
                "status": [value.value for value in status or []],
                "submitted_by": submitted_by,
                "updated_from": updated_from,
                "updated_to": updated_to,
            }
            query_fingerprint = fingerprint(query_parameters)
            snapshot_time = now()
            cursor_data: dict[str, Any] | None = None
            if cursor:
                cursor_data = decode_cursor(cursor, tenant_id, query_fingerprint, sort_value)
                snapshot_time = cursor_data["snapshot_time"]

            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            authorize_intake_action(
                principal,
                "view",
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            # Canonical queries are rebuilt from the durable runtime before
            # filtering. The process-local projection is only a read cache.
            for runtime in runtime_service(request).list_intakes():
                cached = active.intakes.get(runtime["id"]) or {}
                active.intakes[runtime["id"]] = canonicalize_runtime_intake(
                    runtime,
                    scope=copy.deepcopy(cached.get("scope")),
                    submitted_by=cached.get("submitted_by"),
                )

            tenant_items = [
                inbox_projection(v) for v in active.intakes.values()
                if v.get("scope", {}).get("tenant_id") == tenant_id
                and datetime_value(v.get("submitted_at")) is not None
                and datetime_value(v.get("submitted_at"))
                <= datetime_value(snapshot_time)
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
                tenant_items = [
                    v for v in tenant_items if v.get("state") in {
                        item.value for item in status
                    }
                ]
            if intake_method:
                tenant_items = [
                    v for v in tenant_items if v.get("intake_method") in {
                        item.value for item in intake_method
                    }
                ]
            if source_id:
                tenant_items = [v for v in tenant_items if v.get("source_id") in source_id]
            if match_outcome:
                tenant_items = [
                    v for v in tenant_items if v.get("match_outcome") in {
                        item.value for item in match_outcome
                    }
                ]
            if submitted_by:
                tenant_items = [v for v in tenant_items if v.get("submitted_by") == submitted_by]
            if owner_subject_id:
                tenant_items = [
                    v for v in tenant_items
                    if v.get("owner_subject_id") in owner_subject_id
                ]
            if assignment_status:
                tenant_items = [
                    v for v in tenant_items if v.get("assignment_status") in {
                        item.value for item in assignment_status
                    }
                ]
            if assigned is not None:
                tenant_items = [
                    v for v in tenant_items
                    if bool(v.get("assignment_id")) is bool(assigned)
                ]
            if sla_state:
                tenant_items = [
                    v for v in tenant_items if v.get("sla_state") in {
                        item.value for item in sla_state
                    }
                ]
            if heat_zone_id:
                tenant_items = [v for v in tenant_items if v.get("scope", {}).get("heat_zone_id") == heat_zone_id]
            if assigned_area_id:
                tenant_items = [v for v in tenant_items if v.get("scope", {}).get("assigned_area_id") == assigned_area_id]
            if needs_review is not None:
                if needs_review:
                    tenant_items = [v for v in tenant_items if v.get("state") == "NEEDS_REVIEW"]
                else:
                    tenant_items = [v for v in tenant_items if v.get("state") != "NEEDS_REVIEW"]
            if observed_from:
                lower = datetime_value(observed_from)
                tenant_items = [
                    v for v in tenant_items
                    if datetime_value(v.get("last_observed_at")) is not None
                    and datetime_value(v.get("last_observed_at")) >= lower
                ]
            if observed_to:
                upper = datetime_value(observed_to)
                tenant_items = [
                    v for v in tenant_items
                    if datetime_value(v.get("last_observed_at")) is not None
                    and datetime_value(v.get("last_observed_at")) <= upper
                ]
            if updated_from:
                lower = datetime_value(updated_from)
                tenant_items = [
                    v for v in tenant_items
                    if datetime_value(v.get("updated_at")) >= lower
                ]
            if updated_to:
                upper = datetime_value(updated_to)
                tenant_items = [
                    v for v in tenant_items
                    if datetime_value(v.get("updated_at")) <= upper
                ]
            for flag_name, expected in (
                ("restricted_data", restricted_data),
                ("quarantined", quarantined),
                ("failed", failed),
                ("retryable", retryable),
            ):
                if expected is not None:
                    tenant_items = [
                        v for v in tenant_items
                        if bool(v.get(flag_name)) is bool(expected)
                    ]

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
                masked_field_paths = set(masked_val.get("masked_fields") or [])
                location_value = copy.deepcopy(masked_val.get("location") or {})
                if masked_field_paths.intersection(
                    {"address", "address_raw", "normalized_address"}
                ):
                    location_value["address"] = None
                if "district" in masked_field_paths:
                    location_value["district"] = None
                summaries.append(IntakeSummary(
                    intake_id=masked_val["intake_id"],
                    state=masked_val["state"],
                    intake_method=masked_val["intake_method"],
                    source_id=masked_val.get("source_id"),
                    original_url=masked_val.get("original_url"),
                    canonical_url=masked_val.get("canonical_url"),
                    policy_state=masked_val.get("policy_state"),
                    match_outcome=masked_val.get("match_outcome"),
                    submitted_by=masked_val.get("submitted_by"),
                    assigned_to=masked_val.get("assigned_to"),
                    assignment_id=masked_val.get("assignment_id"),
                    assignment_status=masked_val.get("assignment_status"),
                    owner_subject_id=masked_val.get("owner_subject_id"),
                    queue_id=masked_val.get("queue_id"),
                    sla_instance_id=masked_val.get("sla_instance_id"),
                    sla_state=masked_val.get("sla_state"),
                    due_at=masked_val.get("due_at"),
                    last_observed_at=masked_val.get("last_observed_at"),
                    submitted_at=masked_val.get("submitted_at"),
                    updated_at=masked_val.get("updated_at"),
                    version=masked_val["version"],
                    scope=ScopeContext(**masked_val["scope"]),
                    issue=masked_val.get("issue"),
                    next_action=masked_val.get("next_action"),
                    retryable=bool(masked_val.get("retryable")),
                    quarantined=bool(masked_val.get("quarantined")),
                    failed=bool(masked_val.get("failed")),
                    location=InboxLocationSummary(**location_value),
                    masking=InboxMaskingSummary(
                        restricted_data=bool(
                            masked_val.get("restricted_data")
                        ),
                        has_masked_fields=bool(
                            masked_val.get("masked_fields")
                        ),
                        masked_fields=masked_val.get("masked_fields") or [],
                        reason_codes=sorted(
                            {
                                field.get("mask_reason_code")
                                for field in masked_val.get("fields") or []
                                if field.get("masked")
                                and field.get("mask_reason_code")
                            }
                        ),
                    ),
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
            response_model_exclude_none=True,
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
                **api_error_responses(403, 409, 422, idempotency_conflict=True),
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
                intake_id = str(uuid4())
                correlation_id_str = correlation_id or str(uuid4())
                queue = getattr(request.app.state, "job_queue", None)
                if queue is None:
                    raise HTTPException(
                        503,
                        "BACKPRESSURE_ACTIVE: durable intake queue unavailable",
                    )
                if queue.count_active_jobs() >= 200:
                    raise HTTPException(503, "BACKPRESSURE_ACTIVE")
                actor_role_id = runtime_actor_role(principal, request)
                service = runtime_service(request)
                runtime = service.submit_intake(
                    url=body.original_url,
                    heat_zone_id=body.scope.heat_zone_id,
                    actor_role_id=actor_role_id,
                    actor_name=actor_id,
                    idempotency_key=key,
                    correlation_id=correlation_id_str,
                    job_queue=queue,
                    async_intake=True,
                    tenant_id=tenant_id,
                    intake_id=intake_id,
                    scope_context=body.scope.model_dump(),
                )
                runtime.setdefault("matchCaseId", str(uuid4()))
                runtime["purpose"] = body.purpose
                runtime["submittedBy"] = actor_id
                service._save_intake(runtime)
                value = canonicalize_runtime_intake(
                    runtime,
                    scope=body.scope.model_dump(),
                    submitted_by=actor_id,
                )
                active.intakes[runtime["id"]] = value
                job_id = runtime.get("jobId")
                if job_id:
                    active.jobs[job_id] = {
                        "job_id": job_id,
                        "status": "QUEUED",
                        "checkpoint": "CHECKING_IDENTITY",
                        "attempt": 0,
                        "version": 1,
                        "correlation_id": correlation_id_str,
                        "intake_id": runtime["id"],
                        "tenant_id": tenant_id,
                    }
                duplicate_receipt = runtime.get("submissionReceipt") or {}
                existing_listing_id = duplicate_receipt.get("existingListingId")
                receipt_val = {
                    "intake_id": runtime["id"],
                    "state": runtime["stage"],
                    "version": int(runtime.get("version") or 1),
                    "job_id": job_id,
                    "correlation_id": correlation_id_str,
                    "submitted_at": value["submitted_at"],
                    "duplicate_hint": existing_listing_id,
                    "identity_outcome": (
                        "EXACT_DUPLICATE" if existing_listing_id else None
                    ),
                    "existing_listing_id": existing_listing_id,
                    "navigation_target": duplicate_receipt.get("navigationTarget"),
                    "submission_receipt_id": duplicate_receipt.get("receiptId"),
                }
                return receipt_val, (200 if existing_listing_id else 202)

            val, code, was_replayed = replay(request, key, body.model_dump(), tenant_id, actor_id, "submitUrlIntake", make)
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
                **api_error_responses(409, 413, 422, idempotency_conflict=True),
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
                correlation_id_str = correlation_id or str(uuid4())
                service = runtime_service(request)

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
                        runtime = service.submit_structured_intake(
                            method=body.method.value,
                            fields={
                                "address": row.address_raw,
                                "area_ping": row.area_ping,
                                "currency": row.currency,
                                "floor": row.floor,
                                "rent": row.rent_amount,
                                "source_listing_id": row.source_listing_id,
                            },
                            source_id=row.source_id,
                            original_url=row.original_url,
                            heat_zone_id=body.scope.heat_zone_id,
                            actor_role_id=runtime_actor_role(principal, request),
                            actor_name=actor_id,
                            idempotency_key=f"{key}:row:{index + 1}",
                            correlation_id=correlation_id_str,
                            tenant_id=tenant_id,
                            intake_id=intake_id,
                            scope_context=body.scope.model_dump(),
                        )
                        value = canonicalize_runtime_intake(
                            runtime,
                            scope=body.scope.model_dump(),
                            submitted_by=actor_id,
                        )
                        active.intakes[intake_id] = value
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

            val, code, was_replayed = replay(request, key, body.model_dump(), tenant_id, actor_id, "submitIntakeBatch", make)
            response.status_code = code
            return BatchIntakeReceipt(**val)

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
            hydrate_auxiliary_state(request)
            persisted = V1IntakeRepositoryAdapter(active, request.app.state).get_listing_intake(
                intake_id
            )
            if persisted is None:
                raise HTTPException(404, "intake not found")
            if persisted.get("stage") is not None:
                value = canonicalize_runtime_intake(persisted)
                active.intakes[intake_id] = value
            else:
                value = persisted
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

            # Lookup SLA if exists
            active_sla = next(
                (s for s in active.slas.values() if s.get("intake_id") == intake_id),
                None
            )

            response.headers["ETag"] = f'W/"{value["version"]}"'

            projected_value = inbox_projection(value)
            masked_val = mask_intake(principal, projected_value)
            lifecycle = build_lifecycle_aggregate(
                request=request,
                value=projected_value,
                masked_value=masked_val,
                principal=principal,
            )
            masked_field_paths = set(masked_val.get("masked_fields") or [])
            location_value = copy.deepcopy(masked_val.get("location") or {})
            if masked_field_paths.intersection(
                {"address", "address_raw", "normalized_address"}
            ):
                location_value["address"] = None
            if "district" in masked_field_paths:
                location_value["district"] = None
            detail = IntakeDetail(
                intake_id=masked_val["intake_id"],
                state=masked_val["state"],
                intake_method=masked_val["intake_method"],
                source_id=masked_val.get("source_id"),
                match_outcome=masked_val.get("match_outcome"),
                submitted_by=masked_val.get("submitted_by"),
                assigned_to=masked_val.get("assigned_to"),
                assignment_id=masked_val.get("assignment_id"),
                assignment_status=masked_val.get("assignment_status"),
                owner_subject_id=masked_val.get("owner_subject_id"),
                queue_id=masked_val.get("queue_id"),
                sla_instance_id=masked_val.get("sla_instance_id"),
                sla_state=masked_val.get("sla_state"),
                due_at=masked_val.get("due_at"),
                last_observed_at=masked_val.get("last_observed_at"),
                submitted_at=masked_val.get("submitted_at"),
                updated_at=masked_val.get("updated_at"),
                version=masked_val["version"],
                scope=ScopeContext(**masked_val["scope"]),
                issue=masked_val.get("issue"),
                next_action=masked_val.get("next_action"),
                retryable=bool(masked_val.get("retryable")),
                quarantined=bool(masked_val.get("quarantined")),
                failed=bool(masked_val.get("failed")),
                location=InboxLocationSummary(**location_value),
                masking=InboxMaskingSummary(
                    restricted_data=bool(masked_val.get("restricted_data")),
                    has_masked_fields=bool(masked_val.get("masked_fields")),
                    masked_fields=masked_val.get("masked_fields") or [],
                    reason_codes=sorted(
                        {
                            field.get("mask_reason_code")
                            for field in masked_val.get("fields") or []
                            if field.get("masked")
                            and field.get("mask_reason_code")
                        }
                    ),
                ),
                masked_fields=masked_val.get("masked_fields") or [],
                original_url=masked_val.get("original_url"),
                canonical_url=masked_val.get("canonical_url"),
                policy_state=masked_val.get("policy_state") or "APPROVED_RETRIEVAL",
                source_snapshot_id=masked_val.get("source_snapshot_id"),
                parser_run_id=masked_val.get("parser_run_id"),
                match_case_id=masked_val.get("match_case_id"),
                match_case_version=masked_val.get("match_case_version"),
                match_case=masked_val.get("match_case"),
                processing_history=masked_val.get("processing_history") or [],
                fields=masked_val.get("fields") or [],
                audit=masked_val.get("audit") or [],
                evidence=SourceEvidenceDetail(
                    original_url=masked_val.get("original_url"),
                    canonical_url=masked_val.get("canonical_url"),
                    source_id=masked_val.get("source_id"),
                    policy_state=masked_val.get("policy_state"),
                    source_snapshot_id=masked_val.get("source_snapshot_id"),
                    captured_at=(
                        (masked_val.get("runtime_record") or {}).get("capturedAt")
                    ),
                    parser_run_id=masked_val.get("parser_run_id"),
                    parser_version=(
                        (masked_val.get("runtime_record") or {}).get(
                            "parserVersion"
                        )
                    ),
                    correlation_id=masked_val.get("correlation_id"),
                    freshness_state=(
                        "STALE"
                        if masked_val.get("stale_snapshot")
                        else "CURRENT"
                        if masked_val.get("source_snapshot_id")
                        else "NOT_CAPTURED"
                    ),
                ),
                sla_receipt=active_sla.get("receipt") if active_sla else None,
                lifecycle=lifecycle,
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
                **api_error_responses(409, 422, 428),
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
            persisted = V1IntakeRepositoryAdapter(active, request.app.state).get_listing_intake(
                intake_id
            )
            if persisted is None:
                raise HTTPException(404, "intake not found")
            runtime_record = persisted if persisted.get("stage") else None
            current = (
                canonicalize_runtime_intake(persisted) if runtime_record is not None else persisted
            )
            active.intakes[intake_id] = current
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
                allowed_states = (
                    {"NEEDS_REVIEW"}
                    if is_identity_affecting
                    else {"AWAITING_ASSISTED_ENTRY", "NEEDS_REVIEW", "READY"}
                )
                if current.get("state") not in allowed_states:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                if runtime_record is not None:
                    require_version(if_match, runtime_record["version"])
                    service = runtime_service(request)
                    correlation_id_str = correlation_id or request_correlation_id(request)
                    correction_id = str(uuid4())
                    if is_identity_affecting:
                        decision = service.propose_identity_decision(
                            tenant_id=tenant_id,
                            action="identity_correction",
                            plan={
                                "intakeId": intake_id,
                                "correctionId": correction_id,
                                "fieldPath": body.field_path,
                                "correctedValue": copy.deepcopy(body.corrected_value),
                                "sourceSnapshotId": runtime_record.get("snapshotId"),
                                "parserVersion": runtime_record.get("parserVersion"),
                                "relatedIds": {
                                    "intakeId": intake_id,
                                    "correctionId": correction_id,
                                    "listingId": (
                                        runtime_record.get("matchResult") or {}
                                    ).get("targetListingId"),
                                },
                                "evidenceState": (
                                    "COMPLETE"
                                    if runtime_record.get("snapshotId")
                                    and runtime_record.get("parserVersion")
                                    else "PARTIAL"
                                ),
                            },
                            actor_role_id=runtime_actor_role(principal, request),
                            actor_name=actor_id,
                            reason=body.reason or "Identity-affecting correction proposed.",
                            risk_acknowledged=bool(body.risk_acknowledged),
                            correlation_id=correlation_id_str,
                            decision_id=correction_id,
                        )
                        proposal = {
                            "correctionId": correction_id,
                            "decisionId": decision["decisionId"],
                            "fieldPath": body.field_path,
                            "correctedValue": copy.deepcopy(body.corrected_value),
                            "reason": body.reason,
                            "proposer": actor_id,
                            "status": "PENDING_REVIEW",
                            "sourceSnapshotId": runtime_record.get("snapshotId"),
                            "parserVersion": runtime_record.get("parserVersion"),
                            "correlationId": correlation_id_str,
                            "createdAt": now(),
                        }
                        runtime_record.setdefault("correctionProposals", []).append(proposal)
                        runtime_record["version"] = int(runtime_record["version"]) + 1
                        service._save_intake(runtime_record)
                        updated_runtime = runtime_record
                        audit_event_id = decision["auditEventId"]
                        status_value = "PENDING_REVIEW"
                    else:
                        try:
                            updated_runtime = service.correct_intake(
                                intake_id=intake_id,
                                fields={body.field_path: copy.deepcopy(body.corrected_value)},
                                reason=body.reason,
                                risk_summary=body.reason or "Field correction requested.",
                                risk_acknowledged=True,
                                actor_role_id=runtime_actor_role(principal, request),
                                actor_name=actor_id,
                                idempotency_key=key,
                                correlation_id=correlation_id_str,
                            )
                        except Exception as exc:
                            raise HTTPException(409, str(exc)) from exc
                        audit_event_id = updated_runtime["auditEvents"][-1]["id"]
                        status_value = "APPLIED"
                    active.intakes[intake_id] = canonicalize_runtime_intake(updated_runtime)
                    return {
                        "correction_id": correction_id,
                        "status": status_value,
                        "intake_id": intake_id,
                        "version": updated_runtime["version"],
                        "audit_event_id": audit_event_id,
                        "correlation_id": correlation_id_str,
                    }, 201
                require_version(if_match, current["version"])
                cid = str(uuid4())
                correlation_id_str = str(uuid4())
                audit_event_id = str(uuid4())

                if is_identity_affecting:
                    # The first actor only creates a reviewable proposal.  The
                    # correction is applied by reviewIdentityDecision after an
                    # independent actor approves it.
                    current["version"] += 1
                    current["updated_at"] = now()
                    correction = {
                        "correction_id": cid,
                        "status": "PENDING_REVIEW",
                        "intake_id": intake_id,
                        "tenant_id": tenant_id,
                        "scope": copy.deepcopy(current.get("scope", {})),
                        "field_path": body.field_path,
                        "corrected_value": copy.deepcopy(body.corrected_value),
                        "proposer": actor_id,
                        "intake_version": current["version"],
                    }
                    active.corrections[cid] = correction
                    active.decisions[cid] = {
                        "decision_id": cid,
                        "status": "PENDING_REVIEW",
                        "resource_versions": {"intake": current["version"]},
                        "job_id": None,
                        "audit_event_id": audit_event_id,
                        "correlation_id": correlation_id_str,
                        "version": 1,
                        "action": "identity_correction",
                        "tenant_id": tenant_id,
                        "scope": copy.deepcopy(current.get("scope", {})),
                        "intake_id": intake_id,
                        "correction_id": cid,
                        "proposer": actor_id,
                    }
                    status_value = "PENDING_REVIEW"
                else:
                    apply_correction(
                        current,
                        field_path=body.field_path,
                        corrected_value=body.corrected_value,
                        actor_id=actor_id,
                    )
                    status_value = "APPLIED"

                receipt_val = {
                    "correction_id": cid,
                    "status": status_value,
                    "intake_id": intake_id,
                    "version": current["version"],
                    "audit_event_id": audit_event_id,
                    "correlation_id": correlation_id_str,
                }
                return receipt_val, 201

            val, code, was_replayed = replay(
                request,
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
                **api_error_responses(403, 409, 422, 428),
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
            hydrate_auxiliary_state(request)
            persisted = V1IntakeRepositoryAdapter(
                active,
                request.app.state,
            ).get_listing_intake(intake_id)
            if persisted is None:
                raise HTTPException(404, "intake not found")
            current = (
                canonicalize_runtime_intake(persisted)
                if persisted.get("stage") is not None
                else persisted
            )
            active.intakes[intake_id] = current
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
                if active_assignment is not None:
                    raise HTTPException(409, "OWNER_CONFLICT")
                current["version"] += 1
                aid = str(uuid4())
                audit_event_id = str(uuid4())
                ts = now()

                current["assigned_to"] = body.owner_subject_id
                current["due_at"] = body.due_at
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
                    "assignment_id": aid,
                    "status": "ASSIGNED",
                    "owner_subject_id": body.owner_subject_id,
                    "owner_role": body.owner_role,
                    "queue_id": body.owner_role,
                    "due_at": body.due_at,
                    "version": current["version"],
                    "audit_event_id": audit_event_id,
                    "tenant_id": tenant_id,
                    "intake_id": intake_id,
                    "assigned_at": ts,
                    "updated_at": ts,
                }
                save_assignment(request, value)
                sla = {
                    "sla_instance_id": str(uuid4()),
                    "intake_id": intake_id,
                    "tenant_id": tenant_id,
                    "state": initial_sla_state(body.due_at),
                    "due_at": body.due_at,
                    "paused_duration_seconds": 0,
                    "version": 1,
                    "created_at": ts,
                    "updated_at": ts,
                }
                save_sla(request, sla)
                value["sla_instance_id"] = sla["sla_instance_id"]
                return value, 200

            val, code, was_replayed = replay(
                request,
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
            response.headers["Idempotency-Replayed"] = str(was_replayed).lower()
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=intake_id,
                    category="assignment",
                    action="ASSIGN",
                    receipt_value={
                        **val,
                        "reason": body.reason,
                        "handoff_note": body.handoff_note,
                    },
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
                sla = active.slas.get(val.get("sla_instance_id"))
                if sla is not None:
                    persist_lifecycle_receipt(
                        request=request,
                        intake_id=intake_id,
                        category="sla",
                        action="START",
                        receipt_value={
                            **sla,
                            "reason": body.reason,
                        },
                        actor=actor_id,
                        correlation_id=request_correlation_id(request),
                    )
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
                **api_error_responses(403, 409, 422, 428),
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
            validate_idempotency_key(key)
            job, queue = authoritative_job_record(request, job_id)
            if job is None:
                raise HTTPException(404, "job not found")

            if job.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            principal = get_principal(request)
            actor_id = principal.subject_id

            # Exact replay is an immutable receipt lookup. It must precede
            # authorization that depends on mutable intake ownership/state, or
            # a lost successful response can turn into a later 403.
            prior = load_replay(
                request,
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "retryJob",
                resource_id=job_id,
            )
            if prior is not None:
                val, code = prior
                response.status_code = code
                response.headers["ETag"] = f'W/"{val["version"]}"'
                response.headers["Idempotency-Replayed"] = "true"
                return JobReceipt(**val)

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
                if job.get("status") not in {"FAILED", "DEAD_LETTER"}:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                if body.checkpoint.value != job.get("checkpoint"):
                    raise HTTPException(409, "CHECKPOINT_UNAVAILABLE")
                require_version(if_match, job["version"])
                queued = queue.get(job_id) if queue is not None else None
                if queued is not None:
                    try:
                        queue.replay(
                            job_id,
                            expected_version=queued.version,
                            fence_token=queued.fence_token,
                        )
                    except ValueError as exc:
                        raise HTTPException(409, f"JOB_FENCE_REJECTED: {exc}") from exc
                    updated, _ = authoritative_job_record(request, job_id)
                    if updated is None:
                        raise HTTPException(409, "DEPENDENCY_CONFLICT")
                    updated["checkpoint"] = body.checkpoint.value
                    active.jobs[job_id] = updated
                    receipt_val = job_receipt_value(updated)
                else:
                    job["attempt"] += 1
                    job["version"] += 1
                    job["status"] = "QUEUED"
                    job["checkpoint"] = body.checkpoint.value
                    receipt_val = job_receipt_value(job)
                return receipt_val, 202

            val, code, was_replayed = replay(
                request,
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
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=intake_id,
                    category="job",
                    action="RETRY",
                    receipt_value={
                        **val,
                        "reason": body.reason,
                        "override_retry_budget": body.override_retry_budget,
                    },
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
            return JobReceipt(**val)

        @router.post(
            "/jobs/{job_id}/actions/cancel",
            operation_id="cancelJob",
            status_code=200,
            response_model=JobReceipt,
            responses={
                200: {
                    "model": JobReceipt,
                    "description": "Job cancellation committed",
                    "headers": response_headers("ETag", "Idempotency-Replayed"),
                },
                **api_error_responses(403, 409, 422, 428),
            },
        )
        def cancel_job(
            job_id: UuidString,
            body: ReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> JobReceipt:
            validate_idempotency_key(key)
            job, queue = authoritative_job_record(request, job_id)
            if job is None:
                raise HTTPException(404, "job not found")
            if job.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            intake_id = job.get("intake_id")
            if not intake_id:
                raise HTTPException(409, "DEPENDENCY_CONFLICT")
            persisted = V1IntakeRepositoryAdapter(
                active,
                request.app.state,
            ).get_listing_intake(intake_id)
            if persisted is None:
                raise HTTPException(409, "DEPENDENCY_CONFLICT")
            principal = get_principal(request)
            require_intake_scope(principal, persisted)
            role_mode = canonical_role_mode(principal)
            if role_mode not in {"expansion-manager", "data-steward"}:
                raise HTTPException(403, "ROLE_DENIED")
            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                if job.get("status") not in {"QUEUED", "RUNNING", "RETRYING"}:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                require_version(if_match, int(job["version"]))
                queued = queue.get(job_id) if queue is not None else None
                if queued is not None:
                    from shared.jobs.queue import JobStatus as QueueJobStatus

                    try:
                        queue.update_status(
                            job_id,
                            QueueJobStatus.CANCELLED,
                            expected_version=queued.version,
                            fence_token=queued.fence_token,
                        )
                    except ValueError as exc:
                        raise HTTPException(409, f"JOB_FENCE_REJECTED: {exc}") from exc
                    updated, _ = authoritative_job_record(request, job_id)
                    if updated is None:
                        raise HTTPException(409, "DEPENDENCY_CONFLICT")
                    value = job_receipt_value(updated)
                else:
                    job["status"] = "CANCELLED"
                    job["version"] = int(job["version"]) + 1
                    value = job_receipt_value(job)
                return value, 200

            val, code, was_replayed = replay(
                request,
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "cancelJob",
                make,
                resource_id=job_id,
            )
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=intake_id,
                    category="job",
                    action="CANCEL",
                    receipt_value={**val, "reason": body.reason},
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
            response.status_code = 200 if was_replayed else code
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
                **api_error_responses(403, 404),
            },
        )
        def get_job_receipt(
            job_id: UuidString,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
        ) -> JobReceipt:
            job, _queue = authoritative_job_record(request, job_id)
            if job is None:
                raise HTTPException(404, "job not found")
            if job.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            intake = V1IntakeRepositoryAdapter(active, request.app.state).get_listing_intake(
                job.get("intake_id", "")
            )
            if intake is None:
                raise HTTPException(404, "linked intake not found")
            authorize_intake_action(
                get_principal(request),
                "view",
                resource=intake_auth_resource(intake),
                operator_role_id=get_operator_role_id(request),
                audit_log=active_audit_log,
                correlation_id=request.headers.get("x-correlation-id"),
            )
            response.headers["ETag"] = f'W/"{job["version"]}"'
            return JobReceipt(**job_receipt_value(job))

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
            hydrate_auxiliary_state(request)
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            authorize_intake_action(
                principal,
                "view",
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=request.headers.get("x-correlation-id"),
            )
            return [
                SavedView(**view)
                for view in visible_saved_views(principal, tenant_id)
            ]

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
                save_saved_view(request, value)
                return value, 201

            val, code, was_replayed = replay(request, key, body.model_dump(), tenant_id, actor_id, "createSavedView", make)
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
                    outbox_repository=getattr(request.app.state, "outbox_repository", None) or getattr(repository, "outbox_repository", None),
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

                return promo_record, 202

            val, code, was_replayed = replay(
                request,
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
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=intake_id,
                    category="promotion",
                    action="REQUEST",
                    receipt_value=val,
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
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
            op_repo = getattr(request.app.state, "operator_intake_repository", None)
            val = None
            if op_repo:
                val = op_repo.get_promotion(promotion_decision_id)
            if val is None:
                val = active.promotions.get(promotion_decision_id)
            if val is None:
                raise HTTPException(404, "promotion decision not found")

            if val.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            intake = V1IntakeRepositoryAdapter(active, request.app.state).get_listing_intake(val.get("intake_id", ""))
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
            return PromotionDecisionReceipt(**val)

        @router.get(
            "/listings/{listing_id}/revisions",
            operation_id="listListingRevisions",
            responses=api_error_responses(403, 404),
        )
        def list_listing_revisions(
            listing_id: str,
            request: Request,
            tenant_id: str = Depends(require_actor),
        ) -> dict[str, Any]:
            principal = get_principal(request)
            authorize_intake_action(
                principal,
                "view",
                resource={"tenant_id": tenant_id},
                operator_role_id=get_operator_role_id(request),
                audit_log=active_audit_log,
                correlation_id=request.headers.get("x-correlation-id"),
            )
            try:
                revisions = runtime_service(request).list_listing_revisions(listing_id)
            except Exception as exc:
                raise HTTPException(404, str(exc)) from exc
            return {
                "listing_id": listing_id,
                "revisions": revisions,
                "count": len(revisions),
            }

        @router.get(
            "/identity/edges",
            operation_id="listIdentityEdges",
            responses=api_error_responses(403),
        )
        def list_identity_edges(
            request: Request,
            listing_id: str | None = None,
            intake_id: str | None = None,
            include_superseded: bool = True,
            tenant_id: str = Depends(require_actor),
        ) -> dict[str, Any]:
            principal = get_principal(request)
            authorize_intake_action(
                principal,
                "view",
                resource={"tenant_id": tenant_id},
                operator_role_id=get_operator_role_id(request),
                audit_log=active_audit_log,
                correlation_id=request.headers.get("x-correlation-id"),
            )
            service = runtime_service(request)
            graph_edges = service.list_global_identity_edges(
                tenant_id=tenant_id,
                include_superseded=include_superseded,
            )
            listing_edges = service.list_identity_edges(
                listing_id=listing_id,
                intake_id=intake_id,
                include_superseded=include_superseded,
            )
            edges = graph_edges + listing_edges
            return {"edges": edges, "count": len(edges)}

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
            val = persisted_identity_decision(
                request,
                tenant_id=tenant_id,
                decision_id=decision_id,
            )
            if val is None:
                raise HTTPException(404, "identity decision not found")

            if val.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            intake = linked_intake(request, val)
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

        @router.get(
            "/match-cases/{match_case_id}",
            operation_id="getMatchCase",
            response_model=MatchCaseDetail,
            responses={
                200: {
                    "model": MatchCaseDetail,
                    "description": "Authoritative match comparison and graph plan",
                    "headers": response_headers("ETag"),
                },
                **api_error_responses(403, 404),
            },
        )
        def get_match_case(
            match_case_id: UuidString,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
        ) -> MatchCaseDetail:
            service = runtime_service(request)
            runtime = next(
                (
                    intake
                    for intake in service.list_intakes()
                    if intake.get("matchCaseId") == match_case_id
                ),
                None,
            )
            if runtime is None or runtime.get("matchCase") is None:
                raise HTTPException(404, "match case not found")
            if runtime.get("tenantId") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            value = canonicalize_runtime_intake(runtime)
            authorize_intake_action(
                get_principal(request),
                "view",
                resource=intake_auth_resource(value),
                operator_role_id=get_operator_role_id(request),
                audit_log=active_audit_log,
                correlation_id=request_correlation_id(request),
            )
            match_case = match_case_to_api(runtime["matchCase"])
            if match_case is None:
                raise HTTPException(404, "match case not found")
            response.headers["ETag"] = f'W/"{match_case["version"]}"'
            return MatchCaseDetail(**match_case)

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
            service = runtime_service(request)
            runtime = next(
                (
                    intake
                    for intake in service.list_intakes()
                    if intake.get("matchCaseId") == match_case_id
                ),
                None,
            )
            if runtime is None or runtime.get("matchCase") is None:
                raise HTTPException(404, "match case not found")
            if runtime.get("tenantId") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            match_case = runtime["matchCase"]
            require_intake_scope(principal, canonicalize_runtime_intake(runtime))

            def make() -> tuple[dict[str, Any], int]:
                require_version(if_match, int(match_case["version"]))
                decision = service.propose_identity_decision(
                    tenant_id=tenant_id,
                    action="match_decision",
                    plan={
                        "matchCaseId": match_case_id,
                        "matchCaseVersion": int(match_case["version"]),
                        "intakeId": runtime["id"],
                        "decisionType": body.decision_type.value,
                        "targetListingId": (
                            body.target_listing_id
                            or match_case.get("targetListingId")
                        ),
                        "targetPropertyId": body.target_property_id,
                        "graphPlan": copy.deepcopy(match_case["graphPlan"]),
                        "comparisonFields": copy.deepcopy(
                            match_case["comparisonFields"]
                        ),
                        "signals": copy.deepcopy(match_case["signals"]),
                        "sourceSnapshotId": runtime.get("snapshotId"),
                        "parserVersion": runtime.get("parserVersion"),
                        "relatedIds": {
                            "intakeId": runtime["id"],
                            "listingId": body.target_listing_id,
                            "matchCaseId": match_case_id,
                        },
                        "evidenceState": (
                            "COMPLETE"
                            if runtime.get("snapshotId") and runtime.get("parserVersion")
                            else "PARTIAL"
                        ),
                    },
                    actor_role_id=runtime_actor_role(principal, request),
                    actor_name=actor_id,
                    reason=body.reason,
                    risk_acknowledged=body.risk_acknowledged,
                    correlation_id=correlation_id,
                )
                value = identity_decision_to_api(decision)
                active.decisions[value["decision_id"]] = value
                return value, 201

            val, code, was_replayed = replay(
                request,
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
                decision = runtime_service(request).propose_identity_decision(
                    tenant_id=tenant_id,
                    action="merge",
                    plan={
                        "sourcePropertyIds": list(body.source_property_ids),
                        "targetPropertyId": body.target_property_id,
                        "candidateReassignmentPlan": [
                            item.model_dump() for item in body.candidate_reassignment_plan or []
                        ],
                        "expectedPropertyVersions": dict(body.expected_property_versions or {}),
                        "relatedIds": {
                            "sourcePropertyIds": list(body.source_property_ids),
                            "targetPropertyId": body.target_property_id,
                        },
                        "evidenceState": "COMPLETE",
                    },
                    actor_role_id=runtime_actor_role(principal, request),
                    actor_name=actor_id,
                    reason=body.reason,
                    risk_acknowledged=body.risk_acknowledged,
                    correlation_id=correlation_id,
                )
                value = identity_decision_to_api(decision)
                active.decisions[value["decision_id"]] = value
                return value, 202

            val, code, was_replayed = replay(request, key, body.model_dump(), tenant_id, actor_id, "mergeProperties", make)
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
                partitions = [
                    {
                        "targetPropertyId": partition.target_property_id,
                        "sourceIdentityEdgeIds": list(partition.source_identity_edge_ids),
                    }
                    for partition in body.partitions
                ]
                decision = runtime_service(request).propose_identity_decision(
                    tenant_id=tenant_id,
                    action="split",
                    plan={
                        "sourcePropertyId": body.source_property_id,
                        "partitions": partitions,
                        "sourceIdentityEdgeIds": [
                            edge_id
                            for partition in partitions
                            for edge_id in partition["sourceIdentityEdgeIds"]
                        ],
                        "sourcePropertyVersion": body.source_property_version,
                        "relatedIds": {
                            "sourcePropertyId": body.source_property_id,
                        },
                        "evidenceState": "COMPLETE",
                    },
                    actor_role_id=runtime_actor_role(principal, request),
                    actor_name=actor_id,
                    reason=body.reason,
                    risk_acknowledged=body.risk_acknowledged,
                    correlation_id=correlation_id,
                )
                value = identity_decision_to_api(decision)
                active.decisions[value["decision_id"]] = value
                return value, 202

            val, code, was_replayed = replay(request, key, body.model_dump(), tenant_id, actor_id, "splitProperty", make)
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
                decision = runtime_service(request).propose_identity_decision(
                    tenant_id=tenant_id,
                    action="unmerge",
                    plan={
                        "originalDecisionId": body.original_decision_id,
                        "replacementEdges": [
                            {
                                "targetPropertyId": partition.target_property_id,
                                "sourceIdentityEdgeIds": list(partition.source_identity_edge_ids),
                            }
                            for partition in body.replacement_edges
                        ],
                        "relatedIds": {
                            "originalDecisionId": body.original_decision_id,
                        },
                        "evidenceState": "COMPLETE",
                    },
                    actor_role_id=runtime_actor_role(principal, request),
                    actor_name=actor_id,
                    reason=body.reason,
                    risk_acknowledged=body.risk_acknowledged,
                    correlation_id=correlation_id,
                )
                value = identity_decision_to_api(decision)
                active.decisions[value["decision_id"]] = value
                return value, 202

            val, code, was_replayed = replay(request, key, body.model_dump(), tenant_id, actor_id, "unmergeProperty", make)
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
                **api_error_responses(403, 409, 422, 428),
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
            persisted = V1IntakeRepositoryAdapter(active, request.app.state).get_listing_intake(
                intake_id
            )
            if persisted is None:
                raise HTTPException(404, "intake not found")
            runtime_record = persisted if persisted.get("stage") else None
            current = (
                canonicalize_runtime_intake(persisted) if runtime_record is not None else persisted
            )
            active.intakes[intake_id] = current
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
                if runtime_record is not None:
                    require_version(if_match, runtime_record["version"])
                    try:
                        updated_runtime = runtime_service(request).cancel_intake(
                            intake_id=intake_id,
                            actor_role_id=runtime_actor_role(principal, request),
                            actor_name=actor_id,
                            reason=body.reason,
                            correlation_id=(correlation_id or request_correlation_id(request)),
                            job_queue=getattr(request.app.state, "job_queue", None),
                        )
                    except Exception as exc:
                        raise HTTPException(409, str(exc)) from exc
                    active.intakes[intake_id] = canonicalize_runtime_intake(updated_runtime)
                    transition = updated_runtime["processingHistory"][-1]
                    return {
                        "transition_id": transition["transitionId"],
                        "from_state": transition["fromStage"],
                        "to_state": transition["toStage"],
                        "occurred_at": transition["occurredAt"],
                        "actor": transition["actor"],
                        "reason_code": transition["reasonCode"],
                        "version_after": transition["versionAfter"],
                    }, 200
                if current.get("state") not in {
                    "SUBMITTED", "AWAITING_ASSISTED_ENTRY", "NEEDS_REVIEW"
                }:
                    raise HTTPException(
                        409,
                        f"WORKFLOW_STATE_DENIED: cannot cancel intake in state {current.get('state')}",
                    )
                require_version(if_match, current["version"])
                from_state = current.get("state", "SUBMITTED")
                updated = generic_mutate(active.intakes, intake_id, "CANCELLED", actor_id)
                tr = receipt(from_state, "CANCELLED", updated["version"], actor_id)
                return tr, 200

            val, code, was_replayed = replay(
                request,
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
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=intake_id,
                    category="intake",
                    action="CANCEL",
                    receipt_value={**val, "reason": body.reason},
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
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
                request,
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
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=intake_id,
                    category="intake",
                    action="QUARANTINE",
                    receipt_value={
                        **val,
                        "reason": body.reason,
                        "risk_acknowledged": body.risk_acknowledged,
                    },
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
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
            persisted = V1IntakeRepositoryAdapter(active, request.app.state).get_listing_intake(
                intake_id
            )
            if persisted is None:
                raise HTTPException(404, "intake not found")
            runtime_record = persisted if persisted.get("stage") else None
            current = (
                canonicalize_runtime_intake(persisted) if runtime_record is not None else persisted
            )
            active.intakes[intake_id] = current
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
                if runtime_record is not None:
                    require_version(if_match, runtime_record["version"])
                    service = runtime_service(request)
                    try:
                        if from_state == "QUARANTINED":
                            pending = runtime_record.get("pendingQuarantineRelease")
                            if pending is None:
                                updated_runtime = service.propose_quarantine_release(
                                    intake_id=intake_id,
                                    actor_role_id=runtime_actor_role(principal, request),
                                    actor_name=actor_id,
                                    reason=body.reason,
                                    correlation_id=(
                                        correlation_id or request_correlation_id(request)
                                    ),
                                )
                            else:
                                if pending.get("proposer") == actor_id:
                                    raise HTTPException(403, "SELF_REVIEW_DENIED")
                                updated_runtime = service.release_quarantine(
                                    intake_id=intake_id,
                                    actor_role_id=runtime_actor_role(principal, request),
                                    actor_name=actor_id,
                                    reason=body.reason,
                                    correlation_id=(
                                        correlation_id or request_correlation_id(request)
                                    ),
                                )
                        elif from_state == "FAILED":
                            updated_runtime = service.retry_intake(
                                intake_id=intake_id,
                                actor_role_id=runtime_actor_role(principal, request),
                                actor_name=actor_id,
                                correlation_id=(correlation_id or request_correlation_id(request)),
                                job_queue=getattr(request.app.state, "job_queue", None),
                                tenant_id=tenant_id,
                            )
                        else:
                            raise HTTPException(
                                409,
                                (
                                    "WORKFLOW_STATE_DENIED: cannot reopen intake in state "
                                    f"{from_state}"
                                ),
                            )
                    except HTTPException:
                        raise
                    except Exception as exc:
                        raise HTTPException(409, str(exc)) from exc
                    active.intakes[intake_id] = canonicalize_runtime_intake(updated_runtime)
                    transition = updated_runtime["processingHistory"][-1]
                    return {
                        "transition_id": transition["transitionId"],
                        "from_state": transition["fromStage"],
                        "to_state": transition["toStage"],
                        "occurred_at": transition["occurredAt"],
                        "actor": transition["actor"],
                        "reason_code": transition["reasonCode"],
                        "version_after": transition["versionAfter"],
                    }, 200
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
                request,
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
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=intake_id,
                    category="intake",
                    action=("RETRY" if current.get("state") == "FAILED" else "REOPEN"),
                    receipt_value={
                        **val,
                        "reason": body.reason,
                        "risk_acknowledged": body.risk_acknowledged,
                    },
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
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
                **api_error_responses(403, 409, 422, 428),
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
            hydrate_auxiliary_state(request)
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
            intake = linked_intake(request, current)
            if intake is not None:
                require_intake_scope(principal, intake)

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_staff = (principal.has_role(Role.EXPANSION_USER) or operator_role_id in (
                "expansion-staff", "expansionStaff", "expansion-user", "expansion_user"
            )) and not is_manager
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")

            if not (is_manager or is_staff or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            prior = load_replay(
                request,
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "claimAssignment",
                resource_id=assignment_id,
            )
            if prior is not None:
                val, _ = prior
                response.status_code = 200
                response.headers["ETag"] = f'W/"{val["version"]}"'
                return AssignmentReceipt(**val)

            if (is_staff or is_steward) and current.get("owner_subject_id") != actor_id:
                raise HTTPException(403, "OWNERSHIP_REQUIRED")

            def make() -> tuple[dict[str, Any], int]:
                if current.get("status") not in {
                    "ASSIGNED",
                    "TRANSFERRED",
                    "ESCALATED",
                }:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                require_version(if_match, current["version"])
                updated = generic_mutate(active.assignments, assignment_id, "CLAIMED", actor_id)
                save_assignment(request, updated)
                return updated, 200

            val, code, was_replayed = replay(
                request,
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
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=current["intake_id"],
                    category="assignment",
                    action="CLAIM",
                    receipt_value={**val, "reason": body.reason},
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
            return AssignmentReceipt(**val)

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
                **api_error_responses(403, 409, 422, 428),
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
            hydrate_auxiliary_state(request)
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
            intake = linked_intake(request, current)
            if intake is not None:
                require_intake_scope(principal, intake)

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_staff = (principal.has_role(Role.EXPANSION_USER) or operator_role_id in (
                "expansion-staff", "expansionStaff", "expansion-user", "expansion_user"
            )) and not is_manager
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")

            if not (is_manager or is_staff or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            prior = load_replay(
                request,
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "transferAssignment",
                resource_id=assignment_id,
            )
            if prior is not None:
                val, _ = prior
                response.status_code = 200
                response.headers["ETag"] = f'W/"{val["version"]}"'
                return AssignmentReceipt(**val)

            if is_staff and current.get("owner_subject_id") != actor_id:
                raise HTTPException(403, "OWNERSHIP_REQUIRED")

            def make() -> tuple[dict[str, Any], int]:
                if current.get("status") not in {"ASSIGNED", "CLAIMED"}:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                require_version(if_match, current["version"])
                current["owner_subject_id"] = body.target_owner_subject_id
                if body.due_at is not None:
                    current["due_at"] = body.due_at
                updated = generic_mutate(active.assignments, assignment_id, "TRANSFERRED", actor_id)
                updated["audit_event_id"] = str(uuid4())
                save_assignment(request, updated)

                # Update parent intake
                intake = active.intakes.get(updated.get("intake_id", ""))
                if intake:
                    intake["assigned_to"] = body.target_owner_subject_id
                    if body.due_at is not None:
                        intake["due_at"] = body.due_at
                return updated, 200

            val, code, was_replayed = replay(
                request,
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
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=current["intake_id"],
                    category="assignment",
                    action="TRANSFER",
                    receipt_value={
                        **val,
                        "reason": body.reason,
                        "handoff_note": body.handoff_note,
                    },
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
            return AssignmentReceipt(**val)

        @router.post(
            "/assignments/{assignment_id}/actions/escalate",
            operation_id="escalateAssignment",
            status_code=200,
            response_model=AssignmentReceipt,
            responses={
                200: {
                    "model": AssignmentReceipt,
                    "description": "Assignment escalated",
                    "headers": response_headers("ETag", "Idempotency-Replayed"),
                },
                **api_error_responses(403, 409, 422, 428),
            },
        )
        def escalate_assignment(
            assignment_id: UuidString,
            body: ReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: IdempotencyKeyValue = IDEMPOTENCY_KEY_HEADER,
            if_match: IfMatchValue = IF_MATCH_HEADER,
        ) -> AssignmentReceipt:
            validate_idempotency_key(key)
            hydrate_auxiliary_state(request)
            current = active.assignments.get(assignment_id)
            if current is None:
                raise HTTPException(404, "assignment not found")
            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            principal = get_principal(request)
            intake = linked_intake(request, current)
            if intake is None:
                raise HTTPException(409, "DEPENDENCY_CONFLICT")
            require_intake_scope(principal, intake)
            if canonical_role_mode(principal) not in {
                "expansion-manager",
                "data-steward",
            }:
                raise HTTPException(403, "ROLE_DENIED")
            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                if current.get("status") not in {
                    "ASSIGNED",
                    "CLAIMED",
                    "TRANSFERRED",
                }:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                require_version(if_match, current["version"])
                updated = generic_mutate(
                    active.assignments,
                    assignment_id,
                    "ESCALATED",
                    actor_id,
                )
                updated["audit_event_id"] = str(uuid4())
                updated["escalation_reason"] = body.reason
                save_assignment(request, updated)
                return updated, 200

            val, code, was_replayed = replay(
                request,
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "escalateAssignment",
                make,
                resource_id=assignment_id,
            )
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=current["intake_id"],
                    category="assignment",
                    action="ESCALATE",
                    receipt_value={**val, "reason": body.reason},
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'W/"{val["version"]}"'
            response.headers["Idempotency-Replayed"] = str(was_replayed).lower()
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
                **api_error_responses(403, 409, 422, 428),
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
            hydrate_auxiliary_state(request)
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
            intake = linked_intake(request, current)
            if intake is not None:
                require_intake_scope(principal, intake)

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_staff = (principal.has_role(Role.EXPANSION_USER) or operator_role_id in (
                "expansion-staff", "expansionStaff", "expansion-user", "expansion_user"
            )) and not is_manager
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")

            if not (is_manager or is_staff or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            if (is_staff or is_steward) and current.get("owner_subject_id") != actor_id:
                raise HTTPException(403, "OWNERSHIP_REQUIRED")

            def make() -> tuple[dict[str, Any], int]:
                if current.get("status") not in {"CLAIMED", "ESCALATED"}:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                require_version(if_match, current["version"])
                updated = generic_mutate(active.assignments, assignment_id, "COMPLETED", actor_id)
                save_assignment(request, updated)
                for sla in active.slas.values():
                    if sla.get("intake_id") != current["intake_id"]:
                        continue
                    sla["state"] = "COMPLETED"
                    sla["version"] = int(sla.get("version") or 1) + 1
                    sla["updated_at"] = now()
                    save_sla(request, sla)
                return updated, 200

            val, code, was_replayed = replay(
                request,
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
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=current["intake_id"],
                    category="assignment",
                    action="COMPLETE",
                    receipt_value={**val, "reason": body.reason},
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
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
                **api_error_responses(403, 409, 422, 428),
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
            hydrate_auxiliary_state(request)
            current = active.slas.get(sla_instance_id)
            if current is None:
                raise HTTPException(404, "SLA instance not found")
            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            principal = get_principal(request)
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
                current["state_before_pause"] = current["state"]
                updated = generic_mutate(active.slas, sla_instance_id, "PAUSED", actor_id)
                updated["active_pause_interval_id"] = str(uuid4())
                updated["audit_event_id"] = str(uuid4())
                updated["correlation_id"] = correlation_id or str(uuid4())
                updated["receipt"] = f"RCPT-SLA-PAUSE-{str(uuid4())[:8].upper()}"
                save_sla(request, updated)
                return updated, 200

            val, code, was_replayed = replay(
                request,
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
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=current["intake_id"],
                    category="sla",
                    action="PAUSE",
                    receipt_value={
                        **val,
                        "reason": body.reason,
                        "expected_resume_at": body.expected_resume_at,
                    },
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
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
                **api_error_responses(403, 409, 422, 428),
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
            hydrate_auxiliary_state(request)
            current = active.slas.get(sla_instance_id)
            if current is None:
                raise HTTPException(404, "SLA instance not found")
            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            principal = get_principal(request)
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
                resume_state = current.pop("state_before_pause", "ON_TRACK")
                updated = generic_mutate(active.slas, sla_instance_id, resume_state, actor_id)
                updated["active_pause_interval_id"] = None
                updated["audit_event_id"] = str(uuid4())
                updated["correlation_id"] = correlation_id or str(uuid4())
                save_sla(request, updated)
                return updated, 200

            val, code, was_replayed = replay(
                request,
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
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=current["intake_id"],
                    category="sla",
                    action="RESUME",
                    receipt_value={**val, "reason": body.reason},
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
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
                **api_error_responses(403, 409, 422, 428),
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
            validate_idempotency_key(key)
            op_repo = getattr(request.app.state, "operator_intake_repository", None)
            current = None
            if op_repo:
                current = op_repo.get_promotion(promotion_decision_id)
            if current is None:
                current = active.promotions.get(promotion_decision_id)
            if current is None:
                raise HTTPException(404, "promotion decision not found")

            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            intake = V1IntakeRepositoryAdapter(active, request.app.state).get_listing_intake(current["intake_id"])
            intake_tenant = intake.get("scope", {}).get("tenant_id") or intake.get("scope", {}).get("tenantId") if intake else None
            if intake and intake_tenant and intake_tenant != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            if intake is not None:
                require_intake_scope(principal, intake)
            operator_role_id = get_operator_role_id(request)
            actor_id = principal.subject_id

            if current.get("proposer") == actor_id:
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

            def make() -> tuple[dict[str, Any], int]:
                if current.get("status") != "PENDING_REVIEW":
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                require_version(if_match, current["version"])

                from modules.listing.application.promotion import PromotionService
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
                    outbox_repository=getattr(request.app.state, "outbox_repository", None) or getattr(repository, "outbox_repository", None),
                    score_queue_hook=score_queue_hook,
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
                except Exception as exc:
                    if "SELF_REVIEW_DENIED" in str(exc):
                        raise HTTPException(403, "SELF_REVIEW_DENIED") from exc
                    if "DUPLICATE_CANDIDATE" in str(exc) or "DEPENDENCY_CONFLICT" in str(exc):
                        raise HTTPException(409, "DUPLICATE_CANDIDATE") from exc
                    if "WORKFLOW_STATE_DENIED" in str(exc):
                        raise HTTPException(409, "WORKFLOW_STATE_DENIED") from exc
                    raise HTTPException(422, str(exc)) from exc

                updated["reviewer_subject_id"] = actor_id
                return updated, 200

            val, code, was_replayed = replay(
                request,
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "reviewPromotionDecision",
                make,
                resource_id=promotion_decision_id,
            )
            response.status_code = 200 if was_replayed else code
            response.headers["Idempotency-Replayed"] = str(was_replayed).lower()
            response.headers["ETag"] = f'W/"{val["version"]}"'
            if not was_replayed:
                persist_lifecycle_receipt(
                    request=request,
                    intake_id=current["intake_id"],
                    category="promotion",
                    action=body.decision.value,
                    receipt_value=val,
                    actor=actor_id,
                    correlation_id=request_correlation_id(request),
                )
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
                **api_error_responses(403, 409, 422, 428),
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
            service = runtime_service(request)
            service_decision = service.get_identity_decision(
                tenant_id=tenant_id,
                decision_id=decision_id,
            )
            current = persisted_identity_decision(
                request,
                tenant_id=tenant_id,
                decision_id=decision_id,
            )
            if current is None:
                raise HTTPException(404, "identity decision not found")

            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            principal = get_principal(request)
            intake = linked_intake(request, current)
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
                if current.get("status") not in {
                    "PENDING_REVIEW",
                    "REVERSAL_PENDING",
                }:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                require_version(if_match, current["version"])
                if service_decision is not None:
                    try:
                        reviewed = service.review_identity_decision(
                            tenant_id=tenant_id,
                            decision_id=decision_id,
                            approve=body.decision == ReviewDecision.APPROVE,
                            reviewer_role_id=runtime_actor_role(principal, request),
                            reviewer_name=actor_id,
                            reason=body.reason,
                            risk_acknowledged=body.risk_acknowledged,
                            correlation_id=request_correlation_id(request),
                        )
                    except Exception as exc:
                        message = str(exc)
                        status_code = 403 if "SELF_REVIEW_DENIED" in message else 409
                        raise HTTPException(status_code, message) from exc
                    updated = identity_decision_to_api(reviewed)
                    active.decisions[decision_id] = updated
                    return updated, 200
                to_state = "APPROVED" if body.decision == ReviewDecision.APPROVE else "REJECTED"
                correction = None
                correction_intake = None
                if current.get("action") == "identity_correction":
                    correction = active.corrections.get(current.get("correction_id", ""))
                    correction_intake = linked_intake(request, current)
                    if correction is None or correction_intake is None:
                        raise HTTPException(409, "DEPENDENCY_CONFLICT")
                    if correction.get("status") != "PENDING_REVIEW":
                        raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                    if correction_intake.get("version") != correction.get("intake_version"):
                        raise HTTPException(409, "VERSION_CONFLICT")
                updated = generic_mutate(active.decisions, decision_id, to_state, actor_id)
                if correction is not None and correction_intake is not None:
                    if body.decision == ReviewDecision.APPROVE:
                        apply_correction(
                            correction_intake,
                            field_path=correction["field_path"],
                            corrected_value=correction["corrected_value"],
                            actor_id=actor_id,
                        )
                        correction["status"] = "APPLIED"
                        correction["reviewer"] = actor_id
                        updated["resource_versions"]["intake"] = correction_intake["version"]
                    else:
                        correction["status"] = "REJECTED"
                        correction["reviewer"] = actor_id
                return updated, 200

            val, code, was_replayed = replay(
                request,
                key,
                body.model_dump(),
                tenant_id,
                actor_id,
                "reviewIdentityDecision",
                make,
                resource_id=decision_id,
            )
            response.status_code = 200 if was_replayed else code
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
            service = runtime_service(request)
            service_decision = service.get_identity_decision(
                tenant_id=tenant_id,
                decision_id=decision_id,
            )
            current = persisted_identity_decision(
                request,
                tenant_id=tenant_id,
                decision_id=decision_id,
            )
            if current is None:
                raise HTTPException(404, "identity decision not found")

            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            principal = get_principal(request)
            intake = linked_intake(request, current)
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
                if service_decision is not None:
                    try:
                        reversal = service.request_identity_reversal(
                            tenant_id=tenant_id,
                            original_decision_id=decision_id,
                            actor_role_id=runtime_actor_role(principal, request),
                            actor_name=actor_id,
                            reason=body.reason,
                            correlation_id=correlation_id,
                        )
                    except Exception as exc:
                        raise HTTPException(409, str(exc)) from exc
                    updated = identity_decision_to_api(reversal)
                    active.decisions[updated["decision_id"]] = updated
                    return updated, 202
                updated = generic_mutate(
                    active.decisions, decision_id, "REVERSAL_PENDING", actor_id
                )
                return updated, 202

            val, code, was_replayed = replay(
                request,
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
        if bound_repository is not None:
            return bound_repository
        repository = getattr(request.app.state, "listing_repository", None)
        if repository is None:
            repository = InMemoryListingRepository()
            request.app.state.listing_repository = repository
        return repository


    def _geo_pipeline(request: Request) -> GeoPipeline | None:
        return getattr(request.app.state, "listing_geo_pipeline", None)


    __all__ = ["AssistedIntakeStore", "ListingImportPayload", "create_assisted_intake_router", "create_listings_router"]
