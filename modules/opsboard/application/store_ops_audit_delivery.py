"""Transactional delivery intent, with external WORM writes after commit."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shared.audit.integrity import sha256_hex, verify_audit_event_integrity
from shared.audit.worm import AuditWormReceipt
from shared.jobs.queue import JobRequest

COLLECTION = "opsboard.store_ops.audit_delivery"
JOB_TYPE = "store-ops-audit-delivery"


class StoreOpsAuditDelivery:
    def __init__(self, store: Any, sink: Any, job_queue: Any = None) -> None:
        self.store = store
        self.engine = store.engine
        self.sink = sink
        self.job_queue = job_queue

    @property
    def sink_id(self) -> str:
        return self.sink.sink_id

    def write_audit_event(self, event: Any) -> AuditWormReceipt:
        if getattr(self.job_queue, "_engine", None) is not self.engine:
            raise RuntimeError("Store Ops audit delivery requires a same-transaction job queue")
        tenant_id = self.store.tenant_id
        if event.metadata.get("tenant_id") != tenant_id:
            raise ValueError("Store Ops audit event tenant does not match its delivery queue")
        self.store.put(
            COLLECTION,
            event.event_id,
            {
                "event": event,
                "sink_id": self.sink_id,
                "status": "pending",
            },
        )
        self.job_queue.enqueue(
            JobRequest(
                job_type=JOB_TYPE,
                idempotency_key="store-ops-audit:" + tenant_id + ":" + event.event_id,
                payload={
                    "tenant_id": tenant_id,
                    "audit_event_id": event.event_id,
                    "sink_id": self.sink_id,
                },
            ),
            correlation_id=event.correlation_id,
        )
        return AuditWormReceipt(
            sink_id=self.sink_id,
            object_uri="outbox://store-ops-audit/" + event.event_id,
            record_type="audit-events",
            record_id=event.event_id,
            checksum=sha256_hex(event.to_dict()),
        )

    def deliver_pending(self, *, event_id: str | None = None) -> dict[str, Any]:
        """Recover pending deliveries from an authorized request or durable job.

        The delivery row is locked while attempting the immutable write. A
        failed write remains pending. A write interrupted before its receipt
        is saved can use the sink's verified readback instead of overwriting.
        """
        delivered = 0
        candidates = self.store.list_all(COLLECTION) if event_id is None else [self.store.get(COLLECTION, event_id)]
        for candidate in candidates:
            if candidate is None:
                continue
            if candidate.get("status") != "pending":
                continue
            event = candidate["event"]
            with self.engine.transaction():
                self.engine.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    ("operator.store_ops.audit_delivery:" + event.event_id,),
                )
                row = self.store.get(COLLECTION, event.event_id)
                if row is None or row.get("status") != "pending":
                    continue
                if row.get("sink_id") != self.sink_id:
                    continue
                try:
                    event = row["event"]
                    if not verify_audit_event_integrity(event):
                        raise ValueError("queued audit event integrity is invalid")
                    committed = self.engine.query_one("SELECT event_hash FROM durable_audit_events WHERE event_id = ?", (event.event_id,))
                    if committed is None or committed["event_hash"] != event.event_hash:
                        raise ValueError("queued audit does not match the committed audit row")
                    lookup = getattr(self.sink, "lookup_audit_event", None)
                    receipt = lookup(event) if callable(lookup) else None
                    receipt = receipt or self.sink.write_audit_event(event)
                    if (
                        receipt.record_id != event.event_id
                        or receipt.checksum != sha256_hex(event.to_dict())
                        or receipt.sink_id != self.sink_id
                    ):
                        raise ValueError("WORM receipt does not match the committed audit event")
                except Exception as exc:
                    row["last_error_type"] = type(exc).__name__
                    self.store.put(COLLECTION, event.event_id, row)
                    continue
                row.update(
                    {
                        "status": "delivered",
                        "receipt": receipt.to_dict(),
                        "delivered_at": datetime.now(UTC).isoformat(),
                    }
                )
                self.store.put(COLLECTION, event.event_id, row)
                delivered += 1
        pending = sum(row.get("status") == "pending" for row in self.store.list_all(COLLECTION))
        return {
            "status": "pending" if pending else "delivered",
            "pending": pending,
            "deliveredNow": delivered,
        }
