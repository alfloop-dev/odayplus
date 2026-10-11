"""Real PostgreSQL root concurrency/rollback/preservation; NOT live evidence.

Fixed incident IDs below are isolated test inputs, not repaired live history.
No mounted recovery endpoint/capability/rotation/consumer proof is claimed yet.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from modules.opsboard.auth import Credentials
from shared.audit import AuditEvent
from shared.identity.dev_smoke_journal import (
    AUTHORIZATION_ID as CREATION_AUTHORIZATION_ID,
    PRESERVED_ACCOUNT_ID,
    TENANT_ID,
    DevSmokeBindingJournal,
    ProvisioningJournal,
)
from shared.identity.dev_smoke_recovery import (
    ACCEPT_EVENT_ID,
    INVITATION_ID,
    ISSUE_EVENT_ID,
    OLD_EXECUTION_ID,
    OLD_PLAN_DIGEST,
    OLD_QUARANTINE_EVENT_ID,
    TARGET_ACCOUNT_ID,
    RecoveryJournal,
    RecoveryRefused,
)
from shared.identity.invitation_service import _scope
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.postgresql import PostgresEngine
from tests.identity.test_identity_user_role_management import _q, _sign_in
from tests.identity.test_identity_user_role_management import stack as identity_stack  # noqa: F401
from tests.security.test_dev_smoke_recovery import MANIFEST, SHA, plan


@pytest.fixture
def recovery(identity_stack: Any) -> Any:
    s = identity_stack
    # Isolated persisted identity fixture; no external DB or credentials.
    for account, username, roles in (
        (PRESERVED_ACCOUNT_ID, "ajoe734", ["auditor", "platform_admin"]),
        (TARGET_ACCOUNT_ID, "odp-dev-smoke", ["platform_admin"]),
    ):
        s.engine.execute(
            "INSERT INTO identity.accounts (account_id, tenant_id, username, email, status, created_by) "
            "VALUES (?, ?, ?, ?, 'active', ?)",
            (account, TENANT_ID, username, username + "@example.invalid", PRESERVED_ACCOUNT_ID),
        )
        s.engine.execute("INSERT INTO identity.account_scopes (account_id) VALUES (?)", (account,))
        s.engine.execute(
            "INSERT INTO identity.password_credentials (account_id, phc_hash, must_change) "
            "VALUES (?, ?, false)", (account, "$argon2id$v=19$m=65536,t=3,p=1$offline$fixture"),
        )
        for role in roles:
            s.engine.execute("INSERT INTO identity.account_roles (account_id, role, granted_by) VALUES (?, ?, ?)",
                             (account, role, PRESERVED_ACCOUNT_ID))
    now = datetime.now(UTC) - timedelta(minutes=5)
    s.engine.execute(
        "INSERT INTO identity.invitations (invitation_id, tenant_id, email, token_hash, preset_roles, "
        "preset_scope, created_by, expires_at, accepted_at) VALUES (?, ?, ?, ?, CAST(? AS jsonb), "
        "CAST(? AS jsonb), ?, ?, ?)",
        (INVITATION_ID, TENANT_ID, "odp-dev-smoke@example.invalid", "c" * 64,
         json.dumps(["platform_admin"]), json.dumps(_scope(TENANT_ID)), PRESERVED_ACCOUNT_ID,
         now + timedelta(hours=1), now + timedelta(seconds=1)),
    )
    for event_type, event_id, actor, timestamp, metadata in (
        ("identity.account.invite", ISSUE_EVENT_ID, PRESERVED_ACCOUNT_ID, now, {
            "tenant_id": TENANT_ID, "invitation_id": INVITATION_ID, "preset_roles": ["platform_admin"],
            "preset_scope": _scope(TENANT_ID), "expires_at": (now + timedelta(hours=1)).isoformat(),
        }),
        ("identity.account.accept", ACCEPT_EVENT_ID, TARGET_ACCOUNT_ID, now + timedelta(seconds=1), {
            "tenant_id": TENANT_ID, "invitation_id": INVITATION_ID, "account_id": TARGET_ACCOUNT_ID,
            "subject_id": TARGET_ACCOUNT_ID, "roles": ["platform_admin"], "scope": _scope(TENANT_ID),
            "status": "active", "must_change": False,
        }),
    ):
        s.audit.record(AuditEvent(event_id=event_id, event_type=event_type, actor=actor,
            action=event_type.upper().replace(".", "_"), resource=f"identity.invitation:{INVITATION_ID}",
            outcome="success", correlation_id=f"identity-invitation-{INVITATION_ID}",
            occurred_at=timestamp, metadata=metadata))
    for stage, event_id in (("reserved", str(uuid4())), ("recovery-required", OLD_QUARANTINE_EVENT_ID)):
        s.audit.record(AuditEvent(event_id=event_id, event_type=ProvisioningJournal._TYPE,
            actor=ProvisioningJournal._ACTOR, action="DEV_SMOKE_RESERVATION",
            resource=ProvisioningJournal._CORRELATION, correlation_id=ProvisioningJournal._CORRELATION,
            outcome="success", occurred_at=now + timedelta(seconds=2), metadata={
                "authorization_id": CREATION_AUTHORIZATION_ID, "execution_id": OLD_EXECUTION_ID,
                "plan_digest": OLD_PLAN_DIGEST, "release_sha": SHA, "manifest_digest": MANIFEST,
                "tenant_id": TENANT_ID, "stage": stage, "execution_authorized": False,
                "secret_values_redacted": True,
            }))
    s.principal = s.boundary.authenticate(Credentials.from_headers(
        _sign_in(s, PRESERVED_ACCOUNT_ID, TENANT_ID))).principal
    _sign_in(s, TARGET_ACCOUNT_ID, TENANT_ID)
    s.recovery = RecoveryJournal(engine=s.engine, audit_log=s.audit, environment="dev",
        release_profile="dev-admin", release_sha=SHA, manifest_digest=MANIFEST)
    s.plan = plan()
    return s


def snapshot(s: Any) -> dict[str, Any]:
    return {table: _q(s, f"SELECT row_to_json(t)::text FROM identity.{table} t ORDER BY row_to_json(t)::text")
            for table in ("accounts", "account_roles", "account_scopes", "password_credentials", "sessions", "invitations")}


def old_events(s: Any) -> list[Any]:
    return [e.to_dict() for e in s.audit.list_events(correlation_id=ProvisioningJournal._CORRELATION)]


def test_root_preserves_entire_identity_old_quarantine_and_null_binding(recovery: Any) -> None:
    s = recovery
    before, old = snapshot(s), old_events(s)
    assert s.recovery.inspect(s.principal) is None
    reserved = s.recovery.reserve(s.principal, s.plan)
    assert reserved.stage == "reserved"
    assert s.recovery.inspect(s.principal) == reserved
    assert reserved.to_receipt()["execution_authorized"] is False
    assert snapshot(s) == before and old_events(s) == old
    legacy = ProvisioningJournal(engine=s.engine, audit_log=s.audit)
    assert legacy.inspect().stage == "recovery-required"
    assert DevSmokeBindingJournal(journal=legacy).inspect() is None
    with pytest.raises(RecoveryRefused, match="RECOVERY_ALREADY_RESERVED"):
        s.recovery.reserve(s.principal, {**s.plan, "execution_id": str(uuid4())})
    quarantined = s.recovery.quarantine(s.principal, reserved)
    assert quarantined.stage == "recovery-required"
    assert s.recovery.inspect(s.principal) == quarantined
    with pytest.raises(RecoveryRefused, match="RECOVERY_QUARANTINE_REFUSED"):
        s.recovery.quarantine(s.principal, reserved)
    assert snapshot(s) == before and old_events(s) == old
    event = s.audit.list_events(correlation_id=RecoveryJournal._CORRELATION)[0]
    assert set(event.metadata) == RecoveryJournal._KEYS
    assert event.metadata["issuer_session_id"] == s.principal.attributes["sid"]
    assert "phc" not in json.dumps(event.to_dict())
    assert s.audit.verify_chain().ok


@pytest.mark.parametrize("operation", ["reserve", "inspect", "quarantine"])
@pytest.mark.parametrize("drift", ["revoked_session", "must_change", "lost_admin", "extra_original_role", "wrong_actor", "wrong_tenant"])
def test_every_call_revalidates_persisted_original_session(recovery: Any, operation: str, drift: str) -> None:
    s = recovery
    root = s.recovery.reserve(s.principal, s.plan) if operation == "quarantine" else None
    principal = s.principal
    if drift == "revoked_session":
        s.engine.execute("UPDATE identity.sessions SET revoked_at = now() WHERE account_id = ?", (PRESERVED_ACCOUNT_ID,))
    elif drift == "must_change":
        s.engine.execute("UPDATE identity.password_credentials SET must_change = true WHERE account_id = ?", (PRESERVED_ACCOUNT_ID,))
    elif drift == "lost_admin":
        s.engine.execute("DELETE FROM identity.account_roles WHERE account_id = ? AND role = 'platform_admin'", (PRESERVED_ACCOUNT_ID,))
    elif drift == "extra_original_role":
        s.engine.execute("INSERT INTO identity.account_roles (account_id, role, granted_by) VALUES (?, 'operations_manager', 'test')", (PRESERVED_ACCOUNT_ID,))
    elif drift == "wrong_actor":
        principal = replace(principal, subject_id=TARGET_ACCOUNT_ID)
    else:
        principal = replace(principal, scope=replace(principal.scope, tenant_id=str(uuid4())))
    before = snapshot(s)
    with pytest.raises(RecoveryRefused):
        if operation == "reserve":
            s.recovery.reserve(principal, s.plan)
        elif operation == "inspect":
            s.recovery.inspect(principal)
        else:
            s.recovery.quarantine(principal, root)
    assert snapshot(s) == before
    assert len(s.audit.list_events(correlation_id=RecoveryJournal._CORRELATION)) == (1 if root else 0)


@pytest.mark.parametrize("drift", ["target_disabled", "target_role", "target_scope", "missing_credential", "invitation_revoked", "old_binding"])
def test_target_and_exact_old_lineage_drift_refuse_before_reservation(recovery: Any, drift: str) -> None:
    s = recovery
    if drift == "target_disabled":
        s.engine.execute("UPDATE identity.accounts SET status = 'disabled' WHERE account_id = ?", (TARGET_ACCOUNT_ID,))
    elif drift == "target_role":
        s.engine.execute("INSERT INTO identity.account_roles (account_id, role, granted_by) VALUES (?, 'auditor', 'test')", (TARGET_ACCOUNT_ID,))
    elif drift == "target_scope":
        s.engine.execute("UPDATE identity.account_scopes SET modules = '[\"business\"]' WHERE account_id = ?", (TARGET_ACCOUNT_ID,))
    elif drift == "missing_credential":
        s.engine.execute("DELETE FROM identity.password_credentials WHERE account_id = ?", (TARGET_ACCOUNT_ID,))
    elif drift == "invitation_revoked":
        s.engine.execute("UPDATE identity.invitations SET revoked_at = now() WHERE invitation_id = ?", (INVITATION_ID,))
    else:
        # Signed, structurally genuine event; the old NULL binding must still reject it.
        old = s.audit.list_events(correlation_id=ProvisioningJournal._CORRELATION)[0]
        s.audit.record(AuditEvent(event_type=DevSmokeBindingJournal._TYPE, actor=DevSmokeBindingJournal._ACTOR,
            action="DEV_SMOKE_BINDING", resource=DevSmokeBindingJournal._CORRELATION,
            correlation_id=DevSmokeBindingJournal._CORRELATION, outcome="success", metadata={
                **old.metadata, "account_id": TARGET_ACCOUNT_ID, "stage": "binding-intent",
                "secret_name": "ODP_DEV_ADMIN_CREDENTIAL_BUNDLE", "credential_binding_verified": False,
            }))
    before = snapshot(s)
    with pytest.raises(RecoveryRefused):
        s.recovery.reserve(s.principal, s.plan)
    assert s.audit.list_events(correlation_id=RecoveryJournal._CORRELATION) == []
    assert snapshot(s) == before


@pytest.mark.parametrize("operation", ["reserve", "quarantine"])
def test_audit_failure_rolls_back_root_without_leaking_database_detail(recovery: Any, monkeypatch: Any, operation: str) -> None:
    s = recovery
    root = s.recovery.reserve(s.principal, s.plan) if operation == "quarantine" else None
    before, old = snapshot(s), old_events(s)
    monkeypatch.setattr(s.audit, "record", lambda _e: (_ for _ in ()).throw(RuntimeError("private-database-detail")))
    with pytest.raises(RecoveryRefused, match="^RECOVERY_JOURNAL_UNAVAILABLE$"):
        if operation == "reserve":
            s.recovery.reserve(s.principal, s.plan)
        else:
            s.recovery.quarantine(s.principal, root)
    assert snapshot(s) == before and old_events(s) == old
    assert len(s.audit.list_events(correlation_id=RecoveryJournal._CORRELATION)) == (1 if root else 0)


def test_two_pg_pools_cannot_reserve_same_new_authorization_twice(recovery: Any) -> None:
    s = recovery
    other_engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    try:
        other = RecoveryJournal(engine=other_engine, audit_log=DurableAuditLog(other_engine),
            environment="dev", release_profile="dev-admin", release_sha=SHA, manifest_digest=MANIFEST)
        before, old = snapshot(s), old_events(s)

        def reserve(service: RecoveryJournal) -> str:
            try:
                service.reserve(s.principal, plan())
                return "reserved"
            except RecoveryRefused as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(reserve, (s.recovery, other))) == ["RECOVERY_ALREADY_RESERVED", "reserved"]
        assert snapshot(s) == before and old_events(s) == old
        assert len(s.audit.list_events(correlation_id=RecoveryJournal._CORRELATION)) == 1
        assert s.audit.verify_chain().ok
    finally:
        other_engine.close()


def test_target_drift_does_not_block_one_way_quarantine(recovery: Any) -> None:
    s = recovery
    reserved = s.recovery.reserve(s.principal, s.plan)
    s.engine.execute("UPDATE identity.accounts SET status = 'disabled' WHERE account_id = ?", (TARGET_ACCOUNT_ID,))
    assert s.recovery.quarantine(s.principal, reserved).stage == "recovery-required"


@pytest.mark.parametrize("environment,profile", [("prod", "dev-admin"), ("dev", "full"), ("", "dev-admin")])
def test_runtime_guard_is_not_a_plan_override(recovery: Any, environment: str, profile: str) -> None:
    s = recovery
    with pytest.raises(RecoveryRefused, match="RECOVERY_RUNTIME_REFUSED"):
        RecoveryJournal(engine=s.engine, audit_log=s.audit, environment=environment,
                        release_profile=profile, release_sha=SHA, manifest_digest=MANIFEST)
