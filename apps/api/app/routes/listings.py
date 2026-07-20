from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Optional, List, Dict
from uuid import uuid4

from modules.external_data.geo import GeoPipeline
from modules.listing import InMemoryListingRepository, ListingPipeline
from shared.audit import InMemoryAuditLog

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
    from pydantic import BaseModel, Field, field_validator, ConfigDict
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:
    # ---------------------------------------------------------------------------
    # Pydantic Schemas from openapi-effective.json
    # ---------------------------------------------------------------------------
    from enum import Enum

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
        REVISION = "REVISION"
        POSSIBLE_MATCH = "POSSIBLE_MATCH"
        EXACT_DUPLICATE = "EXACT_DUPLICATE"

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

    class ScopeContext(BaseModel):
        tenant_id: str
        assigned_area_id: Optional[str] = None
        brand_id: Optional[str] = None
        heat_zone_id: Optional[str] = None
        region_id: Optional[str] = None

    class UrlIntakeRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        original_url: str = Field(..., max_length=4096)
        scope: ScopeContext
        owner_subject_id: Optional[str] = None
        purpose: Optional[str] = None

        @field_validator("original_url")
        @classmethod
        def validate_uri(cls, v: str) -> str:
            if "://" not in v:
                raise ValueError("invalid URI scheme")
            return v

    class IntakeSubmissionReceipt(BaseModel):
        intake_id: str
        state: IntakeState
        version: int
        job_id: str
        correlation_id: str
        submitted_at: str
        duplicate_hint: Optional[str] = None

    class ManualIntakeRow(BaseModel):
        address_raw: str
        area_ping: Optional[float] = None
        currency: Optional[str] = None
        floor: Optional[str] = None
        original_url: Optional[str] = None
        rent_amount: Optional[float] = None
        source_id: Optional[str] = None
        source_listing_id: Optional[str] = None

    class BatchIntakeRequest(BaseModel):
        batch_id: str
        method: str
        scope: ScopeContext
        rows: List[ManualIntakeRow]

    class FieldError(BaseModel):
        field: str
        code: str
        message: str

    class ApiError(BaseModel):
        code: str
        message: str
        retryable: bool
        correlation_id: str
        reason_code: Optional[str] = None
        field_errors: Optional[List[FieldError]] = None
        current_version: Optional[int] = None
        retry_after_seconds: Optional[int] = None
        occurred_at: str
        next_action: Optional[str] = None

    class BatchRowReceipt(BaseModel):
        row_index: int
        status: str
        intake_id: Optional[str] = None
        client_row_id: Optional[str] = None
        error: Optional[ApiError] = None

    class BatchIntakeReceipt(BaseModel):
        batch_id: str
        submitted_at: str
        accepted_count: int
        rejected_count: int
        rows: List[BatchRowReceipt]
        correlation_id: str

    class IntakeSummary(BaseModel):
        intake_id: str
        state: IntakeState
        intake_method: str
        source_id: Optional[str] = None
        match_outcome: Optional[MatchOutcome] = None
        submitted_by: str
        assigned_to: Optional[str] = None
        due_at: Optional[str] = None
        submitted_at: str
        updated_at: str
        version: int
        scope: ScopeContext
        masked_fields: Optional[List[str]] = None

    class FieldValue(BaseModel):
        field_path: str
        classification: FieldClassification
        masked: bool
        parsed: Optional[Any] = None
        normalized: Optional[Any] = None
        corrected: Optional[Any] = None
        effective: Optional[Any] = None
        confidence: Optional[float] = None
        mask_reason_code: Optional[str] = None

    class TransitionReceipt(BaseModel):
        transition_id: str
        from_state: Optional[str] = None
        to_state: str
        occurred_at: str
        actor: str
        reason_code: Optional[str] = None
        version_after: int

    class AuditReference(BaseModel):
        audit_event_id: str
        action: str
        occurred_at: str
        result: AuditResult
        reason_code: Optional[str] = None

    class IntakeDetail(IntakeSummary):
        original_url: Optional[str] = None
        canonical_url: Optional[str] = None
        policy_state: Optional[SourcePolicyState] = None
        source_snapshot_id: Optional[str] = None
        parser_run_id: Optional[str] = None
        match_case_id: Optional[str] = None
        processing_history: List[TransitionReceipt] = Field(default_factory=list)
        fields: List[FieldValue] = Field(default_factory=list)
        audit: List[AuditReference] = Field(default_factory=list)

    class IntakePage(BaseModel):
        items: List[IntakeSummary]
        next_cursor: Optional[str] = None
        page_size: int
        query_fingerprint: str
        snapshot_time: str
        total_count: int
        total_count_accuracy: str = "exact"

    class CorrectionRequest(BaseModel):
        field_path: str
        corrected_value: Any
        reason: str = Field(..., min_length=3, max_length=2000)
        risk_acknowledged: Optional[bool] = None
        expected_effective_value_sha256: Optional[str] = Field(None, pattern=r"^[a-f0-9]{64}$")

    class CorrectionReceipt(BaseModel):
        correction_id: str
        status: str
        intake_id: str
        version: int
        audit_event_id: str
        correlation_id: str
        listing_revision_id: Optional[str] = None

    class AssignmentRequest(BaseModel):
        owner_subject_id: str
        owner_role: str
        due_at: str
        reason: str = Field(..., min_length=3)
        handoff_note: Optional[str] = None

    class AssignmentReceipt(BaseModel):
        assignment_id: str
        status: str
        owner_subject_id: str
        due_at: str
        version: int
        audit_event_id: str

    class AssignmentTransferRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        target_owner_subject_id: str
        target_owner_role: str
        reason: str = Field(..., min_length=3, max_length=4000)
        handoff_note: str = Field(..., min_length=3, max_length=4000)
        due_at: Optional[str] = None

    class SlaPauseRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        reason: str = Field(..., min_length=3, max_length=4000)
        expected_resume_at: str

    class SlaReceipt(BaseModel):
        sla_instance_id: str
        state: str
        due_at: str
        paused_duration_seconds: int
        version: int
        audit_event_id: str
        correlation_id: str
        due_soon_at: Optional[str] = None
        active_pause_interval_id: Optional[str] = None

    class MatchDecisionRequest(BaseModel):
        decision_type: str
        reason: str = Field(..., min_length=3, max_length=4000)
        requested_second_reviewer_id: Optional[str] = None
        risk_acknowledged: bool
        target_listing_id: Optional[str] = None
        target_property_id: Optional[str] = None

    class DecisionReceipt(BaseModel):
        decision_id: str
        status: str
        resource_versions: Dict[str, int]
        job_id: Optional[str] = None
        audit_event_id: str
        correlation_id: str

    class CandidateReassignment(BaseModel):
        candidate_site_id: str
        disposition: str
        target_property_id: Optional[str] = None

    class MergeRequest(BaseModel):
        source_property_ids: List[str]
        target_property_id: str
        reason: str = Field(..., min_length=20)
        risk_acknowledged: Optional[bool] = None
        candidate_reassignment_plan: Optional[List[CandidateReassignment]] = None
        expected_property_versions: Optional[Dict[str, int]] = None

    class IdentityPartition(BaseModel):
        target_property_id: Optional[str] = None
        source_identity_edge_ids: List[str]

    class SplitRequest(BaseModel):
        source_property_id: str
        partitions: List[IdentityPartition]
        reason: str = Field(..., min_length=20)
        risk_acknowledged: Optional[bool] = None
        source_property_version: Optional[int] = None

    class UnmergeRequest(BaseModel):
        original_decision_id: str
        replacement_edges: List[IdentityPartition]
        reason: str = Field(..., min_length=20)
        risk_acknowledged: Optional[bool] = None

    class PromotionRequest(BaseModel):
        target_format_code: str = Field(..., min_length=1, max_length=64)
        reason: str = Field(..., min_length=3, max_length=4000)
        gate_snapshot_sha256: str = Field(..., pattern=r"^[a-f0-9]{64}$")
        requested_reviewer_id: Optional[str] = None
        risk_acknowledged: bool

    class PromotionDecisionReceipt(BaseModel):
        promotion_decision_id: str
        intake_id: str
        listing_id: str
        status: str
        decision_type: str
        version: int
        audit_event_id: str
        correlation_id: str
        candidate_site_id: Optional[str] = None
        reviewer_subject_id: Optional[str] = None
        site_score_job_id: Optional[str] = None

    class ReviewDecisionRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        decision: str
        reason: str = Field(..., min_length=3, max_length=4000)
        requested_changes: Optional[List[str]] = None
        risk_acknowledged: bool

    class SavedViewRequest(BaseModel):
        name: str = Field(..., min_length=1, max_length=120)
        query: dict
        resource: str = Field("intake", pattern="^intake$")
        shared_role: Optional[str] = None
        visibility: str = Field("PRIVATE", pattern="^(PRIVATE|ROLE|TENANT)$")

    class SavedView(BaseModel):
        name: str = Field(..., min_length=1, max_length=120)
        query: dict
        resource: str = Field("intake", pattern="^intake$")
        shared_role: Optional[str] = None
        visibility: str = Field("PRIVATE", pattern="^(PRIVATE|ROLE|TENANT)$")
        saved_view_id: str
        owner_subject_id: str
        created_at: str
        version: int

    class RetryRequest(BaseModel):
        checkpoint: str
        reason: str
        override_retry_budget: bool = False
        risk_acknowledged: bool = False

    class JobReceipt(BaseModel):
        job_id: str
        status: str
        checkpoint: str
        attempt: int
        version: int
        correlation_id: str

    class ReasonCommand(BaseModel):
        model_config = ConfigDict(extra="forbid")
        reason: str = Field(..., min_length=3, max_length=4000)

    class RiskReasonCommand(ReasonCommand):
        model_config = ConfigDict(extra="forbid")
        risk_acknowledged: bool
        incident_or_change_id: Optional[str] = Field(None, max_length=200)


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
    ) -> APIRouter:
        """Implement the approved ODP assisted-intake `/api/v1` contract."""
        from shared.auth import Principal, Role
        from modules.listing.application.intake_authorization import authorize_intake_action, mask_intake

        active = store or AssistedIntakeStore()
        active_audit_log = audit_log or InMemoryAuditLog()
        router = APIRouter(tags=["assisted-listing-intake"])

        def now() -> str:
            return datetime.now(UTC).isoformat().replace("+00:00", "Z")

        def get_principal(request: Request) -> Principal:
            from apps.api.oday_api.security.dependencies import principal_from_headers
            return principal_from_headers(request.headers)

        def get_operator_role_id(request: Request) -> str | None:
            return request.headers.get("x-operator-role") or request.headers.get("x-roles")

        def require_actor(request: Request) -> str:
            principal = get_principal(request)
            if not principal.authenticated:
                raise HTTPException(401, "principal not authenticated")
            tenant_id = principal.scope.tenant_id if principal.scope else None
            if not tenant_id:
                raise HTTPException(403, "tenant scope is required")
            return tenant_id

        def is_record_owner(principal: Principal, record: dict[str, Any]) -> bool:
            owner = record.get("assigned_to") or record.get("owner")
            submitter = record.get("submitted_by") or record.get("submitter")
            sentinels = {"system", "unassigned", "SYSTEM", "UNASSIGNED", None, ""}
            if owner in sentinels or submitter in sentinels:
                return True
            return principal.subject_id in (owner, submitter)

        def fingerprint(body: Any) -> str:
            return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()

        def replay(key: str | None, body: Any, tenant_id: str, actor_id: str, operation_id: str, make: Any) -> tuple[dict[str, Any], int, bool]:
            if not key:
                raise HTTPException(422, "Idempotency-Key is required")
            digest = fingerprint(body)
            composite_key = f"{tenant_id}:{actor_id}:{operation_id}:{key}"
            prior = active.replays.get(composite_key)
            if prior:
                if prior[0] != digest:
                    raise HTTPException(409, "idempotency key was used with another payload")
                return prior[1], 200, True
            result, code = make()
            active.replays[composite_key] = (digest, result, code)
            return result, code, False

        def require_version(if_match: str | None, current: int = 1) -> None:
            if if_match is None:
                raise HTTPException(428, "If-Match is required")
            supplied = if_match.strip('W/"')
            if supplied not in {str(current), f"v{current}"}:
                raise HTTPException(409, f"version conflict; current version is {current}")

        def receipt(resource: str, state: str, version: int = 1) -> dict[str, Any]:
            return {
                "transition_id": str(uuid4()), "from_state": resource, "to_state": state,
                "occurred_at": now(), "actor": "api", "version_after": version,
            }

        @router.get("/intakes", response_model=IntakePage)
        def list_intakes(
            request: Request,
            cursor: Optional[str] = None,
            page_size: int = Query(50, ge=1, le=200),
            sort: Optional[str] = None,
            status: Optional[str] = None,
            source_id: Optional[str] = None,
            match_outcome: Optional[str] = None,
            submitted_by: Optional[str] = None,
            needs_review: Optional[bool] = None,
            assigned_area_id: Optional[str] = None,
            heat_zone_id: Optional[str] = None,
            q: Optional[str] = None,
            tenant_id: str = Depends(require_actor),
        ) -> IntakePage:
            offset = 0
            if cursor:
                try:
                    offset = int(cursor)
                except ValueError:
                    raise HTTPException(400, "invalid or expired cursor")

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
                tenant_items = [v for v in tenant_items if v.get("state") == status]
            if source_id:
                tenant_items = [v for v in tenant_items if v.get("source_id") == source_id]
            if match_outcome:
                tenant_items = [v for v in tenant_items if v.get("match_outcome") == match_outcome]
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

            items = tenant_items[offset : offset + page_size]

            summaries = []
            for value in items:
                masked_val = mask_intake(principal, value)
                summaries.append(IntakeSummary(
                    intake_id=masked_val["intake_id"],
                    state=masked_val["state"],
                    intake_method=masked_val["intake_method"],
                    source_id=masked_val.get("source_id"),
                    match_outcome=masked_val.get("match_outcome"),
                    submitted_by=masked_val.get("submitted_by") or "system",
                    assigned_to=masked_val.get("assigned_to"),
                    due_at=masked_val.get("due_at"),
                    submitted_at=masked_val.get("submitted_at"),
                    updated_at=masked_val.get("updated_at"),
                    version=masked_val["version"],
                    scope=ScopeContext(**masked_val["scope"]),
                    masked_fields=masked_val.get("masked_fields") or [],
                ))

            return IntakePage(
                items=summaries,
                next_cursor=str(offset + page_size) if offset + page_size < len(tenant_items) else None,
                page_size=page_size,
                total_count=len(tenant_items),
                total_count_accuracy="exact",
                snapshot_time=now(),
                query_fingerprint=fingerprint({"page_size": page_size}),
            )

        @router.post(
            "/intakes/url",
            status_code=202,
            response_model=IntakeSubmissionReceipt,
        )
        def submit_url(
            body: UrlIntakeRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
        ) -> IntakeSubmissionReceipt:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            authorize_intake_action(
                principal,
                "submit_url",
                resource={"tenant_id": body.scope.tenant_id, "heat_zone_id": body.scope.heat_zone_id},
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
                    "checkpoint": "RETRIEVE",
                    "attempt": 0,
                    "version": 1,
                    "correlation_id": correlation_id_str,
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
            response.headers["ETag"] = f'"{val["version"]}"'

            return IntakeSubmissionReceipt(**val)

        @router.post(
            "/intake-batches",
            status_code=207,
            response_model=BatchIntakeReceipt,
        )
        def submit_batch(
            body: BatchIntakeRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
        ) -> BatchIntakeReceipt:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            authorize_intake_action(
                principal,
                "submit_csv",
                resource={"tenant_id": body.scope.tenant_id, "heat_zone_id": body.scope.heat_zone_id},
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
                            "row_index": index,
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
                            "intake_method": "BATCH",
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
                            "row_index": index,
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
            response.status_code = 200 if was_replayed else code
            return BatchIntakeReceipt(**val)

        @router.get(
            "/intakes/{intake_id}",
            response_model=IntakeDetail,
        )
        def get_intake(
            intake_id: str,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
        ) -> IntakeDetail:
            value = active.intakes.get(intake_id)
            if value is None:
                raise HTTPException(404, "intake not found")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            resource_for_auth = dict(value)
            resource_for_auth["tenant_id"] = value.get("scope", {}).get("tenant_id")
            resource_for_auth["submitter"] = value.get("submitted_by")
            resource_for_auth["owner"] = value.get("assigned_to")

            authorize_intake_action(
                principal,
                "view",
                resource=resource_for_auth,
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            response.headers["ETag"] = f'"{value["version"]}"'

            masked_val = mask_intake(principal, value)
            detail = IntakeDetail(
                intake_id=masked_val["intake_id"],
                state=masked_val["state"],
                intake_method=masked_val["intake_method"],
                source_id=masked_val.get("source_id"),
                match_outcome=masked_val.get("match_outcome"),
                submitted_by=masked_val.get("submitted_by") or "system",
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
            )
            return detail

        @router.post(
            "/intakes/{intake_id}/corrections",
            status_code=201,
            response_model=CorrectionReceipt,
        )
        def correct(
            intake_id: str,
            body: CorrectionRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> CorrectionReceipt:
            current = active.intakes.get(intake_id)
            if current is None:
                raise HTTPException(404, "intake not found")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            is_identity_affecting = body.field_path in {"providerListingId", "address", "rent", "areaPing"}
            resource_for_auth = dict(current)
            resource_for_auth["tenant_id"] = current.get("scope", {}).get("tenant_id")
            resource_for_auth["submitter"] = current.get("submitted_by")
            resource_for_auth["owner"] = current.get("assigned_to")

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
                require_version(if_match, current["version"])
                current["version"] += 1
                ts = now()
                cid = str(uuid4())
                correlation_id_str = str(uuid4())
                audit_event_id = str(uuid4())

                current["processing_history"].append({
                    "transition_id": str(uuid4()),
                    "from_state": current["state"],
                    "to_state": current["state"],
                    "occurred_at": ts,
                    "actor": actor_id,
                    "version_after": current["version"],
                })

                field_value = {
                    "field_path": body.field_path,
                    "corrected": body.corrected_value,
                    "classification": "INTERNAL",
                    "masked": False,
                }

                fields_list = current.setdefault("fields", [])
                current["fields"] = [f for f in fields_list if f.get("field_path") != body.field_path]
                current["fields"].append(field_value)

                receipt_val = {
                    "correction_id": cid,
                    "status": "APPLIED",
                    "intake_id": intake_id,
                    "version": current["version"],
                    "audit_event_id": audit_event_id,
                    "correlation_id": correlation_id_str,
                }
                return receipt_val, 201

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "proposeCorrection", make)
            response.status_code = 200 if was_replayed else code
            response.headers["Idempotency-Replayed"] = str(was_replayed).lower()
            response.headers["ETag"] = f'"{current["version"]}"'
            return CorrectionReceipt(**val)

        @router.put(
            "/intakes/{intake_id}/assignment",
            status_code=200,
            response_model=AssignmentReceipt,
        )
        def assign(
            intake_id: str,
            body: AssignmentRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> AssignmentReceipt:
            current = active.intakes.get(intake_id)
            if current is None:
                raise HTTPException(404, "intake not found")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

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

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                require_version(if_match, current["version"])
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

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "assignIntake", make)
            response.status_code = code
            response.headers["ETag"] = f'"{current["version"]}"'
            return AssignmentReceipt(**val)

        @router.post(
            "/jobs/{job_id}/retry",
            status_code=202,
            response_model=JobReceipt,
        )
        def retry_job(
            job_id: str,
            body: RetryRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> JobReceipt:
            job = active.jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "job not found")

            intake = next((v for v in active.intakes.values() if v.get("correlation_id") == job.get("correlation_id")), None)
            if intake and intake.get("scope", {}).get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            authorize_intake_action(
                principal,
                "reopen_failed",
                resource=intake,
                operator_role_id=operator_role_id,
                audit_log=active_audit_log,
                correlation_id=correlation_id,
            )

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                require_version(if_match, job["version"])
                job["attempt"] += 1
                job["version"] += 1
                job["status"] = "QUEUED"
                job["checkpoint"] = body.checkpoint

                receipt_val = {
                    "job_id": job_id,
                    "status": "QUEUED",
                    "checkpoint": body.checkpoint,
                    "attempt": job["attempt"],
                    "version": job["version"],
                    "correlation_id": job["correlation_id"],
                }
                return receipt_val, 202

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "retryJob", make)
            response.status_code = code
            return JobReceipt(**val)

        @router.get("/saved-views", response_model=List[SavedView])
        def list_saved_views(
            request: Request,
            tenant_id: str = Depends(require_actor),
        ) -> List[SavedView]:
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
                if v.get("owner_subject_id") == actor_id
            ]
            return [SavedView(**v) for v in views]

        @router.post(
            "/saved-views",
            status_code=201,
            response_model=SavedView,
        )
        def create_saved_view(
            body: SavedViewRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
        ) -> SavedView:
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
                    "visibility": body.visibility,
                    "owner_subject_id": actor_id,
                    "created_at": now(),
                    "version": 1,
                }
                active.saved_views.append(value)
                return value, 201

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "createSavedView", make)
            response.status_code = code
            return SavedView(**val)

        @router.post(
            "/intakes/{intake_id}/promotion-requests",
            status_code=202,
            response_model=PromotionDecisionReceipt,
        )
        def promote(
            intake_id: str,
            body: PromotionRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> PromotionDecisionReceipt:
            current = active.intakes.get(intake_id)
            if current is None:
                raise HTTPException(404, "intake not found")
            if current.get("scope", {}).get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            if current.get("state") != "READY":
                raise HTTPException(409, "WORKFLOW_STATE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            resource_for_auth = dict(current)
            resource_for_auth["tenant_id"] = current.get("scope", {}).get("tenant_id")
            resource_for_auth["submitter"] = current.get("submitted_by")
            resource_for_auth["owner"] = current.get("assigned_to")

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
                    "decision_type": "PROMOTION",
                    "version": 1,
                    "audit_event_id": str(uuid4()),
                    "correlation_id": str(uuid4()),
                    "tenant_id": tenant_id,
                    "proposer": actor_id,
                }
                active.promotions[did] = value
                return value, 202

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "requestCandidatePromotion", make)
            response.status_code = code
            return PromotionDecisionReceipt(**val)

        @router.get(
            "/promotion-decisions/{promotion_decision_id}",
            response_model=PromotionDecisionReceipt,
        )
        def get_promotion(
            promotion_decision_id: str,
            tenant_id: str = Depends(require_actor),
        ) -> PromotionDecisionReceipt:
            if promotion_decision_id not in active.promotions:
                raise HTTPException(404, "promotion decision not found")
            val = active.promotions[promotion_decision_id]

            if val.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            return PromotionDecisionReceipt(**val)

        @router.get(
            "/identity-decisions/{decision_id}",
            response_model=DecisionReceipt,
        )
        def get_identity(
            decision_id: str,
            tenant_id: str = Depends(require_actor),
        ) -> DecisionReceipt:
            if decision_id not in active.decisions:
                raise HTTPException(404, "identity decision not found")
            val = active.decisions[decision_id]

            if val.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            return DecisionReceipt(**val)

        @router.post(
            "/match-cases/{match_case_id}/decisions",
            status_code=201,
            response_model=DecisionReceipt,
        )
        def decide_match_case(
            match_case_id: str,
            body: MatchDecisionRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
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

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "decideMatchCase", make)
            response.status_code = 200 if was_replayed else code
            return DecisionReceipt(**val)

        @router.post(
            "/identity/merge",
            status_code=202,
            response_model=DecisionReceipt,
        )
        def merge_properties(
            body: MergeRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
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
            response.status_code = 200 if was_replayed else code
            return DecisionReceipt(**val)

        @router.post(
            "/identity/split",
            status_code=202,
            response_model=DecisionReceipt,
        )
        def split_property(
            body: SplitRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
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
            response.status_code = 200 if was_replayed else code
            return DecisionReceipt(**val)

        @router.post(
            "/identity/unmerge",
            status_code=202,
            response_model=DecisionReceipt,
        )
        def unmerge_property(
            body: UnmergeRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
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
            response.status_code = 200 if was_replayed else code
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
            status_code=200,
            response_model=TransitionReceipt,
        )
        def cancel_intake(
            intake_id: str,
            body: ReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> TransitionReceipt:
            current = active.intakes.get(intake_id)
            if current is None:
                raise HTTPException(404, "intake not found")
            if current.get("scope", {}).get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            resource_for_auth = dict(current)
            resource_for_auth["tenant_id"] = current.get("scope", {}).get("tenant_id")
            resource_for_auth["submitter"] = current.get("submitted_by")
            resource_for_auth["owner"] = current.get("assigned_to")

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
                require_version(if_match, current["version"])
                updated = generic_mutate(active.intakes, intake_id, "CANCELLED", actor_id)
                tr = receipt("SUBMITTED", "CANCELLED", updated["version"])
                return tr, 200

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "cancelIntake", make)
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'"{current["version"]}"'
            return TransitionReceipt(**val)

        @router.post(
            "/intakes/{intake_id}/actions/quarantine",
            status_code=200,
            response_model=TransitionReceipt,
        )
        def quarantine_intake(
            intake_id: str,
            body: RiskReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> TransitionReceipt:
            current = active.intakes.get(intake_id)
            if current is None:
                raise HTTPException(404, "intake not found")
            if current.get("scope", {}).get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_staff = (principal.has_role(Role.EXPANSION_USER) or operator_role_id in (
                "expansion-staff", "expansionStaff", "expansion-user", "expansion_user"
            )) and not is_manager
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")
            is_privacy = principal.has_role(Role.FINANCE_LEGAL) or operator_role_id in ("privacy-officer", "privacyOfficer")

            if not (is_manager or is_staff or is_steward or is_privacy):
                raise HTTPException(403, "ROLE_DENIED")

            if not body.reason or not body.reason.strip():
                raise HTTPException(422, "RISK_ACKNOWLEDGEMENT_REQUIRED: risk summary is required")
            if not body.risk_acknowledged:
                raise HTTPException(422, "RISK_ACKNOWLEDGEMENT_REQUIRED: risk acknowledgement is required")

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                require_version(if_match, current["version"])
                from_state = current.get("state", "SUBMITTED")
                updated = generic_mutate(active.intakes, intake_id, "QUARANTINED", actor_id)
                tr = receipt(from_state, "QUARANTINED", updated["version"])
                return tr, 200

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "quarantineIntake", make)
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'"{current["version"]}"'
            return TransitionReceipt(**val)

        @router.post(
            "/intakes/{intake_id}/actions/reopen",
            status_code=200,
            response_model=TransitionReceipt,
        )
        def reopen_intake(
            intake_id: str,
            body: RiskReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> TransitionReceipt:
            current = active.intakes.get(intake_id)
            if current is None:
                raise HTTPException(404, "intake not found")
            if current.get("scope", {}).get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")

            resource_for_auth = dict(current)
            resource_for_auth["tenant_id"] = current.get("scope", {}).get("tenant_id")
            resource_for_auth["submitter"] = current.get("submitted_by")
            resource_for_auth["owner"] = current.get("assigned_to")

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
                require_version(if_match, current["version"])
                from_state = current.get("state", "QUARANTINED")
                updated = generic_mutate(active.intakes, intake_id, "SUBMITTED", actor_id)
                tr = receipt(from_state, "SUBMITTED", updated["version"])
                return tr, 200

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "reopenIntake", make)
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'"{current["version"]}"'
            return TransitionReceipt(**val)

        @router.post(
            "/assignments/{assignment_id}/actions/claim",
            status_code=200,
            response_model=AssignmentReceipt,
        )
        def claim_assignment(
            assignment_id: str,
            body: ReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> AssignmentReceipt:
            current = active.assignments.get(assignment_id)
            if current is None:
                raise HTTPException(404, "assignment not found")

            assignment_tenant = current.get("tenant_id")
            if not assignment_tenant:
                intake = active.intakes.get(current.get("intake_id", ""))
                if intake:
                    assignment_tenant = intake.get("scope", {}).get("tenant_id")
            if assignment_tenant and assignment_tenant != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            actor_id = principal.subject_id

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
                require_version(if_match, current["version"])
                updated = generic_mutate(active.assignments, assignment_id, "CLAIMED", actor_id)
                return updated, 200

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "claimAssignment", make)
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'"{current["version"]}"'
            return AssignmentReceipt(**val)

        @router.post(
            "/assignments/{assignment_id}/actions/transfer",
            status_code=200,
            response_model=AssignmentReceipt,
        )
        def transfer_assignment(
            assignment_id: str,
            body: AssignmentTransferRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> AssignmentReceipt:
            current = active.assignments.get(assignment_id)
            if current is None:
                raise HTTPException(404, "assignment not found")

            assignment_tenant = current.get("tenant_id")
            if not assignment_tenant:
                intake = active.intakes.get(current.get("intake_id", ""))
                if intake:
                    assignment_tenant = intake.get("scope", {}).get("tenant_id")
            if assignment_tenant and assignment_tenant != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            actor_id = principal.subject_id

            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_staff = (principal.has_role(Role.EXPANSION_USER) or operator_role_id in (
                "expansion-staff", "expansionStaff", "expansion-user", "expansion_user"
            )) and not is_manager
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")

            if not (is_manager or is_staff or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            if is_staff and current.get("owner_subject_id") != actor_id:
                raise HTTPException(403, "OWNERSHIP_REQUIRED")

            def make() -> tuple[dict[str, Any], int]:
                require_version(if_match, current["version"])
                current["owner_subject_id"] = body.target_owner_subject_id
                updated = generic_mutate(active.assignments, assignment_id, "ASSIGNED", actor_id)
                return updated, 200

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "transferAssignment", make)
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'"{current["version"]}"'
            return AssignmentReceipt(**val)

        @router.post(
            "/assignments/{assignment_id}/actions/complete",
            status_code=200,
            response_model=AssignmentReceipt,
        )
        def complete_assignment(
            assignment_id: str,
            body: ReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> AssignmentReceipt:
            current = active.assignments.get(assignment_id)
            if current is None:
                raise HTTPException(404, "assignment not found")

            assignment_tenant = current.get("tenant_id")
            if not assignment_tenant:
                intake = active.intakes.get(current.get("intake_id", ""))
                if intake:
                    assignment_tenant = intake.get("scope", {}).get("tenant_id")
            if assignment_tenant and assignment_tenant != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            actor_id = principal.subject_id

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
                require_version(if_match, current["version"])
                updated = generic_mutate(active.assignments, assignment_id, "COMPLETED", actor_id)
                return updated, 200

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "completeAssignment", make)
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'"{current["version"]}"'
            return AssignmentReceipt(**val)

        @router.post(
            "/sla-instances/{sla_instance_id}/actions/pause",
            status_code=200,
            response_model=SlaReceipt,
        )
        def pause_sla(
            sla_instance_id: str,
            body: SlaPauseRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> SlaReceipt:
            current = active.slas.get(sla_instance_id)
            if current is not None:
                if current.get("tenant_id") != tenant_id:
                    raise HTTPException(403, "TENANT_SCOPE_DENIED")
            else:
                current = {
                    "sla_instance_id": sla_instance_id,
                    "state": "ACTIVE",
                    "due_at": now(),
                    "paused_duration_seconds": 0,
                    "version": 1,
                    "audit_event_id": str(uuid4()),
                    "correlation_id": str(uuid4()),
                    "tenant_id": tenant_id,
                }
                active.slas[sla_instance_id] = current

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")
            if not (is_manager or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                require_version(if_match, current["version"])
                updated = generic_mutate(active.slas, sla_instance_id, "PAUSED", actor_id)
                updated["active_pause_interval_id"] = str(uuid4())
                return updated, 200

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "pauseSla", make)
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'"{current["version"]}"'
            return SlaReceipt(**val)

        @router.post(
            "/sla-instances/{sla_instance_id}/actions/resume",
            status_code=200,
            response_model=SlaReceipt,
        )
        def resume_sla(
            sla_instance_id: str,
            body: ReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> SlaReceipt:
            current = active.slas.get(sla_instance_id)
            if current is None:
                raise HTTPException(404, "SLA instance not found")
            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
                "expansion-manager", "expansionManager", "site-reviewer", "siteReviewer", "executive"
            )
            is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in ("data-steward", "dataSteward")
            if not (is_manager or is_steward):
                raise HTTPException(403, "ROLE_DENIED")

            actor_id = principal.subject_id

            def make() -> tuple[dict[str, Any], int]:
                require_version(if_match, current["version"])
                updated = generic_mutate(active.slas, sla_instance_id, "ACTIVE", actor_id)
                updated["active_pause_interval_id"] = None
                return updated, 200

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "resumeSla", make)
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'"{current["version"]}"'
            return SlaReceipt(**val)

        @router.post(
            "/promotion-decisions/{promotion_decision_id}/actions/review",
            status_code=200,
            response_model=PromotionDecisionReceipt,
        )
        def review_promotion(
            promotion_decision_id: str,
            body: ReviewDecisionRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> PromotionDecisionReceipt:
            current = active.promotions.get(promotion_decision_id)
            if current is None:
                raise HTTPException(404, "promotion decision not found")

            intake = active.intakes.get(current["intake_id"])
            if intake and intake.get("scope", {}).get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")
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
                require_version(if_match, current["version"])
                to_state = "APPROVED" if body.decision in {"approve", "APPROVE"} else "REJECTED"
                updated = generic_mutate(active.promotions, promotion_decision_id, to_state, actor_id)
                updated["reviewer_subject_id"] = actor_id
                return updated, 200

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "reviewPromotionDecision", make)
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'"{current["version"]}"'
            return PromotionDecisionReceipt(**val)

        @router.post(
            "/identity-decisions/{decision_id}/actions/review",
            status_code=200,
            response_model=DecisionReceipt,
        )
        def review_identity(
            decision_id: str,
            body: ReviewDecisionRequest,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> DecisionReceipt:
            current = active.decisions.get(decision_id)
            if current is None:
                raise HTTPException(404, "identity decision not found")

            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")
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
                require_version(if_match, current["version"])
                to_state = "APPROVED" if body.decision in {"approve", "APPROVE"} else "REJECTED"
                updated = generic_mutate(active.decisions, decision_id, to_state, actor_id)
                return updated, 200

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "reviewIdentityDecision", make)
            response.status_code = 200 if was_replayed else code
            response.headers["ETag"] = f'"{current["version"]}"'
            return DecisionReceipt(**val)

        @router.post(
            "/identity-decisions/{decision_id}/actions/reverse",
            status_code=202,
            response_model=DecisionReceipt,
        )
        def reverse_identity(
            decision_id: str,
            body: RiskReasonCommand,
            request: Request,
            response: Response,
            tenant_id: str = Depends(require_actor),
            key: str = Header(..., alias="Idempotency-Key"),
            if_match: str = Header(..., alias="If-Match"),
        ) -> DecisionReceipt:
            current = active.decisions.get(decision_id)
            if current is None:
                raise HTTPException(404, "identity decision not found")

            if current.get("tenant_id") != tenant_id:
                raise HTTPException(403, "TENANT_SCOPE_DENIED")

            principal = get_principal(request)
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
                require_version(if_match, current["version"])
                updated = generic_mutate(active.decisions, decision_id, "REVERSAL_PENDING", actor_id)
                return updated, 202

            val, code, was_replayed = replay(key, body.model_dump(), tenant_id, actor_id, "requestIdentityDecisionReversal", make)
            response.status_code = code
            response.headers["ETag"] = f'"{current["version"]}"'
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
