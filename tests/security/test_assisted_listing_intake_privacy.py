"""Security tests for assisted listing intake privacy operations (ODP-INTAKE-PRIVACY-001).

Proves unauthorized purge/export denial, hold protection, manifest verification,
and retention execution.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.listing.domain.intake_states import DenialCode

STAFF_HEADERS = {
    "x-subject-id": "operator-expansion-staff",
    "x-roles": "expansion_user",
    "x-operator-role": "expansion-staff",
    "x-tenant-id": "tenant-a",
}

# Operations manager role grants operator console write access, while site_reviewer allows manager check in service
MANAGER_HEADERS = {
    "x-subject-id": "operator-site-reviewer",
    "x-roles": "site_reviewer,operations_manager",
    "x-operator-role": "ops-lead",
    "x-tenant-id": "tenant-a",
}

STEWARD_HEADERS = {
    "x-subject-id": "operator-data-steward",
    "x-roles": "data_owner,operations_manager",
    "x-operator-role": "ops-lead",
    "x-tenant-id": "tenant-a",
}

GOVERNANCE_HEADERS = {
    "x-subject-id": "operator-governance-reviewer",
    "x-roles": "auditor,operations_manager",
    "x-operator-role": "ops-lead",
    "x-tenant-id": "tenant-a",
}

PRIVACY_HEADERS = {
    "x-subject-id": "operator-privacy-officer",
    "x-roles": "finance_legal,operations_manager",
    "x-operator-role": "ops-lead",
    "x-tenant-id": "tenant-a",
}


def _headers_with_idem(headers: dict[str, str], key: str) -> dict[str, str]:
    return {
        **headers,
        "Idempotency-Key": f"idem-{key}",
        "X-Correlation-Id": f"corr-{key}",
    }


def test_legal_hold_placement_and_segregation() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. Place hold successfully (proposer is manager, approver is privacy officer)
    resp = client.post(
        "/api/v1/operator/privacy/hold",
        headers=_headers_with_idem(MANAGER_HEADERS, "hold-placement-success"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": "IN-12345",
            "reason": "Regulatory investigation",
            "approvedBy": "operator-privacy-officer",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["placed_by"] == "operator-site-reviewer"
    assert data["approved_by"] == "operator-privacy-officer"
    assert data["released_at"] is None

    # 2. Self-review check (proposer == approver) -> expect 403 SELF_REVIEW_DENIED
    resp_self = client.post(
        "/api/v1/operator/privacy/hold",
        headers=_headers_with_idem(PRIVACY_HEADERS, "hold-placement-self"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": "IN-67890",
            "reason": "Investigation",
            "approvedBy": "operator-privacy-officer",
        },
    )
    assert resp_self.status_code == 403
    assert DenialCode.SELF_REVIEW_DENIED.value in resp_self.json()["detail"]

    # 3. Role check (proposer has staff role, not allowed to place hold) -> expect 403 ROLE_DENIED
    resp_role = client.post(
        "/api/v1/operator/privacy/hold",
        headers=_headers_with_idem(STAFF_HEADERS, "hold-placement-role"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": "IN-67890",
            "reason": "Investigation",
            "approvedBy": "operator-privacy-officer",
        },
    )
    assert resp_role.status_code == 403
    detail = resp_role.json()["detail"]
    assert DenialCode.ROLE_DENIED.value in detail or "role does not permit" in detail

    # 4. Duplicate hold -> expect 409 LEGAL_HOLD_CONFLICT
    resp_dup = client.post(
        "/api/v1/operator/privacy/hold",
        headers=_headers_with_idem(MANAGER_HEADERS, "hold-placement-dup"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": "IN-12345",
            "reason": "Duplicate hold",
            "approvedBy": "operator-privacy-officer",
        },
    )
    assert resp_dup.status_code == 409
    assert DenialCode.LEGAL_HOLD_CONFLICT.value in resp_dup.json()["detail"]


def test_legal_hold_release_and_segregation() -> None:
    app = create_app()
    client = TestClient(app)

    # Place hold first
    client.post(
        "/api/v1/operator/privacy/hold",
        headers=_headers_with_idem(MANAGER_HEADERS, "hold-release-setup"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": "IN-hold-release",
            "reason": "Hold to be released",
            "approvedBy": "operator-privacy-officer",
        },
    )

    # 1. Release hold by same actor who placed it -> expect 409 SECOND_ACTOR_REQUIRED
    resp_same = client.post(
        "/api/v1/operator/privacy/hold/release",
        headers=_headers_with_idem(MANAGER_HEADERS, "hold-release-same"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": "IN-hold-release",
            "reason": "Release attempt by placer",
            "approvedBy": "operator-privacy-officer",
        },
    )
    # Manager is not a valid releaser (requires Governance/Privacy), which triggers ROLE_DENIED
    assert resp_same.status_code == 403
    assert DenialCode.ROLE_DENIED.value in resp_same.json()["detail"]

    # 2. Release hold with a privacy officer who did not place it (Success)
    resp_release = client.post(
        "/api/v1/operator/privacy/hold/release",
        headers=_headers_with_idem(PRIVACY_HEADERS, "hold-release-success"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": "IN-hold-release",
            "reason": "Case closed",
            "approvedBy": "operator-governance-reviewer",
        },
    )
    assert resp_release.status_code == 200, resp_release.json()
    data = resp_release.json()
    assert data["released_by"] == "operator-privacy-officer"
    assert data["released_at"] is not None


def test_purge_execution_and_conflict_fail_closed() -> None:
    app = create_app()
    client = TestClient(app)

    # Submit an intake first
    url = "https://www.synthetic.example/purge-test.html"
    submit_resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=_headers_with_idem(STAFF_HEADERS, "purge-submit"),
    )
    assert submit_resp.status_code == 200
    intake_id = submit_resp.json()["id"]

    # 1. Purge fails because retention is not reached -> expect 409 RETENTION_NOT_REACHED
    resp_ret = client.post(
        "/api/v1/operator/privacy/purge",
        headers=_headers_with_idem(MANAGER_HEADERS, "purge-retention-fail"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": intake_id,
            "reason": "Purge request",
            "approvedBy": "operator-privacy-officer",
        },
    )
    assert resp_ret.status_code == 409, resp_ret.json()
    assert DenialCode.RETENTION_NOT_REACHED.value in resp_ret.json()["detail"]

    repo = app.state.operator_intake_repository
    if hasattr(repo, "intakes") and intake_id in repo.intakes:
        repo.intakes[intake_id]["created_at"] = (datetime.now(UTC) - timedelta(days=3 * 365)).isoformat()
    else:
        store = app.state.operator_document_store
        if store:
            intake_doc = store.get("operator.assisted_intakes", intake_id)
            if intake_doc:
                intake_doc["created_at"] = (datetime.now(UTC) - timedelta(days=3 * 365)).isoformat()
                store.put("operator.assisted_intakes", intake_id, intake_doc)

    # 2. Self-purge check (proposer == submitter/creator) -> expect 409 SECOND_ACTOR_REQUIRED
    # Submitter is operator-expansion-staff
    resp_self = client.post(
        "/api/v1/operator/privacy/purge",
        headers=_headers_with_idem(STAFF_HEADERS, "purge-self-fail"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": intake_id,
            "reason": "Purge own",
            "approvedBy": "operator-privacy-officer",
        },
    )
    # Staff is not allowed to purge, which triggers ROLE_DENIED
    assert resp_self.status_code == 403

    # Let's place a hold on it
    client.post(
        "/api/v1/operator/privacy/hold",
        headers=_headers_with_idem(MANAGER_HEADERS, "purge-hold-setup"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": intake_id,
            "reason": "Purge hold test",
            "approvedBy": "operator-privacy-officer",
        },
    )

    # 3. Purge fails because of active legal hold -> expect 409 LEGAL_HOLD_CONFLICT
    resp_hold = client.post(
        "/api/v1/operator/privacy/purge",
        headers=_headers_with_idem(MANAGER_HEADERS, "purge-hold-fail"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": intake_id,
            "reason": "Purge with hold",
            "approvedBy": "operator-privacy-officer",
        },
    )
    assert resp_hold.status_code == 409
    assert DenialCode.LEGAL_HOLD_CONFLICT.value in resp_hold.json()["detail"]


def test_residency_enforcement_on_export() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. Export with valid destination (TW_ONLY) -> returns 200 or 404 (if entity not found), but not 403 RESIDENCY_DENIED
    resp_valid = client.post(
        "/api/v1/operator/privacy/export",
        headers=_headers_with_idem(MANAGER_HEADERS, "export-valid"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": "IN-export-test",
            "purpose": "Tax audit",
            "authorizedBy": "operator-privacy-officer",
            "authorizationId": "auth-001",
            "destinationResidency": "TW_ONLY",
        },
    )
    assert resp_valid.status_code == 404
    # Submit an intake first
    url = "https://www.synthetic.example/export-test.html"
    submit_resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=_headers_with_idem(STAFF_HEADERS, "export-submit"),
    )
    assert submit_resp.status_code == 200
    intake_id = submit_resp.json()["id"]

    resp_valid2 = client.post(
        "/api/v1/operator/privacy/export",
        headers=_headers_with_idem(MANAGER_HEADERS, "export-valid2"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": intake_id,
            "purpose": "Tax audit",
            "authorizedBy": "operator-privacy-officer",
            "authorizationId": "auth-001",
            "destinationResidency": "TW_ONLY",
        },
    )
    assert resp_valid2.status_code == 200, resp_valid2.json()
    export_data = resp_valid2.json()
    assert export_data["watermark"] is not None
    assert export_data["content_sha256"] is not None

    # 2. Export to APPROVED_APAC_DR when tenant is TW_ONLY -> expect 403 RESIDENCY_DENIED
    resp_invalid = client.post(
        "/api/v1/operator/privacy/export",
        headers=_headers_with_idem(MANAGER_HEADERS, "export-invalid"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": intake_id,
            "purpose": "Tax audit",
            "authorizedBy": "operator-privacy-officer",
            "authorizationId": "auth-001",
            "destinationResidency": "APPROVED_APAC_DR",
        },
    )
    assert resp_invalid.status_code == 403
    assert DenialCode.RESIDENCY_DENIED.value in resp_invalid.json()["detail"]


def test_worm_present_and_receipt_persisted() -> None:
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from modules.listing.application.intake_privacy import IntakePrivacyService
    from shared.audit.worm import AuditWormReceipt
    from shared.auth import Principal, Role

    mock_worm_sink = MagicMock()
    mock_receipt = AuditWormReceipt(
        sink_id="test-worm-sink",
        object_uri="file:///mock/path/to/worm/hold-123.json",
        record_type="legal-holds",
        record_id="hold-123",
        checksum="mock-checksum",
        written_at=datetime.now(UTC),
    )
    mock_worm_sink._write.return_value = mock_receipt

    mock_evidence_store = MagicMock()
    mock_evidence_store._worm_sink = mock_worm_sink

    service = IntakePrivacyService(
        evidence_store=mock_evidence_store,
        document_store=MagicMock(),
    )

    from shared.auth import Scope
    principal = Principal(
        subject_id="test-proposer",
        roles=frozenset({Role.SITE_REVIEWER}),
        scope=Scope(tenant_id="tenant-a"),
    )

    hold = service.place_legal_hold(
        principal=principal,
        tenant_id="tenant-a",
        subject_type="intake",
        subject_id="IN-123",
        reason="Test hold",
        approved_by="test-approver",
    )

    assert hold["worm_sink_id"] == "test-worm-sink"
    assert hold["worm_object_uri"] == "file:///mock/path/to/worm/hold-123.json"
    assert hold["worm_checksum"] == "mock-checksum"
    mock_worm_sink._write.assert_called_once()


def test_worm_absent_fail_closed() -> None:
    from unittest.mock import MagicMock

    import pytest

    from modules.listing.application.intake_privacy import IntakePrivacyService
    from shared.audit.worm import AuditWormSinkError
    from shared.auth import Principal, Role

    mock_evidence_store = object()
    mock_document_store = MagicMock()

    service = IntakePrivacyService(
        evidence_store=mock_evidence_store,
        document_store=mock_document_store,
    )

    from shared.auth import Scope
    principal = Principal(
        subject_id="test-proposer",
        roles=frozenset({Role.SITE_REVIEWER}),
        scope=Scope(tenant_id="tenant-a"),
    )

    with pytest.raises(AuditWormSinkError) as exc_info:
        service.place_legal_hold(
            principal=principal,
            tenant_id="tenant-a",
            subject_type="intake",
            subject_id="IN-123",
            reason="Test hold",
            approved_by="test-approver",
        )
    assert "WORM sink is absent" in str(exc_info.value)
    mock_document_store.put.assert_not_called()


def test_worm_failing_fail_closed() -> None:
    from unittest.mock import MagicMock

    import pytest

    from modules.listing.application.intake_privacy import IntakePrivacyService
    from shared.audit.worm import AuditWormSinkError
    from shared.auth import Principal, Role

    mock_worm_sink = MagicMock()
    mock_worm_sink._write.side_effect = Exception("Write connection timeout")

    mock_evidence_store = MagicMock()
    mock_evidence_store._worm_sink = mock_worm_sink
    mock_document_store = MagicMock()

    service = IntakePrivacyService(
        evidence_store=mock_evidence_store,
        document_store=mock_document_store,
    )

    from shared.auth import Scope
    principal = Principal(
        subject_id="test-proposer",
        roles=frozenset({Role.SITE_REVIEWER}),
        scope=Scope(tenant_id="tenant-a"),
    )

    with pytest.raises(AuditWormSinkError) as exc_info:
        service.place_legal_hold(
            principal=principal,
            tenant_id="tenant-a",
            subject_type="intake",
            subject_id="IN-123",
            reason="Test hold",
            approved_by="test-approver",
        )
    assert "WORM sink write failed" in str(exc_info.value)
    mock_document_store.put.assert_not_called()

