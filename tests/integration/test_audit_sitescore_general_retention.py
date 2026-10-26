"""Owned SQLite/LocalWORM general-export engineering controls, not QA03 UAT.

No program/purpose/retention approval registry or source licence is fabricated.
The existing SiteScore producer fixture is explicitly engineering data; these
controls cover real command/transaction/receipt behavior only.
"""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from modules.opsboard.audit.application.evidence_general_admission import (
    GeneralDecisionEvidenceSelection,
    GeneralSiteScoreEvidenceAdmission,
)
from modules.opsboard.audit.application.evidence_general_retention import (
    GeneralSiteScoreRetainedExport,
    general_retained_document_bytes,
)
from modules.opsboard.audit.application.evidence_projection import EvidencePolicyScope
from modules.opsboard.audit.application.evidence_retention import retained_document_bytes
from modules.opsboard.audit.application.sitescore_evidence import (
    SiteScoreEvidenceDenied,
    SiteScoreEvidenceUnavailable,
)
from modules.opsboard.audit.evidence_store import DurableEvidenceBundleStore
from shared.api.idempotency import IdempotencyConflictError
from shared.audit.backlog import AuditBacklogUnavailable, DurableAuditBacklog
from shared.audit.durable_delivery import COLLECTION as AUDIT_COLLECTION
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.audit.integrity import sha256_hex
from shared.audit.persistence import RetainedEvidence
from shared.audit.retained_backlog import RetainedEvidenceBacklog
from shared.audit.retained_delivery import COLLECTION, RetainedEvidenceDelivery
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.auth import DataClassification
from shared.infrastructure.persistence.command_receipts import TenantScopedCommandReceiptStore
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from tests.integration.test_audit_retained_backlog_lifecycle import governance
from tests.integration.test_audit_sitescore_evidence_resolver import case as case
from tests.integration.test_audit_sitescore_evidence_resolver import principal, resolver, selected
from tests.integration.test_audit_sitescore_retained_transaction import all_rows


def setup_general(case, tmp_path):
    parts, approvals, outcome = case
    engine, _reports, _decisions, audit, _workflow, review_owner = parts
    actor = principal()
    sink = LocalAppendOnlyWormSink(tmp_path / "engineering-general-worm-only")
    audit._worm_sink = sink
    store = DurableEvidenceBundleStore(engine, worm_sink=sink)
    queue = DurableJobQueue(engine)
    review_owner._audit_delivery_factory = lambda tenant: DurableAuditDelivery(engine, tenant, queue, sink)
    delivery = RetainedEvidenceDelivery(engine=engine, tenant_id=actor.tenant_id, queue=queue,
                                       evidence_store=store, audit_log=audit)
    admission = GeneralSiteScoreEvidenceAdmission(resolver(parts, approvals))
    source = admission.source_owner.resolve_source(actor, selected(outcome))
    scope = EvidencePolicyScope("json", "authenticated-response", source.events[0].occurred_at,
                                source.events[-1].occurred_at)
    request = GeneralDecisionEvidenceSelection(selected(outcome), "Inspect actual recorded decision", scope)
    receipts = TenantScopedCommandReceiptStore(queue, "sitescore-general-export", transaction_factory=engine.transaction)
    service = GeneralSiteScoreRetainedExport(admission, delivery, receipts)
    return service, delivery, actor, request, sink


def export(parts, *, request=None, actor=None, key="owned-general-key"):
    service, _delivery, original_actor, original_request, _sink = parts
    return service.export(actor or original_actor, request or original_request,
                          idempotency_key=key, correlation_id="owned-general-cid")


def test_general_export_commits_real_reason_export_audit_bytes_and_pending_without_new_approval_records(case, tmp_path):
    parts = setup_general(case, tmp_path)
    service, delivery, actor, request, sink = parts
    result = export(parts)
    record = delivery.evidence_store.get(result["export_id"])
    event = delivery.audit_log.get_event(record.audit_event_id, tenant_id=actor.tenant_id)
    assert event.action == "export" and event.event_type == "audit.evidence_export.v1"
    assert event.actor == actor.subject_id and event.metadata["reason"] == request.reason
    assert event.metadata["selected_scope"]["destination"] == "authenticated-response"
    assert event.metadata["decision_count"] == 1 and event.metadata["audit_event_count"] == 3
    assert event.metadata["external_acknowledged"] is False
    assert record.purpose == request.reason and record.program_id == request.selector.decision_id
    assert record.bundle["selection"]["program_label"] is None
    assert "admission_records" not in record.bundle
    assert record.retention_class == "audit-5y"
    content = general_retained_document_bytes(record)
    assert retained_document_bytes(record) == content
    assert result["artifact_bytes"] == len(content) and result["artifact_sha256"] == sha256_hex(record.bundle["document"])
    assert result["download_ready"] is False and result["delivery_status"] == "pending"
    assert result["external_acknowledged"] is False and delivery.inspect().pending == 1
    assert delivery.store.get(COLLECTION, record.export_id)["receipt"] is None
    assert list(sink._root.rglob("*.json")) == []
    before = all_rows(service.engine)
    replay = export(parts)
    assert replay["replayed"] is True and replay["export_id"] == result["export_id"]
    assert replay["receipt_id"] == result["receipt_id"] and all_rows(service.engine) == before


@pytest.mark.parametrize("change", ["reason", "program_label", "sections"])
def test_same_idempotency_key_cannot_rebind_reason_label_or_source_selection(case, tmp_path, change):
    parts = setup_general(case, tmp_path)
    export(parts)
    request = parts[3]
    if change == "reason":
        request = replace(request, reason="A different real reason")
    elif change == "program_label":
        request = replace(request, program_label="User selection, not eligibility")
    else:
        request = replace(request, selector=replace(request.selector, sections=("audit",)))
    before = all_rows(parts[0].engine)
    with pytest.raises(IdempotencyConflictError):
        export(parts, request=request)
    assert all_rows(parts[0].engine) == before


@pytest.mark.parametrize("denial", ["clearance", "candidate", "module"])
def test_replay_cannot_bypass_current_scope_or_field_clearance(case, tmp_path, denial):
    parts = setup_general(case, tmp_path)
    export(parts)
    actor = parts[2]
    if denial == "clearance":
        actor = replace(actor, scope=replace(actor.scope, clearance=DataClassification.INTERNAL))
    elif denial == "candidate":
        actor = replace(actor, scope=replace(actor.scope, store_ids=frozenset({"foreign-store"})))
    else:
        actor = replace(actor, scope=replace(actor.scope, modules=frozenset({"sitescore"})))
    before = all_rows(parts[0].engine)
    with pytest.raises(SiteScoreEvidenceDenied):
        export(parts, actor=actor)
    assert all_rows(parts[0].engine) == before


@pytest.mark.parametrize("phase", ["record", "intent"])
def test_general_export_sql_failure_leaves_no_export_audit_record_job_receipt_or_external_bytes(case, tmp_path, monkeypatch, phase):
    parts = setup_general(case, tmp_path)
    _service, delivery, _actor, _request, sink = parts
    before = all_rows(delivery.engine)
    if phase == "record":
        original = delivery.evidence_store._insert
        def fail(record):
            original(record)
            raise RuntimeError("Owned general failure after record insert")
        monkeypatch.setattr(delivery.evidence_store, "_insert", fail)
    else:
        original = delivery.stage
        def fail(record):
            original(record)
            raise RuntimeError("Owned general failure after intent")
        monkeypatch.setattr(delivery, "stage", fail)
    with pytest.raises(RuntimeError):
        export(parts)
    assert all_rows(delivery.engine) == before
    assert list(sink._root.rglob("*.json")) == []


def test_general_actual_external_receipt_is_separate_from_generated_sql_success(case, tmp_path):
    parts = setup_general(case, tmp_path)
    _service, delivery, _actor, _request, _sink = parts
    result = export(parts)
    record = delivery.evidence_store.get(result["export_id"])
    assert result["external_acknowledged"] is False
    job = delivery.queue.get_by_idempotency_key(delivery._key(record.export_id))
    actual_receipt = delivery.deliver(job)
    assert actual_receipt["checksum"] == sha256_hex(record.to_dict())
    assert actual_receipt["record_id"] == record.export_id
    assert actual_receipt["object_uri"].startswith("file://")
    assert delivery.inspect().pending == 0


@pytest.mark.parametrize("remaining_fact", ["record", "snapshot_anchor"])
def test_general_record_or_anchor_only_orphan_cannot_disappear_from_shared_pending_union(case, tmp_path, remaining_fact):
    parts = setup_general(case, tmp_path)
    _service, delivery, actor, _request, sink = parts
    result = export(parts)
    record = delivery.evidence_store.get(result["export_id"])
    intent = delivery.store.get(COLLECTION, record.export_id)
    event_ids = (record.audit_event_id, intent["snapshot_audit_event_id"])
    for event_id in event_ids:
        delivery.engine.execute("DELETE FROM durable_documents WHERE collection = ? AND doc_id = ?",
            (delivery.store._collection(AUDIT_COLLECTION), event_id))
    delivery.engine.execute("DELETE FROM durable_documents WHERE collection = ? AND doc_id = ?",
        (delivery.store._collection(COLLECTION), record.export_id))
    delivery.engine.execute("DELETE FROM durable_jobs WHERE correlation_id = ?", (record.correlation_id,))
    if remaining_fact == "record":
        delivery.engine.execute("DELETE FROM durable_audit_events WHERE resource = ?", ("audit-evidence/" + record.export_id,))
        assert delivery.evidence_store.get(record.export_id) == record
    else:
        delivery.engine.execute("DELETE FROM durable_audit_events WHERE event_id = ?", (record.audit_event_id,))
        delivery.engine.execute("DELETE FROM durable_evidence_bundles WHERE export_id = ?", (record.export_id,))
        assert delivery.evidence_store.get(record.export_id) is None
        assert delivery.audit_log.get_event(intent["snapshot_audit_event_id"], tenant_id=actor.tenant_id) is not None
    with pytest.raises(AuditBacklogUnavailable):
        RetainedEvidenceBacklog(delivery.engine, sink.sink_id).inspect(actor.tenant_id)
    with pytest.raises(AuditBacklogUnavailable):
        DurableAuditBacklog(delivery.engine, sink.sink_id).all_tenants()


def test_unknown_integrity_valid_retained_schema_is_unavailable_not_silently_zero_pending(case, tmp_path):
    service, delivery, actor, request, sink = setup_general(case, tmp_path)
    actual = service.admission.resolve(actor, request)
    generated = datetime.now(UTC)
    # A genuine typed store can contain a historical/unsupported bundle. This
    # deliberately does not manufacture a successful canonical export or policy.
    bundle = {"schema": "unsupported-engineering-format.v0", "identity_boundary": {
        "tenant_id": actor.tenant_id, "subject_id": actor.subject_id}}
    checksum = sha256_hex(bundle)
    bundle["bundle_checksum"] = checksum
    record = RetainedEvidence(export_id="owned-unknown-format", program_id=actual.source.decision_id,
        purpose="Stored unknown-version reader negative", requested_by=actor.subject_id,
        audit_event_id=actual.source.events[-1].event_id, bundle_checksum=checksum,
        data_classification=actual.classification.name.lower(), sensitive=False, export_scope="audit",
        retention_class=actual.retention_policy.retention_class,
        retain_until=actual.retention_policy.retain_until(generated), generated_at=generated,
        period_start=request.scope.period_start, period_end=request.scope.period_end,
        correlation_id="owned-unknown-schema-cid", bundle=bundle)
    saved = delivery.evidence_store.save(record)
    assert saved.record_hash and delivery.evidence_store.get(saved.export_id) == saved
    with pytest.raises(AuditBacklogUnavailable):
        RetainedEvidenceBacklog(delivery.engine, sink.sink_id).inspect(actor.tenant_id)
    with pytest.raises(AuditBacklogUnavailable):
        DurableAuditBacklog(delivery.engine, sink.sink_id).all_tenants()


@pytest.mark.parametrize("phase", ["pending", "delivered", "hold_pending", "purge_pending", "hold_delivered", "purge_delivered"])
def test_general_schema_pending_delivery_and_governance_share_truthful_backlog(case, tmp_path, phase):
    parts = setup_general(case, tmp_path)
    _service, delivery, actor, _request, sink = parts
    result = export(parts)
    original = delivery.evidence_store.get(result["export_id"])
    job = delivery.queue.get_by_idempotency_key(delivery._key(original.export_id))
    delivered = phase.endswith("delivered")
    receipt = delivery.deliver(job) if delivered else None
    current = original
    if phase.startswith("hold"):
        current = governance(delivery, original, "hold")
    elif phase.startswith("purge"):
        current = governance(delivery, original, "purge")
    expected = 0 if delivered else 1
    assert RetainedEvidenceBacklog(delivery.engine, sink.sink_id).inspect(actor.tenant_id).pending == expected
    intent = delivery.store.get(COLLECTION, original.export_id)
    if delivered:
        assert delivery.deliver(job) == receipt
        assert receipt["checksum"] == sha256_hex(original.to_dict())
    else:
        assert intent["status"] == "pending" and intent["receipt"] is None
        if phase != "pending":
            from shared.jobs.queue import NonRetryableJobError
            with pytest.raises(NonRetryableJobError, match="governance reconciliation"):
                delivery.deliver(job)
    if current.purged_at is not None:
        with pytest.raises(SiteScoreEvidenceUnavailable, match="purged_content"):
            general_retained_document_bytes(current)
    else:
        assert general_retained_document_bytes(current) == general_retained_document_bytes(original)
