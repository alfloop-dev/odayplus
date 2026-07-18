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

from shared.audit.worm import AuditWormSink, build_audit_worm_sink_from_env

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
    store_ops_repository: Any
    intervention_repository: Any
    intervention_label_registry: Any
    ingestion_run_store: Any
    # Expansion decision-flow stores (ODP-FLOW-002): HeatZone ranking, listing
    # dedup + candidate inbox, SiteScore decisions, and realized sites.
    heatzone_store: Any
    listing_repository: Any
    sitescore_decision_store: Any
    sitescore_realized_store: Any

    tenant_repository: Any
    brand_repository: Any
    address_location_repository: Any
    store_repository: Any
    machine_repository: Any
    transaction_repository: Any
    machine_cycle_repository: Any
    external_fetch_state_store: Any = None
    notification_repository: Any = None
    outbox_repository: Any = None
    engine: Any = None


    @property
    def is_durable(self) -> bool:
        return self.engine is not None


def _memory_bundle(worm_sink: AuditWormSink | None = None) -> PersistenceBundle:
    from models.shared_ml.artifact_store import InMemoryArtifactStore
    from modules.adlift.infrastructure import InMemoryAdLiftRepository
    from modules.avm.infrastructure import InMemoryAVMRepository
    from modules.external_data.application.ingestion_store import (
        InMemoryIngestionRunStore,
    )
    from modules.external_data.workers.scheduled_fetch import InMemoryExternalFetchStateStore
    from modules.forecastops.infrastructure import InMemoryForecastOpsRepository
    from modules.heatzone.infrastructure import HeatZoneResultStore
    from modules.intervention.infrastructure.repositories import (
        InMemoryInterventionRepository,
        InMemoryLabelRegistry,
    )
    from modules.learninghub.infrastructure import InMemoryLearningHubRepository
    from modules.listing.infrastructure.repositories import InMemoryListingRepository
    from modules.netplan.infrastructure import InMemoryNetPlanRepository
    from modules.notifications import InMemoryNotificationRepository
    from modules.opsboard.application.store_ops import InMemoryStoreOpsRepository
    from modules.priceops.infrastructure import InMemoryPriceOpsRepository
    from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository
    from shared.audit.events import InMemoryAuditLog
    from shared.audit.persistence import InMemoryEvidenceBundleStore
    from shared.infrastructure.persistence.outbox import InMemoryOutboxRepository
    from shared.infrastructure.persistence.repositories import (
        InMemoryAddressLocationRepository,
        InMemoryBrandRepository,
        InMemoryMachineCycleRepository,
        InMemoryMachineRepository,
        InMemoryStoreRepository,
        InMemoryTenantRepository,
        InMemoryTransactionRepository,
    )
    from shared.jobs.queue import InMemoryJobQueue
    from shared.workflow.sitescore import InMemoryDecisionStore, InMemoryRealizedSiteStore

    return PersistenceBundle(
        mode="memory",
        audit_log=InMemoryAuditLog(worm_sink=worm_sink),
        evidence_store=InMemoryEvidenceBundleStore(worm_sink=worm_sink),
        job_queue=InMemoryJobQueue(),
        avm_repository=InMemoryAVMRepository(),
        forecastops_repository=InMemoryForecastOpsRepository(),
        netplan_repository=InMemoryNetPlanRepository(),
        learninghub_repository=InMemoryLearningHubRepository(),
        artifact_store=InMemoryArtifactStore(),
        priceops_repository=InMemoryPriceOpsRepository(),
        sitescore_repository=InMemorySiteScoreRepository(),
        adlift_repository=InMemoryAdLiftRepository(),
        store_ops_repository=InMemoryStoreOpsRepository(),
        intervention_repository=InMemoryInterventionRepository(),
        intervention_label_registry=InMemoryLabelRegistry(),
        ingestion_run_store=InMemoryIngestionRunStore(),
        heatzone_store=HeatZoneResultStore(),
        listing_repository=InMemoryListingRepository(),
        sitescore_decision_store=InMemoryDecisionStore(),
        sitescore_realized_store=InMemoryRealizedSiteStore(),

        tenant_repository=InMemoryTenantRepository(),
        brand_repository=InMemoryBrandRepository(),
        address_location_repository=InMemoryAddressLocationRepository(),
        store_repository=InMemoryStoreRepository(),
        machine_repository=InMemoryMachineRepository(),
        transaction_repository=InMemoryTransactionRepository(),
        machine_cycle_repository=InMemoryMachineCycleRepository(),
        external_fetch_state_store=InMemoryExternalFetchStateStore(),
        notification_repository=InMemoryNotificationRepository(),
        outbox_repository=InMemoryOutboxRepository(),
    )


def _durable_bundle(
    db_path: str | Path, *, worm_sink: AuditWormSink | None = None
) -> PersistenceBundle:
    from modules.external_data.workers.scheduled_fetch import DurableExternalFetchStateStore
    from modules.notifications import DurableNotificationRepository
    from modules.opsboard.application.store_ops import DurableStoreOpsRepository
    from modules.opsboard.audit.evidence_store import DurableEvidenceBundleStore
    from shared.infrastructure.persistence.audit_log import DurableAuditLog
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.external_data import DurableIngestionRunStore
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.outbox import DurableOutboxRepository
    from shared.infrastructure.persistence.repositories import (
        DurableAddressLocationRepository,
        DurableAdLiftRepository,
        DurableArtifactStore,
        DurableAVMRepository,
        DurableBrandRepository,
        DurableDecisionStore,
        DurableForecastOpsRepository,
        DurableHeatZoneResultStore,
        DurableInterventionRepository,
        DurableLabelRegistry,
        DurableLearningHubRepository,
        DurableListingRepository,
        DurableMachineCycleRepository,
        DurableMachineRepository,
        DurableNetPlanRepository,
        DurablePriceOpsRepository,
        DurableRealizedSiteStore,
        DurableSiteScoreRepository,
        DurableStoreRepository,
        DurableTenantRepository,
        DurableTransactionRepository,
    )

    engine = SqliteEngine(db_path)
    store = SqliteDocumentStore(engine)
    worm_root = Path(db_path).parent / f"{Path(db_path).stem}-audit-worm"
    resolved_worm_sink = worm_sink or build_audit_worm_sink_from_env(
        default_root=worm_root
    )
    return PersistenceBundle(
        mode="durable",
        audit_log=DurableAuditLog(engine, worm_sink=resolved_worm_sink),
        evidence_store=DurableEvidenceBundleStore(engine, worm_sink=resolved_worm_sink),
        job_queue=DurableJobQueue(engine),
        avm_repository=DurableAVMRepository(store),
        forecastops_repository=DurableForecastOpsRepository(store),
        netplan_repository=DurableNetPlanRepository(store),
        learninghub_repository=DurableLearningHubRepository(store),
        artifact_store=DurableArtifactStore(store),
        priceops_repository=DurablePriceOpsRepository(store),
        sitescore_repository=DurableSiteScoreRepository(store),
        adlift_repository=DurableAdLiftRepository(store),
        store_ops_repository=DurableStoreOpsRepository(store),
        intervention_repository=DurableInterventionRepository(store),
        intervention_label_registry=DurableLabelRegistry(store),
        ingestion_run_store=DurableIngestionRunStore(store),
        heatzone_store=DurableHeatZoneResultStore(store),
        listing_repository=DurableListingRepository(store),
        sitescore_decision_store=DurableDecisionStore(store),
        sitescore_realized_store=DurableRealizedSiteStore(store),
        tenant_repository=DurableTenantRepository(engine),
        brand_repository=DurableBrandRepository(engine),
        address_location_repository=DurableAddressLocationRepository(engine),
        store_repository=DurableStoreRepository(engine),
        machine_repository=DurableMachineRepository(engine),
        transaction_repository=DurableTransactionRepository(engine),
        machine_cycle_repository=DurableMachineCycleRepository(engine),
        external_fetch_state_store=DurableExternalFetchStateStore(store),
        notification_repository=DurableNotificationRepository(engine),
        outbox_repository=DurableOutboxRepository(engine),
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
    worm_sink = build_audit_worm_sink_from_env()
    if resolved_mode in _DURABLE_MODES:
        resolved_path = db_path or os.environ.get("ODP_DB_PATH", DEFAULT_DB_PATH)
        return _durable_bundle(resolved_path, worm_sink=worm_sink)
    return _memory_bundle(worm_sink=worm_sink)


__all__ = ["DEFAULT_DB_PATH", "PersistenceBundle", "build_persistence"]
