"""Operator Console Network SiteScore scoring routes.

Owns:
- GET  /operator/network-scoring
- POST /operator/network-scoring/reset
- POST /operator/network-scoring/candidates/{id}/score
- POST /operator/network-scoring/score            (batch)
- POST /operator/network-scoring/compare

The routes wrap NetworkScoringService and keep write auth/idempotency headers
at the HTTP boundary. Missing-data candidates are rejected here with 422 —
the data-completeness gate is enforced server-side, not only in the UI. They
compose through apps.api.app.routes.operator.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, StrictInt

from apps.api.app.routes._common import reset_allowed_guard
from apps.api.app.routes.operator_modules.live_service import resolve_service
from models.shared_ml.production_runtime import ProductionModelRuntimeError
from modules.listing.application.intake_score_delivery import (
    ScoreDeliveryConflict,
    ScoreDeliveryDenied,
    ScoreDeliveryUnavailable,
    ScoreDeliveryValidationError,
)
from modules.opsboard.application.network_scoring import (
    NetworkScoringGateError,
    NetworkScoringNotFound,
    NetworkScoringRuntimeUnavailable,
    NetworkScoringService,
)
from shared.auth import Role


def execution_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ScoreDeliveryConflict):
        code = 409
    elif isinstance(exc, ScoreDeliveryDenied):
        code = 403
    elif isinstance(exc, (ScoreDeliveryValidationError, ValueError)):
        code = 422
    elif isinstance(exc, (ScoreDeliveryUnavailable, ProductionModelRuntimeError)):
        code = 503
    else:
        code = 503
    return HTTPException(code, {"code": getattr(exc, "code", str(exc))})


class NetworkScoringActorPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    actorRoleId: str = "expansionManager"
    actorName: str | None = None
    expectedReportId: str | None = None
    expectedReportVersion: StrictInt | None = None
    expectedJobVersion: StrictInt | None = None
    reason: str | None = None
    expectedActorSubjectId: str | None = None
    expectedTenantId: str | None = None


class ScoreExecutionRetryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedVersion: StrictInt
    expectedJobVersion: StrictInt
    reason: str
    expectedActorSubjectId: str | None = None
    expectedTenantId: str | None = None


class NetworkScoringBatchPayload(NetworkScoringActorPayload):
    candidateIds: list[str] | None = None


class NetworkScoringComparePayload(NetworkScoringActorPayload):
    candidateIds: list[str]


def create_network_scoring_sub_router(
    service: NetworkScoringService,
    *,
    require_view_permission_fn: Callable[..., Any],
    require_write_permission_fn: Callable[..., Any],
    service_resolver: Callable[[Request], Any] | None = None,
    execution_service: Any = None,
    allow_reset: bool = True,
    read_scope_snapshot_fn: Callable[[Request], dict[str, Any]] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/network-scoring")

    require_reset_allowed = reset_allowed_guard(
        allow_reset=allow_reset,
        resource_label="network scoring",
    )

    def command_principal(request: Request, body: Any, if_actor_match: str | None):
        principal = request.state.operator_principal
        if (if_actor_match is not None and if_actor_match != principal.subject_id
                or body.expectedActorSubjectId is not None and body.expectedActorSubjectId != principal.subject_id):
            raise ScoreDeliveryConflict("ACTOR_CONTEXT_CONFLICT")
        if body.expectedTenantId is not None and body.expectedTenantId != principal.tenant_id:
            raise ScoreDeliveryConflict("TENANT_CONTEXT_CONFLICT")
        return principal

    def command_receipt(operation: str, principal: Any, receipt: dict[str, Any],
                        key: str | None, correlation: str | None) -> dict[str, Any]:
        # The accepted command's actor is distinct from the immutable request
        # creator. A legitimate retry may be made by another scoped actor.
        return {"operation": operation, "actorSubjectId": principal.subject_id,
                "tenantId": principal.tenant_id, "idempotencyKey": key,
                "correlationId": correlation, "executionRequestId": receipt["executionRequestId"],
                "jobId": receipt["jobId"]}

    @router.get("", dependencies=[Depends(require_view_permission_fn)])
    @router.get("/", dependencies=[Depends(require_view_permission_fn)])
    def get_network_scoring(
        request: Request,
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            principal = request.state.operator_principal
            kwargs: dict[str, Any] = {
                "correlation_id": x_correlation_id,
                "read_principal": principal,
            }
            if principal.has_role(Role.OPERATOR_VIEWER):
                kwargs.update(
                    principal=principal,
                    scope_snapshot=(read_scope_snapshot_fn(request) if read_scope_snapshot_fn else {}),
                )
            return resolve_service(request, service, service_resolver).snapshot(**kwargs)
        except NetworkScoringRuntimeUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=exc.to_detail(),
            ) from exc

    @router.post(
        "/reset",
        dependencies=[
            Depends(require_write_permission_fn),
            Depends(require_reset_allowed),
        ],
    )
    def reset_network_scoring(request: Request) -> dict[str, Any]:
        return resolve_service(request, service, service_resolver).reset(
            read_principal=request.state.operator_principal,
        )

    @router.post(
        "/candidates/{candidate_id}/score",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def score_candidate(
        candidate_id: str,
        body: NetworkScoringActorPayload,
        request: Request,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
        if_actor_match: str | None = Header(default=None, alias="If-Actor-Match"),
    ) -> dict[str, Any]:
        try:
            principal = command_principal(request, body, if_actor_match)
            owner = execution_service if execution_service is not None else (service._execution_service if service_resolver is None else None)
            if owner is not None:
                from modules.sitescore.domain.execution import ScoreExecutionCommand
                command = ScoreExecutionCommand(candidate_id, body.expectedReportId, body.expectedReportVersion,
                    body.expectedJobVersion, body.reason, idempotency_key, x_correlation_id or getattr(request.state, "correlation_id", None))
                receipt = owner.create(command, principal=principal)
                result = {"executionRequest": receipt, "correlationId": x_correlation_id,
                          "commandReceipt": command_receipt("create", principal, receipt,
                                                            idempotency_key, command.correlation_id)}
            else:
                result = resolve_service(request, service, service_resolver).score_candidate(
                    candidate_id=candidate_id, actor_role_id=body.actorRoleId, actor_name=body.actorName,
                    idempotency_key=idempotency_key, correlation_id=x_correlation_id,
                    principal=request.state.operator_principal,
                )
            if "executionRequest" in result:
                response.status_code = status.HTTP_202_ACCEPTED
            candidate = result.get("candidate")
            if isinstance(candidate, dict) and "readEvidence" in candidate:
                result = {
                    **result,
                    "candidate": NetworkScoringService._mask_candidate_read(
                        candidate, request.state.operator_principal,
                    ),
                }
            return result
        except (ScoreDeliveryConflict, ScoreDeliveryDenied, ScoreDeliveryValidationError, ScoreDeliveryUnavailable, ProductionModelRuntimeError, ValueError) as exc:
            raise execution_http_error(exc) from exc
        except NetworkScoringNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkScoringGateError as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "missing": exc.missing},
            ) from exc
        except NetworkScoringRuntimeUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=exc.to_detail(),
            ) from exc

    def execution_owner(request: Request):
        owner = execution_service if execution_service is not None else (service._execution_service if service_resolver is None else None)
        if owner is None:
            raise HTTPException(503, {"code": "SITESCORE_EXECUTION_OWNER_UNAVAILABLE"})
        return owner

    @router.get("/candidates/{candidate_id}/execution-context", dependencies=[Depends(require_view_permission_fn)])
    def execution_context(candidate_id: str, request: Request) -> dict[str, Any]:
        try:
            principal = request.state.operator_principal
            context = execution_owner(request).context(candidate_id, principal=principal)
            return {**context, "tenantId": principal.tenant_id}
        except Exception as exc:
            raise execution_http_error(exc) from exc

    @router.get("/execution-requests/{request_id}", dependencies=[Depends(require_view_permission_fn)])
    def execution_request(request_id: str, request: Request) -> dict[str, Any]:
        try:
            receipt = execution_owner(request).get(request_id, principal=request.state.operator_principal)
            if receipt is None:
                raise HTTPException(404, {"code": "SITESCORE_EXECUTION_NOT_FOUND"})
            return receipt
        except HTTPException:
            raise
        except Exception as exc:
            raise execution_http_error(exc) from exc

    @router.post("/execution-requests/{request_id}/retry", status_code=202, dependencies=[Depends(require_write_permission_fn)])
    def retry_execution_request(request_id: str, body: ScoreExecutionRetryPayload, request: Request,
                                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                                x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
                                if_actor_match: str | None = Header(default=None, alias="If-Actor-Match")) -> dict[str, Any]:
        try:
            principal = command_principal(request, body, if_actor_match)
            correlation = x_correlation_id or getattr(request.state, "correlation_id", None)
            receipt = execution_owner(request).retry(request_id, principal=principal,
                expected_version=body.expectedVersion, expected_job_version=body.expectedJobVersion,
                reason=body.reason, idempotency_key=idempotency_key,
                correlation_id=correlation)
            return {"executionRequest": receipt,
                    "commandReceipt": command_receipt("retry", principal, receipt, idempotency_key, correlation)}
        except Exception as exc:
            raise execution_http_error(exc) from exc

    @router.post("/score", dependencies=[Depends(require_write_permission_fn)])
    def score_batch(
        body: NetworkScoringBatchPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return resolve_service(request, service, service_resolver).score_batch(
                actor_role_id=body.actorRoleId,
                actor_name=body.actorName,
                candidate_ids=body.candidateIds,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )
        except NetworkScoringNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkScoringRuntimeUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=exc.to_detail(),
            ) from exc

    @router.post("/compare", dependencies=[Depends(require_write_permission_fn)])
    def set_compare(
        body: NetworkScoringComparePayload,
        request: Request,
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return resolve_service(request, service, service_resolver).set_compare_set(
                candidate_ids=body.candidateIds,
                actor_role_id=body.actorRoleId,
                actor_name=body.actorName,
                correlation_id=x_correlation_id,
            )
        except NetworkScoringNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkScoringRuntimeUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=exc.to_detail(),
            ) from exc

    return router


__all__ = ["create_network_scoring_sub_router"]
