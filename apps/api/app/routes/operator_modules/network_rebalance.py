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
from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.api.app.routes._common import reset_allowed_guard
from apps.api.app.routes.operator_modules.live_service import resolve_service
from modules.opsboard.application.network_rebalance import (
    NetworkRebalanceConflict,
    NetworkRebalanceNotFound,
    NetworkRebalancePolicyError,
    NetworkRebalanceRuntimeUnavailable,
    NetworkRebalanceService,
)
from solver.netplan.model import ConstraintClass


class RebalanceActorPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    actorRoleId: str = "expansionManager"
    actorName: str | None = None
    reason: str | None = None
    simulateUnavailable: bool = False


class RebalanceSubmitPayload(RebalanceActorPayload):
    reason: str
    acknowledgedClasses: list[ConstraintClass | str] | None = None
    acknowledgementReason: str | None = None
    approvalReceiptId: str | None = None

    @field_validator("reason")
    @classmethod
    def reason_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must not be empty")
        return value


class RebalanceScenario(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    score: float | int | None = None
    expectedGrossMargin: float | None = None
    investmentTwd: float | int | None = None
    risk: float | int | str | None = None
    capacityDelta: float | int | None = None
    actions: list[Any] | None = None
    bindingConstraints: list[str] = Field(default_factory=list)
    modelledConstraintClasses: list[ConstraintClass]
    unmodelledConstraintClasses: list[ConstraintClass]
    modelled_constraint_classes: list[ConstraintClass]
    unmodelled_constraint_classes: list[ConstraintClass]
    isSystemRecommendation: bool = False
    selected: bool = False
    solverStatus: str | None = None
    solverVersion: str | None = None
    evidenceIds: list[str] = Field(default_factory=list)
    isStale: bool = False
    isInfeasible: bool = False
    diagnostics: list[Any] = Field(default_factory=list)
    roi: str | None = None
    roiPct: float | None = None
    inv: str | None = None
    payback: str | None = None
    time: str | None = None
    rationale: str | None = None


class RebalanceStore(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    storeId: str
    storeName: str
    status: str
    statusLabel: str | None = None
    ownerRoleId: str | None = None
    ownerName: str | None = None
    summary: str | None = None
    healthNote: str | None = None
    monthlyRevenueLabel: str | None = None
    monthlyRevenueTwd: float | int | None = None
    utilizationLabel: str | None = None
    utilizationPct: float | int | None = None
    sourceIssueId: str | None = None
    lightHistory: list[str] | None = None
    trend: list[float | int] | None = None
    evidence: list[dict[str, Any]] | None = None
    relocationExecuted: bool | None = None
    executionBoundary: str | None = None
    runtimeState: dict[str, Any] | None = None
    avmRequestId: str | None = None
    avmJob: dict[str, Any] | None = None
    avm: dict[str, Any] | None = None
    avmP10: float | int | None = None
    avmP50: float | int | None = None
    avmP90: float | int | None = None
    avmConf: str | None = None
    avmReserve: str | None = None
    avmModelVersion: str | None = None
    avmSnapshotId: str | None = None
    avmEvidenceId: str | None = None
    netPlanJob: dict[str, Any] | None = None
    netPlanScenarios: list[RebalanceScenario] = Field(default_factory=list)
    netPlanModelVersion: str | None = None
    netPlanSnapshotId: str | None = None
    selectedScenarioId: str | None = None
    netPlanOptionId: str | None = None
    selectedScenarioOwner: dict[str, Any] | None = None
    selectedScenarioEvidenceId: str | None = None
    relatedApprovalId: str | None = None
    approvalStatus: str | None = None
    canonicalAvmCaseId: str | None = None
    canonicalNetPlanScenarioIds: list[str] | None = None


class NetworkRebalanceMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    serviceVersion: str | None = None
    canonicalPackage: str | None = None
    canonicalZipSha256: str | None = None
    screenLabels: list[str] = Field(default_factory=list)
    avm: dict[str, Any] | None = None
    netPlan: dict[str, Any] | None = None


class NetworkRebalanceCounts(BaseModel):
    model_config = ConfigDict(extra="allow")

    stores: int = 0
    pendingApprovals: int = 0


class NetworkRebalanceModels(BaseModel):
    model_config = ConfigDict(extra="allow")

    avm: dict[str, Any] | None = None
    netPlan: dict[str, Any] | None = None


class NetworkRebalanceSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str | None = None
    stores: list[RebalanceStore]
    selectedStoreId: str | None = None
    selectedStore: RebalanceStore | None = None
    selectedScenario: RebalanceScenario | None = None
    metadata: NetworkRebalanceMetadata | dict[str, Any] | None = None
    models: NetworkRebalanceModels | dict[str, Any] | None = None
    governApprovals: list[dict[str, Any]] = Field(default_factory=list)
    auditEvents: list[dict[str, Any]] = Field(default_factory=list)
    counts: NetworkRebalanceCounts = Field(default_factory=NetworkRebalanceCounts)
    correlationId: str | None = None


class NetworkRebalanceMutationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    store: RebalanceStore
    auditEvent: dict[str, Any] | None = None
    correlationId: str | None = None


class NetworkRebalanceReviewResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    store: RebalanceStore
    approval: dict[str, Any] | None = None
    auditEvent: dict[str, Any] | None = None
    correlationId: str | None = None


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

    require_reset_allowed = reset_allowed_guard(
        allow_reset=allow_reset,
        resource_label="network rebalance",
    )

    @router.get(
        "",
        response_model=NetworkRebalanceSnapshotResponse,
        dependencies=[Depends(require_view_permission_fn)],
    )
    @router.get(
        "/",
        response_model=NetworkRebalanceSnapshotResponse,
        dependencies=[Depends(require_view_permission_fn)],
    )
    def get_network_rebalance(
        request: Request,
        selected_store_id: str | None = Query(default=None, alias="selectedStoreId"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> NetworkRebalanceSnapshotResponse:
        return resolve_service(request, service, service_resolver).snapshot(
            selected_store_id=selected_store_id,
            correlation_id=x_correlation_id,
        )

    @router.post(
        "/reset",
        response_model=NetworkRebalanceSnapshotResponse,
        dependencies=[
            Depends(require_write_permission_fn),
            Depends(require_reset_allowed),
        ],
    )
    def reset_network_rebalance(request: Request) -> NetworkRebalanceSnapshotResponse:
        if reset_govern_fn is not None:
            reset_govern_fn()
        return resolve_service(request, service, service_resolver).reset()

    @router.post(
        "/stores/{store_id}/avm/request",
        response_model=NetworkRebalanceMutationResponse,
        dependencies=[Depends(require_write_permission_fn)],
    )
    def request_avm(
        store_id: str,
        body: RebalanceActorPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> NetworkRebalanceMutationResponse:
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
        response_model=NetworkRebalanceMutationResponse,
        dependencies=[Depends(require_write_permission_fn)],
    )
    def complete_avm(
        store_id: str,
        body: RebalanceActorPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> NetworkRebalanceMutationResponse:
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
        response_model=NetworkRebalanceMutationResponse,
        dependencies=[Depends(require_write_permission_fn)],
    )
    def solve_netplan(
        store_id: str,
        body: RebalanceActorPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> NetworkRebalanceMutationResponse:
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
        response_model=NetworkRebalanceMutationResponse,
        dependencies=[Depends(require_write_permission_fn)],
    )
    def select_scenario(
        store_id: str,
        scenario_id: str,
        body: RebalanceActorPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> NetworkRebalanceMutationResponse:
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
        response_model=NetworkRebalanceReviewResponse,
        dependencies=[Depends(require_write_permission_fn)],
    )
    def submit_review(
        store_id: str,
        body: RebalanceSubmitPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> NetworkRebalanceReviewResponse:
        try:
            return resolve_service(request, service, service_resolver).submit_review(
                store_id=store_id,
                reason=body.reason,
                actor_role_id=body.actorRoleId,
                actor_name=body.actorName,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
                acknowledged_classes=body.acknowledgedClasses,
                acknowledgement_reason=body.acknowledgementReason,
                approval_receipt_id=body.approvalReceiptId,
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


__all__ = [
    "NetworkRebalanceCounts",
    "NetworkRebalanceMetadata",
    "NetworkRebalanceModels",
    "NetworkRebalanceMutationResponse",
    "NetworkRebalanceReviewResponse",
    "NetworkRebalanceSnapshotResponse",
    "RebalanceActorPayload",
    "RebalanceScenario",
    "RebalanceStore",
    "RebalanceSubmitPayload",
    "create_network_rebalance_sub_router",
]
