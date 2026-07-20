from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app

NETWORK_HEADERS = {
    "x-subject-id": "operator-expansion-manager",
    "x-roles": "site_reviewer",
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
}

OPS_HEADERS = {
    "x-subject-id": "test-ops-manager",
    "x-roles": "operations_manager",
    "x-operator-role": "ops-lead",
    "x-tenant-id": "tenant-a",
}


def _reset(client: TestClient) -> None:
    response = client.post("/api/v1/operator/network-rebalance/reset", headers=NETWORK_HEADERS)
    assert response.status_code == 200, response.text


def _actor(reason: str | None = None) -> dict[str, str]:
    payload = {
        "actorRoleId": "expansionManager",
        "actorName": "王若寧",
    }
    if reason is not None:
        payload["reason"] = reason
    return payload


def test_rebalance_snapshot_exposes_package6_metadata_and_service_outputs() -> None:
    client = TestClient(create_app())
    _reset(client)

    response = client.get(
        "/api/v1/operator/network-rebalance?selectedStoreId=RB-801",
        headers={**NETWORK_HEADERS, "x-correlation-id": "corr-r4-008-snapshot"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "api"
    assert body["selectedStoreId"] == "RB-801"
    assert body["metadata"]["canonicalPackage"] == "r4-20260707-package-6"
    assert "Network 低效重配" in body["metadata"]["screenLabels"]
    assert body["metadata"]["avm"]["modelVersion"].startswith("avm-rebalance")
    assert body["metadata"]["netPlan"]["snapshotId"] == "NP-SNAP-20260714-0615"
    store = body["stores"][0]
    assert store["id"] == "RB-801"
    assert store["status"] == "watching"
    assert store["relocationExecuted"] is False
    assert store["avmP50"] is None
    assert store["netPlanScenarios"] == []


def test_rebalance_avm_netplan_selection_persists_and_creates_govern_approval() -> None:
    client = TestClient(create_app())
    _reset(client)

    avm_request = client.post(
        "/api/v1/operator/network-rebalance/stores/RB-801/avm/request",
        headers={**NETWORK_HEADERS, "idempotency-key": "idem-r4-008-avm-request"},
        json=_actor(),
    )
    assert avm_request.status_code == 200, avm_request.text
    assert avm_request.json()["store"]["status"] == "avmrequested"

    avm_done = client.post(
        "/api/v1/operator/network-rebalance/stores/RB-801/avm/complete",
        headers={**NETWORK_HEADERS, "idempotency-key": "idem-r4-008-avm-complete"},
        json=_actor(),
    )
    assert avm_done.status_code == 200, avm_done.text
    avm_store = avm_done.json()["store"]
    assert avm_store["status"] == "avmready"
    assert avm_store["avmP50"] == 2860000
    assert avm_store["avmModelVersion"] == "avm-rebalance-income-market-v1.0.0"
    assert avm_store["avmSnapshotId"] == "AVM-SNAP-20260714-0600"

    solve = client.post(
        "/api/v1/operator/network-rebalance/stores/RB-801/netplan/solve",
        headers={**NETWORK_HEADERS, "idempotency-key": "idem-r4-008-netplan-solve"},
        json=_actor(),
    )
    assert solve.status_code == 200, solve.text
    solved_store = solve.json()["store"]
    assert solved_store["status"] == "netplanreview"
    assert [item["id"] for item in solved_store["netPlanScenarios"]] == ["keep", "move", "exit"]
    assert {item["snapshotId"] for item in solved_store["netPlanScenarios"]} == {
        "NP-SNAP-20260714-0615"
    }

    selected = client.post(
        "/api/v1/operator/network-rebalance/stores/RB-801/scenarios/move/select",
        headers={**NETWORK_HEADERS, "idempotency-key": "idem-r4-008-select-move"},
        json=_actor(),
    )
    assert selected.status_code == 200, selected.text
    selected_store = selected.json()["store"]
    assert selected_store["selectedScenarioId"] == "move"
    assert selected_store["selectedScenarioOwner"]["actorName"] == "王若寧"
    assert selected_store["selectedScenarioEvidenceId"].startswith("EV-SEL-")

    reloaded = client.get("/api/v1/operator/network-rebalance", headers=NETWORK_HEADERS)
    assert reloaded.status_code == 200, reloaded.text
    reloaded_store = reloaded.json()["stores"][0]
    assert reloaded_store["selectedScenarioId"] == "move"
    assert reloaded_store["selectedScenarioOwner"]["actorRoleId"] == "expansionManager"
    assert (
        reloaded_store["selectedScenarioEvidenceId"] == selected_store["selectedScenarioEvidenceId"]
    )

    submitted = client.post(
        "/api/v1/operator/network-rebalance/stores/RB-801/submit-review",
        headers={**NETWORK_HEADERS, "idempotency-key": "idem-r4-008-submit-review"},
        json=_actor(
            "Move is recommended by NetPlan and requires Govern approval before execution."
        ),
    )
    assert submitted.status_code == 200, submitted.text
    submitted_body = submitted.json()
    assert submitted_body["store"]["status"] == "pendingapproval"
    assert submitted_body["store"]["relatedApprovalId"] == "APR-NET-RB-801"
    assert submitted_body["executionBoundary"]["relocationExecuted"] is False
    assert submitted_body["store"]["relocationExecuted"] is False

    approvals = client.get("/api/v1/operator/approvals", headers=OPS_HEADERS)
    assert approvals.status_code == 200, approvals.text
    approval_ids = {item["id"] for item in approvals.json()["items"]}
    assert "APR-NET-RB-801" in approval_ids


def test_rebalance_runtime_unavailable_fails_closed_with_retryable_state() -> None:
    client = TestClient(create_app())
    _reset(client)

    request = client.post(
        "/api/v1/operator/network-rebalance/stores/RB-801/avm/request",
        headers=NETWORK_HEADERS,
        json=_actor(),
    )
    assert request.status_code == 200

    failure = client.post(
        "/api/v1/operator/network-rebalance/stores/RB-801/avm/complete",
        headers=NETWORK_HEADERS,
        json={**_actor(), "simulateUnavailable": True},
    )

    assert failure.status_code == 503, failure.text
    detail = failure.json()["detail"]
    assert detail["state"] == "retryable_unavailable"
    assert detail["retryable"] is True
    assert detail["model"] == "AVM"

    snapshot = client.get("/api/v1/operator/network-rebalance", headers=NETWORK_HEADERS).json()
    store = snapshot["stores"][0]
    assert store["status"] == "avmrequested"
    assert store["avmP50"] is None
    assert store["runtimeState"]["state"] == "retryable_unavailable"
    assert store["relocationExecuted"] is False
