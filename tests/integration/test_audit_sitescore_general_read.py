"""Owned SQLite/LocalWORM current-read controls, not QA03 human acceptance."""
import builtins
import json
from dataclasses import replace

import pytest

from modules.opsboard.audit.application.evidence_general_read import GeneralSiteScoreRetainedReader
from modules.opsboard.audit.application.sitescore_evidence import (
    SiteScoreEvidenceDenied,
    SiteScoreEvidenceUnavailable,
)
from shared.auth import DataClassification, Role
from tests.integration.test_audit_retained_backlog_lifecycle import governance
from tests.integration.test_audit_sitescore_evidence_resolver import case as case
from tests.integration.test_audit_sitescore_general_retention import export, setup_general
from tests.integration.test_audit_sitescore_retained_transaction import all_rows


def delivered(case, tmp_path):
    parts = setup_general(case, tmp_path)
    result = export(parts)
    service, delivery, actor, _request, _sink = parts
    record = delivery.evidence_store.get(result["export_id"])
    job = delivery.queue.get_by_idempotency_key(delivery._key(record.export_id))
    receipt = delivery.deliver(job)
    return parts, record, receipt, GeneralSiteScoreRetainedReader(service)


def test_general_current_read_is_actual_safe_bytes_with_real_response_audit_and_no_Stage1_approval_owner(case, tmp_path, monkeypatch):
    parts, record, receipt, reader = delivered(case, tmp_path)
    _service, delivery, actor, _request, sink = parts
    original_import = builtins.__import__
    def no_approval_lane(name, *args, **kwargs):
        if name == "modules.opsboard.audit.application.evidence_admission":
            raise AssertionError("General production read must not import Stage1 approval ports")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_approval_lane)
    before = [dict(row) for row in delivery.engine.query("SELECT * FROM durable_audit_events")]
    objects = sorted(sink._root.rglob("*.json"))
    content = reader.content(actor, record.export_id, correlation_id="owned-read-cid")
    assert json.loads(content) == record.bundle["document"]
    assert b"admission_records" not in content and b"governance_log" not in content
    assert b"offline-evidence-reader" not in content
    events = [delivery.audit_log._row_to_event(row) for row in delivery.engine.query("SELECT * FROM durable_audit_events")]
    response = [event for event in events if event.event_type == "audit.evidence_download.v1"]
    assert len(events) == len(before) + 1 and len(response) == 1
    assert response[0].actor == actor.subject_id and response[0].action == "export"
    assert response[0].correlation_id == "owned-read-cid"
    assert response[0].metadata["retained_snapshot_checksum"] == receipt["checksum"]
    assert response[0].metadata["reason"] == record.purpose
    assert sorted(sink._root.rglob("*.json")) == objects
    row = reader.history(actor)[0]
    assert row["external_acknowledged"] is True and row["download_ready"] is True
    assert "bundle" not in row and "governance_log" not in row


@pytest.mark.parametrize("change", ["subject", "tenant", "module", "candidate", "role", "clearance"])
def test_general_download_and_history_recheck_current_authority_without_new_audit_or_external_write(case, tmp_path, change):
    parts, record, _receipt, reader = delivered(case, tmp_path)
    service, _delivery, actor, _request, sink = parts
    if change == "subject":
        actor = replace(actor, subject_id="another-owned-reader")
    elif change == "tenant":
        actor = replace(actor, scope=replace(actor.scope, tenant_id="other-owned-tenant"))
    elif change == "module":
        actor = replace(actor, scope=replace(actor.scope, modules=frozenset({"sitescore"})))
    elif change == "candidate":
        actor = replace(actor, scope=replace(actor.scope, store_ids=frozenset({"not-this-candidate"})))
    elif change == "role":
        actor = replace(actor, roles=frozenset({Role.SITE_REVIEWER}))
    else:
        actor = replace(actor, scope=replace(actor.scope, clearance=DataClassification.INTERNAL))
    before = all_rows(service.engine)
    objects = sorted(sink._root.rglob("*.json"))
    with pytest.raises((SiteScoreEvidenceDenied, SiteScoreEvidenceUnavailable)):
        reader.content(actor, record.export_id, correlation_id="owned-denied-cid")
    # The history cannot reveal a foreign subject, object or revoked row.
    if change != "tenant":
        assert reader.history(actor) == []
    assert all_rows(service.engine) == before
    assert sorted(sink._root.rglob("*.json")) == objects


def test_pending_is_visible_but_never_a_ready_download_or_response_audit(case, tmp_path):
    parts = setup_general(case, tmp_path)
    result = export(parts)
    service, _delivery, actor, _request, _sink = parts
    reader = GeneralSiteScoreRetainedReader(service)
    row = reader.history(actor)[0]
    assert row["delivery_status"] == "pending" and row["external_acknowledged"] is False
    assert row["download_ready"] is False
    before = all_rows(service.engine)
    with pytest.raises(SiteScoreEvidenceUnavailable, match="retained_external_delivery_pending"):
        reader.content(actor, result["export_id"], correlation_id="owned-pending-cid")
    assert all_rows(service.engine) == before


@pytest.mark.parametrize("operation", ["hold", "purge"])
def test_current_governance_does_not_rewrite_original_ack_and_purge_refuses_download(case, tmp_path, operation):
    parts, original, receipt, reader = delivered(case, tmp_path)
    _service, delivery, actor, _request, _sink = parts
    current = governance(delivery, original, operation)
    row = reader.history(actor)[0]
    assert row["external_acknowledged"] is True
    assert delivery.store.get("audit.retained_evidence_delivery", original.export_id)["receipt"] == receipt
    if operation == "purge":
        before = all_rows(delivery.engine)
        assert row["download_ready"] is False and row["purged_at"] == current.purged_at.isoformat()
        with pytest.raises(SiteScoreEvidenceUnavailable, match="purged_content"):
            reader.content(actor, original.export_id, correlation_id="owned-purged-read-cid")
        assert all_rows(delivery.engine) == before
    else:
        assert row["legal_hold"] is True and row["download_ready"] is True
        assert json.loads(reader.content(actor, original.export_id,
            correlation_id="owned-held-read-cid")) == original.bundle["document"]


def test_missing_actual_external_object_cannot_be_hidden_by_SQL_delivered_receipt(case, tmp_path):
    parts, record, receipt, reader = delivered(case, tmp_path)
    service, _delivery, actor, _request, sink = parts
    before = all_rows(service.engine)
    # Deliberate owned LocalWORM corruption, never a production WORM operation.
    path = next(p for p in sink._root.rglob("*.json") if p.resolve().as_uri() == receipt["object_uri"])
    path.unlink()
    with pytest.raises(SiteScoreEvidenceUnavailable, match="retained_external_receipt_unavailable"):
        reader.content(actor, record.export_id, correlation_id="owned-missing-object-cid")
    assert all_rows(service.engine) == before
