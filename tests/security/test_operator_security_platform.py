from __future__ import annotations

import json

from fastapi import status
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.audit.events import InMemoryAuditLog
from shared.audit.policy import SECURITY_EVENT_TYPE

OPS_HEADERS = {
    "X-Subject-Id": "operator-ops-lead",
    "X-Roles": "operations_manager",
    "X-Tenant-Id": "tenant-a",
    "X-Operator-Role": "ops-lead",
}


def _client(audit_log: InMemoryAuditLog | None = None) -> TestClient:
    return TestClient(
        create_app(
            audit_log=audit_log,
            external_provider_validation=lambda: None,
        )
    )


def _security_denials(audit_log: InMemoryAuditLog) -> list:
    return [
        event
        for event in audit_log.list_events()
        if event.event_type == SECURITY_EVENT_TYPE and event.outcome == "deny"
    ]


def test_operator_protected_read_requires_authenticated_principal() -> None:
    audit_log = InMemoryAuditLog()
    client = _client(audit_log)

    response = client.get(
        "/api/v1/operator/bootstrap",
        headers={"X-Correlation-Id": "corr-operator-missing-auth"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    denials = _security_denials(audit_log)
    assert denials
    assert denials[-1].actor == "anonymous"
    assert denials[-1].metadata["policy_id"] == "authenticated"


def test_operator_authenticated_principal_without_scope_is_forbidden() -> None:
    audit_log = InMemoryAuditLog()
    client = _client(audit_log)

    response = client.get(
        "/api/v1/operator/bootstrap",
        headers={
            "X-Subject-Id": "operator-no-role",
            "X-Tenant-Id": "tenant-a",
            "X-Correlation-Id": "corr-operator-no-role",
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    denials = _security_denials(audit_log)
    assert denials
    assert denials[-1].actor == "operator-no-role"
    assert denials[-1].metadata["policy_id"] == "operator.role"


def test_operator_wrong_tenant_cannot_probe_entity_existence() -> None:
    audit_log = InMemoryAuditLog()
    client = _client(audit_log)
    missing_issue_path = "/api/v1/operator/store-ops/issues/ISS-NOT-REAL/evidence"

    denied = client.get(
        missing_issue_path,
        headers={
            **OPS_HEADERS,
            "X-Tenant-Id": "tenant-b",
            "X-Correlation-Id": "corr-operator-wrong-tenant",
        },
    )
    same_tenant_missing = client.get(
        missing_issue_path,
        headers={**OPS_HEADERS, "X-Correlation-Id": "corr-operator-same-tenant"},
    )

    assert denied.status_code == status.HTTP_403_FORBIDDEN
    assert same_tenant_missing.status_code == status.HTTP_404_NOT_FOUND
    denials = _security_denials(audit_log)
    assert denials
    assert denials[-1].metadata["policy_id"] == "operator.tenant_isolation"


def test_store_ops_camera_purpose_audit_excludes_media_secrets() -> None:
    audit_log = InMemoryAuditLog()
    client = _client(audit_log)
    headers = {
        **OPS_HEADERS,
        "Idempotency-Key": "security-camera-purpose-001",
        "X-Correlation-Id": "corr-operator-camera-purpose",
    }
    payload = {
        "purpose": "payment incident quality audit",
        "cameraLocation": "台北信義 A11 counter camera",
        "timeWindow": "2026-07-04T20:00Z/2026-07-04T21:00Z",
        "retentionHours": 24,
        "privacyAcknowledged": True,
        "auditNote": "Purpose-limited review for ISS-1024.",
        "mediaSecret": "sk-live-camera-token",
        "signedPlaybackUrl": "https://media.example.invalid/secret-playback",
    }

    first = client.post(
        "/api/v1/operator/store-ops/issues/ISS-1024/camera-purpose",
        json=payload,
        headers=headers,
    )
    replay = client.post(
        "/api/v1/operator/store-ops/issues/ISS-1024/camera-purpose",
        json=payload,
        headers=headers,
    )

    assert first.status_code == status.HTTP_200_OK
    assert replay.status_code == status.HTTP_200_OK
    assert replay.json()["idempotentReplay"] is True

    purpose_events = [
        event
        for event in audit_log.list_events(correlation_id="corr-operator-camera-purpose")
        if event.event_type == "operator.store_ops.camera_purpose"
    ]
    assert len(purpose_events) == 1
    metadata = purpose_events[0].metadata
    assert metadata["purpose"] == payload["purpose"]
    assert metadata["idempotencyKey"] == "security-camera-purpose-001"
    serialized_metadata = json.dumps(metadata, ensure_ascii=False)
    assert "sk-live-camera-token" not in serialized_metadata
    assert "secret-playback" not in serialized_metadata
    assert "signedPlaybackUrl" not in metadata
    assert "mediaSecret" not in metadata
    assert "auditNote" not in metadata
