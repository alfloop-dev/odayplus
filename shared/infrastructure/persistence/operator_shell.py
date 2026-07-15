"""Durable, restart-survivable product-shell store (ODP-PGAP-SHELL-001).

Durable implementation of
:class:`modules.opsboard.application.shell.ShellRepository`, backed by
:class:`SqliteDocumentStore` (the same generic ``durable_documents`` table the
other durable repositories use). Task assignments, notification inbox state and
preferences, role/workspace administration grants, settings, and franchisee
acknowledgements and reports all survive a process restart, as does the
idempotency replay cache that keeps a retried write from double-applying.

The service depends on the typed contract rather than the document store, so
collection naming and blob layout stay an infrastructure detail.
"""

from __future__ import annotations

from typing import Any

from modules.opsboard.application.shell import (
    SHELL_COLLECTIONS,
    ShellIdempotencyRecord,
)
from shared.infrastructure.persistence.document_store import SqliteDocumentStore


class DurableShellRepository:
    """Durable mirror of ``InMemoryShellRepository``."""

    _IDEMPOTENCY = "operator.shell_idempotency"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def list_records(self, collection: str) -> list[dict[str, Any]]:
        return self._store.list_all(collection)

    def save_record(self, collection: str, doc_id: str, record: dict[str, Any]) -> None:
        self._store.put(collection, doc_id, record)

    def list_idempotency_records(self) -> list[ShellIdempotencyRecord]:
        return self._store.list_all(self._IDEMPOTENCY)

    def save_idempotency_record(self, record: ShellIdempotencyRecord) -> None:
        self._store.put(self._IDEMPOTENCY, f"{record.action}:{record.key}", record)

    def clear(self) -> None:
        for collection in (*SHELL_COLLECTIONS, self._IDEMPOTENCY):
            self._store.delete_collection(collection)


__all__ = ["DurableShellRepository"]
