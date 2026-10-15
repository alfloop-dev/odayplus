"""Tenant-isolated CAS documents for fresh score execution ownership.

The caller holds the original Intake/promotion/job locks before these request
and head locks. Documents participate in the same PostgreSQL transaction as
canonical jobs, delivery, immutable results, audit and outbox.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from modules.listing.application.intake_score_delivery import (
    ScoreDeliveryConflict,
    ScoreDeliveryUnavailable,
)
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore


class ScoreExecutionDocuments:
    REQUESTS = "sitescore.execution_requests"
    KEYS = "sitescore.execution_request_keys"
    HEADS = "sitescore.execution_heads"

    def __init__(self, canonical: Any, tenant_id: str):
        self.canonical = canonical
        self.store = TenantScopedDocumentStore(canonical._store, tenant_id)
        if self.store.engine is not canonical._engine or self.store.engine.dialect != "postgresql":
            raise ScoreDeliveryUnavailable("SITESCORE_EXECUTION_TRANSACTION_UNAVAILABLE")

    def locked(self, collection: str, key: str) -> dict | None:
        return self.canonical._read_locked(self.store._collection(collection), key)

    def insert(self, collection: str, key: str, value: dict) -> None:
        if not self.store.put_if_absent(collection, key, deepcopy(value)):
            raise ScoreDeliveryConflict("SITESCORE_EXECUTION_INSERT_CONFLICT")

    def cas(self, collection: str, key: str, expected_version: int, value: dict) -> dict:
        current = self.locked(collection, key)
        if not current or current.get("version") != expected_version:
            raise ScoreDeliveryConflict("SITESCORE_EXECUTION_VERSION_CONFLICT")
        saved = deepcopy(value)
        saved["version"] = expected_version + 1
        self.store.put(collection, key, saved)
        return saved
