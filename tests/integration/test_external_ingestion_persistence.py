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

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.external_data.application.ingestion_service import ExternalIngestionService
from modules.external_data.application.ingestion_store import InMemoryIngestionRunStore
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
    detail = client.get(
        f"/external-data/ingestion-runs/{run_id}", headers=EXTERNAL_DATA_HEADERS
    )
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["run_id"] == run_id
    assert len(detail_body["lineage"]) == 2

    # And it shows up in the list endpoint.
    listing = client.get("/external-data/ingestion-runs", headers=EXTERNAL_DATA_HEADERS)
    assert listing.status_code == 200
    assert listing.json()["count"] == 1


def test_freshness_reads_persisted_run_state() -> None:
    client = TestClient(create_app())

    # Cold store -> documented fixture fallback.
    cold = client.get("/external-data/freshness", headers=EXTERNAL_DATA_HEADERS)
    assert cold.json()["freshness"][0]["source_snapshot_id"] == "snap-expansion-20260628-0100"

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
    outcome = service.run_scheduled(
        spec, scheduled_at=datetime(2026, 6, 28, 9, 0, tzinfo=UTC)
    )

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
