"""Platform-global audit delivery using the existing durable audit job type.

There is no invented tenant. A committed SQL audit and exact global intent
must bind the event hash, sink and job before any external WORM write occurs.
An outbox receipt is pending delivery, never an external acknowledgement.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from shared.audit import AuditEvent
from shared.audit.integrity import sha256_hex, verify_audit_event_integrity
from shared.audit.worm import AuditWormReceipt
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.jobs.queue import JobRequest, NonRetryableJobError

SCOPE = "platform-feature-flags"
COLLECTION = "audit.platform_feature_flags.delivery"
JOB_TYPE = "durable-audit-delivery"


class PlatformFlagAuditDelivery:
    delivery_job_type = JOB_TYPE

    def __init__(self, engine: Any, queue: Any, sink: Any) -> None:
        if getattr(queue, "_engine", None) is not engine or sink is None:
            raise ValueError("Platform audit delivery requires its same-engine queue and sink")
        self.engine, self.queue, self.sink = engine, queue, sink
        self.store = SqliteDocumentStore(engine)

    @property
    def sink_id(self) -> str:
        return self.sink.sink_id

    @staticmethod
    def _key(event_id: str) -> str:
        return "platform-feature-flag-audit:" + event_id

    def write_audit_event(self, event: Any) -> AuditWormReceipt:
        active = (
            getattr(getattr(self.engine, "_local", None), "connection", None) is not None
            or (getattr(self.engine, "_tx_depth", 0) > 0
                and getattr(getattr(self.engine, "_conn", None), "in_transaction", False))
        )
        if not active or not isinstance(event, AuditEvent) or event.metadata.get("scope") != SCOPE:
            raise RuntimeError("Platform audit intent requires its actual business transaction")
        payload = {"scope": SCOPE, "audit_event_id": event.event_id,
                   "event_hash": event.event_hash, "sink_id": self.sink_id}
        self.store.put(COLLECTION, event.event_id, {
            "event": event, "sink_id": self.sink_id, "status": "pending",
        })
        job, _ = self.queue.enqueue(JobRequest(
            job_type=JOB_TYPE, payload=payload, idempotency_key=self._key(event.event_id),
        ), correlation_id=event.correlation_id)
        if job.payload != payload or job.job_type != JOB_TYPE:
            raise ValueError("Platform audit delivery reservation differs from committed facts")
        return AuditWormReceipt(
            sink_id=self.sink_id, object_uri="outbox://platform-feature-flags/" + event.event_id,
            record_type="audit-events", record_id=event.event_id,
            checksum=sha256_hex(event.to_dict()),
        )

    def deliver(self, job: Any) -> dict[str, Any]:
        event_id = str(job.payload.get("audit_event_id") or "")
        persisted = self.queue.get(job.job_id)
        if (
            not event_id or job.payload.get("scope") != SCOPE
            or job.payload.get("sink_id") != self.sink_id
            or persisted is None or persisted.job_type != JOB_TYPE
            or job.job_type != JOB_TYPE or persisted.payload != job.payload
            or persisted.idempotency_key != self._key(event_id)
        ):
            raise NonRetryableJobError("Platform audit job identity or sink is invalid")
        with self.engine.transaction():
            if getattr(self.engine, "dialect", "") == "postgresql":
                self.engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                                      (self._key(event_id),))
            elif not self.engine._conn.in_transaction:
                self.engine.execute("BEGIN IMMEDIATE")
            row = self.store.get(COLLECTION, event_id)
            event = row.get("event") if isinstance(row, dict) else None
            committed = self.engine.query_one(
                "SELECT event_hash FROM durable_audit_events WHERE event_id = ?", (event_id,),
            )
            if (
                not isinstance(event, AuditEvent) or row.get("sink_id") != self.sink_id
                or event.metadata.get("scope") != SCOPE
                or not event.resource.startswith("feature_flag:")
                or not verify_audit_event_integrity(event)
                or committed is None or committed["event_hash"] != event.event_hash
                or job.payload.get("event_hash") != event.event_hash
            ):
                raise NonRetryableJobError("Platform audit differs from committed audit facts")
            if row.get("status") == "delivered":
                receipt = row.get("receipt", {})
                if (receipt.get("record_id") != event_id or receipt.get("sink_id") != self.sink_id
                        or receipt.get("checksum") != sha256_hex(event.to_dict())
                        or receipt.get("record_type") != "audit-events"
                        or not receipt.get("object_uri")
                        or receipt["object_uri"].startswith("outbox://")):
                    raise NonRetryableJobError("Stored platform WORM receipt is invalid")
                return dict(receipt)
            lookup = getattr(self.sink, "lookup_audit_event", None)
            receipt = lookup(event) if callable(lookup) else None
            receipt = receipt or self.sink.write_audit_event(event)
            if (receipt.record_id != event_id or receipt.sink_id != self.sink_id
                    or receipt.checksum != sha256_hex(event.to_dict())
                    or receipt.record_type != "audit-events"
                    or not receipt.object_uri or receipt.object_uri.startswith("outbox://")):
                raise NonRetryableJobError("Platform WORM receipt differs from committed audit")
            row.update(status="delivered", receipt=receipt.to_dict(),
                       delivered_at=datetime.now(UTC).isoformat())
            self.store.put(COLLECTION, event_id, row)
            return receipt.to_dict()


@dataclass(frozen=True)
class PlatformFlagAuditBacklog:
    pending: int
    oldest_seconds: float
    metadata_digests: frozenset[str]
    payload_digests: frozenset[str]


def inspect_platform_flag_backlog(engine: Any, sink_id: str | None, *, now=None) -> PlatformFlagAuditBacklog:
    """Read and cross-check all global event/intent/job facts, without delivery.

    No invented tenant and no special-case blind skip of claimed global jobs.
    Only exact validated metadata/payloads may be separated from tenant rows.
    """
    from shared.infrastructure.persistence.audit_log import DurableAuditLog
    from shared.infrastructure.persistence.job_queue import DurableJobQueue

    store = SqliteDocumentStore(engine)
    queue = DurableJobQueue(engine)
    expression = ("metadata_json ->> 'scope'" if getattr(engine, "dialect", "") == "postgresql"
                  else "json_extract(metadata_json, '$.scope')")
    marker = ("metadata_json ->> 'audit_delivery_job_type'" if getattr(engine, "dialect", "") == "postgresql"
              else "json_extract(metadata_json, '$.audit_delivery_job_type')")
    # Expressions are complete fixed dialect fragments; scope/type are bound.
    marked = engine.query("SELECT event_id FROM durable_audit_events WHERE " + expression + " = ? AND " + marker + " = ?",  # nosec B608
                          (SCOPE, JOB_TYPE))
    event_ids = {str(row["event_id"]) for row in marked}
    documents = engine.query("SELECT doc_id FROM durable_documents WHERE collection = ?", (COLLECTION,))
    for row in documents:
        event_ids.add(str(row["doc_id"]))
    selected_jobs = {}
    jobs = engine.query("SELECT job_id, payload_json, idempotency_key FROM durable_jobs WHERE job_type = ?", (JOB_TYPE,))
    for row in jobs:
        payload = json.loads(row["payload_json"])
        key = row["idempotency_key"]
        if payload.get("scope") != SCOPE and not str(key or "").startswith("platform-feature-flag-audit:"):
            continue
        event_id = payload.get("audit_event_id")
        if not isinstance(event_id, str) or not event_id or event_id in selected_jobs:
            raise ValueError("Invalid or duplicated platform audit delivery job")
        selected_jobs[event_id] = str(row["job_id"])
        event_ids.add(event_id)
    ages, metadata_digests, payload_digests = [], set(), set()
    for event_id in sorted(event_ids):
        row = engine.query_one("SELECT * FROM durable_audit_events WHERE event_id = ?", (event_id,))
        intent = store.get(COLLECTION, event_id)
        job = queue.get_by_idempotency_key(PlatformFlagAuditDelivery._key(event_id))
        if row is None or not isinstance(intent, dict) or job is None or not sink_id:
            raise ValueError("Committed platform audit lacks its delivery event/intent/job/sink")
        event = DurableAuditLog._row_to_event(row)
        intent_event = intent.get("event")
        expected = {"scope": SCOPE, "audit_event_id": event_id,
                    "event_hash": event.event_hash, "sink_id": sink_id}
        if (not verify_audit_event_integrity(event)
                or event.metadata.get("scope") != SCOPE
                or event.metadata.get("audit_delivery_job_type") != JOB_TYPE
                or "tenant_id" in event.metadata
                or not event.resource.startswith("feature_flag:")
                or event.worm_sink_id != sink_id or intent.get("sink_id") != sink_id
                or not isinstance(intent_event, AuditEvent) or intent_event.to_dict() != event.to_dict()
                or job.job_type != JOB_TYPE or job.payload != expected
                or job.correlation_id != event.correlation_id
                or selected_jobs.get(event_id) != job.job_id):
            raise ValueError("Platform audit delivery facts do not match the committed event")
        metadata_digests.add(sha256_hex(event.metadata))
        payload_digests.add(sha256_hex(job.payload))
        if intent.get("status") == "delivered":
            receipt = intent.get("receipt", {})
            uri = str(receipt.get("object_uri") or "")
            if (receipt.get("record_id") != event_id or receipt.get("sink_id") != sink_id
                    or receipt.get("record_type") != "audit-events"
                    or receipt.get("checksum") != sha256_hex(event.to_dict())
                    or not uri or uri.startswith("outbox:")):
                raise ValueError("Delivered platform audit lacks its verified external receipt")
        elif intent.get("status") == "pending":
            if job.created_at.tzinfo is None:
                raise ValueError("Platform audit delivery clock is invalid")
            ages.append(job.created_at)
        else:
            raise ValueError("Unknown platform audit delivery status")
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None or any(created > clock for created in ages):
        raise ValueError("Platform audit delivery clock is invalid")
    return PlatformFlagAuditBacklog(
        len(ages), max(((clock - created).total_seconds() for created in ages), default=0.0),
        frozenset(metadata_digests), frozenset(payload_digests),
    )
