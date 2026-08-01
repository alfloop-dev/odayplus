"""Closed-loop external-data ingestion persistence (ODP-FLOW-001).

Acceptance covered:
- scheduled and manual ingestion persist canonical outputs;
- DQ quarantine, lineage, and freshness are queryable;
- API reads persisted run state (not a hardcoded fixture);
- idempotent retry rejection and audit trail hold, including across a
  simulated process restart on the durable backend.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.external_data.application.ingestion_service import ExternalIngestionService
from modules.external_data.application.ingestion_store import InMemoryIngestionRunStore
from modules.external_data.connectors import ExternalProviderMode, ProviderValidationResult
from modules.external_data.providers import ListingPartnerFeedProvider
from modules.external_data.workers.scheduled_fetch import ExternalFetchJobSpec
from shared.audit.events import InMemoryAuditLog
from shared.infrastructure.persistence.factory import _durable_bundle
from tests.integration._authz import EXTERNAL_DATA_HEADERS

WINDOW_START = "2026-06-28T08:00:00Z"
WINDOW_END = "2026-06-28T09:00:00Z"

_DUP_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "source_data"
    / "external"
    / "listing_raw_snapshot.duplicate.json"
)


def _run_payload(**overrides):
    body = {
        "provider_id": "listing.partner_feed",
        "schedule_id": "manual",
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
    }
    body.update(overrides)
    return body


# -- manual ingestion persists canonical outputs & is readable ---------------


def test_manual_ingestion_persists_and_is_readable_via_api() -> None:
    client = TestClient(create_app())

    created = client.post(
        "/external-data/ingestion-runs",
        json=_run_payload(),
        headers={**EXTERNAL_DATA_HEADERS, "Idempotency-Key": "flow-001-run-a"},
    )
    assert created.status_code == 202
    body = created.json()
    assert body["created"] is True
    assert body["status"] == "SUCCEEDED"
    assert body["accepted_count"] == 2
    assert body["canonical_snapshot_id"]
    assert body["audit_event_id"]
    run_id = body["run_id"]

    # Persisted run is retrievable by id, with lineage preserved.
    detail = client.get(f"/external-data/ingestion-runs/{run_id}", headers=EXTERNAL_DATA_HEADERS)
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["run_id"] == run_id
    assert len(detail_body["lineage"]) == 2

    # And it shows up in the list endpoint.
    listing = client.get("/external-data/ingestion-runs", headers=EXTERNAL_DATA_HEADERS)
    assert listing.status_code == 200
    assert listing.json()["count"] == 1


def test_freshness_reads_persisted_run_state(monkeypatch) -> None:
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    client = TestClient(create_app())

    # Explicit POC mode keeps the deterministic fixture for product tests.
    cold = client.get("/external-data/freshness", headers=EXTERNAL_DATA_HEADERS)
    assert cold.json()["freshness"][0]["source_snapshot_id"] == "snap-expansion-20260628-0100"
    assert cold.json()["availability"]["source"] == "fixture"

    client.post(
        "/external-data/ingestion-runs",
        json=_run_payload(),
        headers={**EXTERNAL_DATA_HEADERS, "Idempotency-Key": "flow-001-fresh"},
    )

    warm = client.get("/external-data/freshness", headers=EXTERNAL_DATA_HEADERS)
    fresh = warm.json()["freshness"][0]
    # Now the persisted run's snapshot drives freshness, not the fixture.
    assert fresh["source_snapshot_id"] == "listing-2026-06-26"
    assert fresh["data_status"] == "FRESH"
    assert warm.json()["availability"]["source"] == "persisted"


def test_production_cold_store_fails_closed_without_live_runtime(monkeypatch) -> None:
    monkeypatch.setenv("ODP_PRODUCT_MODE", "production")
    client = TestClient(create_app())

    response = client.get("/external-data/freshness", headers=EXTERNAL_DATA_HEADERS)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "production_runtime_unavailable"
    assert "snap-expansion-20260628-0100" not in response.text


def test_live_provider_mode_never_uses_poc_freshness_fixture(monkeypatch) -> None:
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("ODP_EXTERNAL_PROVIDER_MODE", "live")
    validation = ProviderValidationResult(
        mode=ExternalProviderMode.LIVE,
        correlation_id="corr-live-cold-store",
        providers=(),
    )
    client = TestClient(create_app(external_provider_validation=validation))

    response = client.get(
        "/external-data/freshness",
        headers={**EXTERNAL_DATA_HEADERS, "x-correlation-id": "corr-live-cold-store"},
    )

    assert response.status_code == 200
    assert response.json()["freshness"] == []
    assert response.json()["availability"]["status"] == "UNAVAILABLE"
    assert response.json()["correlation_id"] == "corr-live-cold-store"


# -- idempotent retry rejection + audit --------------------------------------


def test_idempotent_retry_rejection_and_audit() -> None:
    audit_log = InMemoryAuditLog()
    client = TestClient(create_app(audit_log=audit_log))
    headers = {**EXTERNAL_DATA_HEADERS, "Idempotency-Key": "flow-001-idem"}

    first = client.post("/external-data/ingestion-runs", json=_run_payload(), headers=headers)
    second = client.post("/external-data/ingestion-runs", json=_run_payload(), headers=headers)

    assert first.json()["created"] is True
    assert second.json()["created"] is False
    # Retry is rejected as a replay of the same run, not a new run.
    assert first.json()["run_id"] == second.json()["run_id"]

    runs = client.get("/external-data/ingestion-runs", headers=EXTERNAL_DATA_HEADERS)
    assert runs.json()["count"] == 1

    outcomes = [
        event.outcome
        for event in audit_log.list_events()
        if event.event_type == "external_data.ingested.v1"
    ]
    assert "accepted" in outcomes
    assert "idempotent_replay" in outcomes


# -- DQ quarantine + lineage queryable ---------------------------------------


def test_quarantine_and_lineage_are_queryable() -> None:
    service = ExternalIngestionService(
        store=InMemoryIngestionRunStore(),
        audit_log=InMemoryAuditLog(),
        provider_factories={
            "listing.partner_feed": lambda: ListingPartnerFeedProvider(
                mode="fixture", replay_fixture_path=_DUP_FIXTURE
            )
        },
    )
    client = TestClient(create_app(external_ingestion_service=service))

    run = client.post(
        "/external-data/ingestion-runs",
        json=_run_payload(),
        headers={**EXTERNAL_DATA_HEADERS, "Idempotency-Key": "flow-001-dq"},
    ).json()
    assert run["quarantined_count"] == 1
    assert run["accepted_count"] == 1

    quarantine = client.get("/external-data/quarantine", headers=EXTERNAL_DATA_HEADERS)
    assert quarantine.status_code == 200
    rows = quarantine.json()["items"]
    assert len(rows) == 1
    assert "duplicate_idempotency_key" in rows[0]["quarantine_reasons"]
    assert rows[0]["run_id"] == run["run_id"]


# -- scheduled ingestion persists via the same path --------------------------


def test_scheduled_ingestion_persists_with_scheduled_trigger() -> None:
    store = InMemoryIngestionRunStore()
    service = ExternalIngestionService(store=store, audit_log=InMemoryAuditLog())

    spec = ExternalFetchJobSpec(
        provider_id="listing.partner_feed",
        schedule_id="hourly",
        interval=timedelta(hours=1),
        freshness_sla=timedelta(hours=24),
    )
    outcome = service.run_scheduled(spec, scheduled_at=datetime(2026, 6, 28, 9, 0, tzinfo=UTC))

    assert outcome.created is True
    assert outcome.record.trigger == "scheduled"
    assert store.list_runs()[0].trigger == "scheduled"


# -- durable persistence survives a simulated process restart ----------------


def test_ingestion_run_survives_restart_and_replays(tmp_path) -> None:
    db_path = str(tmp_path / "durable.sqlite3")
    bundle = _durable_bundle(db_path)
    headers = {**EXTERNAL_DATA_HEADERS, "Idempotency-Key": "flow-001-durable"}
    try:
        client = TestClient(create_app(persistence=bundle))
        created = client.post(
            "/external-data/ingestion-runs", json=_run_payload(), headers=headers
        ).json()
        assert created["created"] is True
        run_id = created["run_id"]
    finally:
        bundle.engine.close()

    # Simulated restart: fresh app + bundle on the same on-disk database.
    reopened = _durable_bundle(db_path)
    try:
        client2 = TestClient(create_app(persistence=reopened))

        # Run written before restart is still retrievable.
        detail = client2.get(
            f"/external-data/ingestion-runs/{run_id}", headers=EXTERNAL_DATA_HEADERS
        )
        assert detail.status_code == 200

        # Idempotent replay after restart returns the original run, no dup.
        replay = client2.post(
            "/external-data/ingestion-runs", json=_run_payload(), headers=headers
        ).json()
        assert replay["created"] is False
        assert replay["run_id"] == run_id

        runs = client2.get("/external-data/ingestion-runs", headers=EXTERNAL_DATA_HEADERS)
        assert runs.json()["count"] == 1
    finally:
        reopened.engine.close()


def test_cross_tenant_ingestion_store_isolation_and_factory_failure_negatives() -> None:
    store_a = InMemoryIngestionRunStore()

    def resolver(tenant_id: str) -> InMemoryIngestionRunStore:
        if tenant_id == "tenant-a":
            return store_a
        if tenant_id == "tenant-b":
            raise RuntimeError("Tenant B store failed to resolve")
        raise ValueError(f"Unknown tenant {tenant_id}")

    service = ExternalIngestionService(
        ingestion_run_store_for_tenant=resolver,
        audit_log=InMemoryAuditLog(),
    )

    # Tenant A ingests successfully into tenant A store
    outcome_a = service.ingest(
        tenant_id="tenant-a",
        api_idempotency_key="shared-api-key",
    )
    assert outcome_a.created is True
    assert outcome_a.record.tenant_id == "tenant-a"
    assert len(store_a.list_runs()) == 1

    # Tenant B attempt with factory failure propagates exception immediately (no fallback to global/unscoped store or tenant A run)
    with pytest.raises(RuntimeError, match="Tenant B store failed to resolve"):
        service.ingest(
            tenant_id="tenant-b",
            api_idempotency_key="shared-api-key",
        )

    # Global store and tenant A store remain unpolluted by tenant B's failed resolution
    assert len(service.store.list_runs()) == 0
    assert len(store_a.list_runs()) == 1

    # Same-service same-window A/B isolation: single service instance handles both tenant A and B
    store_b = InMemoryIngestionRunStore()
    store_map = {"tenant-a": store_a, "tenant-b": store_b}
    service_single = ExternalIngestionService(
        ingestion_run_store_for_tenant=lambda tid: store_map[tid],
        audit_log=InMemoryAuditLog(),
    )

    # First ingest on tenant A with explicit window & correlation
    w_start = datetime(2026, 6, 28, 8, 0, tzinfo=UTC)
    w_end = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)
    run_a = service_single.ingest(
        tenant_id="tenant-a",
        window_start=w_start,
        window_end=w_end,
        correlation_id="corr-tenant-a",
    )
    assert run_a.created is True
    assert run_a.record.tenant_id == "tenant-a"
    assert run_a.record.correlation_id == "corr-tenant-a"

    # Next ingest on tenant B on SAME service instance with SAME window & different correlation
    run_b = service_single.ingest(
        tenant_id="tenant-b",
        window_start=w_start,
        window_end=w_end,
        correlation_id="corr-tenant-b",
    )
    assert run_b.created is True
    assert run_b.record.tenant_id == "tenant-b"
    assert run_b.record.correlation_id == "corr-tenant-b"
    assert run_b.record.run_id != run_a.record.run_id
    assert len(store_b.list_runs()) == 1

    # Same-service window replay dedupes per tenant
    replay_a = service_single.ingest(
        tenant_id="tenant-a",
        window_start=w_start,
        window_end=w_end,
        correlation_id="corr-tenant-a-replay",
    )
    assert replay_a.created is False
    assert replay_a.record.run_id == run_a.record.run_id

    replay_b = service_single.ingest(
        tenant_id="tenant-b",
        window_start=w_start,
        window_end=w_end,
        correlation_id="corr-tenant-b-replay",
    )
    assert replay_b.created is False
    assert replay_b.record.run_id == run_b.record.run_id


def test_cross_tenant_api_fault_isolation() -> None:
    def failing_resolver(tenant_id: str) -> InMemoryIngestionRunStore:
        if tenant_id == "tenant-fault":
            raise RuntimeError("Tenant store factory failure")
        return InMemoryIngestionRunStore()

    failing_service = ExternalIngestionService(
        ingestion_run_store_for_tenant=failing_resolver,
        audit_log=InMemoryAuditLog(),
    )
    app = create_app(external_ingestion_service=failing_service)

    client = TestClient(app)
    headers = {
        **EXTERNAL_DATA_HEADERS,
        "x-tenant-id": "tenant-fault",
        "Idempotency-Key": "fault-key",
    }
    res = client.post(
        "/api/v1/external-data/ingestion-runs",
        json=_run_payload(),
        headers=headers,
    )
    assert res.status_code == 500, res.text
    assert res.json()["detail"] == "Failed to execute tenant ingestion run: Tenant store factory failure"


def test_resolver_call_count_and_non_stable_factory_isolation() -> None:
    call_counts: dict[str, int] = {}

    store_instance = InMemoryIngestionRunStore()

    def non_stable_factory(tenant_id: str) -> Any:
        call_counts[tenant_id] = call_counts.get(tenant_id, 0) + 1
        return store_instance

    service = ExternalIngestionService(
        ingestion_run_store_for_tenant=non_stable_factory,
        audit_log=InMemoryAuditLog(),
    )

    w_start = datetime(2026, 6, 28, 8, 0, tzinfo=UTC)
    w_end = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)
    run_1 = service.ingest(
        tenant_id="tenant-non-stable",
        api_idempotency_key="key-ns-1",
        window_start=w_start,
        window_end=w_end,
    )
    assert run_1.created is True
    assert call_counts["tenant-non-stable"] == 1

    run_replay = service.ingest(
        tenant_id="tenant-non-stable",
        api_idempotency_key="key-ns-1",
        window_start=w_start,
        window_end=w_end,
    )
    assert run_replay.created is False
    assert run_replay.record.run_id == run_1.record.run_id
    assert call_counts["tenant-non-stable"] == 2

    run_window_replay = service.ingest(
        tenant_id="tenant-non-stable",
        window_start=w_start,
        window_end=w_end,
    )
    assert run_window_replay.created is False
    assert run_window_replay.record.run_id == run_1.record.run_id
    assert call_counts["tenant-non-stable"] == 3


def test_same_api_key_and_same_window_tenant_ab_isolation() -> None:
    stores = {
        "tenant-alpha": InMemoryIngestionRunStore(),
        "tenant-beta": InMemoryIngestionRunStore(),
    }
    service = ExternalIngestionService(
        ingestion_run_store_for_tenant=lambda tid: stores[tid],
        audit_log=InMemoryAuditLog(),
    )

    w_start = datetime(2026, 6, 28, 8, 0, tzinfo=UTC)
    w_end = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)
    shared_key = "shared-tenant-api-key"

    run_alpha = service.ingest(
        tenant_id="tenant-alpha",
        api_idempotency_key=shared_key,
        window_start=w_start,
        window_end=w_end,
        correlation_id="corr-alpha",
    )
    assert run_alpha.created is True
    assert run_alpha.record.tenant_id == "tenant-alpha"

    run_beta = service.ingest(
        tenant_id="tenant-beta",
        api_idempotency_key=shared_key,
        window_start=w_start,
        window_end=w_end,
        correlation_id="corr-beta",
    )
    assert run_beta.created is True
    assert run_beta.record.tenant_id == "tenant-beta"
    assert run_beta.record.run_id != run_alpha.record.run_id

    assert len(stores["tenant-alpha"].list_runs()) == 1
    assert stores["tenant-alpha"].list_runs()[0].run_id == run_alpha.record.run_id
    assert len(stores["tenant-beta"].list_runs()) == 1
    assert stores["tenant-beta"].list_runs()[0].run_id == run_beta.record.run_id


def test_per_request_resolver_failure_and_rotation() -> None:
    calls = []
    store = InMemoryIngestionRunStore()

    def rotating_factory(tid: str) -> Any:
        calls.append(tid)
        if len(calls) == 1:
            return store
        raise RuntimeError(f"Tenant store unavailable on call {len(calls)}")

    service = ExternalIngestionService(
        ingestion_run_store_for_tenant=rotating_factory,
        audit_log=InMemoryAuditLog(),
    )

    w_start = datetime(2026, 6, 28, 8, 0, tzinfo=UTC)
    w_end = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)

    # First request resolves tenant store successfully
    first_res = service.ingest(
        tenant_id="tenant-probe",
        window_start=w_start,
        window_end=w_end,
    )
    assert first_res.created is True
    assert len(calls) == 1

    # Second request invokes resolver again; when resolver fails, exception is NOT suppressed by caching
    with pytest.raises(RuntimeError, match="Tenant store unavailable on call 2"):
        service.ingest(
            tenant_id="tenant-probe",
            window_start=w_start,
            window_end=w_end,
        )

    assert len(calls) == 2

