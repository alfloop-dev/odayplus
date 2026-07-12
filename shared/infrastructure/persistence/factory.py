"""Persistence backend selection for the product API (ODP-PV-009).

``build_persistence()`` is the single construction point for the repositories,
audit log, and job queue that ``apps/api`` wires into ``create_app``. The
backend is chosen by environment so the *same* default code path can run either
in-memory (unit tests, fast local boot) or against durable SQLite storage
(Product-Grade E2E, where writes must survive a process restart):

    ODP_PERSISTENCE = memory   (default) -> in-memory implementations
    ODP_PERSISTENCE = durable | sqlite   -> SQLite-backed durable implementations
    ODP_DB_PATH     = <path>             -> durable database file location

In ``memory`` mode the bundle holds exactly the implementations the API used
before this task, so default behaviour is unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = ".odp_data/durable.sqlite3"
_DURABLE_MODES = {"durable", "sqlite"}


@dataclass(frozen=True)
class PersistenceBundle:
    """The set of storage-backed collaborators injected into ``create_app``."""

    mode: str
    audit_log: Any
    evidence_store: Any
    job_queue: Any
    avm_repository: Any
    forecastops_repository: Any
    netplan_repository: Any
    learninghub_repository: Any
    artifact_store: Any
    priceops_repository: Any
    sitescore_repository: Any
    adlift_repository: Any
    intervention_repository: Any
    intervention_label_registry: Any
    ingestion_run_store: Any
    engine: Any = None

    @property
    def is_durable(self) -> bool:
        return self.engine is not None


def _memory_bundle() -> PersistenceBundle:
    from models.shared_ml.artifact_store import InMemoryArtifactStore
    from modules.adlift.infrastructure import InMemoryAdLiftRepository
    from modules.avm.infrastructure import InMemoryAVMRepository
    from modules.forecastops.infrastructure import InMemoryForecastOpsRepository
    from modules.intervention.infrastructure.repositories import (
        InMemoryInterventionRepository,
        InMemoryLabelRegistry,
    )
    from modules.external_data.application.ingestion_store import (
        InMemoryIngestionRunStore,
    )
    from modules.learninghub.infrastructure import InMemoryLearningHubRepository
    from modules.netplan.infrastructure import InMemoryNetPlanRepository
    from modules.priceops.infrastructure import InMemoryPriceOpsRepository
    from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository
    from shared.audit.events import InMemoryAuditLog
    from shared.audit.persistence import InMemoryEvidenceBundleStore
    from shared.jobs.queue import InMemoryJobQueue

    return PersistenceBundle(
        mode="memory",
        audit_log=InMemoryAuditLog(),
        evidence_store=InMemoryEvidenceBundleStore(),
        job_queue=InMemoryJobQueue(),
        avm_repository=InMemoryAVMRepository(),
        forecastops_repository=InMemoryForecastOpsRepository(),
        netplan_repository=InMemoryNetPlanRepository(),
        learninghub_repository=InMemoryLearningHubRepository(),
        artifact_store=InMemoryArtifactStore(),
        priceops_repository=InMemoryPriceOpsRepository(),
        sitescore_repository=InMemorySiteScoreRepository(),
        adlift_repository=InMemoryAdLiftRepository(),
        intervention_repository=InMemoryInterventionRepository(),
        intervention_label_registry=InMemoryLabelRegistry(),
        ingestion_run_store=InMemoryIngestionRunStore(),
    )


def _durable_bundle(db_path: str | Path) -> PersistenceBundle:
    from modules.opsboard.audit.evidence_store import DurableEvidenceBundleStore
    from shared.infrastructure.persistence.audit_log import DurableAuditLog
    from shared.infrastructure.persistence.external_data import DurableIngestionRunStore
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.repositories import (
        DurableAdLiftRepository,
        DurableArtifactStore,
        DurableAVMRepository,
        DurableForecastOpsRepository,
        DurableInterventionRepository,
        DurableLabelRegistry,
        DurableLearningHubRepository,
        DurableNetPlanRepository,
        DurablePriceOpsRepository,
        DurableSiteScoreRepository,
    )

    engine = SqliteEngine(db_path)
    store = SqliteDocumentStore(engine)
    return PersistenceBundle(
        mode="durable",
        audit_log=DurableAuditLog(engine),
        evidence_store=DurableEvidenceBundleStore(engine),
        job_queue=DurableJobQueue(engine),
        avm_repository=DurableAVMRepository(store),
        forecastops_repository=DurableForecastOpsRepository(store),
        netplan_repository=DurableNetPlanRepository(store),
        learninghub_repository=DurableLearningHubRepository(store),
        artifact_store=DurableArtifactStore(store),
        priceops_repository=DurablePriceOpsRepository(store),
        sitescore_repository=DurableSiteScoreRepository(store),
        adlift_repository=DurableAdLiftRepository(store),
        intervention_repository=DurableInterventionRepository(store),
        intervention_label_registry=DurableLabelRegistry(store),
        ingestion_run_store=DurableIngestionRunStore(store),
        engine=engine,
    )


def build_persistence(
    *,
    mode: str | None = None,
    db_path: str | Path | None = None,
) -> PersistenceBundle:
    """Build the persistence bundle for the configured backend.

    Args mirror the env knobs and override them when supplied (used by tests).
    """
    resolved_mode = (mode or os.environ.get("ODP_PERSISTENCE", "memory")).strip().lower()
    if resolved_mode in _DURABLE_MODES:
        resolved_path = db_path or os.environ.get("ODP_DB_PATH", DEFAULT_DB_PATH)
        return _durable_bundle(resolved_path)
    return _memory_bundle()


__all__ = ["DEFAULT_DB_PATH", "PersistenceBundle", "build_persistence"]
