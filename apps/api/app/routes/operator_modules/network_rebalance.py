"""Operator Console Network Rebalance routes.

Owns:
- GET /operator/network-rebalance
- POST /operator/network-rebalance/stores/{id}/avm/request
- POST /operator/network-rebalance/stores/{id}/avm/complete
- POST /operator/network-rebalance/stores/{id}/netplan/solve
- POST /operator/network-rebalance/stores/{id}/scenarios/{scenario_id}/select
- POST /operator/network-rebalance/stores/{id}/submit-review

The routes wrap NetworkRebalanceService and keep auth/idempotency headers at
the HTTP boundary. They compose through apps.api.app.routes.operator.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, field_validator

from apps.api.app.routes.operator_modules.live_service import resolve_service
from modules.opsboard.application.network_rebalance import (
    NetworkRebalanceConflict,
    NetworkRebalanceNotFound,
    NetworkRebalancePolicyError,
    NetworkRebalanceRuntimeUnavailable,
    NetworkRebalanceService,
)


class RebalanceActorPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    actorRoleId: str = "expansionManager"
    actorName: str | None = None
    reason: str | None = None
    simulateUnavailable: bool = False


class RebalanceSubmitPayload(RebalanceActorPayload):
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must not be empty")
        return value


def create_network_rebalance_sub_router(
    service: NetworkRebalanceService,
    *,
    require_view_permission_fn: Callable[..., Any],
    require_write_permission_fn: Callable[..., Any],
    reset_govern_fn: Callable[[], None] | None = None,
    service_resolver: Callable[[Request], Any] | None = None,
    allow_reset: bool = True,
) -> APIRouter:
    router = APIRouter(prefix="/network-rebalance", tags=["operator-network-rebalance"])

    def require_reset_allowed() -> None:
        if not allow_reset:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PRODUCTION_RESET_DENIED",
                    "message": "network rebalance reset is disabled in live mode",
                },
            )

    @router.get("", dependencies=[Depends(require_view_permission_fn)])
    @router.get("/", dependencies=[Depends(require_view_permission_fn)])
    def get_network_rebalance(
        request: Request,
        selected_store_id: str | None = Query(default=None, alias="selectedStoreId"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        return resolve_service(request, service, service_resolver).snapshot(
            selected_store_id=selected_store_id,
            correlation_id=x_correlation_id,
        )

    @router.post(
        "/reset",
        dependencies=[
            Depends(require_write_permission_fn),
            Depends(require_reset_allowed),
        ],
    )
    def reset_network_rebalance(request: Request) -> dict[str, Any]:
        if reset_govern_fn is not None:
            reset_govern_fn()
        return resolve_service(request, service, service_resolver).reset()

    @router.post(
        "/stores/{store_id}/avm/request",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def request_avm(
        store_id: str,
        body: RebalanceActorPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return resolve_service(request, service, service_resolver).request_avm(
                store_id=store_id,
                actor_role_id=body.actorRoleId,
                actor_name=body.actorName,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
                simulate_unavailable=body.simulateUnavailable,
            )
        except NetworkRebalanceRuntimeUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.to_detail()
            ) from exc
        except NetworkRebalanceNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkRebalanceConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkRebalancePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    @router.post(
        "/stores/{store_id}/avm/complete",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def complete_avm(
        store_id: str,
        body: RebalanceActorPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return resolve_service(request, service, service_resolver).complete_avm(
                store_id=store_id,
                actor_role_id=body.actorRoleId,
                actor_name=body.actorName,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
                simulate_unavailable=body.simulateUnavailable,
            )
        except NetworkRebalanceRuntimeUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.to_detail()
            ) from exc
        except NetworkRebalanceNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkRebalanceConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkRebalancePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    @router.post(
        "/stores/{store_id}/netplan/solve",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def solve_netplan(
        store_id: str,
        body: RebalanceActorPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return resolve_service(request, service, service_resolver).solve_netplan(
                store_id=store_id,
                actor_role_id=body.actorRoleId,
                actor_name=body.actorName,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
                simulate_unavailable=body.simulateUnavailable,
            )
        except NetworkRebalanceRuntimeUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.to_detail()
            ) from exc
        except NetworkRebalanceNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkRebalanceConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkRebalancePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    @router.post(
        "/stores/{store_id}/scenarios/{scenario_id}/select",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def select_scenario(
        store_id: str,
        scenario_id: str,
        body: RebalanceActorPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return resolve_service(request, service, service_resolver).select_scenario(
                store_id=store_id,
                scenario_id=scenario_id,
                actor_role_id=body.actorRoleId,
                actor_name=body.actorName,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )
        except NetworkRebalanceNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkRebalanceConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkRebalancePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    @router.post(
        "/stores/{store_id}/submit-review",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def submit_review(
        store_id: str,
        body: RebalanceSubmitPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return resolve_service(request, service, service_resolver).submit_review(
                store_id=store_id,
                reason=body.reason,
                actor_role_id=body.actorRoleId,
                actor_name=body.actorName,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )
        except NetworkRebalanceNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkRebalanceConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkRebalancePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    return router


__all__ = ["create_network_rebalance_sub_router"]
