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
  network_listings.py → /operator/network-listings/*

State contract: all sub-routers share a single OperatorStateService instance
per application startup so writes from one route are immediately visible in
reads from another.  The service delegates to infrastructure.seed_data for
the canonical R4 seed.

Auth contract: write endpoints in issues, approvals, and evidence sub-routers
require the permission guard passed by create_operator_router().  The guard
is never optional — it is always wired at composition time, eliminating the
fail-open risk of orphaned sub-routers without auth.

Backward-compat note:
  The legacy flat DTOs (TransitionPayload, ApprovalDecisionPayload,
  EvidencePurposePayload) are kept as aliases to keep any callers that
  reference them directly from breaking.  Route handlers now use the R4 DTOs
  from modules.opsboard.domain.r4_dtos.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from modules.opsboard.application.growth import GrowthService
from modules.opsboard.application.operator_state import OperatorStateService
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
    document_store: Any | None = None,
    state_service: OperatorStateService | None = None,
    growth_service: GrowthService | None = None,
) -> APIRouter:
    """Assemble the modular Operator Console API router.

    Imports sub-routers from operator_modules/ and wires them to a shared
    OperatorStateService instance.  All routes are registered under the
    /operator prefix (the API gateway adds /api/v1 externally).

    Auth guards for write endpoints are resolved here and passed into each
    sub-router that owns write paths.  Sub-routers never bypass auth.

    Parameters
    ----------
    audit_log:
        Optional shared InMemoryAuditLog for the authz engine.
    document_store:
        Accepted for app-level composition compatibility; the R4 operator
        routes do not read from the document store.
    state_service:
        Optional pre-built OperatorStateService; injected by tests to pass
        a pre-seeded service with deterministic state.
    """
    _ = document_store
    from apps.api.oday_api.security.dependencies import build_engine, require_permission
    from shared.auth import Action

    active_audit_log = audit_log or InMemoryAuditLog()
    authz_engine = build_engine(audit_log=active_audit_log)

    # Shared state service — one instance per router lifetime.
    svc = state_service or OperatorStateService()

    router = APIRouter(prefix="/operator", tags=["operator"])

    # ------------------------------------------------------------------
    # Import sub-routers from operator_modules/ — the only composition
    # path.  Inline route re-definitions are forbidden to keep this file
    # as the single wiring point.
    # ------------------------------------------------------------------
    from apps.api.app.routes.operator_modules.approvals import create_approvals_sub_router
    from apps.api.app.routes.operator_modules.evidence import create_evidence_sub_router
    from apps.api.app.routes.operator_modules.growth import create_growth_sub_router
    from apps.api.app.routes.operator_modules.issues import create_issues_sub_router
    from apps.api.app.routes.operator_modules.network_listings import (
        create_network_listings_sub_router,
    )
    from apps.api.app.routes.operator_modules.network_scoring import (
        create_network_scoring_sub_router,
    )
    from apps.api.app.routes.operator_modules.seed import create_seed_sub_router
    from apps.api.app.routes.operator_modules.shell import create_shell_sub_router
    from modules.opsboard.application.network_listings import NetworkListingService
    from modules.opsboard.application.network_scoring import NetworkScoringService

    # Shell — read-only, no permission guard needed.
    router.include_router(create_shell_sub_router(svc))

    # Network listing intake — read/write paths for R4 Listing Radar.
    router.include_router(
        create_network_listings_sub_router(
            NetworkListingService(),
            require_view_permission_fn=require_permission(
                "listing", Action.VIEW, engine=authz_engine
            ),
            require_write_permission_fn=require_permission(
                "listing", Action.UPDATE, engine=authz_engine
            ),
        )
    )

    # Network SiteScore scoring — read/write paths for R4 Candidate gate,
    # SiteScore job, and Compare recommendation. Missing-data candidates are
    # blocked server-side (422) by the service gate.
    router.include_router(
        create_network_scoring_sub_router(
            NetworkScoringService(),
            require_view_permission_fn=require_permission(
                "sitescore", Action.VIEW, engine=authz_engine
            ),
            require_write_permission_fn=require_permission(
                "sitescore", Action.EXECUTE, engine=authz_engine
            ),
        )
    )

    # Issues — write endpoint requires intervention CREATE guard.
    router.include_router(
        create_issues_sub_router(
            svc,
            require_permission_fn=require_permission(
                "intervention", Action.CREATE, engine=authz_engine
            ),
        )
    )

    # Approvals — decision endpoint requires intervention APPROVE guard.
    router.include_router(
        create_approvals_sub_router(
            svc,
            require_permission_fn=require_permission(
                "intervention", Action.APPROVE, engine=authz_engine
            ),
        )
    )

    # Evidence — purpose unlock requires intervention CREATE guard.
    router.include_router(
        create_evidence_sub_router(
            svc,
            require_permission_fn=require_permission(
                "intervention", Action.CREATE, engine=authz_engine
            ),
        )
    )

    # Seed — deterministic reset for tests and dev (no auth guard).
    router.include_router(create_seed_sub_router(svc))

    # Growth — read-only endpoints open; write endpoints require intervention CREATE guard.
    growth_svc = growth_service or GrowthService()
    router.include_router(
        create_growth_sub_router(
            growth_svc,
            require_permission_fn=require_permission(
                "intervention", Action.CREATE, engine=authz_engine
            ),
        )
    )

    # Govern — aggregation snapshot open; decisions require APPROVE, evidence
    # export requires CREATE.  Shares the Growth service so live Growth
    # decisions/approvals surface in the Govern snapshot.
    from apps.api.app.routes.operator_modules.governance import (
        create_governance_sub_router,
    )
    from modules.opsboard.application.governance import GovernanceService

    router.include_router(
        create_governance_sub_router(
            GovernanceService(growth_service=growth_svc),
            require_decision_permission_fn=require_permission(
                "intervention", Action.APPROVE, engine=authz_engine
            ),
            require_export_permission_fn=require_permission(
                "intervention", Action.CREATE, engine=authz_engine
            ),
        )
    )

    return router


__all__ = [
    "create_operator_router",
    # Legacy DTO exports kept for backward compat
    "TransitionPayload",
    "ApprovalDecisionPayload",
    "EvidencePurposePayload",
    # Re-exported for test convenience
    "GrowthService",
]
