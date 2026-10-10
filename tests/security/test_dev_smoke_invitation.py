"""Invitation application-layer PostgreSQL regressions; not live evidence.

Reuse the existing real identity/auth/session stack rather than introduce a
parallel auth harness. This file tests the internal transaction service; actual
issuer/revoker router coverage lives in test_dev_smoke_provisioning.py. The
acceptance factory/Web adapter are not yet activated; foreground binding is pending.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from modules.opsboard.auth import Credentials
from shared.auth import Role
from shared.identity.credential_service import CredentialService
from shared.identity.invitation_service import InvitationRefused, InvitationService
from tests.identity.test_identity_user_role_management import (
    OTHER_TENANT,
    SECRET,
    TENANT,
    _bootstrap_admin,
    _q,
    _rotate_password,
    _sign_in,
)
from tests.identity.test_identity_user_role_management import (
    stack as identity_stack,  # noqa: F401 — pytest fixture registration
)

PASSWORD = "Independent-Smoke-Credential-7319"
EMAIL = "dedicated.smoke@example.invalid"


BUDGET_MIGRATION = Path("infra/db/migrations/000028_identity_invitation_acceptance_budget.sql")


@pytest.fixture
def stack(request: pytest.FixtureRequest) -> Any:
    base = request.getfixturevalue("identity_stack")
    with base.db.connect(autocommit=True) as conn:
        conn.execute(BUDGET_MIGRATION.read_text(encoding="utf-8"))
    return base


@pytest.fixture
def invitations(stack: Any) -> Any:
    admin = _bootstrap_admin(stack)
    _rotate_password(stack, admin)
    # Preserve the historical business-enabled account case as an offline input.
    for role in (Role.AUDITOR, Role.OPERATIONS_MANAGER):
        stack.engine.execute(
            "INSERT INTO identity.account_roles (account_id, role, granted_by) VALUES (?, ?, 'test')",
            (admin, role.value),
        )
    headers = _sign_in(stack, admin)
    outcome = stack.boundary.authenticate(Credentials.from_headers(headers))
    assert outcome.authenticated
    stack.admin, stack.principal = admin, outcome.principal
    stack.invites = InvitationService(engine=stack.engine, audit_log=stack.audit)
    return stack


def _snapshot(s: Any) -> dict[str, Any]:
    # Exact original inventory including hashes, scopes and all sessions, not
    # just roles/status. Readback is from an independent database connection.
    return {
        table: _q(s, f"SELECT row_to_json(t)::text FROM identity.{table} t "
                     "WHERE account_id = %s ORDER BY row_to_json(t)::text", (s.admin,))
        for table in ("accounts", "password_credentials", "account_roles", "account_scopes", "sessions")
    }


def _issue(s: Any) -> Any:
    return s.invites.issue(s.principal, email=EMAIL)


def _accept(s: Any, issued: Any, **overrides: Any) -> Any:
    return s.invites.accept(**{
        "invitation_id": issued.invitation_id, "token": issued.token,
        "username": "release.smoke", "password": PASSWORD, **overrides,
    })


def test_issue_then_accept_preserves_original_and_audits_new_pure_admin(invitations: Any) -> None:
    s = invitations
    before = _snapshot(s)
    issued = _issue(s)
    assert _snapshot(s) == before
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]
    (stored_hash,), = _q(s, "SELECT token_hash FROM identity.invitations")
    assert issued.token != stored_hash and len(stored_hash) == 64
    assert issued.token not in repr(issued)
    assert issued.token not in json.dumps(issued.to_receipt())

    accepted = _accept(s, issued)
    assert _snapshot(s) == before
    assert accepted.account_id != s.admin
    assert accepted.tenant_id == TENANT
    assert _q(s, "SELECT role FROM identity.account_roles WHERE account_id = %s", (accepted.account_id,)) == [
        ("platform_admin",)
    ]
    (phc, must_change), = _q(s, "SELECT phc_hash, must_change FROM identity.password_credentials "
                                "WHERE account_id = %s", (accepted.account_id,))
    assert CredentialService().verify_password(phc, PASSWORD)
    assert must_change is False
    assert _q(s, "SELECT count(*) FROM identity.sessions WHERE account_id = %s", (accepted.account_id,)) == [(0,)]
    principal = s.boundary.authenticate(Credentials.from_headers(_sign_in(s, accepted.account_id))).principal
    assert principal.roles == frozenset({Role.PLATFORM_ADMIN})
    assert principal.tenant_id == TENANT
    assert not principal.scope.brand_ids
    events = [e for e in s.audit.list_events(tenant_id=TENANT) if e.event_type in {
        "identity.account.invite", "identity.account.accept"
    }]
    assert [e.actor for e in events] == [s.admin, accepted.account_id]
    assert [e.event_id for e in events] == [issued.audit_event_id, accepted.audit_event_id]
    text = json.dumps([e.metadata for e in events]) + json.dumps(accepted.to_receipt())
    for secret in (issued.token, stored_hash, PASSWORD, phc, SECRET):
        assert secret not in text
    assert s.audit.verify_chain().ok


@pytest.mark.parametrize("change", ["anonymous", "wrong_role", "wrong_tenant", "no_sid", "foreign_sid"])
def test_no_caller_claim_can_replace_authoritative_admin_session(invitations: Any, change: str) -> None:
    s = invitations
    p = s.principal
    if change == "anonymous":
        p = replace(p, authenticated=False)
    elif change == "wrong_role":
        p = replace(p, roles=frozenset({Role.OPERATIONS_MANAGER}))
    elif change == "wrong_tenant":
        p = replace(p, scope=replace(p.scope, tenant_id=OTHER_TENANT))
    elif change == "no_sid":
        p = replace(p, attributes={})
    else:
        p = replace(p, attributes={"sid": str(uuid4())})
    with pytest.raises(InvitationRefused):
        s.invites.issue(p, email=EMAIL)
    assert _q(s, "SELECT count(*) FROM identity.invitations") == [(0,)]


@pytest.mark.parametrize("drift", ["disabled", "lost_admin", "must_change", "revoked", "expired"])
def test_stale_validated_principal_cannot_issue_after_db_drift(invitations: Any, drift: str) -> None:
    s = invitations
    if drift == "disabled":
        s.engine.execute("UPDATE identity.accounts SET status = 'disabled' WHERE account_id = ?", (s.admin,))
    elif drift == "lost_admin":
        s.engine.execute("DELETE FROM identity.account_roles WHERE account_id = ? AND role = 'platform_admin'", (s.admin,))
    elif drift == "must_change":
        s.engine.execute("UPDATE identity.password_credentials SET must_change = true WHERE account_id = ?", (s.admin,))
    elif drift == "revoked":
        s.engine.execute("UPDATE identity.sessions SET revoked_at = now() WHERE account_id = ?", (s.admin,))
    else:
        s.engine.execute("UPDATE identity.sessions SET idle_expires_at = now() WHERE account_id = ?", (s.admin,))
    with pytest.raises(InvitationRefused, match="INVITATION_ADMIN_SESSION_INVALID"):
        _issue(s)


@pytest.mark.parametrize("lifetime", [0, -1, 259201, True, 1.5])
def test_lifetime_is_positive_integer_and_at_most_72_hours(invitations: Any, lifetime: Any) -> None:
    with pytest.raises(InvitationRefused, match="INVITATION_LIFETIME_INVALID"):
        invitations.invites.issue(invitations.principal, email=EMAIL, lifetime_seconds=lifetime)


def test_email_and_casefolded_username_collisions_never_overwrite(invitations: Any) -> None:
    s = invitations
    before = _snapshot(s)
    with pytest.raises(InvitationRefused, match="INVITATION_ACCOUNT_EXISTS"):
        s.invites.issue(s.principal, email="ROOT.ADMIN@example.invalid")
    issued = _issue(s)
    with pytest.raises(InvitationRefused, match="INVITATION_PENDING_EXISTS"):
        _issue(s)
    with pytest.raises(InvitationRefused, match="INVITATION_ACCOUNT_EXISTS"):
        _accept(s, issued, username="ROOT.ADMIN")
    assert _snapshot(s) == before
    # A collision neither consumes the token nor changes the colliding account.
    assert _q(s, "SELECT accepted_at FROM identity.invitations") == [(None,)]
    assert _accept(s, issued).account_id != s.admin


@pytest.mark.parametrize("state", ["wrong_token", "expired", "revoked", "accepted"])
def test_invalid_or_consumed_capability_cannot_create_or_reset(invitations: Any, monkeypatch: Any, state: str) -> None:
    s = invitations
    issued = _issue(s)
    token = issued.token
    if state == "wrong_token":
        token = "a" * 43 if token != "a" * 43 else "b" * 43
    elif state == "expired":
        s.engine.execute("UPDATE identity.invitations SET expires_at = now() WHERE invitation_id = ?", (issued.invitation_id,))
    elif state == "revoked":
        s.invites.revoke(s.principal, invitation_id=issued.invitation_id)
    else:
        _accept(s, issued)
    before = _q(s, "SELECT row_to_json(t)::text FROM identity.password_credentials t ORDER BY account_id")

    def forbidden_hasher() -> Any:
        pytest.fail("invalid capability reached expensive credential construction")

    monkeypatch.setattr("shared.identity.invitation_service.CredentialService", forbidden_hasher)
    with pytest.raises(InvitationRefused, match="INVITATION_UNAVAILABLE"):
        _accept(s, issued, token=token)
    assert _q(s, "SELECT row_to_json(t)::text FROM identity.password_credentials t ORDER BY account_id") == before
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(2 if state == "accepted" else 1,)]


def test_tampered_business_role_preset_is_not_silently_downgraded(invitations: Any) -> None:
    s = invitations
    issued = _issue(s)
    s.engine.execute("UPDATE identity.invitations SET preset_roles = CAST(? AS jsonb) WHERE invitation_id = ?",
                     ('["platform_admin", "operations_manager"]', issued.invitation_id))
    with pytest.raises(InvitationRefused, match="INVITATION_PRESET_INVALID"):
        _accept(s, issued)
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]


@pytest.mark.parametrize("operation", ["issue", "accept", "revoke"])
def test_audit_failure_rolls_back_every_mutation_and_can_recover(invitations: Any, monkeypatch: Any, operation: str) -> None:
    s = invitations
    issued = None if operation == "issue" else _issue(s)
    before = _snapshot(s)
    original = s.audit.record

    def fail(_event: Any) -> Any:
        raise RuntimeError("audit unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(s.audit, "record", fail)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            if operation == "issue":
                _issue(s)
            elif operation == "accept":
                _accept(s, issued)
            else:
                s.invites.revoke(s.principal, invitation_id=issued.invitation_id)
    assert s.audit.record == original
    assert _snapshot(s) == before
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]
    assert _q(s, "SELECT accepted_at, revoked_at FROM identity.invitations") == ([] if issued is None else [(None, None)])
    if issued is None:
        assert _issue(s).audit_event_id
    elif operation == "accept":
        assert _accept(s, issued).audit_event_id
    else:
        assert s.invites.revoke(s.principal, invitation_id=issued.invitation_id)["audit_event_id"]


def test_foreign_tenant_revoke_is_indistinguishable_from_missing(invitations: Any) -> None:
    s = invitations
    issued = _issue(s)
    # Move only the offline invitation row to a foreign tenant. No foreign
    # account enumeration or credential issuance is used by the application.
    s.engine.execute("UPDATE identity.invitations SET tenant_id = ? WHERE invitation_id = ?",
                     (OTHER_TENANT, issued.invitation_id))
    with pytest.raises(InvitationRefused, match="INVITATION_NOT_FOUND"):
        s.invites.revoke(s.principal, invitation_id=issued.invitation_id)
    assert _q(s, "SELECT revoked_at FROM identity.invitations") == [(None,)]


def test_acceptance_budget_survives_refusal_resets_by_db_time_and_preserves_login_counters(invitations: Any) -> None:
    s = invitations
    issued = _issue(s)
    s.engine.execute("INSERT INTO identity.login_attempts (attempt_key, failure_count) VALUES (?, 3)",
                     ("account:" + s.admin,))
    before = _snapshot(s)
    for _ in range(5):
        with pytest.raises(InvitationRefused, match="INVITATION_UNAVAILABLE"):
            _accept(s, issued, token="!")
    with pytest.raises(InvitationRefused, match="INVITATION_RATE_LIMITED"):
        _accept(s, issued)
    assert _snapshot(s) == before
    assert _q(s, "SELECT failure_count FROM identity.login_attempts WHERE attempt_key = %s",
              ("account:" + s.admin,)) == [(3,)]
    assert _q(s, "SELECT failure_count FROM identity.invitation_acceptance_budget "
                 "WHERE attempt_key = 'invitation-accept:global'") == [(6,)]
    # Reapplying the expand migration never clears the durable budget.
    with s.db.connect(autocommit=True) as conn:
        conn.execute(BUDGET_MIGRATION.read_text(encoding="utf-8"))
    with pytest.raises(InvitationRefused, match="INVITATION_RATE_LIMITED"):
        _accept(s, issued)
    assert _q(s, "SELECT attempt_key, failure_count FROM identity.login_attempts") == [
        ("account:" + s.admin, 3)
    ]
    s.engine.execute("UPDATE identity.invitation_acceptance_budget "
                     "SET window_started_at = now() - interval '16 minutes' "
                     "WHERE attempt_key LIKE ?", ("invitation-accept:%",))
    assert _accept(s, issued).account_id != s.admin


def test_random_invitation_ids_cannot_grow_budget_rows_or_construct_hasher(invitations: Any, monkeypatch: Any) -> None:
    s = invitations
    monkeypatch.setattr("shared.identity.invitation_service.CredentialService",
                        lambda: pytest.fail("random ids reached Argon2"))
    for _ in range(50):
        with pytest.raises(InvitationRefused, match="INVITATION_UNAVAILABLE"):
            s.invites.accept(invitation_id=str(uuid4()), token="a" * 43,
                             username="release.smoke", password=PASSWORD)
    with pytest.raises(InvitationRefused, match="INVITATION_RATE_LIMITED"):
        s.invites.accept(invitation_id="invalid-private-input", token="a" * 43,
                         username="release.smoke", password=PASSWORD)
    assert _q(s, "SELECT attempt_key, failure_count FROM identity.invitation_acceptance_budget") == [
        ("invitation-accept:global", 50)
    ]
    assert _q(s, "SELECT count(*) FROM identity.login_attempts") == [(0,)]
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]
