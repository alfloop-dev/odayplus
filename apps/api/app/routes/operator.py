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

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel

from modules.opsboard.application.growth import GrowthService
from modules.opsboard.application.operator_live_repository import (
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
        "role_id": getattr(request.state, "operator_role_id", None)
        or x_operator_role,
        "subject_id": getattr(request.state, "operator_subject_id", None)
        or x_subject_id,
        "system_roles": getattr(request.state, "operator_system_roles", None)
        or x_roles,
        "correlation_id": getattr(request.state, "correlation_id", None)
        or x_correlation_id,
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
    evidence_store: Any | None = None,
    intake_repository: Any | None = None,
    live_repository: OperatorLiveRepositoryProtocol | None = None,
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
        OPERATOR_TENANT_ID,
        build_engine,
        require_operator_permission,
        require_permission,
    )
    from shared.auth import Action

    active_audit_log = audit_log or InMemoryAuditLog()
    authz_engine = build_engine(audit_log=active_audit_log)

    # Shared state service — one instance per router lifetime. A live-required
    # router accepts only a state service backed by the injected live
    # repository; fixture services can never cross this composition boundary.
    if require_live_data:
        svc = (
            state_service
            if state_service is not None
            and state_service.live_repository is not None
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
        tenant_id=None if require_live_data else OPERATOR_TENANT_ID,
        engine=authz_engine,
    )
    operator_write_guard = require_operator_permission(
        OPERATOR_CONSOLE_RESOURCE,
        Action.UPDATE,
        tenant_id=None if require_live_data else OPERATOR_TENANT_ID,
        engine=authz_engine,
    )

    if require_live_data:
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

        @router.get("/bootstrap", dependencies=[Depends(operator_view_guard)])
        @router.get("/today", dependencies=[Depends(operator_view_guard)])
        def live_envelope(
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
        def live_search(
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

        def _task_row(item: dict[str, Any]) -> dict[str, Any]:
            target = item.get("target") or {}
            task_id = str(item.get("id", ""))
            tone = str(item.get("tone", "info"))
            return {
                **item,
                "taskId": task_id,
                "assigneeId": None,
                "assigneeName": None,
                "assignedAt": None,
                "assignedToMe": False,
                "slaDueAt": None,
                "slaState": "none",
                "severity": {
                    "danger": "critical",
                    "warning": "warning",
                }.get(tone, "info"),
                "deepLink": {
                    "workspace": target.get(
                        "workspace",
                        item.get("workspace", "today"),
                    ),
                    "entityId": target.get("entityId", task_id),
                    "tab": target.get("tab", "overview"),
                },
                "sourceHref": (
                    f"/tasks?taskId={target.get('entityId', task_id)}"
                    f"&workspace={target.get('workspace', item.get('workspace', 'today'))}"
                ),
            }

        def _notification_row(item: dict[str, Any]) -> dict[str, Any]:
            target = item.get("target") or {}
            notification_id = str(item.get("id") or item.get("title", ""))
            return {
                **item,
                "notificationId": notification_id,
                "severity": {
                    "danger": "critical",
                    "warning": "warning",
                }.get(str(item.get("tone", "info")), "info"),
                "acknowledged": False,
                "acknowledgedAt": None,
                "acknowledgedBy": None,
                "sourceHref": (
                    f"/tasks?taskId={target.get('entityId')}"
                    f"&workspace={target.get('workspace', 'today')}"
                    if target.get("entityId")
                    else "/notifications"
                ),
            }

        @router.get(
            "/shell/home",
            dependencies=[Depends(operator_view_guard)],
        )
        def live_shell_home(
            request: Request,
            x_operator_role: str | None = Header(
                default=None,
                alias="X-Operator-Role",
            ),
            x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
            x_roles: str | None = Header(default=None, alias="X-Roles"),
            x_correlation_id: str | None = Header(
                default=None,
                alias="X-Correlation-Id",
            ),
        ) -> dict[str, Any]:
            envelope = svc.get_today(
                **_context(
                    request,
                    x_operator_role=x_operator_role,
                    x_subject_id=x_subject_id,
                    x_roles=x_roles,
                    x_correlation_id=x_correlation_id,
                )
            )
            tasks = [_task_row(item) for item in envelope["workQueue"]]
            notifications = [
                _notification_row(item) for item in envelope["notifications"]
            ]
            role = envelope["meta"]["role"]
            from modules.opsboard.application.shell import ENTRY_POINTS

            allowed_workspaces = set(role["allowedWorkspaces"])
            entry_points = [
                {
                    key: value
                    for key, value in entry.items()
                    if key != "requiresAdmin"
                }
                for entry in ENTRY_POINTS
                if entry["workspace"] in allowed_workspaces
                and (
                    not entry.get("requiresAdmin")
                    or role["id"] == "ops-lead"
                )
            ]
            return {
                "meta": {
                    **envelope["meta"],
                    "source": "operator-live-shell-home",
                    "allowedWorkspaces": role["allowedWorkspaces"],
                    "isAdmin": role["id"] == "ops-lead",
                },
                "status": {
                    "headline": f"{role['label']}・{len(tasks)} 件待處理",
                    "openTasks": len(tasks),
                    "slaBreached": 0,
                    "slaAtRisk": 0,
                    "pendingApprovals": len(envelope["approvals"]),
                    "unacknowledgedNotifications": len(notifications),
                    "tone": "warning" if tasks else "success",
                },
                "tasks": tasks[:5],
                "approvals": envelope["approvals"][:5],
                "decisions": envelope["decisions"][:5],
                "freshness": [
                    {
                        "source": "operator-live-repository",
                        "label": "Operator live repositories",
                        "generatedAt": envelope["meta"]["generatedAt"],
                        "records": sum(
                            int(value)
                            for value in envelope["meta"]
                            .get("recordCounts", {})
                            .values()
                        ),
                        "state": (
                            "live"
                            if envelope["meta"]["liveReadiness"]["ready"]
                            else "unavailable"
                        ),
                    }
                ],
                "entryPoints": entry_points,
                "notifications": notifications[:5],
                "kpis": envelope["kpis"],
            }

        @router.get(
            "/shell/tasks",
            dependencies=[Depends(operator_view_guard)],
        )
        def live_shell_tasks(
            request: Request,
            sla: str | None = Query(default=None),
            assignee: str | None = Query(default=None),
            task_status: str | None = Query(default=None, alias="status"),
            task_id: str | None = Query(default=None, alias="taskId"),
            x_operator_role: str | None = Header(
                default=None,
                alias="X-Operator-Role",
            ),
            x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
            x_roles: str | None = Header(default=None, alias="X-Roles"),
            x_correlation_id: str | None = Header(
                default=None,
                alias="X-Correlation-Id",
            ),
        ) -> dict[str, Any]:
            context = _context(
                request,
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )
            queue_context = {
                key: value
                for key, value in context.items()
                if key != "correlation_id"
            }
            tasks = [
                _task_row(item)
                for item in svc.get_work_queue(**queue_context)
            ]
            filtered = tasks
            if sla:
                filtered = [
                    item for item in filtered if item["slaState"] == sla
                ]
            if assignee == "me":
                filtered = [
                    item for item in filtered if item["assignedToMe"]
                ]
            elif assignee == "unassigned":
                filtered = [
                    item for item in filtered if not item["assigneeId"]
                ]
            elif assignee:
                filtered = [
                    item
                    for item in filtered
                    if item["assigneeId"] == assignee
                ]
            if task_status:
                filtered = [
                    item
                    for item in filtered
                    if str(item.get("status")) == task_status
                ]
            if task_id:
                filtered = [
                    item for item in filtered if item["taskId"] == task_id
                ]
            return {
                "meta": {
                    "generatedAt": datetime.now(UTC).isoformat(),
                    "correlationId": context["correlation_id"],
                    "source": "operator-live-shell-tasks",
                    "dataOrigin": svc.data_origin,
                    "filters": {
                        "sla": sla,
                        "assignee": assignee,
                        "status": task_status,
                        "taskId": task_id,
                    },
                },
                "items": filtered,
                "count": len(filtered),
                "total": len(tasks),
                "facets": {
                    "sla": {
                        "breached": 0,
                        "at-risk": 0,
                        "on-track": 0,
                        "none": len(tasks),
                    },
                    "status": {
                        status_value: sum(
                            1
                            for item in tasks
                            if str(item.get("status")) == status_value
                        )
                        for status_value in {
                            str(item.get("status")) for item in tasks
                        }
                    },
                    "assignee": {"me": 0},
                },
                "actions": [
                    {
                        "key": "task.open",
                        "label": "開啟來源",
                        "allowed": True,
                        "reason": None,
                    }
                ],
                "assignableRoles": [],
            }

        @router.get(
            "/shell/notifications",
            dependencies=[Depends(operator_view_guard)],
        )
        def live_shell_notifications(
            request: Request,
            severity: str | None = Query(default=None),
            acknowledged: bool | None = Query(default=None),
            x_operator_role: str | None = Header(
                default=None,
                alias="X-Operator-Role",
            ),
            x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
            x_roles: str | None = Header(default=None, alias="X-Roles"),
            x_correlation_id: str | None = Header(
                default=None,
                alias="X-Correlation-Id",
            ),
        ) -> dict[str, Any]:
            context = _context(
                request,
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )
            envelope = svc.get_today(**context)
            rows = [
                _notification_row(item) for item in envelope["notifications"]
            ]
            filtered = rows
            if severity:
                filtered = [
                    item
                    for item in filtered
                    if item["severity"] == severity
                ]
            if acknowledged is not None:
                filtered = [
                    item
                    for item in filtered
                    if item["acknowledged"] is acknowledged
                ]
            return {
                "meta": {
                    "generatedAt": envelope["meta"]["generatedAt"],
                    "correlationId": context["correlation_id"],
                    "source": "operator-live-shell-notifications",
                    "dataOrigin": envelope["meta"]["dataOrigin"],
                },
                "items": filtered,
                "count": len(filtered),
                "unacknowledged": len(rows),
                "facets": {
                    "severity": {
                        level: sum(
                            1 for item in rows if item["severity"] == level
                        )
                        for level in ("critical", "warning", "info")
                    }
                },
                "preferences": None,
            }

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
            TenantScopedDocumentStore,
        )

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
                intake_repository=DurableAssistedIntakeRepository(
                    TenantScopedDocumentStore(document_store, tenant_id)
                ),
                initial_state=state,
                seed_fixtures=False,
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
            factory=lambda state, _tenant_id: NetworkScoringService(
                initial_state=state,
                seed_fixtures=False,
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
            factory=lambda state, _tenant_id: NetworkReviewService(
                initial_state=state,
                seed_fixtures=False,
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
            factory=lambda state, _tenant_id: GrowthService(
                initial_state=state,
                audit_log=active_audit_log,
                seed_fixtures=False,
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
                ),
                initial_state=state,
                seed_fixtures=False,
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
