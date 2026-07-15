"""Durability tests for the product-shell API (ODP-PGAP-SHELL-001).

The acceptance criteria call for *durable* assignment, inbox state and governed
writes. In-memory state satisfies a single-process test run and then silently
loses everything on restart, so these tests drive two separate app instances
over one sqlite file: writes go to the first, reads to the second.

Run:
    uv run pytest tests/integration/test_operator_shell_persistence.py -x -v
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.infrastructure.persistence.factory import _durable_bundle

OPS_HEADERS = {
    "x-subject-id": "operator-ops-lead",
    "x-roles": "operations_manager",
    "x-tenant-id": "tenant-a",
}
FRANCHISEE_HEADERS = {
    "x-subject-id": "franchisee-001",
    "x-roles": "franchisee",
    "x-tenant-id": "tenant-a",
}


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "shell_durable.sqlite3")


def _client(db_path: str) -> TestClient:
    """A fresh app over the given database — a new instance models a restart."""
    return TestClient(create_app(persistence=_durable_bundle(db_path)))


def _write(key: str, headers: dict[str, str] = OPS_HEADERS) -> dict[str, str]:
    return {**headers, "X-Correlation-Id": f"corr-{key}", "Idempotency-Key": f"idem-{key}"}


def test_task_assignment_survives_restart(db_path: str) -> None:
    before = _client(db_path)
    task_id = before.get("/api/v1/operator/shell/tasks", headers=OPS_HEADERS).json()["items"][0][
        "taskId"
    ]
    before.post(
        f"/api/v1/operator/shell/tasks/{task_id}/assignment",
        headers=_write("assign"),
        json={
            "assigneeId": "operator-cs-lead",
            "assigneeName": "張珮珊",
            "slaDueAt": "2030-01-01T00:00:00+00:00",
        },
    )

    after = _client(db_path)
    task = after.get(f"/api/v1/operator/shell/tasks?taskId={task_id}", headers=OPS_HEADERS).json()[
        "items"
    ][0]

    assert task["assigneeName"] == "張珮珊"
    assert task["slaDueAt"] == "2030-01-01T00:00:00+00:00"
    assert task["slaState"] == "on-track"


def test_idempotent_replay_survives_restart(db_path: str) -> None:
    """A retry after a restart must not double-apply the write."""
    before = _client(db_path)
    task_id = before.get("/api/v1/operator/shell/tasks", headers=OPS_HEADERS).json()["items"][0][
        "taskId"
    ]
    body = {"assigneeId": "operator-cs-lead", "assigneeName": "張珮珊"}
    first = before.post(
        f"/api/v1/operator/shell/tasks/{task_id}/assignment",
        headers=_write("replay"),
        json=body,
    )
    assert first.json()["idempotentReplay"] is False

    after = _client(db_path)
    retried = after.post(
        f"/api/v1/operator/shell/tasks/{task_id}/assignment",
        headers=_write("replay"),
        json=body,
    )

    assert retried.status_code == 200
    assert retried.json()["idempotentReplay"] is True
    assert retried.json()["assignment"] == first.json()["assignment"]


def test_notification_acknowledgement_survives_restart(db_path: str) -> None:
    before = _client(db_path)
    inbox = before.get("/api/v1/operator/shell/notifications", headers=OPS_HEADERS).json()
    notification_id = inbox["items"][0]["notificationId"]
    unacked_before = inbox["unacknowledged"]
    before.post(
        f"/api/v1/operator/shell/notifications/{notification_id}/acknowledgement",
        headers=_write("ack"),
    )

    after = _client(db_path)
    reread = after.get("/api/v1/operator/shell/notifications", headers=OPS_HEADERS).json()
    acked = next(i for i in reread["items"] if i["notificationId"] == notification_id)

    assert acked["acknowledged"] is True
    assert acked["acknowledgedBy"] == "operator-ops-lead"
    assert reread["unacknowledged"] == unacked_before - 1


def test_notification_preferences_survive_restart(db_path: str) -> None:
    before = _client(db_path)
    before.put(
        "/api/v1/operator/shell/notifications/preferences",
        headers=_write("prefs"),
        json={
            "channels": {"inApp": True, "email": False, "push": True},
            "severityFloor": "warning",
            "digest": "daily",
        },
    )

    after = _client(db_path)
    prefs = after.get(
        "/api/v1/operator/shell/notifications/preferences", headers=OPS_HEADERS
    ).json()

    assert prefs["isDefault"] is False
    assert prefs["preferences"]["severityFloor"] == "warning"
    assert prefs["preferences"]["channels"] == {"inApp": True, "email": False, "push": True}


def test_role_workspace_grant_survives_restart_and_still_authorizes(db_path: str) -> None:
    """A governance write must still shape authorization after a restart —
    otherwise a revoked grant would silently come back."""
    before = _client(db_path)
    before.put(
        "/api/v1/operator/shell/admin/roles/expansion-manager/workspaces",
        headers=_write("grant"),
        json={"allowedWorkspaces": ["today"]},
    )

    after = _client(db_path)
    admin = after.get("/api/v1/operator/shell/admin", headers=OPS_HEADERS).json()
    row = next(r for r in admin["roles"] if r["roleId"] == "expansion-manager")
    assert row["overridden"] is True
    assert row["allowedWorkspaces"] == ["today"]

    expansion_headers = {
        "x-subject-id": "operator-expansion-manager",
        "x-roles": "expansion_user",
        "x-tenant-id": "tenant-a",
    }
    search = after.get("/api/v1/operator/shell/search?q=", headers=expansion_headers).json()
    assert "network" not in {item["workspace"] for item in search["items"]}


def test_settings_survive_restart(db_path: str) -> None:
    before = _client(db_path)
    before.put(
        "/api/v1/operator/shell/settings",
        headers=_write("settings"),
        json={"values": {"density": "compact", "locale": "en-US"}},
    )

    after = _client(db_path)
    settings = after.get("/api/v1/operator/shell/settings", headers=OPS_HEADERS).json()

    assert settings["isDefault"] is False
    assert settings["values"]["density"] == "compact"
    assert settings["values"]["locale"] == "en-US"
    assert settings["values"]["timezone"] == "Asia/Taipei", "untouched keys are preserved"


def test_franchisee_acknowledgement_and_reports_survive_restart(db_path: str) -> None:
    before = _client(db_path)
    view = before.get("/api/v1/operator/shell/franchisee", headers=FRANCHISEE_HEADERS).json()
    notification_id = view["notifications"][0]["notificationId"]
    before.post(
        "/api/v1/operator/shell/franchisee/acknowledgement",
        headers=_write("fr-ack", FRANCHISEE_HEADERS),
        json={"notificationId": notification_id},
    )
    before.post(
        "/api/v1/operator/shell/franchisee/reports",
        headers=_write("fr-report", FRANCHISEE_HEADERS),
        json={"category": "equipment", "message": "冷藏櫃溫度異常"},
    )

    after = _client(db_path)
    reread = after.get("/api/v1/operator/shell/franchisee", headers=FRANCHISEE_HEADERS).json()

    assert reread["notifications"][0]["acknowledged"] is True
    assert [row["message"] for row in reread["reports"]] == ["冷藏櫃溫度異常"]
    assert reread["reports"][0]["status"] == "received"


def test_memory_mode_still_works_without_a_document_store() -> None:
    """The durable repository is optional; the default app must still serve."""
    client = TestClient(create_app())
    task_id = client.get("/api/v1/operator/shell/tasks", headers=OPS_HEADERS).json()["items"][0][
        "taskId"
    ]

    response = client.post(
        f"/api/v1/operator/shell/tasks/{task_id}/assignment",
        headers=_write("memory"),
        json={"assigneeId": "operator-cs-lead"},
    )

    assert response.status_code == 200
    assert (
        client.get(f"/api/v1/operator/shell/tasks?taskId={task_id}", headers=OPS_HEADERS).json()[
            "items"
        ][0]["assigneeId"]
        == "operator-cs-lead"
    )
