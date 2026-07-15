"""Security tests for the product-shell API (ODP-PGAP-SHELL-001).

Proves the shell surface fails closed: anonymous callers get 401,
under-privileged callers get 403, every denial writes a security audit event,
and the franchisee portal is isolated from the operator console in both
directions.

Run:
    uv run pytest tests/security/test_operator_shell_security.py -x -v
"""

from __future__ import annotations

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.audit import InMemoryAuditLog
from shared.audit.policy import SECURITY_EVENT_TYPE

OPS_HEADERS = {
    "X-Subject-Id": "operator-ops-lead",
    "X-Roles": "operations_manager",
    "X-Tenant-Id": "tenant-a",
    "X-Operator-Role": "ops-lead",
}
AUDITOR_HEADERS = {
    "X-Subject-Id": "operator-pm-audit",
    "X-Roles": "auditor",
    "X-Tenant-Id": "tenant-a",
    "X-Operator-Role": "pm-audit",
}
FRANCHISEE_HEADERS = {
    "X-Subject-Id": "franchisee-001",
    "X-Roles": "franchisee",
    "X-Tenant-Id": "tenant-a",
}

READ_PATHS = [
    "/api/v1/operator/shell/home",
    "/api/v1/operator/shell/tasks",
    "/api/v1/operator/shell/notifications",
    "/api/v1/operator/shell/notifications/preferences",
    "/api/v1/operator/shell/search",
    "/api/v1/operator/shell/settings",
    "/api/v1/operator/shell/admin",
]

# (method, path, json body) for every state-changing shell endpoint.
WRITE_ENDPOINTS = [
    ("post", "/api/v1/operator/shell/tasks/ISS-1024/assignment", {"assigneeId": "x"}),
    ("post", "/api/v1/operator/shell/notifications/NTF-SLA-1024/acknowledgement", None),
    (
        "put",
        "/api/v1/operator/shell/notifications/preferences",
        {"channels": {"inApp": True}, "severityFloor": "info"},
    ),
    (
        "put",
        "/api/v1/operator/shell/admin/roles/cs-lead/workspaces",
        {"allowedWorkspaces": ["today"]},
    ),
    ("put", "/api/v1/operator/shell/settings", {"values": {"density": "compact"}}),
]


def _client(audit_log: InMemoryAuditLog | None = None) -> TestClient:
    return TestClient(create_app(audit_log=audit_log, external_provider_validation=lambda: None))


def _security_denials(audit_log: InMemoryAuditLog) -> list:
    return [
        event
        for event in audit_log.list_events()
        if event.event_type == SECURITY_EVENT_TYPE and event.outcome == "deny"
    ]


@pytest.mark.parametrize("path", READ_PATHS)
def test_anonymous_reads_are_denied(path: str) -> None:
    audit_log = InMemoryAuditLog()
    response = _client(audit_log).get(path)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    denials = _security_denials(audit_log)
    assert denials, f"{path} must write a security audit event on denial"
    assert denials[-1].actor == "anonymous"
    assert denials[-1].metadata["policy_id"] == "authenticated"


@pytest.mark.parametrize(("method", "path", "body"), WRITE_ENDPOINTS)
def test_anonymous_writes_are_denied(method: str, path: str, body: dict | None) -> None:
    audit_log = InMemoryAuditLog()
    response = getattr(_client(audit_log), method)(path, json=body)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert _security_denials(audit_log)


@pytest.mark.parametrize(("method", "path", "body"), WRITE_ENDPOINTS)
def test_view_only_role_cannot_write(method: str, path: str, body: dict | None) -> None:
    """An auditor holds operator_console VIEW but not UPDATE."""
    audit_log = InMemoryAuditLog()
    response = getattr(_client(audit_log), method)(path, headers=AUDITOR_HEADERS, json=body)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    denials = _security_denials(audit_log)
    assert denials[-1].metadata["policy_id"] == "rbac"


def test_auditor_may_read_but_not_reach_the_admin_surface() -> None:
    client = _client()
    assert client.get("/api/v1/operator/shell/tasks", headers=AUDITOR_HEADERS).status_code == 200
    # Admin is guarded by operator_console UPDATE, which an auditor lacks.
    assert client.get("/api/v1/operator/shell/admin", headers=AUDITOR_HEADERS).status_code == 403


def test_franchisee_cannot_reach_the_operator_console() -> None:
    """Role.FRANCHISEE holds no operator_console grant in either direction."""
    audit_log = InMemoryAuditLog()
    client = _client(audit_log)

    for path in READ_PATHS:
        response = client.get(path, headers=FRANCHISEE_HEADERS)
        assert response.status_code == status.HTTP_403_FORBIDDEN, path

    denials = _security_denials(audit_log)
    assert denials[-1].metadata["policy_id"] == "operator.role"


def test_operator_cannot_write_on_a_franchisees_behalf() -> None:
    """Operations holds franchisee_portal VIEW for support, never CREATE."""
    client = _client()

    assert client.get("/api/v1/operator/shell/franchisee", headers=OPS_HEADERS).status_code == 200
    assert (
        client.post(
            "/api/v1/operator/shell/franchisee/acknowledgement",
            headers=OPS_HEADERS,
            json={"notificationId": "NTF-SLA-1024"},
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.post(
            "/api/v1/operator/shell/franchisee/reports",
            headers=OPS_HEADERS,
            json={"category": "other", "message": "x"},
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_anonymous_franchisee_portal_is_denied() -> None:
    audit_log = InMemoryAuditLog()
    client = _client(audit_log)

    assert client.get("/api/v1/operator/shell/franchisee").status_code in {401, 403}
    assert client.post(
        "/api/v1/operator/shell/franchisee/reports",
        json={"category": "other", "message": "x"},
    ).status_code in {401, 403}
    assert _security_denials(audit_log)


def test_non_admin_operator_role_is_refused_the_admin_product_rule() -> None:
    """A role may hold operator_console UPDATE yet not be a shell admin.

    regional_supervisor maps to field-lead, which is outside ADMIN_ROLE_IDS.
    RBAC alone would let it through, so the service layer must answer 403.
    """
    client = _client()
    headers = {
        "X-Subject-Id": "operator-field-lead",
        "X-Roles": "operations_manager,regional_supervisor",
        "X-Tenant-Id": "tenant-a",
        "X-Operator-Role": "field-lead",
    }

    response = client.get("/api/v1/operator/shell/admin", headers=headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "營運主管" in response.json()["detail"]

    assign = client.post(
        "/api/v1/operator/shell/tasks/ISS-1024/assignment",
        headers={**headers, "Idempotency-Key": "k1"},
        json={"assigneeId": "operator-cs-lead"},
    )
    assert assign.status_code == status.HTTP_403_FORBIDDEN


def test_server_derives_role_and_ignores_a_spoofed_header() -> None:
    """X-Operator-Role outside the principal's roles is refused, not honoured."""
    audit_log = InMemoryAuditLog()
    response = _client(audit_log).get(
        "/api/v1/operator/shell/home",
        headers={**AUDITOR_HEADERS, "X-Operator-Role": "ops-lead"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert _security_denials(audit_log)[-1].metadata["policy_id"] == "operator.role_scope"
