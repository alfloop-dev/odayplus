"""Operator Console API router — R4 modular composition.

This module is the assembly point for the Operator Console API.
It wires together the sub-routers from operator_modules/ and exposes
the single entry point create_operator_router() that oday_api/main.py
calls with prefix="/api/v1".

Sub-module ownership (R4):
  shell.py      → /operator/bootstrap, /operator/today
  issues.py     → /operator/issues, /operator/issues/{id}/{action}
  approvals.py  → /operator/approvals, /operator/approvals/{id}/decision
  evidence.py   → /operator/evidence/{id}/purpose
  seed.py       → /operator/seed/reset

State contract: all sub-routers share a single OperatorStateService instance
per application startup so writes from one route are immediately visible in
reads from another.  The service delegates to infrastructure.seed_data for
the canonical R4 seed.

Backward-compat note:
  The legacy flat DTOs (TransitionPayload, ApprovalDecisionPayload,
  EvidencePurposePayload) are kept as aliases to keep any callers that
  reference them directly from breaking.  Route handlers now use the R4 DTOs
  from modules.opsboard.domain.r4_dtos.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from modules.opsboard.application.operator_state import OperatorStateService
from modules.opsboard.domain.r4_dtos import (
    ApprovalDecisionRequest,
    EvidencePurposeRequest,
    IssueTransitionRequest,
)
from shared.audit import InMemoryAuditLog

# ---------------------------------------------------------------------------
# Legacy DTO aliases (backward compat — do not add new fields here)
# ---------------------------------------------------------------------------

class TransitionPayload(BaseModel):
    """Legacy alias — prefer IssueTransitionRequest in new code."""
    issueId: str | None = None
    status: str | None = None
    note: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None


class ApprovalDecisionPayload(BaseModel):
    """Legacy alias — prefer ApprovalDecisionRequest in new code."""
    status: str
    reason: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None


class EvidencePurposePayload(BaseModel):
    """Legacy alias — prefer EvidencePurposeRequest in new code."""
    purpose: str
    cameraLocation: str | None = None
    timeWindow: str | None = None
    retentionHours: int | None = None
    privacyAcknowledged: bool | None = None
    auditNote: str | None = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_operator_router(
    *,
    audit_log: InMemoryAuditLog | None = None,
    state_service: OperatorStateService | None = None,
) -> APIRouter:
    """Assemble the modular Operator Console API router.

    Imports sub-routers from operator_modules/ and wires them to a shared
    OperatorStateService instance.  All routes are registered under the
    /operator prefix (the API gateway adds /api/v1 externally).

    Parameters
    ----------
    audit_log:
        Optional shared InMemoryAuditLog for the authz engine.
    state_service:
        Optional pre-built OperatorStateService; injected by tests to pass
        a pre-seeded service with deterministic state.
    """
    from apps.api.oday_api.security.dependencies import build_engine, require_permission
    from shared.auth import Action

    active_audit_log = audit_log or InMemoryAuditLog()
    authz_engine = build_engine(audit_log=active_audit_log)

    # Shared state service — one instance per router lifetime.
    svc = state_service or OperatorStateService()

    router = APIRouter(prefix="/operator", tags=["operator"])

    # ------------------------------------------------------------------
    # Shell: read-only bootstrap / today
    # ------------------------------------------------------------------
    from apps.api.app.routes.operator_modules.shell import create_shell_sub_router
    router.include_router(create_shell_sub_router(svc))

    # ------------------------------------------------------------------
    # Issues: list + lifecycle transitions (write-guarded)
    # ------------------------------------------------------------------

    @router.get("/issues")
    def get_issues() -> dict[str, Any]:
        items = svc.get_work_queue()
        return {"items": items, "count": len(items)}

    @router.post(
        "/issues/{issue_id}/{action_type}",
        dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))],
    )
    def transition_issue(
        issue_id: str,
        action_type: str,
        body: IssueTransitionRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> Any:
        return svc.transition_issue(
            issue_id=issue_id,
            action_type=action_type,
            body=body,
            idempotency_key=idempotency_key,
            correlation_id=x_correlation_id,
        )

    # ------------------------------------------------------------------
    # Approvals: list + decision (write-guarded)
    # ------------------------------------------------------------------
    @router.get("/approvals")
    def get_approvals() -> dict[str, Any]:
        items = svc.get_approvals()
        return {"items": items, "count": len(items)}

    @router.post(
        "/approvals/{approval_id}/decision",
        dependencies=[Depends(require_permission("intervention", Action.APPROVE, engine=authz_engine))],
    )
    def decide_approval(
        approval_id: str,
        body: ApprovalDecisionRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> Any:
        return svc.decide_approval(
            approval_id=approval_id,
            body=body,
            idempotency_key=idempotency_key,
            correlation_id=x_correlation_id,
        )

    # ------------------------------------------------------------------
    # Evidence: purpose unlock (write-guarded)
    # ------------------------------------------------------------------
    @router.post(
        "/evidence/{evidence_id}/purpose",
        dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))],
    )
    def confirm_evidence_purpose(
        evidence_id: str,
        body: EvidencePurposeRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> Any:
        return svc.confirm_evidence_purpose(
            evidence_id=evidence_id,
            body=body,
            idempotency_key=idempotency_key,
            correlation_id=x_correlation_id,
        )

    # ------------------------------------------------------------------
    # Seed: deterministic reset (no auth guard — dev/test only)
    # ------------------------------------------------------------------
    @router.post("/seed/reset")
    def reset_seed(
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        svc.reset_to_seed()
        return {
            "status": "ok",
            "message": "Operator state reset to canonical R4 seed.",
            "correlation_id": x_correlation_id,
        }

    return router


__all__ = [
    "create_operator_router",
    # Legacy DTO exports kept for backward compat
    "TransitionPayload",
    "ApprovalDecisionPayload",
    "EvidencePurposePayload",
]
