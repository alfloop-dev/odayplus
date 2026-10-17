"""Offline production composition doubles with the declared SQL participants.

SQLite supplies the existing read corpus. The wrapper advertises the production
composition protocol, and the real intake/queue/outbox adapters share it. This
is dependency-injection evidence, not PostgreSQL, inference or rollout proof.
Actual score delivery atomicity is covered by the PostgreSQL runtime suites.
"""

from dataclasses import replace
from typing import Any

from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.outbox import DurableOutboxRepository


class OfflinePostgresCompositionEngine:
    is_production = True
    dialect = "postgresql"

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def bind_offline_score_participants(bundle: Any, *, engine: Any = None) -> Any:
    """Supply actual durable adapter types without claiming a live PG server."""
    engine = engine if engine is not None else OfflinePostgresCompositionEngine(bundle.engine)
    if getattr(engine, "dialect", None) != "postgresql":
        raise ValueError("Offline production composition needs its declared PostgreSQL protocol")
    documents = SqliteDocumentStore(engine)
    return replace(
        bundle, mode="postgresql", engine=engine,
        audit_log=DurableAuditLog(engine, worm_sink=getattr(bundle.audit_log, "_worm_sink", None)),
        assisted_intake_store=DurableAssistedIntakeStore(documents),
        job_queue=DurableJobQueue(engine),
        outbox_repository=DurableOutboxRepository(engine),
    )
