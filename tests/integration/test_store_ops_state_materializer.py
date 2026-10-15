"""ODP-STOREOPS-STATE-MATERIALIZER-001: the production Store Ops state writer.

On PostgreSQL ``DurableStoreOpsRepository.get_state`` fails closed until the
``opsboard.store_ops.state/default`` document exists, and every Store Ops write
reads that document first, so the materializer is the only path that can end
the 503. These tests pin that it writes an honest projection (empty when there
are no stores, never fixtures), is idempotent, keeps the operator lifecycle,
leaves the fail-closed read semantics intact, and is scheduled + handled.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules import create_operator_store_ops_router
from apps.scheduler.oday_scheduler.main import (
    EXTERNAL_FETCH_JOB_TYPE,
    STORE_OPS_MATERIALIZE_JOB_TYPE,
    ODayScheduler,
)
from apps.worker.oday_worker.handlers import (
    build_default_registry,
    handle_store_ops_materialize,
)
from modules.external_data.application.market_data_facade import (
    CUTOVER_MODE_LEGACY_ONLY,
    CUTOVER_MODE_PLATFORM_PRIMARY,
    FACADE_MODE_ENV,
)
from modules.opsboard.application.store_ops import (
    _STORE_OPS_FIXTURE_IDS,
    DurableStoreOpsRepository,
    InMemoryStoreOpsRepository,
    StoreOpsLiveDataUnavailable,
    StoreOpsService,
)
from modules.opsboard.application.store_ops_materializer import (
    MATERIALIZED_AUDIT_ORDINAL_START,
    empty_store_ops_state,
    materialize_store_ops_state,
)
from shared.domain.models import Store
from shared.infrastructure.persistence.factory import build_persistence
from shared.jobs.queue import JobRecord, NonRetryableJobError

TENANT_ID = "tenant-store-ops-live"
BASE = "/api/v1/operator"
STATE_KEY = ("opsboard.store_ops.state", "default")


class _PostgresEngineStub:
    dialect = "postgresql"


class LivePostgresDocumentStore:
    """Dict-backed document store that reports a PostgreSQL dialect."""

    engine = _PostgresEngineStub()

    def __init__(self, documents: dict[tuple[str, str], Any] | None = None) -> None:
        self.documents = dict(documents or {})
        self.put_calls: list[tuple[str, str]] = []
        self.fail_reads = False

    def get(self, collection: str, doc_id: str) -> Any:
        if self.fail_reads:
            raise ConnectionError("postgres unreachable")
        value = self.documents.get((collection, doc_id))
        return copy.deepcopy(value)

    def put(self, collection: str, doc_id: str, value: Any) -> None:
        self.put_calls.append((collection, doc_id))
        self.documents[(collection, doc_id)] = copy.deepcopy(value)


class FakeStoreRepository:
    def __init__(self, stores: list[Store]) -> None:
        self.stores = stores
        self.calls: list[str | None] = []

    def list_stores(self, *, tenant_id: str | None = None, **_kwargs: Any) -> list[Store]:
        self.calls.append(tenant_id)
        return [store for store in self.stores if store.tenant_id == tenant_id]


class FakeForecastOpsRepository:
    def __init__(self, alerts: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.alerts = alerts or {}

    def list_alerts_by_store(self, tenant_id: str, store_id: str) -> list[dict[str, Any]]:
        assert tenant_id == TENANT_ID
        return list(self.alerts.get(store_id, []))


def _store(store_id: str, name: str, **overrides: Any) -> Store:
    return Store(
        store_id=store_id,
        tenant_id=overrides.pop("tenant_id", TENANT_ID),
        brand_id="brand-1",
        store_name=name,
        store_status=overrides.pop("store_status", "open"),
        region_code=overrides.pop("region_code", "TPE-XINYI"),
        **overrides,
    )


def _alert(alert_id: str, store_id: str, level: str, **overrides: Any) -> dict[str, Any]:
    return {
        "alert_id": alert_id,
        "tenant_id": TENANT_ID,
        "store_id": store_id,
        "alert_level": level,
        "alert_reason_code": overrides.pop("alert_reason_code", "sitescore_gap"),
        "opened_at": overrides.pop("opened_at", datetime(2026, 10, 1, 8, 0, tzinfo=UTC)),
        "policy_version": "four-light-policy-v1",
        "status": overrides.pop("status", "open"),
        **overrides,
    }


def _materialize(
    document_store: LivePostgresDocumentStore,
    *,
    stores: list[Store] | None = None,
    alerts: dict[str, list[dict[str, Any]]] | None = None,
):
    return materialize_store_ops_state(
        repository=DurableStoreOpsRepository(document_store),
        store_repository=FakeStoreRepository(stores or []),
        forecastops_repository=FakeForecastOpsRepository(alerts),
        tenant_id=TENANT_ID,
    )


def _client(document_store: LivePostgresDocumentStore) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def correlation_id(request: Request, call_next: Any) -> Any:
        request.state.correlation_id = "corr-store-ops-materialized"
        return await call_next(request)

    app.include_router(
        create_operator_store_ops_router(
            repository=DurableStoreOpsRepository(document_store),
            require_live_data=True,
        ),
        prefix="/api/v1",
    )
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {
        "x-subject-id": f"operator-{TENANT_ID}",
        "x-roles": "operations_manager,site_reviewer,expansion_user",
        "x-operator-role": "ops-lead",
        "x-tenant-id": TENANT_ID,
        "x-correlation-id": f"corr-{TENANT_ID}",
    }


def _assert_no_fixture_ids(text: str) -> None:
    leaked = sorted(fixture_id for fixture_id in _STORE_OPS_FIXTURE_IDS if fixture_id in text)
    assert leaked == []


# ---------------------------------------------------------------------------
# Empty canonical stores
# ---------------------------------------------------------------------------


def test_empty_core_stores_writes_honest_empty_state_and_api_returns_200() -> None:
    document_store = LivePostgresDocumentStore()

    result = _materialize(document_store)

    assert result.status == "written"
    assert result.reason == "created"
    assert document_store.put_calls == [STATE_KEY]
    assert document_store.documents[STATE_KEY] == empty_store_ops_state()

    with _client(document_store) as client:
        summary = client.get(f"{BASE}/store-ops/summary", headers=_headers())
        issues = client.get(f"{BASE}/store-ops/issues", headers=_headers())

    assert summary.status_code == 200
    assert summary.json()["stores"] == []
    assert all(sum(row["counts"].values()) == 0 for row in summary.json()["fourLightSummary"])
    assert issues.status_code == 200
    assert issues.json()["issues"] == []
    assert issues.json()["count"] == 0
    _assert_no_fixture_ids(summary.text + issues.text)


def test_unmaterialized_state_still_returns_503() -> None:
    with _client(LivePostgresDocumentStore()) as client:
        response = client.get(f"{BASE}/store-ops/summary", headers=_headers())

    assert response.status_code == 503
    assert response.json()["detail"]["message"] == (
        "Store Ops production state has not been materialized"
    )


# ---------------------------------------------------------------------------
# Projection from canonical stores and ForecastOps alerts
# ---------------------------------------------------------------------------


def test_projects_stores_four_lights_and_issues_without_fixture_ids() -> None:
    document_store = LivePostgresDocumentStore()
    stores = [
        _store("store-b", "板橋 B 店", region_code="NTPC-BANQIAO"),
        _store("store-a", "信義 A 店"),
        _store("store-closed", "已關店", store_status="closed"),
        _store("store-other-tenant", "別人家的店", tenant_id="tenant-other"),
    ]
    alerts = {
        "store-a": [
            _alert("alert-red", "store-a", "red"),
            _alert("alert-yellow-closed", "store-a", "yellow", status="resolved"),
        ],
        "store-b": [
            _alert("alert-orange", "store-b", "orange", alert_reason_code="data_quality_stale"),
            _alert("alert-green", "store-b", "green", alert_reason_code="within_expected_band"),
        ],
    }

    result = _materialize(document_store, stores=stores, alerts=alerts)

    assert result.status == "written"
    assert result.store_count == 2
    assert result.issue_count == 2
    state = document_store.documents[STATE_KEY]
    stores_by_id = {store["id"]: store for store in state["stores"]}
    assert list(stores_by_id) == ["store-a", "store-b"]
    assert stores_by_id["store-a"]["name"] == "信義 A 店"
    assert stores_by_id["store-a"]["district"] == "TPE-XINYI"
    assert stores_by_id["store-a"]["lights"] == {
        "demand": "red",
        "operations": "red",
        "staffing": "green",
        "margin": "green",
    }
    assert stores_by_id["store-a"]["lightSources"]["staffing"] is None
    assert stores_by_id["store-a"]["lightSources"]["margin"] is None
    assert stores_by_id["store-a"]["riskScore"] == 40
    assert stores_by_id["store-b"]["lights"]["demand"] == "yellow"
    assert stores_by_id["store-b"]["lights"]["operations"] == "red"
    assert stores_by_id["store-b"]["riskScore"] == 30

    issues_by_id = {issue["id"]: issue for issue in state["issues"]}
    assert set(issues_by_id) == {"ISS-FC-alert-red", "ISS-FC-alert-orange"}
    red_issue = issues_by_id["ISS-FC-alert-red"]
    assert red_issue["storeId"] == "store-a"
    assert red_issue["severity"] == "critical"
    assert red_issue["source"] == "forecastOps"
    assert red_issue["status"] == "new"
    assert red_issue["slaDueAt"] == "2026-10-01T12:00:00+00:00"
    assert red_issue["evidenceIds"] == ["EV-FC-alert-red"]
    assert issues_by_id["ISS-FC-alert-orange"]["severity"] == "high"
    assert {item["issueId"] for item in state["evidence"]} == set(issues_by_id)
    assert state["nextAuditOrdinal"] == MATERIALIZED_AUDIT_ORDINAL_START

    with _client(document_store) as client:
        summary = client.get(f"{BASE}/store-ops/summary", headers=_headers())
        issues = client.get(f"{BASE}/store-ops/issues", headers=_headers())

    assert summary.status_code == 200
    demand = next(row for row in summary.json()["fourLightSummary"] if row["dimension"] == "demand")
    assert demand["counts"] == {"green": 0, "yellow": 1, "red": 1}
    assert issues.status_code == 200
    assert issues.json()["count"] == 2
    _assert_no_fixture_ids(summary.text + issues.text)


# ---------------------------------------------------------------------------
# Idempotency and operator lifecycle preservation
# ---------------------------------------------------------------------------


def test_materializer_is_idempotent_and_skips_unchanged_writes() -> None:
    document_store = LivePostgresDocumentStore()
    stores = [_store("store-a", "信義 A 店")]
    alerts = {"store-a": [_alert("alert-red", "store-a", "red")]}

    first = _materialize(document_store, stores=stores, alerts=alerts)
    snapshot = copy.deepcopy(document_store.documents[STATE_KEY])
    second = _materialize(document_store, stores=stores, alerts=alerts)

    assert first.status == "written"
    assert second.status == "unchanged"
    assert second.new_issue_count == 0
    assert document_store.put_calls == [STATE_KEY]
    assert document_store.documents[STATE_KEY] == snapshot


def test_rerun_keeps_operator_lifecycle_and_appends_new_alert_issues() -> None:
    document_store = LivePostgresDocumentStore()
    stores = [_store("store-a", "信義 A 店")]
    alerts = {"store-a": [_alert("alert-red", "store-a", "red")]}
    _materialize(document_store, stores=stores, alerts=alerts)

    service = StoreOpsService(
        repository=DurableStoreOpsRepository(document_store),
        require_live_data=True,
    )
    service.transition_issue(
        issue_id="ISS-FC-alert-red",
        action_type="triage",
        payload={"severity": "high"},
        correlation_id="corr-triage",
    )
    triaged = copy.deepcopy(document_store.documents[STATE_KEY])
    assert triaged["auditEvents"][0]["id"] == f"AUD-OPS-{MATERIALIZED_AUDIT_ORDINAL_START}"

    alerts["store-a"].append(
        _alert("alert-yellow", "store-a", "yellow", opened_at=datetime(2026, 10, 2, tzinfo=UTC))
    )
    stores[0] = _store("store-a", "信義 A 店（改名）")
    result = _materialize(document_store, stores=stores, alerts=alerts)

    assert result.status == "written"
    assert result.new_issue_count == 1
    state = document_store.documents[STATE_KEY]
    issues_by_id = {issue["id"]: issue for issue in state["issues"]}
    assert issues_by_id["ISS-FC-alert-red"]["status"] == "triaged"
    assert issues_by_id["ISS-FC-alert-red"]["severity"] == "high"
    assert issues_by_id["ISS-FC-alert-red"]["storeName"] == "信義 A 店（改名）"
    assert issues_by_id["ISS-FC-alert-yellow"]["status"] == "new"
    assert state["auditEvents"] == triaged["auditEvents"]
    assert state["nextAuditOrdinal"] == triaged["nextAuditOrdinal"]


# ---------------------------------------------------------------------------
# Fail-closed semantics are unchanged
# ---------------------------------------------------------------------------


def test_read_failure_raises_without_writing_and_api_still_returns_503() -> None:
    document_store = LivePostgresDocumentStore()
    _materialize(document_store)
    document_store.fail_reads = True
    document_store.put_calls.clear()

    with pytest.raises(StoreOpsLiveDataUnavailable, match="repository read failed"):
        _materialize(document_store, stores=[_store("store-a", "信義 A 店")])
    assert document_store.put_calls == []

    with _client(document_store) as client:
        response = client.get(f"{BASE}/store-ops/summary", headers=_headers())

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "STORE_OPS_LIVE_DATA_UNAVAILABLE"
    assert "read failed" in response.json()["detail"]["message"]


def test_canonical_source_failure_raises_without_writing() -> None:
    class BrokenStoreRepository:
        def list_stores(self, **_kwargs: Any) -> list[Store]:
            raise RuntimeError("core.stores unavailable")

    document_store = LivePostgresDocumentStore()

    with pytest.raises(StoreOpsLiveDataUnavailable, match="could not read stores"):
        materialize_store_ops_state(
            repository=DurableStoreOpsRepository(document_store),
            store_repository=BrokenStoreRepository(),
            forecastops_repository=FakeForecastOpsRepository(),
            tenant_id=TENANT_ID,
        )
    assert document_store.put_calls == []


def test_refuses_to_overwrite_persisted_fixture_state() -> None:
    fixture_state = InMemoryStoreOpsRepository().get_state()
    document_store = LivePostgresDocumentStore({STATE_KEY: fixture_state})

    with pytest.raises(StoreOpsLiveDataUnavailable, match="fixture identifiers"):
        _materialize(document_store, stores=[_store("store-a", "信義 A 店")])

    assert document_store.put_calls == []
    with _client(document_store) as client:
        response = client.get(f"{BASE}/store-ops/summary", headers=_headers())
    assert response.status_code == 503
    assert response.json()["detail"]["message"] == (
        "Store Ops production state contains canonical fixture identifiers"
    )


def test_non_live_repository_is_skipped_and_keeps_seed_state() -> None:
    repository = InMemoryStoreOpsRepository()
    before = repository.get_state()

    result = materialize_store_ops_state(
        repository=repository,
        store_repository=FakeStoreRepository([_store("store-a", "信義 A 店")]),
        forecastops_repository=FakeForecastOpsRepository(),
        tenant_id=TENANT_ID,
    )

    assert result.status == "skipped"
    assert repository.get_state() == before


def test_missing_tenant_is_refused_before_any_write() -> None:
    document_store = LivePostgresDocumentStore()

    with pytest.raises(StoreOpsLiveDataUnavailable, match="tenant scope"):
        materialize_store_ops_state(
            repository=DurableStoreOpsRepository(document_store),
            store_repository=FakeStoreRepository([]),
            forecastops_repository=FakeForecastOpsRepository(),
            tenant_id="  ",
        )
    assert document_store.put_calls == []


# ---------------------------------------------------------------------------
# Scheduler registration and worker handler
# ---------------------------------------------------------------------------


def _live_bundle(document_store: LivePostgresDocumentStore, stores: list[Store]):
    return replace(
        build_persistence(mode="memory"),
        store_ops_repository=DurableStoreOpsRepository(document_store),
        store_repository=FakeStoreRepository(stores),
        forecastops_repository=FakeForecastOpsRepository(),
    )


def test_scheduler_registers_store_ops_job_only_for_live_repository() -> None:
    live = ODayScheduler(
        persistence=_live_bundle(LivePostgresDocumentStore(), []),
        tenant_id=TENANT_ID,
        env={FACADE_MODE_ENV: CUTOVER_MODE_PLATFORM_PRIMARY},
    )
    legacy_live = ODayScheduler(
        persistence=_live_bundle(LivePostgresDocumentStore(), []),
        tenant_id=TENANT_ID,
        env={FACADE_MODE_ENV: CUTOVER_MODE_LEGACY_ONLY},
    )
    memory = ODayScheduler(
        persistence=build_persistence(mode="memory"),
        tenant_id=TENANT_ID,
        env={FACADE_MODE_ENV: CUTOVER_MODE_PLATFORM_PRIMARY},
    )

    assert live.recurring_job_types() == (STORE_OPS_MATERIALIZE_JOB_TYPE,)
    assert legacy_live.recurring_job_types() == (
        STORE_OPS_MATERIALIZE_JOB_TYPE,
        EXTERNAL_FETCH_JOB_TYPE,
    )
    assert memory.recurring_job_types() == ()


def test_scheduler_tick_enqueues_job_and_worker_materializes_state() -> None:
    from apps.worker.oday_worker.main import ODayWorker

    document_store = LivePostgresDocumentStore()
    bundle = _live_bundle(document_store, [_store("store-a", "信義 A 店")])
    scheduler = ODayScheduler(
        persistence=bundle,
        tenant_id=TENANT_ID,
        env={FACADE_MODE_ENV: CUTOVER_MODE_PLATFORM_PRIMARY},
    )
    enqueued: list[Any] = []
    original_enqueue = scheduler.job_queue.enqueue

    def tracking_enqueue(request: Any, **kwargs: Any) -> Any:
        enqueued.append(request)
        return original_enqueue(request, **kwargs)

    scheduler.job_queue.enqueue = tracking_enqueue
    scheduler.run_once()

    assert [request.job_type for request in enqueued] == [STORE_OPS_MATERIALIZE_JOB_TYPE]
    assert enqueued[0].payload == {"tenant_id": TENANT_ID}
    assert enqueued[0].idempotency_key.startswith(f"store-ops-materialize:{TENANT_ID}:")

    assert ODayWorker(persistence=bundle).run_once() is True

    state = document_store.documents[STATE_KEY]
    assert [store["id"] for store in state["stores"]] == ["store-a"]
    _assert_no_fixture_ids(repr(state))


def test_worker_registry_handles_store_ops_job() -> None:
    assert build_default_registry().has(STORE_OPS_MATERIALIZE_JOB_TYPE)


def test_worker_handler_refuses_job_without_tenant() -> None:
    document_store = LivePostgresDocumentStore()
    job = JobRecord(
        job_id="job-store-ops",
        job_type=STORE_OPS_MATERIALIZE_JOB_TYPE,
        payload={},
        correlation_id="corr-store-ops-no-tenant",
    )

    with pytest.raises(NonRetryableJobError):
        handle_store_ops_materialize(job, _live_bundle(document_store, []))
    assert document_store.put_calls == []
