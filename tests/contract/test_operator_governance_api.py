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

import pytest
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
        # This principal holds both the original decision role and the
        # existing audit-export role; a workspace persona grants neither.
        headers={**_headers(correlation="corr-export-1"), "X-Roles": "operations_manager,auditor"},
    )
    assert resp.status_code == 200
    pkg = resp.json()["package"]

    assert pkg["scope"]["dateFrom"] == "2026-06-01"
    assert pkg["scope"]["dateTo"] == "2026-07-03"
    assert pkg["scope"]["modules"] == ["Store Ops", "Growth", "Network"]
    assert "SLA 報表" in pkg["scope"]["contents"]
    assert pkg["format"] == "PDF"
    assert pkg["actor"] == WRITE_HEADERS["X-Subject-Id"]
    assert pkg["role"] == "ops-lead"
    assert pkg["correlationId"] == "corr-export-1"
    assert pkg["retentionPolicy"]
    assert pkg["range"] == "2026-06-01 – 2026-07-03"

    # The export is recorded in the audit trail and the history list.
    snap = client.get(f"{BASE}/snapshot", headers=WRITE_HEADERS).json()
    assert any(
        row["category"] == "export" and row["entityRef"] == pkg["id"] for row in snap["auditRows"]
    )
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


@pytest.mark.parametrize("action,reason", [("approve", ""), ("return", "Synthetic return reason"), ("reject", "Synthetic reject reason")])
def test_governance_decision_uses_verified_actor_and_binds_request(action, reason):
    client = _client()
    headers = _headers(idem="synthetic-govern-bound", correlation="synthetic-real-correlation")
    body = {"approvalId": "ap-store-1042", "action": action, "reason": reason, "actorName": "forged", "role": "forged"}
    before = client.get(f"{BASE}/snapshot", headers=headers).json()
    expected_ref = next(row["entityRef"] for row in before["approvals"] if row["id"] == body["approvalId"])
    accepted = client.post(f"{BASE}/decisions", json=body, headers=headers)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["decision"]["actor"] == WRITE_HEADERS["X-Subject-Id"]
    assert accepted.json()["decision"]["reason"] == reason
    snapshot = client.get(f"{BASE}/snapshot", headers=headers).json()
    event = next(row for row in snapshot["auditRows"] if row["correlationId"] == "synthetic-real-correlation" and row["category"] == "approval")
    assert event["actor"] == WRITE_HEADERS["X-Subject-Id"] and event["entityRef"] == expected_ref
    replay = client.post(f"{BASE}/decisions", json={**body, "actorName": "another-forged"}, headers=headers)
    assert replay.status_code == 200 and replay.json()["idempotentReplay"]
    assert replay.json()["decision"] == accepted.json()["decision"]
    for changed in [{**body, "reason": "Changed reason"}, {**body, "approvalId": "ap-growth-2207"}]:
        assert client.post(f"{BASE}/decisions", json=changed, headers=headers).status_code == 409
    other_actor = client.post(f"{BASE}/decisions", json=body, headers={**headers, "X-Subject-Id": "synthetic-other"})
    assert other_actor.status_code == 409 and "already decided" in other_actor.text
    assert client.get(f"{BASE}/snapshot", headers=headers).json()["decisions"] == snapshot["decisions"]


def test_governance_fixture_export_receipt_is_scope_actor_and_operation_bound():
    client = _client()
    headers = {**_headers(idem="synthetic-export-bound"), "X-Roles": "operations_manager,auditor"}
    body = {"dateFrom": "2026-07-01", "dateTo": "2026-07-24", "modules": ["Growth"], "contents": ["Audit Trail"], "format": "PDF", "actorName": "forged", "role": "forged"}
    accepted = client.post(f"{BASE}/evidence-package", json=body, headers=headers)
    assert accepted.status_code == 200
    assert accepted.json()["package"]["actor"] == WRITE_HEADERS["X-Subject-Id"]
    replay = client.post(f"{BASE}/evidence-package", json=body, headers=headers)
    assert replay.status_code == 200 and replay.json()["idempotentReplay"]
    assert replay.json()["package"] == accepted.json()["package"]
    for changed in [{**body, "modules": ["Network"]}, {**body, "dateTo": "2026-07-25"}, {**body, "format": "CSV"}]:
        assert client.post(f"{BASE}/evidence-package", json=changed, headers=headers).status_code == 409
    cross_operation = client.post(f"{BASE}/decisions", json={"approvalId": "ap-store-1042", "action": "approve"}, headers=headers)
    assert cross_operation.status_code == 409
    other = client.post(f"{BASE}/evidence-package", json=body, headers={**headers, "X-Subject-Id": "synthetic-other"})
    assert other.status_code == 200 and other.json()["package"]["id"] != accepted.json()["package"]["id"]


@pytest.mark.parametrize("operation", ["decide", "export"])
def test_governance_legacy_receipts_are_not_replayed(operation):
    from modules.opsboard.application.governance import GovernanceConflict, GovernanceService

    service = GovernanceService(initial_state=None)
    service._idempotency["synthetic-legacy"] = {"approvalId": "unattributable"}
    before = service.export_state()
    with pytest.raises(GovernanceConflict, match="UNBOUND_LEGACY_REPLAY"):
        if operation == "decide":
            service.decide(approval_id="ap-store-1042", action="approve", actor_name="synthetic", idempotency_key="synthetic-legacy")
        else:
            service.export_evidence_package(date_from="2026-07-01", date_to="2026-07-24", modules=["Growth"], contents=["Audit Trail"], actor_name="synthetic", idempotency_key="synthetic-legacy")
    assert service.export_state() == before


def test_governance_command_receipts_survive_reload_and_bind_tenant():
    from modules.opsboard.application.governance import GovernancePolicyError, GovernanceService

    service = GovernanceService(tenant_id="synthetic-tenant")
    args = dict(approval_id="ap-store-1042", action="approve", actor_name="synthetic-reviewer", role="ops-lead", tenant_id="synthetic-tenant", idempotency_key="synthetic-durable")
    accepted = service.decide(**args)
    restored = GovernanceService(initial_state=service.export_state(), tenant_id="synthetic-tenant")
    replay = restored.decide(**args)
    assert replay["idempotentReplay"] and replay["decision"] == accepted["decision"]
    with pytest.raises(GovernancePolicyError, match="TENANT_SCOPE_DENIED"):
        restored.decide(**{**args, "tenant_id": "other"})


def test_governance_growth_delegation_recovers_child_save_without_duplicate_decisions():
    from modules.opsboard.application.governance import GovernanceService
    from modules.opsboard.application.growth import GrowthService

    growth = GrowthService(tenant_id="synthetic-tenant")
    draft = growth.create_action(name="Synthetic", segment_id="seg-metro-dinner", objective="Synthetic", target_lift=1, store="Synthetic Store", observation_window="Synthetic Window")
    approval = growth.submit_for_approval(action_id=draft["id"])["approval"]
    child_before = growth.export_state()
    parent = GovernanceService(growth_service=growth, tenant_id="synthetic-tenant")
    args = dict(approval_id=approval["id"], action="approve", reason="Synthetic review", actor_name="synthetic-reviewer", role="ops-lead", tenant_id="synthetic-tenant", idempotency_key="synthetic-linked-command", correlation_id="synthetic-linked-correlation")
    accepted = parent.decide(**args)
    assert accepted["decision"]["module"] == "Growth" and draft["id"] in accepted["decision"]["item"]
    # The parent persisted, while a failed child save left its prior state.
    child = GrowthService(initial_state=child_before, tenant_id="synthetic-tenant")
    reopened = GovernanceService(initial_state=parent.export_state(), growth_service=child, tenant_id="synthetic-tenant")
    replay = reopened.decide(**args)
    assert replay["idempotentReplay"] and replay["decision"] == accepted["decision"]
    assert child.get_action(draft["id"])["status"] == "APPROVED"
    assert len(child.list_decisions()) == 1
    assert child.export_state()["auditEvents"][-1]["actorName"] == "synthetic-reviewer"
    assert child.export_state()["auditEvents"][-1]["actorRoleId"] == "ops-lead"
    again = reopened.decide(**args)
    assert again["idempotentReplay"] and len(child.list_decisions()) == 1
    assert len([d for d in reopened.export_state()["decisions"] if d["approvalId"] == approval["id"]]) == 1


def test_non_fixture_export_refuses_fabricated_artifact_metadata():
    from modules.opsboard.application.governance import (
        GovernanceExportUnavailable,
        GovernanceService,
    )

    service = GovernanceService(seed_fixtures=False, tenant_id="synthetic-tenant")
    before = service.export_state()
    with pytest.raises(GovernanceExportUnavailable, match="CANONICAL_EVIDENCE_EXPORT_REQUIRED"):
        service.export_evidence_package(date_from="2026-07-01", date_to="2026-07-24", modules=["Growth"], contents=["Audit Trail"], actor_name="synthetic", tenant_id="synthetic-tenant")
    assert service.export_state() == before


def test_governance_canonical_configuration_requires_owned_repositories():
    from modules.opsboard.application.governance import GovernancePolicyError, GovernanceService

    with pytest.raises(GovernancePolicyError, match="canonical governance dependencies are unavailable"):
        GovernanceService(seed_fixtures=False, tenant_id="synthetic-tenant", require_canonical=True)


def test_governance_commands_deny_unconfigured_authority():
    from fastapi import FastAPI

    from apps.api.app.routes.operator_modules.governance import create_governance_sub_router
    from modules.opsboard.application.governance import GovernanceService

    service = GovernanceService()
    before = service.export_state()
    app = FastAPI()
    app.include_router(create_governance_sub_router(service))
    client = TestClient(app)
    for endpoint, body in [("decisions", {"approvalId": "ap-store-1042", "action": "approve"}), ("evidence-package", {"dateFrom": "2026-07-01", "dateTo": "2026-07-24"})]:
        response = client.post("/governance/" + endpoint, json=body)
        assert response.status_code == 403 and "GOVERNANCE_COMMAND_AUTHORITY_UNCONFIGURED" in response.text
    assert service.export_state() == before


@pytest.mark.parametrize(
    ("roles", "expected_status", "export_allowed"),
    [
        ("operations_manager", 403, False),
        ("platform_admin,operator_viewer", 403, False),
        ("auditor", 200, True),
        ("operations_manager,auditor", 200, True),
    ],
)
def test_governance_export_requires_existing_audit_grant_not_workspace_persona(
    roles, expected_status, export_allowed,
):
    client = _client()
    headers = {**_headers(), "X-Roles": roles}
    snapshot = client.get(f"{BASE}/snapshot", headers=headers)
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["actionAuthority"]["exportEvidence"] is export_allowed
    before = snapshot.json()["evidencePackages"]
    response = client.post(
        f"{BASE}/evidence-package",
        headers=headers,
        json={"dateFrom": "2026-07-01", "dateTo": "2026-07-24",
              "modules": ["Growth"], "contents": ["Audit Trail"],
              "role": "auditor", "actorName": "forged-auditor"},
    )
    assert response.status_code == expected_status, response.text
    after = client.get(f"{BASE}/snapshot", headers=headers).json()["evidencePackages"]
    if export_allowed:
        assert response.json()["package"]["actor"] == WRITE_HEADERS["X-Subject-Id"]
        assert len(after) == len(before) + 1
    else:
        assert after == before


def test_governance_audit_export_grant_still_requires_operator_console_access():
    client = _client()
    # Compliance has audit:EXPORT, but no Operator Console grant. This route
    # cannot be used to bypass the workspace entry permission.
    response = client.post(
        f"{BASE}/evidence-package",
        headers={**_headers(), "X-Roles": "compliance_officer"},
        json={"dateFrom": "2026-07-01", "dateTo": "2026-07-24"},
    )
    assert response.status_code == 403, response.text


def test_durable_governance_export_checks_same_grant_then_retains_canonical_hold(tmp_path):
    from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
    from shared.infrastructure.persistence.factory import _durable_bundle

    bundle = _durable_bundle(tmp_path / "synthetic-govern-export-permission.sqlite")
    try:
        # Durable storage alone keeps the explicit local fixture composition.
        # Inject its real read owner to exercise the canonical Operator router.
        client = TestClient(create_app(
            persistence=bundle,
            operator_live_repository=OperatorLiveRepository(bundle),
            external_provider_validation=lambda: None,
        ))
        payload = {"dateFrom": "2026-07-01", "dateTo": "2026-07-24",
                   "modules": ["Growth"], "contents": ["Audit Trail"]}
        for roles, expected_status, allowed in [
            ("operations_manager", 403, False), ("auditor", 503, True),
        ]:
            headers = {**_headers(), "X-Roles": roles}
            snapshot = client.get(f"{BASE}/snapshot", headers=headers)
            assert snapshot.status_code == 200, snapshot.text
            assert snapshot.json()["actionAuthority"]["exportEvidence"] is allowed
            before = snapshot.json()["evidencePackages"]
            assert before == [], "canonical empty storage must not project seeded packages"
            response = client.post(f"{BASE}/evidence-package", headers=headers, json=payload)
            assert response.status_code == expected_status, response.text
            if allowed:
                assert response.json()["detail"] == "CANONICAL_EVIDENCE_EXPORT_REQUIRED"
            assert client.get(f"{BASE}/snapshot", headers=headers).json()["evidencePackages"] == before
    finally:
        bundle.engine.close()
