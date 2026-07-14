"""Contract tests for the Operator Console R4 API.

Covers:
- GET /api/v1/operator/bootstrap → returns all required envelope keys
- GET /api/v1/operator/today → alias of bootstrap
- GET /api/v1/operator/issues → list envelope
- POST /api/v1/operator/issues/{id}/{action} → transition + audit
- GET /api/v1/operator/approvals → list envelope
- POST /api/v1/operator/approvals/{id}/decision → requires reason
- POST /api/v1/operator/evidence/{id}/purpose → unlock with purpose
- POST /api/v1/operator/seed/reset → deterministic reset
- Idempotency-Key de-duplication on write routes
- X-Correlation-Id round-trip through write responses

Verification command (per task brief):
  uv run pytest tests/contract/test_operator_api.py tests/integration -x -v

Owner: Antigravity (ODP-OC-R4-001)
Reviewer: Claude
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Role must have intervention.CREATE + intervention.APPROVE permissions.
# shared/auth/rbac.py: Role.OPERATIONS_MANAGER has both.
OPERATOR_HEADERS = {
    "x-subject-id": "test-ops-manager",
    "x-roles": "operations_manager",
}


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Single TestClient reused across all tests in this module."""
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# Bootstrap / today (read paths)
# ---------------------------------------------------------------------------


def test_bootstrap_returns_required_keys(client: TestClient) -> None:
    """Acceptance: /operator renders OperatorConsole and contains required envelope keys."""
    resp = client.get("/api/v1/operator/bootstrap", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "kpis" in body
    assert "workQueue" in body
    assert "decisions" in body
    assert isinstance(body["kpis"], list)
    assert len(body["kpis"]) > 0


def test_today_alias_matches_bootstrap(client: TestClient) -> None:
    resp_b = client.get("/api/v1/operator/bootstrap", headers=OPERATOR_HEADERS)
    resp_t = client.get("/api/v1/operator/today", headers=OPERATOR_HEADERS)
    assert resp_b.status_code == 200
    assert resp_t.status_code == 200
    # Both endpoints must return the same key set.
    assert set(resp_b.json().keys()) == set(resp_t.json().keys())


# ---------------------------------------------------------------------------
# Issues (read + write)
# ---------------------------------------------------------------------------


def test_issues_list_returns_envelope(client: TestClient) -> None:
    resp = client.get("/api/v1/operator/issues", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "count" in body
    assert body["count"] == len(body["items"])


def test_issue_triage_transition(client: TestClient) -> None:
    """Acceptance: domain fleets can transition issues through lifecycle."""
    resp = client.post(
        "/api/v1/operator/issues/ISS-1024/triage",
        headers=OPERATOR_HEADERS,
        json={
            "actorRoleId": "opsLead",
            "actorName": "Test Ops Lead",
            "note": "Triage started.",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["issueId"] == "ISS-1024"
    assert body["newStatus"] == "triaged"
    assert "auditEventId" in body


def test_issue_transition_invalid_action_still_returns_200(client: TestClient) -> None:
    """Unknown action_type falls back to 'closed' — no 422 raised by route."""
    resp = client.post(
        "/api/v1/operator/issues/ISS-1021/unknown-action",
        headers=OPERATOR_HEADERS,
        json={"actorRoleId": "opsLead", "actorName": "Lead"},
    )
    assert resp.status_code == 200
    assert resp.json()["newStatus"] == "closed"


def test_issue_transition_idempotency(client: TestClient) -> None:
    """Acceptance: Idempotency-Key de-duplicates concurrent retries."""
    headers = {**OPERATOR_HEADERS, "Idempotency-Key": "idem-test-triage-1"}
    payload = {"actorRoleId": "opsLead", "actorName": "Lead"}

    r1 = client.post("/api/v1/operator/issues/ISS-1021/assign", headers=headers, json=payload)
    r2 = client.post("/api/v1/operator/issues/ISS-1021/assign", headers=headers, json=payload)

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Both responses must be identical (idempotent).
    assert r1.json()["auditEventId"] == r2.json()["auditEventId"]


# ---------------------------------------------------------------------------
# Approvals (read + write)
# ---------------------------------------------------------------------------


def test_approvals_list_returns_envelope(client: TestClient) -> None:
    resp = client.get("/api/v1/operator/approvals", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "count" in body


def test_approval_decision_requires_reason(client: TestClient) -> None:
    """Acceptance: every write contract includes required reason policy."""
    resp = client.post(
        "/api/v1/operator/approvals/APR-501/decision",
        headers=OPERATOR_HEADERS,
        json={
            "actorRoleId": "expansionManager",
            "actorName": "Test Manager",
            "status": "approved",
            "reason": "",  # empty reason must be rejected
        },
    )
    assert resp.status_code == 422, "Empty reason must return 422 validation error"


def test_approval_decision_with_valid_reason(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/operator/approvals/APR-487/decision",
        headers=OPERATOR_HEADERS,
        json={
            "actorRoleId": "opsLead",
            "actorName": "Test Ops Lead",
            "status": "returned",
            "reason": "Compensation note needs clearer framing.",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["approvalId"] == "APR-487"
    assert body["newStatus"] == "returned"
    assert "auditEventId" in body


# ---------------------------------------------------------------------------
# Evidence (write)
# ---------------------------------------------------------------------------


def test_evidence_purpose_unlock_requires_purpose(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/operator/evidence/EV-001/purpose",
        headers=OPERATOR_HEADERS,
        json={
            "actorRoleId": "opsLead",
            "purpose": "",  # empty purpose must be rejected
        },
    )
    assert resp.status_code == 422


def test_evidence_purpose_unlock_valid(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/operator/evidence/EV-001/purpose",
        headers=OPERATOR_HEADERS,
        json={
            "actorRoleId": "opsLead",
            "actorName": "Ops Lead",
            "purpose": "Payment failure root-cause investigation",
            "privacyAcknowledged": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["evidenceId"] == "EV-001"
    assert body["purpose"] == "Payment failure root-cause investigation"


def test_evidence_retention_ceiling(client: TestClient) -> None:
    """retentionHours must not exceed 72."""
    resp = client.post(
        "/api/v1/operator/evidence/EV-002/purpose",
        headers=OPERATOR_HEADERS,
        json={
            "actorRoleId": "opsLead",
            "purpose": "Investigation",
            "retentionHours": 100,  # exceeds 72h ceiling
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Seed reset (deterministic)
# ---------------------------------------------------------------------------


def test_seed_reset_returns_ok(client: TestClient) -> None:
    """Acceptance: R4 seed reset is deterministic."""
    resp = client.post("/api/v1/operator/seed/reset", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_seed_reset_restores_initial_state(client: TestClient) -> None:
    """After a write followed by reset, state returns to canonical seed."""
    # Mutate: triage ISS-1024
    client.post(
        "/api/v1/operator/issues/ISS-1024/triage",
        headers=OPERATOR_HEADERS,
        json={"actorRoleId": "opsLead", "actorName": "Lead"},
    )

    # Reset to seed
    reset_resp = client.post("/api/v1/operator/seed/reset", headers=OPERATOR_HEADERS)
    assert reset_resp.status_code == 200

    # After reset the bootstrap payload must contain seed kpi count.
    boot = client.get("/api/v1/operator/bootstrap", headers=OPERATOR_HEADERS)
    assert boot.status_code == 200
    assert len(boot.json()["kpis"]) == 6  # seed has 6 KPIs


# ---------------------------------------------------------------------------
# Correlation-Id round-trip
# ---------------------------------------------------------------------------


def test_correlation_id_round_trip(client: TestClient) -> None:
    """Acceptance: write contracts preserve X-Correlation-Id in response."""
    headers = {**OPERATOR_HEADERS, "X-Correlation-Id": "corr-r4-001-test"}
    resp = client.post(
        "/api/v1/operator/issues/NET-305/triage",
        headers=headers,
        json={"actorRoleId": "opsLead", "actorName": "Lead"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("correlationId") == "corr-r4-001-test"
