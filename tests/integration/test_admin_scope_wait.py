"""Actual PG mounted authorisation, committed scope restriction while tenant lock waits."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from modules.opsboard.application.identity_user_role_management import (
    IdentityUserRoleManagementService,
)
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.postgresql import PostgresEngine
from tests.identity.test_identity_user_role_management import (
    TENANT,
    _app,
    _bootstrap_admin,
    _q,
    _rotate_password,
    _sign_in,
)
from tests.identity.test_identity_user_role_management import stack as stack


@pytest.mark.parametrize("restriction", ["module", "clearance"])
def test_real_scope_revocation_while_tenant_lock_waiting_refuses_pending_self_restore(
    stack: Any, restriction: str
) -> None:
    s = stack
    actor = _bootstrap_admin(s)
    _rotate_password(s, actor)
    _q(
        s,
        "INSERT INTO identity.account_scopes (account_id,modules,clearance) VALUES (%s,'[]','CONFIDENTIAL') ON CONFLICT (account_id) DO NOTHING RETURNING account_id",
        (actor,),
    )
    assert _q(
        s, "SELECT modules,clearance FROM identity.account_scopes WHERE account_id=%s", (actor,)
    ) == [([], "CONFIDENTIAL")]
    headers = _sign_in(s, actor)
    engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    audit = DurableAuditLog(engine)
    service = IdentityUserRoleManagementService(engine=engine, audit_log=audit)
    client = TestClient(
        _app(SimpleNamespace(audit=audit, boundary=s.boundary, sessions=s.sessions), service)
    )
    reached = threading.Event()
    original = service._lock_tenant

    def waiting(tenant: str) -> None:
        reached.set()
        original(tenant)

    service._lock_tenant = waiting
    try:
        with s.db.connect(autocommit=False) as blocker:
            blocker.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"identity-admin:{TENANT}",)
            )
            if restriction == "module":
                blocker.execute(
                    "UPDATE identity.account_scopes SET modules='[\"intake\"]'::jsonb WHERE account_id=%s",
                    (actor,),
                )
            else:
                blocker.execute(
                    "UPDATE identity.account_scopes SET clearance='PUBLIC' WHERE account_id=%s",
                    (actor,),
                )
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    client.post,
                    "/api/v1/operator/users",
                    headers=headers,
                    json={
                        "subjectId": actor,
                        "roles": ["platform_admin"],
                        "scope": {"modules": [], "clearance": "CONFIDENTIAL"},
                    },
                )
                assert reached.wait(10), (
                    "actual JWT/scope dependency must pass BEFORE waiting at real tenant lock"
                )
                blocker.commit()
                response = future.result(timeout=15)
        assert response.status_code == 403, response.text
        fresh = client.post(
            "/api/v1/operator/users",
            headers=headers,
            json={
                "subjectId": actor,
                "roles": ["platform_admin"],
                "scope": {"modules": [], "clearance": "CONFIDENTIAL"},
            },
        )
        assert fresh.status_code == 403, fresh.text
        assert _q(
            s,
            "SELECT count(*) FROM odp_runtime.durable_audit_events WHERE event_type='identity.account.roles_updated'",
        ) == [(0,)]
        if restriction == "module":
            assert _q(
                s, "SELECT modules FROM identity.account_scopes WHERE account_id=%s", (actor,)
            ) == [(["intake"],)]
        else:
            assert _q(
                s, "SELECT clearance FROM identity.account_scopes WHERE account_id=%s", (actor,)
            ) == [("PUBLIC",)]
    finally:
        client.close()
        engine.close()
