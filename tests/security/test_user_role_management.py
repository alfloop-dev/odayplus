"""Unit and contract tests for User Role Management (ODP-CAP-USER-ROLE-UI-001).

Tests:
1. UserRoleManagementService initialization, seeding, list, get, save, status toggle, export_state, and audit logging.
2. RBAC enforcement via create_operator_router():
   - PLATFORM_ADMIN is allowed to view, create/update, and toggle status.
   - OPERATIONS_MANAGER is denied (403 Forbidden) on write and read routes for /operator/users.
3. Server-side derivation of audit trail actor from verified request state.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.operator import create_operator_router
from modules.opsboard.application.user_role_management import (
    UserRoleManagementService,
    UserRolePolicyError,
)
from shared.audit import InMemoryAuditLog
from shared.auth import DataClassification, Role


def test_user_role_service_defaults_and_queries() -> None:
    audit_log = InMemoryAuditLog()
    service = UserRoleManagementService(audit_log=audit_log)

    users = service.list_users()
    assert len(users) >= 5

    ops_lead = service.get_user("ops-lead")
    assert ops_lead["name"] == "營運主管"
    assert Role.OPERATIONS_MANAGER.value in ops_lead["roles"]
    assert ops_lead["status"] == "active"

    roles = service.list_roles()
    assert len(roles) == 18
    assert any(r["role_id"] == Role.PLATFORM_ADMIN.value for r in roles)
    assert any(r["role_id"] == Role.OPERATIONS_MANAGER.value for r in roles)


def test_user_role_service_export_state() -> None:
    service = UserRoleManagementService()
    state = service.export_state()
    assert "users" in state
    assert len(state["users"]) >= 5

    reloaded = UserRoleManagementService(initial_state=state, seed_fixtures=False)
    assert len(reloaded.list_users()) == len(state["users"])


def test_user_role_service_save_user_and_audit_event() -> None:
    audit_log = InMemoryAuditLog()
    service = UserRoleManagementService(audit_log=audit_log)

    # Save user with new roles and scope
    updated = service.save_user(
        subject_id="ops-lead",
        roles=[Role.OPERATIONS_MANAGER.value, Role.PLATFORM_ADMIN.value],
        scope={
            "tenant_id": "tenant-001",
            "brand_ids": ["brand-x"],
            "region_ids": ["region-north"],
            "store_ids": ["store-101"],
            "clearance": DataClassification.HIGHLY_RESTRICTED.name,
        },
        actor_name="admin-user",
        actor_role="platform_admin",
        reason="Promoted ops-lead to platform admin",
    )

    assert set(updated["roles"]) == {Role.OPERATIONS_MANAGER.value, Role.PLATFORM_ADMIN.value}
    assert updated["scope"]["tenant_id"] == "tenant-001"
    assert updated["scope"]["brand_ids"] == ["brand-x"]

    # Check audit trail
    events = service.get_audit_trail(subject_id="ops-lead")
    assert len(events) == 1
    assert events[0]["action"] == "USER_UPDATED"
    assert events[0]["actor"] == "admin-user"
    assert events[0]["detail"]["reason"] == "Promoted ops-lead to platform admin"


def test_user_role_service_invalid_role_policy_error() -> None:
    service = UserRoleManagementService()
    with pytest.raises(UserRolePolicyError, match="Invalid role"):
        service.save_user(
            subject_id="test-user",
            roles=["invalid_role_name"],
        )


def test_user_role_service_status_toggle() -> None:
    audit_log = InMemoryAuditLog()
    service = UserRoleManagementService(audit_log=audit_log)

    disabled = service.set_user_status(
        subject_id="marketing-lead",
        status="disabled",
        actor_name="admin",
        reason="Account deactivated",
    )
    assert disabled["status"] == "disabled"

    events = service.get_audit_trail(subject_id="marketing-lead")
    assert len(events) == 1
    assert events[0]["action"] == "USER_STATUS_UPDATED"
    assert events[0]["detail"]["status"] == "disabled"


def test_operator_router_rbac_guards_and_audit_actor() -> None:
    """Test full create_operator_router() RBAC enforcement for user/role management."""
    audit_log = InMemoryAuditLog()
    router = create_operator_router(audit_log=audit_log)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)

    admin_headers = {
        "x-subject-id": "platform-admin-user",
        "x-roles": "platform_admin",
        "x-tenant-id": "tenant-a",
        "x-operator-role": "platform-admin",
    }

    ops_headers = {
        "x-subject-id": "ops-manager-user",
        "x-roles": "operations_manager",
        "x-tenant-id": "tenant-a",
        "x-operator-role": "ops-lead",
    }

    # 1. PLATFORM_ADMIN can list users
    res = client.get("/api/v1/operator/users", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert "users" in data
    assert data["count"] >= 5

    # 2. PLATFORM_ADMIN can list 18 canonical roles
    res = client.get("/api/v1/operator/users/roles", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 18

    # 3. OPERATIONS_MANAGER gets 403 on GET /operator/users
    res = client.get("/api/v1/operator/users", headers=ops_headers)
    assert res.status_code == 403

    # 4. OPERATIONS_MANAGER gets 403 on POST /operator/users (cannot self-promote)
    res = client.post(
        "/api/v1/operator/users",
        headers=ops_headers,
        json={
            "subjectId": "ops-manager-user",
            "roles": ["platform_admin"],
            "reason": "Attempting self escalation",
        },
    )
    assert res.status_code == 403

    # 5. PLATFORM_ADMIN can POST /operator/users and actor is derived server-side
    res = client.post(
        "/api/v1/operator/users",
        headers=admin_headers,
        json={
            "subjectId": "ops-lead",
            "roles": ["operations_manager", "site_reviewer"],
            "reason": "Assigned site_reviewer to ops-lead",
            "actorName": "forged-client-actor-name",  # Should be ignored
        },
    )
    assert res.status_code == 200

    # Verify audit trail actor is 'platform-admin-user' (server-derived), NOT forged name
    audit_res = client.get("/api/v1/operator/users/audit-trail", headers=admin_headers)
    assert audit_res.status_code == 200
    audit_data = audit_res.json()
    latest_event = [e for e in audit_data["events"] if e["metadata"].get("subject_id") == "ops-lead"][-1]
    assert latest_event["actor"] == "platform-admin-user"

    # 6. OPERATIONS_MANAGER gets 403 on status toggle
    res = client.post(
        "/api/v1/operator/users/ops-lead/status",
        headers=ops_headers,
        json={"status": "disabled", "reason": "Unauthorized attempt"},
    )
    assert res.status_code == 403
