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
    _ASSIGNMENTS = "operator.intake_assignments"
    _SLAS = "operator.intake_slas"
    _SAVED_VIEWS = "operator.intake_saved_views"
    _API_REPLAYS = "operator.intake_api_replays"

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

    def get_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        return self._store.get(self._ASSIGNMENTS, assignment_id)

    def save_assignment(self, assignment: dict[str, Any]) -> None:
        self._store.put(
            self._ASSIGNMENTS,
            assignment["assignment_id"],
            assignment,
        )

    def list_assignments(self) -> list[dict[str, Any]]:
        return self._store.list_all(self._ASSIGNMENTS)

    def get_sla(self, sla_instance_id: str) -> dict[str, Any] | None:
        return self._store.get(self._SLAS, sla_instance_id)

    def save_sla(self, sla: dict[str, Any]) -> None:
        self._store.put(self._SLAS, sla["sla_instance_id"], sla)

    def list_slas(self) -> list[dict[str, Any]]:
        return self._store.list_all(self._SLAS)

    def save_saved_view(self, saved_view: dict[str, Any]) -> None:
        self._store.put(
            self._SAVED_VIEWS,
            saved_view["saved_view_id"],
            saved_view,
        )

    def list_saved_views(self) -> list[dict[str, Any]]:
        return self._store.list_all(self._SAVED_VIEWS)

    def get_api_replay(self, replay_key: str) -> dict[str, Any] | None:
        return self._store.get(self._API_REPLAYS, replay_key)

    def save_api_replay(self, replay_key: str, replay: dict[str, Any]) -> None:
        self._store.put(self._API_REPLAYS, replay_key, replay)

    def clear(self) -> None:
        for collection in (
            self._INTAKES,
            self._IDEMPOTENCY,
            self._LISTING_META,
            self._CANDIDATE_META,
            self._PROMOTIONS,
            self._ASSIGNMENTS,
            self._SLAS,
            self._SAVED_VIEWS,
            self._API_REPLAYS,
        ):
            self._store.delete_collection(collection)
