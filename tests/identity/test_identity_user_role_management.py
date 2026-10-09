"""PostgreSQL integration: user administration changes what the auth boundary sees.

Task: ODP-DEV-ADMIN-RELEASE-READINESS-001
Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §4, §5.4, §7.2, §7.3, §8.1

Every request below goes through the production ``AuthenticationBoundary`` and
``require_operator_permission`` resolving accounts, roles, scope and sessions
from the real ``identity.*`` tables, and every write goes through
``IdentityUserRoleManagementService`` with the durable hash-chained audit log
on the same PostgreSQL engine. Accounts other than the bootstrap administrator
are inserted directly as *test inputs* standing in for accepted invitations.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.users_roles import create_user_role_sub_router
from apps.api.oday_api.security.dependencies import (
    OPERATOR_CONSOLE_RESOURCE,
    PASSWORD_CHANGE_REQUIRED,
    require_operator_permission,
)
from modules.opsboard.application.identity_user_role_management import (
    IdentityUserRoleManagementService,
    UnavailableUserRoleManagementService,
)
from modules.opsboard.application.user_role_management import UserRolePolicyError
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    SigningKey,
    encode_compact_jwt,
)
from shared.auth import Action, AuthorizationEngine
from shared.identity.bootstrap import bootstrap_first_admin, request_from_env

IDENTITY_MIGRATION = Path("infra/db/migrations/000011_identity_schema.sql")
LOCAL_KEY = SigningKey(kid="admin-it", algorithm="HS256", secret=b"admin-it-local-signing-key-32-bytes!!")
LOCAL_ISSUER = "urn:odp:identity:local"
AUDIENCE = "oday-api"
TENANT = "0b5e8f0e-9c55-4f0e-8a51-0d7f1c2a3b4c"
OTHER_TENANT = "7d3a1b2c-4e5f-4a6b-9c7d-8e9f0a1b2c3d"
SECRET = "Bootstrap-Secret-For-Tests-5521"


@pytest.fixture
def stack(intake_blank_db: Any) -> Any:
    from shared.identity import (
        SessionConfig,
        SessionService,
        SqlIdentityStore,
        SqlSessionRepository,
    )
    from shared.infrastructure.persistence.audit_log import DurableAuditLog
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    with intake_blank_db.connect(autocommit=True) as conn:
        conn.execute(IDENTITY_MIGRATION.read_text(encoding="utf-8"))
    engine = PostgresEngine(intake_blank_db.url(), bootstrap=True, validate_schema=False)
    audit = DurableAuditLog(engine)
    identity = SqlIdentityStore(connection_factory=engine.pooled_connection)
    sessions = SessionService(
        repository=SqlSessionRepository(connection_factory=engine.pooled_connection),
        config=SessionConfig(),
    )
    boundary = AuthenticationBoundary(
        AuthBoundaryConfig(
            local_issuer=LOCAL_ISSUER,
            local_signing_keys={LOCAL_KEY.kid: LOCAL_KEY},
            local_audiences=frozenset({AUDIENCE}),
            principal_mappings={},
            identity_store=identity,
            session_service=sessions,
        ),
        audit_log=audit,
    )

    class Stack:
        pass

    s = Stack()
    s.db, s.engine, s.audit, s.identity, s.sessions, s.boundary = (
        intake_blank_db, engine, audit, identity, sessions, boundary
    )
    s.service = IdentityUserRoleManagementService(engine=engine, audit_log=audit)
    s.client = TestClient(_app(s, s.service))
    try:
        yield s
    finally:
        engine.close()


def _app(stack: Any, service: Any) -> FastAPI:
    authz = AuthorizationEngine(audit_log=stack.audit)

    def guard(resource: str, action: Action) -> Any:
        return require_operator_permission(
            resource, action, engine=authz, boundary=stack.boundary, session_service=stack.sessions
        )

    app = FastAPI()
    app.include_router(
        create_user_role_sub_router(
            service,
            require_view_permission_fn=guard("user", Action.VIEW),
            require_manage_permission_fn=guard("user", Action.UPDATE),
        ),
        prefix="/api/v1/operator",
    )

    @app.get("/api/v1/operator/bootstrap", dependencies=[Depends(guard(OPERATOR_CONSOLE_RESOURCE, Action.VIEW))])
    def business_shell() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _q(stack: Any, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with stack.db.connect(autocommit=True) as conn:
        return list(conn.execute(sql, params).fetchall())


def _bootstrap_admin(stack: Any) -> str:
    env = {
        "ODP_IDENTITY_BOOTSTRAP_SECRET": SECRET,
        "ODP_IDENTITY_BOOTSTRAP_EXPIRES_AT": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "ODP_IDENTITY_BOOTSTRAP_TENANT_ID": TENANT,
        "ODP_IDENTITY_BOOTSTRAP_USERNAME": "root.admin",
        "ODP_IDENTITY_BOOTSTRAP_EMAIL": "root.admin@example.invalid",
    }
    result = bootstrap_first_admin(
        stack.engine,
        stack.audit,
        request_from_env(env),
        hash_password=lambda _s: "$argon2id$v=19$m=65536,t=3,p=1$c2FsdA$aGFzaA",
    )
    return str(result.account_id)


def _rotate_password(stack: Any, account_id: str) -> None:
    # The exact statement the web /auth/password route issues after verifying
    # the current password (apps/web/src/lib/auth/identityStore.ts changePassword).
    _q(
        stack,
        "UPDATE identity.password_credentials SET phc_hash = %s, must_change = false, "
        "last_rotated_at = now(), updated_at = now() WHERE account_id = %s RETURNING account_id",
        ("$argon2id$v=19$m=65536,t=3,p=1$bmV3$bmV3aGFzaA", account_id),
    )


def _invited_account(stack: Any, username: str, role: str, tenant: str = TENANT) -> str:
    (account_id,), = _q(
        stack,
        "INSERT INTO identity.accounts (tenant_id, username, email, status, created_by) "
        "VALUES (%s, %s, %s, 'active', 'invitation-test-input') RETURNING account_id::text",
        (tenant, username, f"{username}@example.invalid"),
    )
    _q(
        stack,
        "INSERT INTO identity.account_roles (account_id, role, granted_by) VALUES (%s, %s, 'test') "
        "RETURNING account_id",
        (account_id, role),
    )
    return account_id


def _sign_in(stack: Any, account_id: str, tenant: str = TENANT) -> dict[str, str]:
    session = stack.sessions.create_session(account_id=UUID(account_id), provider="local_password")
    now = int(time.time())
    token = encode_compact_jwt(
        {
            "iss": LOCAL_ISSUER,
            "sub": account_id,
            "aud": AUDIENCE,
            "iat": now,
            "nbf": now - 5,
            "exp": now + 3600,
            "sid": str(session.session_id),
            "tenant_id": tenant,
        },
        LOCAL_KEY,
    )
    return {"authorization": f"Bearer {token}"}


def _roles_seen_by_boundary(stack: Any, headers: dict[str, str]) -> set[str]:
    from modules.opsboard.auth import Credentials

    outcome = stack.boundary.authenticate(Credentials.from_headers(headers))
    assert outcome.authenticated, outcome.reason
    return {r.value for r in outcome.principal.roles}


def _audit_types(stack: Any) -> list[str]:
    rows = _q(
        stack,
        "SELECT event_type FROM odp_runtime.durable_audit_events "
        "WHERE event_type LIKE 'identity.account.%%' ORDER BY seq",
    )
    return [r[0] for r in rows]


def test_bootstrap_admin_must_rotate_then_reaches_admin_but_not_business(stack: Any) -> None:
    admin_id = _bootstrap_admin(stack)
    headers = _sign_in(stack, admin_id)

    refused = stack.client.get("/api/v1/operator/users", headers=headers)
    assert refused.status_code == 403
    assert refused.json()["detail"] == PASSWORD_CHANGE_REQUIRED

    _rotate_password(stack, admin_id)

    listed = stack.client.get("/api/v1/operator/users", headers=headers)
    assert listed.status_code == 200, listed.text
    users = listed.json()["users"]
    assert [(u["subject_id"], u["roles"], u["status"]) for u in users] == [
        (admin_id, ["platform_admin"], "active")
    ]
    trail = stack.client.get("/api/v1/operator/users/audit-trail", headers=headers).json()
    assert [e["event_type"] for e in trail["events"]] == ["identity.account.bootstrap"]
    # The pure administrator is not granted business operator data.
    assert stack.client.get("/api/v1/operator/bootstrap", headers=headers).status_code == 403


def test_explicit_operator_view_grant_preserves_admin_scope_status_and_audit(stack: Any) -> None:
    admin_id = _bootstrap_admin(stack)
    _rotate_password(stack, admin_id)
    admin = _sign_in(stack, admin_id)
    before = stack.client.get(f"/api/v1/operator/users/{admin_id}", headers=admin).json()
    assert stack.client.get("/api/v1/operator/bootstrap", headers=admin).status_code == 403
    saved = stack.client.post(
        "/api/v1/operator/users", headers=admin,
        json={"subjectId": admin_id, "roles": ["platform_admin", "operator_viewer"],
              "scope": before["scope"], "status": before["status"], "reason": "Explicit bounded read grant"},
    )
    assert saved.status_code == 200, saved.text
    after = saved.json()["user"]
    assert after["scope"] == before["scope"]
    assert after["status"] == before["status"] == "active"
    # Per-request durable resolution: no role/tenant claims or new auth path.
    assert _roles_seen_by_boundary(stack, admin) == {"platform_admin", "operator_viewer"}
    assert stack.client.get("/api/v1/operator/bootstrap", headers=admin).status_code == 200
    assert stack.client.get("/api/v1/operator/users", headers=admin).status_code == 200
    assert stack.client.get("/api/v1/operator/bootstrap", headers={**admin, "X-Operator-Role": "expansion-manager"}).status_code == 403
    assert "identity.account.roles_updated" in _audit_types(stack)
    trail = stack.client.get("/api/v1/operator/users/audit-trail", headers=admin).json()["events"]
    event = next(e for e in trail if e["event_type"] == "identity.account.roles_updated")
    assert event["metadata"]["roles_before"] == ["platform_admin"]
    assert set(event["metadata"]["roles_after"]) == {"platform_admin", "operator_viewer"}
    # Another unmodified pure administrator still cannot read business data.
    pure_id = _invited_account(stack, "other.admin", "platform_admin")
    pure = _sign_in(stack, pure_id)
    assert stack.client.get("/api/v1/operator/bootstrap", headers=pure).status_code == 403


def test_role_change_is_authoritative_tenant_scoped_and_audited(stack: Any) -> None:
    admin_id = _bootstrap_admin(stack)
    _rotate_password(stack, admin_id)
    admin = _sign_in(stack, admin_id)
    member_id = _invited_account(stack, "ops.member", "operations_manager")
    member = _sign_in(stack, member_id)
    foreign_id = _invited_account(stack, "foreign.member", "operations_manager", OTHER_TENANT)

    assert _roles_seen_by_boundary(stack, member) == {"operations_manager"}
    # Wrong role: an operations manager cannot administer users.
    assert stack.client.get("/api/v1/operator/users", headers=member).status_code == 403
    assert stack.client.get("/api/v1/operator/users").status_code == 401

    saved = stack.client.post(
        "/api/v1/operator/users",
        headers=admin,
        json={
            "subjectId": member_id,
            "roles": ["auditor"],
            "scope": {"tenant_id": "tenant-default", "brand_ids": ["brand-a"], "clearance": "RESTRICTED"},
            "reason": "move to audit",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["user"]["roles"] == ["auditor"]
    assert _q(stack, "SELECT role FROM identity.account_roles WHERE account_id = %s", (member_id,)) == [("auditor",)]
    # The very next authenticated request carries the new authoritative roles.
    assert _roles_seen_by_boundary(stack, member) == {"auditor"}
    assert stack.client.get("/api/v1/operator/users", headers=admin).json()["count"] == 2

    # Cross-tenant accounts are invisible and immutable.
    assert stack.client.get(f"/api/v1/operator/users/{foreign_id}", headers=admin).status_code == 404
    cross = stack.client.post(
        "/api/v1/operator/users", headers=admin, json={"subjectId": foreign_id, "roles": ["auditor"]}
    )
    assert cross.status_code == 422
    assert _q(stack, "SELECT role FROM identity.account_roles WHERE account_id = %s", (foreign_id,)) == [
        ("operations_manager",)
    ]
    other_scope = stack.client.post(
        "/api/v1/operator/users",
        headers=admin,
        json={"subjectId": member_id, "roles": ["auditor"], "scope": {"tenant_id": OTHER_TENANT}},
    )
    assert other_scope.status_code == 422

    # No account manufacturing from the role editor.
    created = stack.client.post(
        "/api/v1/operator/users", headers=admin, json={"subjectId": "brand-new", "roles": ["auditor"]}
    )
    assert created.status_code == 422
    assert _q(stack, "SELECT count(*) FROM identity.accounts") == [(3,)]

    assert _audit_types(stack).count("identity.account.roles_updated") == 1
    trail = stack.client.get(f"/api/v1/operator/users/audit-trail?subject_id={member_id}", headers=admin).json()
    assert trail["events"][0]["metadata"]["roles_before"] == ["operations_manager"]
    assert trail["events"][0]["actor"] == admin_id


def _live_gate() -> Any:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "check_live_e2e_gate_identity_contract", Path("delivery_toolchain/e2e/check_live_e2e_gate.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def test_live_gate_tenant_probe_is_refused_by_the_real_tenant_policy(stack: Any) -> None:
    """The dev-admin gate's exact foreign-tenant probe, against the real router and PostgreSQL.

    Binds the gate's acceptance (422 + identity tenant-policy text naming the
    probe tenant, then an identical readback) to what the service actually
    returns, so the offline gate fixture cannot drift from the API contract.
    """
    gate = _live_gate()
    admin_id = _bootstrap_admin(stack)
    _rotate_password(stack, admin_id)
    admin = _sign_in(stack, admin_id)

    before = stack.client.get("/api/v1/operator/users", headers=admin)
    assert before.status_code == 200, before.text
    [record] = before.json()["users"]
    snapshot = gate._identity_snapshot(record)
    assert snapshot is not None and snapshot["scope"]["tenant_id"] == TENANT
    foreign = gate._foreign_tenant_for(TENANT)
    db_before = _q(
        stack,
        "SELECT tenant_id::text, status FROM identity.accounts WHERE account_id = %s",
        (admin_id,),
    )

    probe = stack.client.post(
        "/api/v1/operator/users",
        headers=admin,
        json={
            "subjectId": snapshot["subject_id"],
            "roles": snapshot["roles"],
            "scope": {**snapshot["scope"], "tenant_id": foreign},
            "status": snapshot["status"],
            "reason": "foreign tenant scope boundary probe",
        },
    )
    assert probe.status_code == 422, probe.text
    detail = probe.json()["detail"]
    assert gate.TENANT_POLICY_REFUSAL_TEXT in detail and foreign in detail

    after = stack.client.get("/api/v1/operator/users", headers=admin).json()["users"]
    assert [gate._identity_snapshot(u) for u in after] == [snapshot]
    assert _q(
        stack,
        "SELECT tenant_id::text, status FROM identity.accounts WHERE account_id = %s",
        (admin_id,),
    ) == db_before
    assert _audit_types(stack).count("identity.account.roles_updated") == 0


def test_disable_revokes_sessions_and_reenable_requires_admin(stack: Any) -> None:
    admin_id = _bootstrap_admin(stack)
    _rotate_password(stack, admin_id)
    admin = _sign_in(stack, admin_id)
    member_id = _invited_account(stack, "ops.member", "platform_admin")
    member = _sign_in(stack, member_id)
    assert stack.client.get("/api/v1/operator/users", headers=member).status_code == 200

    disabled = stack.client.post(
        f"/api/v1/operator/users/{member_id}/status", headers=admin, json={"status": "disabled", "reason": "left"}
    )
    assert disabled.status_code == 200, disabled.text
    assert _q(
        stack, "SELECT count(*) FROM identity.sessions WHERE account_id = %s AND revoked_at IS NULL", (member_id,)
    ) == [(0,)]
    assert _q(
        stack, "SELECT DISTINCT revoked_reason FROM identity.sessions WHERE account_id = %s", (member_id,)
    ) == [("admin_disable_account",)]
    assert stack.client.get("/api/v1/operator/users", headers=member).status_code == 401

    enabled = stack.client.post(
        f"/api/v1/operator/users/{member_id}/status", headers=admin, json={"status": "active"}
    )
    assert enabled.status_code == 200
    assert _q(stack, "SELECT status FROM identity.accounts WHERE account_id = %s", (member_id,)) == [("active",)]
    # Old sessions stay revoked; the account must sign in again.
    assert stack.client.get("/api/v1/operator/users", headers=member).status_code == 401
    assert stack.client.get("/api/v1/operator/users", headers=_sign_in(stack, member_id)).status_code == 200
    assert _audit_types(stack)[-2:] == ["identity.account.disabled", "identity.account.enabled"]

    stack.service.set_user_status(subject_id=member_id, status="disabled", tenant_id=TENANT)
    with pytest.raises(UserRolePolicyError, match="requires platform_admin"):
        stack.service.set_user_status(
            subject_id=member_id, status="active", tenant_id=TENANT, actor_roles=frozenset({"auditor"})
        )


def test_last_platform_admin_cannot_lock_the_tenant_out(stack: Any) -> None:
    admin_id = _bootstrap_admin(stack)
    _rotate_password(stack, admin_id)
    admin = _sign_in(stack, admin_id)

    assert stack.client.post(
        f"/api/v1/operator/users/{admin_id}/status", headers=admin, json={"status": "disabled"}
    ).status_code == 422
    assert stack.client.post(
        "/api/v1/operator/users", headers=admin, json={"subjectId": admin_id, "roles": ["auditor"]}
    ).status_code == 422
    assert _q(stack, "SELECT role FROM identity.account_roles WHERE account_id = %s", (admin_id,)) == [
        ("platform_admin",)
    ]
    assert _q(stack, "SELECT status FROM identity.accounts WHERE account_id = %s", (admin_id,)) == [("active",)]


def test_audit_failure_rolls_back_the_privilege_change(stack: Any) -> None:
    admin_id = _bootstrap_admin(stack)
    member_id = _invited_account(stack, "ops.member", "operations_manager")
    member = _sign_in(stack, member_id)

    class BrokenAudit:
        def record(self, event: Any) -> Any:
            raise RuntimeError("audit sink unavailable")

    broken = IdentityUserRoleManagementService(engine=stack.engine, audit_log=BrokenAudit())
    with pytest.raises(RuntimeError):
        broken.save_user(subject_id=member_id, roles=["platform_admin"], tenant_id=TENANT, actor_name=admin_id)
    with pytest.raises(RuntimeError):
        broken.set_user_status(subject_id=member_id, status="disabled", tenant_id=TENANT)

    assert _q(stack, "SELECT role FROM identity.account_roles WHERE account_id = %s", (member_id,)) == [
        ("operations_manager",)
    ]
    assert _q(stack, "SELECT status FROM identity.accounts WHERE account_id = %s", (member_id,)) == [("active",)]
    assert _roles_seen_by_boundary(stack, member) == {"operations_manager"}


def test_service_refuses_without_a_verified_tenant(stack: Any) -> None:
    with pytest.raises(UserRolePolicyError):
        stack.service.list_users(tenant_id=None)
    with pytest.raises(UserRolePolicyError):
        stack.service.list_users(tenant_id="tenant-default")


def test_unavailable_identity_service_refuses_instead_of_document_success() -> None:
    app = FastAPI()
    app.include_router(create_user_role_sub_router(UnavailableUserRoleManagementService()), prefix="/x")
    client = TestClient(app)
    for response in (
        client.get("/x/users"),
        client.post("/x/users", json={"subjectId": str(uuid4()), "roles": ["auditor"]}),
        client.post(f"/x/users/{uuid4()}/status", json={"status": "disabled"}),
    ):
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "IDENTITY_PERSISTENCE_UNAVAILABLE"


def test_role_editor_preserves_authoritative_hidden_scope_axes(stack: Any) -> None:
    """Ordinary UI name/role edits must not wipe unedited/hidden scope axes."""
    admin_id = _bootstrap_admin(stack)
    _rotate_password(stack, admin_id)
    admin = _sign_in(stack, admin_id)
    member_id = _invited_account(stack, "scoped.member", "operations_manager")
    member = _sign_in(stack, member_id)
    scope = {
        "tenant_id": TENANT,
        "brand_ids": [],
        "region_ids": [],
        "store_ids": [],
        "assigned_area_ids": ["allowed-area"],
        "heat_zone_ids": ["allowed-zone"],
        "modules": ["allowed-module"],
        "clearance": "CONFIDENTIAL",
    }
    seeded = stack.client.post(
        "/api/v1/operator/users",
        headers=admin,
        json={"subjectId": member_id, "roles": ["operations_manager"], "scope": scope},
    )
    assert seeded.status_code == 200, seeded.text
    before_status = stack.client.get("/api/v1/operator/bootstrap", headers=member).status_code
    assert before_status == 403
    before = seeded.json()["user"]

    # Exact fields submitted by UserRoleManagementController.tsx omitting assigned_area_ids, heat_zone_ids, modules
    saved = stack.client.post(
        "/api/v1/operator/users",
        headers=admin,
        json={
            "subjectId": member_id,
            "email": before["email"],
            "name": "Name-only edit",
            "roles": before["roles"],
            "scope": {
                axis: before["scope"][axis]
                for axis in ("tenant_id", "brand_ids", "region_ids", "store_ids", "clearance")
            },
            "attributes": before["attributes"],
            "status": before["status"],
            "reason": "Reviewer: equivalent existing UI name edit",
        },
    )
    assert saved.status_code == 200, saved.text
    after_scope = saved.json()["user"]["scope"]
    assert after_scope["assigned_area_ids"] == ["allowed-area"]
    assert after_scope["heat_zone_ids"] == ["allowed-zone"]
    assert after_scope["modules"] == ["allowed-module"]

    after_status = stack.client.get("/api/v1/operator/bootstrap", headers=member).status_code
    assert after_status == 403, "A name-only UI edit broadened authoritative module access"

    persisted = _q(
        stack,
        "SELECT assigned_area_ids, heat_zone_ids, modules FROM identity.account_scopes WHERE account_id = %s",
        (member_id,),
    )
    assert persisted == [(["allowed-area"], ["allowed-zone"], ["allowed-module"])]


def test_explicit_scope_updates_are_applied_and_can_be_cleared(stack: Any) -> None:
    """Explicitly provided scope axes are updated or cleared while omitted axes are preserved."""
    admin_id = _bootstrap_admin(stack)
    _rotate_password(stack, admin_id)
    admin = _sign_in(stack, admin_id)
    member_id = _invited_account(stack, "scope.explicit.member", "operations_manager")

    initial_scope = {
        "tenant_id": TENANT,
        "brand_ids": ["b1"],
        "region_ids": ["r1"],
        "store_ids": ["s1"],
        "assigned_area_ids": ["area-1"],
        "heat_zone_ids": ["zone-1"],
        "modules": ["mod-1"],
        "clearance": "RESTRICTED",
    }
    seeded = stack.client.post(
        "/api/v1/operator/users",
        headers=admin,
        json={"subjectId": member_id, "roles": ["operations_manager"], "scope": initial_scope},
    )
    assert seeded.status_code == 200, seeded.text

    # Explicitly update modules and brand_ids, omit others
    updated = stack.client.post(
        "/api/v1/operator/users",
        headers=admin,
        json={
            "subjectId": member_id,
            "roles": ["operations_manager"],
            "scope": {"tenant_id": TENANT, "modules": ["mod-2"], "brand_ids": ["b2"]},
        },
    )
    assert updated.status_code == 200, updated.text
    u_scope = updated.json()["user"]["scope"]
    assert u_scope["modules"] == ["mod-2"]
    assert u_scope["brand_ids"] == ["b2"]
    assert u_scope["assigned_area_ids"] == ["area-1"]
    assert u_scope["heat_zone_ids"] == ["zone-1"]
    assert u_scope["region_ids"] == ["r1"]
    assert u_scope["store_ids"] == ["s1"]
    assert u_scope["clearance"] == "RESTRICTED"

    # Explicitly clear modules to empty list
    cleared = stack.client.post(
        "/api/v1/operator/users",
        headers=admin,
        json={
            "subjectId": member_id,
            "roles": ["operations_manager"],
            "scope": {"tenant_id": TENANT, "modules": []},
        },
    )
    assert cleared.status_code == 200, cleared.text
    c_scope = cleared.json()["user"]["scope"]
    assert c_scope["modules"] == []
    assert c_scope["brand_ids"] == ["b2"]
    assert c_scope["assigned_area_ids"] == ["area-1"]

