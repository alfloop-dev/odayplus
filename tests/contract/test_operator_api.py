from __future__ import annotations

from fastapi import status
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.audit.events import InMemoryAuditLog
from shared.auth import Role
from shared.infrastructure.persistence import build_persistence

OPERATOR_HEADERS = {
    "x-subject-id": "operator-contract-test",
    "x-roles": Role.OPERATIONS_MANAGER.value,
    "x-tenant-id": "tenant-a",
}


def test_operator_bootstrap_is_rbac_guarded_and_api_backed() -> None:
    guarded = TestClient(create_app())

    denied = guarded.get("/api/v1/operator/bootstrap")

    assert denied.status_code == status.HTTP_403_FORBIDDEN

    client = TestClient(create_app(), headers=OPERATOR_HEADERS)

    response = client.get("/api/v1/operator/bootstrap")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["version"] == "ODP-FLOW-010"
    assert body["workQueue"]
    assert body["issues"]
    assert body["approvals"]
    assert body["notifications"]
    assert body["tasks"]


def test_operator_issue_transition_persists_state_search_and_idempotent_audit() -> None:
    audit_log = InMemoryAuditLog()
    client = TestClient(create_app(audit_log=audit_log), headers=OPERATOR_HEADERS)
    headers = {"x-correlation-id": "corr-operator-issue", "Idempotency-Key": "idem-issue-1"}
    payload = {
        "actorName": "Ops Lead",
        "actorRoleId": "opsLead",
        "notes": "Triage accepted with payment, review, and camera evidence.",
    }

    first = client.post("/api/v1/operator/issues/ISS-1024/triage", json=payload, headers=headers)
    second = client.post("/api/v1/operator/issues/ISS-1024/triage", json=payload, headers=headers)

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    body = first.json()
    replay = second.json()
    issue = next(item for item in body["issues"] if item["id"] == "ISS-1024")
    queue_item = next(item for item in body["workQueue"] if item["id"] == "ISS-1024")
    assert issue["status"] == "triaged"
    assert queue_item["status"] == "Triaged"
    assert body["tasks"][0]["targetId"] == "ISS-1024"
    assert body["notifications"][0]["title"] == "ISS-1024 Triaged"
    assert replay["auditFeed"] == body["auditFeed"]

    search = client.get("/api/v1/operator/search", params={"q": "ISS-1024"})
    assert search.status_code == status.HTTP_200_OK
    assert search.json()["count"] >= 1

    platform_events = [
        event
        for event in audit_log.list_events(correlation_id="corr-operator-issue")
        if event.event_type == "operator.issue.transition"
    ]
    assert len(platform_events) == 1
    assert platform_events[0].metadata["idempotency_key"] == "idem-issue-1"


def test_operator_approval_decision_reason_gate_and_idempotent_audit() -> None:
    audit_log = InMemoryAuditLog()
    client = TestClient(create_app(audit_log=audit_log), headers=OPERATOR_HEADERS)

    invalid = client.post(
        "/api/v1/operator/approvals/ap-store-1042/decision",
        json={"status": "returned", "reason": "short"},
        headers={"x-correlation-id": "corr-operator-approval-invalid"},
    )

    assert invalid.status_code == status.HTTP_400_BAD_REQUEST

    headers = {"x-correlation-id": "corr-operator-approval", "Idempotency-Key": "idem-approval-1"}
    payload = {
        "status": "returned",
        "reason": "Return until customer callback evidence is attached.",
        "actorName": "Ops Lead",
        "actorRoleId": "opsLead",
    }

    first = client.post("/api/v1/operator/approvals/ap-store-1042/decision", json=payload, headers=headers)
    second = client.post("/api/v1/operator/approvals/ap-store-1042/decision", json=payload, headers=headers)

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    body = first.json()
    approval = next(item for item in body["approvals"] if item["id"] == "ap-store-1042")
    assert approval["status"] == "returned"
    assert approval["reason"] == payload["reason"]
    assert body["governanceDecisions"][0]["reason"] == payload["reason"]
    assert second.json()["governanceAuditRows"] == body["governanceAuditRows"]

    platform_events = [
        event
        for event in audit_log.list_events(correlation_id="corr-operator-approval")
        if event.event_type == "operator.approval.decision"
    ]
    assert len(platform_events) == 1
    assert platform_events[0].outcome == "returned"


def test_operator_state_survives_durable_api_rebuild(tmp_path) -> None:
    db_path = tmp_path / "operator.sqlite3"
    first_client = TestClient(
        create_app(persistence=build_persistence(mode="durable", db_path=db_path)),
        headers=OPERATOR_HEADERS,
    )

    written = first_client.post(
        "/api/v1/operator/issues/ISS-1024/triage",
        json={"actorName": "Ops Lead", "notes": "Persist this transition."},
        headers={"x-correlation-id": "corr-operator-durable", "Idempotency-Key": "idem-durable-1"},
    )

    assert written.status_code == status.HTTP_200_OK

    second_client = TestClient(
        create_app(persistence=build_persistence(mode="durable", db_path=db_path)),
        headers=OPERATOR_HEADERS,
    )
    bootstrap = second_client.get("/api/v1/operator/bootstrap")

    assert bootstrap.status_code == status.HTTP_200_OK
    body = bootstrap.json()
    issue = next(item for item in body["issues"] if item["id"] == "ISS-1024")
    queue_item = next(item for item in body["workQueue"] if item["id"] == "ISS-1024")
    assert issue["status"] == "triaged"
    assert queue_item["status"] == "Triaged"
    assert body["tasks"][0]["targetId"] == "ISS-1024"
