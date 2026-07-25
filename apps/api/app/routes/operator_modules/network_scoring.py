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

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from apps.api.app.routes.operator_modules.live_service import resolve_service
from modules.opsboard.application.network_scoring import (
    NetworkScoringGateError,
    NetworkScoringNotFound,
    NetworkScoringRuntimeUnavailable,
    NetworkScoringService,
)


class NetworkScoringActorPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    actorRoleId: str = "expansionManager"
    actorName: str | None = None


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
    allow_reset: bool = True,
) -> APIRouter:
    router = APIRouter(prefix="/network-scoring")

    def require_reset_allowed() -> None:
        if not allow_reset:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PRODUCTION_RESET_DENIED",
                    "message": "network scoring reset is disabled in live mode",
                },
            )

    @router.get("", dependencies=[Depends(require_view_permission_fn)])
    @router.get("/", dependencies=[Depends(require_view_permission_fn)])
    def get_network_scoring(
        request: Request,
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return resolve_service(request, service, service_resolver).snapshot(
                correlation_id=x_correlation_id
            )
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
        return resolve_service(request, service, service_resolver).reset()

    @router.post(
        "/candidates/{candidate_id}/score",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def score_candidate(
        candidate_id: str,
        body: NetworkScoringActorPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return resolve_service(request, service, service_resolver).score_candidate(
                candidate_id=candidate_id,
                actor_role_id=body.actorRoleId,
                actor_name=body.actorName,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )
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
