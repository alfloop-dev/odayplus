"""Unit and contract tests for User Role Management (ODP-CAP-USER-ROLE-UI-001).

Tests:
1. UserRoleManagementService initialization, seeding, list, get, save, status toggle, export_state, and audit logging.
2. RBAC enforcement via create_operator_router():
   - PLATFORM_ADMIN is allowed to view, create/update, and toggle status.
   - OPERATIONS_MANAGER is denied (403 Forbidden) on write and read routes for /operator/users.
3. Server-side derivation of audit trail actor from verified request state.
"""

from __future__ import annotations

from pathlib import Path

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
    service.save_user(
        subject_id="ops-lead",
        roles=[Role.OPERATIONS_MANAGER.value],
        reason="Testing state export",
    )
    state = service.export_state()
    assert "users" in state
    assert "events" in state
    assert len(state["users"]) >= 5
    assert len(state["events"]) >= 1

    reloaded = UserRoleManagementService(initial_state=state, seed_fixtures=False)
    assert len(reloaded.list_users()) == len(state["users"])
    assert len(reloaded.get_audit_trail()) == len(state["events"])


def test_user_role_service_tenant_isolation_and_filtering() -> None:
    service = UserRoleManagementService()
    # Save user with tenant-a
    service.save_user(
        subject_id="user-a",
        roles=[Role.OPERATIONS_MANAGER.value],
        scope={"tenant_id": "tenant-a"},
        tenant_id="tenant-a",
        reason="Tenant A assignment",
    )
    # Rejects cross-tenant scope modification
    with pytest.raises(UserRolePolicyError, match="restricted to tenant 'tenant-a'"):
        service.save_user(
            subject_id="user-a",
            roles=[Role.OPERATIONS_MANAGER.value],
            scope={"tenant_id": "tenant-b"},
            tenant_id="tenant-a",
            reason="Cross tenant attempt",
        )

    # Save user with tenant-b
    service.save_user(
        subject_id="user-b",
        roles=[Role.OPERATIONS_MANAGER.value],
        scope={"tenant_id": "tenant-b"},
        tenant_id="tenant-b",
        reason="Tenant B assignment",
    )

    # get_audit_trail with tenant_id filters correctly
    events_a = service.get_audit_trail(tenant_id="tenant-a")
    assert all(e["metadata"].get("tenant_id") == "tenant-a" for e in events_a)
    assert any(e["metadata"].get("subject_id") == "user-a" for e in events_a)
    assert not any(e["metadata"].get("subject_id") == "user-b" for e in events_a)


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


def test_durable_audit_trail_isolation_and_no_exponential_growth(tmp_path: Path) -> None:
    """Test F1: Multiple saves do not exponentially grow event count or leak cross-tenant events."""
    from apps.api.app.routes.operator_modules.live_service import DurableTenantServiceResolver
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.operator_domains import (
        DurableOperatorDomainStateRepository,
    )

    doc_store = SqliteDocumentStore(SqliteEngine(tmp_path / "test.db"))
    repo = DurableOperatorDomainStateRepository(doc_store, "users-roles")
    shared_log = InMemoryAuditLog()

    resolver = DurableTenantServiceResolver(
        repo,
        factory=lambda state, tenant_id: UserRoleManagementService(
            audit_log=shared_log,
            initial_state=state,
            seed_fixtures=False,
        ),
        exporter=lambda service: service.export_state(),
        mutating_methods={"save_user", "set_user_status"},
    )

    class DummyState:
        def __init__(self, tenant_id: str):
            self.operator_tenant_id = tenant_id

    class DummyReq:
        def __init__(self, tenant_id: str):
            self.state = DummyState(tenant_id)

    proxy_a = resolver(DummyReq("tenant-a"))
    proxy_b = resolver(DummyReq("tenant-b"))

    # Perform 4 saves for tenant-a
    for i in range(4):
        proxy_a.save_user(
            subject_id="user-a",
            roles=[Role.OPERATIONS_MANAGER.value],
            scope={"tenant_id": "tenant-a"},
            tenant_id="tenant-a",
            reason=f"Tenant A save {i+1}",
        )

    # Perform 2 saves for tenant-b
    for i in range(2):
        proxy_b.save_user(
            subject_id="user-b",
            roles=[Role.AUDITOR.value],
            scope={"tenant_id": "tenant-b"},
            tenant_id="tenant-b",
            reason=f"Tenant B save {i+1}",
        )

    events_a = proxy_a.get_audit_trail(tenant_id="tenant-a")
    assert len(events_a) == 4, f"Expected exactly 4 events for tenant-a, got {len(events_a)}"

    events_b = proxy_b.get_audit_trail(tenant_id="tenant-b")
    assert len(events_b) == 2, f"Expected exactly 2 events for tenant-b, got {len(events_b)}"

    # Check state document for tenant-a directly from repo
    state_a = repo.load("tenant-a")
    assert len(state_a.get("events", [])) == 4


def test_durable_audit_trail_timestamp_and_integrity_preservation() -> None:
    """Test F2 & F3: occurred_at and integrity hash chain are preserved across state reload."""
    from datetime import UTC, datetime, timedelta

    from shared.audit import AuditEvent

    service = UserRoleManagementService(seed_fixtures=False)
    past_time = datetime.now(UTC) - timedelta(hours=2)

    # Record initial event with explicit past timestamp and job_id
    ev1 = AuditEvent(
        event_type="user_role_management.user_created",
        actor="admin",
        action="USER_CREATED",
        resource="user:ops-lead",
        outcome="success",
        correlation_id="cid-123",
        job_id="job-456",
        metadata={"tenant_id": "tenant-a", "subject_id": "ops-lead"},
        occurred_at=past_time,
    )
    service._record_audit_event(ev1)

    state = service.export_state()
    assert len(state["events"]) == 1

    # Reload service from exported state
    reloaded = UserRoleManagementService(initial_state=state, seed_fixtures=False)

    # Check F2: occurred_at is preserved
    reloaded_events = reloaded.audit_log.list_events()
    assert len(reloaded_events) == 1
    assert reloaded_events[0].occurred_at == past_time
    assert reloaded_events[0].job_id == "job-456"

    # Check F3: chain verification succeeds on valid state
    chain_ver = reloaded.audit_log.verify_chain()
    assert chain_ver.ok is True

    # Tamper with state by modifying metadata or deleting an event
    tampered_state = service.export_state()
    tampered_state["events"][0]["metadata"]["subject_id"] = "tampered-user"
    tampered_service = UserRoleManagementService(initial_state=tampered_state, seed_fixtures=False)
    tampered_ver = tampered_service.audit_log.verify_chain()
    assert tampered_ver.ok is False, "Expected hash chain verification to fail for tampered event metadata"


def test_save_user_existing_tenant_guard() -> None:
    """Test Minor 2: Tenant guard prevents modifying a user that belongs to another tenant."""
    service = UserRoleManagementService()
    service.save_user(
        subject_id="tenant-a-user",
        roles=[Role.OPERATIONS_MANAGER.value],
        scope={"tenant_id": "tenant-a"},
        tenant_id="tenant-a",
        reason="Create user in tenant A",
    )

    with pytest.raises(UserRolePolicyError, match="restricted to tenant 'tenant-b'"):
        service.save_user(
            subject_id="tenant-a-user",
            roles=[Role.PLATFORM_ADMIN.value],
            tenant_id="tenant-b",
            reason="Unauthorized cross tenant edit attempt",
        )

