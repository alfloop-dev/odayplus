from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any
from uuid import uuid4

NON_EXECUTABLE_RECEIPT_JOB_TYPE_SUFFIXES = (
    ".receipt",
    ".command-receipt",
)


def is_non_executable_receipt_job_type(job_type: str) -> bool:
    """Return whether a durable queue row is evidence rather than worker work."""

    return job_type.endswith(NON_EXECUTABLE_RECEIPT_JOB_TYPE_SUFFIXES)


from shared.governance.vocabularies import JobDeliveryState, JobStatus


class NonRetryableJobError(RuntimeError):
    """Raised when a job should fail permanently without further retries."""

    pass


JOB_FEATURE_FLAG_MAP: dict[str, str] = {
    "priceops.execute": "high_risk.priceops.execute",
    "priceops_job": "high_risk.priceops.execute",
    "adlift.approve": "high_risk.adlift.approve",
    "adlift_job": "high_risk.adlift.approve",
    "netplan.approve": "high_risk.netplan.approve",
    "netplan_job": "high_risk.netplan.approve",
    "model.publish": "high_risk.model.publish",
    "model_publish_job": "high_risk.model.publish",
    "sitescore.approve": "high_risk.sitescore.approve",
    "sitescore_job": "high_risk.sitescore.approve",
}


def resolve_job_feature_flag_key(job_type: str, payload: dict[str, Any] | None = None) -> str | None:
    """Resolve the feature flag key for a given job type or payload."""

    if payload and "feature_flag" in payload:
        return str(payload["feature_flag"])
    return JOB_FEATURE_FLAG_MAP.get(job_type)


def check_job_feature_flag(
    job_type: str, payload: dict[str, Any] | None = None, *, flags: Any = None, on: Any = None
) -> None:
    """Enforce feature flag check for job execution per FR-SHARED-004.

    If the mapped feature flag is disabled, raises NonRetryableJobError.
    """

    flag_key = resolve_job_feature_flag_key(job_type, payload)
    if flag_key:
        from shared.auth.feature_flags import default_registry

        reg = flags or default_registry()
        if not reg.is_enabled(flag_key, on=on):
            raise NonRetryableJobError(
                f"Feature flag {flag_key!r} is disabled (kill-switch engaged). "
                f"Job execution for {job_type!r} refused."
            )



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
    delivery_state: JobDeliveryState | None = None
    job_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attempts: int = 0
    leased_until: datetime | None = None
    max_retries: int = 3
    fence_token: int = 0
    version: int = 1
    locked_by: str | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status.value,
            "delivery_state": self.delivery_state.value if self.delivery_state else None,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
            "attempts": self.attempts,
            "leased_until": self.leased_until.isoformat() if self.leased_until else None,
            "max_retries": self.max_retries,
            "fence_token": self.fence_token,
            "version": self.version,
            "locked_by": self.locked_by,
            "heartbeat_at": self.heartbeat_at.isoformat() if self.heartbeat_at else None,
            "lease_expires_at": self.lease_expires_at.isoformat()
            if self.lease_expires_at
            else None,
            "error_message": self.error_message,
        }


class InMemoryJobQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._idempotency_index: dict[str, str] = {}
        self._reservation_lock = RLock()

    def count_active_jobs(self, *, tenant_id: str | None = None) -> int:
        return sum(
            1
            for job in self._jobs.values()
            if job.status in (JobStatus.QUEUED, JobStatus.RUNNING)
            and not is_non_executable_receipt_job_type(job.job_type)
            and (tenant_id is None or str(job.payload.get("tenant_id") or "") == tenant_id)
        )

    def enqueue(self, request: JobRequest, *, correlation_id: str) -> tuple[JobRecord, bool]:
        with self._reservation_lock:
            # Enforce kill-switch / feature flag check at enqueue time
            check_job_feature_flag(request.job_type, request.payload)

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
        with self._reservation_lock:
            return self._jobs.get(job_id)

    def lease(self, lease_duration_seconds: float) -> JobRecord | None:
        import dataclasses
        from datetime import timedelta

        now = datetime.now(UTC)

        # Sort by creation time to act as a FIFO queue
        for record in sorted(self._jobs.values(), key=lambda r: r.created_at):
            if is_non_executable_receipt_job_type(record.job_type):
                continue
            is_eligible = record.status == JobStatus.QUEUED or (
                record.status == JobStatus.RUNNING
                and record.leased_until is not None
                and record.leased_until < now
            )
            if is_eligible:
                try:
                    check_job_feature_flag(record.job_type, record.payload)
                except NonRetryableJobError as exc:
                    new_record = dataclasses.replace(
                        record,
                        status=JobStatus.FAILED,
                        leased_until=None,
                        error_message=str(exc),
                    )
                    self._jobs[record.job_id] = new_record
                    continue

                if record.attempts >= record.max_retries:

                    # Move to DLQ (failed status)
                    new_record = dataclasses.replace(
                        record,
                        status=JobStatus.FAILED,
                        delivery_state=JobDeliveryState.DEAD_LETTER,
                        leased_until=None,
                    )
                    self._jobs[record.job_id] = new_record
                    continue

                leased_until = now + timedelta(seconds=lease_duration_seconds)
                new_record = dataclasses.replace(
                    record,
                    status=JobStatus.RUNNING,
                    delivery_state=JobDeliveryState.RETRYING if record.attempts > 0 else None,
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
                token_str = (
                    lease_token.isoformat()
                    if isinstance(lease_token, datetime)
                    else str(lease_token)
                )
                current_token_str = record.leased_until.isoformat() if record.leased_until else None
                if record.status != JobStatus.RUNNING or current_token_str != token_str:
                    return False
            self._jobs[job_id] = dataclasses.replace(
                record, status=JobStatus.SUCCEEDED, delivery_state=None, leased_until=None
            )
            return True
        return False

    def fail(self, job_id: str, lease_token: datetime | str | None = None) -> bool:
        import dataclasses

        if job_id in self._jobs:
            record = self._jobs[job_id]
            if lease_token is not None:
                token_str = (
                    lease_token.isoformat()
                    if isinstance(lease_token, datetime)
                    else str(lease_token)
                )
                current_token_str = record.leased_until.isoformat() if record.leased_until else None
                if record.status != JobStatus.RUNNING or current_token_str != token_str:
                    return False
            if record.attempts < record.max_retries:
                self._jobs[job_id] = dataclasses.replace(
                    record,
                    status=JobStatus.QUEUED,
                    delivery_state=JobDeliveryState.RETRYING,
                    leased_until=None,
                )
            else:
                self._jobs[job_id] = dataclasses.replace(
                    record,
                    status=JobStatus.FAILED,
                    delivery_state=JobDeliveryState.DEAD_LETTER,
                    leased_until=None,
                )
            return True
        return False

    def get_by_idempotency_key(self, idempotency_key: str) -> JobRecord | None:
        job_id = self._idempotency_index.get(idempotency_key)
        return self.get(job_id) if job_id else None

    def replay(
        self, job_id: str, *, expected_version: int | None = None, fence_token: int | None = None
    ) -> JobRecord:
        with self._reservation_lock:
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
                delivery_state=None,
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
        with self._reservation_lock:
            now = datetime.now(UTC)
            for job_id, record in self._jobs.items():
                is_eligible = record.status == JobStatus.QUEUED or (
                    record.status == JobStatus.RUNNING
                    and record.lease_expires_at is not None
                    and record.lease_expires_at < now
                )
                if is_eligible and not is_non_executable_receipt_job_type(record.job_type):
                    updated = JobRecord(
                        job_type=record.job_type,
                        payload=record.payload,
                        correlation_id=record.correlation_id,
                        idempotency_key=record.idempotency_key,
                        status=JobStatus.RUNNING,
                        delivery_state=JobDeliveryState.RETRYING if record.attempts > 0 else None,
                        job_id=record.job_id,
                        created_at=record.created_at,
                        fence_token=record.fence_token + 1,
                        version=record.version + 1,
                        locked_by=worker_id,
                        heartbeat_at=now,
                        lease_expires_at=now + timedelta(seconds=45),
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
        delivery_state: JobDeliveryState | None = None,
        expected_version: int | None = None,
        fence_token: int | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._reservation_lock:
            if job_id not in self._jobs:
                raise ValueError(f"Job {job_id} not found")
            record = self._jobs[job_id]
            if expected_version is not None and record.version != expected_version:
                raise ValueError(
                    f"Job version mismatch: expected {expected_version}, got {record.version}"
                )
            if fence_token is not None and record.fence_token != fence_token:
                raise ValueError(
                    f"Job fence token mismatch: expected {fence_token}, got {record.fence_token}"
                )

            resolved_delivery = delivery_state if delivery_state is not None else record.delivery_state
            if status == JobStatus.SUCCEEDED:
                resolved_delivery = None

            self._jobs[job_id] = JobRecord(
                job_type=record.job_type,
                payload=payload if payload is not None else record.payload,
                correlation_id=record.correlation_id,
                idempotency_key=record.idempotency_key,
                status=status,
                delivery_state=resolved_delivery,
                job_id=record.job_id,
                created_at=record.created_at,
                fence_token=record.fence_token,
                version=record.version + 1,
                locked_by=record.locked_by if status == JobStatus.RUNNING else None,
                heartbeat_at=record.heartbeat_at if status == JobStatus.RUNNING else None,
                lease_expires_at=record.lease_expires_at if status == JobStatus.RUNNING else None,
                attempts=record.attempts,
                error_message=error_message or record.error_message,
            )

    def heartbeat(self, job_id: str, expected_version: int, fence_token: int) -> int:
        with self._reservation_lock:
            if job_id not in self._jobs:
                raise ValueError(f"Job {job_id} not found")
            record = self._jobs[job_id]
            if (
                record.status != JobStatus.RUNNING
                or record.version != expected_version
                or record.fence_token != fence_token
            ):
                raise ValueError("Fence/version mismatch")
            new_version = expected_version + 1
            self._jobs[job_id] = JobRecord(
                job_type=record.job_type,
                payload=record.payload,
                correlation_id=record.correlation_id,
                idempotency_key=record.idempotency_key,
                status=record.status,
                delivery_state=record.delivery_state,
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


__all__ = [
    "InMemoryJobQueue",
    "JobDeliveryState",
    "JobRecord",
    "JobRequest",
    "JobStatus",
    "NonRetryableJobError",
    "check_job_feature_flag",
    "is_non_executable_receipt_job_type",
    "resolve_job_feature_flag_key",
]
