"""Same-transaction delivery intents for durable AuditEvent records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shared.audit.integrity import sha256_hex, verify_audit_event_integrity
from shared.audit.worm import AuditWormReceipt
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.jobs.queue import JobRequest, NonRetryableJobError

COLLECTION = "audit.durable_delivery"
JOB_TYPE = "durable-audit-delivery"


class DurableAuditDelivery:
    def __init__(self, engine: Any, tenant_id: str, queue: Any, sink: Any) -> None:
        if getattr(queue, "_engine", None) is not engine or sink is None:
            raise ValueError("Audit delivery requires a same-engine queue and configured sink")
        self.engine, self.queue, self.sink = engine, queue, sink
        self.store = TenantScopedDocumentStore(SqliteDocumentStore(engine), tenant_id)

    @property
    def sink_id(self) -> str:
        return self.sink.sink_id

    def write_audit_event(self, event: Any) -> AuditWormReceipt:
        active = (
            getattr(getattr(self.engine, "_local", None), "connection", None) is not None
            or (getattr(self.engine, "_tx_depth", 0) > 0
                and getattr(getattr(self.engine, "_conn", None), "in_transaction", False))
        )
        if not active:
            raise RuntimeError("Audit delivery intent must share the business transaction")
        tenant_id = self.store.tenant_id
        if event.metadata.get("tenant_id") != tenant_id:
            raise ValueError("Audit delivery tenant differs from the verified event tenant")
        payload = {
            "tenant_id": tenant_id, "audit_event_id": event.event_id,
            "event_hash": event.event_hash, "sink_id": self.sink_id,
        }
        self.store.put(COLLECTION, event.event_id, {
            "event": event, "sink_id": self.sink_id, "status": "pending",
        })
        job, _ = self.queue.enqueue(JobRequest(
            job_type=JOB_TYPE, payload=payload,
            idempotency_key=self._key(event.event_id),
        ), correlation_id=event.correlation_id)
        if job.payload != payload or job.job_type != JOB_TYPE:
            raise ValueError("Audit delivery reservation differs from the committed event")
        return AuditWormReceipt(
            sink_id=self.sink_id, object_uri="outbox://durable-audit/" + event.event_id,
            record_type="audit-events", record_id=event.event_id,
            checksum=sha256_hex(event.to_dict()),
        )

    def _key(self, event_id: str) -> str:
        return "durable-audit:" + self.store.tenant_id + ":" + event_id

    def deliver(self, job: Any) -> dict[str, Any]:
        """Write only a persisted, integrity-checked event; recover lost responses."""
        event_id = str(job.payload.get("audit_event_id") or "")
        persisted = self.queue.get(job.job_id)
        if (
            not event_id or persisted is None or persisted.job_type != JOB_TYPE
            or persisted.payload != job.payload or persisted.idempotency_key != self._key(event_id)
            or job.payload.get("tenant_id") != self.store.tenant_id
            or job.payload.get("sink_id") != self.sink_id
        ):
            raise NonRetryableJobError("Audit delivery job does not bind to the configured tenant and sink")
        with self.engine.transaction():
            if getattr(self.engine, "dialect", "") == "postgresql":
                self.engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                                      ("durable-audit-delivery:" + event_id,))
            elif not self.engine._conn.in_transaction:
                self.engine.execute("BEGIN IMMEDIATE")
            row = self.store.get(COLLECTION, event_id)
            event = row.get("event") if row else None
            committed = self.engine.query_one(
                "SELECT event_hash FROM durable_audit_events WHERE event_id = ?", (event_id,),
            )
            if (
                event is None or row.get("sink_id") != self.sink_id
                or event.metadata.get("tenant_id") != self.store.tenant_id
                or not verify_audit_event_integrity(event)
                or committed is None or committed["event_hash"] != event.event_hash
                or job.payload.get("event_hash") != event.event_hash
            ):
                raise NonRetryableJobError("Audit delivery differs from the committed audit facts")
            if row.get("status") == "delivered":
                receipt = row.get("receipt", {})
                if (receipt.get("record_id") != event_id or receipt.get("sink_id") != self.sink_id
                        or receipt.get("checksum") != sha256_hex(event.to_dict())
                        or not receipt.get("object_uri")):
                    raise NonRetryableJobError("Stored WORM receipt differs from the committed audit facts")
                return dict(receipt)
            lookup = getattr(self.sink, "lookup_audit_event", None)
            receipt = lookup(event) if callable(lookup) else None
            receipt = receipt or self.sink.write_audit_event(event)
            if (receipt.record_id != event_id or receipt.sink_id != self.sink_id
                    or receipt.checksum != sha256_hex(event.to_dict())):
                raise NonRetryableJobError("WORM receipt differs from the committed audit facts")
            row.update(status="delivered", receipt=receipt.to_dict(),
                       delivered_at=datetime.now(UTC).isoformat())
            self.store.put(COLLECTION, event_id, row)
            return receipt.to_dict()
