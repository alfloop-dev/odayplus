"""Operator Console approvals sub-router.

Owns: GET /operator/approvals, POST /operator/approvals/{approval_id}/decision
Not touching: issues, evidence, seed, shell sub-routers
Composes with: create_operator_router() in operator.py — caller must pass
    require_permission_fn so the decision endpoint carries the correct auth guard.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, Request

from modules.opsboard.application.operator_state import OperatorStateService
from modules.opsboard.domain.r4_dtos import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
)


def _read_context(
    request: Request,
    *,
    x_operator_role: str | None,
    x_subject_id: str | None,
    x_roles: str | None,
) -> dict[str, str | None]:
    return {
        "role_id": getattr(request.state, "operator_role_id", None) or x_operator_role,
        "subject_id": getattr(request.state, "operator_subject_id", None) or x_subject_id,
        "system_roles": getattr(request.state, "operator_system_roles", None) or x_roles,
    }


def create_approvals_sub_router(
    state_service: OperatorStateService,
    require_view_permission_fn: Callable[..., Any],
    require_write_permission_fn: Callable[..., Any],
) -> APIRouter:
    """Return the approvals sub-router.

    Parameters
    ----------
    state_service:
        Shared OperatorStateService instance.
    require_view_permission_fn:
        Operator Console read guard.
    require_write_permission_fn:
        A callable that returns a FastAPI dependency for the write guard.
        Typically ``require_operator_permission("intervention", Action.APPROVE, engine=...)``.
    """
    router = APIRouter()

    @router.get("/approvals", dependencies=[Depends(require_view_permission_fn)])
    def get_approvals(
        request: Request,
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
    ) -> dict[str, Any]:
        """List pending approval decisions."""
        items = state_service.get_approvals(
            **_read_context(
                request,
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
            )
        )
        return {"items": items, "count": len(items)}

    @router.post(
        "/approvals/{approval_id}/decision",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def decide_approval(
        approval_id: str,
        body: ApprovalDecisionRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> ApprovalDecisionResponse:
        """Record an approval decision.

        Body must include status (approved|returned|rejected) and reason.
        High-risk approvals require a non-empty reason field.
        Idempotency-Key header de-duplicates concurrent retries.
        """
        result = state_service.decide_approval(
            approval_id=approval_id,
            body=body,
            idempotency_key=idempotency_key,
            correlation_id=x_correlation_id,
        )
        return result

    return router


__all__ = ["create_approvals_sub_router"]
