"""Owned SQLite/LocalWORM lifecycle controls, never GCS bucket hold evidence.

Use existing canonical engineering producers and original governed operations.
The internal Stage1 policy fixture is not a business approval. Original retained
snapshot ACK and current governance/content authority are separate assertions.
"""
from datetime import timedelta

import pytest

from modules.opsboard.audit.application.evidence_retention import retained_document_bytes
from modules.opsboard.audit.application.sitescore_evidence import SiteScoreEvidenceUnavailable
from shared.audit.backlog import AuditBacklogUnavailable, DurableAuditBacklog
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.audit.integrity import sha256_hex, verify_audit_event_integrity
from shared.audit.persistence import GovernedEvidenceOperation, verify_retained_evidence
from shared.audit.retained_backlog import RetainedEvidenceBacklog
from shared.audit.retained_delivery import COLLECTION
from shared.jobs.queue import JobStatus, NonRetryableJobError
from tests.integration.test_audit_sitescore_evidence_resolver import case as case
from tests.integration.test_audit_sitescore_retained_transaction import command, setup


def created(case, tmp_path):
    parts = setup(case, tmp_path)
    result = command(parts)
    delivery = parts[1]
    record = delivery.evidence_store.get(result["export_id"])
    job = delivery.queue.get_by_idempotency_key(delivery._key(record.export_id))
    return parts, record, job


def governance(delivery, record, operation):
    if operation == "hold":
        return delivery.evidence_store.apply_legal_hold(record.export_id,
            context=GovernedEvidenceOperation(actor="engineering-legal-owner", role="legal",
                reason="Owned lifecycle engineering control", correlation_id="owned-hold-cid"))
    as_of = record.retain_until + timedelta(seconds=1)
    assert delivery.evidence_store.purge_expired(as_of,
        context=GovernedEvidenceOperation(actor="engineering-records-owner", role="retention_manager",
            reason="Owned expired retention control", correlation_id="owned-purge-cid", requested_at=as_of)) == [record.export_id]
    return delivery.evidence_store.get(record.export_id)


def test_shared_global_backlog_includes_retained_pending_and_never_worker_success_as_ack(case, tmp_path):
    parts, record, job = created(case, tmp_path)
    _service, delivery, actor, _selector, _refs, _scope, sink, _ports = parts
    audit_delivery = DurableAuditDelivery(delivery.engine, actor.tenant_id, delivery.queue, sink)
    intent = delivery.store.get(COLLECTION, record.export_id)
    for event_id in (record.audit_event_id, intent["snapshot_audit_event_id"]):
        audit_job = delivery.queue.get_by_idempotency_key("durable-audit:" + actor.tenant_id + ":" + event_id)
        audit_delivery.deliver(audit_job)
    shared = DurableAuditBacklog(delivery.engine, sink.sink_id)
    assert shared.inspect(actor.tenant_id).pending == shared.all_tenants().pending == 1
    delivery.queue.update_status(job.job_id, JobStatus.SUCCEEDED)
    assert shared.all_tenants().pending == 1
    receipt = delivery.deliver(job)
    assert receipt["checksum"] == intent["original_snapshot_sha256"]
    assert shared.all_tenants().pending == 0


@pytest.mark.parametrize("missing", ["record", "intent", "job", "snapshot_anchor", "table"])
def test_global_backlog_refuses_missing_side_of_committed_union(case, tmp_path, missing):
    parts, record, job = created(case, tmp_path)
    delivery, actor, sink = parts[1], parts[2], parts[6]
    intent = delivery.store.get(COLLECTION, record.export_id)
    if missing == "record":
        delivery.engine.execute("DELETE FROM durable_evidence_bundles WHERE export_id = ?", (record.export_id,))
    elif missing == "intent":
        delivery.engine.execute("DELETE FROM durable_documents WHERE collection = ? AND doc_id = ?",
            (delivery.store._collection(COLLECTION), record.export_id))
    elif missing == "job":
        delivery.engine.execute("DELETE FROM durable_jobs WHERE job_id = ?", (job.job_id,))
    elif missing == "snapshot_anchor":
        delivery.engine.execute("DELETE FROM durable_audit_events WHERE event_id = ?", (intent["snapshot_audit_event_id"],))
    else:
        delivery.engine.execute("DROP TABLE durable_evidence_bundles")
    with pytest.raises(AuditBacklogUnavailable):
        RetainedEvidenceBacklog(delivery.engine, sink.sink_id).inspect(actor.tenant_id)
    with pytest.raises(AuditBacklogUnavailable):
        DurableAuditBacklog(delivery.engine, sink.sink_id).all_tenants()


def test_delivered_then_legal_hold_keeps_original_snapshot_ack_separate_from_current_state(case, tmp_path):
    parts, original, job = created(case, tmp_path)
    delivery = parts[1]
    original_bytes = retained_document_bytes(original)
    receipt = delivery.deliver(job)
    original_sha = sha256_hex(original.to_dict())
    held = governance(delivery, original, "hold")
    assert held.legal_hold and held.governance_log[-1]["operation"] == "legal_hold"
    assert held.record_hash == original.record_hash and held.governance_hash != original.governance_hash
    assert sha256_hex(held.to_dict()) != original_sha
    assert retained_document_bytes(held) == original_bytes
    assert delivery.deliver(job) == receipt and receipt["checksum"] == original_sha
    assert delivery.inspect().pending == 0
    assert verify_audit_event_integrity(delivery.audit_log.get_event(original.audit_event_id, tenant_id=parts[2].tenant_id))
    # This proves current SQL governance plus a distinct LocalWORM snapshot,
    # not that a GCS object-level hold was configured or applied.


def test_delivered_then_purge_blocks_content_but_original_audit_and_receipt_stay_verifiable(case, tmp_path):
    parts, original, job = created(case, tmp_path)
    delivery = parts[1]
    receipt = delivery.deliver(job)
    purged = governance(delivery, original, "purge")
    assert purged.purged_at is not None and purged.bundle["purged"] is True
    verify_retained_evidence(purged)
    with pytest.raises(SiteScoreEvidenceUnavailable, match="CANONICAL_EVIDENCE_INPUT_REQUIRED:retained_schema"):
        retained_document_bytes(purged)
    assert delivery.deliver(job) == receipt
    assert receipt["checksum"] == sha256_hex(original.to_dict())
    assert delivery.inspect().pending == 0
    assert RetainedEvidenceBacklog(delivery.engine, parts[6].sink_id).inspect(parts[2].tenant_id).pending == 0
    assert verify_audit_event_integrity(delivery.audit_log.get_event(original.audit_event_id, tenant_id=parts[2].tenant_id))


@pytest.mark.parametrize("operation", ["hold", "purge"])
def test_pending_governance_change_is_not_delivered_or_written_as_original_snapshot(case, tmp_path, operation):
    parts, original, job = created(case, tmp_path)
    delivery, sink = parts[1], parts[6]
    current = governance(delivery, original, operation)
    before_objects = sorted(path.relative_to(sink._root).as_posix() for path in sink._root.rglob("*.json"))
    with pytest.raises(NonRetryableJobError, match="governance reconciliation"):
        delivery.deliver(job)
    intent = delivery.store.get(COLLECTION, original.export_id)
    assert intent["status"] == "pending" and intent["receipt"] is None
    assert intent["original_snapshot_sha256"] == sha256_hex(original.to_dict())
    assert sha256_hex(current.to_dict()) != intent["original_snapshot_sha256"]
    assert delivery.inspect().pending == 1
    assert RetainedEvidenceBacklog(delivery.engine, sink.sink_id).inspect(parts[2].tenant_id).pending == 1
    assert sorted(path.relative_to(sink._root).as_posix() for path in sink._root.rglob("*.json")) == before_objects


def test_mutating_pending_snapshot_digest_cannot_rebind_the_immutable_audit_anchor(case, tmp_path):
    parts, original, _job = created(case, tmp_path)
    delivery = parts[1]
    intent = delivery.store.get(COLLECTION, original.export_id)
    intent["original_snapshot_sha256"] = "0" * 64
    delivery.store.put(COLLECTION, original.export_id, intent)
    with pytest.raises(AuditBacklogUnavailable):
        delivery.inspect()
    with pytest.raises(AuditBacklogUnavailable):
        RetainedEvidenceBacklog(delivery.engine, parts[6].sink_id).inspect(parts[2].tenant_id)


def test_existing_audit_only_database_without_retained_table_is_read_only_empty(case):
    engine = case[0][0]
    engine.execute("DROP TABLE IF EXISTS durable_evidence_bundles")
    before = list(engine.query("SELECT * FROM durable_audit_events"))
    assert engine.query("PRAGMA table_info(durable_evidence_bundles)") == []
    assert RetainedEvidenceBacklog(engine, None).inspect(case[2].decision.tenant_id).pending == 0
    assert DurableAuditBacklog(engine, None).all_tenants().pending == 0
    assert engine.query("PRAGMA table_info(durable_evidence_bundles)") == []
    assert list(engine.query("SELECT * FROM durable_audit_events")) == before
