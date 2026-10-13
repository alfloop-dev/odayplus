"""Additional real-PG controls; audit delay is injected, expiry/rollback are real."""

from __future__ import annotations

import time
from typing import Any

import pytest

from shared.infrastructure.persistence.audit_log import DurableAuditLog
from tests.identity.test_identity_user_role_management import (
    _bootstrap_admin,
    _q,
    _rotate_password,
    _sign_in,
)
from tests.identity.test_identity_user_role_management import stack as stack
from tests.integration.test_invitation_authority_wait import PATH, client, pending


@pytest.mark.parametrize("invalid", ["null", "object", "clearance"])
def test_malformed_persisted_scope_after_dependency_refuses_invitation(
    stack: Any,
    monkeypatch: Any,
    invalid: str,
) -> None:
    s = stack
    actor = _bootstrap_admin(s)
    _rotate_password(s, actor)
    _q(
        s,
        "INSERT INTO identity.account_scopes(account_id) VALUES(%s) RETURNING account_id",
        (actor,),
    )
    headers = _sign_in(s, actor)
    c, engine = client(s, s.boundary)
    try:

        def corrupt(conn: Any) -> None:
            field = (
                "clearance='INVALID'"
                if invalid == "clearance"
                else "modules='" + ("null" if invalid == "null" else "{}") + "'::jsonb"
            )
            assert (
                conn.execute(
                    "UPDATE identity.account_scopes SET " + field + " WHERE account_id=%s", (actor,)
                ).rowcount
                == 1
            )

        response = pending(
            s,
            engine,
            c,
            headers,
            PATH,
            {"email": "malformed-offline@example.invalid"},
            corrupt,
            monkeypatch,
        )
        assert response.status_code == 403
        assert _q(s, "SELECT count(*) FROM identity.invitations") == [(0,)]
    finally:
        c.close()
        engine.close()


@pytest.mark.parametrize("operation", ["issue", "revoke"])
def test_natural_expiry_after_provisional_invitation_audit_rolls_back(
    stack: Any,
    monkeypatch: Any,
    operation: str,
) -> None:
    s = stack
    actor = _bootstrap_admin(s)
    _rotate_password(s, actor)
    headers = _sign_in(s, actor)
    c, engine = client(s, s.boundary)
    try:
        path = PATH
        body = {"email": "expiry-offline@example.invalid"}
        if operation == "revoke":
            issued = c.post(path, headers=headers, json=body)
            assert issued.status_code == 201
            path = PATH + "/" + issued.json()["invitation_id"] + "/revoke"
            body = {}
        before = _q(
            s, "SELECT invitation_id,revoked_at FROM identity.invitations ORDER BY invitation_id"
        )
        audit_before = _q(
            s,
            "SELECT count(*) FROM odp_runtime.durable_audit_events WHERE event_type LIKE %s",
            ("identity.account.%",),
        )
        _q(
            s,
            "UPDATE identity.sessions SET idle_expires_at=clock_timestamp()+interval '3 seconds' WHERE account_id=%s RETURNING session_id",
            (actor,),
        )
        recorded = []
        original = DurableAuditLog.record

        def delay(self: Any, event: Any) -> Any:
            result = original(self, event)
            if self._engine is engine and event.event_type in {
                "identity.account.invite",
                "identity.account.invitation_revoked",
            }:
                recorded.append(result.event_id)
                time.sleep(3.1)  # Natural DB-clock expiry, NOT a mocked clock/session refusal.
            return result

        monkeypatch.setattr(DurableAuditLog, "record", delay)
        response = c.post(path, headers=headers, json=body)
        assert len(recorded) == 1 and response.status_code == 403
        assert (
            _q(
                s,
                "SELECT invitation_id,revoked_at FROM identity.invitations ORDER BY invitation_id",
            )
            == before
        )
        assert (
            _q(
                s,
                "SELECT count(*) FROM odp_runtime.durable_audit_events WHERE event_type LIKE %s",
                ("identity.account.%",),
            )
            == audit_before
        )
        assert _q(
            s,
            "SELECT count(*) FROM identity.sessions WHERE account_id=%s AND revoked_at IS NOT NULL",
            (actor,),
        ) == [(0,)]
    finally:
        c.close()
        engine.close()
