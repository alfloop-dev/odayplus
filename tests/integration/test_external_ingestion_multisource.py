from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from modules.external_data.application.ingestion_service import ExternalIngestionService
from modules.external_data.connectors import build_external_connectors
from modules.external_data.workers.scheduled_fetch import (
    default_external_fetch_provider_factories,
)
from shared.audit.events import InMemoryAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.external_data import DurableIngestionRunStore

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "source_data" / "external"
)


class _ContractSnapshotProvider:
    def __init__(
        self,
        *,
        provider_id: str,
        contract_id: str,
        records: list[dict[str, Any]],
        snapshot_id: str,
    ) -> None:
        self.provider_id = provider_id
        self.contract_id = contract_id
        self.records = records
        self.snapshot_id = snapshot_id
        self.calls = 0

    def fetch_and_ingest(
        self,
        *,
        ingestion_time: datetime | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        self.calls += 1
        fetched_at = ingestion_time or NOW
        connector = build_external_connectors()[self.contract_id]
        run = connector.ingest(self.records, ingestion_time=fetched_at)
        return SimpleNamespace(
            raw_snapshot=SimpleNamespace(
                snapshot_id=self.snapshot_id,
                fetched_at=fetched_at,
                observed_at=NOW,
                records=tuple(self.records),
            ),
            canonical_snapshot=SimpleNamespace(
                snapshot_id=f"canonical-{self.snapshot_id}",
                connector_run=run,
            ),
            connector_run=run,
            correlation_id=correlation_id,
        )


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _providers() -> dict[str, _ContractSnapshotProvider]:
    listing = _load("listing_raw_snapshot.valid.json")
    poi_valid = _load("poi_snapshot.valid.json")
    poi_invalid = _load("poi_snapshot.invalid.json")
    admin = _load("admin_boundary_snapshot.valid.json")
    return {
        "listing.partner_feed": _ContractSnapshotProvider(
            provider_id="listing.partner_feed",
            contract_id="listing_raw_snapshot",
            records=list(listing["records"]),
            snapshot_id="listing-multi-live",
        ),
        "poi.commercial_api": _ContractSnapshotProvider(
            provider_id="poi.commercial_api",
            contract_id="poi_snapshot",
            records=[
                poi_valid["records"][0],
                poi_invalid["cases"][0]["record"],
            ],
            snapshot_id="poi-multi-live",
        ),
        "admin_boundary.official_dataset": _ContractSnapshotProvider(
            provider_id="admin_boundary.official_dataset",
            contract_id="admin_boundary_snapshot",
            records=list(admin["records"]),
            snapshot_id="admin-multi-live",
        ),
    }


def test_default_snapshot_factories_exclude_geocode_lookup() -> None:
    factories = default_external_fetch_provider_factories(
        {
            "ODP_EXTERNAL_PROVIDER_MODE": "live",
            "ODP_PRODUCTION_PROVIDER_IDS": (
                "listing.partner_feed,poi.commercial_api,"
                "admin_boundary.official_dataset,geocode.primary_api"
            ),
        }
    )
    assert set(factories) == {
        "listing.partner_feed",
        "poi.commercial_api",
        "admin_boundary.official_dataset",
    }
    assert "geocode.primary_api" not in factories


def test_listing_poi_admin_closed_loop_persists_across_restart(tmp_path) -> None:
    database_path = tmp_path / "multi-source.sqlite3"
    engine = SqliteEngine(database_path)
    store = DurableIngestionRunStore(SqliteDocumentStore(engine))
    providers = _providers()
    service = ExternalIngestionService(
        store=store,
        audit_log=InMemoryAuditLog(),
        provider_factories={
            provider_id: (lambda provider=provider: provider)
            for provider_id, provider in providers.items()
        },
    )

    created = {}
    for provider_id in providers:
        created[provider_id] = service.ingest(
            provider_id=provider_id,
            schedule_id="hourly",
            trigger="scheduled",
            scheduled_at=NOW,
            correlation_id=f"corr-{provider_id}",
            api_idempotency_key=f"idem-{provider_id}",
        )

    assert all(outcome.created for outcome in created.values())
    assert len(store.list_runs()) == 3
    assert len(store.freshness()) == 3
    assert {item.provider_id for item in store.freshness()} == set(providers)
    poi_run = created["poi.commercial_api"].record
    assert poi_run.accepted_count == 1
    assert poi_run.quarantined_count == 1
    assert poi_run.quarantine[0].quarantine_reasons == (
        "missing_required_field",
    )
    assert poi_run.provider_observed_at == NOW
    assert all(outcome.record.canonical_snapshot_id for outcome in created.values())
    engine.close()

    reopened_engine = SqliteEngine(database_path)
    reopened_store = DurableIngestionRunStore(
        SqliteDocumentStore(reopened_engine)
    )

    def unexpected_provider_call() -> Any:
        raise AssertionError("durable idempotency replay must not call a provider")

    reopened_service = ExternalIngestionService(
        store=reopened_store,
        audit_log=InMemoryAuditLog(),
        provider_factories={
            provider_id: unexpected_provider_call for provider_id in providers
        },
    )
    for provider_id in providers:
        replay = reopened_service.ingest(
            provider_id=provider_id,
            schedule_id="hourly",
            trigger="scheduled",
            scheduled_at=NOW,
            correlation_id=f"corr-replay-{provider_id}",
            api_idempotency_key=f"idem-{provider_id}",
        )
        assert replay.created is False
        assert (
            replay.record.run_id
            == created[provider_id].record.run_id
        )

    assert len(reopened_store.list_runs()) == 3
    assert len(
        reopened_store.quarantine_records(provider_id="poi.commercial_api")
    ) == 1
    reopened_engine.close()
