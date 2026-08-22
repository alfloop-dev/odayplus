"""Market Survey API Routes (ODP-SURVEY-001).

Contract: `odayplus.survey-workflow.v2`.
Release: `consumer-b.json` (ODayPlus Consumer).

Exposes field survey assignment, reviewer separation governance, observation ingestion
as unpromoted evidence snapshots, corrections, SLA sweeps, and promotion to canonical truth.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from modules.market_survey.application.survey_service import MarketSurveyService
from modules.market_survey.domain.models import (
    SURVEY_WORKFLOW_CONTRACT,
    SURVEY_WORKFLOW_VERSION,
    SurveyAuthorizationError,
    SurveyDomainError,
    SurveyNotFoundError,
    SurveyStateConflictError,
    SurveyValidationError,
)
from modules.market_survey.infrastructure.repositories import (
    InMemorySurveyRepository,
    SurveyRepository,
)
from shared.audit import InMemoryAuditLog

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:
    from apps.api.app.routes._common import resolve_tenant_id

    class CreateAssignmentPayload(BaseModel):
        campaign_id: str = Field(min_length=1)
        target_entity_id: str = Field(min_length=1)
        target_entity_kind: str = "CANDIDATE_SITE"
        survey_type: str = "PHYSICAL_FEASIBILITY"
        expires_at: str
        created_by: str = Field(min_length=1)
        assigned_to: str | None = None
        instructions: dict[str, Any] = Field(default_factory=dict)
        metadata: dict[str, Any] = Field(default_factory=dict)
        idempotency_key: str | None = None

    class AssignSurveyPayload(BaseModel):
        assigned_to: str = Field(min_length=1)
        assigned_by: str = Field(min_length=1)
        expires_at: str | None = None

    class ClaimAssignmentPayload(BaseModel):
        actor_id: str = Field(min_length=1)

    class StartSurveyPayload(BaseModel):
        actor_id: str = Field(min_length=1)

    class SubmitSurveyPayload(BaseModel):
        actor_id: str = Field(min_length=1)
        location: dict[str, Any]
        attributes: dict[str, Any] = Field(default_factory=dict)
        media_attachments: list[dict[str, Any]] = Field(default_factory=list)
        surveyed_at: str | None = None
        confidence: float | None = 1.0
        metadata: dict[str, Any] = Field(default_factory=dict)

    class CancelAssignmentPayload(BaseModel):
        actor_id: str = Field(min_length=1)
        reason: str = ""

    class IngestObservationPayload(BaseModel):
        observation: dict[str, Any]

    class IngestDocumentPayload(BaseModel):
        document: dict[str, Any]

    class ReviewSurveyPayload(BaseModel):
        decision: str = "APPROVED"
        reviewer_id: str = Field(min_length=1)
        review_comment: str | None = None
        review_checklist: dict[str, Any] = Field(default_factory=dict)
        conditions: list[str] = Field(default_factory=list)

    class CorrectSurveyPayload(BaseModel):
        corrected_by: str = Field(min_length=1)
        reason: str = Field(min_length=1)
        delta_attributes: dict[str, Any] = Field(default_factory=dict)
        location: dict[str, Any] | None = None
        media_attachments: list[dict[str, Any]] | None = None
        lifecycle_kind: str = "CORRECTION"

    class RetractSurveyPayload(BaseModel):
        retracted_by: str = Field(min_length=1)
        reason: str = Field(min_length=1)

    class PromoteSurveyPayload(BaseModel):
        promoted_by: str = Field(min_length=1)
        target_entity_type: str = "candidate_site"
        target_entity_ref: str | None = None
        promotion_payload: dict[str, Any] = Field(default_factory=dict)

    class SweepExpiryPayload(BaseModel):
        now: str | None = None

    def _handle_domain_error(exc: Exception) -> HTTPException:
        if isinstance(exc, SurveyAuthorizationError):
            return HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": exc.code.value, "message": exc.message, "details": exc.details},
            )
        if isinstance(exc, SurveyNotFoundError):
            return HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": exc.code.value, "message": exc.message, "details": exc.details},
            )
        if isinstance(exc, SurveyStateConflictError):
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": exc.code.value, "message": exc.message, "details": exc.details},
            )
        if isinstance(exc, SurveyValidationError):
            return HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": exc.code.value, "message": exc.message, "details": exc.details},
            )
        if isinstance(exc, SurveyDomainError):
            return HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": exc.code.value, "message": exc.message, "details": exc.details},
            )
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_ERROR", "message": str(exc)},
        )

    def create_market_survey_router(
        *,
        service: MarketSurveyService | None = None,
        repository: SurveyRepository | None = None,
        audit_log: InMemoryAuditLog | None = None,
    ) -> APIRouter:
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        active_audit_log = audit_log or InMemoryAuditLog()
        active_repo = repository or InMemorySurveyRepository()
        active_service = service or MarketSurveyService(repository=active_repo, audit_log=active_audit_log)
        authz_engine = build_engine(audit_log=active_audit_log)

        router = APIRouter(prefix="/market-survey", tags=["market-survey"])

        view_guard = Depends(require_permission("listing", Action.VIEW, engine=authz_engine))
        create_guard = Depends(require_permission("listing", Action.CREATE, engine=authz_engine))
        update_guard = Depends(require_permission("listing", Action.UPDATE, engine=authz_engine))

        # -------------------------------------------------------------------
        # Assignment Endpoints
        # -------------------------------------------------------------------

        @router.post("/assignments", status_code=status.HTTP_201_CREATED, dependencies=[create_guard])
        def create_assignment(
            body: CreateAssignmentPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            corr_id = getattr(request.state, "correlation_id", None)
            try:
                assignment = active_service.create_assignment(
                    tenant_id=tid,
                    campaign_id=body.campaign_id,
                    target_entity_id=body.target_entity_id,
                    target_entity_kind=body.target_entity_kind,
                    survey_type=body.survey_type,
                    expires_at=body.expires_at,
                    created_by=body.created_by,
                    assigned_to=body.assigned_to,
                    instructions=body.instructions,
                    metadata=body.metadata,
                    correlation_id=corr_id,
                )
                payload = assignment.to_dict()
                payload["contract"] = SURVEY_WORKFLOW_CONTRACT
                payload["version"] = SURVEY_WORKFLOW_VERSION
                return payload
            except Exception as exc:
                raise _handle_domain_error(exc) from exc

        @router.get("/assignments", dependencies=[view_guard])
        def list_assignments(
            request: Request,
            campaign_id: str | None = None,
            status: str | None = None,
            assigned_to: str | None = None,
            target_entity_id: str | None = None,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            items = active_service.list_assignments(
                tenant_id=tid,
                campaign_id=campaign_id,
                status=status,
                assigned_to=assigned_to,
                target_entity_id=target_entity_id,
            )
            return {
                "items": [item.to_dict() for item in items],
                "count": len(items),
                "contract": SURVEY_WORKFLOW_CONTRACT,
            }

        @router.get("/assignments/{assignment_id}", dependencies=[view_guard])
        def get_assignment(assignment_id: str, request: Request) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            assignment = active_service.get_assignment(assignment_id, tenant_id=tid)
            if assignment is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
            return assignment.to_dict()

        @router.post("/assignments/{assignment_id}/assign", dependencies=[update_guard])
        def assign_survey(
            assignment_id: str,
            body: AssignSurveyPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            corr_id = getattr(request.state, "correlation_id", None)
            try:
                res = active_service.assign_survey(
                    assignment_id,
                    assigned_to=body.assigned_to,
                    assigned_by=body.assigned_by,
                    expires_at=body.expires_at,
                    tenant_id=tid,
                    correlation_id=corr_id,
                )
                return res.to_dict()
            except Exception as exc:
                raise _handle_domain_error(exc) from exc

        @router.post("/assignments/{assignment_id}/claim", dependencies=[update_guard])
        def claim_assignment(
            assignment_id: str,
            body: ClaimAssignmentPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            corr_id = getattr(request.state, "correlation_id", None)
            try:
                res = active_service.claim_assignment(
                    assignment_id,
                    actor_id=body.actor_id,
                    tenant_id=tid,
                    correlation_id=corr_id,
                )
                return res.to_dict()
            except Exception as exc:
                raise _handle_domain_error(exc) from exc

        @router.post("/assignments/{assignment_id}/start", dependencies=[update_guard])
        def start_survey(
            assignment_id: str,
            body: StartSurveyPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            corr_id = getattr(request.state, "correlation_id", None)
            try:
                res = active_service.start_survey(
                    assignment_id,
                    actor_id=body.actor_id,
                    tenant_id=tid,
                    correlation_id=corr_id,
                )
                return res.to_dict()
            except Exception as exc:
                raise _handle_domain_error(exc) from exc

        @router.post("/assignments/{assignment_id}/submit", dependencies=[update_guard])
        def submit_survey(
            assignment_id: str,
            body: SubmitSurveyPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            corr_id = getattr(request.state, "correlation_id", None)
            try:
                asgn, evidence = active_service.submit_survey(
                    assignment_id,
                    actor_id=body.actor_id,
                    location=body.location,
                    attributes=body.attributes,
                    media_attachments=body.media_attachments,
                    surveyed_at=body.surveyed_at,
                    confidence=body.confidence,
                    metadata=body.metadata,
                    tenant_id=tid,
                    correlation_id=corr_id,
                )
                return {
                    "assignment": asgn.to_dict(),
                    "evidence": evidence.to_dict(),
                    "status": "SUBMITTED",
                    "contract": SURVEY_WORKFLOW_CONTRACT,
                }
            except Exception as exc:
                raise _handle_domain_error(exc) from exc

        @router.post("/assignments/{assignment_id}/cancel", dependencies=[update_guard])
        def cancel_assignment(
            assignment_id: str,
            body: CancelAssignmentPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            corr_id = getattr(request.state, "correlation_id", None)
            try:
                res = active_service.cancel_assignment(
                    assignment_id,
                    actor_id=body.actor_id,
                    reason=body.reason,
                    tenant_id=tid,
                    correlation_id=corr_id,
                )
                return res.to_dict()
            except Exception as exc:
                raise _handle_domain_error(exc) from exc

        @router.post("/assignments/sweep-expiry", dependencies=[update_guard])
        def sweep_expiry(request: Request, body: SweepExpiryPayload | None = None) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            now_dt = None
            if body and body.now:
                now_dt = datetime.fromisoformat(body.now.replace("Z", "+00:00"))
            from modules.market_survey.workers.expiry_worker import run_survey_expiry_sweep
            res = run_survey_expiry_sweep(active_service, tenant_id=tid, now=now_dt)
            return res.to_dict()

        # -------------------------------------------------------------------
        # Platform Ingestion Endpoints (emgi.field-survey.v1 -> evidence)
        # -------------------------------------------------------------------

        @router.post("/ingest-platform-evidence", status_code=status.HTTP_201_CREATED, dependencies=[create_guard])
        def ingest_platform_evidence(
            body: IngestObservationPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            corr_id = getattr(request.state, "correlation_id", None)
            try:
                evidence = active_service.ingest_platform_observation(
                    body.observation,
                    tenant_id=tid,
                    correlation_id=corr_id,
                )
                return {
                    "evidence": evidence.to_dict(),
                    "is_ground_truth": False,
                    "review_status": evidence.review_status.value,
                    "contract": SURVEY_WORKFLOW_CONTRACT,
                }
            except Exception as exc:
                raise _handle_domain_error(exc) from exc

        @router.post("/ingest-platform-document", status_code=status.HTTP_201_CREATED, dependencies=[create_guard])
        def ingest_platform_document(
            body: IngestDocumentPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            corr_id = getattr(request.state, "correlation_id", None)
            try:
                items = active_service.ingest_platform_document(
                    body.document,
                    tenant_id=tid,
                    correlation_id=corr_id,
                )
                return {
                    "items": [item.to_dict() for item in items],
                    "count": len(items),
                    "is_ground_truth": False,
                    "contract": SURVEY_WORKFLOW_CONTRACT,
                }
            except Exception as exc:
                raise _handle_domain_error(exc) from exc

        # -------------------------------------------------------------------
        # Survey Evidence, Review & Promotion Endpoints
        # -------------------------------------------------------------------

        @router.get("/surveys", dependencies=[view_guard])
        def list_surveys(
            request: Request,
            campaign_id: str | None = None,
            target_entity_id: str | None = None,
            review_status: str | None = None,
            promotion_status: str | None = None,
            include_superseded: bool = True,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            surveys = active_service.list_surveys(
                tenant_id=tid,
                campaign_id=campaign_id,
                target_entity_id=target_entity_id,
                review_status=review_status,
                promotion_status=promotion_status,
                include_superseded=include_superseded,
            )
            return {
                "items": [s.to_dict() for s in surveys],
                "count": len(surveys),
                "contract": SURVEY_WORKFLOW_CONTRACT,
            }

        @router.get("/surveys/{survey_id}", dependencies=[view_guard])
        def get_survey(survey_id: str, request: Request) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            survey = active_service.get_survey(survey_id, tenant_id=tid)
            if survey is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Survey evidence not found")
            return survey.to_dict()

        @router.get("/surveys/{survey_id}/lineage", dependencies=[view_guard])
        def get_survey_lineage(survey_id: str, request: Request) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            try:
                return active_service.get_survey_lineage(survey_id, tenant_id=tid)
            except Exception as exc:
                raise _handle_domain_error(exc) from exc

        @router.post("/surveys/{survey_id}/review", dependencies=[update_guard])
        def review_survey(
            survey_id: str,
            body: ReviewSurveyPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            corr_id = getattr(request.state, "correlation_id", None)
            try:
                res = active_service.review_survey(
                    survey_id,
                    decision=body.decision,
                    reviewer_id=body.reviewer_id,
                    review_comment=body.review_comment,
                    review_checklist=body.review_checklist,
                    conditions=body.conditions,
                    tenant_id=tid,
                    correlation_id=corr_id,
                )
                return {
                    "survey": res.to_dict(),
                    "review_status": res.review_status.value,
                    "contract": SURVEY_WORKFLOW_CONTRACT,
                }
            except Exception as exc:
                raise _handle_domain_error(exc) from exc

        @router.post("/surveys/{survey_id}/correct", status_code=status.HTTP_201_CREATED, dependencies=[update_guard])
        def correct_survey(
            survey_id: str,
            body: CorrectSurveyPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            corr_id = getattr(request.state, "correlation_id", None)
            try:
                new_ev, corr = active_service.correct_survey(
                    survey_id,
                    corrected_by=body.corrected_by,
                    reason=body.reason,
                    delta_attributes=body.delta_attributes,
                    location=body.location,
                    media_attachments=body.media_attachments,
                    tenant_id=tid,
                    correlation_id=corr_id,
                )
                return {
                    "new_evidence": new_ev.to_dict(),
                    "correction": corr.to_dict(),
                    "contract": SURVEY_WORKFLOW_CONTRACT,
                }
            except Exception as exc:
                raise _handle_domain_error(exc) from exc

        @router.post("/surveys/{survey_id}/retract", dependencies=[update_guard])
        def retract_survey(
            survey_id: str,
            body: RetractSurveyPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            corr_id = getattr(request.state, "correlation_id", None)
            try:
                res = active_service.retract_survey(
                    survey_id,
                    retracted_by=body.retracted_by,
                    reason=body.reason,
                    tenant_id=tid,
                    correlation_id=corr_id,
                )
                return res.to_dict()
            except Exception as exc:
                raise _handle_domain_error(exc) from exc

        @router.post("/surveys/{survey_id}/promote", dependencies=[update_guard])
        def promote_survey(
            survey_id: str,
            body: PromoteSurveyPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            corr_id = getattr(request.state, "correlation_id", None)
            try:
                record = active_service.promote_survey(
                    survey_id,
                    promoted_by=body.promoted_by,
                    target_entity_type=body.target_entity_type,
                    target_entity_ref=body.target_entity_ref,
                    promotion_payload=body.promotion_payload,
                    tenant_id=tid,
                    correlation_id=corr_id,
                )
                return {
                    "promotion": record.to_dict(),
                    "status": "PROMOTED",
                    "contract": SURVEY_WORKFLOW_CONTRACT,
                }
            except Exception as exc:
                raise _handle_domain_error(exc) from exc

        return router

    __all__ = [
        "AssignSurveyPayload",
        "CancelAssignmentPayload",
        "ClaimAssignmentPayload",
        "CorrectSurveyPayload",
        "CreateAssignmentPayload",
        "IngestDocumentPayload",
        "IngestObservationPayload",
        "PromoteSurveyPayload",
        "RetractSurveyPayload",
        "ReviewSurveyPayload",
        "StartSurveyPayload",
        "SubmitSurveyPayload",
        "SweepExpiryPayload",
        "create_market_survey_router",
    ]
