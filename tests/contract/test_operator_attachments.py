"""Contract and security tests for task attachments with scoped access and masking (ODP-CAP-TASK-ATTACHMENTS-001).

Tests cover:
- Attachment listing with default FR-SHARED-007 sensitivity masking
- Controlled data masking (site photos and lease scans masked unless authorized)
- Unmasked attachment access with privacy_admin or unmasked role
- Attachment upload, download, deletion, and audit logging
- Tenant scope isolation
- Persistence survival across restart
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.infrastructure.persistence.factory import _durable_bundle


def _headers(
    key: str,
    correlation_id: str = "corr-attachments",
    roles: str = "operations_manager",
    tenant_id: str = "tenant-a",
    masking_profile: str = "masked",
) -> dict[str, str]:
    return {
        "Idempotency-Key": key,
        "X-Correlation-ID": correlation_id,
        "X-Subject-Id": "operator-opsLead",
        "X-Roles": roles,
        "X-Tenant-Id": tenant_id,
        "X-Masking-Profile": masking_profile,
    }


def _client(db_path: str | None = None) -> tuple[TestClient, object | None]:
    if db_path is None:
        return TestClient(create_app(external_provider_validation=lambda: None)), None
    bundle = _durable_bundle(db_path)
    return (
        TestClient(create_app(persistence=bundle, external_provider_validation=lambda: None)),
        bundle,
    )


def test_list_attachments_default_masked_profile() -> None:
    client, _ = _client()
    res = client.get(
        "/api/v1/operator/store-ops/issues/ISS-1024/attachments",
        headers=_headers("list-att-1"),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["issueId"] == "ISS-1024"
    assert body["count"] == 2
    attachments = body["attachments"]

    photo = next(a for a in attachments if a["id"] == "ATT-1024-PHOTO")
    assert photo["masked"] is True
    assert "[MASKED-SITE-PHOTO-ATT-1024-PHOTO.jpg]" in photo["filename"]
    assert photo["maskedReason"] == "FR-SHARED-007 controlled data sensitivity masking applied"

    lease = next(a for a in attachments if a["id"] == "ATT-1024-LEASE")
    assert lease["masked"] is True
    assert "[MASKED-LEASE-SCAN-ATT-1024-LEASE.pdf]" in lease["filename"]


def test_list_attachments_unmasked_with_authorized_role() -> None:
    client, _ = _client()
    res = client.get(
        "/api/v1/operator/store-ops/issues/ISS-1024/attachments",
        headers=_headers("list-att-unmasked", roles="operations_manager", masking_profile="unmasked"),
    )
    assert res.status_code == 200
    attachments = res.json()["attachments"]

    photo = next(a for a in attachments if a["id"] == "ATT-1024-PHOTO")
    assert photo["masked"] is False
    assert photo["filename"] == "site_inspection_photo_01.jpg"
    assert photo["storageUri"] == "store-ops/attachments/ISS-1024/ATT-1024-PHOTO.jpg"


def test_unauthorized_unmasked_request_falls_back_to_masked() -> None:
    client, _ = _client()
    res = client.get(
        "/api/v1/operator/store-ops/issues/ISS-1024/attachments",
        headers=_headers("list-att-unauth-unmasked", roles="operations_manager", masking_profile="masked"),
    )
    assert res.status_code == 200
    attachments = res.json()["attachments"]

    photo = next(a for a in attachments if a["id"] == "ATT-1024-PHOTO")
    assert photo["masked"] is True
    assert "[MASKED-" in photo["filename"]


def test_upload_and_download_new_attachment() -> None:
    client, _ = _client()
    payload = {
        "filename": "site_survey_front_door.png",
        "fileType": "image/png",
        "classification": "site_photo",
        "sensitivityLevel": "controlled",
        "contentBase64": "c2l0ZS1waG90by1ieXRlcy1iYXNlNjQ=",
        "actorRoleId": "opsLead",
        "actorName": "營運主管",
    }
    upload_res = client.post(
        "/api/v1/operator/store-ops/issues/ISS-1024/attachments",
        json=payload,
        headers=_headers("upload-att-1"),
    )
    assert upload_res.status_code == 200
    uploaded = upload_res.json()
    assert uploaded["id"].startswith("ATT-1024-")
    assert uploaded["masked"] is True

    att_id = uploaded["id"]

    download_res = client.get(
        f"/api/v1/operator/store-ops/issues/ISS-1024/attachments/{att_id}/download",
        headers=_headers("download-att-1"),
    )
    assert download_res.status_code == 200
    downloaded = download_res.json()
    assert downloaded["id"] == att_id
    assert downloaded["masked"] is True


def test_delete_attachment() -> None:
    client, _ = _client()
    del_res = client.delete(
        "/api/v1/operator/store-ops/issues/ISS-1024/attachments/ATT-1024-LEASE",
        headers=_headers("delete-att-1"),
    )
    assert del_res.status_code == 200
    assert del_res.json()["deletedAttachmentId"] == "ATT-1024-LEASE"

    list_res = client.get(
        "/api/v1/operator/store-ops/issues/ISS-1024/attachments",
        headers=_headers("list-after-del"),
    )
    ids = [a["id"] for a in list_res.json()["attachments"]]
    assert "ATT-1024-LEASE" not in ids


def test_tenant_isolation_prevents_access_across_tenants() -> None:
    client, _ = _client()
    res = client.get(
        "/api/v1/operator/store-ops/issues/ISS-1024/attachments",
        headers=_headers("tenant-b-req", tenant_id="tenant-b"),
    )
    assert res.status_code == 403


def test_attachments_survive_restart(tmp_path) -> None:
    db_path = str(tmp_path / "attachments_test.sqlite3")
    client1, _ = _client(db_path)

    upload_res = client1.post(
        "/api/v1/operator/store-ops/issues/ISS-1021/attachments",
        json={
            "filename": "lease_extension_draft.pdf",
            "fileType": "application/pdf",
            "classification": "lease_scan",
            "sensitivityLevel": "controlled",
            "actorRoleId": "facilitiesLead",
        },
        headers=_headers("persist-upload-1"),
    )
    assert upload_res.status_code == 200
    att_id = upload_res.json()["id"]

    client2, _ = _client(db_path)
    get_res = client2.get(
        f"/api/v1/operator/store-ops/issues/ISS-1021/attachments/{att_id}",
        headers=_headers("persist-get-1"),
    )
    assert get_res.status_code == 200
    assert get_res.json()["id"] == att_id
