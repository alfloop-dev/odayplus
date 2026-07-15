"""Contract tests for the Govern workspace R4 API (ODP-OC-R4-009).

Proves the task acceptance criteria at the HTTP contract layer:

  1. The snapshot exposes every governance value builder — approvals,
     decisions, audit trail and the status board (Data Quality / Model /
     Connector / SLA / Users) — so none is unreachable from the workspace.
  2. Store and Growth decisions plus pending Network approvals appear in the
     snapshot and remain consistent after a fresh read (reload).
  3. Return and reject require a reason; the policy is enforced server-side
     (HTTP 422), never client-only.
  4. Evidence-package export records scope, range, format, actor, correlation
     and retention policy, and appends an audit event.

Plus the cross-cutting R4 contract: Idempotency-Key de-duplication,
X-Correlation-Id round-trip, and fail-closed authorization on write routes.

Design source: canonical package 6 (r4-20260707-package-6),
data-screen-label "Govern 治理稽核".

Verification command (per task brief):
  uv run pytest tests/contract -k govern

Owner: Claude (ODP-OC-R4-009)
Reviewer: Antigravity
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app

BASE = "/api/v1/operator/governance"

# operations_manager carries intervention.CREATE + intervention.APPROVE.
WRITE_HEADERS = {
    "X-Subject-Id": "test-govern-lead",
    "X-Roles": "operations_manager",
    "X-Tenant-Id": "tenant-a",
}


def _client() -> TestClient:
    """Fresh app (hence fresh GovernanceService) per test for isolation."""
    return TestClient(create_app(external_provider_validation=lambda: None))


def _headers(idem: str | None = None, correlation: str = "corr-govern-test") -> dict[str, str]:
    hdr = {**WRITE_HEADERS, "X-Correlation-Id": correlation}
    if idem is not None:
        hdr["Idempotency-Key"] = idem
    return hdr


# ---------------------------------------------------------------------------
# 1. Snapshot exposes every value builder
# ---------------------------------------------------------------------------


def test_governance_snapshot_exposes_every_value_builder() -> None:
    client = _client()
    resp = client.get(
        f"{BASE}/snapshot",
        headers={**WRITE_HEADERS, "X-Correlation-Id": "corr-snap"},
    )
    assert resp.status_code == 200
    body = resp.json()

    for key in ("approvals", "decisions", "auditRows", "statusBoard", "evidencePackages"):
        assert key in body, f"snapshot missing {key}"

    status_board = body["statusBoard"]
    # Data Quality / Model / Connector / SLA / Users must all be reachable.
    for panel in ("dataQuality", "models", "connectors", "sla", "users"):
        assert panel in status_board, f"status board missing {panel}"
        assert status_board[panel], f"status board {panel} is empty"

    assert body["correlation_id"] == "corr-snap"


def test_governance_snapshot_has_store_growth_network_rows() -> None:
    client = _client()
    body = client.get(f"{BASE}/snapshot", headers=WRITE_HEADERS).json()

    approval_modules = {a["module"] for a in body["approvals"]}
    # Pending Network approval must be present alongside Store Ops + Growth.
    assert {"Store Ops", "Growth", "Network"} <= approval_modules

    decision_modules = {d["module"] for d in body["decisions"]}
    assert {"Store Ops", "Growth"} <= decision_modules


# ---------------------------------------------------------------------------
# 2. Consistency after reload
# ---------------------------------------------------------------------------


def test_decision_persists_and_is_consistent_after_reload() -> None:
    client = _client()

    decide = client.post(
        f"{BASE}/decisions",
        json={
            "approvalId": "ap-store-1042",
            "action": "approve",
            "reason": "Evidence package complete.",
            "role": "營運主管",
        },
        headers=_headers(idem="idem-approve-1"),
    )
    assert decide.status_code == 200
    assert decide.json()["finalDecision"] == "Approved"

    # Reload: the approval is now decided and a Decision Log row exists.
    body = client.get(f"{BASE}/snapshot", headers=WRITE_HEADERS).json()
    target = next(a for a in body["approvals"] if a["id"] == "ap-store-1042")
    assert target["status"] == "approved"
    assert any(d["approvalId"] == "ap-store-1042" for d in body["decisions"])


# ---------------------------------------------------------------------------
# 3. Return / reject require reason — enforced server-side
# ---------------------------------------------------------------------------


def test_return_without_reason_is_rejected_server_side() -> None:
    client = _client()
    resp = client.post(
        f"{BASE}/decisions",
        json={"approvalId": "ap-growth-2207", "action": "return", "reason": ""},
        headers=_headers(),
    )
    assert resp.status_code == 422


def test_reject_with_short_reason_is_rejected_server_side() -> None:
    client = _client()
    resp = client.post(
        f"{BASE}/decisions",
        json={"approvalId": "ap-network-3319", "action": "reject", "reason": "no"},
        headers=_headers(),
    )
    assert resp.status_code == 422


def test_return_with_reason_succeeds() -> None:
    client = _client()
    resp = client.post(
        f"{BASE}/decisions",
        json={
            "approvalId": "ap-growth-2207",
            "action": "return",
            "reason": "Audience masking proof is missing — resubmit with mask.",
        },
        headers=_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["finalDecision"] == "Returned"


def test_double_decision_conflicts() -> None:
    client = _client()
    first = client.post(
        f"{BASE}/decisions",
        json={"approvalId": "ap-store-1042", "action": "approve"},
        headers=_headers(idem="idem-a"),
    )
    assert first.status_code == 200
    second = client.post(
        f"{BASE}/decisions",
        json={"approvalId": "ap-store-1042", "action": "reject", "reason": "changed my mind now"},
        headers=_headers(idem="idem-b"),
    )
    assert second.status_code == 409


def test_unknown_approval_is_404() -> None:
    client = _client()
    resp = client.post(
        f"{BASE}/decisions",
        json={"approvalId": "ap-does-not-exist", "action": "approve"},
        headers=_headers(),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. Evidence-package export records scope / range / format / actor / retention
# ---------------------------------------------------------------------------


def test_evidence_package_export_records_full_metadata() -> None:
    client = _client()
    resp = client.post(
        f"{BASE}/evidence-package",
        json={
            "dateFrom": "2026-06-01",
            "dateTo": "2026-07-03",
            "modules": ["Store Ops", "Growth", "Network"],
            "contents": ["Audit Trail", "Decision Log", "Outcome 對比", "SLA 報表"],
            "format": "PDF",
            "role": "PM／稽核",
            "retentionPolicy": "7 天簽章 URL，actor 欄位遮罩",
        },
        headers=_headers(correlation="corr-export-1"),
    )
    assert resp.status_code == 200
    pkg = resp.json()["package"]

    assert pkg["scope"]["dateFrom"] == "2026-06-01"
    assert pkg["scope"]["dateTo"] == "2026-07-03"
    assert pkg["scope"]["modules"] == ["Store Ops", "Growth", "Network"]
    assert "SLA 報表" in pkg["scope"]["contents"]
    assert pkg["format"] == "PDF"
    assert pkg["actor"] == "PM／稽核"
    assert pkg["correlationId"] == "corr-export-1"
    assert pkg["retentionPolicy"]
    assert pkg["range"] == "2026-06-01 – 2026-07-03"

    # The export is recorded in the audit trail and the history list.
    snap = client.get(f"{BASE}/snapshot", headers=WRITE_HEADERS).json()
    assert any(row["category"] == "export" and row["entityRef"] == pkg["id"] for row in snap["auditRows"])
    assert any(item["id"] == pkg["id"] for item in snap["evidencePackages"])


# ---------------------------------------------------------------------------
# Cross-cutting R4 contract
# ---------------------------------------------------------------------------


def test_decision_idempotency_replay() -> None:
    client = _client()
    payload = {"approvalId": "ap-store-1042", "action": "approve"}
    first = client.post(f"{BASE}/decisions", json=payload, headers=_headers(idem="idem-x"))
    second = client.post(f"{BASE}/decisions", json=payload, headers=_headers(idem="idem-x"))
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json().get("idempotentReplay") is True


def test_write_route_is_fail_closed_without_permission() -> None:
    client = _client()
    resp = client.post(
        f"{BASE}/decisions",
        json={"approvalId": "ap-store-1042", "action": "approve"},
        headers={"X-Subject-Id": "nobody", "X-Roles": "", "X-Correlation-Id": "corr-x"},
    )
    assert resp.status_code in (401, 403)


def test_snapshot_read_is_protected() -> None:
    client = _client()
    resp = client.get(f"{BASE}/snapshot")
    assert resp.status_code == 401
