"""Operator Console shell sub-router.

Owns: /operator/bootstrap, /operator/today
Not touching: issues, approvals, evidence, seed sub-routers
Composes with: create_operator_router() in operator.py
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Query

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


def create_shell_sub_router(state_service: OperatorStateService) -> APIRouter:
    """Return the shell sub-router (bootstrap + today endpoints)."""
    router = APIRouter()

    @router.get("/bootstrap")
    def bootstrap(
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return the full operator console bootstrap payload."""
        return state_service.get_today(
            **_context(
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )
        )

    @router.get("/today")
    def get_today(
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return today's operational snapshot (alias of bootstrap for FE compat)."""
        return state_service.get_today(
            **_context(
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )
        )

    @router.get("/search")
    def search(
        q: str = Query(default=""),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Return role-aware command/search results with API deep-link targets."""
        return state_service.search(
            q,
            **_context(
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )
        )

    return router


__all__ = ["create_shell_sub_router"]
