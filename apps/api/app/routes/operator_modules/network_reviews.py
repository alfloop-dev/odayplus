"""Operator Console Network Review decision routes (ODP-OC-R4-007).

Owns:
- GET  /operator/network-reviews
- POST /operator/network-reviews/reset
- POST /operator/network-reviews/{review_id}/decide

The decide endpoint applies one review decision atomically across Candidate,
Review, Approval, Decision, and Audit records (``NetworkReviewService``). The
decision-authority rule is enforced at two layers:

- HTTP guard: the decide route requires ``sitescore`` + ``Action.APPROVE``,
  which is granted to Site Reviewer / Executive but **not** to Expansion — so
  an Expansion caller fails closed with 403 before the handler runs.
- Service guard: a defense-in-depth allowlist raises 403 if a mis-scoped actor
  role reaches the service.

Reason / condition / override policy violations surface as 422, an
already-decided review as 409, and an unknown review as 404. They compose
through apps.api.app.routes.operator.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from apps.api.app.routes._common import reset_allowed_guard
from apps.api.app.routes.operator_modules.live_service import resolve_service
from modules.opsboard.application.network_read_scope import project_review_snapshot
from modules.opsboard.application.network_reviews import (
    DECISION_ACTIONS,
    NetworkReviewConflict,
    NetworkReviewNotFound,
    NetworkReviewPolicyError,
    NetworkReviewRoleError,
    NetworkReviewRuntimeUnavailable,
    NetworkReviewService,
)
from shared.auth import Role


class ReviewDecisionPayload(BaseModel):
    """POST /operator/network-reviews/{review_id}/decide."""

    model_config = ConfigDict(extra="allow")

    decision: str
    reason: str = ""
    conditions: str | None = None
    requiredData: list[str] = []
    overrideAck: bool = False
    actorRoleId: str = "siteReviewer"
    actorName: str | None = None

    @field_validator("decision")
    @classmethod
    def valid_decision(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in DECISION_ACTIONS:
            raise ValueError(f"decision must be one of {', '.join(DECISION_ACTIONS)}")
        return normalized


class ReviewSubmissionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedReportId: str = Field(min_length=1)
    expectedReportVersion: StrictInt = Field(gt=0)
    expectedLatestReportId: str = Field(min_length=1)
    expectedLatestReportVersion: StrictInt = Field(gt=0)
    decisionId: str | None
    expectedDecisionVersion: StrictInt | None = Field(default=None, gt=0)
    expectedCandidateReviewId: str | None
    expectedCandidateState: str = Field(min_length=1)
    expectedCandidateDigest: str = Field(pattern=r"^[0-9a-f]{64}$")
    submissionSummary: str = Field(min_length=1)
    reviewerGroupId: str = Field(min_length=1)
    expectedActorSubjectId: str = Field(min_length=1)
    expectedTenantId: str = Field(min_length=1)

    @field_validator("submissionSummary")
    @classmethod
    def substantive_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("submissionSummary must be explicitly recorded")
        return value


def create_network_review_sub_router(
    service: NetworkReviewService,
    *,
    require_view_permission_fn: Callable[..., Any],
    require_decide_permission_fn: Callable[..., Any],
    require_submit_permission_fn: Callable[..., Any] | None = None,
    service_resolver: Callable[[Request], Any] | None = None,
    audit_log: Any = None,
    allow_reset: bool = True,
    read_scope_snapshot_fn: Callable[[Request], dict[str, Any]] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/network-reviews")

    require_reset_allowed = reset_allowed_guard(
        allow_reset=allow_reset,
        resource_label="network review",
    )

    @router.get("", dependencies=[Depends(require_view_permission_fn)])
    @router.get("/", dependencies=[Depends(require_view_permission_fn)])
    def get_network_reviews(
        request: Request,
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            snapshot = resolve_service(request, service, service_resolver).snapshot(
                correlation_id=x_correlation_id
            )
            principal = request.state.operator_principal
            if principal.has_role(Role.OPERATOR_VIEWER):
                return project_review_snapshot(
                    principal, snapshot,
                    read_scope_snapshot_fn(request) if read_scope_snapshot_fn else {},
                )
            return snapshot
        except NetworkReviewRuntimeUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=exc.to_detail(),
            ) from exc

    def submission_error(exc: Exception) -> HTTPException:
        code = (404 if isinstance(exc, NetworkReviewNotFound) else
                403 if isinstance(exc, NetworkReviewRoleError) else
                503 if isinstance(exc, NetworkReviewRuntimeUnavailable) else 409)
        return HTTPException(code, detail=str(exc))

    @router.get("/candidates/{candidate_id}/submission-context",
                dependencies=[Depends(require_submit_permission_fn or require_decide_permission_fn)])
    def submission_context(candidate_id: str, request: Request, decisionId: str | None = None):
        try:
            return resolve_service(request, service, service_resolver).submission_context(
                candidate_id=candidate_id, principal=request.state.operator_principal, decision_id=decisionId)
        except (NetworkReviewNotFound, NetworkReviewRoleError, NetworkReviewConflict, NetworkReviewRuntimeUnavailable) as exc:
            raise submission_error(exc) from exc

    @router.post("/candidates/{candidate_id}/submit",
                 dependencies=[Depends(require_submit_permission_fn or require_decide_permission_fn)])
    def submit_review(candidate_id: str, body: ReviewSubmissionPayload, request: Request,
                      idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                      correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
                      if_actor_match: str | None = Header(default=None, alias="If-Actor-Match")):
        principal = request.state.operator_principal
        if if_actor_match != principal.subject_id or body.expectedActorSubjectId != principal.subject_id:
            raise HTTPException(409, detail="ACTOR_CONTEXT_CONFLICT")
        if body.expectedTenantId != principal.tenant_id:
            raise HTTPException(409, detail="TENANT_CONTEXT_CONFLICT")
        target = body.model_dump(exclude={"expectedActorSubjectId", "expectedTenantId", "submissionSummary", "reviewerGroupId"})
        try:
            return resolve_service(request, service, service_resolver).submit_review(
                candidate_id=candidate_id, principal=principal, target=target,
                idempotency_key=idempotency_key, correlation_id=correlation_id,
                submission_summary=body.submissionSummary, reviewer_group_id=body.reviewerGroupId)
        except (NetworkReviewNotFound, NetworkReviewRoleError, NetworkReviewConflict, NetworkReviewRuntimeUnavailable) as exc:
            raise submission_error(exc) from exc

    @router.post(
        "/reset",
        dependencies=[
            Depends(require_decide_permission_fn),
            Depends(require_reset_allowed),
        ],
    )
    def reset_network_reviews(request: Request) -> dict[str, Any]:
        return resolve_service(request, service, service_resolver).reset()

    @router.post(
        "/{review_id}/decide",
        dependencies=[Depends(require_decide_permission_fn)],
    )
    def decide_review(
        review_id: str,
        body: ReviewDecisionPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        # The APPROVE guard establishes this principal before the handler runs.
        # Persona/display fields in the body must not manufacture decision
        # authority or impersonate the actor in the five synchronized records.
        principal = request.state.operator_principal
        if Role.SITE_REVIEWER in principal.roles:
            actor_role_id = Role.SITE_REVIEWER.value
        elif Role.EXECUTIVE in principal.roles:
            actor_role_id = Role.EXECUTIVE.value
        else:  # defense in depth if the HTTP permission matrix ever changes
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="reviewer role required")
        try:
            return resolve_service(request, service, service_resolver).decide_review(
                review_id=review_id,
                decision=body.decision,
                reason=body.reason,
                conditions=body.conditions,
                required_data=body.requiredData,
                override_ack=body.overrideAck,
                actor_role_id=actor_role_id,
                actor_name=principal.subject_id,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
                principal=principal,
            )
        except NetworkReviewNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkReviewRoleError as exc:
            if audit_log is not None:
                from apps.api.oday_api.security.dependencies import record_review_owner_denial
                record_review_owner_denial(request, audit_log=audit_log, resource_id=review_id, reason=str(exc))
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except NetworkReviewConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkReviewPolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        except NetworkReviewRuntimeUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=exc.to_detail(),
            ) from exc

    return router


__all__ = ["create_network_review_sub_router"]
