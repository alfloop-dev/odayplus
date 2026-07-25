"""Tenant-scoped domain job receipts backed by the shared job queue."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.jobs.queue import JobRecord, JobRequest, JobStatus


class JobQueue(Protocol):
    def enqueue(self, request: JobRequest, *, correlation_id: str) -> tuple[JobRecord, bool]: ...

    def get(self, job_id: str) -> JobRecord | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> JobRecord | None: ...

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        payload: dict[str, Any] | None = None,
        *,
        expected_version: int | None = None,
        fence_token: int | None = None,
        error_message: str | None = None,
    ) -> None: ...


class JobReceiptIncompleteError(RuntimeError):
    """The idempotency key exists, but its receipt was not finalized."""


@dataclass(frozen=True)
class TenantScopedJobReceiptStore:
    """Store completed API receipts without leaking them across tenants."""

    queue: JobQueue
    service: str

    @property
    def is_durable(self) -> bool:
        return isinstance(self.queue, DurableJobQueue)

    def get_by_idempotency_key(
        self, tenant_id: str, idempotency_key: str | None
    ) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        record = self.queue.get_by_idempotency_key(
            self._scoped_idempotency_key(tenant_id, idempotency_key)
        )
        return self._receipt(record, tenant_id)

    def get(self, tenant_id: str, job_id: str) -> dict[str, Any] | None:
        return self._receipt(self.queue.get(job_id), tenant_id)

    def put_completed(
        self,
        *,
        tenant_id: str,
        idempotency_key: str | None,
        correlation_id: str,
        build_receipt: Callable[[str], dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        """Persist one completed receipt, replaying an existing scoped key."""

        scoped_key = (
            self._scoped_idempotency_key(tenant_id, idempotency_key) if idempotency_key else None
        )
        record, created = self.queue.enqueue(
            JobRequest(
                job_type=self._job_type,
                idempotency_key=scoped_key,
                payload={
                    "tenant_id": tenant_id,
                    "receipt_service": self.service,
                    "receipt": None,
                },
            ),
            correlation_id=correlation_id,
        )
        if not created:
            receipt = self._receipt(record, tenant_id)
            if receipt is None:
                raise JobReceiptIncompleteError(f"{self.service} receipt is not complete")
            return receipt, False

        receipt = dict(build_receipt(record.job_id))
        receipt["job_id"] = record.job_id
        envelope = {
            "tenant_id": tenant_id,
            "receipt_service": self.service,
            "receipt": receipt,
        }
        self.queue.update_status(
            record.job_id,
            JobStatus.SUCCEEDED,
            payload=envelope,
            expected_version=record.version,
            fence_token=record.fence_token,
        )
        return receipt, True

    @property
    def _job_type(self) -> str:
        return f"{self.service}.receipt"

    def _scoped_idempotency_key(self, tenant_id: str, key: str) -> str:
        return f"receipt:v1:{self.service}:{tenant_id}:{key}"

    def _receipt(self, record: JobRecord | None, tenant_id: str) -> dict[str, Any] | None:
        if record is None or record.job_type != self._job_type:
            return None
        if str(record.payload.get("tenant_id") or "") != tenant_id:
            return None
        receipt = record.payload.get("receipt")
        if not isinstance(receipt, dict):
            raise JobReceiptIncompleteError(f"{self.service} receipt is not complete")
        return dict(receipt)


__all__ = [
    "JobQueue",
    "JobReceiptIncompleteError",
    "TenantScopedJobReceiptStore",
]
