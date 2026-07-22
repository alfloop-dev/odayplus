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
CS_HEADERS = {
    "X-Subject-Id": "operator-cs-lead",
    "X-Roles": "operations_manager",
    "X-Tenant-Id": "tenant-a",
    "X-Operator-Role": "cs-lead",
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


def test_operations_manager_can_select_narrow_cs_lead_persona() -> None:
    response = _client().get("/api/v1/operator/bootstrap", headers=CS_HEADERS)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["meta"]["role"]["id"] == "cs-lead"


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


def test_cross_actor_idempotency_replay_is_blocked() -> None:
    """Ensure idempotency replay is scoped by actor and does not bypass authorization."""
    client = _client()

    # 1. Task Assignment (operator write)
    # ops-lead assigns task: succeeds
    task_id = "ISS-1024"
    resp1 = client.post(
        f"/api/v1/operator/shell/tasks/{task_id}/assignment",
        headers={**OPS_HEADERS, "Idempotency-Key": "cross-actor-task"},
        json={"assigneeId": "operator-cs-lead"},
    )
    assert resp1.status_code == status.HTTP_200_OK

    # field-lead (not admin, lacks permission) repeats same endpoint/key: 403 Forbidden instead of 200
    field_lead_headers = {
        "X-Subject-Id": "operator-field-lead",
        "X-Roles": "operations_manager,regional_supervisor",
        "X-Tenant-Id": "tenant-a",
        "X-Operator-Role": "field-lead",
    }
    resp2 = client.post(
        f"/api/v1/operator/shell/tasks/{task_id}/assignment",
        headers={**field_lead_headers, "Idempotency-Key": "cross-actor-task"},
        json={"assigneeId": "operator-cs-lead"},
    )
    assert resp2.status_code == status.HTTP_403_FORBIDDEN

    # 2. Admin Role Workspace Override (admin write)
    # ops-lead overrides role workspaces: succeeds
    resp3 = client.put(
        "/api/v1/operator/shell/admin/roles/cs-lead/workspaces",
        headers={**OPS_HEADERS, "Idempotency-Key": "cross-actor-admin"},
        json={"allowedWorkspaces": ["today"]},
    )
    assert resp3.status_code == status.HTTP_200_OK

    # auditor (lacks admin permission) repeats same endpoint/key: 403 Forbidden instead of 200
    resp4 = client.put(
        "/api/v1/operator/shell/admin/roles/cs-lead/workspaces",
        headers={**AUDITOR_HEADERS, "Idempotency-Key": "cross-actor-admin"},
        json={"allowedWorkspaces": ["today"]},
    )
    assert resp4.status_code == status.HTTP_403_FORBIDDEN

    # 3. Franchisee acknowledgement (franchisee write)
    # franchisee-001 acknowledges notification: succeeds
    notification_id = "NTF-SLA-1024"
    resp5 = client.post(
        "/api/v1/operator/shell/franchisee/acknowledgement",
        headers={**FRANCHISEE_HEADERS, "Idempotency-Key": "cross-actor-franchisee"},
        json={"notificationId": notification_id},
    )
    assert resp5.status_code == status.HTTP_200_OK

    # operator (lacks franchisee portal write permission) repeats same endpoint/key: 403 Forbidden
    resp6 = client.post(
        "/api/v1/operator/shell/franchisee/acknowledgement",
        headers={**OPS_HEADERS, "Idempotency-Key": "cross-actor-franchisee"},
        json={"notificationId": notification_id},
    )
    assert resp6.status_code == status.HTTP_403_FORBIDDEN


def test_franchisee_x_subject_id_spoof_and_idempotency_live_boundary(monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    from apps.api.oday_api.security import dependencies as deps
    from modules.opsboard.auth import SigningKey, encode_compact_jwt

    # 1. Setup live AuthenticationBoundary
    issuer = "https://idp.oday.test"
    audience = "oday-plus-api"
    key = SigningKey(kid="k1", algorithm="HS256", secret=b"api-wiring-secret")

    monkeypatch.setenv("ODP_AUTH_ISSUER", issuer)
    monkeypatch.setenv("ODP_AUTH_AUDIENCES", audience)
    monkeypatch.setenv("ODP_AUTH_HS256_KEYS", "k1:api-wiring-secret")
    deps.reset_default_boundary()

    try:
        client = _client()

        # Helper to generate JWT token headers
        def _bearer_headers(sub: str, roles: list[str]) -> dict[str, str]:
            now = datetime.now(UTC)
            payload = {
                "sub": sub,
                "iss": issuer,
                "aud": audience,
                "iat": now.timestamp(),
                "exp": (now + timedelta(hours=1)).timestamp(),
                "roles": roles,
                "tenant_id": "tenant-a",
            }
            token = encode_compact_jwt(payload, key)
            return {"authorization": f"Bearer {token}"}

        # Test Blocker 1: franchisee X-Subject-Id spoofing under live boundary
        # Attacker holds a valid franchisee-002 token but tries to spoof franchisee-001
        attacker_headers = _bearer_headers(sub="franchisee-002", roles=["franchisee"])
        # We explicitly add X-Subject-Id to try to spoof
        attacker_headers["X-Subject-Id"] = "franchisee-001"

        # Attacker posts a report
        resp_spoof = client.post(
            "/api/v1/operator/shell/franchisee/reports",
            headers=attacker_headers,
            json={"category": "staffing", "message": "Attacker report"},
        )
        assert resp_spoof.status_code == status.HTTP_200_OK
        assert resp_spoof.json()["report"]["subjectId"] == "franchisee-002"

        # Test Blocker 2: Same-role A -> B -> A-retry idempotency sequence
        # Two operators share the 'ops-lead' role (subject-a and subject-b)
        headers_a = _bearer_headers(sub="subject-a", roles=["operations_manager"])
        headers_a["X-Operator-Role"] = "ops-lead"
        headers_b = _bearer_headers(sub="subject-b", roles=["operations_manager"])
        headers_b["X-Operator-Role"] = "ops-lead"

        from uuid import uuid4

        idem_key = f"same-role-idemp-{uuid4().hex[:8]}"

        # A calls settings with key=idem_key
        resp_a1 = client.put(
            "/api/v1/operator/shell/settings",
            headers={**headers_a, "Idempotency-Key": idem_key},
            json={"values": {"density": "comfortable"}},
        )
        assert resp_a1.json().get("idempotentReplay") is False

        # B calls settings with the SAME key
        resp_b = client.put(
            "/api/v1/operator/shell/settings",
            headers={**headers_b, "Idempotency-Key": idem_key},
            json={"values": {"density": "comfortable"}},
        )
        assert resp_b.status_code == status.HTTP_200_OK
        assert (
            resp_b.json().get("idempotentReplay") is False
        )  # B must NOT get a replay of A's cached response

        # A retries settings with the SAME key
        resp_a2 = client.put(
            "/api/v1/operator/shell/settings",
            headers={**headers_a, "Idempotency-Key": idem_key},
            json={"values": {"density": "comfortable"}},
        )
        assert resp_a2.status_code == status.HTTP_200_OK
        assert (
            resp_a2.json().get("idempotentReplay") is True
        )  # A gets their own cached response replayed!

    finally:
        deps.reset_default_boundary()
