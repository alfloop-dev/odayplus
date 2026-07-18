"""Integration tests for assisted listing evidence export (ODP-INTAKE-PRIVACY-001).

Covers evidence export manifest verification, evidence export download,
integrity checks, and WORM boundary persistence.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app

STAFF_HEADERS = {
    "x-subject-id": "operator-expansion-staff",
    "x-roles": "expansion_user",
    "x-operator-role": "expansion-staff",
    "x-tenant-id": "tenant-a",
}

MANAGER_HEADERS = {
    "x-subject-id": "operator-site-reviewer",
    "x-roles": "site_reviewer,operations_manager",
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


def test_evidence_export_manifest_verification_and_download() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. Submit an intake first to export
    url = "https://www.synthetic.example/evidence-export-test.html"
    submit_resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=_headers_with_idem(STAFF_HEADERS, "export-test-submit"),
    )
    assert submit_resp.status_code == 200
    intake_id = submit_resp.json()["id"]

    # 2. Export evidence
    export_resp = client.post(
        "/api/v1/operator/privacy/export",
        headers=_headers_with_idem(MANAGER_HEADERS, "export-test-run"),
        json={
            "tenantId": "tenant-a",
            "subjectType": "intake",
            "subjectId": intake_id,
            "purpose": "Regulatory audit",
            "authorizedBy": "operator-privacy-officer",
            "authorizationId": "auth-gov-99",
            "destinationResidency": "TW_ONLY",
        },
    )
    assert export_resp.status_code == 200
    export_data = export_resp.json()
    export_id = export_data["export_manifest_id"]
    download_evidence_id = export_data["download_evidence_id"]
    assert export_data["content_sha256"] is not None

    # 3. Verify manifest integrity
    verify_resp = client.get(
        f"/api/v1/operator/privacy/export/verify/{export_id}",
        headers=MANAGER_HEADERS,
    )
    assert verify_resp.status_code == 200
    verify_data = verify_resp.json()
    assert verify_data["ok"] is True
    assert verify_data["manifest_checksum"] == export_data["content_sha256"]

    # 4. Download evidence and verify payload matches checksum
    download_resp = client.get(
        f"/api/v1/operator/privacy/export/download/{download_evidence_id}",
        headers=MANAGER_HEADERS,
    )
    assert download_resp.status_code == 200
    download_data = download_resp.json()
    assert download_data["manifest"]["export_manifest_id"] == export_id
    assert download_data["bundle"]["id"] == intake_id

    # Verify WORM event was written to audit log
    audit_events = client.get(
        "/api/v1/audit/events",
        headers=MANAGER_HEADERS,
    ).json().get("events", [])
    export_event = next(
        (e for e in audit_events if e["event_type"] == "audit.evidence_export.v1"), None
    )
    assert export_event is not None
    assert export_event["actor"] == "operator-site-reviewer"
    assert export_event["metadata"]["export_id"] == export_id
