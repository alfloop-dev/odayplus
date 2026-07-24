from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from modules.opsboard.application.operator_state import OperatorStateService
from shared.audit.events import AuditEvent
from shared.domain.models import (
    AddressLocation,
    Brand,
    Store,
    Tenant,
    Transaction,
)
from shared.infrastructure.persistence.assisted_listing_intake import (
    apply_upgrade_to_database,
)
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import build_persistence
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.infrastructure.persistence.repositories import TenantScopeRequiredError
from shared.jobs.queue import JobRequest

pytestmark = pytest.mark.skipif(
    not os.environ.get("INTAKE_TEST_DATABASE_URL"),
    reason="INTAKE_TEST_DATABASE_URL is not configured",
)


def _apply_canonical_schema(database_url: str) -> None:
    engine = PostgresEngine(
        database_url,
        bootstrap=False,
        validate_schema=False,
    )
    try:
        migration = (
            Path(__file__).resolve().parents[2]
            / "infra"
            / "db"
            / "migrations"
            / "000002_data_domain_canonical_entities.sql"
        )
        engine.execute(migration.read_text(encoding="utf-8"))
        engine.apply_runtime_migration()
    finally:
        engine.close()


def test_postgresql_document_audit_and_job_contracts() -> None:
    database_url = os.environ["INTAKE_TEST_DATABASE_URL"]
    engine = PostgresEngine(
        database_url,
        validate_schema=False,
    )
    collection = f"postgres.integration.{uuid4()}"
    correlation_id = f"corr-{uuid4()}"
    tenant_a = f"tenant-{uuid4()}"
    tenant_b = f"tenant-{uuid4()}"
    try:
        store = SqliteDocumentStore(engine)  # shared engine-neutral contract
        store.put(collection, "doc-1", {"source": "postgresql"})
        assert store.get(collection, "doc-1") == {"source": "postgresql"}

        audit = DurableAuditLog(engine)
        event_a = audit.record(
            AuditEvent(
                event_type="postgres.integration.v1",
                actor="integration-test",
                action="verify",
                resource=collection,
                outcome="completed",
                correlation_id=correlation_id,
                metadata={"tenant_id": tenant_a},
            )
        )
        event_b = audit.record(
            AuditEvent(
                event_type="postgres.integration.v1",
                actor="integration-test",
                action="verify",
                resource=collection,
                outcome="completed",
                correlation_id=correlation_id,
                metadata={"tenant_id": tenant_b},
            )
        )
        assert [
            item.event_id
            for item in audit.list_events(
                correlation_id=correlation_id,
                tenant_id=tenant_a,
            )
        ] == [
            event_a.event_id
        ]
        assert event_b.event_id not in {
            item.event_id for item in audit.list_events(tenant_id=tenant_a)
        }

        queue = DurableJobQueue(engine)
        request_a = JobRequest(
            job_type="postgres.integration",
            payload={"collection": collection, "tenant_id": tenant_a},
            idempotency_key=f"idem-{uuid4()}",
        )
        request_b = JobRequest(
            job_type="postgres.integration",
            payload={"collection": collection, "tenant_id": tenant_b},
            idempotency_key=f"idem-{uuid4()}",
        )
        first, created = queue.enqueue(request_a, correlation_id=correlation_id)
        replay, created_again = queue.enqueue(
            request_a,
            correlation_id=correlation_id,
        )
        queue.enqueue(request_b, correlation_id=correlation_id)
        assert created is True
        assert created_again is False
        assert replay.job_id == first.job_id
        assert queue.count_active_jobs(tenant_id=tenant_a) == 1
        assert queue.count_active_jobs(tenant_id=tenant_b) == 1
    finally:
        try:
            engine.execute(
                "DELETE FROM durable_documents WHERE collection = ?",
                (collection,),
            )
        finally:
            engine.close()


def test_factory_builds_production_bundle_against_canonical_core_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ["INTAKE_TEST_DATABASE_URL"]
    _apply_canonical_schema(database_url)

    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODAY_DATABASE_URL", database_url)
    apply_upgrade_to_database(database_url)
    bundle = build_persistence(mode="postgresql")
    tenant = Tenant(tenant_name="PostgreSQL integration")
    try:
        assert bundle.mode == "postgresql"
        assert bundle.is_production is True
        bundle.tenant_repository.save_tenant(tenant)
        restored = bundle.tenant_repository.get_tenant(tenant.tenant_id)
        assert restored is not None
        assert restored.tenant_name == "PostgreSQL integration"
    finally:
        bundle.engine.execute(
            "DELETE FROM core.tenants WHERE tenant_id = ?",
            (tenant.tenant_id,),
        )
        bundle.engine.close()


def test_postgresql_address_store_and_transaction_contracts_are_tenant_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ["INTAKE_TEST_DATABASE_URL"]
    _apply_canonical_schema(database_url)
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODAY_DATABASE_URL", database_url)
    bundle = build_persistence(mode="postgresql")

    tenant_a = Tenant(tenant_name=f"Tenant A {uuid4()}")
    tenant_b = Tenant(tenant_name=f"Tenant B {uuid4()}")
    brand_a = Brand(
        tenant_id=tenant_a.tenant_id,
        brand_code=f"brand-a-{uuid4()}",
        brand_name="Brand A",
    )
    brand_b = Brand(
        tenant_id=tenant_b.tenant_id,
        brand_code=f"brand-b-{uuid4()}",
        brand_name="Brand B",
    )
    address_a = AddressLocation(
        raw_address="台北市信義區測試路 1 號",
        normalized_address="台北市信義區測試路1號",
        city="台北市",
        district="信義區",
        latitude=25.033964,
        longitude=121.564468,
        geocode_precision="rooftop",
        geocode_confidence=0.98,
        manual_override_flag=True,
    )
    address_b = AddressLocation(
        raw_address="高雄市前鎮區測試路 2 號",
        normalized_address="高雄市前鎮區測試路2號",
        city="高雄市",
        district="前鎮區",
        latitude=22.595484,
        longitude=120.307655,
        geocode_precision="rooftop",
        geocode_confidence=0.97,
        manual_override_flag=False,
    )
    store_a = Store(
        tenant_id=tenant_a.tenant_id,
        brand_id=brand_a.brand_id,
        store_name="Tenant A Store",
        store_status="open",
        address_id=address_a.address_id,
        region_code="north",
        is_current=True,
    )
    store_b = Store(
        tenant_id=tenant_b.tenant_id,
        brand_id=brand_b.brand_id,
        store_name="Tenant B Store",
        store_status="open",
        address_id=address_b.address_id,
        region_code="south",
        is_current=False,
    )
    transaction_a = Transaction(
        store_id=store_a.store_id,
        event_time=datetime.now(UTC),
        observation_time=datetime.now(UTC),
        net_amount=110.0,
        source_system="pos",
    )
    transaction_b = Transaction(
        store_id=store_b.store_id,
        event_time=datetime.now(UTC),
        observation_time=datetime.now(UTC),
        net_amount=220.0,
        source_system="pos",
    )

    try:
        for tenant in (tenant_a, tenant_b):
            bundle.tenant_repository.save_tenant(tenant)
        for brand in (brand_a, brand_b):
            bundle.brand_repository.save_brand(brand)
        for address in (address_a, address_b):
            bundle.address_location_repository.save_address(address)
        for store in (store_a, store_b):
            bundle.store_repository.save_store(store)
        for transaction in (transaction_a, transaction_b):
            bundle.transaction_repository.save_transaction(transaction)

        spatial = bundle.engine.query_one(
            "SELECT ST_X(geom) AS longitude, ST_Y(geom) AS latitude, "
            "pg_typeof(geom)::text AS geom_type, "
            "pg_typeof(manual_override_flag)::text AS bool_type, "
            "manual_override_flag "
            "FROM core.address_locations WHERE address_id = ?",
            (address_a.address_id,),
        )
        assert spatial is not None
        assert spatial["longitude"] == pytest.approx(address_a.longitude)
        assert spatial["latitude"] == pytest.approx(address_a.latitude)
        assert spatial["geom_type"] == "geometry"
        assert spatial["bool_type"] == "boolean"
        assert spatial["manual_override_flag"] is True

        stored_boolean = bundle.engine.query_one(
            "SELECT pg_typeof(is_current)::text AS bool_type, is_current "
            "FROM core.stores WHERE store_id = ?",
            (store_b.store_id,),
        )
        assert stored_boolean == {"bool_type": "boolean", "is_current": False}

        stores_a = bundle.store_repository.list_stores(
            tenant_id=tenant_a.tenant_id,
        )
        stores_b = bundle.store_repository.list_stores(
            tenant_id=tenant_b.tenant_id,
        )
        assert [store.store_id for store in stores_a] == [store_a.store_id]
        assert [store.store_id for store in stores_b] == [store_b.store_id]
        assert (
            bundle.store_repository.list_stores(
                tenant_id=tenant_a.tenant_id,
                region_codes=("south",),
            )
            == []
        )

        transactions_a = bundle.transaction_repository.list_transactions(
            tenant_id=tenant_a.tenant_id,
        )
        transactions_b = bundle.transaction_repository.list_transactions(
            tenant_id=tenant_b.tenant_id,
        )
        assert [item.transaction_id for item in transactions_a] == [
            transaction_a.transaction_id
        ]
        assert [item.transaction_id for item in transactions_b] == [
            transaction_b.transaction_id
        ]

        operator = OperatorStateService(
            require_live_data=True,
            persistence_mode="postgresql",
            provider_mode="live",
            live_repository=OperatorLiveRepository(bundle),
        )
        tenant_a_envelope = operator.get_today(
            role_id="ops-lead",
            tenant_id=tenant_a.tenant_id,
        )
        tenant_b_envelope = operator.get_today(
            role_id="ops-lead",
            tenant_id=tenant_b.tenant_id,
        )
        assert tenant_a_envelope["meta"]["tenantId"] == tenant_a.tenant_id
        assert tenant_b_envelope["meta"]["tenantId"] == tenant_b.tenant_id
        assert tenant_a_envelope["meta"]["recordCounts"]["stores"] == 1
        assert tenant_b_envelope["meta"]["recordCounts"]["stores"] == 1
        tenant_a_kpis = {
            item["label"]: item["value"] for item in tenant_a_envelope["kpis"]
        }
        tenant_b_kpis = {
            item["label"]: item["value"] for item in tenant_b_envelope["kpis"]
        }
        assert tenant_a_kpis["交易淨額"] == "110.00"
        assert tenant_b_kpis["交易淨額"] == "220.00"
        assert store_b.store_id not in str(tenant_a_envelope)
        assert transaction_b.transaction_id not in str(tenant_a_envelope)

        with pytest.raises(TenantScopeRequiredError):
            bundle.store_repository.list_stores()
        with pytest.raises(TenantScopeRequiredError):
            bundle.transaction_repository.list_transactions()
    finally:
        bundle.engine.execute(
            "DELETE FROM core.transactions WHERE transaction_id IN (?, ?)",
            (transaction_a.transaction_id, transaction_b.transaction_id),
        )
        bundle.engine.execute(
            "DELETE FROM core.stores WHERE store_id IN (?, ?)",
            (store_a.store_id, store_b.store_id),
        )
        bundle.engine.execute(
            "DELETE FROM core.address_locations WHERE address_id IN (?, ?)",
            (address_a.address_id, address_b.address_id),
        )
        bundle.engine.execute(
            "DELETE FROM core.brands WHERE brand_id IN (?, ?)",
            (brand_a.brand_id, brand_b.brand_id),
        )
        bundle.engine.execute(
            "DELETE FROM core.tenants WHERE tenant_id IN (?, ?)",
            (tenant_a.tenant_id, tenant_b.tenant_id),
        )
        bundle.engine.close()
