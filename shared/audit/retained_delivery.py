"""Committed retained-evidence delivery; pending SQL is never a WORM ACK.

External writes occur only in deliver(), after the producer transaction has
committed. A lost external response/SQL commit is recovered by verifying the
same deterministic immutable object, never by pretending it rolled back.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.opsboard.audit.evidence_store import DurableEvidenceBundleStore
from shared.audit.backlog import AuditBacklog
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.audit.events import AuditEvent
from shared.audit.integrity import sha256_hex, verify_audit_event_integrity
from shared.audit.persistence import RetainedEvidence, verify_retained_evidence
from shared.audit.worm import AuditWormReceipt
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.jobs.queue import JobRequest, NonRetryableJobError

COLLECTION = "audit.retained_evidence_delivery"
JOB_TYPE = "retained-evidence-delivery"


def _active(engine: Any) -> bool:
    return bool(getattr(getattr(engine, "_local", None), "connection", None) is not None
                or (getattr(engine, "_tx_depth", 0) > 0
                    and getattr(getattr(engine, "_conn", None), "in_transaction", False)))


def _snapshot_anchor(engine: Any, tenant_id: str, intent: dict, record: RetainedEvidence):
    anchor_id = intent.get("snapshot_audit_event_id")
    row = engine.query_one("SELECT * FROM durable_audit_events WHERE event_id = ?", (anchor_id,))
    anchor = DurableAuditLog._row_to_event(row) if row is not None else None
    if (anchor is None or not verify_audit_event_integrity(anchor)
            or anchor.event_type != "audit.evidence.retained_snapshot.v1"
            or anchor.action != "retain" or anchor.outcome != "snapshot_recorded"
            or anchor.actor != record.requested_by or anchor.correlation_id != record.correlation_id
            or anchor.resource != "audit-evidence/" + record.export_id
            or anchor.metadata.get("tenant_id") != tenant_id
            or anchor.metadata.get("export_id") != record.export_id
            or anchor.metadata.get("record_hash") != record.record_hash
            or anchor.metadata.get("bundle_checksum") != record.bundle_checksum
            or anchor.metadata.get("audit_event_id") != record.audit_event_id
            or anchor.metadata.get("subject_id") != record.requested_by
            or anchor.event_hash != intent.get("snapshot_audit_event_hash")):
        raise NonRetryableJobError("Retained original snapshot lacks its immutable audit binding")
    for key in ("original_snapshot_sha256", "original_governance_hash"):
        value = anchor.metadata.get(key)
        if (not isinstance(value, str) or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value) or intent.get(key) != value):
            raise NonRetryableJobError("Retained original snapshot hash differs from its audit")
    return anchor


def committed_retained_facts(engine: Any, tenant_id: str, sink_id: str, export_id: str):
    row = engine.query_one("SELECT * FROM durable_evidence_bundles WHERE export_id = ?", (export_id,))
    record = DurableEvidenceBundleStore._row_to_record(row) if row is not None else None
    documents = TenantScopedDocumentStore(SqliteDocumentStore(engine), tenant_id)
    intent = documents.get(COLLECTION, export_id)
    queue = DurableJobQueue(engine)
    job = queue.get_by_idempotency_key("retained-evidence:" + tenant_id + ":" + export_id)
    if record is None or not isinstance(intent, dict) or job is None:
        raise NonRetryableJobError("Retained evidence lacks its committed intent/job")
    verify_retained_evidence(record)
    event_row = engine.query_one("SELECT * FROM durable_audit_events WHERE event_id = ?", (record.audit_event_id,))
    event = DurableAuditLog._row_to_event(event_row) if event_row is not None else None
    anchor = _snapshot_anchor(engine, tenant_id, intent, record)
    boundary = record.bundle.get("identity_boundary", {})
    expected = {"tenant_id": tenant_id, "subject_id": record.requested_by, "export_id": record.export_id,
                "record_hash": record.record_hash, "bundle_checksum": record.bundle_checksum,
                "audit_event_id": record.audit_event_id,
                "audit_event_hash": event.event_hash if event else None, "sink_id": sink_id,
                "original_snapshot_sha256": anchor.metadata["original_snapshot_sha256"],
                "original_governance_hash": anchor.metadata["original_governance_hash"],
                "snapshot_audit_event_id": anchor.event_id, "snapshot_audit_event_hash": anchor.event_hash}
    if (event is None or not verify_audit_event_integrity(event)
            or event.metadata.get("tenant_id") != tenant_id or event.worm_sink_id != sink_id
            or anchor.worm_sink_id != sink_id or record.worm_sink_id != sink_id
            or (record.purged_at is None and (boundary.get("tenant_id") != tenant_id
                                             or boundary.get("subject_id") != record.requested_by))
            or event.actor != record.requested_by or event.resource != "audit-evidence/" + export_id
            or event.correlation_id != record.correlation_id
            or event.metadata.get("bundle_checksum") != record.bundle_checksum
            or event.metadata.get("retained_delivery_job_type") != JOB_TYPE
            or anchor.metadata.get("audit_event_hash") != event.event_hash
            or any(intent.get(key) != value for key, value in expected.items())
            or job.job_type != JOB_TYPE or job.payload != expected
            or job.correlation_id != record.correlation_id):
        raise NonRetryableJobError("Retained delivery differs from immutable committed facts")
    # A purge tombstone is not readable content. Its verified current governance
    # and original immutable audit/receipt can still be reconciled independently.
    if record.purged_at is None:
        if record.bundle.get("schema") == "sitescore.general-decision-audit-retained.v1":
            from modules.opsboard.audit.application.evidence_general_retention import (
                general_retained_document_bytes,
            )
            general_retained_document_bytes(record)
        else:
            # Compatibility for the historical unmounted Stage1 engineering
            # lane. Ordinary general exports never import its approval DTOs.
            from modules.opsboard.audit.application.evidence_retention import (
                retained_document_bytes,
            )
            retained_document_bytes(record)
    return record, intent, job


def verified_retained_receipt(sink_id: str, value: Any, record: RetainedEvidence,
                              intent: dict) -> dict[str, Any]:
    receipt = value.to_dict() if isinstance(value, AuditWormReceipt) else value
    if not isinstance(receipt, dict):
        raise NonRetryableJobError("Retained WORM receipt is unavailable")
    uri = receipt.get("object_uri")
    try:
        written = datetime.fromisoformat(receipt["written_at"])
    except (KeyError, ValueError, TypeError) as exc:
        raise NonRetryableJobError("Retained WORM receipt has an invalid timestamp") from exc
    if (receipt.get("sink_id") != sink_id or receipt.get("record_type") != "retained-evidence"
            or receipt.get("record_id") != record.export_id
            or receipt.get("checksum") != intent["original_snapshot_sha256"]
            or not isinstance(uri, str) or not uri.startswith(("gs://", "file://"))
            or written.utcoffset() is None):
        raise NonRetryableJobError("Retained WORM receipt differs from immutable committed bytes")
    return dict(receipt)


class RetainedEvidenceDelivery:
    def __init__(self, *, engine: Any, tenant_id: str, queue: DurableJobQueue,
                 evidence_store: DurableEvidenceBundleStore, audit_log: DurableAuditLog) -> None:
        if (not isinstance(tenant_id, str) or not tenant_id.strip()
                or not isinstance(queue, DurableJobQueue) or queue._engine is not engine
                or not isinstance(evidence_store, DurableEvidenceBundleStore)
                or evidence_store._engine is not engine or evidence_store._worm_sink is None
                or not isinstance(audit_log, DurableAuditLog) or audit_log._engine is not engine
                or audit_log._worm_sink is not evidence_store._worm_sink):
            raise ValueError("Retained delivery requires exact same-engine durable queue/store/audit/sink")
        self.engine, self.queue, self.evidence_store, self.audit_log = engine, queue, evidence_store, audit_log
        self.sink = evidence_store._worm_sink
        self.store = TenantScopedDocumentStore(SqliteDocumentStore(engine), tenant_id)

    @property
    def sink_id(self) -> str:
        return self.sink.sink_id

    def _key(self, export_id: str) -> str:
        return "retained-evidence:" + self.store.tenant_id + ":" + export_id

    def stage(self, record: RetainedEvidence) -> None:
        if not _active(self.engine):
            raise RuntimeError("Retained intent requires the active business transaction")
        verify_retained_evidence(record)
        boundary = record.bundle.get("identity_boundary", {})
        event = self.audit_log.get_event(record.audit_event_id, tenant_id=self.store.tenant_id)
        if (boundary.get("tenant_id") != self.store.tenant_id
                or boundary.get("subject_id") != record.requested_by
                or record.worm_sink_id != self.sink_id or event is None
                or event.actor != record.requested_by or event.correlation_id != record.correlation_id
                or event.resource != "audit-evidence/" + record.export_id
                or event.metadata.get("bundle_checksum") != record.bundle_checksum):
            raise ValueError("Retained intent differs from its committed identity/audit facts")
        # Anchor the original external snapshot separately from mutable current
        # hold/purge state. The anchor is real same-transaction hash-stamped audit,
        # not a fabricated policy approval or an external acknowledgement.
        snapshot_sha256 = sha256_hex(record.to_dict())
        anchor = AuditEvent(event_id=str(uuid4()), event_type="audit.evidence.retained_snapshot.v1",
            actor=record.requested_by, action="retain", resource="audit-evidence/" + record.export_id,
            outcome="snapshot_recorded", correlation_id=record.correlation_id,
            metadata={"tenant_id": self.store.tenant_id, "subject_id": record.requested_by,
                "export_id": record.export_id, "record_hash": record.record_hash,
                "bundle_checksum": record.bundle_checksum, "audit_event_id": event.event_id,
                "audit_event_hash": event.event_hash, "original_snapshot_sha256": snapshot_sha256,
                "original_governance_hash": record.governance_hash,
                "retained_delivery_job_type": JOB_TYPE})
        audit_delivery = DurableAuditDelivery(self.engine, self.store.tenant_id, self.queue, self.sink)
        with self.audit_log.transactional_worm_sink(audit_delivery):
            anchor = self.audit_log.record(anchor)
        payload = {"tenant_id": self.store.tenant_id, "subject_id": record.requested_by,
                   "export_id": record.export_id, "record_hash": record.record_hash,
                   "bundle_checksum": record.bundle_checksum,
                   "audit_event_id": event.event_id, "audit_event_hash": event.event_hash,
                   "sink_id": self.sink_id, "original_snapshot_sha256": snapshot_sha256,
                   "original_governance_hash": record.governance_hash,
                   "snapshot_audit_event_id": anchor.event_id, "snapshot_audit_event_hash": anchor.event_hash}
        intent = {**payload, "status": "pending", "receipt": None}
        if not self.store.put_if_absent(COLLECTION, record.export_id, intent):
            raise ValueError("Retained delivery intent is append-only")
        job, created = self.queue.enqueue(JobRequest(job_type=JOB_TYPE, payload=payload,
                                                    idempotency_key=self._key(record.export_id)),
                                          correlation_id=record.correlation_id)
        if not created or job.payload != payload or job.job_type != JOB_TYPE:
            raise ValueError("Retained delivery job does not bind the committed record")

    def _facts(self, export_id: str):
        return committed_retained_facts(self.engine, self.store.tenant_id, self.sink_id, export_id)

    def _receipt(self, value: Any, record: RetainedEvidence, intent: dict) -> dict[str, Any]:
        return verified_retained_receipt(self.sink_id, value, record, intent)

    def deliver(self, supplied: Any) -> dict[str, Any]:
        # Reject a direct call while its producer transaction is still open.
        # This preserves SQL-before-external-write even for accidental callers.
        if _active(self.engine):
            raise RuntimeError("Retained delivery cannot run inside the producer transaction")
        with self.engine.transaction():
            if getattr(self.engine, "dialect", "") == "postgresql":
                self.engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                                      ("retained-evidence-delivery:" + str(supplied.job_id),))
            elif not self.engine._conn.in_transaction:
                self.engine.execute("BEGIN IMMEDIATE")
            export_id = supplied.payload.get("export_id")
            if not isinstance(export_id, str) or not export_id:
                raise NonRetryableJobError("Retained job has no canonical export reference")
            record, intent, job = self._facts(export_id)
            if (supplied.job_id != job.job_id or supplied.job_type != job.job_type
                    or supplied.payload != job.payload or supplied.idempotency_key != job.idempotency_key
                    or supplied.correlation_id != job.correlation_id):
                raise NonRetryableJobError("Supplied job differs from the persisted retained job")
            if intent.get("status") == "delivered":
                return self._receipt(intent.get("receipt"), record, intent)
            if intent.get("status") != "pending" or intent.get("receipt") is not None:
                raise NonRetryableJobError("Unknown retained delivery state")
            # Pending governance changes need explicit reconciliation, never a
            # fresh write of changed bytes presented as the original ACK.
            if (record.purged_at is not None or record.governance_log or record.legal_hold
                    or record.governance_hash != intent["original_governance_hash"]
                    or sha256_hex(record.to_dict()) != intent["original_snapshot_sha256"]):
                raise NonRetryableJobError("Pending retained snapshot requires governance reconciliation")
            lookup = getattr(self.sink, "lookup_retained_evidence", None)
            existing = lookup(record) if callable(lookup) else None
            receipt = self._receipt(existing or self.sink.write_retained_evidence(record), record, intent)
            intent.update(status="delivered", receipt=receipt, delivered_at=datetime.now(UTC).isoformat())
            self.store.put(COLLECTION, export_id, intent)
            return receipt

    def inspect(self, *, now: datetime | None = None) -> AuditBacklog:
        """Include the immutable audit side even if other delivery rows vanish."""
        from shared.audit.retained_backlog import RetainedEvidenceBacklog

        return RetainedEvidenceBacklog(self.engine, self.sink_id).inspect(self.store.tenant_id, now=now)
