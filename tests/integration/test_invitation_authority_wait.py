"""Mounted auth + disposable actual PG/two pools, not live invitation/recovery effects."""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from modules.opsboard.application.identity_user_role_management import (
    IdentityUserRoleManagementService,
)
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary, encode_compact_jwt
from shared.identity.invitation_service import InvitationService
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.postgresql import PostgresEngine
from tests.identity.test_identity_user_role_management import (
    AUDIENCE,
    LOCAL_ISSUER,
    TENANT,
    _app,
    _bootstrap_admin,
    _invited_account,
    _q,
    _rotate_password,
    _sign_in,
)
from tests.identity.test_identity_user_role_management import stack as stack
from tests.integration.test_dev_smoke_recovery import PRESERVED_ACCOUNT_ID, RECOVERY_PATH, TENANT_ID
from tests.integration.test_dev_smoke_recovery import identity_stack as identity_stack
from tests.integration.test_dev_smoke_recovery import recovery as recovery
from tests.integration.test_dev_smoke_recovery import recovery_runtime as recovery_runtime
from tests.integration.test_oidc_admin_session_bridge import BRIDGE_KEY, OIDC_ISSUER, OIDC_KEY

PATH = "/api/v1/operator/users/invitations"


def oidc(s: Any, actor: str, tenant: str) -> tuple[Any, dict[str, str]]:
    _q(
        s,
        "INSERT INTO identity.federated_identities(account_id,issuer,subject,linked_by) VALUES(%s,%s,'offline-subject','offline-fixture') RETURNING account_id",
        (actor, OIDC_ISSUER),
    )
    session = s.sessions.create_session(account_id=UUID(actor), provider="oidc")
    boundary = AuthenticationBoundary(
        AuthBoundaryConfig(
            oidc_issuer=OIDC_ISSUER,
            oidc_audiences=frozenset({AUDIENCE}),
            oidc_signing_keys={OIDC_KEY.kid: OIDC_KEY},
            local_issuer=LOCAL_ISSUER,
            local_audiences=frozenset({AUDIENCE}),
            local_signing_keys={BRIDGE_KEY.kid: BRIDGE_KEY},
            identity_store=s.identity,
            session_service=s.sessions,
        ),
        audit_log=s.audit,
    )
    now = int(time.time())
    token = encode_compact_jwt(
        {
            "iss": OIDC_ISSUER,
            "sub": "offline-subject",
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + 120,
        },
        OIDC_KEY,
    )
    bridge = encode_compact_jwt(
        {
            "iss": LOCAL_ISSUER,
            "sub": actor,
            "tenant_id": tenant,
            "sid": str(session.session_id),
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + 120,
            "purpose": "oidc-web-session.v1",
            "oidc_token_digest": hashlib.sha256(token.encode()).hexdigest(),
        },
        BRIDGE_KEY,
    )
    return boundary, {"authorization": f"Bearer {token}", "x-odp-oidc-session": bridge}


def client(s: Any, boundary: Any) -> tuple[Any, Any]:
    engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    audit = DurableAuditLog(engine)
    service = IdentityUserRoleManagementService(engine=engine, audit_log=audit)
    return TestClient(
        _app(SimpleNamespace(audit=audit, boundary=boundary, sessions=s.sessions), service)
    ), engine


def pending(
    s: Any,
    engine: Any,
    c: Any,
    headers: Any,
    path: str,
    body: Any,
    change: Any,
    monkeypatch: Any,
    tenant: str = TENANT,
) -> Any:
    reached = threading.Event()
    original = InvitationService._lock_tenant

    def wait(self: Any, selected: str) -> None:
        if self._engine is engine:
            reached.set()
        original(self, selected)

    monkeypatch.setattr(InvitationService, "_lock_tenant", wait)
    with s.db.connect(autocommit=False) as blocker:
        blocker.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"identity-admin:{tenant}",))
        change(blocker)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(c.post, path, headers=headers, json=body)
            assert reached.wait(10), (
                "real auth/permission dependency must pass BEFORE tenant-lock wait"
            )
            blocker.commit()
            return future.result(timeout=15)


@pytest.mark.parametrize("operation", ["issue", "revoke"])
@pytest.mark.parametrize("restriction", ["module", "clearance"])
def test_invitation_scope_restricted_during_actual_tenant_wait_refuses(
    stack: Any,
    monkeypatch: Any,
    operation: str,
    restriction: str,
) -> None:
    s = stack
    actor = _bootstrap_admin(s)
    _rotate_password(s, actor)
    _q(
        s,
        "INSERT INTO identity.account_scopes(account_id,modules,clearance) VALUES(%s,'[]','CONFIDENTIAL') RETURNING account_id",
        (actor,),
    )
    headers = _sign_in(s, actor)
    c, engine = client(s, s.boundary)
    try:
        path = PATH
        body = {"email": "new-offline@example.invalid"}
        if operation == "revoke":
            issued = c.post(PATH, headers=headers, json=body)
            assert issued.status_code == 201
            path = PATH + "/" + issued.json()["invitation_id"] + "/revoke"
            body = {}
        before = _q(
            s, "SELECT invitation_id,revoked_at FROM identity.invitations ORDER BY invitation_id"
        )

        def restrict(conn: Any) -> None:
            value = (
                "modules='[\"intake\"]'::jsonb" if restriction == "module" else "clearance='PUBLIC'"
            )
            assert (
                conn.execute(
                    "UPDATE identity.account_scopes SET " + value + " WHERE account_id=%s", (actor,)
                ).rowcount
                == 1
            )

        response = pending(s, engine, c, headers, path, body, restrict, monkeypatch)
        assert response.status_code == 403, response.status_code
        assert (
            _q(
                s,
                "SELECT invitation_id,revoked_at FROM identity.invitations ORDER BY invitation_id",
            )
            == before
        )
        assert c.post(path, headers=headers, json=body).status_code == 403
    finally:
        c.close()
        engine.close()


@pytest.mark.parametrize("operation", ["issue", "revoke"])
@pytest.mark.parametrize("password_row", [True, False])
@pytest.mark.parametrize("link_change", ["unlink", "reassign"])
def test_linked_oidc_invitation_support_and_unlink_during_wait(
    stack: Any,
    monkeypatch: Any,
    operation: str,
    password_row: bool,
    link_change: str,
) -> None:
    s = stack
    actor = _bootstrap_admin(s)
    _rotate_password(s, actor)
    # Pre-create revoke target using known healthy local-password authority.
    local = _sign_in(s, actor)
    path = PATH
    body = {"email": "oidc-offline@example.invalid"}
    if operation == "revoke":
        issued = s.client.post(PATH, headers=local, json=body)
        assert issued.status_code == 201
        path = PATH + "/" + issued.json()["invitation_id"] + "/revoke"
        body = {}
    if not password_row:
        _q(
            s,
            "DELETE FROM identity.password_credentials WHERE account_id=%s RETURNING account_id",
            (actor,),
        )
    boundary, headers = oidc(s, actor, TENANT)
    c, engine = client(s, boundary)
    try:
        if not password_row:
            response = c.post(path, headers=headers, json=body)
            assert response.status_code == (201 if operation == "issue" else 200), (
                response.status_code
            )
            return
        before = _q(
            s, "SELECT invitation_id,revoked_at FROM identity.invitations ORDER BY invitation_id"
        )

        peer = (
            _invited_account(s, "offline-linked-peer", "auditor")
            if link_change == "reassign"
            else None
        )

        def unlink(conn: Any) -> None:
            if link_change == "reassign":
                result = conn.execute(
                    "UPDATE identity.federated_identities SET account_id=%s WHERE account_id=%s",
                    (peer, actor),
                )
            else:
                result = conn.execute(
                    "DELETE FROM identity.federated_identities WHERE account_id=%s", (actor,)
                )
            assert result.rowcount == 1

        response = pending(s, engine, c, headers, path, body, unlink, monkeypatch)
        assert response.status_code == 403, response.status_code
        assert (
            _q(
                s,
                "SELECT invitation_id,revoked_at FROM identity.invitations ORDER BY invitation_id",
            )
            == before
        )
    finally:
        c.close()
        engine.close()


def test_verified_oidc_recovery_reservation_refuses_link_removed_during_lock_wait(
    recovery_runtime: Any,
    monkeypatch: Any,
) -> None:
    s = recovery_runtime
    boundary, headers = oidc(s, PRESERVED_ACCOUNT_ID, TENANT_ID)
    c, engine = client(s, boundary)
    try:
        before = _q(
            s, "SELECT account_id,phc_hash FROM identity.password_credentials ORDER BY account_id"
        )

        def unlink(conn: Any) -> None:
            assert (
                conn.execute(
                    "DELETE FROM identity.federated_identities WHERE account_id=%s",
                    (PRESERVED_ACCOUNT_ID,),
                ).rowcount
                == 1
            )

        response = pending(
            s,
            engine,
            c,
            headers,
            RECOVERY_PATH,
            {"action": "reserve", "plan": s.plan},
            unlink,
            monkeypatch,
            TENANT_ID,
        )
        assert response.status_code == 403, response.status_code
        assert _q(
            s,
            "SELECT count(*) FROM odp_runtime.durable_audit_events WHERE event_type='identity.dev_smoke.recovery'",
        ) == [(0,)]
        assert (
            _q(
                s,
                "SELECT account_id,phc_hash FROM identity.password_credentials ORDER BY account_id",
            )
            == before
        )
    finally:
        c.close()
        engine.close()
