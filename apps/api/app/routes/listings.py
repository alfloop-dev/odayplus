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
    def check_uuid(v: str | None) -> str | None:
        if v is None:
            return None
        try:
            UUID(v)
            return v
        except (TypeError, ValueError):
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
        promotion_decision_id: UuidString
        intake_id: UuidString
        listing_id: UuidString
        status: PromotionStatus
        decision_type: PromotionDecisionType
        version: int = Field(..., ge=1)
        audit_event_id: UuidString
        correlation_id: UuidString
        candidate_site_id: UuidString | None = None
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

        def linked_intake(value: dict[str, Any]) -> dict[str, Any] | None:
            intake_id = value.get("intake_id")
            return active.intakes.get(intake_id) if intake_id else None

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
                return prior[0], prior[1], True
            result, code = make()
            digest = fingerprint(body)
            replay_scope = resource_id or "_collection"
            composite_key = (
                f"{tenant_id}:{actor_id}:{operation_id}:{replay_scope}:{key}"
            )
            active.replays[composite_key] = (digest, copy.deepcopy(result), code)
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
                    "policy_state": "APPROVED_RETRIEVAL",
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

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "submitUrlIntake", make)
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
                            "policy_state": "APPROVED_RETRIEVAL",
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

            # Auto-seed assignment if assigned_to is set but no assignment exists
            active_assignment = next(
                (a for a in active.assignments.values() if a.get("intake_id") == intake_id and a.get("status") != "COMPLETED"),
                None
            )
            if active_assignment is None and value.get("assigned_to"):
                aid = str(uuid4())
                active_assignment = {
                    "assignment_id": aid,
                    "status": "ASSIGNED",
                    "owner_subject_id": value.get("assigned_to"),
                    "due_at": value.get("due_at"),
                    "version": 1,
                    "audit_event_id": str(uuid4()),
                    "tenant_id": tenant_id,
                    "intake_id": intake_id,
                }
                active.assignments[aid] = active_assignment

            # Auto-seed SLA if not exists
            active_sla = next(
                (s for s in active.slas.values() if s.get("intake_id") == intake_id),
                None
            )
            if active_sla is None:
                sid = str(uuid4())
                active_sla = {
                    "sla_instance_id": sid,
                    "state": "ON_TRACK",
                    "due_at": value.get("due_at") or now(),
                    "paused_duration_seconds": 0,
                    "version": 1,
                    "audit_event_id": str(uuid4()),
                    "tenant_id": tenant_id,
                    "intake_id": intake_id,
                }
                active.slas[sid] = active_sla

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
                policy_state=masked_val.get("policy_state") or "APPROVED_RETRIEVAL",
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
                allowed_states = (
                    {"NEEDS_REVIEW"}
                    if is_identity_affecting
                    else {"AWAITING_ASSISTED_ENTRY", "NEEDS_REVIEW", "READY"}
                )
                if current.get("state") not in allowed_states:
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
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
                    "due_at": body.due_at,
                    "version": current["version"],
                    "audit_event_id": audit_event_id,
                    "tenant_id": tenant_id,
                    "intake_id": intake_id,
                }
                active.assignments[aid] = value
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
            responses=api_error_responses(403, 409, 422, 428),
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
            job = active.jobs.get(job_id)
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
                return JobReceipt(**val)

            intake_id = job.get("intake_id")
            intake = active.intakes.get(intake_id) if intake_id else None
            if intake is None:
                raise HTTPException(409, "DEPENDENCY_CONFLICT: retry job has no linked intake")
            if intake.get("scope", {}).get("tenant_id") != tenant_id:
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
                job["attempt"] += 1
                job["version"] += 1
                job["status"] = "QUEUED"
                job["checkpoint"] = body.checkpoint.value

                receipt_val = {
                    "job_id": job_id,
                    "status": "QUEUED",
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
            return JobReceipt(**val)

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
            ]
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
                if current.get("state") != "READY":
                    raise HTTPException(409, "WORKFLOW_STATE_DENIED")
                require_version(if_match, current["version"])
                did = str(uuid4())
                current["version"] += 1
                current["updated_at"] = now()

                current["processing_history"].append({
                    "transition_id": str(uuid4()),
                    "from_state": "READY",
                    "to_state": "READY",
                    "occurred_at": now(),
                    "actor": actor_id,
                    "version_after": current["version"],
                })

                value = {
                    "promotion_decision_id": did,
                    "intake_id": intake_id,
                    "listing_id": str(uuid4()),
                    "status": "PENDING_REVIEW",
                    "decision_type": "STANDARD",
                    "version": 1,
                    "audit_event_id": str(uuid4()),
                    "correlation_id": str(uuid4()),
                    "tenant_id": tenant_id,
                    "proposer": actor_id,
                }
                active.promotions[did] = value
                return value, 202

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
            if promotion_decision_id not in active.promotions:
                raise HTTPException(404, "promotion decision not found")
            val = active.promotions[promotion_decision_id]

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
            intake = linked_intake(current)
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
            intake = linked_intake(current)
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
                
                # Update parent intake
                intake = active.intakes.get(updated.get("intake_id", ""))
                if intake:
                    intake["assigned_to"] = body.target_owner_subject_id
                    if body.due_at is not None:
                        intake["due_at"] = body.due_at
                    intake["version"] += 1
                    intake["processing_history"].append({
                        "transition_id": str(uuid4()),
                        "from_state": intake.get("state"),
                        "to_state": intake.get("state"),
                        "occurred_at": now(),
                        "actor": actor_id,
                        "reason_code": f"ASSIGNMENT_TRANSFERRED: {body.reason}",
                        "version_after": intake.get("version"),
                    })
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
            intake = linked_intake(current)
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
                
                # Update parent intake
                intake = active.intakes.get(updated.get("intake_id", ""))
                if intake:
                    intake["version"] += 1
                    intake["processing_history"].append({
                        "transition_id": str(uuid4()),
                        "from_state": intake.get("state"),
                        "to_state": intake.get("state"),
                        "occurred_at": now(),
                        "actor": actor_id,
                        "reason_code": f"SLA_PAUSED: {body.reason} (Resume: {body.expected_resume_at})",
                        "version_after": intake.get("version"),
                    })
                return updated, 200

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
                
                # Update parent intake
                intake = active.intakes.get(updated.get("intake_id", ""))
                if intake:
                    intake["version"] += 1
                    intake["processing_history"].append({
                        "transition_id": str(uuid4()),
                        "from_state": intake.get("state"),
                        "to_state": intake.get("state"),
                        "occurred_at": now(),
                        "actor": actor_id,
                        "reason_code": f"SLA_RESUMED: {body.reason}",
                        "version_after": intake.get("version"),
                    })
                return updated, 200

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
            current = active.promotions.get(promotion_decision_id)
            if current is None:
                raise HTTPException(404, "promotion decision not found")

            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")
            intake = active.intakes.get(current["intake_id"])
            if intake and intake.get("scope", {}).get("tenant_id") != tenant_id:
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
                to_state = "APPROVED" if body.decision == ReviewDecision.APPROVE else "REJECTED"
                updated = generic_mutate(active.promotions, promotion_decision_id, to_state, actor_id)
                updated["reviewer_subject_id"] = actor_id
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
            response.status_code = 200 if was_replayed else code
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
