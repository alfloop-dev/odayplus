"""Deliver a committed canonical audit through the actual durable worker."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog, canonical_audit_payload
from shared.audit.integrity import AuditIntegrityError
from shared.jobs.queue import JobRequest, NonRetryableJobError

JOB_TYPE = "canonical-audit-delivery"


@dataclass(frozen=True)
class _CanonicalWormEvent:
    row: dict[str, Any]

    @property
    def event_id(self):
        return self.row["audit_event_id"]

    @property
    def sequence(self):
        return self.row["sequence_no"]

    def to_dict(self):
        return deepcopy(canonical_audit_payload(self.row))


class CanonicalAuditDelivery:
    def __init__(self, writer: CanonicalPostgresAuditLog, queue: Any, sink: Any):
        self.writer, self.queue, self.sink = writer, queue, sink
        self.engine = writer.engine
        if getattr(queue, "_engine", None) is not self.engine or sink is None:
            raise ValueError("Canonical delivery requires the same PostgreSQL queue and configured sink")

    def require_business_transaction(self):
        if getattr(self.engine._local, "connection", None) is None:
            raise RuntimeError("Canonical audit delivery must share the business transaction")

    def enqueue(self, row):
        self.require_business_transaction()
        payload = {
            "tenant_id": row["tenant_id"], "audit_event_id": row["audit_event_id"],
            "event_sha256": row["event_sha256"], "sink_id": self.sink.sink_id,
        }
        job, _ = self.queue.enqueue(
            JobRequest(
                job_type=JOB_TYPE, payload=payload,
                idempotency_key="canonical-audit:" + row["tenant_id"] + ":" + row["audit_event_id"],
            ), correlation_id=row["correlation_id"],
        )
        if job.payload != payload or job.job_type != JOB_TYPE:
            raise AuditIntegrityError("Existing delivery job has different canonical facts")

    def deliver(self, job):
        persisted = self.queue.get(job.job_id)
        if (
            persisted is None or persisted.job_type != JOB_TYPE
            or persisted.payload != job.payload or job.payload.get("sink_id") != self.sink.sink_id
        ):
            raise NonRetryableJobError("Canonical delivery job or configured sink is invalid")
        tenant_id, event_id = job.payload.get("tenant_id"), job.payload.get("audit_event_id")
        with self.engine.transaction():
            row = self.writer.get_event(tenant_id, event_id)
            if row is None or row["event_sha256"] != job.payload.get("event_sha256"):
                raise NonRetryableJobError("Canonical delivery does not bind to a committed audit")
            if persisted.idempotency_key != "canonical-audit:" + tenant_id + ":" + event_id:
                raise NonRetryableJobError("Canonical delivery job identity is invalid")
            self.engine.query_one(
                "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                ("canonical-audit-delivery:" + event_id,),
            )
            event = _CanonicalWormEvent(row)
            lookup = getattr(self.sink, "lookup_audit_event", None)
            receipt = lookup(event) if callable(lookup) else None
            receipt = receipt or self.sink.write_audit_event(event)
            return self.writer.attach_worm_receipt(
                tenant_id, event_id, expected_event_sha256=row["event_sha256"],
                sink_id=self.sink.sink_id, receipt=receipt,
            )
