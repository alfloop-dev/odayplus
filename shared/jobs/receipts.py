"""Itemized job receipts, status derivation, and replay fencing (ODP-FR-SHARED-001).

Supports multi-item batch jobs reaching JobStatus.PARTIAL, per-member idempotency,
durable receipts, scoped retry (FAILED_ONLY), and deterministic aggregate derivation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from shared.governance.vocabularies import JobStatus


class ItemStatus(StrEnum):
    """Lifecycle or terminal outcome for a specific member item within a batch job."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PENDING = "PENDING"


@dataclass(frozen=True)
class ItemError:
    """Machine-readable and diagnostic details of an item failure."""

    code: str
    message: str
    retryable: bool
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details is not None:
            result["details"] = self.details
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ItemError:
        return cls(
            code=str(data.get("code", "")),
            message=str(data.get("message", "")),
            retryable=bool(data.get("retryable", False)),
            details=data.get("details"),
        )


@dataclass(frozen=True)
class ItemReceipt:
    """Item-level execution receipt within a batch job."""

    item_id: str
    item_status: str  # ItemStatus value or string
    attempt: int  # 0 for unstarted PENDING or pre-execution CANCELLED; >=1 for executed
    result_ref: str | None = None
    error: ItemError | None = None
    idempotency_key: str | None = None
    last_attempt_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_status": str(self.item_status),
            "attempt": self.attempt,
            "result_ref": self.result_ref,
            "error": self.error.to_dict() if self.error else None,
            "idempotency_key": self.idempotency_key,
            "last_attempt_at": self.last_attempt_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ItemReceipt:
        raw_error = data.get("error")
        error_obj: ItemError | None = None
        if isinstance(raw_error, dict):
            error_obj = ItemError.from_dict(raw_error)
        elif isinstance(raw_error, ItemError):
            error_obj = raw_error

        return cls(
            item_id=str(data.get("item_id", "")),
            item_status=str(data.get("item_status", ItemStatus.PENDING.value)),
            attempt=int(data.get("attempt", 0)),
            result_ref=data.get("result_ref"),
            error=error_obj,
            idempotency_key=data.get("idempotency_key"),
            last_attempt_at=data.get("last_attempt_at"),
        )


@dataclass(frozen=True)
class JobSummary:
    """Summary counts of items within a multi-item batch job."""

    total_count: int
    succeeded_count: int
    failed_count: int
    cancelled_count: int
    pending_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_count": self.total_count,
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
            "cancelled_count": self.cancelled_count,
            "pending_count": self.pending_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobSummary:
        return cls(
            total_count=int(data.get("total_count", 0)),
            succeeded_count=int(data.get("succeeded_count", 0)),
            failed_count=int(data.get("failed_count", 0)),
            cancelled_count=int(data.get("cancelled_count", 0)),
            pending_count=int(data.get("pending_count", 0)),
        )


@dataclass(frozen=True)
class DurableJobReceipt:
    """Durable receipt envelope for multi-item batch jobs."""

    job_id: str
    job_type: str
    tenant_id: str
    status: str
    summary: JobSummary
    items: tuple[ItemReceipt, ...]
    created_at: str
    delivery_state: str | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "tenant_id": self.tenant_id,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "delivery_state": self.delivery_state,
            "summary": self.summary.to_dict(),
            "items": [it.to_dict() for it in self.items],
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DurableJobReceipt:
        raw_summary = data.get("summary", {})
        summary_obj = (
            raw_summary
            if isinstance(raw_summary, JobSummary)
            else JobSummary.from_dict(raw_summary)
        )
        raw_items = data.get("items", [])
        items_tuple = tuple(
            it if isinstance(it, ItemReceipt) else ItemReceipt.from_dict(it)
            for it in raw_items
        )
        return cls(
            job_id=str(data.get("job_id", "")),
            job_type=str(data.get("job_type", "")),
            tenant_id=str(data.get("tenant_id", "")),
            status=str(data.get("status", JobStatus.QUEUED.value)),
            delivery_state=data.get("delivery_state"),
            correlation_id=data.get("correlation_id"),
            idempotency_key=data.get("idempotency_key"),
            summary=summary_obj,
            items=items_tuple,
            created_at=str(data.get("created_at", "")),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
        )


def derive_batch_status_and_summary(
    items: list[ItemReceipt | dict[str, Any]] | tuple[ItemReceipt | dict[str, Any], ...],
) -> tuple[JobStatus, JobSummary]:
    """Derive JobStatus and JobSummary deterministically from item receipts.

    Implements state_transition_rules.batch_multi_item_precedence_rules:
    1. Empty batch: If total_count == 0 => JobStatus.SUCCEEDED
    2. Non-finalization rule: If pending_count > 0 => JobStatus.RUNNING
    3. Cancellation precedence: If cancelled_count > 0 => JobStatus.CANCELLED
    4. All Succeeded: If succeeded_count == total_count and total_count > 0 => JobStatus.SUCCEEDED
    5. All Failed: If failed_count == total_count and total_count > 0 => JobStatus.FAILED
    6. Mixed Partial Outcome: If succeeded_count > 0 and failed_count > 0 => JobStatus.PARTIAL
    """
    parsed_items: list[ItemReceipt] = [
        it if isinstance(it, ItemReceipt) else ItemReceipt.from_dict(it)
        for it in items
    ]
    succeeded = sum(1 for it in parsed_items if it.item_status == ItemStatus.SUCCEEDED.value)
    failed = sum(1 for it in parsed_items if it.item_status == ItemStatus.FAILED.value)
    cancelled = sum(1 for it in parsed_items if it.item_status == ItemStatus.CANCELLED.value)
    pending = sum(1 for it in parsed_items if it.item_status == ItemStatus.PENDING.value)
    total = len(parsed_items)

    summary = JobSummary(
        total_count=total,
        succeeded_count=succeeded,
        failed_count=failed,
        cancelled_count=cancelled,
        pending_count=pending,
    )

    if total == 0:
        return JobStatus.SUCCEEDED, summary

    if pending > 0:
        return JobStatus.RUNNING, summary

    if cancelled > 0:
        return JobStatus.CANCELLED, summary

    if succeeded == total and failed == 0:
        return JobStatus.SUCCEEDED, summary

    if failed == total and succeeded == 0:
        return JobStatus.FAILED, summary

    if succeeded > 0 and failed > 0:
        return JobStatus.PARTIAL, summary

    if succeeded > 0 and failed == 0:
        return JobStatus.SUCCEEDED, summary

    if failed > 0 and succeeded == 0:
        return JobStatus.FAILED, summary

    return JobStatus.PARTIAL, summary


def apply_item_result(
    current_items: list[ItemReceipt],
    new_result: ItemReceipt,
) -> tuple[list[ItemReceipt], bool]:
    """Apply an item execution result with (job_id, item_id, attempt) fencing.

    Returns (updated_items, applied_boolean).
    If the result is duplicate, stale, or violates monotonicity / cancellation
    invariants, it is discarded with zero state change and returns False.
    """
    item_map: dict[str, ItemReceipt] = {it.item_id: it for it in current_items}
    existing = item_map.get(new_result.item_id)

    if existing is None:
        updated = list(current_items) + [new_result]
        return updated, True

    # Stale result rule (b): item is already in SUCCEEDED status (terminal for item)
    if existing.item_status == ItemStatus.SUCCEEDED.value:
        return current_items, False

    # Stale result on pre-execution CANCELLED item (attempt == 0)
    if existing.item_status == ItemStatus.CANCELLED.value and existing.attempt == 0:
        return current_items, False

    # A result may only carry the item forward: it either completes the attempt
    # currently recorded as in flight, or reports a later one. An attempt whose
    # outcome is already recorded rejects a second result for that same attempt,
    # which is what makes a duplicate delivery a no-op.
    if new_result.attempt < existing.attempt:
        return current_items, False
    if (
        new_result.attempt == existing.attempt
        and existing.item_status != ItemStatus.PENDING.value
    ):
        return current_items, False

    # Apply new result
    item_map[new_result.item_id] = new_result
    # Preserve original order
    updated = [item_map.get(it.item_id, it) for it in current_items]
    if new_result.item_id not in [it.item_id for it in current_items]:
        updated.append(new_result)
    return updated, True


__all__ = [
    "DurableJobReceipt",
    "ItemError",
    "ItemReceipt",
    "ItemStatus",
    "JobSummary",
    "apply_item_result",
    "derive_batch_status_and_summary",
]
