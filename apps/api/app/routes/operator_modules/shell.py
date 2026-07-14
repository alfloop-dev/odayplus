"""Operator Console shell sub-router.

Owns: /operator/bootstrap, /operator/today
Not touching: issues, approvals, evidence, seed sub-routers
Composes with: create_operator_router() in operator.py
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request

from modules.opsboard.application.operator_state import OperatorStateService


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


def create_shell_sub_router(
    state_service: OperatorStateService,
    *,
    require_view_permission_fn: Callable[..., object],
) -> APIRouter:
    """Return the shell sub-router (bootstrap + today endpoints)."""
    router = APIRouter()

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
            )
        )

    return router


__all__ = ["create_shell_sub_router"]
