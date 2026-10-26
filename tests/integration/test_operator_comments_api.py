"""API, authorization, audit, and restart coverage for OPS-002 comments."""

from __future__ import annotations

from fastapi import status
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.infrastructure.persistence.factory import _durable_bundle

BASE = "/api/v1/operator/comments"
OPS_HEADERS = {
    "x-subject-id": "operator-ops-lead",
    "x-roles": "operations_manager",
    "x-tenant-id": "tenant-a",
    "x-operator-role": "ops-lead",
}
AUDITOR_HEADERS = {
    "x-subject-id": "auditor-1",
    "x-roles": "auditor",
    "x-tenant-id": "tenant-a",
    "x-operator-role": "pm-audit",
}


def _list(client: TestClient, headers: dict[str, str] = OPS_HEADERS):
    return client.get(
        f"{BASE}?targetType=approval&targetId=ap-store-1042", headers=headers
    )


def test_comments_bind_verified_tenant_actor_and_keep_decision_immutable() -> None:
    client = TestClient(create_app())

    empty = _list(client)
    assert empty.status_code == status.HTTP_200_OK
    assert empty.json()["items"] == []

    created = client.post(
        BASE,
        headers={**OPS_HEADERS, "x-correlation-id": "corr-comment-create"},
        json={
            "targetType": "approval",
            "targetId": "ap-store-1042",
            "content": "  Reviewed the customer callback evidence.  ",
            "status": "approved",
            "decision": "approved",
            "actorName": "spoofed-display-actor",
        },
    )
    assert created.status_code == status.HTTP_200_OK
    comment = created.json()["comment"]
    assert comment["tenantId"] == "tenant-a"
    assert comment["createdBy"] == "operator-ops-lead"
    assert comment["targetType"] == "approval"
    assert comment["targetId"] == "ap-store-1042"
    assert comment["content"] == "Reviewed the customer callback evidence."
    assert "status" not in comment
    assert "decision" not in comment
    assert created.json()["auditEvent"]["action"] == "comment.created"

    edited = client.patch(
        f"{BASE}/{comment['id']}",
        headers={
            **OPS_HEADERS,
            "x-correlation-id": "corr-comment-edit",
            "idempotency-key": "comment-edit-once",
        },
        json={"content": "Evidence reviewed and callback retained."},
    )
    assert edited.status_code == status.HTTP_200_OK
    edited_comment = edited.json()["comment"]
    assert edited_comment["targetType"] == "approval"
    assert edited_comment["targetId"] == "ap-store-1042"
    assert edited_comment["createdBy"] == "operator-ops-lead"
    assert edited_comment["updatedBy"] == "operator-ops-lead"
    assert edited_comment["editCount"] == 1
    assert [entry["action"] for entry in edited_comment["history"]] == [
        "created",
        "edited",
    ]
    assert edited.json()["auditEvent"]["action"] == "comment.edited"

    edit_replay = client.patch(
        f"{BASE}/{comment['id']}",
        headers={**OPS_HEADERS, "idempotency-key": "comment-edit-once"},
        json={"content": "A retry must not add another history entry"},
    )
    assert edit_replay.status_code == status.HTTP_200_OK
    assert edit_replay.json()["idempotentReplay"] is True
    assert len(edit_replay.json()["comment"]["history"]) == 2

    readback = _list(client).json()
    assert readback["count"] == 1
    assert readback["items"][0]["content"] == "Evidence reviewed and callback retained."


def test_comments_are_idempotent_and_fail_closed_across_tenants() -> None:
    client = TestClient(create_app())
    headers = {**OPS_HEADERS, "idempotency-key": "comment-once"}
    first = client.post(
        BASE,
        headers=headers,
        json={
            "targetType": "task",
            "targetId": "TASK-401",
            "content": "First durable note",
        },
    )
    replay = client.post(
        BASE,
        headers=headers,
        json={
            "targetType": "task",
            "targetId": "TASK-401",
            "content": "A retry must not create another note",
        },
    )

    assert first.status_code == status.HTTP_200_OK
    assert replay.status_code == status.HTTP_200_OK
    assert replay.json()["idempotentReplay"] is True
    assert replay.json()["comment"]["id"] == first.json()["comment"]["id"]

    cross_tenant = _list(
        client,
        headers={**OPS_HEADERS, "x-tenant-id": "tenant-b"},
    )
    assert cross_tenant.status_code == status.HTTP_403_FORBIDDEN


def test_auditor_can_read_comments_but_cannot_write() -> None:
    client = TestClient(create_app())

    assert _list(client, AUDITOR_HEADERS).status_code == status.HTTP_200_OK
    denied = client.post(
        BASE,
        headers=AUDITOR_HEADERS,
        json={
            "targetType": "decision",
            "targetId": "dec-8841",
            "content": "Auditor note",
        },
    )
    assert denied.status_code == status.HTTP_403_FORBIDDEN


def test_durable_comment_readback_survives_app_restart(tmp_path) -> None:
    db_path = tmp_path / "operator-comments.sqlite3"
    bundle = _durable_bundle(db_path)
    first_client = TestClient(create_app(persistence=bundle))
    created = first_client.post(
        BASE,
        headers={**OPS_HEADERS, "idempotency-key": "durable-comment"},
        json={
            "targetType": "decision",
            "targetId": "dec-8841",
            "content": "Durable decision context",
        },
    )
    assert created.status_code == status.HTTP_200_OK
    audit_events = bundle.audit_log.list_events(tenant_id="tenant-a")
    assert audit_events[-1].action == "comment.created"
    assert audit_events[-1].actor == "operator-ops-lead"
    bundle.engine.close()

    reopened = _durable_bundle(db_path)
    second_client = TestClient(create_app(persistence=reopened))
    response = second_client.get(
        f"{BASE}?targetType=decision&targetId=dec-8841", headers=OPS_HEADERS
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["items"][0]["content"] == "Durable decision context"
    assert response.json()["items"][0]["createdBy"] == "operator-ops-lead"
    reopened.engine.close()
