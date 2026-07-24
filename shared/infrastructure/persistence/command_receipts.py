"""Tenant-scoped durable command receipts and idempotency reservations."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from shared.api.idempotency import IdempotencyConflictError, request_fingerprint
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.job_receipts import JobQueue
from shared.jobs.queue import JobRequest, JobStatus


class CommandReceiptIncompleteError(RuntimeError):
    """A command key is reserved but has no replayable success response."""


class CommandReceiptPersistenceError(RuntimeError):
    """The command receipt backend could not reserve or persist a receipt."""


@dataclass(frozen=True)
class CommandReceiptOutcome:
    value: dict[str, Any]
    replayed: bool
    receipt_id: str


@dataclass(frozen=True)
class TenantScopedCommandReceiptStore:
    """Run a command once and persist its replay response by tenant and scope.

    A reservation is written before the domain operation. If the process dies
    after the domain write but before the receipt is finalized, subsequent
    calls fail closed instead of applying the command a second time.
    """

    queue: JobQueue
    service: str
    replay_wait_timeout_seconds: float = 10.0
    replay_poll_interval_seconds: float = 0.01

    @property
    def is_durable(self) -> bool:
        return isinstance(self.queue, DurableJobQueue)

    def run(
        self,
        *,
        tenant_id: str,
        idempotency_key: str | None,
        scope: str,
        payload: Any,
        correlation_id: str,
        operation: Callable[[str], dict[str, Any]],
    ) -> CommandReceiptOutcome:
        fingerprint = request_fingerprint(payload)
        envelope = {
            "tenant_id": tenant_id,
            "receipt_service": self.service,
            "command_scope": scope,
            "request_fingerprint": fingerprint,
            "response": None,
        }
        scoped_key = (
            self._scoped_idempotency_key(tenant_id, scope, idempotency_key)
            if idempotency_key
            else None
        )
        try:
            record, created = self.queue.enqueue(
                JobRequest(
                    job_type=self._job_type,
                    idempotency_key=scoped_key,
                    payload=envelope,
                ),
                correlation_id=correlation_id,
            )
        except Exception as exc:
            raise CommandReceiptPersistenceError(
                f"{self.service} command reservation is unavailable"
            ) from exc

        if not created:
            return self._replay(
                record=record,
                tenant_id=tenant_id,
                scope=scope,
                fingerprint=fingerprint,
                idempotency_key=idempotency_key or "",
            )

        try:
            response = dict(operation(record.job_id))
        except BaseException:
            self._mark_failed(record.job_id, record.version, record.fence_token, envelope)
            raise

        completed_envelope = {**envelope, "response": response}
        try:
            self.queue.update_status(
                record.job_id,
                JobStatus.SUCCEEDED,
                payload=completed_envelope,
                expected_version=record.version,
                fence_token=record.fence_token,
            )
        except Exception as exc:
            raise CommandReceiptPersistenceError(
                f"{self.service} command receipt could not be finalized"
            ) from exc
        return CommandReceiptOutcome(
            value=response,
            replayed=False,
            receipt_id=record.job_id,
        )

    def get(self, *, tenant_id: str, receipt_id: str) -> dict[str, Any] | None:
        try:
            record = self.queue.get(receipt_id)
        except Exception as exc:
            raise CommandReceiptPersistenceError(
                f"{self.service} command receipt lookup is unavailable"
            ) from exc
        if record is None or record.job_type != self._job_type:
            return None
        payload = record.payload
        if (
            str(payload.get("tenant_id") or "") != tenant_id
            or str(payload.get("receipt_service") or "") != self.service
        ):
            return None
        response = payload.get("response")
        if not isinstance(response, dict):
            raise CommandReceiptIncompleteError(
                f"{self.service} command receipt is not complete"
            )
        return dict(response)

    @property
    def _job_type(self) -> str:
        return f"{self.service}.command-receipt"

    def _scoped_idempotency_key(
        self,
        tenant_id: str,
        scope: str,
        key: str,
    ) -> str:
        return f"command:v1:{self.service}:{tenant_id}:{scope}:{key}"

    def _replay(
        self,
        *,
        record: Any,
        tenant_id: str,
        scope: str,
        fingerprint: str,
        idempotency_key: str,
    ) -> CommandReceiptOutcome:
        payload = record.payload
        if (
            record.job_type != self._job_type
            or str(payload.get("tenant_id") or "") != tenant_id
            or str(payload.get("receipt_service") or "") != self.service
            or str(payload.get("command_scope") or "") != scope
        ):
            raise CommandReceiptIncompleteError(
                f"{self.service} command reservation does not match its scope"
            )
        if str(payload.get("request_fingerprint") or "") != fingerprint:
            raise IdempotencyConflictError(idempotency_key, scope)
        response = payload.get("response")
        if not isinstance(response, dict):
            record = self._await_completed_record(record)
            response = record.payload.get("response")
        if not isinstance(response, dict):
            raise CommandReceiptIncompleteError(
                f"{self.service} command receipt is not complete"
            )
        return CommandReceiptOutcome(
            value=dict(response),
            replayed=True,
            receipt_id=record.job_id,
        )

    def _await_completed_record(self, record: Any) -> Any:
        """Wait for an in-flight owner request, while stale receipts fail closed."""

        deadline = time.monotonic() + max(self.replay_wait_timeout_seconds, 0.0)
        current = record
        while True:
            response = current.payload.get("response")
            if isinstance(response, dict):
                return current
            if current.status in {
                JobStatus.FAILED,
                JobStatus.CANCELLED,
                JobStatus.SUCCEEDED,
            }:
                raise CommandReceiptIncompleteError(
                    f"{self.service} command receipt is not complete"
                )
            if time.monotonic() >= deadline:
                raise CommandReceiptIncompleteError(
                    f"{self.service} command receipt is still in progress"
                )
            time.sleep(max(self.replay_poll_interval_seconds, 0.001))
            try:
                refreshed = self.queue.get(current.job_id)
            except Exception as exc:
                raise CommandReceiptPersistenceError(
                    f"{self.service} command receipt lookup is unavailable"
                ) from exc
            if refreshed is None:
                raise CommandReceiptPersistenceError(
                    f"{self.service} command receipt disappeared during replay"
                )
            current = refreshed

    def _mark_failed(
        self,
        receipt_id: str,
        version: int,
        fence_token: int,
        envelope: dict[str, Any],
    ) -> None:
        try:
            self.queue.update_status(
                receipt_id,
                JobStatus.FAILED,
                payload=envelope,
                expected_version=version,
                fence_token=fence_token,
                error_message="command execution did not produce a durable receipt",
            )
        except Exception:
            # The original domain error remains authoritative. The reservation,
            # if it survived, still fails closed on a retry.
            return


__all__ = [
    "CommandReceiptIncompleteError",
    "CommandReceiptOutcome",
    "CommandReceiptPersistenceError",
    "TenantScopedCommandReceiptStore",
]
