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


def _live_app(database_path: Path) -> tuple[FastAPI, Any]:
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
            live_repository=OperatorLiveRepository(bundle),
            require_live_data=True,
            persistence_mode="postgresql",
            provider_mode="live",
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
            payloads = [
                client.get(f"{BASE}/network-listings", headers=headers).json(),
                client.get(f"{BASE}/network-scoring", headers=headers).json(),
                client.get(f"{BASE}/network-reviews", headers=headers).json(),
                client.get(f"{BASE}/network-rebalance", headers=headers).json(),
                client.get(f"{BASE}/growth/actions", headers=headers).json(),
                client.get(f"{BASE}/governance/snapshot", headers=headers).json(),
            ]

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
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == ("OPERATOR_DOMAIN_PERSISTENCE_UNAVAILABLE")
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
