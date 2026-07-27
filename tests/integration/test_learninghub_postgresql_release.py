from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

from models.shared_ml import ModelAlias, ModelStage
from modules.learninghub import (
    LearningHubService,
    MlflowRegistryAdapter,
    ReleaseType,
)
from modules.learninghub.infrastructure.repositories import (
    LearningHubReleaseConflict,
    LearningHubReleaseFenced,
    ModelReleaseSaga,
    ReleaseSagaState,
)
from shared.audit import AuditEvent
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.infrastructure.persistence import DurableAuditLog, DurableLearningHubRepository
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.postgresql import PostgresEngine
from tests.integration._learninghub_fixtures import prepare_candidate


class _PostgresProcessInterrupted(BaseException):
    """Simulates a worker process dying mid-saga."""

pytestmark = pytest.mark.skipif(
    not os.environ.get("INTAKE_TEST_DATABASE_URL"),
    reason="INTAKE_TEST_DATABASE_URL is not configured",
)


def _repository(database_url: str) -> tuple[PostgresEngine, DurableLearningHubRepository]:
    engine = PostgresEngine(
        database_url,
        bootstrap=True,
        validate_schema=False,
        min_pool_size=1,
        max_pool_size=2,
    )
    return engine, DurableLearningHubRepository(SqliteDocumentStore(engine))


def test_postgresql16_release_advisory_lock_cas_saga_restart_and_worm(
    tmp_path: Path,
) -> None:
    database_url = os.environ["INTAKE_TEST_DATABASE_URL"]
    engine_a, repository_a = _repository(database_url)
    engine_b, repository_b = _repository(database_url)
    model_name = f"learninghub-postgresql-{uuid4()}"
    barrier = Barrier(2)

    def reserve(
        repository: DurableLearningHubRepository,
        idempotency_key: str,
    ) -> str:
        barrier.wait()
        try:
            with repository.release_guard(
                model_name,
                expected_revision=0,
            ) as release_revision:
                release_id = f"release-{uuid4()}"
                repository.save_release_saga(
                    ModelReleaseSaga(
                        release_id=release_id,
                        model_name=model_name,
                        idempotency_key=idempotency_key,
                        request_fingerprint=f"sha256:{uuid4().hex}",
                        release_revision=release_revision,
                        operation="MODEL_RELEASE",
                        command={
                            "model_name": model_name,
                            "version": "1.0.0",
                            "requested_by": "postgres-integration",
                        },
                        version_snapshots=(),
                        alias_snapshots=(),
                    )
                )
                return release_id
        except LearningHubReleaseConflict:
            return "conflict"

    try:
        version = engine_a.query_one("SHOW server_version_num")
        assert version is not None
        assert 160000 <= int(version["server_version_num"]) < 170000

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda args: reserve(*args),
                    (
                        (repository_a, "postgres-idempotency-a"),
                        (repository_b, "postgres-idempotency-b"),
                    ),
                )
            )
        winners = [result for result in results if result != "conflict"]
        assert len(winners) == 1
        assert results.count("conflict") == 1
        assert repository_a.get_release_revision(model_name) == 1

        winner = repository_a.get_release_saga(winners[0])
        assert winner is not None
        repository_a.save_release_saga(
            winner.evolve(
                state=ReleaseSagaState.MODEL_STATE_APPLIED,
                attempt=1,
            )
        )

        worm = LocalAppendOnlyWormSink(tmp_path / "postgres-learninghub-worm")
        audit = DurableAuditLog(engine_a, worm_sink=worm)
        audit.record(
            AuditEvent(
                event_type="learninghub.release_recovery.v1",
                actor="postgres-integration",
                action="recover_release",
                resource=f"model/{model_name}",
                outcome="compensation_failed",
                correlation_id=winners[0],
                metadata={
                    "release_id": winners[0],
                    "release_revision": 1,
                    "scope": "global",
                },
            )
        )
        assert audit.verify_chain().ok
        assert list((tmp_path / "postgres-learninghub-worm").rglob("*.json"))
    finally:
        engine_a.close()
        engine_b.close()

    restarted_engine, restarted_repository = _repository(database_url)
    try:
        restarted = restarted_repository.get_release_saga(winners[0])
        assert restarted is not None
        assert restarted.state is ReleaseSagaState.MODEL_STATE_APPLIED
        assert restarted.attempt == 1
        assert restarted_repository.get_release_revision(model_name) == 1
    finally:
        restarted_engine.execute(
            "DELETE FROM durable_documents WHERE group_key = ?",
            (model_name,),
        )
        restarted_engine.execute(
            "DELETE FROM durable_documents "
            "WHERE collection = ? AND doc_id = ?",
            ("learninghub.release_revisions", model_name),
        )
        restarted_engine.close()


def test_postgresql16_full_mlflow_release_and_lease_fenced_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full governed release and orphan recovery on PostgreSQL 16.

    Durable state (model versions, cards, aliases, sagas, release revisions,
    audit chain) lives in PostgreSQL; the OSS MLflow registry runs against a
    local tracking store. The test drives a real promotion through the MLflow
    adapter, crashes a second release mid-saga, proves a live lease keeps a
    second process from compensating it, and then proves the expired-lease
    takeover compensates MLflow metadata, stages, and aliases while fencing the
    original owner out for good.
    """

    database_url = os.environ["INTAKE_TEST_DATABASE_URL"]
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow.sqlite3'}"
    worm_root = tmp_path / "postgres-learninghub-release-worm"
    model_name = f"forecast_revenue_interval_{uuid4().hex}"
    engine_a, repository_a = _repository(database_url)
    engine_b, repository_b = _repository(database_url)
    registry_a = MlflowRegistryAdapter(repository_a, tracking_uri=tracking_uri)
    registry_b = MlflowRegistryAdapter(repository_b, tracking_uri=tracking_uri)
    service_a = LearningHubService(
        repository=repository_a,
        registry=registry_a,
        audit_log=DurableAuditLog(engine_a, worm_sink=LocalAppendOnlyWormSink(worm_root)),
        worker_id="pg-worker-a",
        release_lease_seconds=600.0,
    )
    service_b = LearningHubService(
        repository=repository_b,
        registry=registry_b,
        audit_log=DurableAuditLog(engine_b, worm_sink=LocalAppendOnlyWormSink(worm_root)),
        worker_id="pg-worker-b",
    )
    validation_run_ids: list[str] = []

    def release(service: LearningHubService, *, version: str, revision: int):
        return service.request_release(
            model_name=model_name,
            version=version,
            release_type=ReleaseType.FULL,
            reason="promote validated champion",
            approval_id=f"approval-pg-{version}",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("production alias resolves",),
            fail_criteria=("coverage regression",),
            requested_by="ml-owner",
            approved_by="reviewer-a",
            correlation_id=f"corr-pg-{version}",
            expected_release_revision=revision,
            idempotency_key=f"release-pg-{version}",
        )

    try:
        v1 = prepare_candidate(service_a, "1.0.0", model_name=model_name)
        v2 = prepare_candidate(service_a, "1.1.0", model_name=model_name)
        for version in (v1.version, v2.version):
            card = repository_a.get_model_card(model_name, version)
            validation_run_ids.append(card.validation_run_id)

        decision = release(service_a, version=v1.version, revision=0)

        assert repository_a.get_alias(model_name, ModelAlias.PRODUCTION).version == v1.version
        assert registry_a.get_by_alias(
            model_name=model_name,
            alias=ModelAlias.PRODUCTION,
        ).version == v1.version
        promoted_tags = registry_a._require_model_version(model_name, v1.version).tags
        assert promoted_tags["oday.model_version.release_id"] == decision.release_id
        assert promoted_tags["oday.model_version.approval_id"] == "approval-pg-1.0.0"
        assert promoted_tags["oday.model_version.release_approved_by"] == "reviewer-a"
        assert promoted_tags["oday.model_version.requested_by"] == "ml-owner"
        assert (
            promoted_tags["oday.model_version.model_card_checksum"]
            == decision.model_card_checksum
        )
        assert promoted_tags["oday.model_version.validation_status"] == "PASSED"
        assert repository_b.get_release_saga(decision.release_id).state is (
            ReleaseSagaState.COMPLETED
        )

        # A second release is interrupted after MLflow was already mutated.
        save_saga = repository_a.save_release_saga
        crashed = False

        def interrupt_after_model_state(saga):
            nonlocal crashed
            saved = save_saga(saga)
            if (
                saga.state is ReleaseSagaState.MODEL_STATE_APPLIED
                and saga.command.get("version") == v2.version
                and not crashed
            ):
                crashed = True
                raise _PostgresProcessInterrupted(saga.state.value)
            return saved

        monkeypatch.setattr(repository_a, "save_release_saga", interrupt_after_model_state)
        with pytest.raises(_PostgresProcessInterrupted):
            release(service_a, version=v2.version, revision=1)
        monkeypatch.setattr(repository_a, "save_release_saga", save_saga)

        orphaned = repository_b.list_release_sagas(model_name, include_terminal=False)[0]
        assert orphaned.lease_owner == "pg-worker-a"
        assert orphaned.lease_active()

        # A live lease is not recoverable state: the peer process must not touch it.
        assert service_b.recover_incomplete_releases(model_name=model_name) == ()
        assert repository_b.get_release_saga(orphaned.release_id).state is (
            ReleaseSagaState.MODEL_STATE_APPLIED
        )

        repository_b.save_release_saga(
            orphaned.evolve(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        recovered = service_b.recover_incomplete_releases(model_name=model_name)
        assert [saga.state for saga in recovered] == [ReleaseSagaState.COMPENSATED]
        assert recovered[0].fence_token == orphaned.fence_token + 1
        assert repository_b.get_release_decision(orphaned.release_id) is None
        assert repository_b.get_alias(model_name, ModelAlias.PRODUCTION).version == v1.version
        assert registry_b.get_by_alias(
            model_name=model_name,
            alias=ModelAlias.PRODUCTION,
        ).version == v1.version
        compensated_tags = registry_b._require_model_version(model_name, v2.version).tags
        assert compensated_tags["oday.model_version.release_id"] == ""
        assert compensated_tags["oday.model_version.approval_id"] == ""
        assert compensated_tags["oday.model_version.approved_by"] == ""
        assert repository_b.get_model_version(model_name, v2.version).stage is ModelStage.DEV

        with pytest.raises(LearningHubReleaseFenced):
            repository_a.save_release_saga(
                orphaned.evolve(state=ReleaseSagaState.ALIASES_APPLIED)
            )
        assert service_b.audit_log.verify_chain().ok
    finally:
        engine_a.close()
        engine_b.close()

    restarted_engine, restarted_repository = _repository(database_url)
    try:
        restarted = restarted_repository.list_release_sagas(model_name)
        assert {saga.state for saga in restarted} == {
            ReleaseSagaState.COMPLETED,
            ReleaseSagaState.COMPENSATED,
        }
        assert restarted_repository.get_release_revision(model_name) == 2
        assert (
            restarted_repository.get_alias(model_name, ModelAlias.PRODUCTION).version == "1.0.0"
        )
    finally:
        restarted_engine.execute(
            "DELETE FROM durable_documents WHERE group_key = ?",
            (model_name,),
        )
        for collection, doc_id in (
            ("learninghub.release_revisions", model_name),
            ("learninghub.datasets", f"{model_name}-training-1.0.0"),
            ("learninghub.datasets", f"{model_name}-training-1.1.0"),
            *(
                ("learninghub.validation_runs", validation_run_id)
                for validation_run_id in validation_run_ids
            ),
        ):
            restarted_engine.execute(
                "DELETE FROM durable_documents WHERE collection = ? AND doc_id = ?",
                (collection, doc_id),
            )
        restarted_engine.close()
