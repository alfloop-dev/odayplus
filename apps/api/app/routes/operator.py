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

from typing import Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel

from modules.opsboard.application.growth import GrowthService
from modules.opsboard.application.operator_live_repository import (
    OperatorLiveRepositoryError,
    OperatorLiveRepositoryProtocol,
)
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


def _live_operator_request_context(
    request: Request,
    *,
    x_operator_role: str | None = None,
    x_subject_id: str | None = None,
    x_roles: str | None = None,
    x_correlation_id: str | None = None,
) -> dict[str, Any]:
    """Build live read scope only from the verified request principal."""

    principal = getattr(request.state, "operator_principal", None)
    scope = getattr(principal, "scope", None)
    return {
        "role_id": getattr(request.state, "operator_role_id", None) or x_operator_role,
        "subject_id": getattr(request.state, "operator_subject_id", None) or x_subject_id,
        "system_roles": getattr(request.state, "operator_system_roles", None) or x_roles,
        "correlation_id": getattr(request.state, "correlation_id", None) or x_correlation_id,
        "tenant_id": getattr(scope, "tenant_id", None),
        "brand_ids": tuple(getattr(scope, "brand_ids", ()) or ()),
        "region_ids": tuple(getattr(scope, "region_ids", ()) or ()),
        "store_ids": tuple(getattr(scope, "store_ids", ()) or ()),
    }


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
    listing_repository_for_tenant: Any | None = None,
    sitescore_repository_for_tenant: Any | None = None,
    sitescore_decision_repository_for_tenant: Any | None = None,
    avm_repository_for_tenant: Any | None = None,
    netplan_repository_for_tenant: Any | None = None,
    priceops_repository_for_tenant: Any | None = None,
    model_runtime: Any | None = None,
    avm_production_executor: Any | None = None,
    netplan_production_executor: Any | None = None,
    evidence_store: Any | None = None,
    intake_repository: Any | None = None,
    live_repository: OperatorLiveRepositoryProtocol | None = None,
    require_live_data: bool = False,
    persistence_mode: str = "memory",
    provider_mode: str = "fixture",
    allow_test_reset: bool = False,
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
        OPERATOR_TENANT_ID,
        build_engine,
        require_operator_permission,
        require_permission,
    )
    from shared.auth import (
        AccessRequest,
        Action,
        Environment,
        Principal,
        ResourceDescriptor,
    )

    active_audit_log = audit_log or InMemoryAuditLog()
    authz_engine = build_engine(audit_log=active_audit_log)

    # Shared state service — one instance per router lifetime. A live-required
    # router accepts only a state service backed by the injected live
    # repository; fixture services can never cross this composition boundary.
    effective_require_live_data = require_live_data or (live_repository is not None)
    if effective_require_live_data:
        svc = (
            state_service
            if state_service is not None and state_service.live_repository is not None
            else OperatorStateService(
                require_live_data=True,
                persistence_mode=persistence_mode,
                provider_mode=provider_mode,
                live_repository=live_repository,
            )
        )
    else:
        svc = state_service or OperatorStateService(
            persistence_mode=persistence_mode,
            provider_mode=provider_mode,
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
        OPERATOR_CONSOLE_RESOURCE,
        Action.VIEW,
        tenant_id=None if effective_require_live_data else OPERATOR_TENANT_ID,
        engine=authz_engine,
    )
    operator_write_guard = require_operator_permission(
        OPERATOR_CONSOLE_RESOURCE,
        Action.UPDATE,
        tenant_id=None if effective_require_live_data else OPERATOR_TENANT_ID,
        engine=authz_engine,
    )

    def authorize_franchisee_store(
        request: Request,
        principal: Principal,
        action: Action,
        requested_store_id: str | None,
    ) -> str:
        """Resolve a verified store and enforce object-level franchisee ABAC."""

        requested = (requested_store_id or "").strip() or None
        scoped_stores = sorted(
            {
                str(store_id).strip()
                for store_id in principal.scope.store_ids
                if str(store_id).strip()
            }
        )
        # The sentinel deliberately fails franchisee ABAC when no verified
        # store grant exists, instead of reviving the old STORE-001 default.
        missing_store_scope = requested is None and not scoped_stores
        effective_store = requested or (
            scoped_stores[0] if scoped_stores else "__missing_store_scope__"
        )
        access = AccessRequest(
            principal=principal,
            action=action,
            resource=ResourceDescriptor(
                type="franchisee_portal",
                resource_id=effective_store,
                tenant_id=principal.scope.tenant_id,
                store_id=effective_store,
            ),
            environment=Environment(
                source_ip=request.client.host if request.client else None,
                attributes={
                    "correlation_id": (
                        getattr(request.state, "correlation_id", None) or "unknown"
                    )
                },
            ),
        )
        decision = authz_engine.authorize(access)
        if not decision.allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=decision.reason,
            )
        if missing_store_scope:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="storeId is required when the principal has no scoped store",
            )
        return effective_store

    if effective_require_live_data:
        # Production exposes only read surfaces backed by the live repository.
        # Seed reset and modules whose services still own process-local state
        # are not mounted at all.
        def _context(
            request: Request,
            *,
            x_operator_role: str | None,
            x_subject_id: str | None,
            x_roles: str | None,
            x_correlation_id: str | None,
        ) -> dict[str, Any]:
            return _live_operator_request_context(
                request,
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )

        def _live_read(operation: str, method: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                return method(*args, **kwargs)
            except OperatorLiveRepositoryError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "OPERATOR_LIVE_DATA_UNAVAILABLE",
                        "operation": operation,
                        "message": str(exc),
                    },
                ) from exc

        def _shell_unavailable(
            operation: str,
            dependency: str,
            *,
            message: str | None = None,
        ) -> None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "OPERATOR_SHELL_CONTRACT_UNAVAILABLE",
                    "operation": operation,
                    "dependency": dependency,
                    "state": "unavailable",
                    "reasonCode": "TENANT_BOUND_DURABLE_SHELL_NOT_WIRED",
                    "message": message
                    or (
                        f"{dependency} has no tenant-bound durable production "
                        "repository wiring"
                    ),
                },
            )

        @router.get("/bootstrap", dependencies=[Depends(operator_view_guard)])
        @router.get("/today", dependencies=[Depends(operator_view_guard)])
        def live_envelope(
            request: Request,
            x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
            x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
            x_roles: str | None = Header(default=None, alias="X-Roles"),
            x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
        ) -> dict[str, Any]:
            return _live_read(
                "operator.envelope",
                svc.get_today,
                **_context(
                    request,
                    x_operator_role=x_operator_role,
                    x_subject_id=x_subject_id,
                    x_roles=x_roles,
                    x_correlation_id=x_correlation_id,
                ),
            )

        @router.get("/search", dependencies=[Depends(operator_view_guard)])
        def live_search(
            request: Request,
            q: str = Query(default=""),
            x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
            x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
            x_roles: str | None = Header(default=None, alias="X-Roles"),
            x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
        ) -> dict[str, Any]:
            return _live_read(
                "operator.search",
                svc.search,
                q,
                **_context(
                    request,
                    x_operator_role=x_operator_role,
                    x_subject_id=x_subject_id,
                    x_roles=x_roles,
                    x_correlation_id=x_correlation_id,
                ),
            )

        shell_document_store = document_store

        from modules.opsboard.application.shell import (
            ShellService,
            TenantBoundOperatorStateService,
        )
        from shared.infrastructure.persistence.operator_domains import (
            TenantScopedDocumentStore,
        )
        from shared.infrastructure.persistence.operator_shell import (
            DurableShellRepository,
        )

        class _LiveShellServiceAdapter:
            """Translate live read-model outages without changing shell policy."""

            def __init__(self, service: ShellService) -> None:
                self._service = service

            def __getattr__(self, name: str) -> Any:
                method = getattr(self._service, name)

                def invoke(*args: Any, **kwargs: Any) -> Any:
                    try:
                        return method(*args, **kwargs)
                    except OperatorLiveRepositoryError as exc:
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail={
                                "code": "OPERATOR_LIVE_DATA_UNAVAILABLE",
                                "operation": f"operator.shell.{name}",
                                "message": str(exc),
                            },
                        ) from exc

                return invoke

        def live_shell_service_for_request(
            request: Request,
            effective_store_id: str | None = None,
        ) -> ShellService:
            context = _live_operator_request_context(request)
            tenant_id = str(context["tenant_id"] or "").strip()
            if svc.live_repository is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "OPERATOR_LIVE_DATA_UNAVAILABLE",
                        "operation": "operator.shell",
                        "message": "Operator live repository is not configured",
                    },
                )
            if shell_document_store is None:
                _shell_unavailable(
                    "operator.shell.persistence",
                    "operator_shell_document_store",
                )
            if not tenant_id:
                _shell_unavailable(
                    "operator.shell.scope",
                    "operator_shell_tenant_scope",
                    message="verified tenant scope is required for the production shell",
                )
            scoped_state = TenantBoundOperatorStateService(
                svc,
                tenant_id=tenant_id,
                brand_ids=cast(tuple[str, ...], context["brand_ids"]),
                region_ids=cast(tuple[str, ...], context["region_ids"]),
                store_ids=(
                    (effective_store_id,)
                    if effective_store_id is not None
                    else cast(tuple[str, ...], context["store_ids"])
                ),
            )
            shell = ShellService(
                scoped_state,
                repository=DurableShellRepository(
                    TenantScopedDocumentStore(shell_document_store, tenant_id)
                ),
            )
            return cast(ShellService, _LiveShellServiceAdapter(shell))

        franchisee_view_guard = require_permission(
            "franchisee_portal",
            Action.VIEW,
            engine=authz_engine,
        )
        franchisee_write_guard = require_permission(
            "franchisee_portal",
            Action.CREATE,
            engine=authz_engine,
        )
        router.include_router(
            create_shell_sub_router(
                svc,
                require_view_permission_fn=operator_view_guard,
                require_write_permission_fn=operator_write_guard,
                require_admin_permission_fn=operator_write_guard,
                require_franchisee_view_fn=franchisee_view_guard,
                require_franchisee_write_fn=franchisee_write_guard,
                authorize_franchisee_store_fn=authorize_franchisee_store,
                shell_service_resolver=live_shell_service_for_request,
                include_legacy_reads=False,
            )
        )

        if document_store is None:

            class _UnavailableOperatorDomainStore:
                @property
                def engine(self) -> None:
                    return None

                def __getattr__(self, _name: str) -> Any:
                    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
                        from fastapi import HTTPException, status

                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail={
                                "code": "OPERATOR_DOMAIN_PERSISTENCE_UNAVAILABLE",
                                "message": (
                                    "live Operator domain routes require an "
                                    "injected durable document store"
                                ),
                            },
                        )

                    return unavailable

            document_store = _UnavailableOperatorDomainStore()

        from apps.api.app.routes.operator_modules.governance import (
            create_governance_sub_router,
        )
        from apps.api.app.routes.operator_modules.live_service import (
            DurableTenantServiceResolver,
        )
        from modules.opsboard.application.governance import GovernanceService
        from shared.infrastructure.persistence.operator_domains import (
            DurableOperatorDomainStateRepository,
        )
        from shared.workflow.sitescore import SiteScoreDecisionWorkflow

        def require_tenant_repository(
            provider: Any | None,
            dependency: str,
            tenant_id: str,
        ) -> Any:
            if provider is None:
                from fastapi import HTTPException, status

                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "OPERATOR_CANONICAL_DEPENDENCY_UNAVAILABLE",
                        "dependency": dependency,
                        "message": (f"live Operator route requires tenant-aware {dependency}"),
                    },
                )
            repository = provider(tenant_id)
            if repository is None:
                from fastapi import HTTPException, status

                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "OPERATOR_CANONICAL_DEPENDENCY_UNAVAILABLE",
                        "dependency": dependency,
                        "message": (
                            f"tenant-aware {dependency} is not configured for tenant {tenant_id}"
                        ),
                    },
                )
            return repository

        def require_model_runtime() -> Any:
            if model_runtime is None:
                from fastapi import HTTPException, status

                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "SITESCORE_RUNTIME_UNAVAILABLE",
                        "dependency": "model_runtime",
                        "message": (
                            "live Operator SiteScore requires the canonical "
                            "MLflow production runtime"
                        ),
                    },
                )
            return model_runtime

        listing_state_repository = DurableOperatorDomainStateRepository(
            document_store,
            "network-listings",
        )
        scoring_state_repository = DurableOperatorDomainStateRepository(
            document_store,
            "network-scoring",
        )
        review_state_repository = DurableOperatorDomainStateRepository(
            document_store,
            "network-reviews",
        )
        rebalance_state_repository = DurableOperatorDomainStateRepository(
            document_store,
            "network-rebalance",
        )
        growth_state_repository = DurableOperatorDomainStateRepository(
            document_store,
            "growth",
        )
        governance_state_repository = DurableOperatorDomainStateRepository(
            document_store,
            "governance",
        )

        listing_resolver = DurableTenantServiceResolver(
            listing_state_repository,
            factory=lambda state, tenant_id: NetworkListingService(
                listing_repository=require_tenant_repository(
                    listing_repository_for_tenant,
                    "listing_repository",
                    tenant_id,
                ),
                intake_repository=DurableAssistedIntakeRepository(
                    TenantScopedDocumentStore(document_store, tenant_id)
                ),
                initial_state=state,
                # Canonical fixture state may exist only behind the explicit
                # test-reset gate (ODP_E2E_MODE): production keeps the durable
                # listing aggregate fail-closed empty until real intake writes.
                seed_fixtures=allow_test_reset,
            ),
            exporter=lambda service: service.export_state(),
            mutating_methods={
                "reset",
                "convert_listing",
                "merge_listing",
                "archive_listing",
                "submit_intake",
                "correct_intake",
                "decide_intake",
                "retry_intake",
                "promote_intake",
            },
        )
        scoring_resolver = DurableTenantServiceResolver(
            scoring_state_repository,
            factory=lambda state, tenant_id: NetworkScoringService(
                initial_state=state,
                seed_fixtures=False,
                listing_repository=require_tenant_repository(
                    listing_repository_for_tenant,
                    "listing_repository",
                    tenant_id,
                ),
                sitescore_repository=require_tenant_repository(
                    sitescore_repository_for_tenant,
                    "sitescore_repository",
                    tenant_id,
                ),
                model_runtime=require_model_runtime(),
                require_canonical=True,
                tenant_id=tenant_id,
            ),
            exporter=lambda service: service.export_state(),
            mutating_methods={
                "reset",
                "score_candidate",
                "score_batch",
                "set_compare_set",
            },
        )
        review_resolver = DurableTenantServiceResolver(
            review_state_repository,
            factory=lambda state, tenant_id: (
                lambda decision_repository, sitescore_repository: NetworkReviewService(
                    initial_state=state,
                    seed_fixtures=False,
                    decision_repository=decision_repository,
                    sitescore_repository=sitescore_repository,
                    decision_workflow=SiteScoreDecisionWorkflow(
                        audit_log=active_audit_log,
                        store=decision_repository,
                    ),
                    require_canonical=True,
                )
            )(
                require_tenant_repository(
                    sitescore_decision_repository_for_tenant,
                    "sitescore_decision_repository",
                    tenant_id,
                ),
                require_tenant_repository(
                    sitescore_repository_for_tenant,
                    "sitescore_repository",
                    tenant_id,
                ),
            ),
            exporter=lambda service: service.export_state(),
            mutating_methods={"reset", "decide_review"},
        )

        def write_governance_approval(
            tenant_id: str,
            approval: dict[str, Any],
        ) -> dict[str, Any]:
            growth_state = growth_state_repository.load(tenant_id)
            governance = GovernanceService(
                growth_service=GrowthService(
                    initial_state=growth_state,
                    seed_fixtures=False,
                ),
                initial_state=governance_state_repository.load(tenant_id),
                seed_fixtures=False,
            )
            result = governance.upsert_approval(approval)
            governance_state_repository.save(
                tenant_id,
                governance.export_state(),
            )
            return result

        rebalance_resolver = DurableTenantServiceResolver(
            rebalance_state_repository,
            factory=lambda state, tenant_id: NetworkRebalanceService(
                govern_approval_writer=lambda approval: write_governance_approval(
                    tenant_id,
                    approval,
                ),
                initial_state=state,
                seed_fixtures=False,
                avm_repository=require_tenant_repository(
                    avm_repository_for_tenant,
                    "avm_repository",
                    tenant_id,
                ),
                netplan_repository=require_tenant_repository(
                    netplan_repository_for_tenant,
                    "netplan_repository",
                    tenant_id,
                ),
                avm_production_executor=avm_production_executor,
                netplan_production_executor=netplan_production_executor,
                runtime_mode="production",
                tenant_id=tenant_id,
                require_canonical=True,
            ),
            exporter=lambda service: service.export_state(),
            mutating_methods={
                "reset",
                "request_avm",
                "complete_avm",
                "solve_netplan",
                "select_scenario",
                "submit_review",
            },
        )
        growth_resolver = DurableTenantServiceResolver(
            growth_state_repository,
            factory=lambda state, tenant_id: GrowthService(
                initial_state=state,
                audit_log=active_audit_log,
                seed_fixtures=False,
                priceops_repository=require_tenant_repository(
                    priceops_repository_for_tenant,
                    "priceops_repository",
                    tenant_id,
                ),
                tenant_id=tenant_id,
                require_canonical=True,
            ),
            exporter=lambda service: service.export_state(),
            mutating_methods={
                "create_action",
                "transition_action",
                "write_outcome",
                "submit_for_approval",
                "resolve_approval",
                "reset_to_seed",
            },
        )

        def governance_factory(
            state: dict[str, Any] | None,
            tenant_id: str,
        ) -> GovernanceService:
            return GovernanceService(
                growth_service=GrowthService(
                    initial_state=growth_state_repository.load(tenant_id),
                    audit_log=active_audit_log,
                    seed_fixtures=False,
                    priceops_repository=require_tenant_repository(
                        priceops_repository_for_tenant,
                        "priceops_repository",
                        tenant_id,
                    ),
                    tenant_id=tenant_id,
                    require_canonical=True,
                ),
                initial_state=state,
                seed_fixtures=False,
                sitescore_decision_repository=require_tenant_repository(
                    sitescore_decision_repository_for_tenant,
                    "sitescore_decision_repository",
                    tenant_id,
                ),
                avm_repository=require_tenant_repository(
                    avm_repository_for_tenant,
                    "avm_repository",
                    tenant_id,
                ),
                netplan_repository=require_tenant_repository(
                    netplan_repository_for_tenant,
                    "netplan_repository",
                    tenant_id,
                ),
                priceops_repository=require_tenant_repository(
                    priceops_repository_for_tenant,
                    "priceops_repository",
                    tenant_id,
                ),
                tenant_id=tenant_id,
                require_canonical=True,
            )

        def save_governance_growth(
            service: GovernanceService,
            tenant_id: str,
        ) -> None:
            growth_state = service.export_growth_state()
            if growth_state is not None:
                growth_state_repository.save(tenant_id, growth_state)

        governance_resolver = DurableTenantServiceResolver(
            governance_state_repository,
            factory=governance_factory,
            exporter=lambda service: service.export_state(),
            mutating_methods={
                "decide",
                "export_evidence_package",
                "upsert_approval",
            },
            after_save=save_governance_growth,
        )

        router.include_router(
            create_network_listings_sub_router(
                NetworkListingService(seed_fixtures=False),
                require_view_permission_fn=require_operator_permission(
                    "listing", Action.VIEW, engine=authz_engine
                ),
                require_write_permission_fn=require_operator_permission(
                    "listing", Action.UPDATE, engine=authz_engine
                ),
                audit_log=active_audit_log,
                service_resolver=listing_resolver,
                allow_reset=allow_test_reset,
            )
        )
        router.include_router(
            create_network_scoring_sub_router(
                NetworkScoringService(seed_fixtures=False),
                require_view_permission_fn=require_operator_permission(
                    "sitescore", Action.VIEW, engine=authz_engine
                ),
                require_write_permission_fn=require_operator_permission(
                    "sitescore", Action.EXECUTE, engine=authz_engine
                ),
                service_resolver=scoring_resolver,
                allow_reset=allow_test_reset,
            )
        )
        router.include_router(
            create_network_review_sub_router(
                NetworkReviewService(seed_fixtures=False),
                require_view_permission_fn=require_operator_permission(
                    "sitescore", Action.VIEW, engine=authz_engine
                ),
                require_decide_permission_fn=require_operator_permission(
                    "sitescore", Action.APPROVE, engine=authz_engine
                ),
                service_resolver=review_resolver,
                allow_reset=allow_test_reset,
            )
        )
        router.include_router(
            create_network_rebalance_sub_router(
                NetworkRebalanceService(seed_fixtures=False),
                require_view_permission_fn=require_operator_permission(
                    "listing", Action.VIEW, engine=authz_engine
                ),
                require_write_permission_fn=require_operator_permission(
                    "listing", Action.UPDATE, engine=authz_engine
                ),
                service_resolver=rebalance_resolver,
                allow_reset=allow_test_reset,
            )
        )
        router.include_router(
            create_growth_sub_router(
                GrowthService(
                    audit_log=active_audit_log,
                    seed_fixtures=False,
                ),
                require_view_permission_fn=operator_view_guard,
                require_permission_fn=require_operator_permission(
                    "intervention", Action.CREATE, engine=authz_engine
                ),
                service_resolver=growth_resolver,
            )
        )
        router.include_router(
            create_governance_sub_router(
                GovernanceService(seed_fixtures=False),
                require_view_permission_fn=operator_view_guard,
                require_decision_permission_fn=require_operator_permission(
                    "intervention", Action.APPROVE, engine=authz_engine
                ),
                require_export_permission_fn=require_operator_permission(
                    "intervention", Action.CREATE, engine=authz_engine
                ),
                service_resolver=governance_resolver,
            )
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
            authorize_franchisee_store_fn=authorize_franchisee_store,
            shell_service=ShellService(
                svc,
                repository=(
                    DurableShellRepository(document_store) if document_store is not None else None
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
            NetworkRebalanceService(govern_approval_writer=svc.upsert_network_rebalance_approval),
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
