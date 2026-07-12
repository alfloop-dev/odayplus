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

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status.value,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
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

    def claim_next(self) -> JobRecord | None:
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
                )
                self._jobs[job_id] = updated
                return updated
        return None

    def update_status(self, job_id: str, status: JobStatus, payload: dict[str, Any] | None = None) -> None:
        if job_id in self._jobs:
            record = self._jobs[job_id]
            self._jobs[job_id] = JobRecord(
                job_type=record.job_type,
                payload=payload if payload is not None else record.payload,
                correlation_id=record.correlation_id,
                idempotency_key=record.idempotency_key,
                status=status,
                job_id=record.job_id,
                created_at=record.created_at,
            )
