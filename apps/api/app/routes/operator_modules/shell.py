"""Operator Console shell sub-router.

Owns: /operator/bootstrap, /operator/today, /operator/search (legacy console
reads) plus the product-shell surface added by ODP-PGAP-SHELL-001 —
/operator/shell/{home,tasks,notifications,search,admin,settings,franchisee}.

Not touching: issues, approvals, evidence, seed sub-routers.
Composes with: create_operator_router() in operator.py, which owns every guard.

Guard contract (all required — an orphaned sub-router must fail loudly at
construction rather than fail open):

- require_view_permission_fn       → operator_console VIEW
- require_write_permission_fn      → operator_console UPDATE
- require_admin_permission_fn      → operator_console UPDATE (admin surface;
  ShellService additionally enforces the ops-lead product rule and answers 403)
- require_franchisee_view_fn       → franchisee_portal VIEW
- require_franchisee_write_fn      → franchisee_portal CREATE

The franchisee guards deliberately do *not* use require_operator_permission:
Role.FRANCHISEE maps to no Operator Console role, so that factory would deny
every franchisee at operator.role. The franchisee surface is a separate
resource with its own grants.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from modules.opsboard.application.operator_state import OperatorStateService
from modules.opsboard.application.shell import (
    ShellConflict,
    ShellForbidden,
    ShellNotFound,
    ShellPolicyError,
    ShellService,
)
from shared.auth.identity import Principal

# ----------------------------------------------------------------------
# Request bodies
# ----------------------------------------------------------------------


class TaskAssignRequest(BaseModel):
    """Assign a Task Center task to a role/subject."""

    assigneeId: str = Field(min_length=1)
    assigneeName: str | None = None
    slaDueAt: str | None = None


class NotificationPreferencesRequest(BaseModel):
    """Per-role notification delivery preferences."""

    channels: dict[str, bool]
    severityFloor: str = "info"
    digest: str = "immediate"


class RoleWorkspacesRequest(BaseModel):
    """Override a role's workspace grants (high-risk, audited)."""

    allowedWorkspaces: list[str]


class SettingsRequest(BaseModel):
    """Workspace settings patch."""

    values: dict[str, Any]


class FranchiseeAcknowledgeRequest(BaseModel):
    """Franchisee acknowledgement of a notification."""

    notificationId: str = Field(min_length=1)
    storeId: str | None = None


class FranchiseeReportRequest(BaseModel):
    """Franchisee field report."""

    category: str = Field(min_length=1)
    message: str = Field(min_length=1)
    storeId: str | None = None


def _context(
    *,
    x_operator_role: str | None,
    x_subject_id: str | None,
    x_roles: str | None,
    x_correlation_id: str | None,
) -> dict[str, str | None]:
    return {
        "role_id": x_operator_role,
        "subject_id": x_subject_id,
        "system_roles": x_roles,
        "correlation_id": x_correlation_id,
    }


def _context_from_request(
    request: Request,
    *,
    x_operator_role: str | None,
    x_subject_id: str | None,
    x_roles: str | None,
    x_correlation_id: str | None,
) -> dict[str, str | None]:
    return _context(
        x_operator_role=getattr(request.state, "operator_role_id", None) or x_operator_role,
        x_subject_id=getattr(request.state, "operator_subject_id", None) or x_subject_id,
        x_roles=getattr(request.state, "operator_system_roles", None) or x_roles,
        x_correlation_id=getattr(request.state, "correlation_id", None) or x_correlation_id,
    )


def _translate(exc: Exception) -> HTTPException:
    """Map application errors onto the HTTP contract."""
    if isinstance(exc, ShellNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ShellConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, ShellForbidden):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


_SHELL_ERRORS = (ShellNotFound, ShellConflict, ShellForbidden, ShellPolicyError)


def create_shell_sub_router(
    state_service: OperatorStateService,
    *,
    require_view_permission_fn: Callable[..., object],
    require_write_permission_fn: Callable[..., object],
    require_admin_permission_fn: Callable[..., object],
    require_franchisee_view_fn: Callable[..., object],
    require_franchisee_write_fn: Callable[..., object],
    shell_service: ShellService | None = None,
) -> APIRouter:
    """Return the shell sub-router (console reads + product-shell surface)."""
    router = APIRouter()
    shell = shell_service or ShellService(state_service)

    # ------------------------------------------------------------------
    # Legacy Operator Console reads (unchanged contract)
    # ------------------------------------------------------------------

    @router.get("/bootstrap", dependencies=[Depends(require_view_permission_fn)])
    def bootstrap(
        request: Request,
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return the full operator console bootstrap payload."""
        return state_service.get_today(
            **_context_from_request(
                request,
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )
        )

    @router.get("/today", dependencies=[Depends(require_view_permission_fn)])
    def get_today(
        request: Request,
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return today's operational snapshot (alias of bootstrap for FE compat)."""
        return state_service.get_today(
            **_context_from_request(
                request,
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )
        )

    @router.get("/search", dependencies=[Depends(require_view_permission_fn)])
    def search(
        request: Request,
        q: str = Query(default=""),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return role-aware command/search results with API deep-link targets."""
        return state_service.search(
            q,
            **_context_from_request(
                request,
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            ),
        )

    # ------------------------------------------------------------------
    # Product shell — Home
    # ------------------------------------------------------------------

    @router.get("/shell/home", dependencies=[Depends(require_view_permission_fn)])
    def shell_home(
        request: Request,
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return the aggregated first screen for the acting role."""
        return shell.get_home(
            **_context_from_request(
                request,
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )
        )

    # ------------------------------------------------------------------
    # Product shell — Task Center
    # ------------------------------------------------------------------

    @router.get("/shell/tasks", dependencies=[Depends(require_view_permission_fn)])
    def shell_tasks(
        request: Request,
        sla: str | None = Query(default=None),
        assignee: str | None = Query(default=None),
        task_status: str | None = Query(default=None, alias="status"),
        task_id: str | None = Query(default=None, alias="taskId"),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return the Task Center list with SLA/assignee filters and deep links."""
        return shell.get_tasks(
            sla=sla,
            assignee=assignee,
            status=task_status,
            task_id=task_id,
            **_context_from_request(
                request,
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            ),
        )

    @router.post(
        "/shell/tasks/{task_id}/assignment",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def shell_assign_task(
        task_id: str,
        body: TaskAssignRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Durably assign a task. Governed, audited, idempotent."""
        try:
            return shell.assign_task(
                task_id=task_id,
                assignee_id=body.assigneeId,
                assignee_name=body.assigneeName,
                sla_due_at=body.slaDueAt,
                idempotency_key=idempotency_key,
                **_context_from_request(
                    request,
                    x_operator_role=x_operator_role,
                    x_subject_id=x_subject_id,
                    x_roles=x_roles,
                    x_correlation_id=x_correlation_id,
                ),
            )
        except _SHELL_ERRORS as exc:
            raise _translate(exc) from exc

    # ------------------------------------------------------------------
    # Product shell — Notifications
    # ------------------------------------------------------------------

    @router.get("/shell/notifications", dependencies=[Depends(require_view_permission_fn)])
    def shell_notifications(
        request: Request,
        severity: str | None = Query(default=None),
        acknowledged: bool | None = Query(default=None),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return the durable notification inbox for the acting role."""
        return shell.get_notifications(
            severity=severity,
            acknowledged=acknowledged,
            **_context_from_request(
                request,
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            ),
        )

    @router.get(
        "/shell/notifications/preferences",
        dependencies=[Depends(require_view_permission_fn)],
    )
    def shell_notification_preferences(
        request: Request,
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return the acting role's durable notification preferences."""
        context = _context_from_request(
            request,
            x_operator_role=x_operator_role,
            x_subject_id=x_subject_id,
            x_roles=x_roles,
            x_correlation_id=x_correlation_id,
        )
        context.pop("correlation_id", None)
        return shell.get_notification_preferences(**context)

    @router.put(
        "/shell/notifications/preferences",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def shell_update_notification_preferences(
        body: NotificationPreferencesRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Durably persist notification preferences. Audited and idempotent."""
        try:
            return shell.update_notification_preferences(
                preferences=body.model_dump(),
                idempotency_key=idempotency_key,
                **_context_from_request(
                    request,
                    x_operator_role=x_operator_role,
                    x_subject_id=x_subject_id,
                    x_roles=x_roles,
                    x_correlation_id=x_correlation_id,
                ),
            )
        except _SHELL_ERRORS as exc:
            raise _translate(exc) from exc

    @router.post(
        "/shell/notifications/{notification_id}/acknowledgement",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def shell_acknowledge_notification(
        notification_id: str,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Durably acknowledge a notification. Audited and idempotent."""
        try:
            return shell.acknowledge_notification(
                notification_id=notification_id,
                idempotency_key=idempotency_key,
                **_context_from_request(
                    request,
                    x_operator_role=x_operator_role,
                    x_subject_id=x_subject_id,
                    x_roles=x_roles,
                    x_correlation_id=x_correlation_id,
                ),
            )
        except _SHELL_ERRORS as exc:
            raise _translate(exc) from exc

    # ------------------------------------------------------------------
    # Product shell — Global search
    # ------------------------------------------------------------------

    @router.get("/shell/search", dependencies=[Depends(require_view_permission_fn)])
    def shell_search(
        request: Request,
        q: str = Query(default=""),
        limit: int = Query(default=20, ge=1, le=100),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return authorized cross-domain results and keyboard commands."""
        return shell.search(
            q,
            limit=limit,
            **_context_from_request(
                request,
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            ),
        )

    # ------------------------------------------------------------------
    # Product shell — Administration
    # ------------------------------------------------------------------

    @router.get("/shell/admin", dependencies=[Depends(require_admin_permission_fn)])
    def shell_admin(
        request: Request,
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return the role/workspace administration view."""
        try:
            return shell.get_admin(
                **_context_from_request(
                    request,
                    x_operator_role=x_operator_role,
                    x_subject_id=x_subject_id,
                    x_roles=x_roles,
                    x_correlation_id=x_correlation_id,
                )
            )
        except _SHELL_ERRORS as exc:
            raise _translate(exc) from exc

    @router.put(
        "/shell/admin/roles/{target_role_id}/workspaces",
        dependencies=[Depends(require_admin_permission_fn)],
    )
    def shell_update_role_workspaces(
        target_role_id: str,
        body: RoleWorkspacesRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Durably override a role's workspace grants. High-risk, always audited."""
        try:
            return shell.update_role_workspaces(
                target_role_id=target_role_id,
                allowed_workspaces=body.allowedWorkspaces,
                idempotency_key=idempotency_key,
                **_context_from_request(
                    request,
                    x_operator_role=x_operator_role,
                    x_subject_id=x_subject_id,
                    x_roles=x_roles,
                    x_correlation_id=x_correlation_id,
                ),
            )
        except _SHELL_ERRORS as exc:
            raise _translate(exc) from exc

    # ------------------------------------------------------------------
    # Product shell — Settings
    # ------------------------------------------------------------------

    @router.get("/shell/settings", dependencies=[Depends(require_view_permission_fn)])
    def shell_settings(
        request: Request,
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return workspace settings for the acting role."""
        return shell.get_settings(
            **_context_from_request(
                request,
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )
        )

    @router.put("/shell/settings", dependencies=[Depends(require_write_permission_fn)])
    def shell_update_settings(
        body: SettingsRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Durably persist workspace settings. Governed and audited."""
        try:
            return shell.update_settings(
                values=body.values,
                idempotency_key=idempotency_key,
                **_context_from_request(
                    request,
                    x_operator_role=x_operator_role,
                    x_subject_id=x_subject_id,
                    x_roles=x_roles,
                    x_correlation_id=x_correlation_id,
                ),
            )
        except _SHELL_ERRORS as exc:
            raise _translate(exc) from exc

    # ------------------------------------------------------------------
    # Product shell — Franchisee (separate resource; see module docstring)
    # ------------------------------------------------------------------

    @router.get("/shell/franchisee")
    def shell_franchisee(
        request: Request,
        store_id: str | None = Query(default=None, alias="storeId"),
        principal: Principal = Depends(require_franchisee_view_fn),  # noqa: B008
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return the franchisee-scoped view (no operator-only data)."""
        return shell.get_franchisee_view(
            subject_id=principal.subject_id,
            store_id=store_id,
            correlation_id=getattr(request.state, "correlation_id", None) or x_correlation_id,
        )

    @router.post(
        "/shell/franchisee/acknowledgement",
    )
    def shell_franchisee_acknowledge(
        body: FranchiseeAcknowledgeRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        principal: Principal = Depends(require_franchisee_write_fn),  # noqa: B008
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Record a franchisee acknowledgement. Audited and idempotent."""
        try:
            return shell.franchisee_acknowledge(
                notification_id=body.notificationId,
                subject_id=principal.subject_id,
                store_id=body.storeId,
                idempotency_key=idempotency_key,
                correlation_id=getattr(request.state, "correlation_id", None) or x_correlation_id,
            )
        except _SHELL_ERRORS as exc:
            raise _translate(exc) from exc

    @router.post(
        "/shell/franchisee/reports",
    )
    def shell_franchisee_report(
        body: FranchiseeReportRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        principal: Principal = Depends(require_franchisee_write_fn),  # noqa: B008
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Record a franchisee field report. Audited and idempotent."""
        try:
            return shell.franchisee_report(
                category=body.category,
                message=body.message,
                subject_id=principal.subject_id,
                store_id=body.storeId,
                idempotency_key=idempotency_key,
                correlation_id=getattr(request.state, "correlation_id", None) or x_correlation_id,
            )
        except _SHELL_ERRORS as exc:
            raise _translate(exc) from exc

    return router


__all__ = ["create_shell_sub_router"]
