from __future__ import annotations

import os
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from modules.opsboard.application.store_ops import (
    StoreOpsConflict,
    StoreOpsLiveDataUnavailable,
    StoreOpsNotFound,
    StoreOpsPolicyError,
    StoreOpsService,
)
from shared.audit import InMemoryAuditLog


class StoreOpsTransitionPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    issueId: str | None = None
    issueTitle: str | None = None
    storeId: str | None = None
    storeName: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class StoreOpsCameraPurposePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    issueId: str | None = None
    issueTitle: str | None = None
    storeId: str | None = None
    storeName: str | None = None
    purpose: str
    cameraLocation: str | None = None
    timeWindow: str | None = None
    retentionHours: int | None = None
    privacyAcknowledged: bool = False
    auditNote: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None


def _live_data_required(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    flag = os.environ.get("ODP_REQUIRE_LIVE_DATA", "").strip().lower()
    deployment = (
        os.environ.get(
            "ODP_DEPLOY_ENV",
            os.environ.get("APP_ENV", "development"),
        )
        .strip()
        .lower()
    )
    return flag in {"1", "true", "yes", "on"} or deployment in {
        "staging",
        "stage",
        "prod",
        "production",
    }


def create_operator_store_ops_router(
    *,
    repository: Any = None,
    audit_log: InMemoryAuditLog | None = None,
    require_live_data: bool | None = None,
) -> APIRouter:
    from apps.api.oday_api.security.dependencies import (
        OPERATOR_CONSOLE_RESOURCE,
        build_engine,
        require_operator_permission,
    )
    from shared.auth import Action

    active_audit_log = audit_log or InMemoryAuditLog()
    authz_engine = build_engine(audit_log=active_audit_log)
    read_guard = require_operator_permission(
        OPERATOR_CONSOLE_RESOURCE, Action.VIEW, engine=authz_engine
    )
    write_guard = require_operator_permission("intervention", Action.CREATE, engine=authz_engine)
    live_required = _live_data_required(require_live_data)
    service = StoreOpsService(
        repository=repository,
        audit_log=active_audit_log,
        require_live_data=live_required,
    )
    router = APIRouter(prefix="/operator/store-ops", tags=["operator-store-ops"])

    def call_service(operation: str, method: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except StoreOpsLiveDataUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "STORE_OPS_LIVE_DATA_UNAVAILABLE",
                    "operation": operation,
                    "message": str(exc),
                },
            ) from exc

    @router.get("/summary", dependencies=[Depends(read_guard)])
    def get_summary(request: Request) -> dict[str, Any]:
        snapshot = call_service("store_ops.summary", service.snapshot)
        return {
            "fourLightSummary": snapshot["fourLightSummary"],
            "stores": snapshot["stores"],
            "correlation_id": request.state.correlation_id,
        }

    @router.get("/issues", dependencies=[Depends(read_guard)])
    def list_issues(
        request: Request,
        query: str | None = None,
        statuses: Annotated[list[str] | None, Query()] = None,
        sources: Annotated[list[str] | None, Query()] = None,
        severities: Annotated[list[str] | None, Query()] = None,
        mineOnly: bool = False,
        roleId: str = "opsLead",
        light: Literal["demand", "operations", "staffing", "margin"] | None = None,
        lightStatus: Literal["green", "yellow", "red"] | None = None,
    ) -> dict[str, Any]:
        snapshot = call_service(
            "store_ops.issues",
            service.snapshot,
            query=query,
            statuses=statuses or (),
            sources=sources or (),
            severities=severities or (),
            mine_only=mineOnly,
            role_id=_store_ops_role_from_request(request, roleId),
            light=light,
            light_status=lightStatus,
        )
        snapshot["correlation_id"] = request.state.correlation_id
        return snapshot

    @router.get("/issues/{issue_id}", dependencies=[Depends(read_guard)])
    def get_issue(issue_id: str, request: Request) -> dict[str, Any]:
        try:
            return {
                "issue": call_service(
                    "store_ops.issue",
                    service.get_issue,
                    issue_id,
                ),
                "correlation_id": request.state.correlation_id,
            }
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/issues/{issue_id}/evidence", dependencies=[Depends(read_guard)])
    def get_issue_evidence(issue_id: str, request: Request) -> dict[str, Any]:
        try:
            result = call_service(
                "store_ops.issue_evidence",
                service.issue_evidence,
                issue_id,
            )
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        result["correlation_id"] = request.state.correlation_id
        return result

    @router.post(
        "/issues/{issue_id}/{action_type}",
        dependencies=[Depends(write_guard)],
    )
    def transition_issue(
        issue_id: str,
        action_type: str,
        body: StoreOpsTransitionPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            if action_type == "camera-purpose":
                return call_service(
                    "store_ops.camera_purpose",
                    service.record_camera_purpose,
                    issue_id=issue_id,
                    payload=body.model_dump(exclude_none=True),
                    idempotency_key=idempotency_key,
                    correlation_id=request.state.correlation_id,
                    actor_role_id=body.actorRoleId or "opsLead",
                    actor_name=body.actorName or _actor_name_from_role(body.actorRoleId),
                )
            return call_service(
                "store_ops.transition",
                service.transition_issue,
                issue_id=issue_id,
                action_type=action_type,
                payload=body.model_dump(exclude_none=True),
                idempotency_key=idempotency_key,
                correlation_id=request.state.correlation_id,
                actor_role_id=body.actorRoleId or "opsLead",
                actor_name=body.actorName or _actor_name_from_role(body.actorRoleId),
            )
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except StoreOpsConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except StoreOpsPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post(
        "/issues/{issue_id}/camera-purpose",
        dependencies=[Depends(write_guard)],
    )
    def record_camera_purpose(
        issue_id: str,
        body: StoreOpsCameraPurposePayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            return call_service(
                "store_ops.camera_purpose",
                service.record_camera_purpose,
                issue_id=issue_id,
                payload=body.model_dump(exclude_none=True),
                idempotency_key=idempotency_key,
                correlation_id=request.state.correlation_id,
                actor_role_id=body.actorRoleId or "opsLead",
                actor_name=body.actorName or _actor_name_from_role(body.actorRoleId),
            )
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except StoreOpsConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except StoreOpsPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router


def _actor_name_from_role(role_id: str | None) -> str:
    return {
        "opsLead": "營運主管",
        "supportLead": "客服主管",
        "facilitiesLead": "工務主任",
        "marketingManager": "行銷經理",
        "expansionManager": "展店經理",
        "auditPm": "PM／稽核",
    }.get(role_id or "opsLead", "Operator")


def _store_ops_role_from_request(request: Request, fallback: str) -> str:
    role_id = getattr(request.state, "operator_role_id", None)
    return {
        "ops-lead": "opsLead",
        "cs-lead": "supportLead",
        "field-lead": "facilitiesLead",
        "marketing-manager": "marketingManager",
        "expansion-manager": "expansionManager",
        "pm-audit": "auditPm",
    }.get(role_id or "", fallback)


__all__ = ["create_operator_store_ops_router"]
