"""ODP-STOREOPS-STATE-MATERIALIZER-001: the production Store Ops state writer.

On PostgreSQL ``DurableStoreOpsRepository.get_state`` fails closed until a
tenant's Store Ops state document exists, and every Store Ops write reads that
document first, so the materializer is the only path that can end the 503.
These tests pin that it writes an honest projection (empty when there are no
stores, never fixtures) into the tenant's own partition, is idempotent, never
rolls back an interleaved operator write, leaves the fail-closed read semantics
intact, and is scheduled + handled.

The document store is the production ``PostgresDocumentStore`` class over a
SQLite engine that reports the PostgreSQL dialect, so the versioned
compare-and-swap runs as real SQL against ``durable_documents``.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
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
    StoreOpsConcurrentWriteConflict,
    StoreOpsLiveDataUnavailable,
    StoreOpsService,
)
from modules.opsboard.application.store_ops_materializer import (
    MATERIALIZED_AUDIT_ORDINAL_START,
    empty_store_ops_state,
    materialize_store_ops_state,
)
from shared.audit import InMemoryAuditLog
from shared.domain.models import Store
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.factory import build_persistence
from shared.infrastructure.persistence.postgresql import PostgresDocumentStore
from shared.jobs.queue import JobRecord, NonRetryableJobError

TENANT_ID = "tenant-store-ops-live"
OTHER_TENANT_ID = "tenant-store-ops-other"
BASE = "/api/v1/operator"
STATE_COLLECTION = "opsboard.store_ops.state"
STATE_DOC_ID = "default"


def _state_collection(tenant_id: str = TENANT_ID) -> str:
    partition = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
    return f"{STATE_COLLECTION}.tenant.{partition}"


class _PostgresDialectSqliteEngine(SqliteEngine):
    dialect = "postgresql"


class LivePostgresDocumentStore(PostgresDocumentStore):
    """The production document store class, reporting PostgreSQL, on SQLite.

    Records state writes, can fail reads, and can run a hook right before the
    next compare-and-swap to interleave a competing writer deterministically.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(_PostgresDialectSqliteEngine(path))  # type: ignore[arg-type]
        self.put_calls: list[str] = []
        self.fail_reads = False
        self.before_next_write: Callable[[], None] | None = None

    def _check_read(self) -> None:
        if self.fail_reads:
            raise ConnectionError("postgres unreachable")

    def get(self, collection: str, doc_id: str) -> Any:
        self._check_read()
        return super().get(collection, doc_id)

    def get_versioned(self, collection: str, doc_id: str) -> tuple[Any, int] | None:
        self._check_read()
        return super().get_versioned(collection, doc_id)

    def compare_and_put(
        self, collection: str, doc_id: str, obj: Any, *, expected_version: int | None
    ) -> int | None:
        hook, self.before_next_write = self.before_next_write, None
        if hook is not None:
            hook()
        self.put_calls.append(collection)
        return super().compare_and_put(
            collection, doc_id, obj, expected_version=expected_version
        )

    def state(self, tenant_id: str = TENANT_ID) -> Any:
        return super().get(_state_collection(tenant_id), STATE_DOC_ID)


@pytest.fixture
def document_store(tmp_path: Path) -> LivePostgresDocumentStore:
    return LivePostgresDocumentStore(tmp_path / "store-ops.db")


class FakeStoreRepository:
    def __init__(self, stores: list[Store]) -> None:
        self.stores = stores
        self.calls: list[str | None] = []
        self.on_list: Callable[[], None] | None = None

    def list_stores(self, *, tenant_id: str | None = None, **_kwargs: Any) -> list[Store]:
        self.calls.append(tenant_id)
        hook, self.on_list = self.on_list, None
        if hook is not None:
            hook()
        return [store for store in self.stores if store.tenant_id == tenant_id]


class FakeForecastOpsRepository:
    def __init__(self, alerts: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.alerts = alerts or {}

    def list_alerts_by_store(self, tenant_id: str, store_id: str) -> list[dict[str, Any]]:
        return [
            alert
            for alert in self.alerts.get(store_id, [])
            if alert.get("tenant_id") == tenant_id
        ]


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
        "tenant_id": overrides.pop("tenant_id", TENANT_ID),
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
    tenant_id: str = TENANT_ID,
    store_repository: FakeStoreRepository | None = None,
):
    return materialize_store_ops_state(
        repository=DurableStoreOpsRepository(document_store),
        store_repository=store_repository or FakeStoreRepository(stores or []),
        forecastops_repository=FakeForecastOpsRepository(alerts),
        tenant_id=tenant_id,
    )


def _client(
    document_store: LivePostgresDocumentStore,
    *,
    require_live_data: bool | None = True,
) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def correlation_id(request: Request, call_next: Any) -> Any:
        request.state.correlation_id = "corr-store-ops-materialized"
        return await call_next(request)

    app.include_router(
        create_operator_store_ops_router(
            repository=DurableStoreOpsRepository(document_store),
            require_live_data=require_live_data,
        ),
        prefix="/api/v1",
    )
    return TestClient(app)


def _service(
    document_store: LivePostgresDocumentStore, tenant_id: str = TENANT_ID
) -> StoreOpsService:
    return StoreOpsService(
        repository=DurableStoreOpsRepository(document_store).for_tenant(tenant_id),
        require_live_data=True,
    )


def _headers(tenant_id: str = TENANT_ID) -> dict[str, str]:
    return {
        "x-subject-id": f"operator-{tenant_id}",
        "x-roles": "operations_manager,site_reviewer,expansion_user",
        "x-operator-role": "ops-lead",
        "x-tenant-id": tenant_id,
        "x-correlation-id": f"corr-{tenant_id}",
    }


def _assert_no_fixture_ids(text: str) -> None:
    leaked = sorted(fixture_id for fixture_id in _STORE_OPS_FIXTURE_IDS if fixture_id in text)
    assert leaked == []


# ---------------------------------------------------------------------------
# Empty canonical stores
# ---------------------------------------------------------------------------


def test_empty_core_stores_writes_honest_empty_state_and_api_returns_200(document_store: LivePostgresDocumentStore) -> None:

    result = _materialize(document_store)

    assert result.status == "written"
    assert result.reason == "created"
    assert document_store.put_calls == [_state_collection()]
    assert document_store.state() == empty_store_ops_state()

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


def test_unmaterialized_state_still_returns_503(document_store: LivePostgresDocumentStore) -> None:
    with _client(document_store) as client:
        response = client.get(f"{BASE}/store-ops/summary", headers=_headers())

    assert response.status_code == 503
    assert response.json()["detail"]["message"] == (
        "Store Ops production state has not been materialized"
    )


# ---------------------------------------------------------------------------
# Projection from canonical stores and ForecastOps alerts
# ---------------------------------------------------------------------------


def test_projects_stores_four_lights_and_issues_without_fixture_ids(document_store: LivePostgresDocumentStore) -> None:
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
    state = document_store.state()
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


def test_materializer_is_idempotent_and_skips_unchanged_writes(document_store: LivePostgresDocumentStore) -> None:
    stores = [_store("store-a", "信義 A 店")]
    alerts = {"store-a": [_alert("alert-red", "store-a", "red")]}

    first = _materialize(document_store, stores=stores, alerts=alerts)
    snapshot = copy.deepcopy(document_store.state())
    second = _materialize(document_store, stores=stores, alerts=alerts)

    assert first.status == "written"
    assert second.status == "unchanged"
    assert second.new_issue_count == 0
    assert document_store.put_calls == [_state_collection()]
    assert document_store.state() == snapshot


def test_rerun_keeps_operator_lifecycle_and_appends_new_alert_issues(document_store: LivePostgresDocumentStore) -> None:
    stores = [_store("store-a", "信義 A 店")]
    alerts = {"store-a": [_alert("alert-red", "store-a", "red")]}
    _materialize(document_store, stores=stores, alerts=alerts)

    _service(document_store).transition_issue(
        issue_id="ISS-FC-alert-red",
        action_type="triage",
        payload={"severity": "high"},
        correlation_id="corr-triage",
    )
    triaged = copy.deepcopy(document_store.state())
    assert triaged["auditEvents"][0]["id"] == f"AUD-OPS-{MATERIALIZED_AUDIT_ORDINAL_START}"

    alerts["store-a"].append(
        _alert("alert-yellow", "store-a", "yellow", opened_at=datetime(2026, 10, 2, tzinfo=UTC))
    )
    stores[0] = _store("store-a", "信義 A 店（改名）")
    result = _materialize(document_store, stores=stores, alerts=alerts)

    assert result.status == "written"
    assert result.new_issue_count == 1
    state = document_store.state()
    issues_by_id = {issue["id"]: issue for issue in state["issues"]}
    assert issues_by_id["ISS-FC-alert-red"]["status"] == "triaged"
    assert issues_by_id["ISS-FC-alert-red"]["severity"] == "high"
    assert issues_by_id["ISS-FC-alert-red"]["storeName"] == "信義 A 店（改名）"
    assert issues_by_id["ISS-FC-alert-yellow"]["status"] == "new"
    assert state["auditEvents"] == triaged["auditEvents"]
    assert state["nextAuditOrdinal"] == triaged["nextAuditOrdinal"]


# ---------------------------------------------------------------------------
# Concurrent writers: no lost updates in either direction
# ---------------------------------------------------------------------------


def _triage(document_store: LivePostgresDocumentStore) -> None:
    _service(document_store).transition_issue(
        issue_id="ISS-FC-alert-red",
        action_type="triage",
        payload={"severity": "high"},
        correlation_id="corr-interleaved-triage",
    )


def test_operator_write_during_canonical_reads_survives_materialization(
    document_store: LivePostgresDocumentStore,
) -> None:
    alerts = {"store-a": [_alert("alert-red", "store-a", "red")]}
    _materialize(document_store, stores=[_store("store-a", "信義 A 店")], alerts=alerts)
    renamed = FakeStoreRepository([_store("store-a", "信義 A 店（改名）")])
    renamed.on_list = lambda: _triage(document_store)

    result = _materialize(document_store, alerts=alerts, store_repository=renamed)

    assert result.status == "written"
    state = document_store.state()
    issue = state["issues"][0]
    assert issue["status"] == "triaged"
    assert issue["storeName"] == "信義 A 店（改名）"
    assert [event["action"] for event in state["auditEvents"]] == ["issue.triage"]
    assert state["nextAuditOrdinal"] == MATERIALIZED_AUDIT_ORDINAL_START + 1


def test_operator_write_between_read_and_save_forces_reprojection(
    document_store: LivePostgresDocumentStore,
) -> None:
    alerts = {"store-a": [_alert("alert-red", "store-a", "red")]}
    _materialize(document_store, stores=[_store("store-a", "信義 A 店")], alerts=alerts)
    _service(document_store).add_attachment(
        issue_id="ISS-FC-alert-red",
        payload={"filename": "before.txt"},
        tenant_id=TENANT_ID,
    )
    # The operator write lands after the materializer read the state and right
    # before its save: the stale save must be refused and the projection redone.
    document_store.before_next_write = lambda: _triage(document_store)
    document_store.put_calls.clear()

    result = _materialize(
        document_store, stores=[_store("store-a", "信義 A 店（改名）")], alerts=alerts
    )

    assert result.status == "written"
    state = document_store.state()
    assert state["issues"][0]["status"] == "triaged"
    assert state["issues"][0]["storeName"] == "信義 A 店（改名）"
    assert [event["action"] for event in state["auditEvents"]] == [
        "attachment.uploaded",
        "issue.triage",
    ]
    assert [item["filename"] for item in state["attachments"]] == ["before.txt"]
    assert state["nextAuditOrdinal"] == MATERIALIZED_AUDIT_ORDINAL_START + 2
    # materializer attempt (refused), the triage, materializer retry.
    assert len(document_store.put_calls) == 3


def test_materialization_between_operator_read_and_save_keeps_both(
    document_store: LivePostgresDocumentStore,
) -> None:
    alerts = {"store-a": [_alert("alert-red", "store-a", "red")]}
    _materialize(document_store, stores=[_store("store-a", "信義 A 店")], alerts=alerts)
    alerts["store-a"].append(
        _alert("alert-yellow", "store-a", "yellow", opened_at=datetime(2026, 10, 2, tzinfo=UTC))
    )
    document_store.before_next_write = lambda: _materialize(
        document_store, stores=[_store("store-a", "信義 A 店（改名）")], alerts=alerts
    )

    _triage(document_store)

    state = document_store.state()
    issues_by_id = {issue["id"]: issue for issue in state["issues"]}
    assert issues_by_id["ISS-FC-alert-red"]["status"] == "triaged"
    assert issues_by_id["ISS-FC-alert-red"]["storeName"] == "信義 A 店（改名）"
    assert issues_by_id["ISS-FC-alert-yellow"]["status"] == "new"
    assert [store["name"] for store in state["stores"]] == ["信義 A 店（改名）"]
    assert [event["action"] for event in state["auditEvents"]] == ["issue.triage"]


def test_write_gives_up_without_saving_when_state_never_settles(
    document_store: LivePostgresDocumentStore,
) -> None:
    alerts = {"store-a": [_alert("alert-red", "store-a", "red")]}
    _materialize(document_store, stores=[_store("store-a", "信義 A 店")], alerts=alerts)
    before = document_store.state()

    class AlwaysStale(LivePostgresDocumentStore):
        def compare_and_put(self, *args: Any, **kwargs: Any) -> int | None:
            return None

    contended = AlwaysStale.__new__(AlwaysStale)
    contended.__dict__.update(document_store.__dict__)

    with pytest.raises(StoreOpsConcurrentWriteConflict):
        _triage(contended)
    with pytest.raises(StoreOpsConcurrentWriteConflict):
        _materialize(contended, stores=[_store("store-a", "改名")], alerts=alerts)
    assert document_store.state() == before


# ---------------------------------------------------------------------------
# Tenant isolation on the production router path
# ---------------------------------------------------------------------------


def _production_client(
    document_store: LivePostgresDocumentStore, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """Mount the router exactly as ``apps/api/oday_api/main.py`` does.

    Production passes only the bundle repository and audit log; live mode comes
    from the runtime environment, and the bundle repository is the unscoped
    ``DurableStoreOpsRepository`` over ``PostgresDocumentStore``.
    """
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    app = FastAPI()

    @app.middleware("http")
    async def correlation_id(request: Request, call_next: Any) -> Any:
        request.state.correlation_id = "corr-store-ops-tenants"
        return await call_next(request)

    app.include_router(
        create_operator_store_ops_router(
            repository=DurableStoreOpsRepository(document_store),
            audit_log=InMemoryAuditLog(),
        ),
        prefix="/api/v1",
    )
    return TestClient(app)


def test_tenants_never_see_or_overwrite_each_others_state(
    document_store: LivePostgresDocumentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = [
        _store("store-a", "信義 A 店"),
        _store("store-b", "板橋 B 店", tenant_id=OTHER_TENANT_ID),
    ]
    alerts = {
        "store-a": [_alert("alert-a", "store-a", "red")],
        "store-b": [_alert("alert-b", "store-b", "red", tenant_id=OTHER_TENANT_ID)],
    }
    _materialize(document_store, stores=stores, alerts=alerts)

    with _production_client(document_store, monkeypatch) as client:
        other_before = client.get(
            f"{BASE}/store-ops/summary", headers=_headers(OTHER_TENANT_ID)
        )
        other_issue = client.get(
            f"{BASE}/store-ops/issues/ISS-FC-alert-a", headers=_headers(OTHER_TENANT_ID)
        )
        # Tenant B's own (empty) materialization must not touch tenant A.
        assert _materialize(document_store, alerts=alerts, tenant_id=OTHER_TENANT_ID).status == (
            "written"
        )
        own = client.get(f"{BASE}/store-ops/issues", headers=_headers())
        other = client.get(f"{BASE}/store-ops/issues", headers=_headers(OTHER_TENANT_ID))
        cross_write = client.post(
            f"{BASE}/store-ops/issues/ISS-FC-alert-a/triage",
            headers=_headers(OTHER_TENANT_ID),
            json={"payload": {"severity": "low"}},
        )
        own_write = client.post(
            f"{BASE}/store-ops/issues/ISS-FC-alert-a/triage",
            headers={**_headers(), "Idempotency-Key": "shared-key"},
            json={"payload": {"severity": "high"}},
        )

    assert other_before.status_code == 503
    assert other_before.json()["detail"]["message"] == (
        "Store Ops production state has not been materialized"
    )
    assert "store-a" not in other_before.text
    assert other_issue.status_code == 503

    assert own.status_code == 200
    assert [issue["id"] for issue in own.json()["issues"]] == ["ISS-FC-alert-a"]
    assert [store["id"] for store in own.json()["stores"]] == ["store-a"]
    assert other.status_code == 200
    assert other.json()["issues"] == []
    assert other.json()["stores"] == []
    assert "store-a" not in other.text and "alert-a" not in other.text

    assert cross_write.status_code == 404
    assert own_write.status_code == 200
    assert document_store.state()["issues"][0]["status"] == "triaged"
    assert document_store.state(OTHER_TENANT_ID)["issues"] == []
    assert document_store.state(OTHER_TENANT_ID)["auditEvents"] == []

    # Idempotency results are partitioned too: the same key in another tenant
    # is not a replay of tenant A's transition.
    assert (
        DurableStoreOpsRepository(document_store)
        .for_tenant(OTHER_TENANT_ID)
        .get_idempotency_result("shared-key")
        is None
    )
    assert (
        DurableStoreOpsRepository(document_store)
        .for_tenant(TENANT_ID)
        .get_idempotency_result("shared-key")
        is not None
    )


def test_production_never_reads_or_writes_an_unscoped_document(
    document_store: LivePostgresDocumentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A legacy global document must neither be served nor overwritten.
    document_store.put(STATE_COLLECTION, STATE_DOC_ID, empty_store_ops_state())
    unscoped = DurableStoreOpsRepository(document_store)

    with pytest.raises(StoreOpsLiveDataUnavailable, match="authenticated tenant scope"):
        unscoped.get_state()
    with pytest.raises(StoreOpsLiveDataUnavailable, match="authenticated tenant scope"):
        unscoped.save_state(empty_store_ops_state())
    with _production_client(document_store, monkeypatch) as client:
        response = client.get(f"{BASE}/store-ops/summary", headers=_headers())

    assert response.status_code == 503
    assert response.json()["detail"]["message"] == (
        "Store Ops production state has not been materialized"
    )
    assert document_store.put_calls == []


# ---------------------------------------------------------------------------
# Fail-closed semantics are unchanged
# ---------------------------------------------------------------------------


def test_read_failure_raises_without_writing_and_api_still_returns_503(document_store: LivePostgresDocumentStore) -> None:
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


def test_canonical_source_failure_raises_without_writing(document_store: LivePostgresDocumentStore) -> None:
    class BrokenStoreRepository:
        def list_stores(self, **_kwargs: Any) -> list[Store]:
            raise RuntimeError("core.stores unavailable")


    with pytest.raises(StoreOpsLiveDataUnavailable, match="could not read stores"):
        materialize_store_ops_state(
            repository=DurableStoreOpsRepository(document_store),
            store_repository=BrokenStoreRepository(),
            forecastops_repository=FakeForecastOpsRepository(),
            tenant_id=TENANT_ID,
        )
    assert document_store.put_calls == []


def test_refuses_to_overwrite_persisted_fixture_state(
    document_store: LivePostgresDocumentStore,
) -> None:
    fixture_state = InMemoryStoreOpsRepository().get_state()
    document_store.put(_state_collection(), STATE_DOC_ID, fixture_state)

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


def test_missing_tenant_is_refused_before_any_write(document_store: LivePostgresDocumentStore) -> None:

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


def test_scheduler_registers_store_ops_job_only_for_live_repository(
    document_store: LivePostgresDocumentStore,
) -> None:
    live = ODayScheduler(
        persistence=_live_bundle(document_store, []),
        tenant_id=TENANT_ID,
        env={FACADE_MODE_ENV: CUTOVER_MODE_PLATFORM_PRIMARY},
    )
    legacy_live = ODayScheduler(
        persistence=_live_bundle(document_store, []),
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


def test_scheduler_tick_enqueues_job_and_worker_materializes_state(document_store: LivePostgresDocumentStore) -> None:
    from apps.worker.oday_worker.main import ODayWorker

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

    state = document_store.state()
    assert [store["id"] for store in state["stores"]] == ["store-a"]
    _assert_no_fixture_ids(repr(state))


def test_worker_registry_handles_store_ops_job() -> None:
    assert build_default_registry().has(STORE_OPS_MATERIALIZE_JOB_TYPE)


def test_worker_handler_refuses_job_without_tenant(document_store: LivePostgresDocumentStore) -> None:
    job = JobRecord(
        job_id="job-store-ops",
        job_type=STORE_OPS_MATERIALIZE_JOB_TYPE,
        payload={},
        correlation_id="corr-store-ops-no-tenant",
    )

    with pytest.raises(NonRetryableJobError):
        handle_store_ops_materialize(job, _live_bundle(document_store, []))
    assert document_store.put_calls == []
