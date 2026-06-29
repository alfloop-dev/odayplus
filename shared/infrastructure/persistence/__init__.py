"""Durable persistence layer for ODay Plus (ODP-PV-009).

Takes the product API off in-memory repositories and onto restart-survivable
SQLite storage for Product-Grade E2E validation, while keeping the exact
repository interfaces the domain/application layers already depend on.

Entry point: :func:`build_persistence` (env-driven backend selection).
"""

from __future__ import annotations

from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.factory import (
    DEFAULT_DB_PATH,
    PersistenceBundle,
    build_persistence,
)
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.repositories import (
    DurableAdLiftRepository,
    DurableArtifactStore,
    DurableAVMRepository,
    DurableForecastOpsRepository,
    DurableInterventionRepository,
    DurableLabelRegistry,
    DurableLearningHubRepository,
    DurablePriceOpsRepository,
    DurableSiteScoreRepository,
)

__all__ = [
    "DEFAULT_DB_PATH",
    "DurableAVMRepository",
    "DurableAdLiftRepository",
    "DurableArtifactStore",
    "DurableAuditLog",
    "DurableForecastOpsRepository",
    "DurableInterventionRepository",
    "DurableJobQueue",
    "DurableLabelRegistry",
    "DurableLearningHubRepository",
    "DurablePriceOpsRepository",
    "DurableSiteScoreRepository",
    "PersistenceBundle",
    "SqliteDocumentStore",
    "SqliteEngine",
    "build_persistence",
]
