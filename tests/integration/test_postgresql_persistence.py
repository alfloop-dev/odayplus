from __future__ import annotations

import os
from uuid import uuid4

import pytest

from shared.audit.events import AuditEvent
from shared.domain.models import Tenant
from shared.infrastructure.persistence.assisted_listing_intake import (
    apply_upgrade_to_database,
)
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import build_persistence
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.jobs.queue import JobRequest

pytestmark = pytest.mark.skipif(
    not os.environ.get("INTAKE_TEST_DATABASE_URL"),
    reason="INTAKE_TEST_DATABASE_URL is not configured",
)


def test_postgresql_document_audit_and_job_contracts() -> None:
    database_url = os.environ["INTAKE_TEST_DATABASE_URL"]
    engine = PostgresEngine(
        database_url,
        validate_schema=False,
    )
    collection = f"postgres.integration.{uuid4()}"
    correlation_id = f"corr-{uuid4()}"
    try:
        store = SqliteDocumentStore(engine)  # shared engine-neutral contract
        store.put(collection, "doc-1", {"source": "postgresql"})
        assert store.get(collection, "doc-1") == {"source": "postgresql"}

        audit = DurableAuditLog(engine)
        event = audit.record(
            AuditEvent(
                event_type="postgres.integration.v1",
                actor="integration-test",
                action="verify",
                resource=collection,
                outcome="completed",
                correlation_id=correlation_id,
            )
        )
        assert [item.event_id for item in audit.list_events(correlation_id=correlation_id)] == [
            event.event_id
        ]

        queue = DurableJobQueue(engine)
        request = JobRequest(
            job_type="postgres.integration",
            payload={"collection": collection},
            idempotency_key=f"idem-{uuid4()}",
        )
        first, created = queue.enqueue(request, correlation_id=correlation_id)
        replay, created_again = queue.enqueue(request, correlation_id=correlation_id)
        assert created is True
        assert created_again is False
        assert replay.job_id == first.job_id
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
    bootstrap = PostgresEngine(database_url, validate_schema=False)
    try:
        bootstrap.execute(
            """
            CREATE SCHEMA IF NOT EXISTS core;
            CREATE TABLE IF NOT EXISTS core.tenants (
                tenant_id UUID PRIMARY KEY,
                tenant_name TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            );
            CREATE TABLE IF NOT EXISTS core.brands (brand_id UUID PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS core.address_locations (address_id UUID PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS core.stores (store_id UUID PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS core.machines (machine_id UUID PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS core.transactions (transaction_id UUID PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS core.machine_cycles (cycle_id UUID PRIMARY KEY);
            """
        )
    finally:
        bootstrap.close()

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
