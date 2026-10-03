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
