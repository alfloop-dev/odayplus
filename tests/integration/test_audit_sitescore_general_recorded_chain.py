"""Canonical technical producers, actual retained bytes and explicit partial chain.

SQLite/LocalWORM controls are not PostgreSQL/GCS or human QA03 acceptance.
"""
import hashlib
import io
import json
import zipfile
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader

from apps.api.app.routes.audit import create_audit_router
from modules.opsboard.audit.application.evidence_general_admission import (
    GeneralSiteScoreEvidenceAdmission,
)
from modules.opsboard.audit.application.evidence_general_document import (
    CHAIN_GENERAL_SCHEMA,
    validate_general_document,
)
from modules.opsboard.audit.application.evidence_general_formats import render_general_document
from modules.opsboard.audit.application.evidence_general_preview import (
    GeneralSiteScoreEvidencePreview,
)
from modules.opsboard.audit.application.evidence_general_read import GeneralSiteScoreRetainedReader
from modules.opsboard.audit.application.evidence_general_retention import (
    GeneralSiteScoreRetainedExport,
    general_retained_document_bytes,
)
from modules.opsboard.audit.application.sitescore_decision_chain import (
    SiteScoreRecordedDecisionChainReader,
)
from modules.opsboard.audit.application.sitescore_evidence import SiteScoreEvidenceUnavailable
from shared.audit.integrity import sha256_hex
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurableRealizedSiteStore
from tests.integration.test_audit_sitescore_evidence_resolver import principal, selected
from tests.integration.test_audit_sitescore_general_factory import actual_factory as actual_factory
from tests.integration.test_audit_sitescore_general_factory import verified_boundary
from tests.integration.test_audit_sitescore_general_retention import export, setup_general
from tests.integration.test_audit_sitescore_recorded_decision_chain import (
    approved_chain as approved_chain,
)
from tests.integration.test_audit_sitescore_retained_transaction import all_rows


def chain_export(approved_chain, tmp_path):
    parts, approvals, outcome, realized, _report = approved_chain
    service, delivery, actor, selection, sink = setup_general((parts, approvals, outcome), tmp_path)
    source = service.admission.source_owner
    admission = GeneralSiteScoreEvidenceAdmission(source, SiteScoreRecordedDecisionChainReader(source, realized))
    return (GeneralSiteScoreRetainedExport(admission, delivery, service.receipts), delivery, actor, selection, sink)


@pytest.mark.parametrize("format", ["json", "csv", "pdf"])
def test_same_frozen_recorded_chain_is_retained_and_rendered_in_every_actual_format(approved_chain, tmp_path, format):
    parts = chain_export(approved_chain, tmp_path)
    service, delivery, actor, _selection, sink = parts
    result = export(parts)
    record = delivery.evidence_store.get(result["export_id"])
    doc = json.loads(general_retained_document_bytes(record))
    assert doc["schema"] == CHAIN_GENERAL_SCHEMA
    chain = doc["recorded_chain"]
    frozen = approved_chain[-1]
    assert chain["prediction"]["report_sha256"] == sha256_hex(frozen.to_dict())
    assert chain["prediction"]["intervals"]["m12"] == frozen.m12.to_dict()
    assert chain["human_decision"]["approval_id"] == approved_chain[2].decision.approval_id
    assert chain["execution"]["record"]["kind"] == "canonical_candidate_realization"
    assert chain["outcome"]["status"] == "unavailable" and chain["coverage"]["complete"] is False
    receipt = delivery.deliver(delivery.queue.get_by_idempotency_key(delivery._key(record.export_id)))
    objects = {str(path.relative_to(sink._root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sink._root.rglob("*.json")}
    artifact = GeneralSiteScoreRetainedReader(service).artifact(actor, record.export_id,
        correlation_id="actual-chain-format-cid", format=format)
    if format == "json":
        assert json.loads(artifact.content) == doc
    elif format == "csv":
        with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
            assert archive.testzip() is None
            assert json.loads(archive.read("recorded-decision-chain.json")) == chain
            assert b"recorded_chain.outcome.status" in archive.read("recorded-decision-chain.csv")
            assert json.loads(archive.read("canonical-evidence.json")) == doc
    else:
        pdf = PdfReader(io.BytesIO(artifact.content))
        text = "".join(page.extract_text() for page in pdf.pages).replace("\n", "")
        assert "sitescore.recorded-decision-chain.v1" in text and "NO_CANONICAL_DECISION_BOUND_SITESCORE_OUTCOME_OWNER" in text
    assert delivery.evidence_store.get(record.export_id) == record
    assert {str(path.relative_to(sink._root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sink._root.rglob("*.json")} == objects
    events = [delivery.audit_log._row_to_event(row) for row in delivery.engine.query(
        "SELECT * FROM durable_audit_events WHERE event_type = ?", ("audit.evidence_download.v1",))]
    assert len(events) == 1
    assert events[0].metadata["artifact_sha256"] == artifact.sha256
    assert events[0].metadata["canonical_json_sha256"] == sha256_hex(doc)
    assert events[0].metadata["retained_canonical_json_snapshot_checksum"] == receipt["checksum"]
    assert events[0].metadata["retained_canonical_json_external_acknowledged"] is True
    assert "external_acknowledged" not in events[0].metadata
    assert events[0].metadata["generated_artifact_external_acknowledged"] is False


def test_current_safe_preview_is_exact_versioned_document_without_export_or_delivery_write(approved_chain, tmp_path):
    parts = chain_export(approved_chain, tmp_path)
    service, _delivery, actor, selection, _sink = parts
    before = all_rows(service.engine)
    preview = GeneralSiteScoreEvidencePreview(service).preview(actor, selection)
    assert preview["document"] == service.admission.resolve(actor, selection).document()
    assert preview["document"]["recorded_chain"]["coverage"]["complete"] is False
    assert preview["retained"] is False and preview["external_acknowledged"] is False
    assert all_rows(service.engine) == before


@pytest.mark.parametrize("tamper", ["raw_field", "human_join", "report_join", "actor", "invented_outcome"])
@pytest.mark.parametrize("format", ["json", "csv", "pdf"])
def test_versioned_chain_cannot_mix_cross_decision_or_raw_unmasked_and_fictional_fields(
        approved_chain, tmp_path, tamper, format):
    service, _delivery, actor, selection, _sink = chain_export(approved_chain, tmp_path)
    document = deepcopy(service.admission.resolve(actor, selection).document())
    chain = document["recorded_chain"]
    if tamper == "raw_field":
        chain["prediction"]["raw_report"] = {"opaque": "not permitted"}
    elif tamper == "human_join":
        chain["human_decision"]["approval_id"] = "another-signature"
    elif tamper == "report_join":
        chain["prediction"]["report_id"] = "another-report"
    elif tamper == "actor":
        chain["human_decision"]["actor"] = "unmasked-actor"
    else:
        chain["outcome"] = {"status": "recorded", "record": {"revenue": 100}, "reason": None}
    with pytest.raises(SiteScoreEvidenceUnavailable):
        validate_general_document(document)
    with pytest.raises(SiteScoreEvidenceUnavailable):
        render_general_document(document, format)


def test_audit_only_selection_does_not_silently_include_unselected_prediction_and_human_detail(approved_chain, tmp_path):
    service, _delivery, actor, selection, _sink = chain_export(approved_chain, tmp_path)
    selected_audit = replace(selection, selector=replace(selection.selector, sections=("audit",)))
    doc = service.admission.resolve(actor, selected_audit).document()
    assert "recorded_chain" not in doc and list(doc["sections"]) == ["audit"]


def test_wrong_actual_realization_tenant_cannot_produce_an_export_or_new_audit(approved_chain, tmp_path):
    service, delivery, actor, selection, _sink = chain_export(approved_chain, tmp_path)
    source = service.admission.source_owner
    foreign = DurableRealizedSiteStore(TenantScopedDocumentStore(SqliteDocumentStore(service.engine), "other-engineering-tenant"))
    admission = GeneralSiteScoreEvidenceAdmission(source, SiteScoreRecordedDecisionChainReader(source, foreign))
    wrong = GeneralSiteScoreRetainedExport(admission, delivery, service.receipts)
    before = all_rows(service.engine)
    with pytest.raises(SiteScoreEvidenceUnavailable, match="canonical_realization_owner_binding"):
        wrong.export(actor, selection, idempotency_key="foreign-chain-owner-key", correlation_id="foreign-chain-owner-cid")
    assert all_rows(service.engine) == before


def test_actual_factory_injects_same_canonical_read_owners_instead_of_request_supplied_chain(actual_factory):
    _bundle, factory, outcome, _sink = actual_factory
    actor = principal()
    service = factory(actor.tenant_id)
    assert service.admission.recorded_chain_owner.source_owner is service.admission.source_owner
    source = service.admission.source_owner.resolve_source(actor, selected(outcome))
    from modules.opsboard.audit.application.evidence_general_admission import (
        GeneralDecisionEvidenceSelection,
    )
    from modules.opsboard.audit.application.evidence_projection import EvidencePolicyScope
    selection = GeneralDecisionEvidenceSelection(selected(outcome), "Inspect actual factory recorded data",
        EvidencePolicyScope("json", "authenticated-response", source.events[0].occurred_at, source.events[-1].occurred_at))
    preview = GeneralSiteScoreEvidencePreview(service).preview(actor, selection)
    assert preview["document"]["schema"] == CHAIN_GENERAL_SCHEMA
    assert preview["document"]["recorded_chain"]["human_decision"]["decision_id"] == outcome.decision.decision_id
    assert preview["document"]["recorded_chain"]["outcome"]["record"] is None


def test_changed_partial_chain_cannot_export_different_content_from_the_actual_preview(approved_chain, tmp_path):
    service, _delivery, actor, selection, _sink = chain_export(approved_chain, tmp_path)
    preview = GeneralSiteScoreEvidencePreview(service).preview(actor, selection)
    realized = approved_chain[3]
    original = realized.get(approved_chain[2].decision.candidate_site_id)
    # Deliberate owned typed-owner replacement, not a production write. The
    # decision/report source digest remains identical while execution changes.
    realized.put(replace(original, decision_id="different-engineering-round"))
    before = all_rows(service.engine)
    with pytest.raises(SiteScoreEvidenceUnavailable, match="preview_document_changed"):
        service.export(actor, selection, idempotency_key="stale-preview-document-key",
            correlation_id="stale-preview-document-cid", expected_document_sha256=preview["artifact_sha256"])
    assert all_rows(service.engine) == before


@pytest.mark.parametrize("format", ["json", "csv", "pdf"])
def test_real_factory_current_HTTP_preview_create_and_actual_format_keep_the_same_partial_chain(
        actual_factory, monkeypatch, format):
    bundle, factory, outcome, _sink = actual_factory
    actor = principal()
    service = factory(actor.tenant_id)
    source = service.admission.source_owner.resolve_source(actor, selected(outcome))
    boundary, headers = verified_boundary()
    app = FastAPI()
    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = "chain-real-factory-http-cid"
        return await call_next(request)
    app.include_router(create_audit_router(audit_log=bundle.audit_log, evidence_store=bundle.evidence_store,
        boundary=boundary, canonical_service_for_tenant=factory))
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    client = TestClient(app, headers=headers)
    body = {"decision_id": source.decision_id, "expected_decision_version": source.decision_version,
        "sections": ["decision_log", "audit"], "reason": "Inspect actual recorded partial chain",
        "from_time": source.events[0].occurred_at.isoformat(), "to_time": source.events[-1].occurred_at.isoformat(), "format": "json"}
    before = all_rows(bundle.engine)
    before_outbox = [tuple(row) for row in bundle.engine.query(
        "SELECT * FROM durable_outbox_events ORDER BY event_id")]
    preview = client.post("/audit/evidence/preview", json=body)
    assert preview.status_code == 200
    _assert_chain_HTTP_only_appends_actual_authorization(
        bundle, actor, before, before_outbox, expected_outcomes=("allow",))
    doc = preview.json()["document"]
    assert doc["schema"] == CHAIN_GENERAL_SCHEMA and doc["recorded_chain"]["coverage"]["complete"] is False
    rejected = client.post("/audit/evidence/export", json={**body, "expected_document_sha256": "0" * 64})
    assert rejected.status_code == 503
    _assert_chain_HTTP_only_appends_actual_authorization(
        bundle, actor, before, before_outbox, expected_outcomes=("allow", "allow"))
    created = client.post("/audit/evidence/export", json={**body, "expected_document_sha256": preview.json()["artifact_sha256"]})
    assert created.status_code == 201
    record = bundle.evidence_store.get(created.json()["export_id"])
    assert record.bundle["document"] == doc
    delivery = factory(actor.tenant_id).delivery
    receipt = delivery.deliver(delivery.queue.get_by_idempotency_key(delivery._key(record.export_id)))
    downloaded = client.get(f"/audit/evidence/exports/{record.export_id}/download", params={"format": format})
    assert downloaded.status_code == 200
    if format == "json":
        assert downloaded.json() == doc
    elif format == "csv":
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            assert json.loads(archive.read("canonical-evidence.json")) == doc
            assert json.loads(archive.read("recorded-decision-chain.json")) == doc["recorded_chain"]
    else:
        text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(downloaded.content)).pages).replace("\n", "")
        assert "NO_CANONICAL_DECISION_BOUND_SITESCORE_OUTCOME_OWNER" in text
    events = [bundle.audit_log._row_to_event(row) for row in bundle.engine.query(
        "SELECT * FROM durable_audit_events WHERE event_type = ?", ("audit.evidence_download.v1",))]
    assert len(events) == 1 and events[0].metadata["retained_canonical_json_snapshot_checksum"] == receipt["checksum"]
    assert events[0].metadata["canonical_json_sha256"] == sha256_hex(doc)


def _assert_chain_HTTP_only_appends_actual_authorization(delivery, actor, before, before_outbox, *, expected_outcomes):
    after = all_rows(delivery.engine)
    assert set(after) == set(before)
    for table in before:
        if table != "durable_audit_events":
            assert after[table] == before[table]
    assert [tuple(row) for row in delivery.engine.query(
        "SELECT * FROM durable_outbox_events ORDER BY event_id")] == before_outbox
    # Preserve all original raw audit rows; only the precise original Auth005
    # security records may append. Never suppress or replace production audit.
    previous = before["durable_audit_events"]
    current = after["durable_audit_events"]
    assert all(row in current for row in previous)
    added = [delivery.audit_log._row_to_event(row) for row in delivery.engine.query(
        "SELECT * FROM durable_audit_events ORDER BY sequence") if tuple(row) not in previous]
    assert len(current) == len(previous) + len(expected_outcomes)
    assert len(added) == len(expected_outcomes)
    assert tuple(event.outcome for event in added) == expected_outcomes
    for event in added:
        assert event.event_type == "security.authorization"
        assert event.actor == actor.subject_id and event.resource == "audit" and event.action == "export"
        assert event.metadata["tenant_id"] == actor.tenant_id
        assert event.correlation_id == "chain-real-factory-http-cid"
        assert isinstance(event.metadata["reason"], str) and event.metadata["reason"]
    delivery.audit_log.verify_chain().raise_for_tamper()
