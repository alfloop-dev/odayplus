"""Actual PG federation/session and mounted writes; only offline synthetic signed tokens."""

from __future__ import annotations

import hashlib
import time
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    Credentials,
    SigningKey,
    encode_compact_jwt,
)
from tests.identity.test_identity_user_role_management import (
    AUDIENCE,
    LOCAL_ISSUER,
    TENANT,
    _app,
    _bootstrap_admin,
    _invited_account,
    _q,
    _rotate_password,
)
from tests.identity.test_identity_user_role_management import stack as stack

OIDC_ISSUER = "https://offline.invalid/issuer"
OIDC_KEY = SigningKey(
    kid="oidc-offline", algorithm="HS256", secret=b"offline-federation-signing-key-32bytes"
)
BRIDGE_KEY = SigningKey(
    kid="local-default",
    algorithm="HS256",
    secret=b"offline-api-bridge-signing-key-at-least-thirty-two-bytes",
)


@pytest.mark.parametrize("password_row", [True, False])
@pytest.mark.parametrize(
    "binding",
    [
        "healthy",
        "missing",
        "wrong-digest",
        "wrong-account",
        "wrong-tenant",
        "wrong-provider",
        "expired",
        "unlinked-during-wait",
    ],
)
def test_linked_oidc_admin_uses_signed_session_binding_not_caller_sid(
    stack: Any, password_row: bool, binding: str
) -> None:
    s = stack
    actor = _bootstrap_admin(s)
    _rotate_password(s, actor)
    target = _invited_account(s, "offline-target", "auditor")
    if not password_row:
        _q(
            s,
            "DELETE FROM identity.password_credentials WHERE account_id=%s RETURNING account_id",
            (actor,),
        )
    _q(
        s,
        "INSERT INTO identity.federated_identities (account_id,issuer,subject,linked_by) VALUES (%s,%s,%s,'offline-fixture') RETURNING account_id",
        (actor, OIDC_ISSUER, "oidc-subject"),
    )
    session = s.sessions.create_session(
        account_id=UUID(actor), provider="local_password" if binding == "wrong-provider" else "oidc"
    )
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
    oidc = encode_compact_jwt(
        {"iss": OIDC_ISSUER, "sub": "oidc-subject", "aud": AUDIENCE, "iat": now, "exp": now + 120},
        OIDC_KEY,
    )
    claims = {
        "iss": LOCAL_ISSUER,
        "sub": actor,
        "tenant_id": TENANT,
        "sid": str(session.session_id),
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 120,
        "purpose": "oidc-web-session.v1",
        "oidc_token_digest": hashlib.sha256(oidc.encode()).hexdigest(),
    }
    if binding == "wrong-digest":
        claims["oidc_token_digest"] = "0" * 64
    if binding == "wrong-account":
        claims["sub"] = target
    if binding == "wrong-tenant":
        claims["tenant_id"] = "00000000-0000-0000-0000-000000000001"
    if binding == "expired":
        claims.update(iat=now - 300, exp=now - 120)
    bridge = encode_compact_jwt(claims, BRIDGE_KEY)
    headers = {"authorization": f"Bearer {oidc}", "x-odp-oidc-session": bridge}
    if binding == "missing":
        headers.pop("x-odp-oidc-session")
        headers["x-session-id"] = str(session.session_id)
    assert boundary.authenticate(Credentials.from_headers(headers)).authenticated == (
        binding in {"healthy", "missing", "unlinked-during-wait"}
    )
    client = TestClient(
        _app(SimpleNamespace(audit=s.audit, boundary=boundary, sessions=s.sessions), s.service)
    )
    try:
        if binding == "unlinked-during-wait":
            import threading
            from concurrent.futures import ThreadPoolExecutor

            reached = threading.Event()
            original = s.service._lock_tenant

            def wait(tenant: str) -> None:
                reached.set()
                original(tenant)

            s.service._lock_tenant = wait
            with s.db.connect(autocommit=False) as blocker:
                blocker.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"identity-admin:{TENANT}",)
                )
                blocker.execute(
                    "DELETE FROM identity.federated_identities WHERE account_id=%s", (actor,)
                )
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        client.post,
                        "/api/v1/operator/users",
                        headers=headers,
                        json={"subjectId": target, "roles": ["operations_manager"]},
                    )
                    assert reached.wait(10), (
                        "verified OLD linkage must reach tenant lock before committed unlink"
                    )
                    blocker.commit()
                    response = future.result(timeout=15)
        else:
            response = client.post(
                "/api/v1/operator/users",
                headers=headers,
                json={"subjectId": target, "roles": ["operations_manager"]},
            )
        if binding != "healthy":
            assert response.status_code in {401, 403}, response.text
            assert _q(
                s, "SELECT role FROM identity.account_roles WHERE account_id=%s", (target,)
            ) == [("auditor",)]
            assert _q(
                s,
                "SELECT count(*) FROM odp_runtime.durable_audit_events WHERE event_type='identity.account.roles_updated'",
            ) == [(0,)]
            return
        assert response.status_code == 200, response.text
        assert _q(s, "SELECT role FROM identity.account_roles WHERE account_id=%s", (target,)) == [
            ("operations_manager",)
        ]
        s.sessions.revoke_session(session.session_id, "offline-logout")
        refused = client.post(
            "/api/v1/operator/users",
            headers=headers,
            json={"subjectId": target, "roles": ["auditor"]},
        )
        assert refused.status_code in {401, 403}, refused.text
        assert _q(s, "SELECT role FROM identity.account_roles WHERE account_id=%s", (target,)) == [
            ("operations_manager",)
        ]
    finally:
        client.close()
