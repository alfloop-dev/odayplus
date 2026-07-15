from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


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
    attempts: int = 0
    leased_until: datetime | None = None
    max_retries: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status.value,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
            "attempts": self.attempts,
            "leased_until": self.leased_until.isoformat() if self.leased_until else None,
            "max_retries": self.max_retries,
        }


class InMemoryJobQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._idempotency_index: dict[str, str] = {}

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

    def lease(self, lease_duration_seconds: float) -> JobRecord | None:
        import dataclasses
        from datetime import timedelta
        now = datetime.now(UTC)

        # Sort by creation time to act as a FIFO queue
        for record in sorted(self._jobs.values(), key=lambda r: r.created_at):
            is_eligible = record.status == JobStatus.QUEUED or (
                record.status == JobStatus.RUNNING
                and record.leased_until is not None
                and record.leased_until < now
            )
            if is_eligible:
                if record.attempts >= record.max_retries:
                    # Move to DLQ (failed status)
                    new_record = dataclasses.replace(
                        record, status=JobStatus.FAILED, leased_until=None
                    )
                    self._jobs[record.job_id] = new_record
                    continue

                leased_until = now + timedelta(seconds=lease_duration_seconds)
                new_record = dataclasses.replace(
                    record,
                    status=JobStatus.RUNNING,
                    attempts=record.attempts + 1,
                    leased_until=leased_until,
                )
                self._jobs[record.job_id] = new_record
                return new_record
        return None

    def complete(self, job_id: str, lease_token: datetime | str | None = None) -> bool:
        import dataclasses
        if job_id in self._jobs:
            record = self._jobs[job_id]
            if lease_token is not None:
                token_str = lease_token.isoformat() if isinstance(lease_token, datetime) else str(lease_token)
                current_token_str = record.leased_until.isoformat() if record.leased_until else None
                if record.status != JobStatus.RUNNING or current_token_str != token_str:
                    return False
            self._jobs[job_id] = dataclasses.replace(
                record, status=JobStatus.SUCCEEDED, leased_until=None
            )
            return True
        return False

    def fail(self, job_id: str, lease_token: datetime | str | None = None) -> bool:
        import dataclasses
        if job_id in self._jobs:
            record = self._jobs[job_id]
            if lease_token is not None:
                token_str = lease_token.isoformat() if isinstance(lease_token, datetime) else str(lease_token)
                current_token_str = record.leased_until.isoformat() if record.leased_until else None
                if record.status != JobStatus.RUNNING or current_token_str != token_str:
                    return False
            if record.attempts < record.max_retries:
                self._jobs[job_id] = dataclasses.replace(
                    record, status=JobStatus.QUEUED, leased_until=None
                )
            else:
                self._jobs[job_id] = dataclasses.replace(
                    record, status=JobStatus.FAILED, leased_until=None
                )
            return True
        return False
