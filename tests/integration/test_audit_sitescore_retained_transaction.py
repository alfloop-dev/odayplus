"""Owned SQLite transaction/recovery engineering controls, not export admission.

The policy ports are explicitly engineering snapshots from the Stage1 suite.
They do not create a production policy, source licence or human approval.
"""
from dataclasses import replace

import pytest

from modules.opsboard.audit.application.evidence_admission import (
    EvidenceAdmissionOwners,
    EvidenceAdmissionReferences,
    EvidencePolicyScope,
    SiteScoreStage1Admission,
)
from modules.opsboard.audit.application.evidence_retention import (
    SiteScoreRetainedCommit,
    retained_document_bytes,
)
from modules.opsboard.audit.application.sitescore_evidence import SiteScoreEvidenceUnavailable
from modules.opsboard.audit.evidence_store import DurableEvidenceBundleStore
from shared.api.idempotency import IdempotencyConflictError
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.audit.integrity import sha256_hex
from shared.audit.retained_delivery import COLLECTION, JOB_TYPE, RetainedEvidenceDelivery
from shared.audit.worm import AuditWormReceipt, AuditWormSinkError, LocalAppendOnlyWormSink
from shared.infrastructure.persistence.command_receipts import TenantScopedCommandReceiptStore
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.jobs.queue import JobStatus, NonRetryableJobError
from tests.integration.test_audit_sitescore_admission_projection import (
    EngineeringReadPorts,
    current_source,
)
from tests.integration.test_audit_sitescore_evidence_resolver import case as case
from tests.integration.test_audit_sitescore_evidence_resolver import (
    principal,
    resolver,
    rows,
    selected,
)


def setup(case, tmp_path):
    parts, approvals, outcome = case
    engine, _scores, _decisions, audit, _workflow, _service = parts
    actor = principal()
    ports = EngineeringReadPorts(engine, current_source(case), actor)
    refs = EvidenceAdmissionReferences(**ports.refs)
    sink = LocalAppendOnlyWormSink(tmp_path / "engineering-worm-only")
    audit._worm_sink = sink
    store = DurableEvidenceBundleStore(engine, worm_sink=sink)
    queue = DurableJobQueue(engine)
    # Preserve the canonical read owner's existing same-engine WORM requirement.
    _service._audit_delivery_factory = lambda tenant: DurableAuditDelivery(engine, tenant, queue, sink)
    delivery = RetainedEvidenceDelivery(engine=engine, tenant_id=actor.tenant_id, queue=queue,
                                        evidence_store=store, audit_log=audit)
    admission = SiteScoreStage1Admission(resolver(parts, approvals), EvidenceAdmissionOwners(ports, ports, ports, ports))
    receipts = TenantScopedCommandReceiptStore(queue, "sitescore-evidence-retention", transaction_factory=engine.transaction)
    service = SiteScoreRetainedCommit(admission, delivery, receipts)
    source = current_source(case)
    scope = EvidencePolicyScope("json", "engineering-only-internal-fixture", source.events[0].occurred_at,
                                source.events[-1].occurred_at)
    return service, delivery, actor, selected(outcome), refs, scope, sink, ports


def command(parts, *, key="owned-engineering-key", **changes):
    service, _delivery, actor, selector, refs, scope, _sink, _ports = parts
    return service.commit(changes.get("actor", actor), changes.get("selector", selector), refs,
                          policy_scope=changes.get("scope", scope), idempotency_key=key,
                          correlation_id="owned-stage2-cid")


def all_rows(engine):
    return {**rows(engine), "durable_evidence_bundles": [tuple(row) for row in engine.query(
        "SELECT * FROM durable_evidence_bundles ORDER BY export_id")]}


def test_one_commit_contains_record_export_audit_both_intents_jobs_and_canonical_receipt(case, tmp_path):
    parts = setup(case, tmp_path)
    service, delivery, actor, _selector, _refs, _scope, sink, _ports = parts
    result = command(parts)
    record = delivery.evidence_store.get(result["export_id"])
    event = delivery.audit_log.get_event(record.audit_event_id, tenant_id=actor.tenant_id)
    jobs = service.engine.query("SELECT job_type FROM durable_jobs")
    assert {row["job_type"] for row in jobs} >= {JOB_TYPE, "durable-audit-delivery", "sitescore-evidence-retention.command-receipt"}
    assert event.resource == "audit-evidence/" + record.export_id
    assert event.actor == actor.subject_id and event.outcome == "PENDING_DELIVERY"
    assert record.requested_by == actor.subject_id
    assert record.bundle["identity_boundary"] == {"tenant_id": actor.tenant_id, "subject_id": actor.subject_id}
    assert record.bundle["artifact_sha256"] == sha256_hex(record.bundle["document"])
    assert retained_document_bytes(record) and b'"reason"' not in retained_document_bytes(record)
    assert result["delivery_status"] == "pending" and result["external_acknowledged"] is False
    assert result["export_admitted"] is False
    assert delivery.store.get(COLLECTION, record.export_id)["receipt"] is None
    assert delivery.inspect().pending == 1
    assert list(sink._root.rglob("*.json")) == []  # No external write before SQL commit.
    before = all_rows(service.engine)
    replay = command(parts)
    assert replay["replayed"] is True and replay["export_id"] == result["export_id"]
    assert replay["receipt_id"] == result["receipt_id"] and all_rows(service.engine) == before


@pytest.mark.parametrize("phase", ["record_insert", "intent_insert", "receipt_finalize"])
def test_any_sql_failure_rolls_back_record_audit_both_outboxes_and_receipt(case, tmp_path, monkeypatch, phase):
    parts = setup(case, tmp_path)
    service, delivery, _actor, _selector, _refs, _scope, sink, _ports = parts
    before = all_rows(service.engine)
    if phase == "record_insert":
        original = delivery.evidence_store._insert
        def fail(record):
            original(record)
            raise RuntimeError("engineering failure after insert")
        monkeypatch.setattr(delivery.evidence_store, "_insert", fail)
    elif phase == "intent_insert":
        original = delivery.stage
        def fail(record):
            original(record)
            raise RuntimeError("engineering failure after intent/job")
        monkeypatch.setattr(delivery, "stage", fail)
    else:
        original = delivery.queue.update_status
        def fail(job_id, status, *args, **kwargs):
            if status == JobStatus.SUCCEEDED:
                raise RuntimeError("engineering failure before canonical receipt")
            return original(job_id, status, *args, **kwargs)
        monkeypatch.setattr(delivery.queue, "update_status", fail)
    with pytest.raises(RuntimeError):
        command(parts)
    assert all_rows(service.engine) == before
    assert list(sink._root.rglob("*.json")) == []


def test_replay_rechecks_current_policy_records_and_payload_without_new_writes(case, tmp_path):
    parts = setup(case, tmp_path)
    service, _delivery, _actor, _selector, _refs, scope, _sink, ports = parts
    command(parts)
    before = all_rows(service.engine)
    with pytest.raises(IdempotencyConflictError):
        command(parts, selector=replace(parts[3], sections=("audit",)))
    assert all_rows(service.engine) == before
    ports.documents.put(ports.collection, "purpose", None)
    before = all_rows(service.engine)
    with pytest.raises(SiteScoreEvidenceUnavailable):
        command(parts)
    assert all_rows(service.engine) == before


@pytest.mark.parametrize("wrong", ["tenant", "subject", "scope"])
def test_foreign_actor_or_revoked_object_scope_cannot_replay_or_create(case, tmp_path, wrong):
    parts = setup(case, tmp_path)
    service, _delivery, actor, _selector, _refs, _scope, _sink, _ports = parts
    command(parts)
    if wrong == "tenant":
        candidate = replace(actor, scope=replace(actor.scope, tenant_id="other-tenant"))
    elif wrong == "subject":
        candidate = replace(actor, subject_id="other-subject")
    else:
        candidate = replace(actor, scope=replace(actor.scope, store_ids=frozenset({"other-store"})))
    before = all_rows(service.engine)
    with pytest.raises((SiteScoreEvidenceUnavailable, PermissionError)):
        command(parts, actor=candidate)
    assert all_rows(service.engine) == before


def test_external_worm_receipt_and_audit_receipt_are_separate_actual_deliveries(case, tmp_path):
    parts = setup(case, tmp_path)
    _service, delivery, actor, _selector, _refs, _scope, sink, _ports = parts
    result = command(parts)
    record = delivery.evidence_store.get(result["export_id"])
    job = delivery.queue.get_by_idempotency_key(delivery._key(record.export_id))
    receipt = delivery.deliver(job)
    assert receipt["record_type"] == "retained-evidence" and receipt["checksum"] == sha256_hex(record.to_dict())
    assert receipt["object_uri"].startswith("file://") and delivery.inspect().pending == 0
    assert len(list(sink._root.rglob("*.json"))) == 1
    audit_job = delivery.queue.get_by_idempotency_key("durable-audit:" + actor.tenant_id + ":" + record.audit_event_id)
    assert audit_job is not None
    audit_receipt = DurableAuditDelivery(delivery.engine, actor.tenant_id, delivery.queue, sink).deliver(audit_job)
    assert audit_receipt["record_type"] == "audit-events"
    assert len(list(sink._root.rglob("*.json"))) == 2
    assert delivery.deliver(job) == receipt
    assert len(list(sink._root.rglob("*.json"))) == 2


@pytest.mark.parametrize("interruption", ["response_lost", "sql_ack_lost"])
def test_recovery_verifies_existing_immutable_bytes_after_external_write(case, tmp_path, monkeypatch, interruption):
    parts = setup(case, tmp_path)
    _service, delivery, _actor, _selector, _refs, _scope, sink, _ports = parts
    result = command(parts)
    record = delivery.evidence_store.get(result["export_id"])
    job = delivery.queue.get_by_idempotency_key(delivery._key(record.export_id))
    if interruption == "response_lost":
        original = sink.write_retained_evidence
        def fail(record):
            original(record)
            raise AuditWormSinkError("engineering response lost after immutable write")
        monkeypatch.setattr(sink, "write_retained_evidence", fail)
    else:
        original = delivery.store.put
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("engineering SQL acknowledgement lost")
        monkeypatch.setattr(delivery.store, "put", fail)
    with pytest.raises((RuntimeError, AuditWormSinkError)):
        delivery.deliver(job)
    assert len(list(sink._root.rglob("*.json"))) == 1
    assert delivery.store.get(COLLECTION, record.export_id)["status"] == "pending"
    assert delivery.inspect().pending == 1
    monkeypatch.undo()
    receipt = delivery.deliver(job)
    assert receipt["checksum"] == sha256_hex(record.to_dict())
    assert len(list(sink._root.rglob("*.json"))) == 1
    assert delivery.inspect().pending == 0


@pytest.mark.parametrize("bad", ["outbox", "checksum", "record_type", "sink_id"])
def test_pending_or_wrong_external_receipt_never_acknowledges(case, tmp_path, monkeypatch, bad):
    parts = setup(case, tmp_path)
    _service, delivery, _actor, _selector, _refs, _scope, sink, _ports = parts
    result = command(parts)
    record = delivery.evidence_store.get(result["export_id"])
    job = delivery.queue.get_by_idempotency_key(delivery._key(record.export_id))
    receipt = AuditWormReceipt(sink.sink_id, "file:///engineering-object", "retained-evidence", record.export_id,
                               sha256_hex(record.to_dict()))
    changes = {"outbox": {"object_uri": "outbox://not-an-ack"}, "checksum": {"checksum": "0" * 64},
               "record_type": {"record_type": "audit-events"}, "sink_id": {"sink_id": "wrong-sink"}}[bad]
    monkeypatch.setattr(sink, "write_retained_evidence", lambda _record: replace(receipt, **changes))
    with pytest.raises(NonRetryableJobError):
        delivery.deliver(job)
    assert delivery.store.get(COLLECTION, record.export_id)["status"] == "pending"
    assert delivery.inspect().pending == 1


def test_worker_job_success_is_not_external_ack_and_missing_job_is_unavailable(case, tmp_path):
    parts = setup(case, tmp_path)
    _service, delivery, _actor, _selector, _refs, _scope, _sink, _ports = parts
    result = command(parts)
    record = delivery.evidence_store.get(result["export_id"])
    job = delivery.queue.get_by_idempotency_key(delivery._key(record.export_id))
    delivery.queue.update_status(job.job_id, JobStatus.SUCCEEDED)
    assert delivery.inspect().pending == 1
    delivery.engine.execute("DELETE FROM durable_jobs WHERE job_id = ?", (job.job_id,))
    from shared.audit.backlog import AuditBacklogUnavailable
    with pytest.raises(AuditBacklogUnavailable):
        delivery.inspect()


def test_delivery_inside_producer_transaction_and_foreign_job_are_rejected(case, tmp_path):
    parts = setup(case, tmp_path)
    _service, delivery, _actor, _selector, _refs, _scope, sink, _ports = parts
    result = command(parts)
    record = delivery.evidence_store.get(result["export_id"])
    job = delivery.queue.get_by_idempotency_key(delivery._key(record.export_id))
    with delivery.engine.transaction():
        delivery.engine.execute("UPDATE durable_sequences SET counter = counter WHERE name = ?", ("nonexistent",))
        with pytest.raises(RuntimeError, match="producer transaction"):
            delivery.deliver(job)
    with pytest.raises(NonRetryableJobError):
        delivery.deliver(replace(job, payload={**job.payload, "tenant_id": "other-tenant"}))
    assert list(sink._root.rglob("*.json")) == []


def test_missing_production_read_owners_cannot_use_engineering_commit(case, tmp_path):
    parts = setup(case, tmp_path)
    service, _delivery, _actor, _selector, _refs, _scope, _sink, _ports = parts
    service.admission.owners = EvidenceAdmissionOwners()
    before = all_rows(service.engine)
    with pytest.raises(SiteScoreEvidenceUnavailable, match="same_engine_admission_read_owners"):
        command(parts)
    assert all_rows(service.engine) == before


@pytest.mark.parametrize("format", ["csv", "pdf"])
def test_unimplemented_artifact_formats_cannot_relabel_json_bytes(case, tmp_path, format):
    parts = setup(case, tmp_path)
    before = all_rows(parts[0].engine)
    with pytest.raises(SiteScoreEvidenceUnavailable, match="unimplemented_artifact_format"):
        command(parts, scope=replace(parts[5], format=format))
    assert all_rows(parts[0].engine) == before
