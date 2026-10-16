"""Actual PG + authenticated mounted mutation waits, synthetic offline accounts."""

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
from modules.opsboard.auth import Credentials
from shared.infrastructure.persistence.postgresql import PostgresEngine
from tests.identity.test_identity_user_role_management import (
    TENANT,
    _app,
    _bootstrap_admin,
    _invited_account,
    _q,
    _rotate_password,
    _sign_in,
)
from tests.identity.test_identity_user_role_management import stack as stack


@pytest.mark.parametrize("mutation", ["restore-self-role", "disable-other-admin"])
def test_admin_demoted_during_real_tenant_lock_wait_cannot_commit_cached_authority(
    stack: Any, mutation: str
) -> None:
    s = stack
    actor = _bootstrap_admin(s)
    _rotate_password(s, actor)
    other = _invited_account(s, "other-admin", "platform_admin")
    _invited_account(
        s, "surviving-admin", "platform_admin"
    )  # isolate authority from last-admin policy
    headers = _sign_in(s, actor)
    engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    # Audits must share the mutation engine, not the first pool/transaction.
    from shared.infrastructure.persistence.audit_log import DurableAuditLog

    service = IdentityUserRoleManagementService(engine=engine, audit_log=DurableAuditLog(engine))
    second = SimpleNamespace(audit=service._audit_log, boundary=s.boundary, sessions=s.sessions)
    client = TestClient(_app(second, service))
    reached = threading.Event()
    original = service._lock_tenant

    def wait(tenant: str) -> None:
        reached.set()
        original(tenant)

    service._lock_tenant = wait
    try:
        with s.db.connect(autocommit=False) as blocker:
            blocker.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"identity-admin:{TENANT}",)
            )
            blocker.execute(
                "UPDATE identity.account_roles SET role='auditor' WHERE account_id=%s AND role='platform_admin'",
                (actor,),
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                if mutation == "restore-self-role":
                    future = pool.submit(
                        client.post,
                        "/api/v1/operator/users",
                        headers=headers,
                        json={"subjectId": actor, "roles": ["platform_admin"]},
                    )
                else:
                    future = pool.submit(
                        client.post,
                        f"/api/v1/operator/users/{other}/status",
                        headers=headers,
                        json={"status": "disabled"},
                    )
                assert reached.wait(10), (
                    "request must pass real pre-transaction HTTP auth and reach held PG tenant lock"
                )
                blocker.commit()
                response = future.result(timeout=15)
        assert response.status_code == 403, response.text
        assert _q(s, "SELECT role FROM identity.account_roles WHERE account_id=%s", (actor,)) == [
            ("auditor",)
        ]
        assert _q(s, "SELECT status FROM identity.accounts WHERE account_id=%s", (other,)) == [
            ("active",)
        ]
        assert _q(
            s,
            "SELECT count(*) FROM odp_runtime.durable_audit_events WHERE event_type IN ('identity.account.roles_updated','identity.account.disabled')",
        ) == [(0,)]
    finally:
        client.close()
        engine.close()


@pytest.mark.parametrize("mutation", ["roles", "status"])
def test_admin_natural_expiry_during_provisional_audit_rolls_back_the_whole_mutation(
    stack: Any, mutation: str
) -> None:
    import time
    from datetime import UTC, datetime, timedelta

    s = stack
    actor = _bootstrap_admin(s)
    _rotate_password(s, actor)
    member = _invited_account(s, "offline-member", "operations_manager")
    headers = _sign_in(s, actor)
    sid = s.boundary.authenticate(Credentials.from_headers(headers)).principal.attributes["sid"]
    deadline = datetime.now(UTC) + timedelta(seconds=2)
    _q(
        s,
        "UPDATE identity.sessions SET absolute_expires_at=%s WHERE session_id=%s RETURNING session_id",
        (deadline, sid),
    )
    original = s.service._record

    def record(**kwargs: Any) -> None:
        original(**kwargs)
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if s.engine.query_one(
                "SELECT clock_timestamp() >= CAST(? AS timestamptz) AS expired", (deadline,)
            )["expired"]:
                return
            time.sleep(0.01)
        raise AssertionError(
            "DB must observe natural expiry while provisional rows are in the real transaction"
        )

    s.service._record = record
    if mutation == "roles":
        response = s.client.post(
            "/api/v1/operator/users",
            headers=headers,
            json={"subjectId": member, "roles": ["auditor"]},
        )
    else:
        response = s.client.post(
            f"/api/v1/operator/users/{member}/status", headers=headers, json={"status": "disabled"}
        )
    assert response.status_code == 403, response.text
    assert _q(s, "SELECT role FROM identity.account_roles WHERE account_id=%s", (member,)) == [
        ("operations_manager",)
    ]
    assert _q(s, "SELECT status FROM identity.accounts WHERE account_id=%s", (member,)) == [
        ("active",)
    ]
    assert _q(
        s,
        "SELECT count(*) FROM odp_runtime.durable_audit_events WHERE event_type IN ('identity.account.roles_updated','identity.account.disabled')",
    ) == [(0,)]


@pytest.mark.parametrize("mutation", ["self-demotion", "self-disable"])
def test_authorized_healthy_self_change_is_not_misclassified_as_stale_authority(
    stack: Any, mutation: str
) -> None:
    s = stack
    actor = _bootstrap_admin(s)
    _rotate_password(s, actor)
    _invited_account(s, "remaining-admin", "platform_admin")
    headers = _sign_in(s, actor)
    if mutation == "self-demotion":
        response = s.client.post(
            "/api/v1/operator/users",
            headers=headers,
            json={"subjectId": actor, "roles": ["auditor"]},
        )
    else:
        response = s.client.post(
            f"/api/v1/operator/users/{actor}/status", headers=headers, json={"status": "disabled"}
        )
    assert response.status_code == 200, response.text
