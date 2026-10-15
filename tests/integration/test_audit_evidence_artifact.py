from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.audit import create_audit_router
from modules.opsboard.audit import AuditEvidenceExportService, EvidenceExportRequest
from modules.opsboard.audit.application.evidence_artifact import (
    EvidenceArtifactUnavailable,
    render_evidence_archive,
)
from modules.opsboard.audit.evidence_store import DurableEvidenceBundleStore
from shared.audit import InMemoryAuditLog
from shared.audit.persistence import (
    EvidenceIntegrityError,
    GovernedEvidenceOperation,
    InMemoryEvidenceBundleStore,
    attach_retained_evidence_integrity,
)
from shared.auth import Principal, Role
from shared.infrastructure.persistence.factory import _durable_bundle
from tests.integration._authz import auth_headers
from tests.integration.test_audit_evidence_export import _ready_card


def _retained(store=None, *, title="  =SUM(1,2)"):
    now = datetime.now(UTC)
    store = store or InMemoryEvidenceBundleStore()
    service = AuditEvidenceExportService(evidence_store=store)
    bundle = service.export(
        EvidenceExportRequest(
            program_id="synthetic-artifact-program",
            purpose="Synthetic artifact integrity verification",
            requested_by="synthetic-owner",
            from_time=now - timedelta(days=1),
            to_time=now + timedelta(days=1),
            correlation_ids=("synthetic-artifact-correlation",),
            export_scope="synthetic-only",
        ),
        decision_cards=(replace(_ready_card(now), title=title, rationale="中文理由\n第二行"),),
        generated_at=now,
    )
    return store.get(bundle.export_id)


def test_archive_preserves_evidence_hashes_counts_and_formula_values():
    record = _retained()
    artifact = render_evidence_archive(record)
    assert artifact.content == render_evidence_archive(record).content
    assert hashlib.sha256(artifact.content).hexdigest() == artifact.sha256
    with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {
            "bundle.json",
            "retained-evidence.json",
            "decision-cards.csv",
            "audit-events.csv",
            "subsidy-matrix.csv",
            "manifest.json",
        }
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["bundle_checksum"] == record.bundle_checksum
        for item in manifest["files"]:
            content = archive.read(item["path"])
            assert len(content) == item["bytes"]
            assert hashlib.sha256(content).hexdigest() == item["sha256"]
        assert json.loads(archive.read("bundle.json")) == record.bundle
        assert json.loads(archive.read("retained-evidence.json")) == record.to_dict()
        rows = list(csv.DictReader(io.StringIO(archive.read("decision-cards.csv").decode())))
        assert len(rows) == len(record.bundle["decision_cards"])
        assert rows[0]["title"] == "'  =SUM(1,2)"
        assert rows[0]["rationale"] == "中文理由\n第二行"
        assert (
            json.loads(rows[0]["lifecycle_refs"])
            == record.bundle["decision_cards"][0]["lifecycle_refs"]
        )
        matrix = list(csv.DictReader(io.StringIO(archive.read("subsidy-matrix.csv").decode())))
        assert len(matrix) == len(record.bundle["subsidy_matrix"])


def test_archive_reproduces_after_sqlite_restart(tmp_path):
    path = tmp_path / "artifact.sqlite"
    persistence = _durable_bundle(path)
    record = _retained(DurableEvidenceBundleStore(persistence.engine))
    original = render_evidence_archive(record)
    persistence.engine.close()
    restarted = _durable_bundle(path)
    try:
        restored = DurableEvidenceBundleStore(restarted.engine).get(record.export_id)
        assert render_evidence_archive(restored) == original
    finally:
        restarted.engine.close()


def test_archive_rejects_tampering_and_unmasked_content():
    record = _retained()
    tampered = replace(record, bundle={**record.bundle, "purpose": "tampered"})
    with pytest.raises(EvidenceIntegrityError):
        render_evidence_archive(tampered)
    unmasked = replace(
        record,
        bundle={
            **record.bundle,
            "export_governance": {
                **record.bundle["export_governance"],
                "masking_profile": "unmasked",
            },
        },
    )
    stamped = attach_retained_evidence_integrity(
        unmasked, sequence=record.sequence, previous_hash=record.previous_hash
    )
    with pytest.raises(EvidenceArtifactUnavailable, match="masked"):
        render_evidence_archive(stamped)


def _client():
    store = InMemoryEvidenceBundleStore()
    audit_log = InMemoryAuditLog()
    app = FastAPI()
    app.include_router(create_audit_router(audit_log=audit_log, evidence_store=store))
    client = TestClient(app, headers=_headers())
    now = datetime.now(UTC)
    body = {
        "program_id": "synthetic-download-program",
        "purpose": "Synthetic download verification",
        "requested_by": "spoofed-body-owner",
        "identity_boundary": "spoofed-boundary",
        "from_time": (now - timedelta(days=1)).isoformat(),
        "to_time": (now + timedelta(days=1)).isoformat(),
        "correlation_ids": ["synthetic-download-correlation"],
        "export_scope": "synthetic-only",
        "decision_cards": [_ready_card(now).to_dict()],
    }

    # The router also supports lightweight apps; production middleware normally
    # supplies this correlation state.
    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = "synthetic-http-correlation"
        return await call_next(request)

    return client, store, body, audit_log


def _headers(subject="synthetic-owner", tenant="synthetic-tenant"):
    return {**auth_headers(Role.AUDITOR, subject=subject), "x-tenant-id": tenant}


def test_download_binds_verified_subject_tenant_and_archive_digest():
    client, store, body, audit_log = _client()
    contract = client.get("/openapi.json").json()["paths"][
        "/audit/evidence/exports/{export_id}/download"
    ]["get"]
    assert contract["responses"]["200"]["content"]["application/zip"] == {
        "schema": {"type": "string", "format": "binary"}
    }
    response = client.post("/audit/evidence/export", json=body)
    assert response.status_code == 201
    result = response.json()
    assert result["requested_by"] == "synthetic-owner"
    assert (
        result["export_governance"]["identity_boundary"]
        == 'http-principal:{"subject_id":"synthetic-owner","tenant_id":"synthetic-tenant"}'
    )
    export_id = result["export_id"]
    download = client.get(f"/audit/evidence/exports/{export_id}/download")
    assert download.status_code == 200
    assert download.content == render_evidence_archive(store.get(export_id)).content
    assert download.headers["x-artifact-sha256"] == hashlib.sha256(download.content).hexdigest()
    assert download.headers["content-type"] == "application/zip"
    assert download.headers["cache-control"] == "private, no-store"
    assert "attachment" in download.headers["content-disposition"]
    events = [
        event
        for event in audit_log.list_events()
        if event.event_type == "audit.evidence_download.v1"
    ]
    assert len(events) == 1
    assert events[0].actor == "synthetic-owner"
    assert events[0].metadata["artifact_sha256"] == download.headers["x-artifact-sha256"]
    for headers in (_headers(subject="synthetic-other"), _headers(tenant="other-tenant")):
        assert (
            client.get(f"/audit/evidence/exports/{export_id}", headers=headers).status_code == 404
        )
        assert (
            client.get(f"/audit/evidence/exports/{export_id}/download", headers=headers).status_code
            == 404
        )
        assert client.get("/audit/evidence/exports", headers=headers).json()["exports"] == []
    viewer = {
        **auth_headers(Role.OPERATIONS_MANAGER, subject="synthetic-owner"),
        "x-tenant-id": "synthetic-tenant",
    }
    assert client.get(f"/audit/evidence/exports/{export_id}", headers=viewer).status_code == 200
    assert (
        client.get(f"/audit/evidence/exports/{export_id}/download", headers=viewer).status_code
        == 403
    )
    assert (
        len(
            [
                event
                for event in audit_log.list_events()
                if event.event_type == "audit.evidence_download.v1"
            ]
        )
        == 1
    )
    assert client.get("/audit/evidence/exports").json()["exports"][0]["export_id"] == export_id


@pytest.mark.parametrize("format", ["zip", "pdf"])
def test_sensitive_download_requires_verified_authority_not_body_strings(format):
    client, _, body, _ = _client()
    body.update(
        {
            "sensitive": True,
            "data_classification": "restricted",
            "purpose_scope": "synthetic-test",
            "authorized_by": "caller-invented-approver",
            "authorization_id": "caller-invented-receipt",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
    )
    response = client.post("/audit/evidence/export", json=body)
    assert response.status_code == 201
    download = client.get(
        f"/audit/evidence/exports/{response.json()['export_id']}/download",
        params={"format": format},
    )
    assert download.status_code == 403
    assert download.json()["detail"] == "SENSITIVE_EVIDENCE_DOWNLOAD_AUTHORITY_REQUIRED"


@pytest.mark.parametrize("format", ["zip", "pdf"])
def test_download_rejects_expired_authorization_and_unbound_legacy_record(format):
    client, store, body, _ = _client()
    body["expires_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    response = client.post("/audit/evidence/export", json=body)
    assert response.status_code == 201
    download = client.get(
        f"/audit/evidence/exports/{response.json()['export_id']}/download",
        params={"format": format},
    )
    assert download.status_code == 410
    legacy = _retained(store)
    assert client.get(f"/audit/evidence/exports/{legacy.export_id}").status_code == 404
    assert client.get(f"/audit/evidence/exports/{legacy.export_id}/download").status_code == 404


@pytest.mark.parametrize("title", ["=SUM(1,2)", "+cmd", "-cmd", "@cmd", "\tformula", " \r=cmd"])
def test_archive_csv_blocks_formula_prefixes_without_changing_json(title):
    record = _retained(title=title)
    with zipfile.ZipFile(io.BytesIO(render_evidence_archive(record).content)) as archive:
        row = next(csv.DictReader(io.StringIO(archive.read("decision-cards.csv").decode())))
        assert row["title"] == "'" + title
        assert json.loads(archive.read("bundle.json"))["decision_cards"][0]["title"] == title


def test_archive_rejects_retention_purge_tombstone():
    store = InMemoryEvidenceBundleStore()
    record = _retained(store)
    purged = store.purge_expired(
        datetime.now(UTC) + timedelta(days=4000),
        context=GovernedEvidenceOperation(
            actor="synthetic-records-manager",
            role="records_manager",
            reason="Synthetic retention test",
            correlation_id="synthetic-purge",
        ),
    )
    assert record.export_id in purged
    with pytest.raises(EvidenceArtifactUnavailable, match="purged"):
        render_evidence_archive(store.get(record.export_id))


@pytest.mark.parametrize("format", ["zip", "pdf"])
def test_production_rejects_caller_supplied_evidence_and_unadmitted_download(monkeypatch, format):
    from apps.api.oday_api.security import dependencies

    client, store, body, _ = _client()
    response = client.post("/audit/evidence/export", json=body)
    assert response.status_code == 201
    count = len(store.list_all())
    # Only the input-admission gate is under test here. This explicit synthetic
    # principal does not claim to validate production JWT authentication.
    monkeypatch.setattr(
        dependencies,
        "principal_from_headers",
        lambda *_args, **_kwargs: Principal(
            subject_id="synthetic-owner",
            roles=frozenset({Role.AUDITOR}),
        ),
    )
    monkeypatch.setenv("ODP_PRODUCT_MODE", "production")
    rejected = client.post("/audit/evidence/export", json=body)
    assert rejected.status_code == 503
    assert rejected.json()["detail"] == "CANONICAL_EVIDENCE_INPUT_REQUIRED"
    assert len(store.list_all()) == count
    download = client.get(
        f"/audit/evidence/exports/{response.json()['export_id']}/download",
        params={"format": format},
    )
    assert download.status_code == 503
    assert download.json()["detail"] == "CANONICAL_EVIDENCE_DOWNLOAD_ADMISSION_REQUIRED"
