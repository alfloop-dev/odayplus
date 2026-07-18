from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NonRetryableJobError(RuntimeError):
    """Raised when a job should fail permanently without further retries."""

    pass


@dataclass(frozen=True)
class JobRequest:
    job_type: str
    payload: dict[str, Any]
    idempotency_key: str | None = None


@dataclass(frozen=True)
class JobRecord:
    job_type: str
    payload: dict[str, Any]
    correlation_id: str
    idempotency_key: str | None = None
    status: JobStatus = JobStatus.QUEUED
    job_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    fence_token: int = 0
    version: int = 1
    locked_by: str | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    attempts: int = 0
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status.value,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
            "fence_token": self.fence_token,
            "version": self.version,
            "locked_by": self.locked_by,
            "heartbeat_at": self.heartbeat_at.isoformat() if self.heartbeat_at else None,
            "lease_expires_at": self.lease_expires_at.isoformat()
            if self.lease_expires_at
            else None,
            "attempts": self.attempts,
            "error_message": self.error_message,
        }


class InMemoryJobQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._idempotency_index: dict[str, str] = {}

    def count_active_jobs(self) -> int:
        return sum(
            1 for job in self._jobs.values() if job.status in (JobStatus.QUEUED, JobStatus.RUNNING)
        )

    def enqueue(self, request: JobRequest, *, correlation_id: str) -> tuple[JobRecord, bool]:
        if request.idempotency_key:
            existing_job_id = self._idempotency_index.get(request.idempotency_key)
            if existing_job_id is not None:
                return self._jobs[existing_job_id], False

        record = JobRecord(
            job_type=request.job_type,
            payload=request.payload,
            correlation_id=correlation_id,
            idempotency_key=request.idempotency_key,
        )
        self._jobs[record.job_id] = record
        if request.idempotency_key:
            self._idempotency_index[request.idempotency_key] = record.job_id
        return record, True

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def get_by_idempotency_key(self, idempotency_key: str) -> JobRecord | None:
        job_id = self._idempotency_index.get(idempotency_key)
        return self.get(job_id) if job_id else None

    def replay(
        self, job_id: str, *, expected_version: int | None = None, fence_token: int | None = None
    ) -> JobRecord:
        if job_id not in self._jobs:
            raise ValueError(f"Job {job_id} not found")
        record = self._jobs[job_id]
        if expected_version is not None and record.version != expected_version:
            raise ValueError("Fence/version mismatch")
        if fence_token is not None and record.fence_token != fence_token:
            raise ValueError("Fence/version mismatch")

        payload = dict(record.payload)
        payload.pop("_retry_count", None)
        payload.pop("stage_attempts", None)
        payload.pop("current_stage", None)

        updated = JobRecord(
            job_type=record.job_type,
            payload=payload,
            correlation_id=record.correlation_id,
            idempotency_key=record.idempotency_key,
            status=JobStatus.QUEUED,
            job_id=record.job_id,
            created_at=record.created_at,
            fence_token=record.fence_token,
            version=record.version + 1,
            locked_by=None,
            heartbeat_at=None,
            lease_expires_at=None,
            attempts=0,
            error_message=None,
        )
        self._jobs[job_id] = updated
        return updated

    def claim_next(self, worker_id: str = "worker-1") -> JobRecord | None:
        for job_id, record in self._jobs.items():
            if record.status == JobStatus.QUEUED:
                updated = JobRecord(
                    job_type=record.job_type,
                    payload=record.payload,
                    correlation_id=record.correlation_id,
                    idempotency_key=record.idempotency_key,
                    status=JobStatus.RUNNING,
                    job_id=record.job_id,
                    created_at=record.created_at,
                    fence_token=record.fence_token + 1,
                    version=record.version + 1,
                    locked_by=worker_id,
                    heartbeat_at=datetime.now(UTC),
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=45),
                    attempts=record.attempts + 1,
                )
                self._jobs[job_id] = updated
                return updated
        return None

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
        if job_id in self._jobs:
            record = self._jobs[job_id]
            # Simple check for mock
            if expected_version is not None and record.version != expected_version:
                raise ValueError(
                    f"Job version mismatch: expected {expected_version}, got {record.version}"
                )
            if fence_token is not None and record.fence_token != fence_token:
                raise ValueError(
                    f"Job fence token mismatch: expected {fence_token}, got {record.fence_token}"
                )

            self._jobs[job_id] = JobRecord(
                job_type=record.job_type,
                payload=payload if payload is not None else record.payload,
                correlation_id=record.correlation_id,
                idempotency_key=record.idempotency_key,
                status=status,
                job_id=record.job_id,
                created_at=record.created_at,
                fence_token=record.fence_token,
                version=record.version + 1,
                locked_by=record.locked_by if status == JobStatus.RUNNING else None,
                heartbeat_at=record.heartbeat_at,
                lease_expires_at=record.lease_expires_at,
                attempts=record.attempts,
                error_message=error_message or record.error_message,
            )

    def heartbeat(self, job_id: str, expected_version: int, fence_token: int) -> int:
        if job_id not in self._jobs:
            raise ValueError(f"Job {job_id} not found")
        record = self._jobs[job_id]
        if record.version != expected_version or record.fence_token != fence_token:
            raise ValueError("Fence/version mismatch")
        new_version = expected_version + 1
        self._jobs[job_id] = JobRecord(
            job_type=record.job_type,
            payload=record.payload,
            correlation_id=record.correlation_id,
            idempotency_key=record.idempotency_key,
            status=record.status,
            job_id=record.job_id,
            created_at=record.created_at,
            fence_token=record.fence_token,
            version=new_version,
            locked_by=record.locked_by,
            heartbeat_at=datetime.now(UTC),
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=45),
            attempts=record.attempts,
            error_message=record.error_message,
        )
        return new_version
