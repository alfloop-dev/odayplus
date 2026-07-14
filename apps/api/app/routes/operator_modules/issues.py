"""Operator Console issues sub-router.

Owns: GET /operator/issues, POST /operator/issues/{issue_id}/{action_type}
Not touching: approvals, evidence, seed, shell sub-routers
Composes with: create_operator_router() in operator.py
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header

from modules.opsboard.application.operator_state import OperatorStateService
from modules.opsboard.domain.r4_dtos import IssueTransitionRequest, IssueTransitionResponse


def create_issues_sub_router(
    state_service: OperatorStateService,
    require_permission_fn: Any,
) -> APIRouter:
    """Return the issues sub-router."""
    router = APIRouter()

    @router.get("/issues")
    def get_issues() -> dict[str, Any]:
        """List current work-queue issues."""
        items = state_service.get_work_queue()
        return {"items": items, "count": len(items)}

    @router.post("/issues/{issue_id}/{action_type}")
    def transition_issue(
        issue_id: str,
        action_type: str,
        body: IssueTransitionRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> IssueTransitionResponse:
        """Transition an issue through its workflow lifecycle.

        action_type: triage | assign | actions | outcome
        Requires reason when action_type is 'outcome'.
        Idempotency-Key header de-duplicates in-flight retries.
        """
        result = state_service.transition_issue(
            issue_id=issue_id,
            action_type=action_type,
            body=body,
            idempotency_key=idempotency_key,
            correlation_id=x_correlation_id,
        )
        return result

    return router


__all__ = ["create_issues_sub_router"]
