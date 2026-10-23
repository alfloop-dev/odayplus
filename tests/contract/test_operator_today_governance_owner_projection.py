"""POC Today cards must read and decide the same real in-memory owner records.

This app uses the normal router/permission guards with explicit local test
headers. It creates no SQL, native fixture, model runtime or production grant.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator import create_operator_router
from apps.api.oday_api.security import dependencies
from modules.opsboard.application.operator_state import OperatorStateService

BASE = "/api/v1/operator"
OPS = {"x-subject-id": "local-owner-checker", "x-roles": "operations_manager", "x-tenant-id": "tenant-a"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setattr(dependencies, "_default_boundary", None)
    app = FastAPI()

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id") or str(uuid4())
        response = await call_next(request)
        response.headers["x-correlation-id"] = request.state.correlation_id
        return response

    app.include_router(create_operator_router(), prefix="/api/v1")
    return TestClient(app)


def bootstrap(client: TestClient, headers: dict[str, str] = OPS) -> dict:
    response = client.get(BASE + "/bootstrap", headers=headers)
    assert response.status_code == 200
    return response.json()


def test_today_cards_and_counts_bind_exact_pending_governance_owner(client: TestClient) -> None:
    home = bootstrap(client)
    assert home["meta"]["dataMode"] == "fixture"
    snapshot = client.get(BASE + "/governance/snapshot", headers=OPS).json()
    pending = {row["id"]: row for row in snapshot["approvals"] if row["status"] == "pending"}
    cards = home["today"]["decisions"]
    assert {row["id"] for row in cards} == set(pending)
    assert home["header"]["counts"]["approvals"] == home["meta"]["counts"]["approvals"] == len(pending)
    for card in cards:
        assert card["title"] == pending[card["id"]]["title"]
        assert card["target"] == {"workspace": "govern", "entityId": card["id"], "tab": "approvals"}


def test_full_owner_decision_refreshes_today_counts_after_real_typed_receipt(client: TestClient) -> None:
    before = bootstrap(client)["header"]["counts"]["approvals"]
    owner = client.get(BASE + "/governance/snapshot", headers=OPS).json()
    approval = next(row for row in owner["approvals"] if row["status"] == "pending")
    # Opening the full owner review is read-only; it cannot decrement a count.
    assert bootstrap(client)["header"]["counts"]["approvals"] == before
    correlation = str(uuid4())
    response = client.post(
        BASE + "/governance/decisions",
        headers={**OPS, "idempotency-key": str(uuid4()), "x-correlation-id": correlation},
        json={"approvalId": approval["id"], "action": "approve", "reason": "Explicit local owner review completed."},
    )
    assert response.status_code == 200
    receipt = response.json()
    assert {key: receipt[key] for key in ("approvalId", "action", "status", "finalDecision", "correlation_id")} == {
        "approvalId": approval["id"], "action": "approve", "status": "approved", "finalDecision": "Approved", "correlation_id": correlation,
    }
    assert receipt["decision"]["approvalId"] == approval["id"]
    fresh = bootstrap(client)
    assert fresh["header"]["counts"]["approvals"] == before - 1
    assert all(row["id"] != approval["id"] for row in fresh["today"]["decisions"])
    readback = client.get(BASE + "/governance/snapshot", headers=OPS).json()
    assert next(row for row in readback["approvals"] if row["id"] == approval["id"])["status"] == "approved"


def test_denied_owner_decision_keeps_pending_and_presentation_role_does_not_grant(client: TestClient) -> None:
    before = bootstrap(client)
    approval = client.get(BASE + "/governance/snapshot", headers=OPS).json()["approvals"][0]
    auditor = {**OPS, "x-roles": "auditor", "x-operator-role": "pm-audit"}
    response = client.post(
        BASE + "/governance/decisions",
        headers={**auditor, "idempotency-key": str(uuid4())},
        json={"approvalId": approval["id"], "action": "approve", "role": "營運主管", "actorName": "spoofed"},
    )
    assert response.status_code == 403
    assert bootstrap(client)["today"]["decisions"] == before["today"]["decisions"]
    assert bootstrap(client, auditor)["today"]["decisions"] == []


@pytest.mark.parametrize(("persona", "system_roles", "can_decide"), [
    ("ops-lead", "operations_manager", True),
    ("cs-lead", "operations_manager", False),
    ("field-lead", "regional_supervisor", False),
    ("marketing-manager", "marketing_manager", False),
    ("expansion-manager", "expansion_user,site_reviewer", False),
    ("pm-audit", "auditor", False),
    ("expansion-staff", "expansion_user", False),
    ("ops-lead", "auditor,platform_admin", False),
    ("pm-audit", "operations_manager,auditor", True),
])
def test_today_owner_scope_follows_navigation_and_actual_approval_authority(client: TestClient, persona, system_roles, can_decide) -> None:
    headers = {**OPS, "x-roles": system_roles, "x-operator-role": persona}
    home = bootstrap(client, headers)
    cards = home["today"]["decisions"]
    assert bool(cards) is can_decide
    assert home["header"]["counts"]["approvals"] == len(cards)
    assert all(row["target"]["workspace"] in home["navigation"]["allowedWorkspaces"] for row in cards)
    legacy = OperatorStateService().get_today(role_id=persona, system_roles=system_roles)
    assert home["today"]["queue"] == legacy["today"]["queue"]
    assert [row["label"] for row in home["today"]["kpis"]] == [row["label"] for row in legacy["today"]["kpis"]]


def test_live_read_never_uses_the_poc_owner_provider() -> None:
    target = {"workspace": "today", "entityId": "explicit-native-unit-case", "tab": "interventions"}

    class LiveReadFixture:
        data_origin = {"kind": "explicit-unit-fixture", "sourceId": "independent-native-read"}

        def load_state(self, **scope):
            assert scope["tenant_id"] == "unit-native-tenant"
            return {"_meta": {"dataMode": "live", "tenantId": scope["tenant_id"]}, "decisions": [
                {"id": target["entityId"], "title": "Explicit native read fixture", "status": "PENDING_APPROVAL", "meta": "CLEANING", "target": target},
            ]}

    def forbidden_poc_read(_role_id):
        raise AssertionError("POC provider must never become native authority")

    service = OperatorStateService(
        require_live_data=True, live_repository=LiveReadFixture(), fixture_approval_reader=forbidden_poc_read,
    )
    home = service.get_today(tenant_id="unit-native-tenant")
    assert home["header"]["counts"]["approvals"] == 1
    assert home["today"]["decisions"][0]["target"] == target
