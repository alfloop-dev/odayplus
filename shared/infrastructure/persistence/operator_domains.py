"""Tenant-scoped durable state for Operator Console domain modules.

The Operator domain modules predate the production persistence bundle and keep
their aggregate state as JSON-like dictionaries.  This adapter gives those
modules a restart-survivable boundary without allowing one tenant to enumerate
or overwrite another tenant's records.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any

from shared.infrastructure.persistence.document_store import SqliteDocumentStore


def _tenant_partition(tenant_id: str) -> str:
    value = tenant_id.strip()
    if not value:
        raise ValueError("tenant_id is required for Operator domain persistence")
    return sha256(value.encode("utf-8")).hexdigest()


class TenantScopedDocumentStore:
    """Restrict a document store instance to one opaque tenant partition."""

    def __init__(self, store: SqliteDocumentStore, tenant_id: str) -> None:
        self._store = store
        self._partition = _tenant_partition(tenant_id)

    @property
    def engine(self) -> Any:
        return self._store.engine

    def _collection(self, collection: str) -> str:
        return f"{collection}.tenant.{self._partition}"

    def put(
        self,
        collection: str,
        doc_id: str,
        obj: Any,
        *,
        group_key: str | None = None,
        seq: int | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self._store.put(
            self._collection(collection),
            doc_id,
            obj,
            group_key=group_key,
            seq=seq,
            correlation_id=correlation_id,
        )

    def get(self, collection: str, doc_id: str) -> Any | None:
        return self._store.get(self._collection(collection), doc_id)

    def list_all(self, collection: str) -> list[Any]:
        return self._store.list_all(self._collection(collection))

    def list_by_group(self, collection: str, group_key: str) -> list[Any]:
        return self._store.list_by_group(self._collection(collection), group_key)

    def latest_in_group(self, collection: str, group_key: str) -> Any | None:
        return self._store.latest_in_group(
            self._collection(collection),
            group_key,
        )

    def latest_per_group(self, collection: str) -> list[Any]:
        return self._store.latest_per_group(self._collection(collection))

    def count_in_group(self, collection: str, group_key: str) -> int:
        return self._store.count_in_group(
            self._collection(collection),
            group_key,
        )

    def append_version(
        self,
        collection: str,
        doc_id: str,
        obj: Any,
        *,
        group_key: str,
        correlation_id: str | None = None,
    ) -> int:
        return self._store.append_version(
            self._collection(collection),
            doc_id,
            obj,
            group_key=group_key,
            correlation_id=correlation_id,
        )

    def replace_latest_in_group(
        self,
        collection: str,
        obj: Any,
        *,
        group_key: str,
        correlation_id: str | None = None,
    ) -> int:
        return self._store.replace_latest_in_group(
            self._collection(collection),
            obj,
            group_key=group_key,
            correlation_id=correlation_id,
        )

    def delete_collection(self, collection: str) -> None:
        self._store.delete_collection(self._collection(collection))


class DurableOperatorDomainStateRepository:
    """Persist one aggregate state document per domain and tenant."""

    _COLLECTION = "operator.live_domain_state"

    def __init__(self, store: SqliteDocumentStore, domain: str) -> None:
        if not domain.strip():
            raise ValueError("domain is required")
        self._store = store
        self._domain = domain.strip()

    def load(self, tenant_id: str) -> dict[str, Any] | None:
        scoped = TenantScopedDocumentStore(self._store, tenant_id)
        state = scoped.get(self._COLLECTION, self._domain)
        return None if state is None else deepcopy(state)

    def save(self, tenant_id: str, state: dict[str, Any]) -> None:
        scoped = TenantScopedDocumentStore(self._store, tenant_id)
        scoped.put(self._COLLECTION, self._domain, deepcopy(state))


__all__ = [
    "DurableOperatorDomainStateRepository",
    "TenantScopedDocumentStore",
]
