"""User & Role Management sub-router for Operator Console (ODP-CAP-USER-ROLE-UI-001).

Routes (all under /operator/users):
  GET  /operator/users             — List all user role/scope assignments
  GET  /operator/users/roles       — List canonical platform role definitions
  GET  /operator/users/audit-trail — List user & role management audit trail
  GET  /operator/users/{subject_id} — Get user role detail by subject_id
  POST /operator/users             — Save (create/update) user role & scope mapping
  POST /operator/users/{subject_id}/status — Update user active status
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from apps.api.app.routes.operator_modules.live_service import resolve_service
from modules.opsboard.application.user_role_management import (
    UserNotFound,
    UserRoleManagementService,
    UserRolePolicyError,
)

# ---------------------------------------------------------------------------
# Request DTOs
# ---------------------------------------------------------------------------


class ScopePayload(BaseModel):
    """Scope constraint parameters on Tenant, Brand, Region, Store, etc."""

    model_config = ConfigDict(extra="allow")

    tenant_id: str = "tenant-default"
    brand_ids: list[str] = Field(default_factory=list)
    region_ids: list[str] = Field(default_factory=list)
    store_ids: list[str] = Field(default_factory=list)
    assigned_area_ids: list[str] = Field(default_factory=list)
    heat_zone_ids: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    clearance: str = "CONFIDENTIAL"


class UserSavePayload(BaseModel):
    """POST /operator/users — payload for user role & scope assignment."""

    model_config = ConfigDict(extra="allow")

    subjectId: str = Field(min_length=1)
    email: str | None = None
    name: str | None = None
    roles: list[str] = Field(min_length=1)
    scope: ScopePayload | None = None
    attributes: dict[str, Any] | None = None
    status: str = "active"
    reason: str = ""
    actorName: str | None = None
    actorRole: str | None = None


class UserStatusPayload(BaseModel):
    """POST /operator/users/{subject_id}/status — payload for status change."""

    model_config = ConfigDict(extra="allow")

    status: str
    reason: str = ""
    actorName: str | None = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_user_role_sub_router(
    service: UserRoleManagementService,
    *,
    require_view_permission_fn: Any = None,
    require_manage_permission_fn: Any = None,
    service_resolver: Any = None,
) -> APIRouter:
    """Return the User & Role Management sub-router."""
    router = APIRouter(prefix="/users", tags=["operator-users-roles"])

    read_deps: list[Any] = (
        [Depends(require_view_permission_fn)] if require_view_permission_fn else []
    )
    manage_deps: list[Any] = (
        [Depends(require_manage_permission_fn)] if require_manage_permission_fn else []
    )

    def get_svc(req: Request) -> UserRoleManagementService:
        return resolve_service(req, service, service_resolver)

    @router.get("", dependencies=read_deps)
    def list_users(
        request: Request,
        status_filter: str | None = None,
    ) -> dict[str, Any]:
        svc = get_svc(request)
        users = svc.list_users(status_filter=status_filter)
        return {
            "users": users,
            "count": len(users),
            "correlation_id": getattr(request.state, "correlation_id", None),
        }

    @router.get("/roles", dependencies=read_deps)
    def list_roles(request: Request) -> dict[str, Any]:
        svc = get_svc(request)
        roles = svc.list_roles()
        return {
            "roles": roles,
            "count": len(roles),
            "correlation_id": getattr(request.state, "correlation_id", None),
        }

    @router.get("/audit-trail", dependencies=read_deps)
    def list_audit_trail(
        request: Request,
        subject_id: str | None = None,
    ) -> dict[str, Any]:
        svc = get_svc(request)
        tenant_id = (
            getattr(request.state, "operator_tenant_id", None)
            or getattr(request.state, "tenant_id", None)
            or request.headers.get("x-tenant-id")
        )
        events = svc.get_audit_trail(subject_id=subject_id, tenant_id=tenant_id)
        return {
            "events": events,
            "count": len(events),
            "correlation_id": getattr(request.state, "correlation_id", None),
        }

    @router.get("/{subject_id}", dependencies=read_deps)
    def get_user(subject_id: str, request: Request) -> dict[str, Any]:
        svc = get_svc(request)
        try:
            return svc.get_user(subject_id)
        except UserNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc

    @router.post("", dependencies=manage_deps)
    def save_user(
        body: UserSavePayload,
        request: Request,
    ) -> dict[str, Any]:
        svc = get_svc(request)
        scope_dict = body.scope.model_dump() if body.scope else None
        server_actor = getattr(request.state, "operator_subject_id", None) or "operator"
        server_role = getattr(request.state, "operator_role_id", None) or "platform_admin"
        partition_tenant = (
            getattr(request.state, "operator_tenant_id", None)
            or getattr(request.state, "tenant_id", None)
            or request.headers.get("x-tenant-id")
        )
        try:
            user = svc.save_user(
                subject_id=body.subjectId,
                roles=body.roles,
                scope=scope_dict,
                attributes=body.attributes,
                email=body.email,
                name=body.name,
                status=body.status,
                actor_name=server_actor,
                actor_role=server_role,
                reason=body.reason,
                correlation_id=getattr(request.state, "correlation_id", None),
                tenant_id=partition_tenant,
            )
            return {
                "user": user,
                "message": f"User '{body.subjectId}' role and scope saved successfully.",
                "correlation_id": getattr(request.state, "correlation_id", None),
            }
        except UserRolePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    @router.post("/{subject_id}/status", dependencies=manage_deps)
    def set_status(
        subject_id: str,
        body: UserStatusPayload,
        request: Request,
    ) -> dict[str, Any]:
        svc = get_svc(request)
        server_actor = getattr(request.state, "operator_subject_id", None) or "operator"
        partition_tenant = (
            getattr(request.state, "operator_tenant_id", None)
            or getattr(request.state, "tenant_id", None)
            or request.headers.get("x-tenant-id")
        )
        try:
            user = svc.set_user_status(
                subject_id=subject_id,
                status=body.status,
                actor_name=server_actor,
                reason=body.reason,
                correlation_id=getattr(request.state, "correlation_id", None),
                tenant_id=partition_tenant,
            )
            return {
                "user": user,
                "message": f"User '{subject_id}' status updated to {body.status}.",
                "correlation_id": getattr(request.state, "correlation_id", None),
            }
        except UserNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc
        except UserRolePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    return router


__all__ = ["create_user_role_sub_router"]
