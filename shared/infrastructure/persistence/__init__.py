"""Durable persistence and migration helpers for ODay Plus."""

from __future__ import annotations

from modules.opsboard.application.store_ops import DurableStoreOpsRepository
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.factory import (
    DEFAULT_DB_PATH,
    PersistenceBundle,
    build_persistence,
)
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.migrations import (
    MigrationAsset,
    MigrationStep,
    build_migration_manifest_checksum,
    discover_migration_steps,
)
from shared.infrastructure.persistence.model_ready import (
    DEFAULT_SCHEMA_VERSION,
    MODEL_READY_SNAPSHOT_TYPE,
    DatasetSnapshotMaterializer,
    DocumentStoreLineageRecorder,
    InMemoryLineageRecorder,
    LineageConflictError,
    LineageManifest,
    LineageRecorder,
    MaterializationError,
    MaterializedSnapshot,
    MissingLiveInputError,
    SnapshotSink,
    build_lineage_manifest,
)
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

__all__ = [
    "DEFAULT_DB_PATH",
    "DEFAULT_SCHEMA_VERSION",
    "MODEL_READY_SNAPSHOT_TYPE",
    "DatasetSnapshotMaterializer",
    "DocumentStoreLineageRecorder",
    "DurableAVMRepository",
    "DurableAdLiftRepository",
    "DurableArtifactStore",
    "DurableAuditLog",
    "DurableForecastOpsRepository",
    "DurableInterventionRepository",
    "DurableJobQueue",
    "DurableLabelRegistry",
    "DurableLearningHubRepository",
    "DurableNetPlanRepository",
    "DurablePriceOpsRepository",
    "DurableSiteScoreRepository",
    "DurableStoreOpsRepository",
    "InMemoryLineageRecorder",
    "LineageConflictError",
    "LineageManifest",
    "LineageRecorder",
    "MaterializationError",
    "MaterializedSnapshot",
    "MigrationAsset",
    "MigrationStep",
    "MissingLiveInputError",
    "PersistenceBundle",
    "SnapshotSink",
    "SqliteDocumentStore",
    "SqliteEngine",
    "build_lineage_manifest",
    "build_migration_manifest_checksum",
    "build_persistence",
    "discover_migration_steps",
]
