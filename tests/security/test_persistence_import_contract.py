"""Fresh-interpreter controls for the persistence public import boundary.

Separate owning selection: do not add cases to the original persistence unit
or six-member PostgreSQL module. No application modules are replaced by doubles.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "shared.infrastructure.persistence"
# Frozen original package export order, independent of the implementation table.
EXPORT_ORDER = """
DEFAULT_DB_PATH DEFAULT_SCHEMA_VERSION MODEL_READY_SNAPSHOT_TYPE
DatasetSnapshotMaterializer DocumentStoreLineageRecorder DurableAVMRepository
DurableAdLiftRepository DurableArtifactStore DurableAuditLog DurableCommentRepository
DurableDecisionStore DurableDecisionPolicyRepository DurableForecastOpsRepository
DurableHeatZoneResultStore DurableInterventionRepository DurableJobQueue
DurableLabelRegistry DurableLearningHubRepository DurableListingRepository
DurableNetPlanRepository DurablePriceOpsRepository DurableRealizedSiteStore
DurableSiteScoreRepository DurableStoreOpsRepository InMemoryLineageRecorder
LineageConflictError LineageManifest LineageRecorder MaterializationError MaterializedSnapshot
InMemoryTenantRepository DurableTenantRepository InMemoryBrandRepository DurableBrandRepository
InMemoryAddressLocationRepository DurableAddressLocationRepository InMemoryManualCorrectionRepository
DurableManualCorrectionRepository InvalidCorrectionError StaleRevisionError InMemoryStoreRepository
DurableStoreRepository InMemoryMachineRepository DurableMachineRepository
InMemoryTransactionRepository DurableTransactionRepository InMemoryMachineCycleRepository
DurableMachineCycleRepository DurableOutboxRepository InMemoryOutboxRepository MigrationAsset
MigrationStep MissingLiveInputError PersistenceBundle PostgresDocumentStore PostgresEngine
PostgreSQLConfigurationError PostgreSQLSchemaError SnapshotSink SqliteDocumentStore
SqlDecisionPolicyRepository SqliteEngine build_lineage_manifest build_migration_manifest_checksum
build_persistence discover_migration_steps
""".split()
# Original owners/aliases, not inferred from the candidate's private table.
OWNERS = {
    "modules.opsboard.application.store_ops": "DurableStoreOpsRepository",
    f"{PACKAGE}.audit_log": "DurableAuditLog",
    f"{PACKAGE}.decision_policy": "DurableDecisionPolicyRepository SqlDecisionPolicyRepository",
    f"{PACKAGE}.document_store": "SqliteDocumentStore",
    f"{PACKAGE}.engine": "SqliteEngine",
    f"{PACKAGE}.factory": "DEFAULT_DB_PATH PersistenceBundle build_persistence",
    f"{PACKAGE}.job_queue": "DurableJobQueue",
    f"{PACKAGE}.migrations": (
        "MigrationAsset MigrationStep build_migration_manifest_checksum discover_migration_steps"
    ),
    f"{PACKAGE}.model_ready": (
        "DEFAULT_SCHEMA_VERSION MODEL_READY_SNAPSHOT_TYPE DatasetSnapshotMaterializer "
        "DocumentStoreLineageRecorder InMemoryLineageRecorder LineageConflictError LineageManifest "
        "LineageRecorder MaterializationError MaterializedSnapshot MissingLiveInputError "
        "SnapshotSink build_lineage_manifest"
    ),
    f"{PACKAGE}.operator_comments": "DurableCommentRepository",
    f"{PACKAGE}.outbox": "DurableOutboxRepository InMemoryOutboxRepository",
    f"{PACKAGE}.postgresql": (
        "PostgresDocumentStore PostgresEngine PostgreSQLConfigurationError PostgreSQLSchemaError"
    ),
    f"{PACKAGE}.repositories": (
        "DurableAddressLocationRepository DurableAdLiftRepository DurableArtifactStore "
        "DurableAVMRepository DurableBrandRepository DurableDecisionStore DurableForecastOpsRepository "
        "DurableHeatZoneResultStore DurableInterventionRepository DurableLabelRegistry "
        "DurableLearningHubRepository DurableListingRepository DurableMachineCycleRepository "
        "DurableMachineRepository DurableManualCorrectionRepository DurableNetPlanRepository "
        "DurablePriceOpsRepository DurableRealizedSiteStore DurableSiteScoreRepository "
        "DurableStoreRepository DurableTenantRepository DurableTransactionRepository "
        "InMemoryAddressLocationRepository InMemoryBrandRepository InMemoryMachineCycleRepository "
        "InMemoryMachineRepository InMemoryManualCorrectionRepository InMemoryStoreRepository "
        "InMemoryTenantRepository InMemoryTransactionRepository InvalidCorrectionError StaleRevisionError"
    ),
}


def _fresh(source: str) -> None:
    # The outer verifier supplies sanitized fixture/environment authority. -I
    # ignores host PYTHONPATH/user-site, and the only inserted path is this repo.
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        + textwrap.dedent(source)
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=ROOT,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_narrow_clients_and_original_spawn_module_do_not_initialize_ml_exports() -> None:
    _fresh(
        """
        import runpy
        import shared.infrastructure.persistence as package
        from shared.infrastructure.persistence import DurableJobQueue, PostgresEngine
        from shared.infrastructure.persistence.job_queue import DurableJobQueue as QueueOwner
        from shared.infrastructure.persistence.postgresql import PostgresEngine as EngineOwner
        assert DurableJobQueue is QueueOwner
        assert PostgresEngine is EngineOwner
        # Execute the real original test module's import/definition phase, just
        # as spawn does; no test, DB claim or fake readiness work is performed.
        runpy.run_path('tests/integration/test_postgresql_persistence.py')
        for name in ('modules.learninghub', 'scipy', 'mlflow',
                     'shared.infrastructure.persistence.model_ready',
                     'shared.infrastructure.persistence.repositories'):
            assert name not in sys.modules, name
        assert 'DatasetSnapshotMaterializer' in dir(package)
        for unknown in ('not_an_export', '__path_from_caller__', '.repositories', '__secret__'):
            try:
                getattr(package, unknown)
            except AttributeError:
                pass
            else:
                raise AssertionError(unknown)
        assert 'shared.infrastructure.persistence.model_ready' not in sys.modules
        """
    )


@pytest.mark.parametrize("owners_first", [False, True])
def test_every_original_export_from_star_alias_identity_and_order(owners_first: bool) -> None:
    _fresh(
        f"""
        import importlib
        import inspect
        import json
        owners = json.loads({json.dumps(OWNERS)!r})
        order = {EXPORT_ORDER!r}
        if {owners_first!r}:
            for module in reversed(owners):
                importlib.import_module(module)
        import shared.infrastructure.persistence as package
        assert package.__all__ == order
        assert len(order) == len(set(order))
        namespace = {{}}
        exec('from shared.infrastructure.persistence import *', namespace)
        assert [name for name in namespace if not name.startswith('__')] == order
        for module_name, names in owners.items():
            module = importlib.import_module(module_name)
            for name in names.split():
                owner = getattr(module, name)
                value = getattr(package, name)
                assert value is owner, name
                assert namespace[name] is owner, name
                alias = {{}}
                exec(f'from shared.infrastructure.persistence import {{name}} as alias', alias)
                assert alias['alias'] is owner
                if inspect.isfunction(owner) or inspect.isclass(owner):
                    assert inspect.signature(value) == inspect.signature(owner)
        from shared.infrastructure.persistence import (
            DurableDecisionPolicyRepository, SqlDecisionPolicyRepository,
        )
        assert DurableDecisionPolicyRepository is SqlDecisionPolicyRepository
        """
    )


@pytest.mark.parametrize(
    "first",
    [
        "modules.learninghub",
        "shared.infrastructure.persistence.model_ready",
        "shared.infrastructure.persistence.repositories",
        "shared.infrastructure.persistence.factory",
    ],
)
def test_real_import_order_and_cycles(first: str) -> None:
    _fresh(
        f"""
        import importlib
        importlib.import_module({first!r})
        import shared.infrastructure.persistence as package
        from shared.infrastructure.persistence.model_ready import DatasetSnapshotMaterializer
        from shared.infrastructure.persistence.repositories import DurableLearningHubRepository
        from modules.learninghub.infrastructure.mlflow_adapter import MlflowRegistryAdapter
        assert package.DatasetSnapshotMaterializer is DatasetSnapshotMaterializer
        assert package.DurableLearningHubRepository is DurableLearningHubRepository
        assert callable(MlflowRegistryAdapter)
        """
    )


@pytest.mark.parametrize("owner", ["model_ready", "repositories"])
def test_required_export_initialization_errors_are_not_swallowed_or_cached(owner: str) -> None:
    name = "DatasetSnapshotMaterializer" if owner == "model_ready" else "DurableLearningHubRepository"
    _fresh(
        f"""
        import importlib
        import importlib.abc
        class UnavailableOwner(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'shared.infrastructure.persistence.{owner}':
                    raise RuntimeError('owning initialization unavailable')
        blocker = UnavailableOwner()
        sys.meta_path.insert(0, blocker)
        import shared.infrastructure.persistence as package
        from shared.infrastructure.persistence import DurableJobQueue, PostgresEngine
        # Deny initialization without replacing any module or returning stubs.
        for statement in (
            'package.{name}',
            'from shared.infrastructure.persistence import {name}',
            'from shared.infrastructure.persistence import *',
        ):
            try:
                exec(statement)
            except RuntimeError as error:
                assert str(error) == 'owning initialization unavailable'
            else:
                raise AssertionError(statement)
        assert '{name}' not in vars(package)
        sys.meta_path.remove(blocker)
        owner_module = importlib.import_module('shared.infrastructure.persistence.{owner}')
        assert package.{name} is getattr(owner_module, '{name}')
        """
    )


def test_backend_availability_and_configuration_still_fail_at_construction() -> None:
    _fresh(
        """
        import importlib.abc
        class MissingBackend(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'psycopg_pool':
                    raise ModuleNotFoundError('backend deliberately unavailable', name=fullname)
        sys.meta_path.insert(0, MissingBackend())
        from shared.infrastructure.persistence import PostgresEngine, PostgreSQLConfigurationError
        for url, expected in (
            ('', 'required'),
            ('sqlite:///not-production', 'postgres://'),
            ('postgresql://localhost/database', 'requires psycopg'),
        ):
            try:
                PostgresEngine(url)
            except PostgreSQLConfigurationError as error:
                assert expected in str(error), str(error)
            else:
                raise AssertionError('backend/configuration error was suppressed')
        """
    )
