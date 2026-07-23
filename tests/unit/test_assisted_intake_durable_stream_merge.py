from __future__ import annotations

import threading

from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)


def test_concurrent_intake_writers_do_not_drop_append_only_receipts(
    tmp_path,
) -> None:
    database_path = str(tmp_path / "intake-streams.sqlite3")
    api_engine = SqliteEngine(database_path)
    worker_engine = SqliteEngine(database_path)
    try:
        api_repository = DurableAssistedIntakeRepository(
            SqliteDocumentStore(api_engine)
        )
        worker_repository = DurableAssistedIntakeRepository(
            SqliteDocumentStore(worker_engine)
        )
        base = {
            "id": "intake-001",
            "version": 1,
            "updatedAt": "2026-07-23T10:00:00Z",
            "state": "FAILED",
            "processingHistory": [],
            "lifecycleReceipts": [],
            "auditEvents": [],
            "decisionReceipts": [],
        }
        api_repository.save_intake(base)

        api_writer = {
            **base,
            "version": 2,
            "updatedAt": "2026-07-23T10:01:00Z",
            "state": "SUBMITTED",
            "lifecycleReceipts": [
                {
                    "receiptId": "receipt-retry",
                    "dedupeKey": "job:RETRY:job-001:2",
                    "occurredAt": "2026-07-23T10:01:00Z",
                    "action": "RETRY",
                }
            ],
        }
        worker_writer = {
            **base,
            "version": 3,
            "updatedAt": "2026-07-23T10:02:00Z",
            "state": "RETRIEVING",
            "processingHistory": [
                {
                    "transitionId": "transition-retrieving",
                    "occurredAt": "2026-07-23T10:02:00Z",
                    "toStage": "RETRIEVING",
                }
            ],
        }

        barrier = threading.Barrier(3)

        def save(repository, payload) -> None:
            barrier.wait()
            repository.save_intake(payload)

        api_thread = threading.Thread(
            target=save,
            args=(api_repository, api_writer),
        )
        worker_thread = threading.Thread(
            target=save,
            args=(worker_repository, worker_writer),
        )
        api_thread.start()
        worker_thread.start()
        barrier.wait()
        api_thread.join(timeout=5)
        worker_thread.join(timeout=5)

        assert not api_thread.is_alive()
        assert not worker_thread.is_alive()

        persisted = api_repository.list_intakes()[0]
        assert persisted["version"] == 3
        assert persisted["state"] == "RETRIEVING"
        assert [
            item["action"] for item in persisted["lifecycleReceipts"]
        ] == ["RETRY"]
        assert [
            item["toStage"] for item in persisted["processingHistory"]
        ] == ["RETRIEVING"]

        stale_writer = {
            **base,
            "version": 2,
            "updatedAt": "2026-07-23T10:01:30Z",
            "state": "FAILED",
            "auditEvents": [
                {
                    "id": "audit-stale-writer",
                    "occurredAt": "2026-07-23T10:01:30Z",
                }
            ],
        }
        worker_repository.save_intake(stale_writer)
        persisted = api_repository.list_intakes()[0]
        assert persisted["version"] == 3
        assert persisted["state"] == "RETRIEVING"
        assert [item["id"] for item in persisted["auditEvents"]] == [
            "audit-stale-writer"
        ]
    finally:
        api_engine.close()
        worker_engine.close()
