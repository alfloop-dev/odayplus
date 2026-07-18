"""Durable, restart-survivable job queue (ODP-PV-009).

Drop-in replacement for :class:`shared.jobs.queue.InMemoryJobQueue`. Jobs and
their idempotency index are persisted columnar so a retried submission replays
the original job after a restart instead of duplicating work, and so a job's
correlation id remains queryable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from shared.infrastructure.persistence.engine import SqliteEngine
from shared.jobs.queue import JobRecord, JobRequest, JobStatus


class JobFenceRejectedError(ValueError):
    """Raised when a job write/checkpoint fails due to stale fence_token or version."""

    pass


class DurableJobQueue:
    """``enqueue`` / ``get`` over the ``durable_jobs`` table."""

    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    def count_active_jobs(self) -> int:
        with self._engine.lock:
            row = self._engine.query_one(
                "SELECT COUNT(*) as count FROM durable_jobs WHERE status = ? OR status = ?",
                (JobStatus.QUEUED.value, JobStatus.RUNNING.value),
            )
            return row["count"] if row else 0

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
            "  payload_json, created_at, fence_token, version, locked_by, "
            "  heartbeat_at, lease_expires_at, attempts, error_message"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.job_id,
                record.job_type,
                record.status.value,
                record.correlation_id,
                record.idempotency_key,
                json.dumps(record.payload),
                record.created_at.isoformat(),
                record.fence_token,
                record.version,
                record.locked_by,
                record.heartbeat_at.isoformat() if record.heartbeat_at else None,
                record.lease_expires_at.isoformat() if record.lease_expires_at else None,
                record.attempts,
                record.error_message,
            ),
        )
        return record, True

    def get(self, job_id: str) -> JobRecord | None:
        row = self._engine.query_one("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,))
        return None if row is None else self._row_to_record(row)

    def claim_next(self, worker_id: str = "worker-1") -> JobRecord | None:
        with self._engine.lock:
            now = datetime.now(UTC).isoformat()
            # Claim either standard queued jobs, or expired running jobs (timeout lease expiration)
            row = self._engine.query_one(
                "SELECT * FROM durable_jobs WHERE status = ? OR (status = ? AND lease_expires_at IS NOT NULL AND lease_expires_at < ?) ORDER BY created_at LIMIT 1",
                (JobStatus.QUEUED.value, JobStatus.RUNNING.value, now),
            )
            if row is None:
                return None
            record = self._row_to_record(row)

            new_fence = record.fence_token + 1
            new_version = record.version + 1
            lease_expires = (datetime.now(UTC) + timedelta(seconds=45)).isoformat()
            heartbeat = datetime.now(UTC).isoformat()
            attempts = record.attempts + 1

            self._engine.execute(
                "UPDATE durable_jobs SET status = ?, fence_token = ?, version = ?, locked_by = ?, heartbeat_at = ?, lease_expires_at = ?, attempts = ? WHERE job_id = ? AND version = ?",
                (
                    JobStatus.RUNNING.value,
                    new_fence,
                    new_version,
                    worker_id,
                    heartbeat,
                    lease_expires,
                    attempts,
                    record.job_id,
                    record.version,
                ),
            )

            # Fetch the updated row to return it accurately
            updated = self._engine.query_one(
                "SELECT * FROM durable_jobs WHERE job_id = ?", (record.job_id,)
            )
            return self._row_to_record(updated)

    def get_by_idempotency_key(self, idempotency_key: str) -> JobRecord | None:
        row = self._engine.query_one(
            "SELECT * FROM durable_jobs WHERE idempotency_key = ?", (idempotency_key,)
        )
        return None if row is None else self._row_to_record(row)

    def replay(
        self, job_id: str, *, expected_version: int | None = None, fence_token: int | None = None
    ) -> JobRecord:
        """Replay a failed or cancelled job by resetting attempts to 0 and status to QUEUED."""
        with self._engine.lock:
            row = self._engine.query_one("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,))
            if row is None:
                raise ValueError(f"Job {job_id} not found")
            record = self._row_to_record(row)
            if expected_version is not None and record.version != expected_version:
                raise JobFenceRejectedError(
                    f"Job {job_id} version mismatch: expected {expected_version}, got {record.version}"
                )
            if fence_token is not None and record.fence_token != fence_token:
                raise JobFenceRejectedError(
                    f"Job {job_id} fence token mismatch: expected {fence_token}, got {record.fence_token}"
                )

            # Reset attempts and status, lock fields to None, clear error message
            payload = dict(record.payload)
            payload.pop("_retry_count", None)
            payload.pop("stage_attempts", None)
            payload.pop("current_stage", None)

            payload_json = json.dumps(payload)
            new_version = record.version + 1
            self._engine.execute(
                "UPDATE durable_jobs SET status = ?, payload_json = ?, version = ?, attempts = 0, error_message = NULL, locked_by = NULL, heartbeat_at = NULL, lease_expires_at = NULL WHERE job_id = ?",
                (JobStatus.QUEUED.value, payload_json, new_version, job_id),
            )
            updated = self._engine.query_one(
                "SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)
            )
            return self._row_to_record(updated)

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        payload: dict[str, Any] | None = None,
        *,
        expected_version: int | None = None,
        fence_token: int | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._engine.lock:
            # Fencing / optimistic lock validation if expected_version/fence_token are supplied
            if expected_version is not None or fence_token is not None:
                row = self._engine.query_one(
                    "SELECT version, fence_token FROM durable_jobs WHERE job_id = ?", (job_id,)
                )
                if row is None:
                    raise ValueError(f"Job {job_id} not found")
                if expected_version is not None and row["version"] != expected_version:
                    raise JobFenceRejectedError(
                        f"Job {job_id} version mismatch: expected {expected_version}, got {row['version']}"
                    )
                if fence_token is not None and row["fence_token"] != fence_token:
                    raise JobFenceRejectedError(
                        f"Job {job_id} fence token mismatch: expected {fence_token}, got {row['fence_token']}"
                    )

            # Reset locking fields if transitioning to succeeded or failed
            locked_by_val = None
            if status == JobStatus.RUNNING:
                locked_by_val = "worker-1"  # keep running lock

            if payload is not None:
                payload_json = json.dumps(payload)
                self._engine.execute(
                    "UPDATE durable_jobs SET status = ?, payload_json = ?, version = version + 1, error_message = ?, locked_by = ? WHERE job_id = ?",
                    (status.value, payload_json, error_message, locked_by_val, job_id),
                )
            else:
                self._engine.execute(
                    "UPDATE durable_jobs SET status = ?, version = version + 1, error_message = ?, locked_by = ? WHERE job_id = ?",
                    (status.value, error_message, locked_by_val, job_id),
                )

    def heartbeat(self, job_id: str, expected_version: int, fence_token: int) -> int:
        """Update lease expiration and heartbeat timestamp.

        Returns the new version number after successful update, or raises JobFenceRejectedError.
        """
        with self._engine.lock:
            row = self._engine.query_one(
                "SELECT version, fence_token FROM durable_jobs WHERE job_id = ?", (job_id,)
            )
            if row is None:
                raise ValueError(f"Job {job_id} not found")
            if row["version"] != expected_version or row["fence_token"] != fence_token:
                raise JobFenceRejectedError(
                    f"Job {job_id} fence/version rejected in heartbeat: expected v{expected_version} f{fence_token}, got v{row['version']} f{row['fence_token']}"
                )

            now = datetime.now(UTC)
            heartbeat_at = now.isoformat()
            lease_expires_at = (now + timedelta(seconds=45)).isoformat()
            new_version = expected_version + 1

            self._engine.execute(
                "UPDATE durable_jobs SET heartbeat_at = ?, lease_expires_at = ?, version = ? WHERE job_id = ?",
                (heartbeat_at, lease_expires_at, new_version, job_id),
            )
            return new_version

    @staticmethod
    def _row_to_record(row) -> JobRecord:
        keys = row.keys()

        heartbeat_val = None
        if "heartbeat_at" in keys and row["heartbeat_at"] is not None:
            heartbeat_val = datetime.fromisoformat(row["heartbeat_at"])

        lease_val = None
        if "lease_expires_at" in keys and row["lease_expires_at"] is not None:
            lease_val = datetime.fromisoformat(row["lease_expires_at"])

        return JobRecord(
            job_type=row["job_type"],
            payload=json.loads(row["payload_json"]),
            correlation_id=row["correlation_id"],
            idempotency_key=row["idempotency_key"],
            status=JobStatus(row["status"]),
            job_id=row["job_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            fence_token=row["fence_token"] if "fence_token" in keys else 0,
            version=row["version"] if "version" in keys else 1,
            locked_by=row["locked_by"] if "locked_by" in keys else None,
            heartbeat_at=heartbeat_val,
            lease_expires_at=lease_val,
            attempts=row["attempts"] if "attempts" in keys else 0,
            error_message=row["error_message"] if "error_message" in keys else None,
        )


__all__ = ["DurableJobQueue", "JobFenceRejectedError"]
