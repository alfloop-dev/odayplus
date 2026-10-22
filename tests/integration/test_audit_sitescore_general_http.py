"""Actual route/HMAC/SQLite/LocalWORM controls; no production credentials/UAT."""
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.audit import create_audit_router
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    SigningKey,
    encode_compact_jwt,
)
from shared.audit.integrity import sha256_hex
from shared.auth import Role
from tests.integration.test_audit_retained_backlog_lifecycle import governance
from tests.integration.test_audit_sitescore_evidence_resolver import case as case
from tests.integration.test_audit_sitescore_general_retention import setup_general


def client_parts(case, tmp_path, monkeypatch, *, owner=True):
    parts = setup_general(case, tmp_path)
    service, delivery, actor, selection, _sink = parts
    key = SigningKey(kid="owned-general-http", algorithm="HS256", secret=b"engineering-general-http-only-key")
    mapping = {actor.subject_id: {"roles": [Role.AUDITOR.value, Role.SITE_REVIEWER.value],
        "scope": {"tenant_id": actor.tenant_id}}}
    boundary = AuthenticationBoundary(AuthBoundaryConfig(issuer="https://general-http.invalid",
        audiences=frozenset({"owned-audit-api"}), signing_keys={key.kid: key}, principal_mappings=mapping))
    now = datetime.now(UTC)
    token = encode_compact_jwt({"sub": actor.subject_id, "iss": "https://general-http.invalid",
        "aud": "owned-audit-api", "iat": now.timestamp(),
        "exp": (now + timedelta(minutes=5)).timestamp()}, key)
    app = FastAPI()
    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id", "owned-general-http-cid")
        return await call_next(request)
    app.include_router(create_audit_router(audit_log=delivery.audit_log,
        evidence_store=delivery.evidence_store, boundary=boundary,
        canonical_service_for_tenant=(lambda tenant: service) if owner else None))
    client = TestClient(app, headers={"Authorization": "Bearer " + token,
        "Idempotency-Key": "owned-general-http-key"})
    body = {"decision_id": selection.selector.decision_id,
        "expected_decision_version": selection.selector.expected_decision_version,
        "sections": list(selection.selector.sections), "reason": "Inspect actual recorded decision",
        "from_time": selection.scope.period_start.isoformat(), "to_time": selection.scope.period_end.isoformat(),
        "format": "json"}
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    return client, parts, body, mapping


def export_events(delivery):
    return [delivery.audit_log._row_to_event(row) for row in delivery.engine.query(
        "SELECT * FROM durable_audit_events WHERE event_type = ?", ("audit.evidence_export.v1",))]


def test_real_general_HTTP_export_replay_pending_then_actual_current_JSON_download(case, tmp_path, monkeypatch):
    client, parts, body, _mapping = client_parts(case, tmp_path, monkeypatch)
    _service, delivery, actor, _selection, _sink = parts
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    result = created.json()
    assert result["external_acknowledged"] is False and result["download_ready"] is False
    history = client.get("/audit/evidence/exports")
    assert history.status_code == 200 and history.json()["exports"][0]["delivery_status"] == "pending"
    pending = client.get(f"/audit/evidence/exports/{result['export_id']}/download", params={"format": "json"})
    assert pending.status_code == 503 and "retained_external_delivery_pending" in pending.json()["detail"]
    replay = client.post("/audit/evidence/export", json=body)
    assert replay.status_code == 201 and replay.json()["replayed"] is True
    assert replay.json()["export_id"] == result["export_id"]
    assert len(export_events(delivery)) == 1
    record = delivery.evidence_store.get(result["export_id"])
    delivery.deliver(delivery.queue.get_by_idempotency_key(delivery._key(record.export_id)))
    response = client.get(f"/audit/evidence/exports/{record.export_id}/download", params={"format": "json"},
        headers={"x-correlation-id": "owned-download-cid"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/json")
    assert hashlib.sha256(response.content).hexdigest() == response.headers["x-artifact-sha256"]
    assert response.json() == record.bundle["document"]
    assert response.headers["cache-control"] == "private, no-store"
    assert "bundle" not in response.json() and "admission_records" not in response.json()
    assert actor.subject_id not in response.text
    full = client.get(f"/audit/evidence/exports/{record.export_id}")
    assert full.status_code == 200 and full.content == response.content
    events = [delivery.audit_log._row_to_event(row) for row in delivery.engine.query(
        "SELECT * FROM durable_audit_events WHERE event_type = ?", ("audit.evidence_download.v1",))]
    assert len(events) == 2 and any(event.correlation_id == "owned-download-cid" for event in events)
    assert all(event.actor == actor.subject_id and event.action == "export" for event in events)


@pytest.mark.parametrize("claim,value", [("requested_by", "spoofed"), ("data_classification", "public"),
    ("authorization_id", "caller-claims-approved"), ("retention_days", 1), ("decision_cards", [{}])])
def test_canonical_HTTP_forbids_body_authority_fields_and_cannot_write(case, tmp_path, monkeypatch, claim, value):
    client, parts, body, _mapping = client_parts(case, tmp_path, monkeypatch)
    before = len(parts[1].evidence_store.list_all())
    response = client.post("/audit/evidence/export", json={**body, claim: value})
    assert response.status_code == 422
    assert len(parts[1].evidence_store.list_all()) == before and export_events(parts[1]) == []


@pytest.mark.parametrize("failure", ["reason", "key", "format", "version", "owner"])
def test_missing_general_required_input_or_owner_is_not_export_success(case, tmp_path, monkeypatch, failure):
    client, parts, body, _mapping = client_parts(case, tmp_path, monkeypatch, owner=failure != "owner")
    if failure == "reason":
        body["reason"] = " "
    elif failure == "key":
        client.headers.pop("idempotency-key")
    elif failure == "format":
        body["format"] = "pdf"
    elif failure == "version":
        body["expected_decision_version"] = True
    response = client.post("/audit/evidence/export", json=body)
    assert response.status_code == (503 if failure == "owner" else 422)
    assert parts[1].evidence_store.list_all() == [] and export_events(parts[1]) == []


def test_current_permission_revocation_denies_retained_HTTP_content_but_does_not_rewrite_original_export(case, tmp_path, monkeypatch):
    client, parts, body, mapping = client_parts(case, tmp_path, monkeypatch)
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    delivery, actor = parts[1], parts[2]
    record = delivery.evidence_store.get(created.json()["export_id"])
    delivery.deliver(delivery.queue.get_by_idempotency_key(delivery._key(record.export_id)))
    mapping[actor.subject_id]["roles"] = [Role.SITE_REVIEWER.value]
    response = client.get(f"/audit/evidence/exports/{record.export_id}/download", params={"format": "json"})
    assert response.status_code == 403
    assert delivery.evidence_store.get(record.export_id) == record and len(export_events(delivery)) == 1
    assert delivery.engine.query("SELECT * FROM durable_audit_events WHERE event_type = ?",
        ("audit.evidence_download.v1",)) == []


@pytest.mark.parametrize("format,media,extension,audited_format", [
    ("json", "application/json", "json", "json"),
    ("csv", "application/zip", "zip", "csv"),
    ("zip", "application/zip", "zip", "csv"),
    ("pdf", "application/pdf", "pdf", "pdf"),
])
def test_current_HTTP_returns_real_selected_artifact_and_audits_exact_response_bytes(
        case, tmp_path, monkeypatch, format, media, extension, audited_format):
    client, parts, body, _mapping = client_parts(case, tmp_path, monkeypatch)
    delivery, actor = parts[1], parts[2]
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    record = delivery.evidence_store.get(created.json()["export_id"])
    delivery.deliver(delivery.queue.get_by_idempotency_key(delivery._key(record.export_id)))
    before_record = delivery.evidence_store.get(record.export_id)
    response = client.get(f"/audit/evidence/exports/{record.export_id}/download",
        params={"format": format}, headers={"x-correlation-id": "owned-selected-format-cid"})
    assert response.status_code == 200 and response.headers["content-type"].startswith(media)
    digest = hashlib.sha256(response.content).hexdigest()
    assert digest == response.headers["x-artifact-sha256"]
    assert response.headers["content-disposition"] == f'attachment; filename="audit-evidence.{extension}"'
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    if audited_format == "json":
        assert response.json() == record.bundle["document"]
    elif audited_format == "csv":
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.testzip() is None
            assert set(archive.namelist()) == {"canonical-evidence.json", "decision-log.csv", "audit.csv", "manifest.json"}
            assert json.loads(archive.read("canonical-evidence.json")) == record.bundle["document"]
            manifest = json.loads(archive.read("manifest.json"))
            for item in manifest["files"]:
                data = archive.read(item["path"])
                assert len(data) == item["bytes"] and hashlib.sha256(data).hexdigest() == item["sha256"]
    else:
        from pypdf import PdfReader
        pdf = PdfReader(io.BytesIO(response.content))
        assert response.content.startswith(b"%PDF-") and len(pdf.pages) > 0
        assert "Reference coverage only" in "".join(page.extract_text() for page in pdf.pages)
    events = [delivery.audit_log._row_to_event(row) for row in delivery.engine.query(
        "SELECT * FROM durable_audit_events WHERE event_type = ?", ("audit.evidence_download.v1",))]
    assert len(events) == 1
    event = events[0]
    assert event.actor == actor.subject_id and event.action == "export"
    assert event.correlation_id == "owned-selected-format-cid"
    assert event.metadata["format"] == audited_format
    assert event.metadata["artifact_sha256"] == digest
    assert event.metadata["artifact_bytes"] == len(response.content)
    assert event.metadata["canonical_json_sha256"] == sha256_hex(record.bundle["document"])
    assert event.metadata["retained_canonical_json_external_acknowledged"] is True
    assert "external_acknowledged" not in event.metadata
    assert event.metadata["generated_artifact_external_acknowledged"] is False
    assert event.metadata["retained_ack_scope"] == "original_retained_record_snapshot_including_canonical_json"
    assert event.metadata["retained_canonical_json_snapshot_checksum"] == delivery.store.get(
        "audit.retained_evidence_delivery", record.export_id)["receipt"]["checksum"]
    assert delivery.evidence_store.get(record.export_id) == before_record
    assert len(export_events(delivery)) == 1


@pytest.mark.parametrize("format", ["json", "csv", "pdf"])
@pytest.mark.parametrize("state", ["pending", "revoked", "purged"])
def test_all_current_HTTP_formats_recheck_pending_authority_and_purge_without_response_audit(
        case, tmp_path, monkeypatch, format, state):
    client, parts, body, mapping = client_parts(case, tmp_path, monkeypatch)
    delivery, actor = parts[1], parts[2]
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    record = delivery.evidence_store.get(created.json()["export_id"])
    if state != "pending":
        delivery.deliver(delivery.queue.get_by_idempotency_key(delivery._key(record.export_id)))
    if state == "revoked":
        mapping[actor.subject_id]["roles"] = [Role.SITE_REVIEWER.value]
    elif state == "purged":
        governance(delivery, record, "purge")
    before = delivery.evidence_store.get(record.export_id)
    response = client.get(f"/audit/evidence/exports/{record.export_id}/download", params={"format": format})
    assert response.status_code == {"pending": 503, "revoked": 403, "purged": 410}[state]
    assert delivery.evidence_store.get(record.export_id) == before
    assert delivery.engine.query("SELECT * FROM durable_audit_events WHERE event_type = ?",
        ("audit.evidence_download.v1",)) == []
    assert len(export_events(delivery)) == 1


@pytest.mark.parametrize("format", ["json", "csv", "pdf"])
def test_held_current_HTTP_artifact_keeps_original_external_ack_and_retained_bytes(
        case, tmp_path, monkeypatch, format):
    client, parts, body, _mapping = client_parts(case, tmp_path, monkeypatch)
    delivery = parts[1]
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    record = delivery.evidence_store.get(created.json()["export_id"])
    receipt = delivery.deliver(delivery.queue.get_by_idempotency_key(delivery._key(record.export_id)))
    held = governance(delivery, record, "hold")
    response = client.get(f"/audit/evidence/exports/{record.export_id}/download", params={"format": format})
    assert response.status_code == 200
    assert delivery.evidence_store.get(record.export_id) == held
    assert held.bundle == record.bundle and held.governance_hash != record.governance_hash
    assert delivery.store.get("audit.retained_evidence_delivery", record.export_id)["receipt"] == receipt
    events = [delivery.audit_log._row_to_event(row) for row in delivery.engine.query(
        "SELECT * FROM durable_audit_events WHERE event_type = ?", ("audit.evidence_download.v1",))]
    assert len(events) == 1 and events[0].metadata["retained_snapshot_checksum"] == receipt["checksum"]



def test_actual_current_source_choices_and_safe_preview_are_read_only_not_retained_or_acknowledged(
        case, tmp_path, monkeypatch):
    from tests.integration.test_audit_sitescore_retained_transaction import all_rows
    client, parts, body, _mapping = client_parts(case, tmp_path, monkeypatch)
    delivery, actor = parts[1], parts[2]
    before = all_rows(delivery.engine)
    before_outbox = [tuple(row) for row in delivery.engine.query("SELECT * FROM durable_outbox_events ORDER BY event_id")]
    choices = client.get("/audit/evidence/sources")
    assert choices.status_code == 200
    rows = choices.json()["sources"]
    assert len(rows) == 1 and rows[0]["decision_id"] == body["decision_id"]
    assert rows[0]["decision_version"] == body["expected_decision_version"]
    assert choices.json()["supported_modules"] == ["SiteScore"]
    assert choices.json()["supported_sections"] == ["decision_log", "audit"]
    preview = client.post("/audit/evidence/preview", json=body)
    assert preview.status_code == 200
    result = preview.json()
    assert result["retained"] is False and result["external_acknowledged"] is False
    assert result["decision_id"] == body["decision_id"] and result["decision_version"] == body["expected_decision_version"]
    assert result["reason"] == body["reason"] and result["classification"] == "CONFIDENTIAL"
    assert sha256_hex(result["document"]) == result["artifact_sha256"]
    assert result["document"]["limits"]["content"] == "recorded_decision_and_audit_references_only"
    assert actor.subject_id not in preview.text
    _assert_preview_only_appends_actual_authorization(delivery, actor, before, before_outbox,
        expected_outcomes=("allow", "allow"))
    assert delivery.evidence_store.list_all() == []


@pytest.mark.parametrize("change", ["role", "version", "reason", "body_authority"])
def test_preview_rechecks_current_authority_version_and_real_reason_without_write(
        case, tmp_path, monkeypatch, change):
    from tests.integration.test_audit_sitescore_retained_transaction import all_rows
    client, parts, body, mapping = client_parts(case, tmp_path, monkeypatch)
    delivery, actor = parts[1], parts[2]
    if change == "role":
        mapping[actor.subject_id]["roles"] = [Role.SITE_REVIEWER.value]
    elif change == "version":
        body["expected_decision_version"] += 1
    elif change == "reason":
        body["reason"] = " "
    else:
        body["authorized_by"] = "caller-is-not-authority"
    before = all_rows(delivery.engine)
    before_outbox = [tuple(row) for row in delivery.engine.query("SELECT * FROM durable_outbox_events ORDER BY event_id")]
    response = client.post("/audit/evidence/preview", json=body)
    assert response.status_code == {"role": 403, "version": 503, "reason": 422, "body_authority": 422}[change]
    _assert_preview_only_appends_actual_authorization(delivery, actor, before, before_outbox,
        expected_outcomes=("deny",) if change == "role" else ("allow",))
    if change == "role":
        assert client.get("/audit/evidence/sources").status_code == 403
        _assert_preview_only_appends_actual_authorization(delivery, actor, before, before_outbox,
            expected_outcomes=("deny", "deny"))


@pytest.mark.parametrize("format", ["json", "csv", "pdf", "full"])
@pytest.mark.parametrize("state", ["unknown", "foreign_same_subject", "corrupt_unverified"])
def test_unknown_foreign_same_subject_and_unverified_corrupt_HTTP_ids_have_one_404_before_facts(
        case, tmp_path, monkeypatch, format, state):
    from dataclasses import replace

    from shared.audit.persistence import (
        attach_retained_evidence_integrity,
        verify_retained_evidence,
    )

    client, parts, body, _mapping = client_parts(case, tmp_path, monkeypatch)
    delivery, actor = parts[1], parts[2]
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    record = delivery.evidence_store.get(created.json()["export_id"])
    export_id = record.export_id
    if state == "unknown":
        export_id = "owned-absent-retained-reference"
    elif state == "foreign_same_subject":
        # Explicit historical foreign-row injection, not a fake policy grant:
        # the record is internally intact but its tenant differs, with the SAME
        # actual subject. It must be rejected before current-tenant intent lookup.
        foreign = replace(record, bundle={**record.bundle,
            "identity_boundary": {"tenant_id": "owned-foreign-tenant", "subject_id": actor.subject_id}})
        foreign = attach_retained_evidence_integrity(foreign,
            sequence=record.sequence, previous_hash=record.previous_hash)
        verify_retained_evidence(foreign)
        delivery.engine.execute("UPDATE durable_evidence_bundles SET bundle_json = ?, record_hash = ?, "
            "governance_hash = ? WHERE export_id = ?",
            (json.dumps(foreign.bundle), foreign.record_hash, foreign.governance_hash, record.export_id))
        assert delivery.evidence_store.get(export_id).requested_by == actor.subject_id
    else:
        # The real durable get verifies integrity and raises before ownership
        # can be established. That exception must not become an existence oracle.
        delivery.engine.execute("UPDATE durable_evidence_bundles SET record_hash = ? WHERE export_id = ?",
            ("0" * 64, record.export_id))
    before_records = [dict(row) for row in delivery.engine.query("SELECT * FROM durable_evidence_bundles")]
    before_audit = [dict(row) for row in delivery.engine.query("SELECT * FROM durable_audit_events")]
    observed = {"facts": 0, "sink": 0}
    def forbidden_facts(*args, **kwargs):
        observed["facts"] += 1
        raise AssertionError("Unverified owner reached retained facts")
    def forbidden_sink(*args, **kwargs):
        observed["sink"] += 1
        raise AssertionError("Unverified owner reached external sink")
    monkeypatch.setattr(delivery, "_facts", forbidden_facts)
    monkeypatch.setattr(delivery.sink, "lookup_retained_evidence", forbidden_sink)
    response = (client.get(f"/audit/evidence/exports/{export_id}") if format == "full" else
        client.get(f"/audit/evidence/exports/{export_id}/download", params={"format": format}))
    assert response.status_code == 404
    assert response.json() == {"detail": "retained evidence source not available"}
    assert observed == {"facts": 0, "sink": 0}
    assert [dict(row) for row in delivery.engine.query("SELECT * FROM durable_evidence_bundles")] == before_records
    _assert_existing_audit_and_only_actual_export_authorization(delivery, actor, before_audit)
    assert len(export_events(delivery)) == 1


@pytest.mark.parametrize("format", ["json", "csv", "pdf", "full"])
def test_current_verified_owner_corrupt_external_ack_remains_failclosed_without_download_audit(
        case, tmp_path, monkeypatch, format):
    from shared.audit.retained_delivery import COLLECTION

    client, parts, body, _mapping = client_parts(case, tmp_path, monkeypatch)
    delivery, actor = parts[1], parts[2]
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    record = delivery.evidence_store.get(created.json()["export_id"])
    delivery.deliver(delivery.queue.get_by_idempotency_key(delivery._key(record.export_id)))
    assert record.bundle["identity_boundary"] == {"tenant_id": actor.tenant_id, "subject_id": actor.subject_id}
    intent = delivery.store.get(COLLECTION, record.export_id)
    broken = {**intent, "receipt": {**intent["receipt"], "checksum": "0" * 64}}
    delivery.store.put(COLLECTION, record.export_id, broken)
    before_audit = [dict(row) for row in delivery.engine.query("SELECT * FROM durable_audit_events")]
    response = (client.get(f"/audit/evidence/exports/{record.export_id}") if format == "full" else
        client.get(f"/audit/evidence/exports/{record.export_id}/download", params={"format": format}))
    assert response.status_code == 503
    assert response.json() == {"detail": "CANONICAL_EVIDENCE_INPUT_REQUIRED"}
    assert delivery.evidence_store.get(record.export_id) == record
    assert delivery.store.get(COLLECTION, record.export_id) == broken
    _assert_existing_audit_and_only_actual_export_authorization(delivery, actor, before_audit)
    assert len(export_events(delivery)) == 1


def _assert_existing_audit_and_only_actual_export_authorization(delivery, actor, before):
    # HTTP type-level authorization is truthfully audited even when the later
    # retained-object read is denied. No new evidence export/download is allowed.
    after = [dict(row) for row in delivery.engine.query("SELECT * FROM durable_audit_events")]
    before_by_id = {row["event_id"]: row for row in before}
    assert {row["event_id"]: row for row in after if row["event_id"] in before_by_id} == before_by_id
    added = [delivery.audit_log._row_to_event(row) for row in after if row["event_id"] not in before_by_id]
    assert len(added) == 1
    event = added[0]
    assert event.event_type == "security.authorization"
    assert event.actor == actor.subject_id and event.resource == "audit" and event.action == "export"
    assert event.outcome == "allow" and event.metadata["tenant_id"] == actor.tenant_id
    assert event.correlation_id == "owned-general-http-cid"


def _assert_preview_only_appends_actual_authorization(delivery, actor, before, before_outbox, *, expected_outcomes):
    from tests.integration.test_audit_sitescore_retained_transaction import all_rows

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
        assert event.correlation_id == "owned-general-http-cid"
        assert isinstance(event.metadata["reason"], str) and event.metadata["reason"]
    delivery.audit_log.verify_chain().raise_for_tamper()
