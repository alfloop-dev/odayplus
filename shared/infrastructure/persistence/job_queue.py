"""Durable, restart-survivable job queue (ODP-PV-009).

Drop-in replacement for :class:`shared.jobs.queue.InMemoryJobQueue`. Jobs and
their idempotency index are persisted columnar so a retried submission replays
the original job after a restart instead of duplicating work, and so a job's
correlation id remains queryable.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from shared.infrastructure.persistence.engine import SqliteEngine
from shared.jobs.queue import JobRecord, JobRequest, JobStatus


class DurableJobQueue:
    """``enqueue`` / ``get`` over the ``durable_jobs`` table."""

    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    def enqueue(self, request: JobRequest, *, correlation_id: str) -> tuple[JobRecord, bool]:
        if request.idempotency_key:
            existing = self._engine.query_one(
                "SELECT * FROM durable_jobs WHERE idempotency_key = ?",
                (request.idempotency_key,),
            )
            if existing is not None:
                return self._row_to_record(existing), False

        record = JobRecord(
            job_type=request.job_type,
            payload=request.payload,
            correlation_id=correlation_id,
            idempotency_key=request.idempotency_key,
        )
        self._engine.execute(
            "INSERT INTO durable_jobs("
            "  job_id, job_type, status, correlation_id, idempotency_key, "
            "  payload_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.job_id,
                record.job_type,
                record.status.value,
                record.correlation_id,
                record.idempotency_key,
                json.dumps(record.payload),
                record.created_at.isoformat(),
            ),
        )
        return record, True

    def get(self, job_id: str) -> JobRecord | None:
        row = self._engine.query_one(
            "SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)
        )
        return None if row is None else self._row_to_record(row)

    def claim_next(self) -> JobRecord | None:
        with self._engine.lock:
            row = self._engine.query_one(
                "SELECT * FROM durable_jobs WHERE status = ? ORDER BY created_at LIMIT 1",
                (JobStatus.QUEUED.value,),
            )
            if row is None:
                return None
            record = self._row_to_record(row)
            self._engine.execute(
                "UPDATE durable_jobs SET status = ? WHERE job_id = ?",
                (JobStatus.RUNNING.value, record.job_id),
            )
            return JobRecord(
                job_type=record.job_type,
                payload=record.payload,
                correlation_id=record.correlation_id,
                idempotency_key=record.idempotency_key,
                status=JobStatus.RUNNING,
                job_id=record.job_id,
                created_at=record.created_at,
            )

    def update_status(self, job_id: str, status: JobStatus, payload: dict[str, Any] | None = None) -> None:
        if payload is not None:
            self._engine.execute(
                "UPDATE durable_jobs SET status = ?, payload_json = ? WHERE job_id = ?",
                (status.value, json.dumps(payload), job_id),
            )
        else:
            self._engine.execute(
                "UPDATE durable_jobs SET status = ? WHERE job_id = ?",
                (status.value, job_id),
            )

    @staticmethod
    def _row_to_record(row) -> JobRecord:
        return JobRecord(
            job_type=row["job_type"],
            payload=json.loads(row["payload_json"]),
            correlation_id=row["correlation_id"],
            idempotency_key=row["idempotency_key"],
            status=JobStatus(row["status"]),
            job_id=row["job_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


__all__ = ["DurableJobQueue"]
