"""Live Store Ops availability contracts and explicitly separate CAS engineering.

Live nonempty issue workflows remain available with partial nullable health.
Missing KPI sources are not healthy measurements. Empty stores stay empty.
The live-path fixture is the original SQL-backed PostgreSQL-dialect unit double;
it does not establish real PostgreSQL, canonical KPI or deployment acceptance.

Nonempty projection/CAS/typed-history assertions use an ordinary SQLite engine
and the pure projector. Their results are not native PostgreSQL acceptance.
No fixture source label or timestamp supplies a missing production owner.
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
    StoreOpsMaterializationResult,
    empty_store_ops_state,
    materialize_store_ops_state,
    project_store_ops_state,
)
from modules.opsboard.application.store_ops_scope import ScopedStoreOpsRepository, binding_hash
from modules.opsboard.domain.store_ops_reviews import TriageRecord
from shared.audit import InMemoryAuditLog
from shared.domain.models import Store
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.factory import build_persistence
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
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
    """SQLite unit double; it does not verify PostgreSQL advisory exclusion."""

    dialect = "postgresql"

    def __init__(self, path: Path) -> None:
        self.advisory_calls: list[tuple[Any, ...]] = []
        super().__init__(path)

    def execute(self, sql: str, params: tuple = ()) -> Any:
        if sql == "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))":
            self.advisory_calls.append(params)
            return super().execute("SELECT 1")
        return super().execute(sql, params)

    def query_one(self, sql: str, params: tuple = ()) -> Any:
        if sql == "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))":
            self.advisory_calls.append(params)
            return super().query_one("SELECT 1")
        return super().query_one(sql, params)


class LivePostgresDocumentStore(PostgresDocumentStore):
    """The production document store class, reporting PostgreSQL, on SQLite.

    Records state writes, can fail reads, and can run a hook right before the
    next compare-and-swap to interleave a competing writer deterministically.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(_PostgresDialectSqliteEngine(path))  # type: ignore[arg-type]
        # Explicit canonical test owner, never reconstructed from the view document.
        self.canonical_store_repository = FakeStoreRepository([])
        self.put_calls: list[str] = []
        self.canonical_forecastops_repository = FakeForecastOpsRepository()
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


class EngineeringSqliteDocumentStore(SqliteDocumentStore):
    """Ordinary SQLite with deterministic write hooks; never a live owner.

    Only the CAS/projection engineering tests below use this fixture. Its
    intermediate four-light values cannot satisfy the production read guard.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(SqliteEngine(path))
        self.put_calls: list[str] = []
        self.fail_reads = False
        self.before_next_write: Callable[[], None] | None = None

    def _check_read(self) -> None:
        if self.fail_reads:
            raise ConnectionError("sqlite test read unavailable")

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
def engineering_document_store(tmp_path: Path) -> EngineeringSqliteDocumentStore:
    return EngineeringSqliteDocumentStore(tmp_path / "engineering-cas.sqlite")


def _project_and_cas_engineering(
    document_store: EngineeringSqliteDocumentStore,
    *,
    stores: list[Store] | None = None,
    alerts: dict[str, list[dict[str, Any]]] | None = None,
    tenant_id: str = TENANT_ID,
    store_repository: FakeStoreRepository | None = None,
) -> StoreOpsMaterializationResult:
    """Pure projection + actual SQLite CAS; explicitly not the live materializer.

    The writer hook exercises the shared repository version boundary without
    claiming that missing canonical staffing/margin data has become available.
    """
    repository = DurableStoreOpsRepository(document_store).for_tenant(tenant_id)
    assert repository.is_live is False
    canonical = store_repository or FakeStoreRepository(stores or [])
    projected_stores = canonical.list_stores(tenant_id=tenant_id)
    alerts_by_store = {
        store.store_id: FakeForecastOpsRepository(alerts).list_alerts_by_store(tenant_id, store.store_id)
        for store in projected_stores
    }
    for _attempt in range(8):
        existing, version = repository.read_versioned_state()
        state = project_store_ops_state(
            stores=projected_stores, alerts_by_store=alerts_by_store, existing_state=existing
        )
        fields = {
            "store_count": len(state["stores"]), "issue_count": len(state["issues"]),
            "new_issue_count": len(state["issues"]) - len(existing.get("issues", [])) if existing else len(state["issues"]),
        }
        if existing is not None and state == existing:
            return StoreOpsMaterializationResult(status="unchanged", reason="engineering-only", **fields)
        if repository.compare_and_save_state(state, expected_version=version):
            return StoreOpsMaterializationResult(status="written", reason="engineering-only", **fields)
    raise StoreOpsConcurrentWriteConflict("Engineering projection CAS did not settle")


def _engineering_service(
    document_store: EngineeringSqliteDocumentStore, tenant_id: str = TENANT_ID
) -> StoreOpsService:
    repository = DurableStoreOpsRepository(document_store).for_tenant(tenant_id)
    assert repository.is_live is False
    return StoreOpsService(repository=repository, require_live_data=False)


class FakeStoreRepository:
    def __init__(self, stores: list[Store]) -> None:
        self.stores = stores
        self.calls: list[str | None] = []
        self.on_list: Callable[[], None] | None = None

    def list_stores(
        self, *, tenant_id: str | None = None, store_ids: tuple[str, ...] = (),
        brand_ids: tuple[str, ...] = (), region_codes: tuple[str, ...] = (),
    ) -> list[Store]:
        self.calls.append(tenant_id)
        hook, self.on_list = self.on_list, None
        if hook is not None:
            hook()
        return [
            store for store in self.stores if store.tenant_id == tenant_id
            and (not store_ids or store.store_id in store_ids)
            and (not brand_ids or store.brand_id in brand_ids)
            and (not region_codes or store.region_code in region_codes)
        ]


class FakeForecastOpsRepository:
    def __init__(self, alerts: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.alerts = alerts or {}
        self.calls: list[tuple[str, str]] = []

    def list_alerts_by_store(self, tenant_id: str, store_id: str) -> list[dict[str, Any]]:
        self.calls.append((tenant_id, store_id))
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
    canonical_stores = store_repository or FakeStoreRepository(stores or [])
    # Retain the actual typed input masters independently of projected state.
    # A later empty tenant materialization must not erase another tenant's owner.
    known = {
        (store.tenant_id, store.store_id): store
        for store in document_store.canonical_store_repository.stores
    }
    known.update({
        (store.tenant_id, store.store_id): store for store in getattr(canonical_stores, "stores", [])
    })
    document_store.canonical_store_repository.stores = list(known.values())
    document_store.canonical_forecastops_repository.alerts = alerts or {}
    return materialize_store_ops_state(
        repository=DurableStoreOpsRepository(document_store),
        store_repository=canonical_stores,
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
            store_repository=document_store.canonical_store_repository,
            forecastops_repository=document_store.canonical_forecastops_repository,
            audit_log=DurableAuditLog(document_store.engine),
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
        forecastops_repository=document_store.canonical_forecastops_repository,
        tenant_id=tenant_id,
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
    assert summary.json()["healthAvailability"] == {
        "state": "available", "complete": True, "totalCount": 0,
        "coveredCount": 0, "unavailableCount": 0, "warningCount": 0,
    }
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
        "operations": None,
        "staffing": None,
        "margin": None,
    }
    assert stores_by_id["store-a"]["lightSources"]["staffing"] is None
    assert stores_by_id["store-a"]["lightSources"]["margin"] is None
    assert stores_by_id["store-a"]["riskScore"] is None
    assert stores_by_id["store-b"]["lights"]["demand"] == "yellow"
    assert stores_by_id["store-b"]["lights"]["operations"] is None
    assert stores_by_id["store-b"]["riskScore"] is None

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
    assert all(item["confidence"] is None for item in state["evidence"])
    assert state["nextAuditOrdinal"] == MATERIALIZED_AUDIT_ORDINAL_START

    with _client(document_store) as client:
        summary = client.get(f"{BASE}/store-ops/summary", headers=_headers())
        issues = client.get(f"{BASE}/store-ops/issues", headers=_headers())

    assert summary.status_code == 200
    demand = next(row for row in summary.json()["fourLightSummary"] if row["dimension"] == "demand")
    assert demand["counts"] == {"green": 0, "yellow": 1, "red": 1}
    assert summary.json()["healthAvailability"] == {
        "state": "degraded", "complete": False, "totalCount": 2,
        "coveredCount": 0, "unavailableCount": 2, "warningCount": 2,
    }
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

    Inject the same canonical store and transactional audit capabilities as
    the scoped router. This remains an explicit SQLite unit double, including
    its advisory-call recorder; it is not native PostgreSQL acceptance.
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
            store_repository=document_store.canonical_store_repository,
            forecastops_repository=document_store.canonical_forecastops_repository,
            audit_log=DurableAuditLog(document_store.engine),
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
    # Nonempty tenant/operator/idempotency isolation remains covered by the
    # explicit SQLite engineering interleavings and namespace control below.


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


# Scoped Operator writes must use the same SQL CAS as the recurring materializer.
# These are explicitly the existing SQLite-backed document-store test fixture,
# not evidence of a PostgreSQL deployment or a human review.
def _scoped_engineering_service(
    document_store: EngineeringSqliteDocumentStore,
) -> tuple[StoreOpsService, ScopedStoreOpsRepository, InMemoryAuditLog]:
    repository = ScopedStoreOpsRepository(
        DurableStoreOpsRepository(TenantScopedDocumentStore(document_store, TENANT_ID)),
        store_ids=frozenset({"store-a"}),
        subject_id="synthetic-scoped-operator",
        scope_hash=binding_hash({"tenantId": TENANT_ID, "storeIds": ["store-a"]}),
        request_hash=binding_hash({"issueId": "ISS-FC-alert-red", "severity": "high"}),
        operation="triage",
    )
    audit_log = InMemoryAuditLog()
    return (
        StoreOpsService(
            repository=repository,
            audit_log=audit_log,
            require_live_data=False,
            actor_subject_id="synthetic-scoped-operator",
        ),
        repository,
        audit_log,
    )


def _assert_scoped_typed_triage(
    state: dict[str, Any], *, source_updated_at: str
) -> dict[str, Any]:
    issue = next(row for row in state["issues"] if row["id"] == "ISS-FC-alert-red")
    assert issue["status"] == "triaged"
    assert issue["severity"] == "high"
    assert len(issue["workflowRecords"]) == 1
    record = TriageRecord.model_validate(issue["workflowRecords"][0]).model_dump(mode="json")
    assert record["actorSubjectId"] == "synthetic-scoped-operator"
    assert record["actorEvidence"] == "authenticated_principal"
    assert record["sourceIssueUpdatedAt"] == source_updated_at
    assert record["issueId"] == issue["id"]
    assert record["storeId"] == "store-a"
    assert record["evidenceIds"] == issue["evidenceIds"]
    assert record["submission"]["severity"] == "high"
    assert len(state["auditEvents"]) == 1
    audit = state["auditEvents"][0]
    assert audit["id"] == record["id"] == record["workflowAuditId"]
    assert audit["action"] == "issue.triage"
    assert audit["actorSubjectId"] == record["actorSubjectId"]
    assert audit["metadata"]["reviewRecordHash"] == binding_hash(record)
    assert state["nextAuditOrdinal"] == MATERIALIZED_AUDIT_ORDINAL_START + 1
    return record


def test_scoped_operator_cas_retry_preserves_materialized_new_issue_and_hidden_rows(
    engineering_document_store: EngineeringSqliteDocumentStore,
) -> None:
    document_store = engineering_document_store
    stores = [_store("store-a", "A"), _store("store-b", "B")]
    alerts = {
        "store-a": [_alert("alert-red", "store-a", "red")],
        "store-b": [_alert("alert-hidden", "store-b", "yellow")],
    }
    _project_and_cas_engineering(document_store, stores=stores, alerts=alerts)
    before = copy.deepcopy(document_store.state())
    source_updated_at = before["issues"][0]["updatedAt"]
    hidden_issue = next(row for row in before["issues"] if row["storeId"] == "store-b")
    hidden_evidence = [row for row in before["evidence"] if row["issueId"] == hidden_issue["id"]]
    service, scoped, audit_log = _scoped_engineering_service(document_store)
    assert [row["storeId"] for row in scoped.get_state()["issues"]] == ["store-a"]
    alerts["store-a"].append(_alert("alert-new", "store-a", "yellow"))
    document_store.before_next_write = lambda: _project_and_cas_engineering(
        document_store, stores=stores, alerts=alerts
    )
    document_store.put_calls.clear()

    result = service.transition_issue(
        issue_id="ISS-FC-alert-red", action_type="triage", payload={"severity": "high"},
        correlation_id="scoped-after-materializer", actor_name="Synthetic Operator",
    )

    state = document_store.state()
    record = _assert_scoped_typed_triage(state, source_updated_at=source_updated_at)
    assert {row["id"] for row in state["issues"]} == {
        "ISS-FC-alert-red", "ISS-FC-alert-new", "ISS-FC-alert-hidden"
    }
    assert next(row for row in state["issues"] if row["id"] == hidden_issue["id"]) == hidden_issue
    assert [row for row in state["evidence"] if row["issueId"] == hidden_issue["id"]] == hidden_evidence
    assert result["issue"]["workflowRecords"] == [record]
    assert all(row["storeId"] == "store-a" for row in result["issues"])
    assert len(scoped.new_audits) == 1
    shared = audit_log.list_events(correlation_id="scoped-after-materializer")
    assert len(shared) == 1
    assert shared[0].event_id == record["auditEventId"]
    assert shared[0].actor == record["actorSubjectId"]
    assert len(document_store.put_calls) == 3  # failed scoped CAS, materializer, scoped retry


def test_materializer_cas_retry_preserves_scoped_typed_history_audit_and_ordinal(
    engineering_document_store: EngineeringSqliteDocumentStore,
) -> None:
    document_store = engineering_document_store
    stores = [_store("store-a", "A"), _store("store-b", "B")]
    alerts = {
        "store-a": [_alert("alert-red", "store-a", "red")],
        "store-b": [_alert("alert-hidden", "store-b", "yellow")],
    }
    _project_and_cas_engineering(document_store, stores=stores, alerts=alerts)
    before = copy.deepcopy(document_store.state())
    source_updated_at = before["issues"][0]["updatedAt"]
    hidden_issue = next(row for row in before["issues"] if row["storeId"] == "store-b")
    service, scoped, audit_log = _scoped_engineering_service(document_store)
    committed: dict[str, Any] = {}

    def operator_commit() -> None:
        service.transition_issue(
            issue_id="ISS-FC-alert-red", action_type="triage", payload={"severity": "high"},
            correlation_id="scoped-before-materializer", actor_name="Synthetic Operator",
        )
        committed.update(copy.deepcopy(document_store.state()))

    alerts["store-a"].append(_alert("alert-new", "store-a", "yellow"))
    document_store.before_next_write = operator_commit
    document_store.put_calls.clear()
    result = _project_and_cas_engineering(document_store, stores=stores, alerts=alerts)

    assert result.status == "written"
    state = document_store.state()
    _assert_scoped_typed_triage(state, source_updated_at=source_updated_at)
    assert state["auditEvents"] == committed["auditEvents"]
    assert state["nextAuditOrdinal"] == committed["nextAuditOrdinal"]
    assert next(row for row in state["issues"] if row["id"] == hidden_issue["id"]) == hidden_issue
    assert {row["id"] for row in state["issues"]} == {
        "ISS-FC-alert-red", "ISS-FC-alert-new", "ISS-FC-alert-hidden"
    }
    assert next(row for row in state["issues"] if row["id"] == "ISS-FC-alert-red")["workflowRecords"] == (
        next(row for row in committed["issues"] if row["id"] == "ISS-FC-alert-red")["workflowRecords"]
    )
    assert len(scoped.new_audits) == len(audit_log.list_events()) == 1
    assert len(document_store.put_calls) == 3  # failed materializer CAS, operator, retry


def test_prebound_tenant_and_raw_factory_share_state_versions_and_idempotency_namespace(
    document_store: LivePostgresDocumentStore,
) -> None:
    raw = DurableStoreOpsRepository(document_store)
    tenant = raw.for_tenant(TENANT_ID)
    prebound = DurableStoreOpsRepository(TenantScopedDocumentStore(document_store, TENANT_ID))
    assert tenant.tenant_id == prebound.tenant_id == TENANT_ID
    assert tenant.read_versioned_state() == prebound.read_versioned_state() == (None, None)
    state = empty_store_ops_state()
    assert prebound.compare_and_save_state(state, expected_version=None) is True
    assert tenant.read_versioned_state() == prebound.read_versioned_state() == (state, 1)
    updated = copy.deepcopy(state)
    updated["nextAuditOrdinal"] += 1
    assert tenant.compare_and_save_state(updated, expected_version=1) is True
    assert prebound.read_versioned_state() == (updated, 2)
    assert prebound.compare_and_save_state(state, expected_version=1) is False
    prebound.save_idempotency_result("same-command", {"scope": "synthetic-test"})
    assert tenant.get_idempotency_result("same-command") == {"scope": "synthetic-test"}
    assert raw.for_tenant(OTHER_TENANT_ID).read_versioned_state() == (None, None)
    assert raw.for_tenant(OTHER_TENANT_ID).get_idempotency_result("same-command") is None
    with pytest.raises(StoreOpsLiveDataUnavailable, match="another tenant"):
        prebound.for_tenant(OTHER_TENANT_ID)
    with pytest.raises(StoreOpsLiveDataUnavailable, match="another tenant"):
        tenant.for_tenant(OTHER_TENANT_ID)
    foreign_stores = FakeStoreRepository([])
    with pytest.raises(StoreOpsLiveDataUnavailable, match="another tenant"):
        materialize_store_ops_state(
            repository=tenant, store_repository=foreign_stores,
            forecastops_repository=FakeForecastOpsRepository(), tenant_id=OTHER_TENANT_ID,
        )
    assert foreign_stores.calls == []
    with pytest.raises(StoreOpsLiveDataUnavailable, match="tenant scopes differ"):
        DurableStoreOpsRepository(
            TenantScopedDocumentStore(document_store, TENANT_ID), tenant_id=OTHER_TENANT_ID
        )
    assert document_store.state() == updated
    assert document_store.put_calls == [_state_collection(), _state_collection(), _state_collection()]


def test_detached_scoped_save_rejects_stale_snapshot_instead_of_dropping_new_issue(
    engineering_document_store: EngineeringSqliteDocumentStore,
) -> None:
    document_store = engineering_document_store
    stores = [_store("store-a", "A")]
    alerts = {"store-a": [_alert("alert-red", "store-a", "red")]}
    _project_and_cas_engineering(document_store, stores=stores, alerts=alerts)
    _service, scoped, audit_log = _scoped_engineering_service(document_store)
    stale = scoped.get_state()
    alerts["store-a"].append(_alert("alert-new", "store-a", "yellow"))
    _project_and_cas_engineering(document_store, stores=stores, alerts=alerts)
    current = copy.deepcopy(document_store.state())
    with pytest.raises(StoreOpsConcurrentWriteConflict, match="scoped read"):
        scoped.save_state(stale)
    assert document_store.state() == current
    assert len(current["issues"]) == 2
    assert scoped.new_audits == []
    assert audit_log.list_events() == []


def test_live_partial_health_keeps_typed_issue_workflow_and_read_does_not_rewrite_state(
    document_store: LivePostgresDocumentStore,
) -> None:
    _materialize(document_store, stores=[_store("store-a", "A")], alerts={
        "store-a": [_alert("alert-red", "store-a", "red")],
    })
    repository = DurableStoreOpsRepository(document_store).for_tenant(TENANT_ID)
    before = repository.read_versioned_state()
    with _client(document_store) as client:
        summary = client.get(f"{BASE}/store-ops/summary", headers=_headers())
        issues = client.get(f"{BASE}/store-ops/issues", headers=_headers())
        green = client.get(f"{BASE}/store-ops/issues?light=staffing&lightStatus=green", headers=_headers())
        assert summary.status_code == issues.status_code == green.status_code == 200
        assert repository.read_versioned_state() == before
        transition = client.post(
            f"{BASE}/store-ops/issues/ISS-FC-alert-red/triage",
            headers={**_headers(), "Idempotency-Key": "partial-native-triage"},
            json={"payload": {"severity": "high"}},
        )
    assert summary.json()["stores"][0]["lights"] == {
        "demand": "red", "operations": None, "staffing": None, "margin": None,
    }
    assert summary.json()["stores"][0]["riskScore"] is None
    assert summary.json()["healthAvailability"]["warningCount"] == 1
    assert summary.json()["healthAvailability"]["unavailableCount"] == 1
    assert summary.json()["healthAvailability"]["coveredCount"] == 0
    assert issues.json()["count"] == 1 and green.json()["count"] == 0
    assert transition.status_code == 200
    assert transition.json()["issue"]["status"] == "triaged"
    assert transition.json()["auditEvent"]["actorName"] == _headers()["x-subject-id"]
    assert transition.json()["issue"]["workflowRecords"][-1]["actorEvidence"] == "authenticated_principal"
    assert len(document_store.state()["auditEvents"]) == 1


@pytest.mark.parametrize("claimed_sources", ["missing", None, {
    "demand": "forecastops.alerts", "operations": "opsboard.store_ops.issues",
    "staffing": "claimed-owner", "margin": "claimed-owner",
}])
def test_legacy_colors_flags_and_claimed_sources_are_health_only_unknown(
    document_store: LivePostgresDocumentStore, claimed_sources: dict[str, str] | str | None,
) -> None:
    _materialize(document_store, stores=[_store("store-a", "A")], alerts={
        "store-a": [_alert("alert-red", "store-a", "red")],
    })
    state = document_store.state()
    store = state["stores"][0]
    store["lights"] = {dimension: "green" for dimension in ("demand", "operations", "staffing", "margin")}
    store["riskScore"] = 0
    store["healthAvailability"] = {"state": "available", "complete": True}
    if claimed_sources == "missing":
        store.pop("lightSources")
    else:
        store["lightSources"] = claimed_sources
    document_store.put(_state_collection(), STATE_DOC_ID, state)
    # The actual owner now has no observation. Retained issue/evidence refs
    # and arbitrary source metadata cannot keep an old color alive.
    document_store.canonical_forecastops_repository.alerts = {}
    repository = DurableStoreOpsRepository(document_store).for_tenant(TENANT_ID)
    before = repository.read_versioned_state()
    with _client(document_store) as client:
        result = client.get(f"{BASE}/store-ops/issues", headers=_headers())
        filtered = client.get(f"{BASE}/store-ops/issues?light=demand&lightStatus=green", headers=_headers())
    assert result.status_code == filtered.status_code == 200
    body = result.json()
    assert [issue["id"] for issue in body["issues"]] == ["ISS-FC-alert-red"]
    assert all(value is None for value in body["stores"][0]["lights"].values())
    assert body["stores"][0]["riskScore"] is None
    assert body["healthAvailability"] == {
        "state": "unavailable", "complete": False, "totalCount": 1,
        "coveredCount": 0, "unavailableCount": 1, "warningCount": 0,
    }
    assert all(row["counts"]["green"] == 0 and row["unavailableCount"] == 1 for row in body["fourLightSummary"])
    assert filtered.json()["issues"] == []
    assert repository.read_versioned_state() == before
    assert repository.get_state() == state


@pytest.mark.parametrize("replacement", ["closed", "green", "yellow", "absent"])
def test_current_forecast_owner_replaces_retained_alert_health_without_rewriting_issue_history(
    document_store: LivePostgresDocumentStore, replacement: str,
) -> None:
    _materialize(document_store, stores=[_store("store-a", "A")], alerts={
        "store-a": [_alert("alert-red", "store-a", "red")],
    })
    before = copy.deepcopy(document_store.state())
    current = [] if replacement == "absent" else [
        _alert("new-alert" if replacement == "yellow" else "alert-red", "store-a",
               "green" if replacement == "green" else "yellow" if replacement == "yellow" else "red",
               status="closed" if replacement == "closed" else "open"),
    ]
    document_store.canonical_forecastops_repository.alerts = {"store-a": current}
    result = _service(document_store).snapshot()
    expected = "yellow" if replacement == "yellow" else "green" if replacement == "green" else None
    assert result["stores"][0]["lights"]["demand"] == expected
    assert result["healthAvailability"]["warningCount"] == (1 if expected == "yellow" else 0)
    assert result["healthAvailability"]["coveredCount"] == 0
    assert result["healthAvailability"]["complete"] is False
    assert result["issues"][0]["status"] == "new"
    if replacement == "yellow":
        assert result["stores"][0]["healthAvailability"]["lights"]["demand"]["recordIds"] == ["new-alert"]
    assert document_store.state() == before


@pytest.mark.parametrize("failure", ["missing-port", "read-error", "foreign-tenant", "foreign-store", "missing-identity"])
def test_optional_health_owner_failure_is_partial_and_never_grants_foreign_observation(
    document_store: LivePostgresDocumentStore, failure: str,
) -> None:
    _materialize(document_store, stores=[_store("store-a", "A")], alerts={
        "store-a": [_alert("alert-red", "store-a", "red")],
    })

    class CurrentOwner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def list_alerts_by_store(self, tenant_id: str, store_id: str) -> list[Any]:
            self.calls.append((tenant_id, store_id))
            if failure == "read-error":
                raise ConnectionError("private backend details must not be returned")
            row = _alert("current", store_id, "red")
            if failure == "foreign-tenant":
                row["tenant_id"] = OTHER_TENANT_ID
            elif failure == "foreign-store":
                row["store_id"] = "foreign-store"
            elif failure == "missing-identity":
                row.pop("tenant_id")
            return [row]

    owner = CurrentOwner()
    repository = DurableStoreOpsRepository(document_store).for_tenant(TENANT_ID)
    service = StoreOpsService(
        repository=repository, require_live_data=True, tenant_id=TENANT_ID,
        forecastops_repository=None if failure == "missing-port" else owner,
    )
    before = copy.deepcopy(document_store.state())
    result = service.snapshot()
    assert all(value is None for value in result["stores"][0]["lights"].values())
    assert result["healthAvailability"]["warningCount"] == 0
    assert [row["id"] for row in result["issues"]] == ["ISS-FC-alert-red"]
    assert owner.calls == ([] if failure == "missing-port" else [(TENANT_ID, "store-a")])
    assert "private backend" not in str(result)
    assert document_store.state() == before
    document_store.canonical_forecastops_repository = None if failure == "missing-port" else owner
    with _client(document_store) as client:
        transition = client.post(
            f"{BASE}/store-ops/issues/ISS-FC-alert-red/triage", headers=_headers(),
            json={"payload": {"severity": "high"}},
        )
    assert transition.status_code == 200
    assert transition.json()["issue"]["status"] == "triaged"
    assert "private backend" not in transition.text


def test_store_only_partial_and_worker_no_observation_never_create_green_or_risk_zero(
    document_store: LivePostgresDocumentStore,
) -> None:
    job = JobRecord(
        job_id="job-store-ops-partial", job_type=STORE_OPS_MATERIALIZE_JOB_TYPE,
        payload={"tenant_id": TENANT_ID}, correlation_id="corr-store-ops-partial",
    )
    result = handle_store_ops_materialize(job, _live_bundle(document_store, [_store("store-a", "A")]))
    assert result is None
    state = document_store.state()
    assert state["issues"] == []
    assert all(value is None for value in state["stores"][0]["lights"].values())
    assert state["stores"][0]["riskScore"] is None
    document_store.canonical_store_repository = FakeStoreRepository([_store("store-a", "A")])
    with _client(document_store) as client:
        response = client.get(f"{BASE}/store-ops/issues", headers=_headers())
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["stores"]] == ["store-a"]
    assert response.json()["issues"] == []
    assert response.json()["healthAvailability"]["unavailableCount"] == 1


def test_health_read_port_only_reads_current_authorized_store_scope(
    document_store: LivePostgresDocumentStore,
) -> None:
    _materialize(document_store, stores=[_store("store-a", "A"), _store("store-hidden", "Hidden")], alerts={
        "store-a": [_alert("alert-a", "store-a", "yellow")],
        "store-hidden": [_alert("alert-hidden", "store-hidden", "red")],
    })
    owner = document_store.canonical_forecastops_repository
    owner.calls.clear()
    with _client(document_store) as client:
        response = client.get(f"{BASE}/store-ops/issues", headers={**_headers(), "x-store-ids": "store-a"})
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["stores"]] == ["store-a"]
    assert owner.calls == [(TENANT_ID, "store-a")]
    assert "alert-hidden" not in response.text
    assert response.json()["stores"][0]["healthAvailability"]["lights"]["demand"]["recordIds"] == ["alert-a"]


@pytest.mark.parametrize("malformed", [None, [], "unavailable", {"stores": None}, {"stores": {}}])
def test_malformed_live_workflow_still_has_503_before_mutation_or_fixture_probe(
    document_store: LivePostgresDocumentStore, malformed: Any,
) -> None:
    state = malformed
    if isinstance(malformed, dict):
        state = {**empty_store_ops_state(), **malformed}
    document_store.put(_state_collection(), STATE_DOC_ID, state)
    document_store.put_calls.clear()
    repository = DurableStoreOpsRepository(document_store).for_tenant(TENANT_ID)
    called: list[bool] = []
    with pytest.raises(StoreOpsLiveDataUnavailable):
        repository.get_state()
    with pytest.raises(StoreOpsLiveDataUnavailable):
        repository.update_state(lambda value: called.append(True))
    with pytest.raises(StoreOpsLiveDataUnavailable):
        repository.compare_and_save_state(state, expected_version=0)
    assert called == []
    assert document_store.put_calls == []
    assert document_store.state() == state


@pytest.mark.parametrize("field,value", [
    ("lights", {"demand": "blue"}), ("lights", {"demand": []}), ("lights", {"demand": {}}),
    ("riskScore", True), ("riskScore", "0"),
])
def test_illegal_supplied_health_types_are_structural_failures_not_missing_data(
    document_store: LivePostgresDocumentStore, field: str, value: Any,
) -> None:
    _materialize(document_store, stores=[_store("store-a", "A")])
    state = document_store.state()
    state["stores"][0][field] = value
    document_store.put(_state_collection(), STATE_DOC_ID, state)
    document_store.put_calls.clear()
    repository = DurableStoreOpsRepository(document_store).for_tenant(TENANT_ID)
    with pytest.raises(StoreOpsLiveDataUnavailable):
        repository.get_state()
    with pytest.raises(StoreOpsLiveDataUnavailable):
        repository.compare_and_save_state(state, expected_version=0)
    assert document_store.state() == state
    assert document_store.put_calls == []
