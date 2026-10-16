"""Durable persistence and migration helpers for ODay Plus."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from modules.opsboard.application.store_ops import DurableStoreOpsRepository
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.decision_policy import (
    DurableDecisionPolicyRepository,
    SqlDecisionPolicyRepository,
)
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
from shared.infrastructure.persistence.operator_comments import DurableCommentRepository
from shared.infrastructure.persistence.outbox import (
    DurableOutboxRepository,
    InMemoryOutboxRepository,
)
from shared.infrastructure.persistence.postgresql import (
    PostgresDocumentStore,
    PostgresEngine,
    PostgreSQLConfigurationError,
    PostgreSQLSchemaError,
)

if TYPE_CHECKING:
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
        DurableManualCorrectionRepository,
        DurableNetPlanRepository,
        DurablePriceOpsRepository,
        DurableRealizedSiteStore,
        DurableSiteScoreRepository,
        DurableStoreRepository,
        DurableTenantRepository,
        DurableTransactionRepository,
        InMemoryAddressLocationRepository,
        InMemoryBrandRepository,
        InMemoryMachineCycleRepository,
        InMemoryMachineRepository,
        InMemoryManualCorrectionRepository,
        InMemoryStoreRepository,
        InMemoryTenantRepository,
        InMemoryTransactionRepository,
        InvalidCorrectionError,
        StaleRevisionError,
    )

# These two re-export groups transitively import the product ML/application
# stack. Narrow engine/queue clients do not consume it (including fresh spawn
# workers). Their owning modules perform no persistence registration/preflight
# on this package's behalf. Keep all other exports eager, and load these exact
# owners only when consumed; their import/initialization errors propagate.
_DEFERRED_EXPORTS = {
    **dict.fromkeys(
        (
            "DEFAULT_SCHEMA_VERSION",
            "MODEL_READY_SNAPSHOT_TYPE",
            "DatasetSnapshotMaterializer",
            "DocumentStoreLineageRecorder",
            "InMemoryLineageRecorder",
            "LineageConflictError",
            "LineageManifest",
            "LineageRecorder",
            "MaterializationError",
            "MaterializedSnapshot",
            "MissingLiveInputError",
            "SnapshotSink",
            "build_lineage_manifest",
        ),
        ".model_ready",
    ),
    **dict.fromkeys(
        (
            "DurableAddressLocationRepository",
            "DurableAdLiftRepository",
            "DurableArtifactStore",
            "DurableAVMRepository",
            "DurableBrandRepository",
            "DurableDecisionStore",
            "DurableForecastOpsRepository",
            "DurableHeatZoneResultStore",
            "DurableInterventionRepository",
            "DurableLabelRegistry",
            "DurableLearningHubRepository",
            "DurableListingRepository",
            "DurableMachineCycleRepository",
            "DurableMachineRepository",
            "DurableManualCorrectionRepository",
            "DurableNetPlanRepository",
            "DurablePriceOpsRepository",
            "DurableRealizedSiteStore",
            "DurableSiteScoreRepository",
            "DurableStoreRepository",
            "DurableTenantRepository",
            "DurableTransactionRepository",
            "InMemoryAddressLocationRepository",
            "InMemoryBrandRepository",
            "InMemoryMachineCycleRepository",
            "InMemoryMachineRepository",
            "InMemoryManualCorrectionRepository",
            "InMemoryStoreRepository",
            "InMemoryTenantRepository",
            "InMemoryTransactionRepository",
            "InvalidCorrectionError",
            "StaleRevisionError",
        ),
        ".repositories",
    ),
}


def __getattr__(name: str) -> Any:
    module_name = _DEFERRED_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # The fixed export table is the only source of import targets. Return the
    # actual owning object, not a proxy/wrapper, and rely on Python's module
    # import lock/cache rather than a second mutable object cache.
    return getattr(import_module(module_name, __name__), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

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
    "DurableCommentRepository",
    "DurableDecisionStore",
    "DurableDecisionPolicyRepository",
    "DurableForecastOpsRepository",
    "DurableHeatZoneResultStore",
    "DurableInterventionRepository",
    "DurableJobQueue",
    "DurableLabelRegistry",
    "DurableLearningHubRepository",
    "DurableListingRepository",
    "DurableNetPlanRepository",
    "DurablePriceOpsRepository",
    "DurableRealizedSiteStore",
    "DurableSiteScoreRepository",
    "DurableStoreOpsRepository",
    "InMemoryLineageRecorder",
    "LineageConflictError",
    "LineageManifest",
    "LineageRecorder",
    "MaterializationError",
    "MaterializedSnapshot",
    "InMemoryTenantRepository",
    "DurableTenantRepository",
    "InMemoryBrandRepository",
    "DurableBrandRepository",
    "InMemoryAddressLocationRepository",
    "DurableAddressLocationRepository",
    "InMemoryManualCorrectionRepository",
    "DurableManualCorrectionRepository",
    "InvalidCorrectionError",
    "StaleRevisionError",
    "InMemoryStoreRepository",
    "DurableStoreRepository",
    "InMemoryMachineRepository",
    "DurableMachineRepository",
    "InMemoryTransactionRepository",
    "DurableTransactionRepository",
    "InMemoryMachineCycleRepository",
    "DurableMachineCycleRepository",
    "DurableOutboxRepository",
    "InMemoryOutboxRepository",
    "MigrationAsset",
    "MigrationStep",
    "MissingLiveInputError",
    "PersistenceBundle",
    "PostgresDocumentStore",
    "PostgresEngine",
    "PostgreSQLConfigurationError",
    "PostgreSQLSchemaError",
    "SnapshotSink",
    "SqliteDocumentStore",
    "SqlDecisionPolicyRepository",
    "SqliteEngine",
    "build_lineage_manifest",
    "build_migration_manifest_checksum",
    "build_persistence",
    "discover_migration_steps",
]
