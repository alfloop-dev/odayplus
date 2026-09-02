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
from shared.jobs.queue import (
    NON_EXECUTABLE_RECEIPT_JOB_TYPE_SUFFIXES,
    JobDeliveryState,
    JobRecord,
    JobRequest,
    JobStatus,
)


class JobFenceRejectedError(ValueError):
    """Raised when a job write/checkpoint fails due to stale fence_token or version."""

    pass


class DurableJobQueue:
    """``enqueue`` / ``get`` over the ``durable_jobs`` table."""

    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    def count_active_jobs(self, *, tenant_id: str | None = None) -> int:
        with self._engine.lock:
            tenant_clause = ""
            params: list[str] = [
                JobStatus.QUEUED.value,
                JobStatus.RUNNING.value,
                *(f"%{suffix}" for suffix in NON_EXECUTABLE_RECEIPT_JOB_TYPE_SUFFIXES),
            ]
            if tenant_id is not None:
                if str(getattr(self._engine, "dialect", "")).lower() == "postgresql":
                    tenant_clause = " AND payload_json ->> 'tenant_id' = ?"
                else:
                    tenant_clause = " AND json_extract(payload_json, '$.tenant_id') = ?"
                params.append(tenant_id)
            # tenant_clause is one of two fixed SQL fragments; the value is bound.
            row = self._engine.query_one(
                "SELECT COUNT(*) as count FROM durable_jobs "
                "WHERE (status = ? OR status = ?) "
                "AND job_type NOT LIKE ? AND job_type NOT LIKE ?" + tenant_clause,  # nosec B608
                tuple(params),
            )
            return row["count"] if row else 0

    def enqueue(self, request: JobRequest, *, correlation_id: str) -> tuple[JobRecord, bool]:
        record = JobRecord(
            job_type=request.job_type,
            payload=request.payload,
            correlation_id=correlation_id,
            idempotency_key=request.idempotency_key,
        )
        with self._engine.lock:
            result = self._engine.execute(
                "INSERT INTO durable_jobs("
                "  job_id, job_type, status, correlation_id, idempotency_key, "
                "  payload_json, created_at, fence_token, version, locked_by, "
                "  heartbeat_at, lease_expires_at, attempts, error_message, "
                "  leased_until, max_retries"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
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
                    record.leased_until.isoformat() if record.leased_until else None,
                    record.max_retries,
                ),
            )
            if int(getattr(result, "rowcount", 0)) > 0:
                return record, True
            if request.idempotency_key:
                existing = self._engine.query_one(
                    "SELECT * FROM durable_jobs WHERE idempotency_key = ?",
                    (request.idempotency_key,),
                )
                if existing is not None:
                    return self._row_to_record(existing), False
        raise RuntimeError("durable job reservation conflicted without a replay record")

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
                    "SELECT * FROM durable_jobs "
                    "WHERE (status = ? OR (status = ? AND leased_until < ?)) "
                    "AND job_type NOT LIKE ? AND job_type NOT LIKE ? "
                    "ORDER BY created_at ASC LIMIT 1",
                    (
                        JobStatus.QUEUED.value,
                        JobStatus.RUNNING.value,
                        now_str,
                        *(f"%{suffix}" for suffix in NON_EXECUTABLE_RECEIPT_JOB_TYPE_SUFFIXES),
                    ),
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
                        (JobStatus.FAILED.value, job_id),
                    )
                    continue

                new_attempts = current_attempts + 1
                leased_until_dt = now + timedelta(seconds=lease_duration_seconds)
                leased_until_str = leased_until_dt.isoformat()

                self._engine.execute(
                    "UPDATE durable_jobs SET status = ?, attempts = ?, leased_until = ? WHERE job_id = ?",
                    (JobStatus.RUNNING.value, new_attempts, leased_until_str, job_id),
                )

                updated_row = self._engine.query_one(
                    "SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)
                )
                return self._row_to_record(updated_row)

    def complete(self, job_id: str, lease_token: datetime | str | None = None) -> bool:
        with self._engine.lock:
            if lease_token is not None:
                row = self._engine.query_one(
                    "SELECT status, leased_until FROM durable_jobs WHERE job_id = ?", (job_id,)
                )
                if row is None:
                    return False
                token_str = (
                    lease_token.isoformat()
                    if isinstance(lease_token, datetime)
                    else str(lease_token)
                )
                if row["status"] != JobStatus.RUNNING.value or row["leased_until"] != token_str:
                    return False
            self._engine.execute(
                "UPDATE durable_jobs SET status = ?, leased_until = NULL WHERE job_id = ?",
                (JobStatus.SUCCEEDED.value, job_id),
            )
            return True

    def fail(self, job_id: str, lease_token: datetime | str | None = None) -> bool:
        with self._engine.lock:
            if lease_token is not None:
                row = self._engine.query_one(
                    "SELECT status, leased_until FROM durable_jobs WHERE job_id = ?", (job_id,)
                )
                if row is None:
                    return False
                token_str = (
                    lease_token.isoformat()
                    if isinstance(lease_token, datetime)
                    else str(lease_token)
                )
                if row["status"] != JobStatus.RUNNING.value or row["leased_until"] != token_str:
                    return False

            row = self._engine.query_one(
                "SELECT max_retries, attempts FROM durable_jobs WHERE job_id = ?", (job_id,)
            )
            if row is not None:
                attempts = row["attempts"]
                max_retries = row["max_retries"]
                if attempts < max_retries:
                    self._engine.execute(
                        "UPDATE durable_jobs SET status = ?, leased_until = NULL WHERE job_id = ?",
                        (JobStatus.QUEUED.value, job_id),
                    )
                else:
                    self._engine.execute(
                        "UPDATE durable_jobs SET status = ?, leased_until = NULL WHERE job_id = ?",
                        (JobStatus.FAILED.value, job_id),
                    )
            return True

    def claim_next(self, worker_id: str = "worker-1") -> JobRecord | None:
        with self._engine.lock:
            now = datetime.now(UTC)
            now_str = now.isoformat()
            heartbeat = now_str
            lease_expires = (now + timedelta(seconds=45)).isoformat()
            receipt_patterns = tuple(
                f"%{suffix}" for suffix in NON_EXECUTABLE_RECEIPT_JOB_TYPE_SUFFIXES
            )

            if str(getattr(self._engine, "dialect", "")).lower() == "postgresql":
                updated = self._engine.query_one(
                    "WITH candidate AS ("
                    "  SELECT job_id FROM durable_jobs "
                    "  WHERE (status = ? OR (status = ? AND lease_expires_at IS NOT NULL "
                    "    AND lease_expires_at < ?)) "
                    "  AND job_type NOT LIKE ? AND job_type NOT LIKE ? "
                    "  ORDER BY created_at "
                    "  FOR UPDATE SKIP LOCKED "
                    "  LIMIT 1"
                    ") "
                    "UPDATE durable_jobs AS jobs SET "
                    "  status = ?, fence_token = jobs.fence_token + 1, "
                    "  version = jobs.version + 1, locked_by = ?, heartbeat_at = ?, "
                    "  lease_expires_at = ?, attempts = jobs.attempts + 1 "
                    "FROM candidate "
                    "WHERE jobs.job_id = candidate.job_id "
                    "RETURNING jobs.*",
                    (
                        JobStatus.QUEUED.value,
                        JobStatus.RUNNING.value,
                        now_str,
                        *receipt_patterns,
                        JobStatus.RUNNING.value,
                        worker_id,
                        heartbeat,
                        lease_expires,
                    ),
                )
                return None if updated is None else self._row_to_record(updated)

            while True:
                row = self._engine.query_one(
                    "SELECT * FROM durable_jobs "
                    "WHERE (status = ? OR (status = ? AND lease_expires_at IS NOT NULL "
                    "AND lease_expires_at < ?)) "
                    "AND job_type NOT LIKE ? AND job_type NOT LIKE ? "
                    "ORDER BY created_at LIMIT 1",
                    (
                        JobStatus.QUEUED.value,
                        JobStatus.RUNNING.value,
                        now_str,
                        *receipt_patterns,
                    ),
                )
                if row is None:
                    return None
                record = self._row_to_record(row)
                result = self._engine.execute(
                    "UPDATE durable_jobs SET "
                    "status = ?, fence_token = fence_token + 1, version = version + 1, "
                    "locked_by = ?, heartbeat_at = ?, lease_expires_at = ?, "
                    "attempts = attempts + 1 "
                    "WHERE job_id = ? AND version = ? "
                    "AND (status = ? OR (status = ? AND lease_expires_at IS NOT NULL "
                    "AND lease_expires_at < ?))",
                    (
                        JobStatus.RUNNING.value,
                        worker_id,
                        heartbeat,
                        lease_expires,
                        record.job_id,
                        record.version,
                        JobStatus.QUEUED.value,
                        JobStatus.RUNNING.value,
                        now_str,
                    ),
                )
                if int(getattr(result, "rowcount", 0)) != 1:
                    continue
                updated = self._engine.query_one(
                    "SELECT * FROM durable_jobs WHERE job_id = ?", (record.job_id,)
                )
                if updated is None:
                    raise RuntimeError("claimed durable job disappeared")
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
            result = self._engine.execute(
                "UPDATE durable_jobs SET status = ?, payload_json = ?, "
                "version = version + 1, attempts = 0, error_message = NULL, "
                "locked_by = NULL, heartbeat_at = NULL, lease_expires_at = NULL "
                "WHERE job_id = ? AND version = ? AND fence_token = ?",
                (
                    JobStatus.QUEUED.value,
                    payload_json,
                    job_id,
                    record.version,
                    record.fence_token,
                ),
            )
            if int(getattr(result, "rowcount", 0)) != 1:
                raise JobFenceRejectedError(f"Job {job_id} changed while replay was being applied")
            updated = self._engine.query_one(
                "SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)
            )
            if updated is None:
                raise ValueError(f"Job {job_id} not found after replay")
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
            assignments = [
                "status = ?",
                "version = version + 1",
                "error_message = ?",
            ]
            params: list[Any] = [status.value, error_message]
            if payload is not None:
                assignments.append("payload_json = ?")
                params.append(json.dumps(payload))
            if status != JobStatus.RUNNING:
                assignments.extend(
                    [
                        "locked_by = NULL",
                        "heartbeat_at = NULL",
                        "lease_expires_at = NULL",
                    ]
                )

            predicates = ["job_id = ?"]
            params.append(job_id)
            if expected_version is not None:
                predicates.append("version = ?")
                params.append(expected_version)
            if fence_token is not None:
                predicates.append("fence_token = ?")
                params.append(fence_token)

            # assignments/predicates hold only fixed literal fragments;
            # every value is bound through ? placeholders in `params`.
            result = self._engine.execute(
                "UPDATE durable_jobs SET "  # nosec B608 - fixed fragments, values bound via ? placeholders
                + ", ".join(assignments)
                + " WHERE "
                + " AND ".join(predicates),
                tuple(params),
            )
            if int(getattr(result, "rowcount", 0)) == 1:
                return

            current = self._engine.query_one(
                "SELECT version, fence_token FROM durable_jobs WHERE job_id = ?",
                (job_id,),
            )
            if current is None:
                raise ValueError(f"Job {job_id} not found")
            if expected_version is not None or fence_token is not None:
                raise JobFenceRejectedError(
                    f"Job {job_id} fence/version rejected: expected "
                    f"v{expected_version} f{fence_token}, got "
                    f"v{current['version']} f{current['fence_token']}"
                )
            raise RuntimeError(f"Job {job_id} status update did not persist")

    def heartbeat(self, job_id: str, expected_version: int, fence_token: int) -> int:
        """Update lease expiration and heartbeat timestamp.

        Returns the new version number after successful update, or raises JobFenceRejectedError.
        """
        with self._engine.lock:
            now = datetime.now(UTC)
            heartbeat_at = now.isoformat()
            lease_expires_at = (now + timedelta(seconds=45)).isoformat()
            new_version = expected_version + 1

            result = self._engine.execute(
                "UPDATE durable_jobs SET heartbeat_at = ?, lease_expires_at = ?, "
                "version = version + 1 "
                "WHERE job_id = ? AND status = ? AND version = ? AND fence_token = ?",
                (
                    heartbeat_at,
                    lease_expires_at,
                    job_id,
                    JobStatus.RUNNING.value,
                    expected_version,
                    fence_token,
                ),
            )
            if int(getattr(result, "rowcount", 0)) == 1:
                return new_version

            current = self._engine.query_one(
                "SELECT version, fence_token FROM durable_jobs WHERE job_id = ?",
                (job_id,),
            )
            if current is None:
                raise ValueError(f"Job {job_id} not found")
            raise JobFenceRejectedError(
                f"Job {job_id} fence/version rejected in heartbeat: expected "
                f"v{expected_version} f{fence_token}, got "
                f"v{current['version']} f{current['fence_token']}"
            )

    @staticmethod
    def _row_to_record(row) -> JobRecord:
        keys = row.keys()
        attempts = row["attempts"] if "attempts" in keys else 0
        leased_until_str = row["leased_until"] if "leased_until" in keys else None
        leased_until = datetime.fromisoformat(leased_until_str) if leased_until_str else None
        max_retries = row["max_retries"] if "max_retries" in keys else 3

        heartbeat_val = None
        if "heartbeat_at" in keys and row["heartbeat_at"] is not None:
            heartbeat_val = datetime.fromisoformat(row["heartbeat_at"])

        lease_val = None
        if "lease_expires_at" in keys and row["lease_expires_at"] is not None:
            lease_val = datetime.fromisoformat(row["lease_expires_at"])

        status_val = JobStatus(row["status"])
        delivery_state: JobDeliveryState | None = None
        if "delivery_state" in keys and row["delivery_state"]:
            delivery_state = JobDeliveryState(row["delivery_state"])
        elif attempts >= max_retries and status_val == JobStatus.FAILED:
            delivery_state = JobDeliveryState.DEAD_LETTER
        elif attempts > 0 and status_val in (JobStatus.RUNNING, JobStatus.QUEUED):
            delivery_state = JobDeliveryState.RETRYING

        return JobRecord(
            job_type=row["job_type"],
            payload=json.loads(row["payload_json"]),
            correlation_id=row["correlation_id"],
            idempotency_key=row["idempotency_key"],
            status=status_val,
            delivery_state=delivery_state,
            job_id=row["job_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            attempts=attempts,
            leased_until=leased_until,
            max_retries=max_retries,
            fence_token=row["fence_token"] if "fence_token" in keys else 0,
            version=row["version"] if "version" in keys else 1,
            locked_by=row["locked_by"] if "locked_by" in keys else None,
            heartbeat_at=heartbeat_val,
            lease_expires_at=lease_val,
            error_message=row["error_message"] if "error_message" in keys else None,
        )


__all__ = ["DurableJobQueue", "JobFenceRejectedError"]
