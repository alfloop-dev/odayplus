"""Durable, restart-survivable external-data ingestion run store (ODP-FLOW-001).

Drop-in replacement for
:class:`modules.external_data.application.ingestion_store.InMemoryIngestionRunStore`.
Records are persisted through :class:`SqliteDocumentStore` (the same generic
``durable_documents`` table the other durable repositories use), so persisted
ingestion runs — canonical output summary, DQ quarantine, lineage, and
freshness — survive a process restart and stay queryable via the API/UI.

``group_key`` is the provider id, so ``latest_per_provider`` (which powers the
freshness endpoint) resolves to the newest run per provider with a single
grouped query.
"""

from __future__ import annotations

from typing import Any

from modules.external_data.application.ingestion_store import IngestionRunRecord
from modules.external_data.workers.scheduled_fetch import SourceFreshnessEvidence
from shared.infrastructure.persistence.document_store import SqliteDocumentStore


class DurableIngestionRunStore:
    """Durable mirror of ``InMemoryIngestionRunStore``."""

    _C = "external_data.ingestion_runs"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save(self, record: IngestionRunRecord) -> IngestionRunRecord:
        # Runs are write-once (a fresh run_id per accepted ingestion); an
        # existing run_id upserts in place and keeps its version/ordinal.
        if self._store.get(self._C, record.run_id) is None:
            self._store.append_version(
                self._C,
                record.run_id,
                record,
                group_key=record.provider_id,
                correlation_id=record.correlation_id,
            )
        else:
            self._store.put(
                self._C,
                record.run_id,
                record,
                group_key=record.provider_id,
                correlation_id=record.correlation_id,
            )
        return record

    def link_api_key(self, api_idempotency_key: str, run_id: str) -> None:
        # The api key is carried on the record itself and resolved by scan, so
        # linking after the fact means re-persisting the record with the key.
        record = self._store.get(self._C, run_id)
        if record is None or not api_idempotency_key:
            return
        from dataclasses import replace

        self.save(replace(record, api_idempotency_key=api_idempotency_key))

    def get(self, run_id: str) -> IngestionRunRecord | None:
        return self._store.get(self._C, run_id)

    def get_by_window_key(self, idempotency_key: str) -> IngestionRunRecord | None:
        for record in reversed(self._store.list_all(self._C)):
            if record.idempotency_key == idempotency_key:
                return record
        return None

    def get_by_api_key(self, api_idempotency_key: str) -> IngestionRunRecord | None:
        for record in reversed(self._store.list_all(self._C)):
            if record.api_idempotency_key == api_idempotency_key:
                return record
        return None

    def list_runs(self, *, provider_id: str | None = None) -> list[IngestionRunRecord]:
        if provider_id is not None:
            return self._store.list_by_group(self._C, provider_id)
        return self._store.list_all(self._C)

    def latest_per_provider(self) -> list[IngestionRunRecord]:
        return self._store.latest_per_group(self._C)

    def freshness(self) -> list[SourceFreshnessEvidence]:
        return [record.freshness for record in self.latest_per_provider()]

    def quarantine_records(
        self, *, provider_id: str | None = None
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for record in self.list_runs(provider_id=provider_id):
            for item in record.quarantine:
                payload = item.to_dict()
                payload["run_id"] = record.run_id
                payload["provider_id"] = record.provider_id
                rows.append(payload)
        return rows


__all__ = ["DurableIngestionRunStore"]
