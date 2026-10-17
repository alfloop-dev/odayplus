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

from contextlib import contextmanager
from copy import deepcopy
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

    def get_intake(self, intake_id: str) -> dict[str, Any] | None:
        return self._store.get(self._INTAKES, intake_id)

    def save_intake(self, intake: dict[str, Any]) -> None:
        self._store.put(self._INTAKES, intake["id"], intake)

    def create_intake_if_absent(self, intake: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        inserted = self._store.put_if_absent(self._INTAKES, intake["id"], intake)
        if inserted:
            return intake, True
        existing = self._store.get(self._INTAKES, intake["id"])
        return (existing if existing is not None else intake), False

    def list_idempotency_records(self) -> list[IntakeIdempotencyRecord]:
        return self._store.list_all(self._IDEMPOTENCY)

    def save_idempotency_record(self, record: IntakeIdempotencyRecord) -> None:
        self._store.put(self._IDEMPOTENCY, f"{record.action}:{record.key}", record)

    def get_listing_metadata(self, listing_id: str) -> dict[str, Any]:
        return self._store.get(self._LISTING_META, listing_id) or {}

    def save_listing_metadata(self, listing_id: str, metadata: dict[str, Any]) -> None:
        engine = self._store.engine
        with engine.lock:
            if getattr(engine, "dialect", None) == "postgresql":
                collection = (self._store._collection(self._LISTING_META)
                              if hasattr(self._store, "_collection") else self._LISTING_META)
                # Match the score reader's key even when no row exists yet;
                # FOR UPDATE alone cannot serialize a late first insertion.
                engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?, 0)) AS locked",
                                 (f"{collection}:{listing_id}",))
            self._store.put(self._LISTING_META, listing_id, metadata)

    def get_candidate_metadata(self, candidate_id: str) -> dict[str, Any]:
        return self._store.get(self._CANDIDATE_META, candidate_id) or {}

    @contextmanager
    def candidate_metadata_guard(self, candidate_id: str):
        engine = self._store.engine
        with engine.lock, engine.transaction():
            if getattr(engine, "dialect", None) == "postgresql":
                collection = (self._store._collection(self._CANDIDATE_META)
                              if hasattr(self._store, "_collection") else self._CANDIDATE_META)
                engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?, 0)) AS locked",
                                 (f"{collection}:{candidate_id}",))
            elif not engine._conn.in_transaction:
                engine.execute("BEGIN IMMEDIATE")
            yield

    def save_candidate_metadata(self, candidate_id: str, metadata: dict[str, Any], *,
                                expected_review_owned: dict[str, Any] | None = None) -> None:
        with self.candidate_metadata_guard(candidate_id):
            current = self.get_candidate_metadata(candidate_id)
            owned = {key: current[key] for key in ("reviewWorkflow", "reviewSubmission") if key in current}
            merged = deepcopy(metadata)
            if expected_review_owned is not None:
                if owned != expected_review_owned:
                    raise ValueError("CANDIDATE_REVIEW_METADATA_CONFLICT")
            else:
                merged.update(deepcopy(owned))
            self._store.put(self._CANDIDATE_META, candidate_id, merged)

    def save_review_metadata(self, candidate_id: str, metadata: dict[str, Any], *,
                             expected_owned: dict[str, Any]) -> None:
        # One physical write hook retains fault/rollback observability for both
        # ordinary metadata writers and the typed review owner CAS.
        self.save_candidate_metadata(candidate_id, metadata, expected_review_owned=expected_owned)

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
