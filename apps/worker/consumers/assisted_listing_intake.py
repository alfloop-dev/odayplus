from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from shared.domain.events import DomainEvent, validate_event
from shared.infrastructure.persistence.factory import PersistenceBundle

logger = logging.getLogger(__name__)

class ConsumerDeduplicationError(Exception):
    pass

class AssistedListingIntakeConsumer:
    """Event consumer for Assisted Listing Intake events.

    Implements:
    - At-least-once processing
    - Idempotency / Deduplication (tenant_id:event_id, 30 days lifetime)
    - Bounded retries (max 10 attempts)
    - DLQ for poison messages
    """

    def __init__(
        self,
        persistence: PersistenceBundle,
        max_attempts: int = 10,
    ) -> None:
        self._persistence = persistence
        self._max_attempts = max_attempts
        self._handlers: dict[str, Callable[[DomainEvent], None]] = {}

    def register_handler(self, event_type: str, handler: Callable[[DomainEvent], None]) -> None:
        self._handlers[event_type] = handler

    def consume(self, event: DomainEvent) -> None:
        # Validate incoming event envelope and payload
        errors = validate_event(event)
        if errors:
            logger.error(f"Event validation failed: {errors}")
            self._send_to_dlq(event, f"Validation failed: {errors}")
            return

        # Deduplication check: tenant_id:event_id
        dedup_key = f"{event.tenant_id}:{event.event_id}"
        if not self._register_deduplication(dedup_key):
            logger.warning(f"Duplicate event detected and ignored: {dedup_key}")
            return

        # Route to handler
        handler = self._handlers.get(event.event_type)
        if not handler:
            logger.info(f"No handler registered for event type: {event.event_type}")
            return

        attempts = 0
        while attempts < self._max_attempts:
            try:
                handler(event)
                return
            except Exception as e:
                attempts += 1
                logger.exception(f"Error handling event {event.event_id} (attempt {attempts}/{self._max_attempts}): {e}")
                if attempts >= self._max_attempts:
                    self._send_to_dlq(event, f"Max attempts reached. Last error: {e}")
                    raise

    def _register_deduplication(self, dedup_key: str) -> bool:
        """Register dedup key. Returns True if registered successfully, False if already exists."""
        if self._persistence.is_durable:
            store = self._persistence.listing_repository._store
            collection = "consumer.deduplication"
            existing = store.get(collection, dedup_key)
            if existing:
                created_at = datetime.fromisoformat(existing["created_at"])
                if datetime.now(UTC) - created_at < timedelta(days=30):
                    return False
            store.put(collection, dedup_key, {"created_at": datetime.now(UTC).isoformat()})
            return True
        else:
            if not hasattr(self, "_memory_dedup"):
                self._memory_dedup: dict[str, datetime] = {}
            if dedup_key in self._memory_dedup:
                created_at = self._memory_dedup[dedup_key]
                if datetime.now(UTC) - created_at < timedelta(days=30):
                    return False
            self._memory_dedup[dedup_key] = datetime.now(UTC)
            return True

    def _send_to_dlq(self, event: DomainEvent, reason: str) -> None:
        logger.error(f"Routing event {event.event_id} to DLQ. Reason: {reason}")
        if self._persistence.is_durable:
            store = self._persistence.listing_repository._store
            collection = "consumer.dlq"
            store.put(collection, event.event_id, {
                "event": event.to_dict(),
                "reason": reason,
                "failed_at": datetime.now(UTC).isoformat(),
            })
        else:
            if not hasattr(self, "_memory_dlq"):
                self._memory_dlq: dict[str, dict[str, Any]] = {}
            self._memory_dlq[event.event_id] = {
                "event": event,
                "reason": reason,
                "failed_at": datetime.now(UTC),
            }
