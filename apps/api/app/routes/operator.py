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

from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel

from modules.opsboard.application.growth import GrowthService
from modules.opsboard.application.operator_live_repository import (
    OperatorLiveRepositoryError,
    OperatorLiveRepositoryProtocol,
)
from modules.opsboard.application.operator_state import OperatorStateService
from shared.audit import InMemoryAuditLog

if TYPE_CHECKING:
    from modules.avm.application import DepreciationRollbackReceipt

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
    job_queue: Any | None = None,
    feature_flags: Any | None = None,
    state_service: OperatorStateService | None = None,
    growth_service: GrowthService | None = None,
    listing_repository: Any | None = None,
    listing_repository_for_tenant: Any | None = None,
    sitescore_repository_for_tenant: Any | None = None,
    sitescore_decision_repository_for_tenant: Any | None = None,
    avm_repository_for_tenant: Any | None = None,
    netplan_repository_for_tenant: Any | None = None,
    priceops_repository_for_tenant: Any | None = None,
    priceops_store_repository: Any | None = None,
    priceops_brand_repository: Any | None = None,
    model_runtime: Any | None = None,
    score_execution_service: Any | None = None,
    avm_production_executor: Any | None = None,
    avm_depreciation_version_pin: str | None = None,
    avm_rollback_receipt: DepreciationRollbackReceipt | None = None,
    netplan_production_executor: Any | None = None,
    netplan_policy_repository: Any | None = None,
    netplan_approval_verifier: Any | None = None,
    evidence_store: Any | None = None,
    intake_repository: Any | None = None,
    intake_workflow_store: Any | None = None,
    intake_workflow_policy: Any | None = None,
    live_repository: OperatorLiveRepositoryProtocol | None = None,
    fixture_issue_repository: Any | None = None,
    identity_user_role_service: Any | None = None,
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
    identity_user_role_service:
        Identity-backed user administration (PostgreSQL ``identity.*``). Live
        routers mount it for ``/operator/users``; without it those routes
        refuse with 503 instead of editing a document nobody authenticates
        against.
    """
    from apps.api.oday_api.security.dependencies import (
        OPERATOR_CONSOLE_RESOURCE,
        OPERATOR_NETWORK_READ_RESOURCE,
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
    local_growth_service = None
    local_governance_service = None
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
        from modules.opsboard.application.governance import GovernanceService

        local_growth_service = growth_service or GrowthService()
        local_governance_service = GovernanceService(growth_service=local_growth_service)
        svc = state_service or OperatorStateService(
            persistence_mode=persistence_mode,
            provider_mode=provider_mode,
            fixture_issue_repository=fixture_issue_repository,
            fixture_approval_reader=lambda role_id: local_governance_service.snapshot(role_id=role_id)["approvals"],
        )

    router = APIRouter(prefix="/operator", tags=["operator"])

    # FinanceLegal is an existing AVM principal, not an Operator display persona.
    # Reuse its native type permission/session guard, then admit actual objects.
    from apps.api.app.routes.operator_modules.avm_dealroom import create_avm_dealroom_router
    from apps.api.oday_api.runtime_mode import live_data_required
    from apps.api.oday_api.security.dependencies import default_boundary
    from modules.opsboard.application.avm_dealroom import ScopedAVMDealRoom

    avm_boundary = default_boundary()

    def avm_permission(action):
        native_guard = require_permission("avm", action, engine=authz_engine, boundary=avm_boundary)

        def guarded(request: Request):
            # A formal app never degrades to header trust. A factory resolved
            # without a boundary cannot silently adopt another app's boundary.
            if avm_boundary is None and (live_data_required() or default_boundary() is not None):
                raise HTTPException(503, "AVM_AUTHENTICATION_OWNER_UNAVAILABLE")
            if avm_boundary is not None and getattr(avm_boundary, "_session_service", None) is None and default_boundary() is not avm_boundary:
                raise HTTPException(503, "AVM_AUTHENTICATION_OWNER_UNAVAILABLE")
            if not getattr(request.state, "correlation_id", None):
                from uuid import uuid4
                # Preserve the server middleware's trace when present. This
                # fallback is local to the scoped AVM guard, not a new caller
                # identity or a global authentication policy change.
                request.state.correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
            return native_guard(request)

        return guarded

    def avm_dealroom_for_request(request: Request):
        principal = request.state.operator_principal
        if not principal.tenant_id or not callable(avm_repository_for_tenant):
            raise HTTPException(503, "AVM_DEALROOM_OWNER_UNAVAILABLE")
        return ScopedAVMDealRoom(
            principal=principal, repository=avm_repository_for_tenant(principal.tenant_id),
            stores=priceops_store_repository, brands=priceops_brand_repository,
            audit_log=active_audit_log, queue=job_queue,
            correlation_id=getattr(request.state, "correlation_id", None) or request.headers.get("X-Correlation-Id", ""),
            production_executor=avm_production_executor,
            depreciation_version_pin=avm_depreciation_version_pin, rollback_receipt=avm_rollback_receipt,
            force_production=effective_require_live_data or live_data_required(),
        )

    router.include_router(create_avm_dealroom_router(
        factory=avm_dealroom_for_request, require_view=avm_permission(Action.VIEW),
        require_create=avm_permission(Action.CREATE), require_approve=avm_permission(Action.APPROVE),
        require_export=avm_permission(Action.EXPORT),
    ))

    # ------------------------------------------------------------------
    # Import sub-routers from operator_modules/ — the only composition
    # path.  Inline route re-definitions are forbidden to keep this file
    # as the single wiring point.
    # ------------------------------------------------------------------
    from apps.api.app.routes.operator_modules.approvals import create_approvals_sub_router
    from apps.api.app.routes.operator_modules.comments import create_comments_sub_router
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
    from apps.api.app.routes.operator_modules.shell import (
        create_shell_sub_router,
        project_operator_navigation,
    )
    from modules.opsboard.application.comments import CommentsService
    from modules.opsboard.application.network_listings import NetworkListingService
    from modules.opsboard.application.network_rebalance import NetworkRebalanceService
    from modules.opsboard.application.network_reviews import NetworkReviewService
    from modules.opsboard.application.network_scoring import NetworkScoringService
    from modules.opsboard.application.shell import ShellService
    from shared.infrastructure.persistence.operator_comments import DurableCommentRepository
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

    def comments_service_for(store: Any | None) -> CommentsService:
        if store is None:
            return CommentsService(audit_log=active_audit_log)
        return CommentsService(
            repository=DurableCommentRepository(getattr(store, "engine", None)),
            audit_log=active_audit_log,
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
                module="franchisee_portal",
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
            envelope = _live_read(
                "operator.envelope",
                svc.get_today,
                read_projection=lambda state: pending_state_for_request(request, state),
                **_context(
                    request,
                    x_operator_role=x_operator_role,
                    x_subject_id=x_subject_id,
                    x_roles=x_roles,
                    x_correlation_id=x_correlation_id,
                ),
            )

            return project_operator_navigation(envelope, request)

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
                read_projection=lambda state: pending_state_for_request(request, state),
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
            class RequestProjectedState:
                # This adapter and its principal live for one HTTP request.
                # Never bind a request closure to the router-lifetime service.
                def __getattr__(self, name: str) -> Any:
                    return getattr(svc, name)

                def get_today(self, **kwargs: Any) -> dict[str, Any]:
                    return svc.get_today(**kwargs, read_projection=lambda state: pending_state_for_request(request, state))

                def get_work_queue(self, **kwargs: Any) -> list[dict[str, Any]]:
                    return self.get_today(**kwargs)["workQueue"]

                def get_task_center(self, **kwargs: Any) -> dict[str, Any]:
                    def project(state: dict[str, Any]) -> dict[str, Any]:
                        from apps.api.oday_api.security.dependencies import (
                            operator_permission_decision,
                        )
                        from modules.opsboard.application.operator_tasks import (
                            admit_task_center_records,
                        )
                        from shared.auth import DataClassification

                        principal = request.state.operator_principal

                        def can_read(resource_type: str) -> bool:
                            return operator_permission_decision(
                                principal,
                                ResourceDescriptor(type=resource_type, tenant_id=principal.tenant_id,
                                                   module="operator_console", data_classification=DataClassification.CONFIDENTIAL),
                                Action.VIEW,
                                scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE if resource_type in {"listing", "sitescore"} else None,
                            ).allowed

                        def can_approve(resource_type: str) -> bool:
                            return operator_permission_decision(
                                principal,
                                ResourceDescriptor(type=resource_type, tenant_id=principal.tenant_id,
                                                   module="operator_console", data_classification=DataClassification.CONFIDENTIAL),
                                Action.APPROVE,
                            ).allowed

                        return admit_task_center_records(
                            pending_state_for_request(request, state), principal=principal,
                            store_repository=priceops_store_repository, brand_repository=priceops_brand_repository,
                            listing_snapshot_reader=lambda: listing_resolver(request).snapshot(tenant_id=principal.tenant_id),
                            can_read=can_read, can_approve=can_approve,
                        )

                    return svc.get_task_center(**kwargs, read_projection=project)

                def admit_franchisee_reports(self, reports: list[dict[str, Any]], audit_events: list[dict[str, Any]]) -> dict[str, Any]:
                    from apps.api.oday_api.security.dependencies import operator_permission_decision
                    from modules.opsboard.application.operator_franchisee_reports import (
                        admit_supervisor_reports,
                    )
                    from shared.auth import DataClassification

                    principal = request.state.operator_principal

                    def can_read(resource_type: str) -> bool:
                        return operator_permission_decision(
                            principal,
                            ResourceDescriptor(type=resource_type, tenant_id=principal.tenant_id,
                                               module="operator_console", data_classification=DataClassification.CONFIDENTIAL),
                            Action.VIEW,
                        ).allowed

                    return admit_supervisor_reports(reports, audit_events, principal=principal,
                        store_repository=priceops_store_repository, brand_repository=priceops_brand_repository,
                        can_read=can_read)

                def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
                    return svc.search(query, **kwargs, read_projection=lambda state: pending_state_for_request(request, state))

            # Only franchisee routes supply this already-authorized store.
            # Their store-task portal reads the original native state and has
            # no dependency on Operator-only pending review owners.
            state_reader = (
                svc
                if effective_store_id is not None
                else cast(OperatorStateService, RequestProjectedState())
            )
            scoped_state = TenantBoundOperatorStateService(
                state_reader,
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

        router.include_router(
            create_comments_sub_router(
                comments_service_for(document_store),
                require_view_permission_fn=operator_view_guard,
                require_write_permission_fn=operator_write_guard,
            )
        )

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
        from shared.infrastructure.persistence.repositories import DurableApprovalRepository
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
        listing_write_guard = None
        if getattr(document_store.engine, "dialect", None) == "postgresql":
            from shared.audit.backlog import (
                AuditBacklogBlocked,
                AuditBacklogUnavailable,
                CanonicalAuditBacklog,
            )

            listing_backlog = CanonicalAuditBacklog(document_store.engine)

            def listing_write_guard(tenant_id: str) -> None:
                try:
                    listing_backlog.require_available(tenant_id)
                except (AuditBacklogBlocked, AuditBacklogUnavailable) as exc:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="BACKPRESSURE_ACTIVE",
                        headers={"Retry-After": "30"},
                    ) from exc
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

        def live_intake_repository(tenant_id: str) -> Any:
            legacy = DurableAssistedIntakeRepository(
                TenantScopedDocumentStore(document_store, tenant_id)
            )
            if allow_test_reset:
                return legacy
            if intake_workflow_store is None:
                raise HTTPException(503, "INTAKE_WORKFLOW_STORE_UNAVAILABLE")
            from shared.infrastructure.persistence.operator_intake_workflow import (
                OperatorIntakeWorkflowRepository,
            )

            return OperatorIntakeWorkflowRepository(
                intake_workflow_store, legacy, tenant_id, intake_workflow_policy,
            )

        listing_resolver = DurableTenantServiceResolver(
            listing_state_repository,
            factory=lambda state, tenant_id: NetworkListingService(
                listing_repository=require_tenant_repository(
                    listing_repository_for_tenant,
                    "listing_repository",
                    tenant_id,
                ),
                intake_repository=live_intake_repository(tenant_id),
                initial_state=state,
                # Canonical fixture state may exist only behind the explicit
                # test-reset gate (ODP_E2E_MODE): production keeps the durable
                # listing aggregate fail-closed empty until real intake writes.
                seed_fixtures=allow_test_reset,
                tenant_id=tenant_id,
                high_risk_write_guard=listing_write_guard,
                review_decision_repository=require_tenant_repository(
                    sitescore_decision_repository_for_tenant, "sitescore_decision_repository", tenant_id,
                ),
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
        from modules.listing.application.candidate_score_context import CandidateScoreContextReader

        scoring_resolver = DurableTenantServiceResolver(
            scoring_state_repository,
            factory=lambda state, tenant_id: (
                lambda listing_repository: NetworkScoringService(
                    initial_state=state,
                    seed_fixtures=False,
                    listing_repository=listing_repository,
                    current_context_reader=CandidateScoreContextReader(
                        tenant_id=tenant_id, repository=listing_repository,
                        engine=document_store.engine, store=intake_workflow_store,
                    ),
                    sitescore_repository=require_tenant_repository(
                        sitescore_repository_for_tenant,
                        "sitescore_repository",
                        tenant_id,
                    ),
                    model_runtime=require_model_runtime(),
                    current_model_admission=getattr(
                        getattr(score_execution_service, "repository", None), "model_admission", None,
                    ),
                    execution_service=score_execution_service,
                    # The resolver creates a new service for every invocation.
                    # snapshot observes current authority itself; defer the
                    # otherwise duplicate constructor observation. Non-read
                    # methods/export still load canonical data before using it.
                    defer_canonical_refresh=True,
                    require_canonical=True,
                    tenant_id=tenant_id,
                )
            )(
                require_tenant_repository(
                    listing_repository_for_tenant, "listing_repository", tenant_id,
                ),
            ),
            exporter=lambda service: service.export_state(),
            mutating_methods={
                "reset",
                "score_candidate",
                "score_batch",
                "set_compare_set",
            },
        )
        def review_service_factory(state: dict[str, Any] | None, tenant_id: str) -> NetworkReviewService:
            decision_repository = require_tenant_repository(
                sitescore_decision_repository_for_tenant, "sitescore_decision_repository", tenant_id,
            )
            scoped_store = TenantScopedDocumentStore(document_store, tenant_id)
            candidate_service = NetworkListingService(
                listing_repository=require_tenant_repository(listing_repository_for_tenant, "listing_repository", tenant_id),
                intake_repository=DurableAssistedIntakeRepository(scoped_store),
                seed_fixtures=False, tenant_id=tenant_id, review_decision_repository=decision_repository,
            )
            def audit_delivery_factory(tenant: str):
                from shared.audit.durable_delivery import DurableAuditDelivery
                from shared.infrastructure.persistence.job_queue import DurableJobQueue
                # Older router callers can share the existing SQL queue table;
                # production supplies the worker's same-engine queue instance.
                queue = job_queue if job_queue is not None else DurableJobQueue(document_store.engine)
                return DurableAuditDelivery(document_store.engine, tenant, queue, active_audit_log._worm_sink)
            return NetworkReviewService(
                initial_state=state, seed_fixtures=False, require_canonical=True,
                decision_repository=decision_repository,
                sitescore_repository=require_tenant_repository(sitescore_repository_for_tenant, "sitescore_repository", tenant_id),
                decision_workflow=SiteScoreDecisionWorkflow(audit_log=active_audit_log, store=decision_repository,
                                                          approval_store=DurableApprovalRepository(scoped_store)),
                candidate_workflow_service=candidate_service, audit_delivery_factory=audit_delivery_factory,
                feature_flags=feature_flags, job_queue=job_queue,
            )

        review_resolver = DurableTenantServiceResolver(
            review_state_repository,
            factory=review_service_factory,
            exporter=lambda service: service.export_state(),
            mutating_methods={"reset", "decide_review", "submit_review", "submit_native_report", "decide_native_review"},
            transactional_mutations=True,
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
                netplan_policy_repository=netplan_policy_repository,
                netplan_approval_verifier=netplan_approval_verifier,
                avm_production_executor=avm_production_executor,
                avm_depreciation_version_pin=avm_depreciation_version_pin,
                avm_rollback_receipt=avm_rollback_receipt,
                netplan_production_executor=netplan_production_executor,
                runtime_mode="production",
                tenant_id=tenant_id,
                require_canonical=True,
            ),
            exporter=lambda service: service.export_state(),
            transactional_mutations=True,
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

        def priceops_read_owner(request: Request) -> Any:
            from apps.api.app.routes.priceops import priceops_plan_in_scope
            from modules.opsboard.application.priceops_read_scope import (
                ScopedPriceOpsReadRepository,
            )

            principal = request.state.operator_principal
            owner = require_tenant_repository(priceops_repository_for_tenant, "priceops_repository", principal.tenant_id)
            scope = principal.scope
            if (scope.brand_ids or scope.region_ids or scope.store_ids) and (
                priceops_store_repository is None or priceops_brand_repository is None
            ):
                from fastapi import HTTPException

                raise HTTPException(status_code=503, detail={
                    "code": "PRICEOPS_STORE_BINDING_UNAVAILABLE",
                    "message": "Canonical pricing ownership is unavailable",
                })
            plans = [plan for plan in owner.list_plans() if priceops_plan_in_scope(
                plan, principal, store_repository=priceops_store_repository,
                brand_repository=priceops_brand_repository, require_tenant=True,
                scope_module="operator_console",
            )]
            return ScopedPriceOpsReadRepository(owner, plans)

        def growth_read_service(request: Request) -> GrowthService:
            from modules.opsboard.application.priceops_read_scope import project_growth_read_state

            tenant_id = request.state.operator_principal.tenant_id
            owner = priceops_read_owner(request)
            state = project_growth_read_state(growth_state_repository.load(tenant_id), owner.list_plans())
            return GrowthService(initial_state=state, audit_log=active_audit_log, seed_fixtures=False,
                                 priceops_repository=owner, tenant_id=tenant_id, require_canonical=True)

        def governance_factory(
            state: dict[str, Any] | None,
            tenant_id: str,
            *,
            read_owner: Any | None = None,
        ) -> GovernanceService:
            from modules.opsboard.application.priceops_read_scope import (
                project_governance_read_state,
                project_growth_read_state,
            )

            growth_state = growth_state_repository.load(tenant_id)
            if read_owner is not None:
                growth_state = project_growth_read_state(growth_state, read_owner.list_plans())
                state = project_governance_read_state(state, read_owner.list_plans(), growth_state)
            priceops_owner = read_owner if read_owner is not None else require_tenant_repository(
                priceops_repository_for_tenant, "priceops_repository", tenant_id,
            )
            return GovernanceService(
                growth_service=GrowthService(
                    initial_state=growth_state,
                    audit_log=active_audit_log,
                    seed_fixtures=False,
                    priceops_repository=priceops_owner,
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
                sitescore_approval_repository=DurableApprovalRepository(TenantScopedDocumentStore(document_store, tenant_id)),
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
                priceops_repository=priceops_owner,
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

        def governance_read_service(request: Request) -> GovernanceService:
            tenant_id = request.state.operator_principal.tenant_id
            return governance_factory(governance_state_repository.load(tenant_id), tenant_id,
                                      read_owner=priceops_read_owner(request))

        def pending_network_scope_snapshot(request: Request) -> dict[str, Any]:
            # Use the existing canonical Review parent reader. Reading a
            # committed candidate's scope does not submit or operate Intake,
            # and must not instantiate its unrelated command workflow owner.
            tenant_id = request.state.operator_principal.tenant_id
            return NetworkListingService(
                listing_repository=require_tenant_repository(
                    listing_repository_for_tenant, "listing_repository", tenant_id,
                ),
                intake_repository=DurableAssistedIntakeRepository(TenantScopedDocumentStore(document_store, tenant_id)),
                seed_fixtures=False, tenant_id=tenant_id,
                review_decision_repository=require_tenant_repository(
                    sitescore_decision_repository_for_tenant, "sitescore_decision_repository", tenant_id,
                ),
            ).snapshot(tenant_id=tenant_id)

        def pending_snapshot_for_request(
            request: Request, native_state: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            from modules.opsboard.application.operator_pending_projection import (
                project_pending_snapshot,
            )

            # This cache belongs to the verified request, never a shared service
            # or a mutable actor closure retained across requests.
            cached = getattr(request.state, "operator_pending_snapshot", None)
            if cached is not None:
                return cached
            try:
                principal = request.state.operator_principal
                context = _live_operator_request_context(request)
                if native_state is None:
                    if svc.live_repository is None:
                        raise OperatorLiveRepositoryError("native pending read owner is unavailable")
                    native_state = svc.live_repository.load_state(
                        tenant_id=principal.tenant_id,
                        brand_ids=context["brand_ids"], region_ids=context["region_ids"],
                        store_ids=context["store_ids"],
                    )
                snapshot = governance_read_service(request).snapshot(
                    role_id=context["role_id"], correlation_id=context["correlation_id"],
                )
                projected = project_pending_snapshot(
                    snapshot, native_state, principal=principal,
                    listing_snapshot_reader=lambda: pending_network_scope_snapshot(request),
                    sitescore_owner=require_tenant_repository(sitescore_decision_repository_for_tenant, "sitescore_decision_repository", principal.tenant_id),
                    avm_owner=require_tenant_repository(avm_repository_for_tenant, "avm_repository", principal.tenant_id),
                    netplan_owner=require_tenant_repository(netplan_repository_for_tenant, "netplan_repository", principal.tenant_id),
                    store_repository=priceops_store_repository,
                    brand_repository=priceops_brand_repository,
                )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=503, detail={
                    "code": "OPERATOR_PENDING_OWNER_UNAVAILABLE",
                    "state": "unavailable",
                    "message": "Canonical pending review ownership is unavailable",
                }) from exc
            request.state.operator_pending_snapshot = projected
            return projected

        def pending_state_for_request(request: Request, state: dict[str, Any]) -> dict[str, Any]:
            from modules.opsboard.application.operator_pending_projection import (
                project_pending_state,
            )

            projected = project_pending_state(state, pending_snapshot_for_request(request, state))
            if _live_operator_request_context(request)["role_id"] == "marketing-manager":
                # Reuse the Growth HTTP read owner: tenant state is projected
                # against the current principal's canonical PriceOps parents.
                # Never retain this owner or its facts on the shared service.
                projected["todayGrowth"] = None
                try:
                    growth_owner = growth_read_service(request)
                    projected["todayGrowth"] = {
                        "actions": growth_owner.list_actions(),
                        "approvals": growth_owner.list_approvals(),
                    }
                except Exception:
                    # A missing/invalid source is unknown, never fixture or 0.
                    # The original pending-owner failures above remain 503.
                    pass
            return projected

        def pending_governance_snapshot(request: Request) -> dict[str, Any]:
            return {key: value for key, value in pending_snapshot_for_request(request).items()
                    if not key.startswith("_")}

        def network_read_scope_snapshot(request: Request) -> dict[str, Any]:
            principal = request.state.operator_principal
            return listing_resolver(request).snapshot(tenant_id=principal.tenant_id)

        require_zone_read = require_operator_permission(
            "heatzone", Action.VIEW, engine=authz_engine,
            scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE,
        )

        def canonical_heat_zone_source(request: Request) -> dict[str, Any]:
            # Check the native domain read grant before resolving its repository.
            # An unavailable HeatZone source must not erase permitted Radar rows.
            try:
                principal = require_zone_read(request)
                reader = getattr(svc.live_repository, "load_heat_zones", None)
                if not callable(reader):
                    return {}
                return reader(tenant_id=principal.tenant_id)
            except HTTPException as exc:
                if exc.status_code == 401:
                    raise
                return {}
            except Exception:
                return {}

        router.include_router(
            create_network_listings_sub_router(
                NetworkListingService(seed_fixtures=False),
                require_view_permission_fn=require_operator_permission(
                    "listing",
                    Action.VIEW,
                    engine=authz_engine,
                    scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE,
                ),
                require_write_permission_fn=require_operator_permission(
                    "listing", Action.UPDATE, engine=authz_engine
                ),
                audit_log=active_audit_log,
                service_resolver=listing_resolver,
                heat_zone_reader=canonical_heat_zone_source,
                allow_reset=allow_test_reset,
            )
        )
        router.include_router(
            create_network_scoring_sub_router(
                NetworkScoringService(seed_fixtures=False),
                require_view_permission_fn=require_operator_permission(
                    "sitescore",
                    Action.VIEW,
                    engine=authz_engine,
                    scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE,
                ),
                require_write_permission_fn=require_operator_permission(
                    "sitescore", Action.EXECUTE, engine=authz_engine
                ),
                service_resolver=scoring_resolver,
                execution_service=score_execution_service,
                read_scope_snapshot_fn=network_read_scope_snapshot,
                allow_reset=allow_test_reset,
            )
        )
        router.include_router(
            create_network_review_sub_router(
                NetworkReviewService(seed_fixtures=False, feature_flags=feature_flags, job_queue=job_queue),
                audit_log=active_audit_log,
                require_view_permission_fn=require_operator_permission(
                    "sitescore",
                    Action.VIEW,
                    engine=authz_engine,
                    scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE,
                ),
                require_submit_permission_fn=require_operator_permission(
                    "sitescore", Action.EXECUTE, engine=authz_engine
                ),
                require_decide_permission_fn=require_operator_permission(
                    "sitescore", Action.APPROVE, engine=authz_engine
                ),
                service_resolver=review_resolver,
                read_scope_snapshot_fn=network_read_scope_snapshot,
                allow_reset=allow_test_reset,
            )
        )
        from apps.api.app.routes.operator_modules.netplan_builder import (
            create_netplan_builder_router,
        )
        from modules.opsboard.application.netplan_builder import CanonicalNetPlanBuilder

        def netplan_builder_for_request(request: Request):
            principal = request.state.operator_principal
            return CanonicalNetPlanBuilder(
                principal=principal,
                scenario_repository=require_tenant_repository(
                    netplan_repository_for_tenant, "netplan_repository", principal.tenant_id),
                state_repository=rebalance_state_repository,
                rebalance_factory=rebalance_resolver._factory,
                store_repository=priceops_store_repository,
                brand_repository=priceops_brand_repository,
                listing_snapshot_reader=lambda: listing_resolver(request).snapshot(tenant_id=principal.tenant_id),
                audit_log=active_audit_log, job_queue=job_queue, policy_repository=netplan_policy_repository,
                feature_flags=feature_flags,
                correlation_id=request.state.correlation_id,
                role_id=request.state.operator_role_id,
            )

        router.include_router(create_netplan_builder_router(
            factory=netplan_builder_for_request,
            require_view=require_operator_permission("netplan", Action.VIEW, engine=authz_engine,
                                                     scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE),
            require_edit=require_operator_permission("netplan", Action.CREATE, engine=authz_engine),
            require_solve=require_operator_permission("netplan", Action.EXECUTE, engine=authz_engine),
            require_decide=require_operator_permission("netplan", Action.APPROVE, engine=authz_engine),
        ))
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
                require_approval_permission_fn=require_operator_permission(
                    "intervention", Action.APPROVE, engine=authz_engine
                ),
                require_transition_permission_fn=require_operator_permission(
                    "intervention", Action.EXECUTE, engine=authz_engine
                ),
                service_resolver=growth_resolver,
                read_service_resolver=growth_read_service,
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
                    "audit", Action.EXPORT, engine=authz_engine
                ),
                service_resolver=governance_resolver,
                read_service_resolver=governance_read_service,
                snapshot_reader=pending_governance_snapshot,
            )
        )
        from apps.api.app.routes.operator_modules.users_roles import (
            create_user_role_sub_router,
        )
        from modules.opsboard.application.identity_user_role_management import (
            UnavailableUserRoleManagementService,
        )

        # Live user administration mutates the identity.* rows the auth
        # boundary resolves; a users-roles document edit would report success
        # for a privilege change no authenticated request ever sees.
        router.include_router(
            create_user_role_sub_router(
                identity_user_role_service or UnavailableUserRoleManagementService(),
                require_view_permission_fn=require_operator_permission(
                    "user", Action.VIEW, engine=authz_engine
                ),
                require_manage_permission_fn=require_operator_permission(
                    "user", Action.UPDATE, engine=authz_engine
                ),
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

    router.include_router(
        create_comments_sub_router(
            comments_service_for(document_store),
            require_view_permission_fn=operator_view_guard,
            require_write_permission_fn=operator_write_guard,
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
    operator_listing_repository = listing_repository
    if listing_repository_for_tenant is not None:
        operator_listing_repository = listing_repository_for_tenant(OPERATOR_TENANT_ID)
        if operator_listing_repository is None:
            raise RuntimeError(
                "tenant-aware listing repository is unavailable for the local Operator scope"
            )

    # Network listing intake — read/write paths for R4 Listing Radar.
    network_listing_service = NetworkListingService(
        listing_repository=operator_listing_repository,
        intake_repository=shared_intake_repo,
    )

    def local_network_read_scope_snapshot(request: Request) -> dict[str, Any]:
        return network_listing_service.snapshot(tenant_id=request.state.operator_principal.tenant_id)

    router.include_router(
        create_network_listings_sub_router(
            network_listing_service,
            require_view_permission_fn=require_operator_permission(
                "listing",
                Action.VIEW,
                engine=authz_engine,
                scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE,
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
            read_scope_snapshot_fn=local_network_read_scope_snapshot,
            require_view_permission_fn=require_operator_permission(
                "sitescore",
                Action.VIEW,
                engine=authz_engine,
                scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE,
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
            NetworkReviewService(
                feature_flags=feature_flags, job_queue=job_queue,
                business_engine=getattr(document_store, "engine", None),
            ),
            audit_log=active_audit_log,            read_scope_snapshot_fn=local_network_read_scope_snapshot,
            require_view_permission_fn=require_operator_permission(
                "sitescore",
                Action.VIEW,
                engine=authz_engine,
                scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE,
            ),
            require_submit_permission_fn=require_operator_permission(
                "sitescore", Action.EXECUTE, engine=authz_engine
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
    growth_svc = local_growth_service
    router.include_router(
        create_growth_sub_router(
            growth_svc,
            require_view_permission_fn=operator_view_guard,
            require_permission_fn=require_operator_permission(
                "intervention", Action.CREATE, engine=authz_engine
            ),
            require_approval_permission_fn=require_operator_permission(
                "intervention", Action.APPROVE, engine=authz_engine
            ),
            require_transition_permission_fn=require_operator_permission(
                "intervention", Action.EXECUTE, engine=authz_engine
            ),
        )
    )

    # Govern — aggregation snapshot open; decisions require APPROVE, evidence
    # export requires CREATE.  Shares the Growth service so live Growth
    # decisions/approvals surface in the Govern snapshot.
    from apps.api.app.routes.operator_modules.governance import (
        create_governance_sub_router,
    )
    router.include_router(
        create_governance_sub_router(
            local_governance_service,
            require_view_permission_fn=operator_view_guard,
            require_decision_permission_fn=require_operator_permission(
                "intervention", Action.APPROVE, engine=authz_engine
            ),
            require_export_permission_fn=require_operator_permission(
                "audit", Action.EXPORT, engine=authz_engine
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

    # Users & Roles — Self-service role assignment, scope axes, and audit logging (ODP-CAP-USER-ROLE-UI-001)
    from apps.api.app.routes.operator_modules.live_service import (
        DurableTenantServiceResolver,
    )
    from apps.api.app.routes.operator_modules.users_roles import (
        create_user_role_sub_router,
    )
    from modules.opsboard.application.user_role_management import (
        UserRoleManagementService,
    )
    from shared.infrastructure.persistence.operator_domains import (
        DurableOperatorDomainStateRepository,
    )

    user_role_state_repo_fallback = (
        DurableOperatorDomainStateRepository(document_store, "users-roles")
        if document_store is not None
        else None
    )
    user_role_resolver_fallback = (
        DurableTenantServiceResolver(
            user_role_state_repo_fallback,
            factory=lambda state, tenant_id: UserRoleManagementService(
                audit_log=active_audit_log,
                initial_state=state,
                seed_fixtures=True,
            ),
            exporter=lambda service: service.export_state(),
            mutating_methods={"save_user", "set_user_status"},
        )
        if user_role_state_repo_fallback is not None
        else None
    )

    user_role_view_guard = require_operator_permission(
        "user", Action.VIEW, tenant_id=OPERATOR_TENANT_ID, engine=authz_engine
    )
    user_role_manage_guard = require_operator_permission(
        "user", Action.UPDATE, tenant_id=OPERATOR_TENANT_ID, engine=authz_engine
    )
    user_role_service = UserRoleManagementService(audit_log=active_audit_log)
    router.include_router(
        create_user_role_sub_router(
            user_role_service,
            require_view_permission_fn=user_role_view_guard,
            require_manage_permission_fn=user_role_manage_guard,
            service_resolver=user_role_resolver_fallback,
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
