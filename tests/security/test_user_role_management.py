"""Unit and contract tests for User Role Management (ODP-CAP-USER-ROLE-UI-001).

Tests:
1. UserRoleManagementService initialization, seeding, list, get, save, status toggle, and audit trail logging.
2. FastAPI sub-router endpoint responses: GET /operator/users, GET /operator/users/roles, GET /operator/users/audit-trail, POST /operator/users, POST /operator/users/{subject_id}/status.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from apps.api.app.routes.operator_modules.users_roles import create_user_role_sub_router
from modules.opsboard.application.user_role_management import (
    UserNotFound,
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
    assert any(r["role_id"] == Role.PLATFORM_ADMIN.value for r in roles)
    assert any(r["role_id"] == Role.OPERATIONS_MANAGER.value for r in roles)


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


def test_user_role_sub_router_endpoints() -> None:
    audit_log = InMemoryAuditLog()
    service = UserRoleManagementService(audit_log=audit_log)
    router = create_user_role_sub_router(service)

    app = FastAPI()
    app.include_router(router, prefix="/operator")
    client = TestClient(app)

    # 1. GET /operator/users
    res = client.get("/operator/users")
    assert res.status_code == 200
    data = res.json()
    assert "users" in data
    assert data["count"] >= 5

    # 2. GET /operator/users/roles
    res = client.get("/operator/users/roles")
    assert res.status_code == 200
    data = res.json()
    assert "roles" in data
    assert data["count"] >= 10

    # 3. GET /operator/users/ops-lead
    res = client.get("/operator/users/ops-lead")
    assert res.status_code == 200
    data = res.json()
    assert data["subject_id"] == "ops-lead"

    # 4. POST /operator/users (save user)
    res = client.post(
        "/operator/users",
        json={
            "subjectId": "new-operator-01",
            "email": "new-op@odayplus.com",
            "name": "新營運人員",
            "roles": ["operations_manager", "site_reviewer"],
            "scope": {
                "tenant_id": "tenant-default",
                "brand_ids": ["brand-a"],
                "region_ids": [],
                "store_ids": [],
                "clearance": "CONFIDENTIAL",
            },
            "reason": "Creating new operator for Store Ops",
            "actorName": "admin",
        },
    )
    assert res.status_code == 200
    save_data = res.json()
    assert save_data["user"]["subject_id"] == "new-operator-01"
    assert "operations_manager" in save_data["user"]["roles"]

    # 5. POST /operator/users/new-operator-01/status
    res = client.post(
        "/operator/users/new-operator-01/status",
        json={"status": "disabled", "reason": "Temporary suspension"},
    )
    assert res.status_code == 200
    status_data = res.json()
    assert status_data["user"]["status"] == "disabled"

    # 6. GET /operator/users/audit-trail
    res = client.get("/operator/users/audit-trail")
    assert res.status_code == 200
    audit_data = res.json()
    assert audit_data["count"] >= 2
