"""Durable repository and persistence wiring (ODP-PV-009).

These tests exercise the four task acceptance criteria:

1. Product API defaults can run against durable E2E database storage.
2. Repository interfaces remain compatible with domain/application code.
3. API/workflow writes survive process restart in E2E.
4. Core decision entities persist audit/correlation metadata.

"Process restart" is simulated by closing the durable engine and building a
fresh persistence bundle pointed at the same on-disk SQLite file, then reading
the data back through the public interfaces.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.forecastops import (
    ForecastInput,
    ForecastOpsService,
    StoreDayObservation,
)
from shared.audit.events import AuditEvent
from shared.infrastructure.persistence import (
    DurableForecastOpsRepository,
    PersistenceBundle,
    SqliteDocumentStore,
    SqliteEngine,
    build_persistence,
)
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.jobs.queue import JobRequest

PREDICTION_TIME = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "durable.sqlite3")


def _observation(day: int, revenue: float) -> StoreDayObservation:
    return StoreDayObservation(
        store_id="store-001",
        business_date=date(2026, 6, day),
        actual_revenue=revenue,
        machine_cycles=int(revenue / 100),
        site_score_baseline_p50=100_000.0,
        source_snapshot_ids=(f"pos-202606{day:02d}",),
    )


# -- factory / backend selection ----------------------------------------------


def test_factory_defaults_to_in_memory(monkeypatch) -> None:
    monkeypatch.delenv("ODP_PERSISTENCE", raising=False)
    bundle = build_persistence()
    assert bundle.mode == "memory"
    assert not bundle.is_durable


def test_factory_selects_durable_from_env(monkeypatch, db_path) -> None:
    monkeypatch.setenv("ODP_PERSISTENCE", "durable")
    monkeypatch.setenv("ODP_DB_PATH", db_path)
    bundle = build_persistence()
    try:
        assert bundle.mode == "durable"
        assert bundle.is_durable
        assert isinstance(bundle, PersistenceBundle)
    finally:
        bundle.engine.close()


# -- application-layer compatibility + restart survival -----------------------


def test_forecast_service_writes_survive_restart(db_path) -> None:
    """A ForecastOpsService backed by the durable repo behaves identically,
    and its writes are readable after a simulated restart."""
    bundle = _durable_bundle(db_path)
    try:
        service = ForecastOpsService(repository=bundle.forecastops_repository)
        observations = tuple(_observation(day, 80_000 - day * 2_000) for day in range(20, 27))
        result = service.forecast(
            [
                ForecastInput(
                    store_id="store-001",
                    observations=observations,
                    prediction_origin_time=PREDICTION_TIME,
                )
            ]
        )
        # Interface compatibility: same versioning semantics as in-memory repo.
        assert result.forecasts[0].forecast_version == 1
        first_id = result.forecasts[0].forecast_output_id
    finally:
        bundle.engine.close()

    # --- simulated process restart: fresh bundle, same file ---
    reopened = _durable_bundle(db_path)
    try:
        repo = reopened.forecastops_repository
        assert isinstance(repo, DurableForecastOpsRepository)
        latest = repo.latest_forecasts()
        assert len(latest) == 1
        assert latest[0].forecast_output_id == first_id
        assert len(repo.history("store-001")) == 1
        assert len(repo.list_alerts()) >= 1
    finally:
        reopened.engine.close()


def test_document_store_versioning_and_kv_survive_restart(db_path) -> None:
    """The shared document-store primitive preserves grouped versioning,
    insertion order, and key/value upserts across a restart."""
    engine = SqliteEngine(db_path)
    store = SqliteDocumentStore(engine)

    # Grouped, versioned appends within one group_key.
    store.append_version("reports", "r1", {"v": 1}, group_key="case-1")
    store.append_version("reports", "r2", {"v": 2}, group_key="case-1")
    # Key/value upsert keeps insertion order across an in-place update.
    store.put("cases", "case-1", {"name": "alpha"})
    store.put("cases", "case-2", {"name": "beta"})
    store.put("cases", "case-1", {"name": "alpha-2"})  # in-place update
    assert store.count_in_group("reports", "case-1") == 2
    engine.close()

    reopened = SqliteEngine(db_path)
    store2 = SqliteDocumentStore(reopened)
    history = store2.list_by_group("reports", "case-1")
    assert [d["v"] for d in history] == [1, 2]
    assert store2.latest_in_group("reports", "case-1") == {"v": 2}
    # Insertion order preserved; updated value reflected.
    assert [c["name"] for c in store2.list_all("cases")] == ["alpha-2", "beta"]
    reopened.close()


# -- API wiring: defaults run on durable storage, metadata persists -----------


def test_api_jobs_and_audit_persist_across_restart(db_path) -> None:
    bundle = _durable_bundle(db_path)
    correlation_id = "corr-pv-009"
    try:
        app = create_app(persistence=bundle)
        client = TestClient(app)
        resp = client.post(
            "/jobs",
            json={"job_type": "forecast", "payload": {"store_id": "store-001"}},
            headers={"X-Correlation-ID": correlation_id, "Idempotency-Key": "idem-1"},
        )
        assert resp.status_code == 202
        body = resp.json()
        job_id = body["job_id"]
        assert body["created"] is True
        assert body["correlation_id"] == correlation_id
    finally:
        bundle.engine.close()

    # --- simulated restart: brand-new app + bundle on the same DB file ---
    reopened = _durable_bundle(db_path)
    try:
        app2 = create_app(persistence=reopened)
        client2 = TestClient(app2)

        # The job written before restart is still retrievable.
        got = client2.get(f"/jobs/{job_id}")
        assert got.status_code == 200
        assert got.json()["correlation_id"] == correlation_id

        # Decision/audit metadata persisted and is queryable by correlation id.
        events = client2.get("/audit/events", params={"correlation_id": correlation_id}).json()
        assert len(events["events"]) == 1
        assert events["events"][0]["event_type"] == "job.enqueue"
        assert events["events"][0]["correlation_id"] == correlation_id

        # Idempotent replay after restart returns the original job, no dup.
        replay = client2.post(
            "/jobs",
            json={"job_type": "forecast", "payload": {"store_id": "store-001"}},
            headers={"X-Correlation-ID": "different", "Idempotency-Key": "idem-1"},
        )
        assert replay.status_code == 202
        assert replay.json()["created"] is False
        assert replay.json()["job_id"] == job_id
    finally:
        reopened.engine.close()


def test_durable_audit_log_filters_by_correlation(db_path) -> None:
    bundle = _durable_bundle(db_path)
    try:
        log = bundle.audit_log
        log.record(_audit("a", "corr-A"))
        log.record(_audit("b", "corr-B"))
        log.record(_audit("c", "corr-A"))
        assert len(log.list_events()) == 3
        assert len(log.list_events(correlation_id="corr-A")) == 2
        assert len(log.list_events(correlation_id="corr-B")) == 1
    finally:
        bundle.engine.close()


def _audit(action: str, correlation_id: str) -> AuditEvent:
    return AuditEvent(
        event_type="test.event",
        actor="tester",
        action=action,
        resource="resource/1",
        outcome="ok",
        correlation_id=correlation_id,
    )


def test_durable_job_queue_idempotency_survives_restart(db_path) -> None:
    bundle = _durable_bundle(db_path)
    try:
        rec, created = bundle.job_queue.enqueue(
            JobRequest(job_type="forecast", payload={"k": 1}, idempotency_key="key-1"),
            correlation_id="corr-1",
        )
        assert created is True
        job_id = rec.job_id
    finally:
        bundle.engine.close()

    reopened = _durable_bundle(db_path)
    try:
        # Restart: original record is intact, replay does not duplicate.
        assert reopened.job_queue.get(job_id) is not None
        replay, created = reopened.job_queue.enqueue(
            JobRequest(job_type="forecast", payload={"k": 1}, idempotency_key="key-1"),
            correlation_id="corr-2",
        )
        assert created is False
        assert replay.job_id == job_id
    finally:
        reopened.engine.close()


def test_product_domain_writes_survive_restart(db_path) -> None:
    from shared.domain.models import Tenant, Brand, AddressLocation, Store, Machine, Transaction, MachineCycle
    from datetime import date, datetime, time, UTC
    import uuid

    # 1. Write data to the durable bundle
    bundle = _durable_bundle(db_path)
    try:
        tenant_id = str(uuid.uuid4())
        brand_id = str(uuid.uuid4())
        address_id = str(uuid.uuid4())
        store_id = str(uuid.uuid4())
        machine_id = str(uuid.uuid4())
        tx_id = str(uuid.uuid4())
        cycle_id = str(uuid.uuid4())

        tenant = Tenant(tenant_id=tenant_id, tenant_name="ODay Group")
        brand = Brand(brand_id=brand_id, tenant_id=tenant_id, brand_code="ODAY", brand_name="ODay Laundry")
        address = AddressLocation(address_id=address_id, raw_address="Taipei 101", city="Taipei", latitude=25.033, longitude=121.564)
        store = Store(
            store_id=store_id, tenant_id=tenant_id, brand_id=brand_id, store_name="Xinyi Store",
            address_id=address_id, opened_on=date(2026, 6, 26), service_start_time=time(8, 0), service_end_time=time(22, 0)
        )
        machine = Machine(
            machine_id=machine_id, store_id=store_id, source_machine_id="M01",
            machine_family="washer", capacity_kg=15.0, installed_on=date(2026, 6, 26)
        )
        tx = Transaction(
            transaction_id=tx_id, store_id=store_id, machine_id=machine_id,
            gross_amount=150.0, net_amount=150.0, source_system="POS"
        )
        cycle = MachineCycle(
            cycle_id=cycle_id, store_id=store_id, machine_id=machine_id, transaction_id=tx_id,
            cycle_start_time=datetime(2026, 7, 11, 3, 0, tzinfo=UTC), cycle_end_time=datetime(2026, 7, 11, 3, 30, tzinfo=UTC),
            cycle_type="wash", duration_sec=1800
        )

        bundle.tenant_repository.save_tenant(tenant)
        bundle.brand_repository.save_brand(brand)
        bundle.address_location_repository.save_address(address)
        bundle.store_repository.save_store(store)
        bundle.machine_repository.save_machine(machine)
        bundle.transaction_repository.save_transaction(tx)
        bundle.machine_cycle_repository.save_machine_cycle(cycle)

    finally:
        bundle.engine.close()

    # 2. Simulated process restart: reopen on the same DB file and verify
    reopened = _durable_bundle(db_path)
    try:
        t = reopened.tenant_repository.get_tenant(tenant_id)
        assert t is not None
        assert t.tenant_name == "ODay Group"

        b = reopened.brand_repository.get_brand(brand_id)
        assert b is not None
        assert b.brand_code == "ODAY"

        a = reopened.address_location_repository.get_address(address_id)
        assert a is not None
        assert a.raw_address == "Taipei 101"

        s = reopened.store_repository.get_store(store_id)
        assert s is not None
        assert s.store_name == "Xinyi Store"
        assert s.opened_on == date(2026, 6, 26)
        assert s.service_start_time == time(8, 0)

        m = reopened.machine_repository.get_machine(machine_id)
        assert m is not None
        assert m.source_machine_id == "M01"

        tx_ret = reopened.transaction_repository.get_transaction(tx_id)
        assert tx_ret is not None
        assert tx_ret.gross_amount == 150.0

        c = reopened.machine_cycle_repository.get_machine_cycle(cycle_id)
        assert c is not None
        assert c.cycle_type == "wash"
        assert c.duration_sec == 1800

        # Verify list methods
        assert len(reopened.tenant_repository.list_tenants()) == 1
        assert len(reopened.brand_repository.list_brands()) == 1
        assert len(reopened.address_location_repository.list_addresses()) == 1
        assert len(reopened.store_repository.list_stores()) == 1
        assert len(reopened.machine_repository.list_machines()) == 1
        assert len(reopened.transaction_repository.list_transactions()) == 1
        assert len(reopened.machine_cycle_repository.list_machine_cycles()) == 1

    finally:
        reopened.engine.close()
