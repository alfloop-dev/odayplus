"""Contract tests for the Operator Console R4 API.

Covers:
- GET /api/v1/operator/bootstrap -> returns all required envelope keys
- GET /api/v1/operator/today -> alias of bootstrap
- GET /api/v1/operator/issues -> list envelope
- POST /api/v1/operator/issues/{id}/{action} -> transition + audit
- GET /api/v1/operator/approvals -> list envelope
- POST /api/v1/operator/approvals/{id}/decision -> requires reason
- POST /api/v1/operator/evidence/{id}/purpose -> unlock with purpose
- POST /api/v1/operator/seed/reset -> deterministic reset
- Idempotency-Key de-duplication on write routes
- X-Correlation-Id round-trip through write responses
- Store Ops four-light filtering and durable issue lifecycle

Verification command (per task brief):
  uv run pytest tests/contract/test_operator_api.py tests/integration -x -v

Owner: Antigravity (ODP-OC-R4-001), Codex (ODP-OC-R4-003 Store Ops)
Reviewer: Claude, Claude2
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.infrastructure.persistence.factory import _durable_bundle

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
    """Unknown action_type falls back to 'closed'; no 422 raised by route."""
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
            "reason": "",
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
            "purpose": "",
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
            "retentionHours": 100,
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
    client.post(
        "/api/v1/operator/issues/ISS-1024/triage",
        headers=OPERATOR_HEADERS,
        json={"actorRoleId": "opsLead", "actorName": "Lead"},
    )

    reset_resp = client.post("/api/v1/operator/seed/reset", headers=OPERATOR_HEADERS)
    assert reset_resp.status_code == 200

    boot = client.get("/api/v1/operator/bootstrap", headers=OPERATOR_HEADERS)
    assert boot.status_code == 200
    assert len(boot.json()["kpis"]) == 6


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


# ---------------------------------------------------------------------------
# Store Ops R4 four-light summary and durable lifecycle
# ---------------------------------------------------------------------------


def _store_ops_headers(key: str, correlation_id: str = "corr-store-ops") -> dict[str, str]:
    return {
        "Idempotency-Key": key,
        "X-Correlation-ID": correlation_id,
        "X-Subject-Id": "operator-opsLead",
        "X-Roles": "operations_manager",
        "X-Tenant-Id": "tenant-a",
    }


def _store_ops_client(db_path: str | None = None) -> tuple[TestClient, object | None]:
    if db_path is None:
        return TestClient(create_app(external_provider_validation=lambda: None)), None
    bundle = _durable_bundle(db_path)
    return (
        TestClient(create_app(persistence=bundle, external_provider_validation=lambda: None)),
        bundle,
    )


def test_store_ops_four_light_filter_returns_deterministic_queue() -> None:
    client, _ = _store_ops_client()

    response = client.get(
        "/api/v1/operator/store-ops/issues",
        params={"light": "operations", "lightStatus": "red"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["filters"]["light"] == "operations"
    assert body["filters"]["lightStatus"] == "red"
    assert [issue["id"] for issue in body["issues"]] == ["ISS-1024"]
    operations = next(item for item in body["fourLightSummary"] if item["dimension"] == "operations")
    assert operations["counts"]["red"] == 1
    assert operations["issueCounts"]["red"] == 1


def test_store_ops_invalid_transition_409_and_idempotency_dedupes_audit() -> None:
    client, _ = _store_ops_client()

    invalid = client.post(
        "/api/v1/operator/store-ops/issues/ISS-1024/outcome",
        json={"outcome": "effective", "closeIssue": True},
        headers=_store_ops_headers("idem-invalid"),
    )
    assert invalid.status_code == 409

    payload = {"severity": "critical", "decision": "accept", "notes": "Accepted for ownership."}
    first = client.post(
        "/api/v1/operator/store-ops/issues/ISS-1024/triage",
        json=payload,
        headers=_store_ops_headers("idem-triage"),
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["issue"]["status"] == "triaged"
    audit_count = len(first_body["auditEvents"])

    replay = client.post(
        "/api/v1/operator/store-ops/issues/ISS-1024/triage",
        json=payload,
        headers=_store_ops_headers("idem-triage"),
    )
    assert replay.status_code == 200
    replay_body = replay.json()
    assert replay_body["idempotentReplay"] is True
    assert len(replay_body["auditEvents"]) == audit_count


def test_store_ops_issue_1024_lifecycle_and_camera_purpose_survive_restart(tmp_path) -> None:
    db_path = str(tmp_path / "store_ops.sqlite3")
    client, bundle = _store_ops_client(db_path)
    assert bundle is not None
    try:
        locked = client.get("/api/v1/operator/store-ops/issues/ISS-1024/evidence")
        assert locked.status_code == 200
        camera = next(item for item in locked.json()["evidence"] if item["kind"] == "camera")
        assert camera["lockedReason"]

        denied = client.post(
            "/api/v1/operator/store-ops/issues/ISS-1024/camera-purpose",
            json={
                "purpose": "marketing curiosity",
                "privacyAcknowledged": True,
                "retentionHours": 24,
            },
            headers=_store_ops_headers("idem-camera-denied"),
        )
        assert denied.status_code == 422

        allowed = client.post(
            "/api/v1/operator/store-ops/issues/ISS-1024/camera-purpose",
            json={
                "purpose": "payment incident quality audit",
                "cameraLocation": "台北信義 A11 counter camera",
                "timeWindow": "2026-07-04T20:00Z/2026-07-04T21:00Z",
                "retentionHours": 24,
                "privacyAcknowledged": True,
                "auditNote": "Purpose-limited review for ISS-1024.",
            },
            headers=_store_ops_headers("idem-camera-allowed"),
        )
        assert allowed.status_code == 200
        assert "lockedReason" not in allowed.json()["evidenceItem"]

        steps = [
            ("triage", {"severity": "critical", "decision": "accept", "notes": "Accepted."}, "triaged"),
            (
                "assign",
                {
                    "ownerRoleId": "facilitiesLead",
                    "ownerName": "工務主任",
                    "slaDueAt": "2026-07-05T13:00:00.000Z",
                },
                "assigned",
            ),
            (
                "actions",
                {
                    "actionType": "cleaningCheck",
                    "title": "Clean counter lane",
                    "instructions": "Close the hygiene loop.",
                    "checklistItems": ["clean lane", "attach photo"],
                    "requiresApproval": False,
                },
                "inprogress",
            ),
            (
                "field-report",
                {
                    "reportedBy": "工務主任",
                    "observedAt": "2026-07-05T09:30:00.000Z",
                    "summary": "Counter lane cleaned and payment queue cleared.",
                    "checklistStatus": "complete",
                    "attachmentNames": ["field-photo.jpg"],
                },
                "observing",
            ),
            (
                "outcome",
                {
                    "outcome": "effective",
                    "impactSummary": "Negative review cluster stopped after field action.",
                    "evidenceSummary": "Payment queue and CS case trend returned to baseline.",
                    "closeIssue": True,
                },
                "closed",
            ),
        ]
        for index, (action, payload, expected_status) in enumerate(steps, start=1):
            response = client.post(
                f"/api/v1/operator/store-ops/issues/ISS-1024/{action}",
                json=payload,
                headers=_store_ops_headers(f"idem-lifecycle-{index}", f"corr-lifecycle-{index}"),
            )
            assert response.status_code == 200
            assert response.json()["issue"]["status"] == expected_status
    finally:
        bundle.engine.close()

    reopened_client, reopened_bundle = _store_ops_client(db_path)
    assert reopened_bundle is not None
    try:
        issue = reopened_client.get("/api/v1/operator/store-ops/issues/ISS-1024").json()["issue"]
        assert issue["status"] == "closed"

        evidence = reopened_client.get("/api/v1/operator/store-ops/issues/ISS-1024/evidence").json()["evidence"]
        camera = next(item for item in evidence if item["kind"] == "camera")
        assert camera["purpose"] == "payment incident quality audit"
        assert "lockedReason" not in camera

        audit = reopened_client.get("/api/v1/operator/store-ops/issues").json()["auditEvents"]
        assert any(event["action"] == "evidence.camera_purpose.recorded" for event in audit)
        assert any(event["action"] == "issue.outcome" for event in audit)
    finally:
        reopened_bundle.engine.close()
