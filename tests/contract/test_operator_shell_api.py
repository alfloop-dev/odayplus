"""Contract tests for the product-shell API (ODP-PGAP-SHELL-001).

Covers the acceptance criteria that live in the API layer:

- Home aggregates status/tasks/approvals/decisions/freshness + role entry points
- Task Center: assignment, SLA filtering, deep links, permission-aware actions
- Notifications: inbox state, severity, acknowledgement, preferences, sources
- Global search: authorized cross-domain results without leakage
- Admin + settings: governed audited server writes
- Franchisee: approved viewing, acknowledgement, reporting, no operator data

Run:
    uv run pytest tests/contract/test_operator_shell_api.py -x -v
"""

from __future__ import annotations

import json

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app

# ops-lead: operations_manager holds operator_console VIEW + UPDATE.
OPS_HEADERS = {
    "x-subject-id": "operator-ops-lead",
    "x-roles": "operations_manager",
    "x-tenant-id": "tenant-a",
}
# pm-audit: auditor holds operator_console VIEW only — no UPDATE.
AUDITOR_HEADERS = {
    "x-subject-id": "operator-pm-audit",
    "x-roles": "auditor",
    "x-tenant-id": "tenant-a",
}
# expansion-manager: no govern-only workspaces beyond its own grants.
EXPANSION_HEADERS = {
    "x-subject-id": "operator-expansion-manager",
    "x-roles": "expansion_user",
    "x-tenant-id": "tenant-a",
}
FRANCHISEE_HEADERS = {
    "x-subject-id": "franchisee-001",
    "x-roles": "franchisee",
    "x-tenant-id": "tenant-a",
}


def _write(headers: dict[str, str], key: str) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key, "X-Correlation-Id": f"corr-{key}"}


@pytest.fixture
def client() -> TestClient:
    """A fresh app per test — shell writes mutate service state."""
    return TestClient(create_app())


# ----------------------------------------------------------------------
# Home
# ----------------------------------------------------------------------


def test_home_aggregates_every_first_screen_region(client: TestClient) -> None:
    body = client.get("/api/v1/operator/shell/home", headers=OPS_HEADERS).json()

    for region in ("status", "tasks", "approvals", "decisions", "freshness", "entryPoints"):
        assert region in body, f"home is missing the {region} region"

    status_block = body["status"]
    assert status_block["openTasks"] == len(
        client.get("/api/v1/operator/shell/tasks", headers=OPS_HEADERS).json()["items"]
    )
    assert status_block["tone"] in {"danger", "warning", "success"}
    # Freshness must name each contributing source, not a single global stamp.
    assert {row["source"] for row in body["freshness"]} == {"operator-state", "shell-overlay"}
    assert all(row["generatedAt"] for row in body["freshness"])


def test_home_entry_points_are_role_relevant(client: TestClient) -> None:
    ops = client.get("/api/v1/operator/shell/home", headers=OPS_HEADERS).json()
    expansion = client.get("/api/v1/operator/shell/home", headers=EXPANSION_HEADERS).json()

    ops_keys = {entry["key"] for entry in ops["entryPoints"]}
    expansion_keys = {entry["key"] for entry in expansion["entryPoints"]}

    # ops-lead reaches every workspace and is the only admin.
    assert "admin" in ops_keys
    assert "store" in ops_keys
    # expansion-manager has no store/growth workspace and is not an admin.
    assert "admin" not in expansion_keys
    assert "store" not in expansion_keys
    assert "network" in expansion_keys
    assert expansion["meta"]["isAdmin"] is False


# ----------------------------------------------------------------------
# Task Center
# ----------------------------------------------------------------------


def test_tasks_expose_deep_links_and_permission_aware_actions(client: TestClient) -> None:
    ops = client.get("/api/v1/operator/shell/tasks", headers=OPS_HEADERS).json()
    auditor = client.get("/api/v1/operator/shell/tasks", headers=AUDITOR_HEADERS).json()

    assert ops["items"], "ops-lead should see queue items"
    first = ops["items"][0]
    assert first["deepLink"]["entityId"] == first["taskId"]
    assert first["sourceHref"].startswith("/tasks?taskId=")

    def assign_action(payload: dict) -> dict:
        return next(a for a in payload["actions"] if a["key"] == "task.assign")

    assert assign_action(ops)["allowed"] is True
    # An auditor may read the Task Center but never assign; the denial carries
    # operator-facing prose rather than a bare boolean.
    denied = assign_action(auditor)
    assert denied["allowed"] is False
    assert denied["reason"]


def test_tasks_filter_by_sla_assignee_and_status(client: TestClient) -> None:
    all_tasks = client.get("/api/v1/operator/shell/tasks", headers=OPS_HEADERS).json()
    task_id = all_tasks["items"][0]["taskId"]

    # Assign with an already-past SLA so the task is deterministically breached.
    client.post(
        f"/api/v1/operator/shell/tasks/{task_id}/assignment",
        headers=_write(OPS_HEADERS, "assign-sla"),
        json={
            "assigneeId": "operator-ops-lead",
            "assigneeName": "林承翰",
            "slaDueAt": "2020-01-01T00:00:00+00:00",
        },
    )

    breached = client.get("/api/v1/operator/shell/tasks?sla=breached", headers=OPS_HEADERS).json()
    assert [item["taskId"] for item in breached["items"]] == [task_id]
    assert breached["facets"]["sla"]["breached"] == 1
    assert breached["total"] == all_tasks["total"], "facets/total describe the unfiltered set"

    mine = client.get("/api/v1/operator/shell/tasks?assignee=me", headers=OPS_HEADERS).json()
    assert [item["taskId"] for item in mine["items"]] == [task_id]
    assert mine["items"][0]["assignedToMe"] is True

    unassigned = client.get(
        "/api/v1/operator/shell/tasks?assignee=unassigned", headers=OPS_HEADERS
    ).json()
    assert task_id not in [item["taskId"] for item in unassigned["items"]]

    deep = client.get(f"/api/v1/operator/shell/tasks?taskId={task_id}", headers=OPS_HEADERS).json()
    assert deep["count"] == 1, "deep link by taskId resolves a single task"


def test_task_assignment_is_durable_and_idempotent(client: TestClient) -> None:
    task_id = client.get("/api/v1/operator/shell/tasks", headers=OPS_HEADERS).json()["items"][0][
        "taskId"
    ]
    headers = _write(OPS_HEADERS, "assign-idem")
    body = {"assigneeId": "operator-cs-lead", "assigneeName": "張珮珊"}

    first = client.post(
        f"/api/v1/operator/shell/tasks/{task_id}/assignment", headers=headers, json=body
    )
    replay = client.post(
        f"/api/v1/operator/shell/tasks/{task_id}/assignment", headers=headers, json=body
    )

    assert first.status_code == status.HTTP_200_OK
    assert first.json()["idempotentReplay"] is False
    assert replay.json()["idempotentReplay"] is True
    assert replay.json()["assignment"] == first.json()["assignment"]
    # The write is audited with the key that produced it.
    assert first.json()["auditEvent"]["metadata"]["idempotencyKey"] == "assign-idem"

    reread = client.get(f"/api/v1/operator/shell/tasks?taskId={task_id}", headers=OPS_HEADERS)
    assert reread.json()["items"][0]["assigneeName"] == "張珮珊"


def test_task_assignment_rejects_unknown_task_and_bad_sla(client: TestClient) -> None:
    missing = client.post(
        "/api/v1/operator/shell/tasks/NOPE-999/assignment",
        headers=_write(OPS_HEADERS, "assign-404"),
        json={"assigneeId": "operator-cs-lead"},
    )
    assert missing.status_code == status.HTTP_404_NOT_FOUND

    task_id = client.get("/api/v1/operator/shell/tasks", headers=OPS_HEADERS).json()["items"][0][
        "taskId"
    ]
    bad_sla = client.post(
        f"/api/v1/operator/shell/tasks/{task_id}/assignment",
        headers=_write(OPS_HEADERS, "assign-bad-sla"),
        json={"assigneeId": "operator-cs-lead", "slaDueAt": "not-a-date"},
    )
    assert bad_sla.status_code == 422


# ----------------------------------------------------------------------
# Notifications
# ----------------------------------------------------------------------


def test_notifications_carry_severity_and_source_links(client: TestClient) -> None:
    body = client.get("/api/v1/operator/shell/notifications", headers=OPS_HEADERS).json()

    assert body["items"], "ops-lead should have notifications"
    assert all(item["severity"] in {"critical", "warning", "info"} for item in body["items"])
    assert all(item["sourceHref"] for item in body["items"])
    assert body["unacknowledged"] == len(body["items"])
    assert sum(body["facets"]["severity"].values()) == len(body["items"])
    # Severity ordering: the SLA notification (danger) sorts to the top.
    assert body["items"][0]["severity"] == "critical"


def test_notification_acknowledgement_is_durable_and_idempotent(client: TestClient) -> None:
    inbox = client.get("/api/v1/operator/shell/notifications", headers=OPS_HEADERS).json()
    notification_id = inbox["items"][0]["notificationId"]
    headers = _write(OPS_HEADERS, "ack-1")
    path = f"/api/v1/operator/shell/notifications/{notification_id}/acknowledgement"

    first = client.post(path, headers=headers)
    replay = client.post(path, headers=headers)

    assert first.status_code == status.HTTP_200_OK
    assert first.json()["idempotentReplay"] is False
    assert replay.json()["idempotentReplay"] is True

    after = client.get("/api/v1/operator/shell/notifications", headers=OPS_HEADERS).json()
    acked = next(i for i in after["items"] if i["notificationId"] == notification_id)
    assert acked["acknowledged"] is True
    assert acked["acknowledgedBy"] == "operator-ops-lead"
    assert after["unacknowledged"] == len(inbox["items"]) - 1
    # Acknowledged rows sink below unacknowledged ones.
    assert after["items"][-1]["notificationId"] == notification_id


def test_notification_acknowledgement_is_scoped_per_user(client: TestClient) -> None:
    """An acknowledgement belongs to a person, not to their role.

    If it were keyed by role, one ops-lead acknowledging a critical SLA alert
    would silently clear it from every other ops-lead's inbox.
    """
    inbox = client.get("/api/v1/operator/shell/notifications", headers=OPS_HEADERS).json()
    notification_id = inbox["items"][0]["notificationId"]
    client.post(
        f"/api/v1/operator/shell/notifications/{notification_id}/acknowledgement",
        headers=_write(OPS_HEADERS, "ack-scope"),
    )

    # A different role sharing the notification has not acknowledged it...
    auditor_inbox = client.get(
        "/api/v1/operator/shell/notifications", headers=AUDITOR_HEADERS
    ).json()
    same = [i for i in auditor_inbox["items"] if i["notificationId"] == notification_id]
    assert same and same[0]["acknowledged"] is False

    # ...and neither has a colleague holding the *same* role.
    colleague = {**OPS_HEADERS, "x-subject-id": "operator-ops-lead-colleague"}
    colleague_inbox = client.get("/api/v1/operator/shell/notifications", headers=colleague).json()
    mine = [i for i in colleague_inbox["items"] if i["notificationId"] == notification_id]
    assert mine and mine[0]["acknowledged"] is False, (
        "a colleague on the same role must not inherit someone else's acknowledgement"
    )


def test_notifications_filter_by_severity_and_ack_state(client: TestClient) -> None:
    critical = client.get(
        "/api/v1/operator/shell/notifications?severity=critical", headers=OPS_HEADERS
    ).json()
    assert critical["items"]
    assert all(item["severity"] == "critical" for item in critical["items"])

    unacked = client.get(
        "/api/v1/operator/shell/notifications?acknowledged=false", headers=OPS_HEADERS
    ).json()
    assert all(item["acknowledged"] is False for item in unacked["items"])


def test_notification_preferences_round_trip(client: TestClient) -> None:
    default = client.get(
        "/api/v1/operator/shell/notifications/preferences", headers=OPS_HEADERS
    ).json()
    assert default["isDefault"] is True

    updated = client.put(
        "/api/v1/operator/shell/notifications/preferences",
        headers=_write(OPS_HEADERS, "prefs-1"),
        json={
            "channels": {"inApp": True, "email": False, "push": True},
            "severityFloor": "warning",
            "digest": "daily",
        },
    )
    assert updated.status_code == status.HTTP_200_OK
    assert updated.json()["preferences"]["severityFloor"] == "warning"
    assert updated.json()["auditEvent"]["action"] == "update_preferences"

    reread = client.get(
        "/api/v1/operator/shell/notifications/preferences", headers=OPS_HEADERS
    ).json()
    assert reread["isDefault"] is False
    assert reread["preferences"]["channels"]["email"] is False

    # Preferences are personal: a colleague on the same role keeps the defaults.
    colleague = {**OPS_HEADERS, "x-subject-id": "operator-ops-lead-colleague"}
    theirs = client.get(
        "/api/v1/operator/shell/notifications/preferences", headers=colleague
    ).json()
    assert theirs["isDefault"] is True
    # The inbox reports the live preferences alongside the rows.
    inbox = client.get("/api/v1/operator/shell/notifications", headers=OPS_HEADERS).json()
    assert inbox["preferences"]["severityFloor"] == "warning"


def test_notification_preferences_reject_bad_input(client: TestClient) -> None:
    bad_floor = client.put(
        "/api/v1/operator/shell/notifications/preferences",
        headers=_write(OPS_HEADERS, "prefs-bad"),
        json={"channels": {"inApp": True}, "severityFloor": "nope"},
    )
    assert bad_floor.status_code == 422

    no_channels = client.put(
        "/api/v1/operator/shell/notifications/preferences",
        headers=_write(OPS_HEADERS, "prefs-empty"),
        json={"channels": {}, "severityFloor": "info"},
    )
    assert no_channels.status_code == 422


# ----------------------------------------------------------------------
# Global search
# ----------------------------------------------------------------------


def test_search_returns_authorized_results_and_commands(client: TestClient) -> None:
    body = client.get("/api/v1/operator/shell/search?q=SLA", headers=OPS_HEADERS).json()

    assert body["items"], "a matching query should return entities"
    assert all(item["href"] for item in body["items"])
    assert body["commands"], "search doubles as the command palette"


def test_search_does_not_leak_unauthorized_workspaces(client: TestClient) -> None:
    """An expansion manager must not see store/growth entities — not even titles."""
    ops = client.get("/api/v1/operator/shell/search?q=", headers=OPS_HEADERS).json()
    expansion = client.get("/api/v1/operator/shell/search?q=", headers=EXPANSION_HEADERS).json()

    ops_workspaces = {item["workspace"] for item in ops["items"]}
    expansion_workspaces = {item["workspace"] for item in expansion["items"]}

    assert "store" in ops_workspaces
    assert expansion_workspaces <= {"today", "network", "govern"}
    assert "store" not in expansion_workspaces
    assert "growth" not in expansion_workspaces

    # The store-only issue must be absent from the raw payload, not merely
    # filtered out of the rendered list.
    blob = json.dumps(expansion, ensure_ascii=False)
    assert "ISS-1024" not in blob
    assert "支付失敗率異常升高" not in blob
    # ...and the admin command is not offered to a non-admin.
    assert "command-admin" not in {command["id"] for command in expansion["commands"]}


def test_search_honours_limit(client: TestClient) -> None:
    body = client.get("/api/v1/operator/shell/search?q=&limit=2", headers=OPS_HEADERS).json()
    assert len(body["items"]) == 2
    assert body["total"] >= body["count"]


# ----------------------------------------------------------------------
# Admin + settings
# ----------------------------------------------------------------------


def test_admin_lists_roles_with_current_grants(client: TestClient) -> None:
    body = client.get("/api/v1/operator/shell/admin", headers=OPS_HEADERS).json()
    assert {row["roleId"] for row in body["roles"]} >= {"ops-lead", "cs-lead", "pm-audit"}
    assert all(row["overridden"] is False for row in body["roles"])


def test_role_workspace_override_is_audited_and_changes_authorization(
    client: TestClient,
) -> None:
    """The admin write must actually re-shape what the target role can reach."""
    before = client.get("/api/v1/operator/shell/search?q=", headers=EXPANSION_HEADERS).json()
    assert "network" in {item["workspace"] for item in before["items"]}

    response = client.put(
        "/api/v1/operator/shell/admin/roles/expansion-manager/workspaces",
        headers=_write(OPS_HEADERS, "grant-1"),
        json={"allowedWorkspaces": ["today"]},
    )
    assert response.status_code == status.HTTP_200_OK
    audit = response.json()["auditEvent"]
    assert audit["action"] == "update_role_workspaces"
    assert audit["metadata"]["highRisk"] is True
    assert audit["actorSubjectId"] == "operator-ops-lead"

    after = client.get("/api/v1/operator/shell/search?q=", headers=EXPANSION_HEADERS).json()
    assert "network" not in {item["workspace"] for item in after["items"]}

    admin = client.get("/api/v1/operator/shell/admin", headers=OPS_HEADERS).json()
    row = next(r for r in admin["roles"] if r["roleId"] == "expansion-manager")
    assert row["overridden"] is True
    assert row["allowedWorkspaces"] == ["today"]
    assert admin["auditFeed"], "the admin surface surfaces its own audit trail"


def test_role_workspace_override_guards_against_lockout(client: TestClient) -> None:
    # Every role keeps Today.
    dropped_today = client.put(
        "/api/v1/operator/shell/admin/roles/cs-lead/workspaces",
        headers=_write(OPS_HEADERS, "grant-no-today"),
        json={"allowedWorkspaces": ["store"]},
    )
    assert dropped_today.status_code == 422

    # The admin role cannot drop govern, or nobody could restore grants.
    lockout = client.put(
        "/api/v1/operator/shell/admin/roles/ops-lead/workspaces",
        headers=_write(OPS_HEADERS, "grant-lockout"),
        json={"allowedWorkspaces": ["today", "store"]},
    )
    assert lockout.status_code == status.HTTP_409_CONFLICT

    unknown_role = client.put(
        "/api/v1/operator/shell/admin/roles/nope/workspaces",
        headers=_write(OPS_HEADERS, "grant-404"),
        json={"allowedWorkspaces": ["today"]},
    )
    assert unknown_role.status_code == status.HTTP_404_NOT_FOUND

    unknown_workspace = client.put(
        "/api/v1/operator/shell/admin/roles/cs-lead/workspaces",
        headers=_write(OPS_HEADERS, "grant-bad-ws"),
        json={"allowedWorkspaces": ["today", "atlantis"]},
    )
    assert unknown_workspace.status_code == 422


def test_settings_round_trip_and_validation(client: TestClient) -> None:
    default = client.get("/api/v1/operator/shell/settings", headers=OPS_HEADERS).json()
    assert default["isDefault"] is True
    assert default["values"]["locale"] == "zh-TW"

    updated = client.put(
        "/api/v1/operator/shell/settings",
        headers=_write(OPS_HEADERS, "settings-1"),
        json={"values": {"density": "compact"}},
    )
    assert updated.status_code == status.HTTP_200_OK
    # A patch merges rather than replacing the untouched keys.
    assert updated.json()["values"] == {
        "locale": "zh-TW",
        "timezone": "Asia/Taipei",
        "density": "compact",
    }
    assert updated.json()["auditEvent"]["category"] == "shell.settings"

    reread = client.get("/api/v1/operator/shell/settings", headers=OPS_HEADERS).json()
    assert reread["isDefault"] is False
    assert reread["updatedBy"] == "operator-ops-lead"

    rejected = client.put(
        "/api/v1/operator/shell/settings",
        headers=_write(OPS_HEADERS, "settings-bad"),
        json={"values": {"density": "microscopic"}},
    )
    assert rejected.status_code == 422


def test_settings_are_scoped_per_user(client: TestClient) -> None:
    """Settings are personal: one operator's density choice must not rewrite a
    colleague's, even on the same role."""
    client.put(
        "/api/v1/operator/shell/settings",
        headers=_write(OPS_HEADERS, "settings-scope"),
        json={"values": {"density": "compact"}},
    )
    auditor = client.get("/api/v1/operator/shell/settings", headers=AUDITOR_HEADERS).json()
    assert auditor["values"]["density"] == "comfortable"
    assert auditor["isDefault"] is True

    colleague = {**OPS_HEADERS, "x-subject-id": "operator-ops-lead-colleague"}
    same_role = client.get("/api/v1/operator/shell/settings", headers=colleague).json()
    assert same_role["isDefault"] is True
    assert same_role["values"]["density"] == "comfortable"


# ----------------------------------------------------------------------
# Franchisee
# ----------------------------------------------------------------------


def test_franchisee_view_excludes_operator_only_data(client: TestClient) -> None:
    body = client.get("/api/v1/operator/shell/franchisee", headers=FRANCHISEE_HEADERS).json()

    assert body["tasks"], "a franchisee sees their own store's tasks"
    assert {"id", "title", "status"} <= set(body["tasks"][0])
    # Operator-internal task detail is projected away.
    assert "owner" not in body["tasks"][0]
    assert "meta" not in body["tasks"][0]
    assert "description" not in body["tasks"][0]

    blob = json.dumps(body, ensure_ascii=False)
    for operator_only in (
        "APR-501",  # govern approval
        "NTF-APR-501",
        "NTF-MODEL-0600",  # model snapshot notification
        "GRW-201",  # growth workspace
        "NET-305",  # network workspace
        "riskRows",
        "auditFeed",
        "kpis",
    ):
        assert operator_only not in blob, f"{operator_only} leaked to the franchisee surface"

    assert {item["notificationId"] for item in body["notifications"]} == {"NTF-SLA-1024"}


def test_franchisee_acknowledgement_and_report_are_durable(client: TestClient) -> None:
    view = client.get("/api/v1/operator/shell/franchisee", headers=FRANCHISEE_HEADERS).json()
    notification_id = view["notifications"][0]["notificationId"]

    headers = _write(FRANCHISEE_HEADERS, "fr-ack")
    first = client.post(
        "/api/v1/operator/shell/franchisee/acknowledgement",
        headers=headers,
        json={"notificationId": notification_id},
    )
    replay = client.post(
        "/api/v1/operator/shell/franchisee/acknowledgement",
        headers=headers,
        json={"notificationId": notification_id},
    )
    assert first.status_code == status.HTTP_200_OK
    assert replay.json()["idempotentReplay"] is True

    after = client.get("/api/v1/operator/shell/franchisee", headers=FRANCHISEE_HEADERS).json()
    assert after["notifications"][0]["acknowledged"] is True

    report = client.post(
        "/api/v1/operator/shell/franchisee/reports",
        headers=_write(FRANCHISEE_HEADERS, "fr-report"),
        json={"category": "equipment", "message": "冷藏櫃溫度異常"},
    )
    assert report.status_code == status.HTTP_200_OK
    assert report.json()["report"]["status"] == "received"

    listed = client.get("/api/v1/operator/shell/franchisee", headers=FRANCHISEE_HEADERS).json()
    assert [row["message"] for row in listed["reports"]] == ["冷藏櫃溫度異常"]


def test_franchisee_reports_are_scoped_to_their_author(client: TestClient) -> None:
    client.post(
        "/api/v1/operator/shell/franchisee/reports",
        headers=_write(FRANCHISEE_HEADERS, "fr-mine"),
        json={"category": "supply", "message": "缺貨"},
    )
    other = {**FRANCHISEE_HEADERS, "x-subject-id": "franchisee-002"}
    view = client.get("/api/v1/operator/shell/franchisee", headers=other).json()
    assert view["reports"] == [], "a franchisee never sees another franchisee's reports"


def test_franchisee_cannot_acknowledge_an_operator_only_notification(
    client: TestClient,
) -> None:
    """404, not 403 — the operator-only notification is invisible, so its
    existence is not disclosed."""
    response = client.post(
        "/api/v1/operator/shell/franchisee/acknowledgement",
        headers=_write(FRANCHISEE_HEADERS, "fr-leak"),
        json={"notificationId": "NTF-APR-501"},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_franchisee_report_rejects_unknown_category_and_blank_message(
    client: TestClient,
) -> None:
    bad_category = client.post(
        "/api/v1/operator/shell/franchisee/reports",
        headers=_write(FRANCHISEE_HEADERS, "fr-bad-cat"),
        json={"category": "bogus", "message": "x"},
    )
    assert bad_category.status_code == 422

    blank = client.post(
        "/api/v1/operator/shell/franchisee/reports",
        headers=_write(FRANCHISEE_HEADERS, "fr-blank"),
        json={"category": "other", "message": "   "},
    )
    assert blank.status_code == 422
