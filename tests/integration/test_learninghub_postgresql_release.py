from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

from modules.learninghub.infrastructure.repositories import (
    LearningHubReleaseConflict,
    ModelReleaseSaga,
    ReleaseSagaState,
)
from shared.audit import AuditEvent
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.infrastructure.persistence import DurableAuditLog, DurableLearningHubRepository
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.postgresql import PostgresEngine

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
