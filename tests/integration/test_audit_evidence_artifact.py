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
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    SigningKey,
    encode_compact_jwt,
)
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


def _client(*, boundary=None):
    store = InMemoryEvidenceBundleStore()
    audit_log = InMemoryAuditLog()
    app = FastAPI()
    app.include_router(create_audit_router(audit_log=audit_log, evidence_store=store, boundary=boundary))
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
    # Metadata history remains VIEW-readable; the full JSON is evidence content.
    assert client.get(f"/audit/evidence/exports/{export_id}", headers=viewer).status_code == 403
    assert client.get("/audit/evidence/exports", headers=viewer).json()["exports"][0]["export_id"] == export_id
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
@pytest.mark.parametrize(
    ("sensitive", "classification"),
    [(True, "restricted"), (False, "restricted"), (False, " Restricted ")],
)
def test_sensitive_download_requires_verified_authority_not_body_strings(
    format, sensitive, classification
):
    client, _, body, _ = _client()
    body.update(
        {
            "sensitive": sensitive,
            "data_classification": classification,
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


def _audit_owner(label):
    """Synthetic HMAC credentials verified by the real auth boundary."""
    subject = f"auditor-{label}"
    issuer = f"https://audit-{label}.invalid"
    key = SigningKey(kid=label, algorithm="HS256", secret=f"audit-{label}-unit-key".encode())
    mappings = {subject: {"roles": ["auditor"], "scope": {"tenant_id": f"tenant-{label}"}}}
    boundary = AuthenticationBoundary(AuthBoundaryConfig(
        issuer=issuer, audiences=frozenset({"audit-unit-api"}),
        signing_keys={key.kid: key}, principal_mappings=mappings,
    ))
    now = datetime.now(UTC)
    token = encode_compact_jwt({
        "sub": subject, "iss": issuer, "aud": "audit-unit-api",
        "iat": now.timestamp(), "exp": (now + timedelta(minutes=5)).timestamp(),
    }, key)
    return boundary, {"Authorization": f"Bearer {token}"}, mappings


@pytest.mark.parametrize("name,value", [
    ("ODP_PRODUCT_MODE", "production"),
    ("ODP_PRODUCT_MODE", "PROD"),
    ("ODP_PRODUCT_MODE", "live"),
    ("ODP_DEPLOY_ENV", "production"),
    ("ODP_DEPLOY_ENV", "staging"),
    ("APP_ENV", "prod"),
    ("NODE_ENV", "production"),
    ("ODP_REQUIRE_LIVE_DATA", "true"),
])
def test_every_server_live_marker_requires_canonical_input_and_full_content_admission(
    monkeypatch, name, value,
):
    # Isolate one actual server marker without a higher-priority deployment
    # variable from the original test environment shadowing it.
    for marker in (
        "ODP_PRODUCT_MODE", "ODP_DEPLOY_ENV", "ODAY_ENV", "ODP_ENV", "APP_ENV",
        "ENVIRONMENT", "NODE_ENV", "ODP_REQUIRE_LIVE_DATA",
    ):
        monkeypatch.delenv(marker, raising=False)
    boundary, headers, _ = _audit_owner("live-marker")
    client, store, body, _ = _client(boundary=boundary)
    client.headers.update(headers)
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    export_id = created.json()["export_id"]
    count = len(store.list_all())
    monkeypatch.setenv(name, value)

    rejected = client.post("/audit/evidence/export", json=body)
    assert rejected.status_code == 503
    assert rejected.json()["detail"] == "CANONICAL_EVIDENCE_INPUT_REQUIRED"
    assert len(store.list_all()) == count
    for suffix in ("", "/download?format=zip", "/download?format=pdf"):
        response = client.get(f"/audit/evidence/exports/{export_id}{suffix}")
        assert response.status_code == 503
        assert response.json()["detail"] == "CANONICAL_EVIDENCE_DOWNLOAD_ADMISSION_REQUIRED"
        assert "bundle" not in response.json()
    # Owned metadata history does not silently become full content.
    history = client.get("/audit/evidence/exports").json()["exports"]
    assert [row["export_id"] for row in history] == [export_id]
    assert all("bundle" not in row for row in history)


def test_same_verified_owner_losing_export_can_read_only_metadata_history():
    boundary, headers, mappings = _audit_owner("role-change")
    client, _, body, _ = _client(boundary=boundary)
    client.headers.update(headers)
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    export_id = created.json()["export_id"]
    mappings["auditor-role-change"]["roles"] = ["operations_manager"]

    assert client.get(f"/audit/evidence/exports/{export_id}").status_code == 403
    assert client.get(f"/audit/evidence/exports/{export_id}/download").status_code == 403
    history = client.get("/audit/evidence/exports")
    assert history.status_code == 200
    assert [row["export_id"] for row in history.json()["exports"]] == [export_id]
    assert all("bundle" not in row for row in history.json()["exports"])


@pytest.mark.parametrize("classification,sensitive", [
    ("internal", True), ("restricted", False), ("highly_restricted", False),
    ("secret", False), ("top_secret", False),
])
def test_full_json_requires_independent_sensitive_authority(classification, sensitive):
    client, _, body, audit = _client()
    body.update({
        "data_classification": classification, "sensitive": sensitive,
        "purpose_scope": "synthetic-sensitive-test",
        "authorized_by": "unverified-authorization-name",
        "authorization_id": "unverified-authorization-reference",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    })
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    export_id = created.json()["export_id"]

    for suffix in ("", "/download"):
        response = client.get(f"/audit/evidence/exports/{export_id}{suffix}")
        assert response.status_code == 403
        assert response.json()["detail"] == "SENSITIVE_EVIDENCE_DOWNLOAD_AUTHORITY_REQUIRED"
        assert "bundle" not in response.json()
    assert not any(event.event_type == "audit.evidence_download.v1" for event in audit.list_events())


@pytest.mark.parametrize("condition", ["expired", "unmasked", "purged", "tampered", "malformed"])
def test_full_json_and_download_reject_unavailable_retained_content(condition):
    client, store, body, audit = _client()
    if condition == "expired":
        body["expires_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    elif condition == "unmasked":
        body["masking_profile"] = "unmasked"
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    export_id = created.json()["export_id"]
    if condition == "purged":
        assert store.purge_expired(datetime.now(UTC) + timedelta(days=4000), context=GovernedEvidenceOperation(
            actor="synthetic-records-owner", role="records_manager",
            reason="Synthetic purge control", correlation_id="synthetic-purge-control",
        )) == [export_id]
    elif condition == "tampered":
        record = store.get(export_id)
        # Deliberate corruption of this isolated in-memory test record.
        store._records[export_id] = replace(record, bundle={**record.bundle, "purpose": "tampered-private-content"})
    elif condition == "malformed":
        record = store.get(export_id)
        # A hash-valid historical record still needs the original archive's
        # structural checks before its raw JSON may be disclosed.
        store._records[export_id] = attach_retained_evidence_integrity(
            replace(record, bundle={**record.bundle, "audit_events": "not-an-array"}),
            sequence=record.sequence, previous_hash=record.previous_hash,
        )
    for suffix in ("", "/download?format=zip", "/download?format=pdf"):
        response = client.get(f"/audit/evidence/exports/{export_id}{suffix}")
        # Purge tombstones intentionally do not retain the HTTP owner boundary;
        # never assign the old owner anew or disclose it to another caller.
        assert response.status_code == (404 if condition in {"purged", "tampered"} else 410)
        assert "bundle" not in response.json()
        assert "tampered-private-content" not in response.text
    assert not any(event.event_type == "audit.evidence_download.v1" for event in audit.list_events())


def test_full_json_checks_without_rendering_and_audits_exact_response(monkeypatch):
    from apps.api.app.routes import audit as audit_routes

    client, _, body, audit = _client()
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    export_id = created.json()["export_id"]

    def no_renderer(_record):
        raise AssertionError("Full JSON must not render a PDF or ZIP")

    monkeypatch.setattr(audit_routes, "render_evidence_pdf", no_renderer)
    monkeypatch.setattr(audit_routes, "render_evidence_archive", no_renderer)
    response = client.get(f"/audit/evidence/exports/{export_id}")
    assert response.status_code == 200
    assert response.json()["bundle"]["export_id"] == export_id
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    events = [event for event in audit.list_events() if event.event_type == "audit.evidence_download.v1"]
    assert len(events) == 1
    assert events[0].metadata["format"] == "JSON"
    assert events[0].metadata["artifact_sha256"] == hashlib.sha256(response.content).hexdigest()
    assert events[0].metadata["artifact_bytes"] == len(response.content)


def test_retention_eligibility_and_legal_hold_are_not_authorization_expiry(monkeypatch):
    from apps.api.app.routes import audit as audit_routes

    client, store, body, _ = _client()
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    export_id = created.json()["export_id"]
    record = store.get(export_id)
    after_retention = record.retain_until + timedelta(days=1)

    class RetentionClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return after_retention if tz else after_retention.replace(tzinfo=None)

    monkeypatch.setattr(audit_routes, "datetime", RetentionClock)
    assert store.list_expired(after_retention)[0].export_id == export_id
    assert client.get(f"/audit/evidence/exports/{export_id}").status_code == 200
    store.apply_legal_hold(export_id, context=GovernedEvidenceOperation(
        actor="synthetic-legal-owner", role="legal", reason="Synthetic hold control",
        correlation_id="synthetic-hold-control",
    ))
    assert store.list_expired(after_retention) == []
    assert client.get(f"/audit/evidence/exports/{export_id}").status_code == 200


def test_audit_router_keeps_each_real_authentication_owner(monkeypatch):
    from apps.api.oday_api.security import dependencies

    owner_a, token_a, _ = _audit_owner("owner-a")
    owner_b, token_b, _ = _audit_owner("owner-b")
    current_owner = {"boundary": owner_a}
    monkeypatch.setattr(dependencies, "default_boundary", lambda: current_owner["boundary"])
    client_a, store_a, body_a, _ = _client()
    current_owner["boundary"] = owner_b
    client_b, store_b, body_b, _ = _client()
    created_a = client_a.post("/audit/evidence/export", json=body_a, headers=token_a)
    assert created_a.status_code == 201
    created_b = client_b.post("/audit/evidence/export", json=body_b, headers=token_b)
    assert created_b.status_code == 201
    assert client_a.get("/audit/evidence/exports", headers=token_b).status_code == 401
    assert client_b.get("/audit/evidence/exports", headers=token_a).status_code == 401
    assert [row.requested_by for row in store_a.list_all()] == ["auditor-owner-a"]
    assert [row.requested_by for row in store_b.list_all()] == ["auditor-owner-b"]


def test_old_header_router_cannot_adopt_a_later_real_owner(monkeypatch):
    from apps.api.oday_api.security import dependencies

    current_owner = {"boundary": None}
    monkeypatch.setattr(dependencies, "default_boundary", lambda: current_owner["boundary"])
    client, store, body, _ = _client()
    assert client.get("/audit/evidence/exports").status_code == 200
    owner, token, _ = _audit_owner("later-owner")
    current_owner["boundary"] = owner
    for headers in (_headers(), token):
        assert client.post("/audit/evidence/export", json=body, headers=headers).status_code == 401
        assert client.get("/audit/evidence/exports", headers=headers).status_code == 401
    assert store.list_all() == []


@pytest.mark.parametrize("headers", [
    _headers(), _headers(subject="foreign-reader"), _headers(tenant="foreign-tenant"),
])
def test_unverifiable_owner_never_reveals_corrupt_record_existence(headers):
    client, store, body, audit = _client()
    created = client.post("/audit/evidence/export", json=body)
    assert created.status_code == 201
    export_id = created.json()["export_id"]
    original = store.get(export_id)
    # Corrupt only the isolated test record. Neither its owner fields nor its
    # alleged boundary may become an authority after integrity verification fails.
    store._records[export_id] = replace(original, bundle={
        **original.bundle, "purpose": "corrupt-private-purpose",
    })
    for suffix in ("", "/download?format=zip", "/download?format=pdf"):
        corrupt = client.get(f"/audit/evidence/exports/{export_id}{suffix}", headers=headers)
        unknown = client.get("/audit/evidence/exports/unknown-audit-export" + suffix, headers=headers)
        assert corrupt.status_code == unknown.status_code == 404
        assert corrupt.json() == unknown.json() == {"detail": "retained evidence bundle not found"}
        assert "corrupt-private-purpose" not in corrupt.text
        assert "bundle" not in corrupt.json()
    assert not any(event.event_type == "audit.evidence_download.v1" for event in audit.list_events())
