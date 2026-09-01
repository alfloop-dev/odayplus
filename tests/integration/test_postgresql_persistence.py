from __future__ import annotations

import os
from datetime import UTC, datetime
from multiprocessing import get_context
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

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
from shared.infrastructure.persistence.job_queue import DurableJobQueue, JobFenceRejectedError
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.infrastructure.persistence.repositories import TenantScopeRequiredError
from shared.jobs.queue import JobRequest, JobStatus

pytestmark = pytest.mark.skipif(
    not os.environ.get("INTAKE_TEST_DATABASE_URL"),
    reason="INTAKE_TEST_DATABASE_URL is not configured",
)


def _provision_canonical_schema(database: Any) -> None:
    """Provision the canonical schema the way the deployment job does.

    This used to hand-pick one file, the data-domain canonical entities
    migration. That subset and ``_REQUIRED_RELATIONS`` were two independent
    lists that had to agree, and they stopped agreeing: ODP-FORECAST-ALERT-
    POLICY-001 made ``workflow.decision_policies`` a required production
    relation and created it in Alembic revision 0008, which this path never
    reached, so the validator demanded a table the test database could not have.

    Running the migration job's own command instead -- ``alembic upgrade head``,
    the command ``build_migration_plan`` publishes -- removes the second list:
    the schema the bundle is validated against is the schema deployment
    produces. It needs a database of its own because revision 0001 is not
    rerunnable, and re-creating the data-domain tables the shared product
    database already has is exactly what it would attempt.
    """

    config = Config("infra/db/migrations/alembic.ini")
    # set_main_option interpolates %, and percent-encoding in the URL (a socket
    # path, a password) would otherwise be read as an interpolation token.
    config.set_main_option(
        "sqlalchemy.url", database.url(driver="psycopg").replace("%", "%%")
    )
    command.upgrade(config, "head")


def _claim_from_independent_process(
    database_url: str,
    worker_id: str,
    start_event,
    result_queue,
) -> None:
    engine = PostgresEngine(
        database_url,
        min_pool_size=1,
        max_pool_size=1,
        bootstrap=False,
        validate_schema=False,
    )
    try:
        start_event.wait(timeout=10)
        claimed = DurableJobQueue(engine).claim_next(worker_id=worker_id)
        result_queue.put(
            None
            if claimed is None
            else (
                claimed.job_id,
                claimed.locked_by,
                claimed.version,
                claimed.fence_token,
            )
        )
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
        ] == [event_a.event_id]
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
                "DELETE FROM durable_jobs WHERE correlation_id = ?",
                (correlation_id,),
            )
            engine.execute(
                "DELETE FROM durable_documents WHERE collection = ?",
                (collection,),
            )
        finally:
            engine.close()


def test_postgresql_claim_is_atomic_across_worker_processes() -> None:
    database_url = os.environ["INTAKE_TEST_DATABASE_URL"]
    engine = PostgresEngine(database_url, validate_schema=False)
    queue = DurableJobQueue(engine)
    job_type = f"postgres.claim.{uuid4()}"
    job, created = queue.enqueue(
        JobRequest(
            job_type=job_type,
            payload={"tenant_id": f"tenant-{uuid4()}"},
            idempotency_key=f"claim-{uuid4()}",
        ),
        correlation_id=f"corr-{uuid4()}",
    )
    assert created is True

    context = get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_claim_from_independent_process,
            args=(database_url, f"worker-{index}", start_event, result_queue),
        )
        for index in range(4)
    ]
    try:
        for process in processes:
            process.start()
        start_event.set()
        results = [result_queue.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0

        claims = [result for result in results if result is not None]
        assert len(claims) == 1
        claimed_job_id, worker_id, claimed_version, claimed_fence = claims[0]
        assert claimed_job_id == job.job_id

        persisted = queue.get(job.job_id)
        assert persisted is not None
        assert persisted.status == JobStatus.RUNNING
        assert persisted.locked_by == worker_id
        assert persisted.version == claimed_version
        assert persisted.fence_token == claimed_fence

        engine.execute(
            "UPDATE durable_jobs SET lease_expires_at = ? WHERE job_id = ?",
            ((datetime.now(UTC).replace(year=2000)).isoformat(), job.job_id),
        )
        replacement = queue.claim_next(worker_id="replacement-worker")
        assert replacement is not None
        assert replacement.job_id == job.job_id
        with pytest.raises(JobFenceRejectedError):
            queue.update_status(
                job.job_id,
                JobStatus.SUCCEEDED,
                expected_version=claimed_version,
                fence_token=claimed_fence,
            )
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        engine.execute("DELETE FROM durable_jobs WHERE job_id = ?", (job.job_id,))
        engine.close()


def test_the_migration_job_provisions_every_relation_the_bundle_requires(
    intake_blank_db: Any,
) -> None:
    """``_REQUIRED_RELATIONS`` and the provisioning path are two lists that must
    agree, and nothing held them together: ``workflow.decision_policies`` became
    required while no path the tests exercised created it. Checked here against
    the migration job's own output, so a relation added to the validator without
    a revision behind it fails on the schema, not on the first bundle to want
    the table."""
    _provision_canonical_schema(intake_blank_db)

    engine = PostgresEngine(
        intake_blank_db.url(), bootstrap=True, validate_schema=False
    )
    try:
        # Raises PostgreSQLSchemaError naming whatever is missing.
        engine.validate_schema()
    finally:
        engine.close()


def test_factory_builds_production_bundle_against_canonical_core_schema(
    intake_blank_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _provision_canonical_schema(intake_blank_db)
    database_url = intake_blank_db.url()

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
        # No row cleanup: the database is this test's own and is dropped with it.
        bundle.engine.close()


def test_postgresql_address_store_and_transaction_contracts_are_tenant_scoped(
    intake_blank_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _provision_canonical_schema(intake_blank_db)
    database_url = intake_blank_db.url()
    apply_upgrade_to_database(database_url)
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
        assert [item.transaction_id for item in transactions_a] == [transaction_a.transaction_id]
        assert [item.transaction_id for item in transactions_b] == [transaction_b.transaction_id]

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
        tenant_a_kpis = {item["label"]: item["value"] for item in tenant_a_envelope["kpis"]}
        tenant_b_kpis = {item["label"]: item["value"] for item in tenant_b_envelope["kpis"]}
        assert tenant_a_kpis["交易淨額"] == "110.00"
        assert tenant_b_kpis["交易淨額"] == "220.00"
        assert store_b.store_id not in str(tenant_a_envelope)
        assert transaction_b.transaction_id not in str(tenant_a_envelope)

        with pytest.raises(TenantScopeRequiredError):
            bundle.store_repository.list_stores()
        with pytest.raises(TenantScopeRequiredError):
            bundle.transaction_repository.list_transactions()
    finally:
        # The row-by-row teardown this had was there to leave the shared product
        # database as it was found. On a database of this test's own it is not
        # only redundant, it cannot succeed: onboarding a tenant now seeds its
        # forecast alert policies, and those rows reference the tenant, so
        # deleting the tenant is refused -- correctly, since a decision cites
        # the policy version that produced it.
        bundle.engine.close()
