from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import uvloop
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator import create_operator_router
from apps.api.app.routes.operator_modules import create_operator_store_ops_router
from modules.opsboard.application.operator_live_repository import (
    OperatorLiveRepository,
    OperatorLiveRepositoryError,
)
from modules.opsboard.application.store_ops import (
    DurableStoreOpsRepository,
    InMemoryStoreOpsRepository,
)
from shared.infrastructure.persistence import (
    DurableAVMRepository,
    DurableListingRepository,
    DurableNetPlanRepository,
    DurablePriceOpsRepository,
    DurableSiteScoreRepository,
)
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.operator_domains import (
    DurableOperatorDomainStateRepository,
    TenantScopedDocumentStore,
)
from shared.infrastructure.persistence.repositories import DurableDecisionStore
from tests.intake_registry_fixture import (
    APPROVER,
    MANAGER,
    OWNER,
    TENANT,
    _audit_retention_row,
    _calendar_row,
    _workflow_row,
)
from tests.integration.test_intake_registry_calendar_postgresql import (
    _bundle as _postgres_bundle,
)
from tests.integration.test_intake_registry_calendar_postgresql import (
    _insert_policy,
)
from tests.integration.test_operator_intake_workflow_postgresql import (
    bridge_database as bridge_database,
)

BASE = "/api/v1/operator"
SEED_IDS = {
    "HZ-01",
    "L-2024",
    "CS-1001",
    "RV-701",
    "RB-801",
    "seg-metro-dinner",
    "ap-store-1042",
}


@pytest.fixture(autouse=True)
def _use_production_event_loop_policy() -> Any:
    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(previous)


class _UnusedModelRuntime:
    def infer(self, **_kwargs: Any) -> Any:
        raise AssertionError("empty canonical stores must not invoke SiteScore")


def _headers(tenant_id: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {
        "x-subject-id": f"operator-{tenant_id}",
        "x-roles": "operations_manager,site_reviewer,expansion_user",
        "x-operator-role": "expansion-manager",
        "x-tenant-id": tenant_id,
        "x-correlation-id": f"corr-{tenant_id}",
    }
    if idempotency_key is not None:
        headers["idempotency-key"] = idempotency_key
    return headers


def _ops_headers(
    tenant_id: str,
    *,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    return {
        **_headers(tenant_id, idempotency_key=idempotency_key),
        "x-operator-role": "ops-lead",
    }


def _franchisee_headers(
    tenant_id: str,
    *,
    idempotency_key: str | None = None,
    store_ids: str = "STORE-001",
) -> dict[str, str]:
    headers = {
        "x-subject-id": f"franchisee-{tenant_id}",
        "x-roles": "franchisee",
        "x-tenant-id": tenant_id,
        "x-store-ids": store_ids,
        "x-correlation-id": f"corr-franchisee-{tenant_id}",
    }
    if idempotency_key is not None:
        headers["idempotency-key"] = idempotency_key
    return headers


class _StaticLiveRepository:
    """Small non-fixture live projection for canonical shell write tests."""

    @property
    def data_origin(self) -> dict[str, Any]:
        return {
            "kind": "live",
            "sourceId": "operator-shell-live-test",
            "persistenceMode": "postgresql",
        }

    def load_state(self, *, tenant_id: str, **_scope: Any) -> dict[str, Any]:
        origin = {**self.data_origin, "tenantId": tenant_id}
        return {
            "_meta": {
                "generatedAt": "2026-07-30T12:00:00+00:00",
                "dataMode": "live",
                "dataOrigin": origin,
                "tenantId": tenant_id,
                "recordCounts": {"workQueue": 1, "notifications": 1},
                "sections": {
                    "workQueue": {
                        "state": "live",
                        "source": "operator-shell-live-test",
                        "recordCount": 1,
                    },
                    "approvals": {
                        "state": "available",
                        "source": "operator-shell-live-test",
                        "recordCount": 0,
                    },
                    "notifications": {
                        "state": "live",
                        "source": "operator-shell-live-test",
                        "recordCount": 1,
                    },
                },
            },
            "workQueue": [
                {
                    "id": "LIVE-STORE-TASK-1",
                    "title": "門市冷藏櫃檢查",
                    "status": "open",
                    "time": "12:00",
                    "owner": "門市營運",
                    "meta": "SLA 追蹤",
                    "tone": "danger",
                    "workspace": "store",
                    "roles": ["ops-lead"],
                    "target": {
                        "workspace": "store",
                        "entityId": "LIVE-STORE-TASK-1",
                        "tab": "overview",
                    },
                }
            ],
            "notifications": [
                {
                    "id": "LIVE-STORE-NOTIFICATION-1",
                    "title": "冷藏櫃溫度異常",
                    "detail": "門市需在 SLA 內確認",
                    "tone": "danger",
                    "roles": ["ops-lead"],
                    "target": {
                        "workspace": "store",
                        "entityId": "LIVE-STORE-TASK-1",
                    },
                }
            ],
            "decisions": [],
            "riskRows": [],
            "auditFeed": [],
            "kpis": [],
        }


class _ScopeCapturingLiveRepository(_StaticLiveRepository):
    """Record the exact live repository scope selected by the HTTP route."""

    def __init__(self) -> None:
        self.load_scopes: list[dict[str, Any]] = []

    def load_state(self, *, tenant_id: str, **scope: Any) -> dict[str, Any]:
        self.load_scopes.append({"tenant_id": tenant_id, **scope})
        return super().load_state(tenant_id=tenant_id, **scope)


def _live_app(
    database_path: Path | None,
    *,
    allow_test_reset: bool = False,
    live_repository: Any | None = None,
    bundle: Any | None = None,
    with_priceops_masters: bool = False,
) -> tuple[FastAPI, Any]:
    bundle = bundle if bundle is not None else _durable_bundle(database_path)
    workflow_store = getattr(bundle, "assisted_intake_store", None)
    document_store = workflow_store._store if workflow_store else SqliteDocumentStore(bundle.engine)

    def scoped(tenant_id: str) -> TenantScopedDocumentStore:
        return TenantScopedDocumentStore(document_store, tenant_id)

    app = FastAPI()
    app.state.job_queue = bundle.job_queue

    @app.middleware("http")
    async def correlation_id(request: Request, call_next: Any) -> Any:
        request.state.correlation_id = request.headers.get(
            "x-correlation-id",
            "corr-live-test",
        )
        return await call_next(request)

    app.include_router(
        create_operator_router(
            audit_log=bundle.audit_log,
            document_store=document_store,
            listing_repository_for_tenant=lambda tenant_id: DurableListingRepository(
                scoped(tenant_id)
            ),
            sitescore_repository_for_tenant=lambda tenant_id: DurableSiteScoreRepository(
                scoped(tenant_id)
            ),
            sitescore_decision_repository_for_tenant=lambda tenant_id: DurableDecisionStore(
                scoped(tenant_id)
            ),
            avm_repository_for_tenant=lambda tenant_id: DurableAVMRepository(scoped(tenant_id)),
            netplan_repository_for_tenant=lambda tenant_id: DurableNetPlanRepository(
                scoped(tenant_id)
            ),
            priceops_repository_for_tenant=lambda tenant_id: DurablePriceOpsRepository(
                scoped(tenant_id)
            ),
            model_runtime=_UnusedModelRuntime(),
            priceops_store_repository=(bundle.store_repository if with_priceops_masters else None),
            priceops_brand_repository=(bundle.brand_repository if with_priceops_masters else None),
            intake_workflow_store=workflow_store,
            intake_workflow_policy=getattr(bundle, "assisted_intake_workflow_policy", None),
            live_repository=live_repository or OperatorLiveRepository(bundle),
            require_live_data=True,
            persistence_mode="postgresql",
            provider_mode="live",
            allow_test_reset=allow_test_reset,
        ),
        prefix="/api/v1",
    )
    return app, bundle


@pytest.mark.parametrize(("roles", "tabs"), [
    ("auditor,platform_admin", ["rebalance"]),
    ("operator_viewer", ["areas", "radar", "candidates", "score", "compare", "review"]),
])
def test_live_bootstrap_and_today_project_verified_navigation(tmp_path, roles, tabs) -> None:
    app, bundle = _live_app(tmp_path / "nav.sqlite3", live_repository=_StaticLiveRepository())
    try:
        client = TestClient(app)
        headers = {
            "X-Subject-Id": "nav-probe", "X-Roles": roles,
            "X-Tenant-Id": "tenant-a", "X-Operator-Role": "pm-audit",
        }
        for path in ("bootstrap", "today"):
            response = client.get(f"{BASE}/{path}", headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["meta"]["dataMode"] == "live"
            assert response.json()["navigation"]["allowedTabs"]["network"] == tabs
        for endpoint, tab in [
            ("network-listings", "radar"), ("network-scoring", "score"),
            ("network-reviews", "review"), ("network-rebalance", "rebalance"),
        ]:
            response = client.get(f"{BASE}/{endpoint}", headers=headers)
            assert (response.status_code != 403) == (tab in tabs), response.text
    finally:
        bundle.engine.close()


def _postgres_live_app(database, monkeypatch):
    """Production workflow dependencies use a real isolated PostgreSQL engine."""
    return _live_app(None, bundle=_postgres_bundle(database, monkeypatch))


def _second_workflow_tenant(database):
    """A second explicitly synthetic registry, never installed in product code."""
    tenant = "00000000-0000-0000-0000-000000000002"
    ids = {APPROVER: "00000000-0000-0000-0000-000000000201",
           MANAGER: "00000000-0000-0000-0000-000000000202",
           OWNER: "00000000-0000-0000-0000-000000000203"}
    with database.connect() as conn:
        conn.execute("INSERT INTO core.tenants (tenant_id,tenant_name) VALUES (%s,%s)",
                     (tenant, "Synthetic second workflow tenant"))
        for source, target in ids.items():
            conn.execute("""INSERT INTO identity.accounts
                (account_id,tenant_id,username,email,status,created_by)
                SELECT %s,%s,%s,%s,status,created_by FROM identity.accounts WHERE account_id=%s""",
                         (target, tenant, target, target + "@example.invalid", source))
            conn.execute("""INSERT INTO identity.account_roles(account_id,role,granted_by)
                SELECT %s,role,granted_by FROM identity.account_roles WHERE account_id=%s""", (target, source))
            conn.execute("INSERT INTO identity.account_scopes(account_id) VALUES (%s)", (target,))
        for row in (_workflow_row(), _calendar_row(), _audit_retention_row()):
            parameters = deepcopy(row.parameters)
            if "calendar_version_id" in parameters:
                parameters["calendar_version_id"] = parameters["calendar_version_id"].replace(TENANT, tenant)
            _insert_policy(conn, replace(row, tenant_id=tenant, approved_by=ids[APPROVER],
                                        policy_version_id=row.policy_version_id.replace(TENANT, tenant),
                                        parameters=parameters))
    return tenant, ids[MANAGER]


@pytest.mark.requires_live_env
def test_live_router_mounts_all_operator_domain_routes_without_seed_rows(
    bridge_database, monkeypatch,
) -> None:
    app, bundle = _postgres_live_app(bridge_database, monkeypatch)
    try:
        paths = set(app.openapi()["paths"])
        assert {
            f"{BASE}/shell/search",
            f"{BASE}/shell/admin",
            f"{BASE}/shell/settings",
            f"{BASE}/shell/franchisee",
            f"{BASE}/shell/tasks/{{task_id}}/assignment",
            f"{BASE}/shell/notifications/preferences",
            f"{BASE}/shell/notifications/{{notification_id}}/acknowledgement",
            f"{BASE}/network-listings",
            f"{BASE}/network-listings/intake/submit",
            f"{BASE}/network-scoring",
            f"{BASE}/network-scoring/score",
            f"{BASE}/network-reviews",
            f"{BASE}/network-reviews/{{review_id}}/decide",
            f"{BASE}/network-rebalance",
            f"{BASE}/network-rebalance/stores/{{store_id}}/avm/request",
            f"{BASE}/growth/actions",
            f"{BASE}/governance/snapshot",
            f"{BASE}/governance/evidence-package",
        } <= paths

        with TestClient(app) as client:
            headers = {**_headers(TENANT), "x-subject-id": MANAGER}
            ops_headers = {**_ops_headers(TENANT), "x-subject-id": MANAGER}
            franchisee_headers = {**_franchisee_headers(TENANT), "x-subject-id": MANAGER}
            shell_search = client.get(
                f"{BASE}/shell/search",
                headers=headers,
                params={"q": ""},
            )
            shell_tasks = client.get(f"{BASE}/shell/tasks", headers=headers)
            shell_notifications = client.get(
                f"{BASE}/shell/notifications",
                headers=headers,
            )
            shell_admin = client.get(f"{BASE}/shell/admin", headers=ops_headers)
            shell_settings = client.get(f"{BASE}/shell/settings", headers=headers)
            shell_franchisee = client.get(
                f"{BASE}/shell/franchisee",
                headers=franchisee_headers,
            )
            payloads = [
                client.get(f"{BASE}/network-listings", headers=headers).json(),
                client.get(f"{BASE}/network-scoring", headers=headers).json(),
                client.get(f"{BASE}/network-reviews", headers=headers).json(),
                client.get(f"{BASE}/network-rebalance", headers=headers).json(),
                client.get(f"{BASE}/growth/actions", headers=headers).json(),
                client.get(f"{BASE}/governance/snapshot", headers=headers).json(),
            ]

        assert shell_search.status_code == 200
        assert shell_tasks.status_code == 200
        task_payload = shell_tasks.json()
        assert task_payload["facets"]["sla"]["breached"] == 0
        assert task_payload["facets"]["assignee"]["me"] == 0
        assert task_payload["assignableRoles"]
        assert shell_notifications.status_code == 200
        notification_payload = shell_notifications.json()
        assert notification_payload["unacknowledged"] == 0
        assert notification_payload["preferences"]["severityFloor"] == "info"
        assert shell_admin.status_code == 200
        assert shell_settings.status_code == 200
        assert shell_settings.json()["isDefault"] is True
        assert shell_franchisee.status_code == 200
        assert shell_franchisee.json()["tasks"] == []

        serialized = str(payloads)
        assert all(seed_id not in serialized for seed_id in SEED_IDS)
        assert payloads[0]["listings"] == []
        assert payloads[0]["assistedIntakes"] == []
        assert payloads[1]["candidates"] == []
        assert payloads[2]["reviews"] == []
        assert payloads[3]["stores"] == []
        assert payloads[4]["items"] == []
        assert payloads[5]["approvals"] == []
    finally:
        bundle.engine.close()


def test_live_shell_writes_are_tenant_scoped_and_recover_after_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "operator-live-shell.sqlite3"
    repository = _StaticLiveRepository()
    ops = _ops_headers("tenant-shell-a")
    franchisee = _franchisee_headers("tenant-shell-a")
    assignment_body = {
        "assigneeId": "operator-cs-lead",
        "assigneeName": "張珮珊",
        "slaDueAt": "2030-01-01T00:00:00+00:00",
    }

    first_app, first_bundle = _live_app(
        database_path,
        live_repository=repository,
    )
    try:
        with TestClient(first_app) as client:
            assigned = client.post(
                f"{BASE}/shell/tasks/LIVE-STORE-TASK-1/assignment",
                headers={
                    **ops,
                    "idempotency-key": "live-shell-assignment-1",
                },
                json=assignment_body,
            )
            inbox = client.get(f"{BASE}/shell/notifications", headers=ops).json()
            acknowledged = client.post(
                (
                    f"{BASE}/shell/notifications/"
                    f"{inbox['items'][0]['notificationId']}/acknowledgement"
                ),
                headers={**ops, "idempotency-key": "live-shell-notification-1"},
            )
            preferences = client.put(
                f"{BASE}/shell/notifications/preferences",
                headers={**ops, "idempotency-key": "live-shell-preferences-1"},
                json={
                    "channels": {"inApp": True, "email": False},
                    "severityFloor": "warning",
                    "digest": "daily",
                },
            )
            settings = client.put(
                f"{BASE}/shell/settings",
                headers={**ops, "idempotency-key": "live-shell-settings-1"},
                json={"values": {"density": "compact"}},
            )
            grant = client.put(
                f"{BASE}/shell/admin/roles/expansion-manager/workspaces",
                headers={**ops, "idempotency-key": "live-shell-grant-1"},
                json={"allowedWorkspaces": ["today"]},
            )
            franchisee_view = client.get(
                f"{BASE}/shell/franchisee",
                headers=franchisee,
            ).json()
            franchisee_ack = client.post(
                f"{BASE}/shell/franchisee/acknowledgement",
                headers={
                    **franchisee,
                    "idempotency-key": "live-shell-franchisee-ack-1",
                },
                json={
                    "notificationId": franchisee_view["notifications"][0][
                        "notificationId"
                    ]
                },
            )
            franchisee_report = client.post(
                f"{BASE}/shell/franchisee/reports",
                headers={
                    **franchisee,
                    "idempotency-key": "live-shell-franchisee-report-1",
                },
                json={"category": "equipment", "message": "冷藏櫃溫度異常"},
            )

        assert [
            response.status_code
            for response in (
                assigned,
                acknowledged,
                preferences,
                settings,
                grant,
                franchisee_ack,
                franchisee_report,
            )
        ] == [200] * 7
        assignment_receipt = assigned.json()
        grant_audit_id = grant.json()["auditEvent"]["auditEventId"]
    finally:
        first_bundle.engine.close()

    reopened_app, reopened_bundle = _live_app(
        database_path,
        live_repository=repository,
    )
    try:
        with TestClient(reopened_app) as client:
            replay = client.post(
                f"{BASE}/shell/tasks/LIVE-STORE-TASK-1/assignment",
                headers={
                    **ops,
                    "idempotency-key": "live-shell-assignment-1",
                },
                json=assignment_body,
            )
            task = client.get(
                f"{BASE}/shell/tasks",
                headers=ops,
                params={"taskId": "LIVE-STORE-TASK-1"},
            ).json()["items"][0]
            inbox = client.get(f"{BASE}/shell/notifications", headers=ops).json()
            preferences_after = client.get(
                f"{BASE}/shell/notifications/preferences",
                headers=ops,
            ).json()
            settings_after = client.get(
                f"{BASE}/shell/settings",
                headers=ops,
            ).json()
            admin_after = client.get(
                f"{BASE}/shell/admin",
                headers=ops,
            ).json()
            franchisee_after = client.get(
                f"{BASE}/shell/franchisee",
                headers=franchisee,
            ).json()

            other_tenant_task = client.get(
                f"{BASE}/shell/tasks",
                headers=_ops_headers("tenant-shell-b"),
                params={"taskId": "LIVE-STORE-TASK-1"},
            ).json()["items"][0]
            other_tenant_settings = client.get(
                f"{BASE}/shell/settings",
                headers=_ops_headers("tenant-shell-b"),
            ).json()

        assert replay.status_code == 200
        assert replay.json()["idempotentReplay"] is True
        assert replay.json()["auditEvent"] == assignment_receipt["auditEvent"]
        assert task["assigneeName"] == "張珮珊"
        assert task["slaState"] == "on-track"
        assert inbox["items"][0]["acknowledged"] is True
        assert preferences_after["preferences"]["severityFloor"] == "warning"
        assert settings_after["values"]["density"] == "compact"
        assert grant_audit_id in {
            event["auditEventId"] for event in admin_after["auditFeed"]
        }
        assert franchisee_after["notifications"][0]["acknowledged"] is True
        assert [report["message"] for report in franchisee_after["reports"]] == [
            "冷藏櫃溫度異常"
        ]
        assert other_tenant_task["assigneeId"] is None
        assert other_tenant_settings["isDefault"] is True
    finally:
        reopened_bundle.engine.close()


def test_live_franchisee_routes_enforce_verified_store_scope_and_audit_denials(
    tmp_path: Path,
) -> None:
    app, bundle = _live_app(
        tmp_path / "operator-live-franchisee-scope.sqlite3",
        live_repository=_StaticLiveRepository(),
    )
    headers = {
        **_franchisee_headers("tenant-franchisee-scope"),
        "x-correlation-id": "corr-franchisee-store-scope",
    }
    try:
        with TestClient(app) as client:
            default_view = client.get(
                f"{BASE}/shell/franchisee",
                headers=headers,
            )
            denied_view = client.get(
                f"{BASE}/shell/franchisee",
                headers=headers,
                params={"storeId": "STORE-OTHER"},
            )
            denied_ack = client.post(
                f"{BASE}/shell/franchisee/acknowledgement",
                headers={**headers, "idempotency-key": "cross-store-ack"},
                json={
                    "notificationId": "LIVE-STORE-NOTIFICATION-1",
                    "storeId": "STORE-OTHER",
                },
            )
            denied_report = client.post(
                f"{BASE}/shell/franchisee/reports",
                headers={**headers, "idempotency-key": "cross-store-report"},
                json={
                    "category": "equipment",
                    "message": "must not persist",
                    "storeId": "STORE-OTHER",
                },
            )
            after = client.get(
                f"{BASE}/shell/franchisee",
                headers=headers,
            )
            missing_scope = client.get(
                f"{BASE}/shell/franchisee",
                headers={
                    **_franchisee_headers(
                        "tenant-franchisee-scope",
                        store_ids="",
                    ),
                    "x-correlation-id": "corr-franchisee-missing-store-scope",
                },
            )

        assert default_view.status_code == 200
        assert default_view.json()["store"]["id"] == "STORE-001"
        assert default_view.json()["meta"]["scope"]["storeId"] == "STORE-001"
        assert [
            denied_view.status_code,
            denied_ack.status_code,
            denied_report.status_code,
        ] == [403, 403, 403]
        assert after.json()["reports"] == []
        assert missing_scope.status_code == 403

        denials = [
            event
            for event in bundle.audit_log.list_events(
                correlation_id="corr-franchisee-store-scope"
            )
            if event.outcome == "deny"
        ]
        assert len(denials) == 3
        assert {event.action for event in denials} == {"view", "create"}
        assert {event.resource for event in denials} == {
            "franchisee_portal/STORE-OTHER"
        }
        assert {event.metadata["policy_id"] for event in denials} == {"scope.store"}

        missing_scope_events = bundle.audit_log.list_events(
            correlation_id="corr-franchisee-missing-store-scope"
        )
        # Two audit events: the type-level RBAC guard (require_permission)
        # emits an allow before the object-level store scope check
        # (authorize_franchisee_store) emits a deny.
        assert len(missing_scope_events) == 2
        missing_scope_allows = [
            e for e in missing_scope_events if e.outcome == "allow"
        ]
        missing_scope_denials = [
            e for e in missing_scope_events if e.outcome == "deny"
        ]
        assert len(missing_scope_allows) == 1
        assert len(missing_scope_denials) == 1
        assert missing_scope_denials[0].metadata["policy_id"] == (
            "franchisee_isolation"
        )
    finally:
        bundle.engine.close()


def test_live_franchisee_selected_store_narrows_multi_store_repository_scope(
    tmp_path: Path,
) -> None:
    live_repository = _ScopeCapturingLiveRepository()
    app, bundle = _live_app(
        tmp_path / "operator-live-franchisee-selection.sqlite3",
        live_repository=live_repository,
    )
    try:
        with TestClient(app) as client:
            response = client.get(
                f"{BASE}/shell/franchisee",
                headers=_franchisee_headers(
                    "tenant-franchisee-selection",
                    store_ids="STORE-001,STORE-002",
                ),
                params={"storeId": "STORE-001"},
            )
            missing_support_selection = client.get(
                f"{BASE}/shell/franchisee",
                headers=_ops_headers("tenant-franchisee-selection"),
            )

        assert response.status_code == 200
        assert response.json()["meta"]["scope"]["storeId"] == "STORE-001"
        assert missing_support_selection.status_code == 422
        assert "__missing_store_scope__" not in missing_support_selection.text
        assert live_repository.load_scopes
        assert {
            tuple(scope["store_ids"]) for scope in live_repository.load_scopes
        } == {("STORE-001",)}
    finally:
        bundle.engine.close()


def test_durable_listings_seed_canonical_state_only_behind_test_reset_gate(
    tmp_path: Path,
) -> None:
    # ODP-P10-DEV-LANDING-FIX-001: the durable listing aggregate serves the
    # canonical Package 10 fixture state only when the explicit test-reset gate
    # (ODP_E2E_MODE -> allow_test_reset) is on. Production keeps seed_fixtures
    # off, which the empty-state test above already pins.
    app, bundle = _live_app(
        tmp_path / "operator-live-e2e-seed.sqlite3",
        allow_test_reset=True,
    )
    try:
        with TestClient(app) as client:
            headers = _headers("tenant-e2e-seeded")
            snapshot = client.get(f"{BASE}/network-listings", headers=headers)
            assert snapshot.status_code == 200
            listing_ids = {
                listing["id"] for listing in snapshot.json()["listings"]
            }
            assert {"L-2024", "L-2025", "L-2029", "L-2030"} <= listing_ids
            assert next(
                listing for listing in snapshot.json()["listings"] if listing["id"] == "L-2024"
            )["hardRuleFailures"] == []
            # The next request reconstructs the aggregate from durable rows;
            # the seed's recorded pass must survive that round trip.
            restored = client.get(f"{BASE}/network-listings", headers=headers)
            assert restored.status_code == 200
            assert next(
                listing for listing in restored.json()["listings"] if listing["id"] == "L-2024"
            )["hardRuleFailures"] == []

            converted = client.post(
                f"{BASE}/network-listings/listings/L-2024/convert",
                headers=_headers(
                    "tenant-e2e-seeded",
                    idempotency_key="e2e-seed-convert-1",
                ),
                json={"actorRoleId": "expansionManager"},
            )
            assert converted.status_code == 200, converted.text

            # The conversion is durable: a fresh request rebuilds the service
            # from persisted state and still sees candidate linkage.
            after = client.get(
                f"{BASE}/network-listings", headers=headers
            ).json()
            l2024 = next(
                listing
                for listing in after["listings"]
                if listing["id"] == "L-2024"
            )
            assert l2024["status"] == "candidate"
            assert l2024["candidateId"] == "CS-1001"
            assert l2024["hardRuleFailures"] == []

            # Reset stays available behind the gate and restores the canonical
            # seed, discarding the conversion.
            reset = client.post(
                f"{BASE}/network-listings/reset", headers=headers
            )
            assert reset.status_code == 200
            reseeded = client.get(
                f"{BASE}/network-listings", headers=headers
            ).json()
            l2024_reset = next(
                listing
                for listing in reseeded["listings"]
                if listing["id"] == "L-2024"
            )
            assert l2024_reset.get("candidateId") in (None, "")
            assert l2024_reset["hardRuleFailures"] == []
            converted_after_reset = client.post(
                f"{BASE}/network-listings/listings/L-2024/convert",
                headers=_headers("tenant-e2e-seeded", idempotency_key="e2e-seed-convert-2"),
                json={"actorRoleId": "expansionManager"},
            )
            assert converted_after_reset.status_code == 200, converted_after_reset.text
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("verdict", [None, ["floor_not_ground_level"]], ids=["unknown", "failed"])
def test_durable_seed_retains_unapproved_verdict_across_requests_and_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verdict: list[str] | None,
) -> None:
    from modules.opsboard.application import network_listings

    original_seed = network_listings._seed_state

    def explicit_seed(**kwargs: Any) -> dict[str, Any]:
        state = original_seed(**kwargs)
        next(row for row in state["listings"] if row["id"] == "L-2024")["hardRuleFailures"] = verdict
        return state

    monkeypatch.setattr(network_listings, "_seed_state", explicit_seed)
    app, bundle = _live_app(tmp_path / "operator-seed-verdict.sqlite3", allow_test_reset=True)
    try:
        with TestClient(app) as client:
            headers = _headers("tenant-seed-verdict")
            for phase in ("initial", "reset"):
                if phase == "reset":
                    reset = client.post(f"{BASE}/network-listings/reset", headers=headers)
                    assert reset.status_code == 200
                # Both initial seed and reconstructed service must expose the
                # actual null/failure verdict, never an invented pass.
                for _ in range(2):
                    snapshot = client.get(f"{BASE}/network-listings", headers=headers)
                    assert snapshot.status_code == 200
                    listing = next(
                        row for row in snapshot.json()["listings"] if row["id"] == "L-2024"
                    )
                    assert listing["hardRuleFailures"] == verdict
                converted = client.post(
                    f"{BASE}/network-listings/listings/L-2024/convert",
                    headers=_headers("tenant-seed-verdict", idempotency_key=f"seed-verdict-{phase}"),
                    json={"actorRoleId": "expansionManager"},
                )
                assert converted.status_code == 422, converted.text
                expected = "HARD_RULE_EVIDENCE_REQUIRED" if verdict is None else "hard-rule failures"
                assert expected in converted.json()["detail"]
                after = client.get(f"{BASE}/network-listings", headers=headers)
                assert after.status_code == 200
                assert after.json()["candidates"] == []
    finally:
        bundle.engine.close()


def test_local_durable_operator_writes_use_the_fixed_verified_tenant_partition(
    tmp_path: Path,
) -> None:
    bundle = _durable_bundle(tmp_path / "operator-local-e2e-scope.sqlite3")
    document_store = SqliteDocumentStore(bundle.engine)

    def listing_for_tenant(tenant_id: str) -> DurableListingRepository:
        return DurableListingRepository(TenantScopedDocumentStore(document_store, tenant_id))

    app = FastAPI()
    app.include_router(
        create_operator_router(
            audit_log=bundle.audit_log,
            document_store=document_store,
            listing_repository=bundle.listing_repository,
            listing_repository_for_tenant=listing_for_tenant,
            allow_test_reset=True,
        ),
        prefix="/api/v1",
    )
    try:
        with TestClient(app) as client:
            reset = client.post(
                f"{BASE}/network-listings/reset",
                headers=_headers("tenant-a"),
            )

        assert reset.status_code == 200
        scoped_ids = {
            listing.listing_id
            for listing in listing_for_tenant("tenant-a").list_listings()
        }
        assert {"L-2024", "L-2025", "L-2029", "L-2030"} <= scoped_ids
        assert bundle.listing_repository.list_listings() == []
    finally:
        bundle.engine.close()


def test_live_router_rejects_every_network_reset(tmp_path: Path) -> None:
    app, bundle = _live_app(tmp_path / "operator-live-reset-denied.sqlite3")
    try:
        with TestClient(app) as client:
            headers = _headers("tenant-live-reset")
            responses = [
                client.post(f"{BASE}/network-listings/reset", headers=headers),
                client.post(f"{BASE}/network-scoring/reset", headers=headers),
                client.post(f"{BASE}/network-reviews/reset", headers=headers),
                client.post(f"{BASE}/network-rebalance/reset", headers=headers),
            ]

        assert [response.status_code for response in responses] == [403] * 4
        assert {response.json()["detail"]["code"] for response in responses} == {
            "PRODUCTION_RESET_DENIED"
        }
    finally:
        bundle.engine.close()


def test_live_operator_repository_failure_returns_503_instead_of_empty_200() -> None:
    class BrokenLiveRepository:
        @property
        def data_origin(self) -> dict[str, Any]:
            return {
                "kind": "live",
                "sourceId": "broken-live-repository",
                "persistenceMode": "postgresql",
            }

        def load_state(self, **_kwargs: Any) -> dict[str, Any]:
            raise OperatorLiveRepositoryError("database unavailable")

    app = FastAPI()
    app.include_router(
        create_operator_router(
            live_repository=BrokenLiveRepository(),
            require_live_data=True,
            persistence_mode="postgresql",
            provider_mode="live",
        ),
        prefix="/api/v1",
    )

    with TestClient(app) as client:
        response = client.get(
            f"{BASE}/today",
            headers=_headers("tenant-live-failure"),
        )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "OPERATOR_LIVE_DATA_UNAVAILABLE",
        "operation": "operator.envelope",
        "message": "database unavailable",
    }


def test_live_operator_without_repository_returns_503_not_fixture() -> None:
    app = FastAPI()
    app.include_router(
        create_operator_router(
            require_live_data=True,
            persistence_mode="postgresql",
            provider_mode="live",
        ),
        prefix="/api/v1",
    )

    with TestClient(app) as client:
        response = client.get(
            f"{BASE}/shell/home",
            headers=_headers("tenant-live-unwired"),
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "OPERATOR_LIVE_DATA_UNAVAILABLE"
    assert "r4-seed" not in response.text


def test_store_ops_production_empty_postgres_state_returns_503_without_seeding() -> None:
    class PostgresEngineStub:
        dialect = "postgresql"

    class EmptyPostgresDocumentStore:
        engine = PostgresEngineStub()

        def __init__(self) -> None:
            self.put_calls: list[tuple[Any, ...]] = []

        def get(self, *_args: Any) -> None:
            return None

        def put(self, *args: Any, **_kwargs: Any) -> None:
            self.put_calls.append(args)

    store = EmptyPostgresDocumentStore()
    app = FastAPI()

    @app.middleware("http")
    async def correlation_id(request: Request, call_next: Any) -> Any:
        request.state.correlation_id = "corr-store-ops-production"
        return await call_next(request)

    app.include_router(
        create_operator_store_ops_router(
            repository=DurableStoreOpsRepository(store),
            require_live_data=True,
        ),
        prefix="/api/v1",
    )

    with TestClient(app) as client:
        response = client.get(
            f"{BASE}/store-ops/summary",
            headers=_headers("tenant-store-ops"),
        )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "STORE_OPS_LIVE_DATA_UNAVAILABLE",
        "operation": "store_ops.summary",
        "message": "Store Ops scoped PostgreSQL repositories are not configured",
    }
    assert store.put_calls == []
    assert "ST-008" not in response.text


def test_store_ops_production_rejects_in_memory_fixture_repository() -> None:
    app = FastAPI()

    @app.middleware("http")
    async def correlation_id(request: Request, call_next: Any) -> Any:
        request.state.correlation_id = "corr-store-ops-memory"
        return await call_next(request)

    app.include_router(
        create_operator_store_ops_router(
            repository=InMemoryStoreOpsRepository(),
            require_live_data=True,
        ),
        prefix="/api/v1",
    )

    with TestClient(app) as client:
        response = client.get(
            f"{BASE}/store-ops/issues",
            headers=_headers("tenant-store-ops"),
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "STORE_OPS_LIVE_DATA_UNAVAILABLE"
    assert "ISS-1021" not in response.text


def test_store_ops_production_rejects_fixture_already_persisted_in_postgres() -> None:
    class PostgresEngineStub:
        dialect = "postgresql"

    fixture_state = InMemoryStoreOpsRepository().get_state()

    class FixturePostgresDocumentStore:
        engine = PostgresEngineStub()

        def get(self, *_args: Any) -> dict[str, Any]:
            return fixture_state

        def put(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("production fixture state must never be rewritten")

    app = FastAPI()

    @app.middleware("http")
    async def correlation_id(request: Request, call_next: Any) -> Any:
        request.state.correlation_id = "corr-store-ops-persisted-fixture"
        return await call_next(request)

    app.include_router(
        create_operator_store_ops_router(
            repository=DurableStoreOpsRepository(FixturePostgresDocumentStore()),
            require_live_data=True,
        ),
        prefix="/api/v1",
    )

    with TestClient(app) as client:
        response = client.get(
            f"{BASE}/store-ops/summary",
            headers=_headers("tenant-store-ops"),
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "STORE_OPS_LIVE_DATA_UNAVAILABLE"
    assert response.json()["detail"]["message"] == (
        "Store Ops scoped PostgreSQL repositories are not configured"
    )
    assert "ST-008" not in response.text


def test_live_router_without_document_store_mounts_routes_and_returns_503(
    tmp_path: Path,
) -> None:
    bundle = _durable_bundle(tmp_path / "operator-live-unavailable.sqlite3")
    app = FastAPI()
    app.include_router(
        create_operator_router(
            audit_log=bundle.audit_log,
            live_repository=OperatorLiveRepository(bundle),
            require_live_data=True,
            persistence_mode="postgresql",
            provider_mode="live",
        ),
        prefix="/api/v1",
    )
    try:
        assert f"{BASE}/network-scoring" in app.openapi()["paths"]
        with TestClient(app) as client:
            response = client.get(
                f"{BASE}/network-scoring",
                headers=_headers("tenant-unavailable"),
            )
            shell_response = client.get(
                f"{BASE}/shell/settings",
                headers=_ops_headers("tenant-unavailable"),
            )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == ("OPERATOR_DOMAIN_PERSISTENCE_UNAVAILABLE")
        assert shell_response.status_code == 503
        assert shell_response.json()["detail"] == {
            "code": "OPERATOR_SHELL_CONTRACT_UNAVAILABLE",
            "operation": "operator.shell.persistence",
            "dependency": "operator_shell_document_store",
            "state": "unavailable",
            "reasonCode": "TENANT_BOUND_DURABLE_SHELL_NOT_WIRED",
            "message": (
                "operator_shell_document_store has no tenant-bound durable "
                "production repository wiring"
            ),
        }
    finally:
        bundle.engine.close()


@pytest.mark.requires_live_env
def test_live_intake_write_and_idempotency_survive_restart(
    bridge_database, monkeypatch,
) -> None:
    tenant_id = TENANT
    headers = {**_headers(tenant_id, idempotency_key="idem-live-a-1"),
               "x-subject-id": MANAGER, "x-correlation-id": str(uuid4())}
    payload = {
        "url": "https://example.com/property/live-a-1",
        "heatZoneId": None,
    }

    first_app, first_bundle = _postgres_live_app(bridge_database, monkeypatch)
    try:
        with TestClient(first_app) as client:
            first = client.post(
                f"{BASE}/network-listings/intake/submit",
                headers=headers,
                json=payload,
            )
            assert first.status_code == 200, first.text
            intake_id = first.json()["id"]
    finally:
        first_bundle.engine.close()

    reopened_app, reopened_bundle = _postgres_live_app(bridge_database, monkeypatch)
    try:
        with TestClient(reopened_app) as client:
            detail = client.get(
                f"{BASE}/network-listings/intake/{intake_id}",
                headers=headers,
            )
            replay = client.post(
                f"{BASE}/network-listings/intake/submit",
                headers=headers,
                json=payload,
            )

        assert detail.status_code == 200, detail.text
        assert detail.json()["id"] == intake_id
        assert detail.json()["originalUrl"] == payload["url"]
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == intake_id
    finally:
        reopened_bundle.engine.close()


@pytest.mark.requires_live_env
def test_live_domain_state_and_idempotency_are_tenant_isolated(
    bridge_database, monkeypatch,
) -> None:
    tenant_a_id = TENANT
    tenant_b_id, actor_b = _second_workflow_tenant(bridge_database)
    headers_a = {**_headers(tenant_a_id, idempotency_key="shared-key"),
                 "x-subject-id": MANAGER, "x-correlation-id": str(uuid4())}
    headers_b = {**_headers(tenant_b_id, idempotency_key="shared-key"),
                 "x-subject-id": actor_b, "x-correlation-id": str(uuid4())}
    app, bundle = _postgres_live_app(bridge_database, monkeypatch)
    try:
        with TestClient(app) as client:
            tenant_a = client.post(
                f"{BASE}/network-listings/intake/submit",
                headers=headers_a,
                json={"url": "https://example.com/property/tenant-a"},
            )
            tenant_b_list = client.get(
                f"{BASE}/network-listings/intake",
                headers=headers_b,
            )
            tenant_b = client.post(
                f"{BASE}/network-listings/intake/submit",
                headers=headers_b,
                json={"url": "https://example.com/property/tenant-b"},
            )
            tenant_a_list = client.get(
                f"{BASE}/network-listings/intake",
                headers=headers_a,
            )

        assert tenant_a.status_code == 200, tenant_a.text
        assert tenant_b.status_code == 200, tenant_b.text
        assert tenant_a.json()["id"] != tenant_b.json()["id"]
        assert tenant_b_list.status_code == 200, tenant_b_list.text
        assert tenant_b_list.json()["items"] == []
        assert {item["id"] for item in tenant_a_list.json()["items"]} == {tenant_a.json()["id"]}
        assert "tenant-b" not in str(tenant_a_list.json())
    finally:
        bundle.engine.close()


def test_live_governance_export_refuses_metadata_only_success_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "operator-governance-unavailable.sqlite3"
    headers = _headers("tenant-governance", idempotency_key="idem-unadmitted-export")
    payload = {"dateFrom": "2026-07-01", "dateTo": "2026-07-24", "modules": ["Network"], "contents": ["Audit Trail"], "format": "PDF"}
    for _attempt in range(2):
        app, bundle = _live_app(database_path)
        try:
            with TestClient(app) as client:
                before = client.get(f"{BASE}/governance/snapshot", headers=headers).json()
                response = client.post(f"{BASE}/governance/evidence-package", headers=headers, json=payload)
                assert response.status_code == 503 and "CANONICAL_EVIDENCE_EXPORT_REQUIRED" in response.text
                after = client.get(f"{BASE}/governance/snapshot", headers=headers).json()
                assert after["evidencePackages"] == before["evidencePackages"] == []
                assert after["auditRows"] == before["auditRows"]
                assert "package" not in response.json()
        finally:
            bundle.engine.close()


@pytest.mark.parametrize(("header", "axis", "allowed"), [
    ("x-heat-zone-ids", "heatZoneId", "ZONE-A"),
    ("x-brand-ids", "brandId", "BRAND-A"),
    ("x-region-ids", "regionId", "NORTH"),
    ("x-store-ids", "storeId", "STORE-A"),
])
@pytest.mark.requires_live_env
def test_live_network_viewer_scope_is_projected_after_tenant_resolution(bridge_database, monkeypatch, header, axis, allowed):
    app, bundle = _postgres_live_app(bridge_database, monkeypatch)
    store = bundle.assisted_intake_store._store
    state_repository = DurableOperatorDomainStateRepository(store, "network-listings")
    state_repository.save(TENANT, {
        "heatZones": [{"id": "ZONE-A", "label": "A"}, {"id": "ZONE-B", "label": "B"}],
        "listings": [
            {"id": "LISTING-A", "tenantId": TENANT, "heatZoneId": "ZONE-A", axis: allowed},
            {"id": "LISTING-B", "tenantId": TENANT, "heatZoneId": "ZONE-B", axis: "excluded"},
        ],
        "candidates": [
            {"id": "CANDIDATE-A", "listingId": "LISTING-A"},
            {"id": "CANDIDATE-B", "listingId": "LISTING-B", "tenantId": TENANT},
        ],
        "siteReviews": [{"id": "REVIEW-A", "candidateId": "CANDIDATE-A"},
                        {"id": "REVIEW-B", "candidateId": "CANDIDATE-B"}],
        "assistedIntakes": [], "listingSources": [], "auditEvents": [],
    })
    headers = {"x-subject-id": MANAGER, "x-roles": "platform_admin,operator_viewer",
               "x-operator-role": "pm-audit", "x-tenant-id": TENANT, header: allowed}
    try:
        with TestClient(app) as client:
            response = client.get(f"{BASE}/network-listings", headers=headers)
            assert response.status_code == 200, response.text
            snap = response.json()
            assert [row["id"] for row in snap["listings"]] == ["LISTING-A"]
            assert [row["id"] for row in snap["candidates"]] == ["CANDIDATE-A"]
            assert [row["id"] for row in snap["siteReviews"]] == ["REVIEW-A"]
            assert snap["counts"]["listings"] == 1
            assert "LISTING-B" not in response.text and "CANDIDATE-B" not in response.text
            foreign = client.get(f"{BASE}/network-listings", headers={**headers, "x-tenant-id": "00000000-0000-0000-0000-000000000002"})
            assert foreign.status_code == 200 and foreign.json()["listings"] == []
    finally:
        bundle.engine.close()


def test_tenant_scoped_document_store_never_queries_unpartitioned_collections() -> None:
    called_collections: list[str] = []

    class SpyStore:
        def get(self, collection: str, doc_id: str) -> Any | None:
            called_collections.append(collection)
            return None

        def list_all(self, collection: str) -> list[Any]:
            called_collections.append(collection)
            return []

        def list_by_group(self, collection: str, group_key: str) -> list[Any]:
            called_collections.append(collection)
            return []

        def latest_in_group(self, collection: str, group_key: str) -> Any | None:
            called_collections.append(collection)
            return None

    spy = SpyStore()
    scoped = TenantScopedDocumentStore(spy, "tenant-spy-probe")
    base_coll = "listing.listings"

    scoped.get(base_coll, "doc-1")
    scoped.list_all(base_coll)
    scoped.list_by_group(base_coll, "group-1")
    scoped.latest_in_group(base_coll, "group-1")

    assert called_collections
    for coll in called_collections:
        assert coll.startswith(f"{base_coll}.tenant.")
        assert coll != base_coll


@pytest.mark.requires_live_env
def test_batch_worker_result_is_readable_by_live_operator_after_restart(bridge_database, monkeypatch) -> None:
    """A worker receipt must identify a row the live tenant route can read."""
    from apps.worker.oday_worker.handlers import BATCH_LISTING_INTAKE_JOB_TYPE
    from apps.worker.oday_worker.main import ODayWorker
    from shared.jobs.queue import JobRequest, JobStatus

    tenant = TENANT
    app, bundle = _postgres_live_app(bridge_database, monkeypatch)
    try:
        job, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
                payload={
                    "tenant_id": tenant,
                    "items": [{"item_id": "one", "address_raw": "Synthetic address"}],
                },
            ),
            correlation_id="synthetic-batch-readback",
        )
        assert ODayWorker(persistence=bundle).run_once()
        completed = bundle.job_queue.get(job.job_id)
        assert completed.status == JobStatus.SUCCEEDED
        intake_id = completed.payload["receipt"]["items"][0]["result_ref"]
        with TestClient(app) as client:
            response = client.get(f"{BASE}/network-listings/intake/{intake_id}", headers=_headers(tenant))
            assert response.status_code == 200, response.text
            assert response.json()["id"] == intake_id
    finally:
        bundle.engine.close()

    restarted_app, restarted = _postgres_live_app(bridge_database, monkeypatch)
    try:
        with TestClient(restarted_app) as client:
            same = client.get(f"{BASE}/network-listings/intake/{intake_id}", headers=_headers(tenant))
            assert same.status_code == 200, same.text
            assert same.json()["tenantId"] == tenant
            assert same.json()["parsedFields"]["address"]["normalizedValue"] == "Synthetic address"
            foreign = client.get(f"{BASE}/network-listings/intake/{intake_id}", headers=_headers("00000000-0000-0000-0000-000000000002"))
            assert foreign.status_code == 404
    finally:
        restarted.engine.close()


def test_tenant_atomic_intake_insert_preserves_first_record_and_separates_tenants(tmp_path: Path) -> None:
    path = tmp_path / "tenant-atomic-intake.sqlite3"
    bundle = _durable_bundle(path)
    try:
        first = {"id": "same-id", "tenantId": "tenant-a", "version": 7}
        repo_a = bundle.operator_intake_repository_for_tenant("tenant-a")
        assert repo_a.create_intake_if_absent(first) == (first, True)
        replacement = {**first, "version": 1}
        assert repo_a.create_intake_if_absent(replacement) == (first, False)
        foreign = {**replacement, "tenantId": "tenant-b"}
        repo_b = bundle.operator_intake_repository_for_tenant("tenant-b")
        assert repo_b.get_intake("same-id") is None
        assert repo_b.create_intake_if_absent(foreign) == (foreign, True)
        # No unpartitioned document is created by either atomic write.
        assert bundle.operator_intake_repository.get_intake("same-id") is None
    finally:
        bundle.engine.close()
    reopened = _durable_bundle(path)
    try:
        assert reopened.operator_intake_repository_for_tenant("tenant-a").get_intake("same-id") == first
        assert reopened.operator_intake_repository_for_tenant("tenant-b").get_intake("same-id") == foreign
    finally:
        reopened.engine.close()


@pytest.mark.parametrize("dependency", ["operator_intake_repository", "listing_repository"])
def test_durable_batch_missing_scoped_repository_fails_before_writes(tmp_path: Path, dependency: str) -> None:
    from dataclasses import replace

    from apps.worker.oday_worker.handlers import build_batch_listing_intake_service

    bundle = _durable_bundle(tmp_path / "missing-batch-dependency.sqlite3")
    try:
        incomplete = replace(bundle, **{dependency: None})
        with pytest.raises(RuntimeError, match="tenant-aware batch"):
            build_batch_listing_intake_service("verified-tenant", incomplete)
        assert bundle.engine.query_one("SELECT count(*) AS total FROM durable_documents")["total"] == 0
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("tenant", ["", " ", None])
def test_batch_service_rejects_missing_tenant_before_repository_access(tenant: Any) -> None:
    from types import SimpleNamespace

    from apps.worker.oday_worker.handlers import build_batch_listing_intake_service

    with pytest.raises(ValueError, match="verified tenant"):
        build_batch_listing_intake_service(tenant, SimpleNamespace())


@pytest.mark.parametrize("legacy_tenant", ["verified-tenant", "foreign-tenant"])
def test_batch_legacy_row_requires_reconciliation_without_copy_or_disclosure(tmp_path: Path, legacy_tenant: str) -> None:
    from apps.worker.oday_worker.handlers import (
        _default_batch_listing_item_executor,
        batch_listing_intake_id,
    )

    bundle = _durable_bundle(tmp_path / "legacy-batch-row.sqlite3")
    tenant = "verified-tenant"
    intake_id = batch_listing_intake_id(tenant, "job", "one")
    legacy = {"id": intake_id, "tenantId": legacy_tenant, "version": 8, "private_note": "legacy correction"}
    try:
        bundle.operator_intake_repository.save_intake(legacy)
        result, error = _default_batch_listing_item_executor(
            {"item_id": "one", "address_raw": "Synthetic replacement"},
            tenant, bundle, job_id="job", item_id="one",
        )
        assert result is None
        assert error.code == "INTAKE_PARTITION_RECONCILIATION_REQUIRED"
        assert error.retryable is False
        assert "legacy correction" not in str(error)
        assert bundle.operator_intake_repository.get_intake(intake_id) == legacy
        assert bundle.operator_intake_repository_for_tenant(tenant).get_intake(intake_id) is None
    finally:
        bundle.engine.close()



def _persist_synthetic_franchisee_native_stores(bundle: Any, tenant_id: str) -> None:
    """Owned SQLite input only; no source/model/production approval is implied."""
    from shared.domain import AddressLocation, Brand, Store, Tenant

    bundle.tenant_repository.save_tenant(Tenant(
        tenant_id=tenant_id, tenant_name="Explicit synthetic franchisee tenant",
    ))
    bundle.brand_repository.save_brand(Brand(
        brand_id="synthetic-franchisee-brand", tenant_id=tenant_id,
        brand_code="synthetic-franchisee", brand_name="Explicit synthetic brand",
    ))
    for store_id in ("STORE-001", "STORE-002"):
        address_id = "synthetic-address-" + store_id
        bundle.address_location_repository.save_address(AddressLocation(
            address_id=address_id, tenant_id=tenant_id, raw_address="Explicit local test address",
        ))
        bundle.store_repository.save_store(Store(
            store_id=store_id, tenant_id=tenant_id, brand_id="synthetic-franchisee-brand",
            store_name="Explicit synthetic " + store_id, store_status="open",
            address_id=address_id, region_code="north",
        ))


def test_live_native_franchisee_empty_does_not_read_operator_pending_owners(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    app, bundle = _live_app(tmp_path / "franchisee-empty-native.sqlite3")
    pending_owner_reads: list[str] = []
    for owner_class in (DurableAVMRepository, DurableNetPlanRepository, DurablePriceOpsRepository):
        original_init = owner_class.__init__

        def observe_init(self: Any, *args: Any, _original: Any = original_init,
                         _name: str = owner_class.__name__, **kwargs: Any) -> None:
            pending_owner_reads.append(_name)
            _original(self, *args, **kwargs)

        monkeypatch.setattr(owner_class, "__init__", observe_init)
    original_list_plans = DurablePriceOpsRepository.list_plans

    def observe_list_plans(self: Any, *args: Any, **kwargs: Any) -> Any:
        pending_owner_reads.append("DurablePriceOpsRepository.list_plans")
        return original_list_plans(self, *args, **kwargs)

    monkeypatch.setattr(DurablePriceOpsRepository, "list_plans", observe_list_plans)
    try:
        with TestClient(app) as client:
            response = client.get(
                f"{BASE}/shell/franchisee", headers=_franchisee_headers("tenant-franchisee-empty"),
            )
        assert response.status_code == 200, response.text
        assert response.json()["store"]["id"] == "STORE-001"
        assert response.json()["tasks"] == []
        assert response.json()["notifications"] == []
        assert pending_owner_reads == []
    finally:
        bundle.engine.close()


def test_live_native_franchisee_tasks_are_self_store_only_without_operator_grants(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from modules.forecastops.domain.forecasting import Alert, AlertLevel

    tenant_id = "tenant-franchisee-native-tasks"
    app, bundle = _live_app(tmp_path / "franchisee-populated-native.sqlite3")
    try:
        _persist_synthetic_franchisee_native_stores(bundle, tenant_id)
        # Persist actual typed native inputs, not a mocked live projection.
        # These declared local alerts are not generated model/financial evidence.
        for store_id in ("STORE-001", "STORE-002"):
            alert = Alert(
                alert_id="synthetic-franchisee-alert-" + store_id, tenant_id=tenant_id,
                store_id=store_id, alert_level=AlertLevel.RED,
                alert_reason_code="EXPLICIT_SYNTHETIC_STORE_TASK_" + store_id,
                evidence_json={"input_kind": "explicit_local_test_not_model_inference"},
                opened_at=datetime(2026, 10, 15, tzinfo=UTC),
                policy_id="explicit-synthetic-test-policy", policy_version="local-test-input-v1",
            )
            bundle.forecastops_repository.save_alert(alert)
            assert bundle.forecastops_repository.get_alert(tenant_id, alert.alert_id) == alert
        headers = _franchisee_headers(tenant_id)
        assert headers["x-roles"] == "franchisee"
        with TestClient(app) as client:
            response = client.get(f"{BASE}/shell/franchisee", headers=headers)
            denied_store = client.get(
                f"{BASE}/shell/franchisee", headers=headers, params={"storeId": "STORE-002"},
            )
            denied_operator = client.get(f"{BASE}/bootstrap", headers=headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["meta"]["scope"]["storeId"] == "STORE-001"
        assert [row["id"] for row in payload["tasks"]] == ["synthetic-franchisee-alert-STORE-001"]
        assert set(payload["tasks"][0]) == {"id", "title", "status", "time"}
        assert [row["notificationId"] for row in payload["notifications"]] == [
            "notification-synthetic-franchisee-alert-STORE-001",
        ]
        assert "STORE-002" not in response.text
        assert not {"approvals", "decisions", "kpis", "riskRows", "auditFeed"} & set(payload)
        assert denied_store.status_code == 403
        assert denied_operator.status_code == 403
    finally:
        bundle.engine.close()


def test_live_operator_shell_missing_masters_or_native_owner_still_returns_503(
    tmp_path: Path,
) -> None:
    for missing in ("pricing_masters", "native_intervention_owner"):
        bundle = _durable_bundle(tmp_path / f"operator-missing-{missing}.sqlite3")
        _persist_synthetic_franchisee_native_stores(bundle, "tenant-operator-unavailable")
        if missing == "native_intervention_owner":
            bundle = replace(bundle, intervention_repository=None)
        app, _ = _live_app(
            None, bundle=bundle, with_priceops_masters=missing == "native_intervention_owner",
        )
        headers = {**_ops_headers("tenant-operator-unavailable"), "x-store-ids": "STORE-001"}
        expected_code = (
            "PRICEOPS_STORE_BINDING_UNAVAILABLE" if missing == "pricing_masters"
            else "OPERATOR_PENDING_OWNER_UNAVAILABLE"
        )
        try:
            with TestClient(app) as client:
                for endpoint in ("/shell/tasks", "/today", "/bootstrap"):
                    response = client.get(BASE + endpoint, headers=headers)
                    assert response.status_code == 503, response.text
                    assert response.json()["detail"]["code"] == expected_code
        finally:
            bundle.engine.close()
