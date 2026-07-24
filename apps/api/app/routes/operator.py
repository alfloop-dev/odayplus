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
  network_scoring.py → /operator/network-scoring/*
  network_rebalance.py → /operator/network-rebalance/*

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

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
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
    listing_repository: Any | None = None,
    evidence_store: Any | None = None,
    intake_repository: Any | None = None,
    require_live_data: bool = False,
    persistence_mode: str = "memory",
    provider_mode: str = "fixture",
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
        Optional durable document store. When present it backs the assisted
        listing intake repository, so intakes and their idempotent write
        replays survive a restart; when absent the intake state is in-memory.
    state_service:
        Optional pre-built OperatorStateService; injected by tests to pass
        a pre-seeded service with deterministic state.
    """
    from apps.api.oday_api.security.dependencies import (
        OPERATOR_CONSOLE_RESOURCE,
        build_engine,
        require_operator_permission,
        require_permission,
    )
    from shared.auth import Action

    active_audit_log = audit_log or InMemoryAuditLog()
    authz_engine = build_engine(audit_log=active_audit_log)

    # Shared state service — one instance per router lifetime. There is no live
    # operator-state repository in this composition root yet, so the
    # require-live branch must not reuse an injected fixture service.
    svc = (
        OperatorStateService(
            require_live_data=True,
            persistence_mode=persistence_mode,
            provider_mode=provider_mode,
        )
        if require_live_data
        else state_service
        or OperatorStateService(
            persistence_mode=persistence_mode,
            provider_mode=provider_mode,
        )
    )

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
    from apps.api.app.routes.operator_modules.network_rebalance import (
        create_network_rebalance_sub_router,
    )
    from apps.api.app.routes.operator_modules.network_reviews import (
        create_network_review_sub_router,
    )
    from apps.api.app.routes.operator_modules.network_scoring import (
        create_network_scoring_sub_router,
    )
    from apps.api.app.routes.operator_modules.seed import create_seed_sub_router
    from apps.api.app.routes.operator_modules.shell import create_shell_sub_router
    from modules.opsboard.application.network_listings import NetworkListingService
    from modules.opsboard.application.network_rebalance import NetworkRebalanceService
    from modules.opsboard.application.network_reviews import NetworkReviewService
    from modules.opsboard.application.network_scoring import NetworkScoringService
    from modules.opsboard.application.shell import ShellService
    from shared.infrastructure.persistence.operator_network_listings import (
        DurableAssistedIntakeRepository,
    )
    from shared.infrastructure.persistence.operator_shell import DurableShellRepository

    operator_view_guard = require_operator_permission(
        OPERATOR_CONSOLE_RESOURCE, Action.VIEW, engine=authz_engine
    )
    operator_write_guard = require_operator_permission(
        OPERATOR_CONSOLE_RESOURCE, Action.UPDATE, engine=authz_engine
    )

    if require_live_data:
        # A live operator repository is not implemented in this codebase. Keep
        # the read envelope available so clients can inspect provenance and
        # readiness, but never mount fixture-backed product modules or seed
        # mutation routes. Any other operator operation fails closed.
        def _context(
            request: Request,
            *,
            x_operator_role: str | None,
            x_subject_id: str | None,
            x_roles: str | None,
            x_correlation_id: str | None,
        ) -> dict[str, str | None]:
            return {
                "role_id": getattr(request.state, "operator_role_id", None)
                or x_operator_role,
                "subject_id": getattr(request.state, "operator_subject_id", None)
                or x_subject_id,
                "system_roles": getattr(request.state, "operator_system_roles", None)
                or x_roles,
                "correlation_id": getattr(request.state, "correlation_id", None)
                or x_correlation_id,
            }

        @router.get("/bootstrap", dependencies=[Depends(operator_view_guard)])
        @router.get("/today", dependencies=[Depends(operator_view_guard)])
        def unavailable_envelope(
            request: Request,
            x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
            x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
            x_roles: str | None = Header(default=None, alias="X-Roles"),
            x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
        ) -> dict[str, Any]:
            return svc.get_today(
                **_context(
                    request,
                    x_operator_role=x_operator_role,
                    x_subject_id=x_subject_id,
                    x_roles=x_roles,
                    x_correlation_id=x_correlation_id,
                )
            )

        @router.get("/search", dependencies=[Depends(operator_view_guard)])
        def unavailable_search(
            request: Request,
            q: str = Query(default=""),
            x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
            x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
            x_roles: str | None = Header(default=None, alias="X-Roles"),
            x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
        ) -> dict[str, Any]:
            return svc.search(
                q,
                **_context(
                    request,
                    x_operator_role=x_operator_role,
                    x_subject_id=x_subject_id,
                    x_roles=x_roles,
                    x_correlation_id=x_correlation_id,
                ),
            )

        @router.post(
            "/seed/reset",
            dependencies=[Depends(operator_view_guard)],
            include_in_schema=False,
        )
        def seed_reset_disabled() -> None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Operator seed/reset is disabled because live data is required."
                ),
            )

        return router

    # Shell — protected read envelope plus the product-shell surface.
    #
    # The franchisee guards use require_permission (not the operator variant):
    # Role.FRANCHISEE maps to no Operator Console role, so the operator factory
    # would deny every franchisee at operator.role before RBAC ever ran.
    router.include_router(
        create_shell_sub_router(
            svc,
            require_view_permission_fn=operator_view_guard,
            require_write_permission_fn=operator_write_guard,
            require_admin_permission_fn=operator_write_guard,
            require_franchisee_view_fn=require_permission(
                "franchisee_portal", Action.VIEW, engine=authz_engine
            ),
            require_franchisee_write_fn=require_permission(
                "franchisee_portal", Action.CREATE, engine=authz_engine
            ),
            shell_service=ShellService(
                svc,
                repository=(
                    DurableShellRepository(document_store)
                    if document_store is not None
                    else None
                ),
            ),
        )
    )

    from modules.opsboard.application.network_listings import (
        InMemoryAssistedIntakeRepository,
    )

    shared_intake_repo = intake_repository or (
        DurableAssistedIntakeRepository(document_store)
        if document_store is not None
        else InMemoryAssistedIntakeRepository()
    )

    # Network listing intake — read/write paths for R4 Listing Radar.
    router.include_router(
        create_network_listings_sub_router(
            NetworkListingService(
                listing_repository=listing_repository,
                intake_repository=shared_intake_repo,
            ),
            require_view_permission_fn=require_operator_permission(
                "listing", Action.VIEW, engine=authz_engine
            ),
            require_write_permission_fn=require_operator_permission(
                "listing", Action.UPDATE, engine=authz_engine
            ),
            audit_log=active_audit_log,
        )
    )

    # Network SiteScore scoring — read/write paths for R4 Candidate gate,
    # SiteScore job, and Compare recommendation. Missing-data candidates are
    # blocked server-side (422) by the service gate.
    router.include_router(
        create_network_scoring_sub_router(
            NetworkScoringService(),
            require_view_permission_fn=require_operator_permission(
                "sitescore", Action.VIEW, engine=authz_engine
            ),
            require_write_permission_fn=require_operator_permission(
                "sitescore", Action.EXECUTE, engine=authz_engine
            ),
        )
    )

    # Network Review decision — read open to viewers; the decide endpoint
    # requires sitescore APPROVE, which Site Reviewer / Executive hold but
    # Expansion does not. That is the "Expansion may submit but not decide"
    # rule enforced at the HTTP boundary; the service adds a defense-in-depth
    # allowlist. Candidate/Review/Approval/Decision/Audit sync atomically.
    router.include_router(
        create_network_review_sub_router(
            NetworkReviewService(),
            require_view_permission_fn=require_operator_permission(
                "sitescore", Action.VIEW, engine=authz_engine
            ),
            require_decide_permission_fn=require_operator_permission(
                "sitescore", Action.APPROVE, engine=authz_engine
            ),
        )
    )

    # Network rebalance — AVM job, NetPlan three-case solve, Govern approval boundary.
    router.include_router(
        create_network_rebalance_sub_router(
            NetworkRebalanceService(
                govern_approval_writer=svc.upsert_network_rebalance_approval
            ),
            require_view_permission_fn=require_operator_permission(
                "listing", Action.VIEW, engine=authz_engine
            ),
            require_write_permission_fn=require_operator_permission(
                "listing", Action.UPDATE, engine=authz_engine
            ),
            reset_govern_fn=svc.reset_to_seed,
        )
    )

    # Issues — write endpoint requires intervention CREATE guard.
    router.include_router(
        create_issues_sub_router(
            svc,
            require_view_permission_fn=operator_view_guard,
            require_write_permission_fn=require_operator_permission(
                "intervention", Action.CREATE, engine=authz_engine
            ),
        )
    )

    # Approvals — decision endpoint requires intervention APPROVE guard.
    router.include_router(
        create_approvals_sub_router(
            svc,
            require_view_permission_fn=operator_view_guard,
            require_write_permission_fn=require_operator_permission(
                "intervention", Action.APPROVE, engine=authz_engine
            ),
        )
    )

    # Evidence — purpose unlock requires intervention CREATE guard.
    router.include_router(
        create_evidence_sub_router(
            svc,
            require_permission_fn=require_operator_permission(
                "intervention", Action.CREATE, engine=authz_engine
            ),
        )
    )

    # Seed — deterministic reset for tests/dev, still protected by Operator auth.
    router.include_router(
        create_seed_sub_router(
            svc,
            require_reset_permission_fn=require_operator_permission(
                OPERATOR_CONSOLE_RESOURCE, Action.UPDATE, engine=authz_engine
            ),
        )
    )

    # Growth — reads require Operator Console view, writes require intervention CREATE.
    growth_svc = growth_service or GrowthService()
    router.include_router(
        create_growth_sub_router(
            growth_svc,
            require_view_permission_fn=operator_view_guard,
            require_permission_fn=require_operator_permission(
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
            require_view_permission_fn=operator_view_guard,
            require_decision_permission_fn=require_operator_permission(
                "intervention", Action.APPROVE, engine=authz_engine
            ),
            require_export_permission_fn=require_operator_permission(
                "intervention", Action.CREATE, engine=authz_engine
            ),
        )
    )

    # Privacy — purge, legal hold, evidence export and WORM integrity
    from apps.api.app.routes.operator_modules.privacy import (
        create_privacy_sub_router,
    )
    from modules.listing.application.intake_privacy import IntakePrivacyService

    privacy_service = IntakePrivacyService(
        audit_log=active_audit_log,
        evidence_store=evidence_store,
        document_store=document_store,
        intake_repository=shared_intake_repo,
    )
    router.include_router(
        create_privacy_sub_router(
            privacy_service,
            require_view_permission_fn=operator_view_guard,
            require_write_permission_fn=operator_write_guard,
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
