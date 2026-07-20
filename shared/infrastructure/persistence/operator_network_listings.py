"""Durable, restart-survivable assisted listing intake store (ODP-OC-R5-011).

Durable implementation of
:class:`modules.opsboard.application.network_listings.AssistedIntakeRepository`,
backed by :class:`SqliteDocumentStore` (the same generic ``durable_documents``
table the other durable repositories use). Submitted intakes, their idempotency
replay cache, and the listing/candidate presentation metadata that the domain
repositories do not own all survive a process restart.

The service depends on the typed contract rather than the document store, so
collection naming and blob layout stay an infrastructure detail.
"""

from __future__ import annotations

from typing import Any

from modules.opsboard.application.network_listings import IntakeIdempotencyRecord
from shared.infrastructure.persistence.document_store import SqliteDocumentStore


class DurableAssistedIntakeRepository:
    """Durable mirror of ``InMemoryAssistedIntakeRepository``."""

    _INTAKES = "operator.assisted_intakes"
    _IDEMPOTENCY = "operator.idempotency_cache"
    _LISTING_META = "operator.listing_metadata"
    _CANDIDATE_META = "operator.candidate_metadata"
    _PROMOTIONS = "operator.promotions"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def list_intakes(self) -> list[dict[str, Any]]:
        return self._store.list_all(self._INTAKES)

    def save_intake(self, intake: dict[str, Any]) -> None:
        self._store.put(self._INTAKES, intake["id"], intake)

    def list_idempotency_records(self) -> list[IntakeIdempotencyRecord]:
        return self._store.list_all(self._IDEMPOTENCY)

    def save_idempotency_record(self, record: IntakeIdempotencyRecord) -> None:
        self._store.put(self._IDEMPOTENCY, f"{record.action}:{record.key}", record)

    def get_listing_metadata(self, listing_id: str) -> dict[str, Any]:
        return self._store.get(self._LISTING_META, listing_id) or {}

    def save_listing_metadata(self, listing_id: str, metadata: dict[str, Any]) -> None:
        self._store.put(self._LISTING_META, listing_id, metadata)

    def get_candidate_metadata(self, candidate_id: str) -> dict[str, Any]:
        return self._store.get(self._CANDIDATE_META, candidate_id) or {}

    def save_candidate_metadata(self, candidate_id: str, metadata: dict[str, Any]) -> None:
        self._store.put(self._CANDIDATE_META, candidate_id, metadata)

    def get_promotion(self, promo_id: str) -> dict[str, Any] | None:
        return self._store.get(self._PROMOTIONS, promo_id)

    def save_promotion(self, promo: dict[str, Any]) -> None:
        self._store.put(self._PROMOTIONS, promo["promotion_decision_id"], promo)

    def list_promotions(self) -> list[dict[str, Any]]:
        return self._store.list_all(self._PROMOTIONS)

    def clear(self) -> None:
        for collection in (
            self._INTAKES,
            self._IDEMPOTENCY,
            self._LISTING_META,
            self._CANDIDATE_META,
            self._PROMOTIONS,
        ):
            self._store.delete_collection(collection)

