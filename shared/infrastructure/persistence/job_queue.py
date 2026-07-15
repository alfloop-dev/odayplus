"""Durable, restart-survivable job queue (ODP-PV-009).

Drop-in replacement for :class:`shared.jobs.queue.InMemoryJobQueue`. Jobs and
their idempotency index are persisted columnar so a retried submission replays
the original job after a restart instead of duplicating work, and so a job's
correlation id remains queryable.
"""

from __future__ import annotations

import json
from datetime import datetime

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
            "  payload_json, created_at, attempts, leased_until, max_retries"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.job_id,
                record.job_type,
                record.status.value,
                record.correlation_id,
                record.idempotency_key,
                json.dumps(record.payload),
                record.created_at.isoformat(),
                record.attempts,
                record.leased_until.isoformat() if record.leased_until else None,
                record.max_retries,
            ),
        )
        return record, True

    def get(self, job_id: str) -> JobRecord | None:
        row = self._engine.query_one("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,))
        return None if row is None else self._row_to_record(row)

    def lease(self, lease_duration_seconds: float) -> JobRecord | None:
        from datetime import UTC, timedelta
        with self._engine.lock:
            now = datetime.now(UTC)
            now_str = now.isoformat()

            while True:
                # Find the oldest eligible job
                row = self._engine.query_one(
                    "SELECT * FROM durable_jobs WHERE status = ? OR (status = ? AND leased_until < ?) ORDER BY created_at ASC LIMIT 1",
                    (JobStatus.QUEUED.value, JobStatus.RUNNING.value, now_str)
                )
                if row is None:
                    return None

                job_id = row["job_id"]
                current_attempts = row["attempts"]
                max_retries = row["max_retries"]

                # Check if it has exceeded max_retries
                if current_attempts >= max_retries:
                    self._engine.execute(
                        "UPDATE durable_jobs SET status = ?, leased_until = NULL WHERE job_id = ?",
                        (JobStatus.FAILED.value, job_id)
                    )
                    continue

                new_attempts = current_attempts + 1
                leased_until_dt = now + timedelta(seconds=lease_duration_seconds)
                leased_until_str = leased_until_dt.isoformat()

                self._engine.execute(
                    "UPDATE durable_jobs SET status = ?, attempts = ?, leased_until = ? WHERE job_id = ?",
                    (JobStatus.RUNNING.value, new_attempts, leased_until_str, job_id)
                )

                updated_row = self._engine.query_one("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,))
                return self._row_to_record(updated_row)

    def complete(self, job_id: str, lease_token: datetime | str | None = None) -> bool:
        with self._engine.lock:
            if lease_token is not None:
                row = self._engine.query_one("SELECT status, leased_until FROM durable_jobs WHERE job_id = ?", (job_id,))
                if row is None:
                    return False
                token_str = lease_token.isoformat() if isinstance(lease_token, datetime) else str(lease_token)
                if row["status"] != JobStatus.RUNNING.value or row["leased_until"] != token_str:
                    return False
            self._engine.execute(
                "UPDATE durable_jobs SET status = ?, leased_until = NULL WHERE job_id = ?",
                (JobStatus.SUCCEEDED.value, job_id)
            )
            return True

    def fail(self, job_id: str, lease_token: datetime | str | None = None) -> bool:
        with self._engine.lock:
            if lease_token is not None:
                row = self._engine.query_one("SELECT status, leased_until FROM durable_jobs WHERE job_id = ?", (job_id,))
                if row is None:
                    return False
                token_str = lease_token.isoformat() if isinstance(lease_token, datetime) else str(lease_token)
                if row["status"] != JobStatus.RUNNING.value or row["leased_until"] != token_str:
                    return False

            row = self._engine.query_one("SELECT max_retries, attempts FROM durable_jobs WHERE job_id = ?", (job_id,))
            if row is not None:
                attempts = row["attempts"]
                max_retries = row["max_retries"]
                if attempts < max_retries:
                    self._engine.execute(
                        "UPDATE durable_jobs SET status = ?, leased_until = NULL WHERE job_id = ?",
                        (JobStatus.QUEUED.value, job_id)
                    )
                else:
                    self._engine.execute(
                        "UPDATE durable_jobs SET status = ?, leased_until = NULL WHERE job_id = ?",
                        (JobStatus.FAILED.value, job_id)
                    )
            return True

    @staticmethod
    def _row_to_record(row) -> JobRecord:
        attempts = row["attempts"] if "attempts" in row.keys() else 0
        leased_until_str = row["leased_until"] if "leased_until" in row.keys() else None
        leased_until = datetime.fromisoformat(leased_until_str) if leased_until_str else None
        max_retries = row["max_retries"] if "max_retries" in row.keys() else 3

        return JobRecord(
            job_type=row["job_type"],
            payload=json.loads(row["payload_json"]),
            correlation_id=row["correlation_id"],
            idempotency_key=row["idempotency_key"],
            status=JobStatus(row["status"]),
            job_id=row["job_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            attempts=attempts,
            leased_until=leased_until,
            max_retries=max_retries,
        )



__all__ = ["DurableJobQueue"]
