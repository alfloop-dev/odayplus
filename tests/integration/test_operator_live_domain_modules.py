from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

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
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurableDecisionStore

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
                        "state": "live",
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
    database_path: Path,
    *,
    allow_test_reset: bool = False,
    live_repository: Any | None = None,
) -> tuple[FastAPI, Any]:
    bundle = _durable_bundle(database_path)
    document_store = SqliteDocumentStore(bundle.engine)

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
            live_repository=live_repository or OperatorLiveRepository(bundle),
            require_live_data=True,
            persistence_mode="postgresql",
            provider_mode="live",
            allow_test_reset=allow_test_reset,
        ),
        prefix="/api/v1",
    )
    return app, bundle


def test_live_router_mounts_all_operator_domain_routes_without_seed_rows(
    tmp_path: Path,
) -> None:
    app, bundle = _live_app(tmp_path / "operator-live-routes.sqlite3")
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
            headers = _headers("tenant-live-empty")
            ops_headers = _ops_headers("tenant-live-empty")
            franchisee_headers = _franchisee_headers("tenant-live-empty")
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

        missing_scope_denials = bundle.audit_log.list_events(
            correlation_id="corr-franchisee-missing-store-scope"
        )
        assert len(missing_scope_denials) == 1
        assert missing_scope_denials[0].outcome == "deny"
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

            converted = client.post(
                f"{BASE}/network-listings/listings/L-2024/convert",
                headers=_headers(
                    "tenant-e2e-seeded",
                    idempotency_key="e2e-seed-convert-1",
                ),
                json={"actorRoleId": "expansionManager"},
            )
            assert converted.status_code == 200

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
        "message": "Store Ops production state has not been materialized",
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
        "Store Ops production state contains canonical fixture identifiers"
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


def test_live_intake_write_and_idempotency_survive_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "operator-live-restart.sqlite3"
    headers = _headers("tenant-live-a", idempotency_key="idem-live-a-1")
    payload = {
        "url": "https://example.com/property/live-a-1",
        "heatZoneId": None,
    }

    first_app, first_bundle = _live_app(database_path)
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

    reopened_app, reopened_bundle = _live_app(database_path)
    try:
        with TestClient(reopened_app) as client:
            detail = client.get(
                f"{BASE}/network-listings/intake/{intake_id}",
                headers=_headers("tenant-live-a"),
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


def test_live_domain_state_and_idempotency_are_tenant_isolated(
    tmp_path: Path,
) -> None:
    app, bundle = _live_app(tmp_path / "operator-live-tenants.sqlite3")
    try:
        with TestClient(app) as client:
            tenant_a = client.post(
                f"{BASE}/network-listings/intake/submit",
                headers=_headers("tenant-a", idempotency_key="shared-key"),
                json={"url": "https://example.com/property/tenant-a"},
            )
            tenant_b_list = client.get(
                f"{BASE}/network-listings/intake",
                headers=_headers("tenant-b"),
            )
            tenant_b = client.post(
                f"{BASE}/network-listings/intake/submit",
                headers=_headers("tenant-b", idempotency_key="shared-key"),
                json={"url": "https://example.com/property/tenant-b"},
            )
            tenant_a_list = client.get(
                f"{BASE}/network-listings/intake",
                headers=_headers("tenant-a"),
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


def test_live_governance_write_and_idempotency_survive_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "operator-governance-restart.sqlite3"
    headers = _headers(
        "tenant-governance",
        idempotency_key="idem-governance-export",
    )
    payload = {
        "dateFrom": "2026-07-01",
        "dateTo": "2026-07-24",
        "modules": ["Network"],
        "contents": ["Audit Trail"],
        "format": "PDF",
    }

    first_app, first_bundle = _live_app(database_path)
    try:
        with TestClient(first_app) as client:
            first = client.post(
                f"{BASE}/governance/evidence-package",
                headers=headers,
                json=payload,
            )
        assert first.status_code == 200, first.text
        package_id = first.json()["package"]["id"]
    finally:
        first_bundle.engine.close()

    reopened_app, reopened_bundle = _live_app(database_path)
    try:
        with TestClient(reopened_app) as client:
            history = client.get(
                f"{BASE}/governance/evidence-packages",
                headers=_headers("tenant-governance"),
            )
            replay = client.post(
                f"{BASE}/governance/evidence-package",
                headers=headers,
                json=payload,
            )

        assert history.status_code == 200, history.text
        assert [item["id"] for item in history.json()["items"]] == [package_id]
        assert replay.status_code == 200, replay.text
        assert replay.json()["package"]["id"] == package_id
        assert replay.json()["idempotentReplay"] is True
    finally:
        reopened_bundle.engine.close()


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
