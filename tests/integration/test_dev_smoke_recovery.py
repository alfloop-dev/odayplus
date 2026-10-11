"""Real PostgreSQL root concurrency/rollback/preservation; NOT live evidence.

Fixed incident IDs below are isolated test inputs, not repaired live history.
Internal capability/rotation tests are isolated source proof, not mounted API,
consumer, deployed implementation or live recovery proof.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
    RecoveryCapabilityService,
    RecoveryBindingJournal,
    RECOVERY_BINDING_CORRELATION,
    RECOVERY_BINDING_KEYS,
    recovery_bundle_lineage,
    recovery_bundle_acknowledged,
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
    with s.db.connect(autocommit=True) as conn:
        # Identity administration fixture loads only 000011; add the actual
        # production Web bearer/session expansion before insertion regressions.
        conn.execute(Path("infra/db/migrations/000012_identity_session_server_secrets.sql").read_text())
        conn.execute(Path("infra/db/migrations/000029_dev_smoke_recovery_capability.sql").read_text())
        conn.execute(Path("infra/db/migrations/000030_dev_smoke_session_fence.sql").read_text())
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
            outcome="success", occurred_at=now + timedelta(seconds=-1 if stage == "reserved" else 2), metadata={
                "authorization_id": CREATION_AUTHORIZATION_ID, "execution_id": OLD_EXECUTION_ID,
                "plan_digest": OLD_PLAN_DIGEST, "release_sha": SHA, "manifest_digest": MANIFEST,
                "tenant_id": TENANT_ID, "stage": stage, "execution_authorized": False,
                "secret_values_redacted": True,
            }))
    s.headers = _sign_in(s, PRESERVED_ACCOUNT_ID, TENANT_ID)
    s.principal = s.boundary.authenticate(Credentials.from_headers(s.headers)).principal
    _sign_in(s, TARGET_ACCOUNT_ID, TENANT_ID)
    s.recovery = RecoveryJournal(engine=s.engine, audit_log=s.audit, environment="dev",
        release_profile="dev-admin", release_sha=SHA, manifest_digest=MANIFEST)
    s.capabilities = RecoveryCapabilityService(engine=s.engine, audit_log=s.audit, environment="dev",
        release_profile="dev-admin", release_sha=SHA, manifest_digest=MANIFEST)
    s.binding = RecoveryBindingJournal(engine=s.engine, audit_log=s.audit, environment="dev",
        release_profile="dev-admin", release_sha=SHA, manifest_digest=MANIFEST)
    s.plan = plan()
    return s


@pytest.fixture
def recovery_runtime(recovery: Any, monkeypatch: Any) -> Any:
    for key, value in {"ODP_DEPLOY_ENV": "dev", "ODP_RELEASE_PROFILE": "dev-admin",
                       "ODAY_RELEASE_SHA": SHA, "ODP_RELEASE_MANIFEST_DIGEST": MANIFEST}.items():
        monkeypatch.setenv(key, value)
    return recovery


RECOVERY_PATH = "/api/v1/operator/users/dev-smoke-recovery"


def test_mounted_recovery_rotates_existing_pair_and_projects_safe_lineage(recovery_runtime: Any) -> None:
    s = recovery_runtime
    old = old_events(s)
    before = snapshot(s)
    assert s.client.get(RECOVERY_PATH, headers=s.headers).json()["reservation"] is None
    response = s.client.post(RECOVERY_PATH, headers=s.headers, json={"action": "reserve", "plan": s.plan})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    root = response.json()["reservation"]
    response = s.client.post(RECOVERY_PATH, headers=s.headers, json={"action": "issue", "reservation": root})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    cap = response.json()
    password = "Mounted-Recovery-Password-9821!"
    response = s.client.post(RECOVERY_PATH, headers=s.headers, json={
        "action": "consume", "reservation": root, "capability_id": cap["capability_id"],
        "token": cap["token"], "password": password,
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rotated"
    assert response.headers["cache-control"] == "no-store"
    assert cap["token"] not in response.text and password not in response.text
    assert old_events(s) == old
    after = snapshot(s)
    for table in ("accounts", "account_roles", "account_scopes", "invitations"):
        assert after[table] == before[table]
    projected = s.client.get("/api/v1/operator/users/audit-trail", headers=s.headers,
                             params={"subject_id": TARGET_ACCOUNT_ID})
    assert projected.status_code == 200
    events = [e for e in projected.json()["events"] if e["event_type"].startswith("identity.dev_smoke.")]
    assert len(events) == 3
    assert all(e["metadata"]["target_account_id"] == TARGET_ACCOUNT_ID for e in events)
    assert cap["token"] not in projected.text and password not in projected.text
    assert "token_hash" not in projected.text and "phc_hash" not in projected.text
    assert s.audit.verify_chain().ok


@pytest.mark.parametrize("body", [
    b'{"action":"issue","token":"private-input"}', b'{"action":"reserve","action":"issue"}',
    b'{"action":[]}', b'{"password":"private-input"', b'[]', b'{"action":"consume"}',
])
def test_mounted_recovery_strict_body_does_not_echo_or_mutate(recovery_runtime: Any, body: bytes) -> None:
    s = recovery_runtime
    before = snapshot(s)
    response = s.client.post(RECOVERY_PATH, headers={**s.headers, "content-type": "application/json"}, content=body)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert "private-input" not in response.text
    assert snapshot(s) == before
    assert s.audit.list_events(correlation_id=RecoveryJournal._CORRELATION) == []


@pytest.mark.parametrize("mode", ["auditor", "target", "unauthenticated", "revoked", "must_change"])
def test_mounted_role_session_denial_precedes_malformed_dto_and_domain(recovery_runtime: Any, monkeypatch: Any, mode: str) -> None:
    s = recovery_runtime
    headers = s.headers
    if mode == "auditor":
        s.engine.execute("DELETE FROM identity.account_roles WHERE account_id = ? AND role = 'platform_admin'", (PRESERVED_ACCOUNT_ID,))
    elif mode == "target":
        headers = _sign_in(s, TARGET_ACCOUNT_ID, TENANT_ID)
    elif mode == "unauthenticated":
        headers = {"x-subject-id": PRESERVED_ACCOUNT_ID, "x-tenant-id": TENANT_ID, "x-roles": "platform_admin"}
    elif mode == "revoked":
        s.engine.execute("UPDATE identity.sessions SET revoked_at = now() WHERE account_id = ?", (PRESERVED_ACCOUNT_ID,))
    else:
        s.engine.execute("UPDATE identity.password_credentials SET must_change = true WHERE account_id = ?", (PRESERVED_ACCOUNT_ID,))
    before = snapshot(s)
    calls = []
    for name in ("reserve", "issue", "consume", "quarantine", "inspect"):
        monkeypatch.setattr(RecoveryCapabilityService, name, lambda *a, **kw: calls.append((a, kw)))
    response = s.client.post(RECOVERY_PATH, headers={**headers, "content-type": "application/json"}, content=b'{"password":"private-input"')
    assert response.status_code in {401, 403}
    assert response.headers["cache-control"] == "no-store"
    assert "private-input" not in response.text
    assert calls == [] and snapshot(s) == before
    assert s.audit.list_events(correlation_id=RecoveryJournal._CORRELATION) == []


def test_mounted_recovery_limits_query_body_runtime_and_quarantine(recovery_runtime: Any, monkeypatch: Any) -> None:
    s = recovery_runtime
    for method in ("get", "post"):
        response = getattr(s.client, method)(RECOVERY_PATH + "?token=private-query", headers=s.headers)
        assert response.status_code == 422 and "private-query" not in response.text
    response = s.client.post(RECOVERY_PATH, headers={**s.headers, "content-type": "application/json"}, content=b' ' * 8193)
    assert response.status_code == 413 and response.headers["cache-control"] == "no-store"
    monkeypatch.setenv("ODP_DEPLOY_ENV", "prod")
    assert s.client.post(RECOVERY_PATH, headers=s.headers, json={"action": "reserve", "plan": s.plan}).status_code == 409
    monkeypatch.setenv("ODP_DEPLOY_ENV", "dev")
    root = s.client.post(RECOVERY_PATH, headers=s.headers, json={"action": "reserve", "plan": s.plan}).json()["reservation"]
    response = s.client.post(RECOVERY_PATH, headers=s.headers, json={"action": "quarantine", "reservation": root})
    assert response.status_code == 200
    assert response.json()["reservation"]["stage"] == "recovery-required"
    assert s.client.post(RECOVERY_PATH, headers=s.headers, json={"action": "issue", "reservation": root}).status_code == 409


def test_recovery_audit_projection_omits_extended_credential_metadata(recovery_runtime: Any) -> None:
    s = recovery_runtime
    s.recovery.reserve(s.principal, s.plan)
    root = s.audit.list_events(correlation_id=RecoveryJournal._CORRELATION)[0]
    s.audit.record(AuditEvent(event_type=root.event_type, actor=root.actor, action=root.action,
        resource=root.resource, correlation_id=root.correlation_id, outcome="success",
        metadata={**root.metadata, "token": "private-injected-marker"}))
    response = s.client.get("/api/v1/operator/users/audit-trail", headers=s.headers)
    assert response.status_code == 200 and "private-injected-marker" not in response.text
    assert len([e for e in response.json()["events"] if e["event_type"] == root.event_type]) == 1


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


PASSWORD = "Random-new-offline-pair-739!"


def issue(s: Any) -> tuple[Any, Any]:
    root = s.recovery.reserve(s.principal, s.plan)
    return root, s.capabilities.issue(s.principal, root)


def consume(s: Any, root: Any, cap: Any, **changes: Any) -> dict[str, Any]:
    inputs = {"capability_id": cap.capability_id, "token": cap.token, "password": PASSWORD}
    return s.capabilities.consume(s.principal, root, **{**inputs, **changes})


def test_issue_once_hash_only_short_lived_and_target_only_atomic_rotation(recovery: Any) -> None:
    from shared.identity.credential_service import CredentialService
    s = recovery
    before, old = snapshot(s), old_events(s)
    root, cap = issue(s)
    assert snapshot(s) == before
    assert cap.token not in repr(cap) and cap.token not in json.dumps(cap.to_receipt())
    row = s.engine.query_one("SELECT * FROM identity.dev_smoke_recovery_capabilities")
    import hashlib
    assert row["token_hash"] == hashlib.sha256(cap.token.encode()).hexdigest()
    assert 0 < (datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["issued_at"])).total_seconds() <= 600
    assert cap.token not in json.dumps(row)
    with pytest.raises(RecoveryRefused, match="RECOVERY_CAPABILITY_ALREADY_ISSUED"):
        s.capabilities.issue(s.principal, root)
    # Original and an unrelated account have existing sessions; none may change.
    other = "9c72f0ee-7634-4538-9433-de2f669607c3"
    s.engine.execute("INSERT INTO identity.accounts (account_id, tenant_id, username, email, status, created_by) "
                     "VALUES (?, ?, 'unrelated', 'other@example.invalid', 'active', 'test')", (other, TENANT_ID))
    _sign_in(s, other, TENANT_ID)
    preserved = _q(s, "SELECT row_to_json(t)::text FROM identity.sessions t WHERE account_id <> %s ORDER BY session_id", (TARGET_ACCOUNT_ID,))
    result = consume(s, root, cap, password="Ｒandom-new-offline-pair-739!")
    assert result["status"] == "rotated" and result["revoked_target_sessions"] == 1
    assert result["credential_binding_verified"] is False
    assert s.capabilities._credential_revisions() == ("1", "2")
    phc = s.engine.query_one("SELECT phc_hash FROM identity.password_credentials WHERE account_id = ?", (TARGET_ACCOUNT_ID,))["phc_hash"]
    assert CredentialService().verify_password(phc, PASSWORD)
    assert "$argon2id$v=19$m=65536,t=3,p=1$" in phc
    after = snapshot(s)
    for table in ("account_roles", "account_scopes", "invitations"):
        assert after[table] == before[table]
    assert _q(s, "SELECT row_to_json(t)::text FROM identity.sessions t WHERE account_id <> %s ORDER BY session_id", (TARGET_ACCOUNT_ID,)) == preserved
    assert _q(s, "SELECT revoked_reason FROM identity.sessions WHERE account_id = %s", (TARGET_ACCOUNT_ID,)) == [("dev-smoke-recovery",)]
    assert _q(s, "SELECT row_to_json(t)::text FROM identity.password_credentials t WHERE account_id = %s", (PRESERVED_ACCOUNT_ID,)) == [r for r in before["password_credentials"] if PRESERVED_ACCOUNT_ID in r[0]]
    assert old_events(s) == old and s.recovery.inspect(s.principal) == root
    events = s.capabilities._cap_events()
    assert [e.metadata["stage"] for e in events] == ["issued", "rotated"]
    assert cap.token not in json.dumps([e.to_dict() for e in events])
    assert PASSWORD not in json.dumps(result) and phc not in json.dumps([e.to_dict() for e in events])
    assert s.audit.verify_chain().ok
    with pytest.raises(RecoveryRefused):
        consume(s, root, cap)
    with pytest.raises(RecoveryRefused):
        s.capabilities.issue(s.principal, root)


def test_consume_refusals_commit_budget_before_any_argon_job(recovery: Any, monkeypatch: Any) -> None:
    s = recovery
    root, cap = issue(s)
    before = snapshot(s)
    monkeypatch.setattr("shared.identity.dev_smoke_recovery.CredentialService",
                        lambda: pytest.fail("invalid request reached Argon2 constructor"))
    for changes in ({"token": "x" * 43}, {"capability_id": "invalid-secret-input"},
                    {"password": "weak"}, {"password": "password12345"}, {"token": cap.token + "!"}):
        with pytest.raises(RecoveryRefused):
            consume(s, root, cap, **changes)
    assert _q(s, "SELECT attempt_count FROM identity.dev_smoke_recovery_budget") == [(5,)]
    with pytest.raises(RecoveryRefused, match="RECOVERY_RATE_LIMITED"):
        consume(s, root, cap)
    assert snapshot(s) == before
    assert s.engine.query_one("SELECT consumed_at FROM identity.dev_smoke_recovery_capabilities")["consumed_at"] is None
    assert _q(s, "SELECT * FROM identity.login_attempts") == []


@pytest.mark.parametrize("operation", ["issue", "consume"])
def test_capability_audit_failure_rolls_back_all_effects_but_not_budget(recovery: Any, monkeypatch: Any, operation: str) -> None:
    s = recovery
    root = s.recovery.reserve(s.principal, s.plan)
    cap = s.capabilities.issue(s.principal, root) if operation == "consume" else None
    before, old = snapshot(s), old_events(s)
    monkeypatch.setattr(s.audit, "record", lambda _e: (_ for _ in ()).throw(RuntimeError("private-value")))
    with pytest.raises(RecoveryRefused, match="^RECOVERY_CAPABILITY_UNAVAILABLE$"):
        if operation == "issue":
            s.capabilities.issue(s.principal, root)
        else:
            consume(s, root, cap)
    assert snapshot(s) == before and old_events(s) == old
    assert len(s.capabilities._cap_events()) == (1 if cap else 0)
    if cap:
        row = s.engine.query_one("SELECT consumed_at, rotation_event_id FROM identity.dev_smoke_recovery_capabilities")
        assert row == {"consumed_at": None, "rotation_event_id": None}
        assert _q(s, "SELECT attempt_count FROM identity.dev_smoke_recovery_budget") == [(1,)]
    else:
        assert s.engine.query("SELECT * FROM identity.dev_smoke_recovery_capabilities") == []


@pytest.mark.parametrize("drift", ["expired", "quarantined", "fresh_session", "revoked_session", "target_role", "target_password", "original_password", "source"])
def test_capability_cannot_outlive_session_plan_identity_or_source(recovery: Any, monkeypatch: Any, drift: str) -> None:
    s = recovery
    root, cap = issue(s)
    if drift == "expired":
        s.engine.execute("UPDATE identity.dev_smoke_recovery_capabilities SET issued_at = now() - interval '20 minutes', expires_at = now() - interval '10 minutes'")
    elif drift == "quarantined":
        s.recovery.quarantine(s.principal, root)
    elif drift == "fresh_session":
        s.principal = s.boundary.authenticate(Credentials.from_headers(_sign_in(s, PRESERVED_ACCOUNT_ID, TENANT_ID))).principal
    elif drift == "revoked_session":
        s.engine.execute("UPDATE identity.sessions SET revoked_at = now() WHERE account_id = ?", (PRESERVED_ACCOUNT_ID,))
    elif drift == "target_role":
        s.engine.execute("INSERT INTO identity.account_roles (account_id, role, granted_by) VALUES (?, 'auditor', 'test')", (TARGET_ACCOUNT_ID,))
    elif drift in {"target_password", "original_password"}:
        account = TARGET_ACCOUNT_ID if drift == "target_password" else PRESERVED_ACCOUNT_ID
        s.engine.execute("UPDATE identity.password_credentials SET phc_hash = 'drift' WHERE account_id = ?", (account,))
    else:
        s.capabilities._release_sha = "d" * 40
    before = snapshot(s)
    monkeypatch.setattr("shared.identity.dev_smoke_recovery.CredentialService", lambda: pytest.fail("drift reached Argon2"))
    with pytest.raises(RecoveryRefused):
        consume(s, root, cap)
    assert snapshot(s) == before and len(s.capabilities._cap_events()) == 1


def test_revalidation_after_hashing_refuses_expired_session_without_rotation(recovery: Any, monkeypatch: Any) -> None:
    from shared.identity.credential_service import CredentialService
    s = recovery
    root, cap = issue(s)
    real_hash = CredentialService.hash_password

    def hashing(service: Any, password: str) -> str:
        phc = real_hash(service, password)
        s.engine.execute("UPDATE identity.sessions SET idle_expires_at = now() - interval '1 second' WHERE session_id = ?",
                         (s.principal.attributes["sid"],))
        return phc

    before = _q(s, "SELECT phc_hash FROM identity.password_credentials WHERE account_id = %s", (TARGET_ACCOUNT_ID,))
    monkeypatch.setattr(CredentialService, "hash_password", hashing)
    with pytest.raises(RecoveryRefused):
        consume(s, root, cap)
    assert _q(s, "SELECT phc_hash FROM identity.password_credentials WHERE account_id = %s", (TARGET_ACCOUNT_ID,)) == before
    assert len(s.capabilities._cap_events()) == 1


@pytest.mark.parametrize("operation", ["issue", "consume"])
def test_two_pg_pools_single_capability_and_single_rotation(recovery: Any, operation: str) -> None:
    s = recovery
    root = s.recovery.reserve(s.principal, s.plan)
    cap = s.capabilities.issue(s.principal, root) if operation == "consume" else None
    engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    try:
        other = RecoveryCapabilityService(engine=engine, audit_log=DurableAuditLog(engine), environment="dev",
            release_profile="dev-admin", release_sha=SHA, manifest_digest=MANIFEST)

        def attempt(service: Any) -> str:
            try:
                if operation == "issue":
                    service.issue(s.principal, root)
                else:
                    service.consume(s.principal, root, capability_id=cap.capability_id, token=cap.token, password=PASSWORD)
                return "success"
            except RecoveryRefused:
                return "refused"

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(attempt, (s.capabilities, other))) == ["refused", "success"]
        assert len(s.capabilities._cap_events()) == (2 if cap else 1)
        assert s.audit.verify_chain().ok
    finally:
        engine.close()


def test_nested_transaction_cannot_undo_budget_or_return_uncommitted_capability(recovery: Any) -> None:
    s = recovery
    root, cap = issue(s)
    with s.engine.lock:
        for action in (lambda: consume(s, root, cap), lambda: s.capabilities.issue(s.principal, root)):
            with pytest.raises(RecoveryRefused, match="RECOVERY_OUTERMOST_TRANSACTION_REQUIRED"):
                action()
    assert s.engine.query("SELECT * FROM identity.dev_smoke_recovery_budget") == []


@pytest.mark.parametrize("lifetime", [0, 601, True])
def test_issue_lifetime_cannot_be_extended_or_reinterpreted(recovery: Any, lifetime: Any) -> None:
    s = recovery
    root = s.recovery.reserve(s.principal, s.plan)
    with pytest.raises(RecoveryRefused, match="RECOVERY_CAPABILITY_LIFETIME_INVALID"):
        s.capabilities.issue(s.principal, root, lifetime_seconds=lifetime)
    assert s.engine.query("SELECT * FROM identity.dev_smoke_recovery_capabilities") == []
    assert s.capabilities._cap_events() == []


def test_commit_failure_rolls_back_rotation_consumption_audit_and_session_revocation(recovery: Any) -> None:
    s = recovery
    root, cap = issue(s)
    before = snapshot(s)
    # Real PG deferred failure, not a fake transaction or SQLite approximation.
    with s.db.connect(autocommit=True) as conn:
        conn.execute("""
            CREATE FUNCTION identity.refuse_recovery_commit() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'private-commit-error'; END;
            $$ LANGUAGE plpgsql;
            CREATE CONSTRAINT TRIGGER recovery_commit_failure
            AFTER UPDATE ON identity.dev_smoke_recovery_capabilities
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION identity.refuse_recovery_commit();
        """)
    with pytest.raises(RecoveryRefused, match="^RECOVERY_CAPABILITY_UNAVAILABLE$"):
        consume(s, root, cap)
    assert snapshot(s) == before
    assert s.engine.query_one("SELECT consumed_at FROM identity.dev_smoke_recovery_capabilities")["consumed_at"] is None
    assert [e.metadata["stage"] for e in s.capabilities._cap_events()] == ["issued"]
    assert _q(s, "SELECT attempt_count FROM identity.dev_smoke_recovery_budget") == [(1,)]
    assert s.audit.verify_chain().ok


def insert_fenced_session(engine: PostgresEngine, revision: str | None, *, account: str = TARGET_ACCOUNT_ID) -> str:
    sid = str(uuid4())
    engine.execute(
        "INSERT INTO identity.sessions (session_id, account_id, provider, access_token, "
        "created_at, last_seen_at, idle_expires_at, absolute_expires_at, verified_credential_revision) "
        "VALUES (?, ?, 'local_password', 'offline-token', clock_timestamp(), clock_timestamp(), "
        "clock_timestamp() + interval '30 minutes', clock_timestamp() + interval '1 hour', ?)",
        (sid, account, revision),
    )
    return sid


@pytest.mark.parametrize("revision", [None, "1"])
def test_old_verified_login_cannot_insert_after_rotation_on_separate_pool(recovery: Any, revision: str | None) -> None:
    s = recovery
    root, cap = issue(s)
    engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    try:
        # This is the non-secret revision observed alongside the OLD PHC before
        # Argon verification. Legacy in-flight writers have no revision at all.
        old_revision = engine.query_one("SELECT credential_revision::text AS revision "
            "FROM identity.password_credentials WHERE account_id = ?", (TARGET_ACCOUNT_ID,))["revision"]
        assert old_revision == "1"
        assert consume(s, root, cap)["status"] == "rotated"
        before = snapshot(s)
        with pytest.raises(Exception, match="SESSION_CREDENTIAL_REVISION_REFUSED"):
            insert_fenced_session(engine, revision)
        assert snapshot(s) == before
        sid = insert_fenced_session(engine, "2")
        assert engine.query_one("SELECT revoked_at FROM identity.sessions WHERE session_id = ?", (sid,)) == {"revoked_at": None}
        # No broad denial/revocation of original or unrelated legacy writers.
        insert_fenced_session(engine, None, account=PRESERVED_ACCOUNT_ID)
    finally:
        engine.close()


@pytest.mark.parametrize("revision", [None, "1"])
def test_session_insert_waits_on_uncommitted_rotation_not_stale_snapshot(recovery: Any, monkeypatch: Any, revision: str | None) -> None:
    from threading import Event
    s = recovery
    root, cap = issue(s)
    ready, release = Event(), Event()
    real_record = s.capabilities._record_cap
    engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)

    def record(*args: Any, **kwargs: Any) -> AuditEvent:
        if kwargs["stage"] == "rotated":
            ready.set()  # target credential row UPDATE is uncommitted
            if not release.wait(10):
                raise RuntimeError("offline-test-release-timeout")
        return real_record(*args, **kwargs)

    monkeypatch.setattr(s.capabilities, "_record_cap", record)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(consume, s, root, cap)
            try:
                assert ready.wait(10)
                # Bounded real PG wait demonstrates the INSERT trigger takes
                # the same credential lock, rather than trusting its snapshot.
                with pytest.raises(Exception, match="lock timeout"):
                    with engine.lock:
                        engine.execute("SET LOCAL lock_timeout = '200ms'")
                        insert_fenced_session(engine, revision)
            finally:
                release.set()
            assert future.result(timeout=10)["status"] == "rotated"
        with pytest.raises(Exception, match="SESSION_CREDENTIAL_REVISION_REFUSED"):
            insert_fenced_session(engine, revision)
        assert len(s.capabilities._cap_events()) == 2
        assert s.audit.verify_chain().ok
    finally:
        release.set()
        engine.close()


@pytest.mark.parametrize("commit", [True, False])
@pytest.mark.parametrize("revision", [None, "1"])
def test_inflight_insert_rechecks_committed_revision_or_survives_rotation_rollback(recovery: Any, monkeypatch: Any, commit: bool, revision: str | None) -> None:
    from threading import Event
    from time import monotonic, sleep
    s = recovery
    root, cap = issue(s)
    ready, release, insert_started = Event(), Event(), Event()
    real_record = s.capabilities._record_cap
    engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    backend: list[int] = []

    def record(*args: Any, **kwargs: Any) -> AuditEvent:
        if kwargs["stage"] == "rotated":
            ready.set()
            if not release.wait(10):
                raise RuntimeError("offline-test-release-timeout")
            if not commit:
                raise RuntimeError("offline-audit-rollback")
        return real_record(*args, **kwargs)

    def delayed_insert() -> str:
        with engine.lock:
            backend.append(engine.query_one("SELECT pg_backend_pid() AS pid")["pid"])
            engine.execute("SET LOCAL lock_timeout = '8s'")
            insert_started.set()
            return insert_fenced_session(engine, revision)

    monkeypatch.setattr(s.capabilities, "_record_cap", record)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            rotation = pool.submit(consume, s, root, cap)
            try:
                assert ready.wait(10)
                insertion = pool.submit(delayed_insert)
                assert insert_started.wait(5)
                # Bounded diagnostic of this exact DB backend, not a process-name
                # wait/grep loop. Prove the original INSERT is really waiting.
                deadline = monotonic() + 5
                with s.db.connect(autocommit=True) as conn:
                    while monotonic() < deadline:
                        state = conn.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (backend[0],)).fetchone()
                        if state and state[0] == "Lock":
                            break
                        sleep(0.01)
                    else:
                        pytest.fail("expected session INSERT did not reach credential lock")
            finally:
                release.set()
            if commit:
                assert rotation.result(timeout=10)["status"] == "rotated"
                with pytest.raises(Exception, match="SESSION_CREDENTIAL_REVISION_REFUSED"):
                    insertion.result(timeout=10)
            else:
                with pytest.raises(RecoveryRefused, match="RECOVERY_CAPABILITY_UNAVAILABLE"):
                    rotation.result(timeout=10)
                sid = insertion.result(timeout=10)
                assert engine.query_one("SELECT revoked_at FROM identity.sessions WHERE session_id = ?", (sid,)) == {"revoked_at": None}
                assert s.capabilities._credential_revisions() == ("1", "1")
                assert len(s.capabilities._cap_events()) == 1
        assert s.audit.verify_chain().ok
    finally:
        release.set()
        engine.close()


def test_sessions_committed_before_rotation_are_revoked_even_with_future_client_clock(recovery: Any) -> None:
    s = recovery
    root, cap = issue(s)
    engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    try:
        sid = insert_fenced_session(engine, "1")
        # Web writes JS time; a forward-skewed creation clock must not escape
        # target-only revocation simply by being greater than DB rotation time.
        engine.execute("UPDATE identity.sessions SET created_at = clock_timestamp() + interval '5 minutes' "
                       "WHERE session_id = ?", (sid,))
        assert consume(s, root, cap)["revoked_target_sessions"] == 2
        assert engine.query_one("SELECT revoked_reason FROM identity.sessions WHERE session_id = ?", (sid,)) == {
            "revoked_reason": "dev-smoke-recovery",
        }
    finally:
        engine.close()


def test_credential_revision_advances_even_when_transaction_timestamps_collide(recovery: Any) -> None:
    s = recovery
    with s.engine.lock:
        s.engine.execute("UPDATE identity.password_credentials SET phc_hash = 'offline-phc-one' WHERE account_id = ?", (TARGET_ACCOUNT_ID,))
        first = s.engine.query_one("SELECT updated_at, credential_revision FROM identity.password_credentials WHERE account_id = ?", (TARGET_ACCOUNT_ID,))
        s.engine.execute("UPDATE identity.password_credentials SET phc_hash = 'offline-phc-two', credential_revision = 1 WHERE account_id = ?", (TARGET_ACCOUNT_ID,))
        second = s.engine.query_one("SELECT updated_at, credential_revision FROM identity.password_credentials WHERE account_id = ?", (TARGET_ACCOUNT_ID,))
        assert first["updated_at"] == second["updated_at"]
        assert (first["credential_revision"], second["credential_revision"]) == (2, 3)
        s.engine.execute("UPDATE identity.password_credentials SET credential_revision = 100 WHERE account_id = ?", (TARGET_ACCOUNT_ID,))
        assert s.capabilities._credential_revisions() == ("1", "3")


def binding_ready(s: Any) -> tuple[Any, Any]:
    root, cap = issue(s)
    consume(s, root, cap)
    insert_fenced_session(s.engine, "2")
    return root, cap


def binding_proven(s: Any, root: Any, cap: Any, intent: dict[str, Any]) -> bool:
    lineage = recovery_bundle_lineage({
        "plan_digest": root.plan_digest, "root_event_id": root.event_id,
        "capability_id": cap.capability_id, "capability_issue_event_id": cap.audit_event_id,
        "rotation_event_id": s.capabilities._cap_events()[-1].event_id,
        "binding_intent_event_id": intent["audit_event_id"], "old_execution_id": OLD_EXECUTION_ID,
        "old_plan_digest": OLD_PLAN_DIGEST, "old_quarantine_event_id": OLD_QUARANTINE_EVENT_ID,
        "invitation_id": INVITATION_ID, "issue_event_id": ISSUE_EVENT_ID, "accept_event_id": ACCEPT_EVENT_ID,
    })
    # Use the actual mounted, allowlisted authenticated audit/user readbacks,
    # not manually minted ACK fixtures or an arbitrary passing receipt.
    events = s.client.get("/api/v1/operator/users/audit-trail", headers=s.headers).json()["events"]
    target = s.client.get(f"/api/v1/operator/users/{TARGET_ACCOUNT_ID}", headers=s.headers).json()
    return recovery_bundle_acknowledged(execution_id=root.execution_id, lineage=lineage,
                                        events=events, target_account=target)


def test_binding_pg_producer_roundtrip_strict_gate_predicate_and_preservation(recovery: Any) -> None:
    s = recovery
    root, cap = binding_ready(s)
    before, old = snapshot(s), old_events(s)
    assert s.binding.binding_inspect(s.principal) is None
    intent = s.binding.binding_intent(s.principal, root)
    assert intent["stage"] == "binding-intent" and intent["put_status"] == 0
    assert not binding_proven(s, root, cap, intent)
    ack = s.binding.binding_acknowledge(s.principal, root, intent_event_id=intent["audit_event_id"], put_status=201)
    assert ack["stage"] == "binding-acknowledged" and ack["put_status"] == 201
    assert ack["credential_binding_verified"] is False
    assert set(ack) == RECOVERY_BINDING_KEYS | {"audit_event_id"}
    assert s.binding.binding_inspect(s.principal) == ack
    assert binding_proven(s, root, cap, intent)
    assert snapshot(s) == before and old_events(s) == old
    assert cap.token not in json.dumps(ack) and PASSWORD not in json.dumps(ack)
    assert "token_hash" not in json.dumps(ack) and "phc_hash" not in json.dumps(ack)
    for action in (lambda: s.binding.binding_intent(s.principal, root),
                   lambda: s.binding.binding_acknowledge(s.principal, root, intent_event_id=intent["audit_event_id"], put_status=201)):
        with pytest.raises(RecoveryRefused):
            action()
    # A committed ACK does not allow recovery of an unknown PUT or gate failure.
    # New root quarantine invalidates lineage, preserving both ledger histories.
    s.recovery.quarantine(s.principal, root)
    assert not binding_proven(s, root, cap, intent)
    assert s.binding.binding_inspect(s.principal) == ack
    assert snapshot(s) == before and old_events(s) == old
    assert s.audit.verify_chain().ok


@pytest.mark.parametrize("operation", ["intent", "ack"])
@pytest.mark.parametrize("drift", ["target_password", "original_password", "target_role", "target_scope", "original_name",
                                  "revoked_original", "fresh_original", "revoked_target", "source", "expired", "quarantine"])
def test_binding_revalidates_postrotation_preservation_session_plan_source(recovery: Any, operation: str, drift: str) -> None:
    s = recovery
    root, _cap = binding_ready(s)
    intent = s.binding.binding_intent(s.principal, root) if operation == "ack" else None
    if drift in {"target_password", "original_password"}:
        account = TARGET_ACCOUNT_ID if drift == "target_password" else PRESERVED_ACCOUNT_ID
        s.engine.execute("UPDATE identity.password_credentials SET phc_hash = 'offline-drift' WHERE account_id = ?", (account,))
    elif drift == "target_role":
        s.engine.execute("INSERT INTO identity.account_roles (account_id, role, granted_by) VALUES (?, 'auditor', 'test')", (TARGET_ACCOUNT_ID,))
    elif drift == "target_scope":
        s.engine.execute("UPDATE identity.account_scopes SET clearance = 'PUBLIC' WHERE account_id = ?", (TARGET_ACCOUNT_ID,))
    elif drift == "original_name":
        s.engine.execute("UPDATE identity.accounts SET display_name = 'offline-drift' WHERE account_id = ?", (PRESERVED_ACCOUNT_ID,))
    elif drift in {"revoked_original", "revoked_target"}:
        account = TARGET_ACCOUNT_ID if drift == "revoked_target" else PRESERVED_ACCOUNT_ID
        s.engine.execute("UPDATE identity.sessions SET revoked_at = now() WHERE account_id = ?", (account,))
    elif drift == "fresh_original":
        s.principal = s.boundary.authenticate(Credentials.from_headers(_sign_in(s, PRESERVED_ACCOUNT_ID, TENANT_ID))).principal
    elif drift == "source":
        s.binding._release_sha = "d" * 40
    elif drift == "expired":
        s.binding._identity._now = lambda: datetime.fromisoformat(s.plan["expires_at"]) + timedelta(seconds=1)
    else:
        s.recovery.quarantine(s.principal, root)
    before, old = snapshot(s), old_events(s)
    with pytest.raises(RecoveryRefused):
        if operation == "intent":
            s.binding.binding_intent(s.principal, root)
        else:
            s.binding.binding_acknowledge(s.principal, root, intent_event_id=intent["audit_event_id"], put_status=201)
    assert snapshot(s) == before and old_events(s) == old
    assert len(s.audit.list_events(correlation_id=RECOVERY_BINDING_CORRELATION)) == (1 if intent else 0)


@pytest.mark.parametrize("operation", ["intent", "ack"])
def test_binding_postcommit_receipt_loss_quarantines_without_replay(recovery: Any, monkeypatch: Any, operation: str) -> None:
    s = recovery
    root, cap = binding_ready(s)
    intent = s.binding.binding_intent(s.principal, root) if operation == "ack" else None
    before, old = snapshot(s), old_events(s)
    # The original service transaction commits, then receipt conversion fails.
    # A later GET may resolve truth but cannot resume the foreground attempt.
    with monkeypatch.context() as patch:
        patch.setattr(s.binding, "_binding_receipt", lambda _e: (_ for _ in ()).throw(RuntimeError("private-lost-reply")))
        with pytest.raises(RecoveryRefused, match="^RECOVERY_BINDING_UNAVAILABLE$"):
            if operation == "intent":
                s.binding.binding_intent(s.principal, root)
            else:
                s.binding.binding_acknowledge(s.principal, root, intent_event_id=intent["audit_event_id"], put_status=201)
    observed = s.binding.binding_inspect(s.principal)
    assert observed["stage"] == ("binding-intent" if operation == "intent" else "binding-acknowledged")
    s.recovery.quarantine(s.principal, root)
    assert not binding_proven(s, root, cap, intent or observed)
    with pytest.raises(RecoveryRefused):
        s.binding.binding_intent(s.principal, root)
    with pytest.raises(RecoveryRefused):
        s.binding.binding_acknowledge(s.principal, root,
            intent_event_id=intent["audit_event_id"] if intent else observed["audit_event_id"], put_status=201)
    assert snapshot(s) == before and old_events(s) == old
    assert s.audit.verify_chain().ok


@pytest.mark.parametrize("drift", ["old_binding", "duplicate_rotation", "consumed_time", "rotation_reference"])
def test_binding_rechecks_persisted_old_and_new_lineage(recovery: Any, drift: str) -> None:
    s = recovery
    root, _cap = binding_ready(s)
    if drift == "old_binding":
        old = s.audit.list_events(correlation_id=ProvisioningJournal._CORRELATION)[0]
        s.audit.record(AuditEvent(event_type=DevSmokeBindingJournal._TYPE, actor=DevSmokeBindingJournal._ACTOR,
            action="DEV_SMOKE_BINDING", resource=DevSmokeBindingJournal._CORRELATION,
            correlation_id=DevSmokeBindingJournal._CORRELATION, outcome="success", metadata={
                **old.metadata, "account_id": TARGET_ACCOUNT_ID, "stage": "binding-intent",
                "secret_name": "ODP_DEV_ADMIN_CREDENTIAL_BUNDLE", "credential_binding_verified": False,
            }))
    elif drift == "duplicate_rotation":
        rotation = s.capabilities._cap_events()[-1]
        s.audit.record(AuditEvent(event_type=rotation.event_type, actor=rotation.actor, action=rotation.action,
            resource=rotation.resource, correlation_id=rotation.correlation_id, outcome="success", metadata=rotation.metadata))
    elif drift == "consumed_time":
        s.engine.execute("UPDATE identity.dev_smoke_recovery_capabilities SET consumed_at = consumed_at + interval '1 second'")
    else:
        s.engine.execute("UPDATE identity.dev_smoke_recovery_capabilities SET rotation_event_id = ?", (str(uuid4()),))
    before = snapshot(s)
    with pytest.raises(RecoveryRefused):
        s.binding.binding_intent(s.principal, root)
    assert snapshot(s) == before
    assert s.binding.binding_inspect(s.principal) is None


@pytest.mark.parametrize("status", [204, 200, 500, True, "201", None])
def test_binding_non201_uncertain_reply_cannot_ack_or_restart(recovery: Any, status: Any) -> None:
    s = recovery
    root, cap = binding_ready(s)
    intent = s.binding.binding_intent(s.principal, root)
    with pytest.raises(RecoveryRefused, match="RECOVERY_BINDING_ACK_REFUSED"):
        s.binding.binding_acknowledge(s.principal, root, intent_event_id=intent["audit_event_id"], put_status=status)
    with pytest.raises(RecoveryRefused, match="RECOVERY_BINDING_ALREADY_ATTEMPTED"):
        s.binding.binding_intent(s.principal, root)
    assert not binding_proven(s, root, cap, intent)
    s.recovery.quarantine(s.principal, root)
    with pytest.raises(RecoveryRefused):
        s.binding.binding_acknowledge(s.principal, root, intent_event_id=intent["audit_event_id"], put_status=201)
    assert [e.metadata["stage"] for e in s.audit.list_events(correlation_id=RECOVERY_BINDING_CORRELATION)] == ["binding-intent"]


def test_binding_requires_committed_rotation_and_fresh_credential_revision_session(recovery: Any) -> None:
    s = recovery
    root, cap = issue(s)
    with pytest.raises(RecoveryRefused, match="RECOVERY_ROTATION_UNVERIFIED"):
        s.binding.binding_intent(s.principal, root)
    consume(s, root, cap)
    with pytest.raises(RecoveryRefused, match="RECOVERY_FRESH_TARGET_SESSION_REQUIRED"):
        s.binding.binding_intent(s.principal, root)
    insert_fenced_session(s.engine, "2")
    with pytest.raises(RecoveryRefused, match="RECOVERY_BINDING_ACK_REFUSED"):
        s.binding.binding_acknowledge(s.principal, root, intent_event_id=str(uuid4()), put_status=201)
    intent = s.binding.binding_intent(s.principal, root)
    with pytest.raises(RecoveryRefused, match="RECOVERY_BINDING_ACK_REFUSED"):
        s.binding.binding_acknowledge(s.principal, root, intent_event_id=str(uuid4()), put_status=201)
    with s.engine.lock:
        with pytest.raises(RecoveryRefused, match="RECOVERY_OUTERMOST_TRANSACTION_REQUIRED"):
            s.binding.binding_acknowledge(s.principal, root, intent_event_id=intent["audit_event_id"], put_status=201)


@pytest.mark.parametrize("operation", ["intent", "ack"])
@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_binding_audit_or_real_pg_commit_failure_never_returns_success(recovery: Any, monkeypatch: Any, operation: str, failure: str) -> None:
    s = recovery
    root, cap = binding_ready(s)
    intent = s.binding.binding_intent(s.principal, root) if operation == "ack" else None
    before, old = snapshot(s), old_events(s)
    if failure == "audit":
        real_record = s.audit.record

        def refuse_binding(event: AuditEvent) -> AuditEvent:
            if event.correlation_id == RECOVERY_BINDING_CORRELATION:
                raise RuntimeError("private-value")
            return real_record(event)

        monkeypatch.setattr(s.audit, "record", refuse_binding)
    else:
        with s.db.connect(autocommit=True) as conn:
            conn.execute("""
                CREATE FUNCTION odp_runtime.refuse_binding_commit() RETURNS trigger AS $$
                BEGIN
                    IF NEW.correlation_id LIKE 'dev-smoke-recovery-binding:%' THEN
                        RAISE EXCEPTION 'private-commit-value';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                CREATE CONSTRAINT TRIGGER binding_commit_failure
                AFTER INSERT ON odp_runtime.durable_audit_events
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                EXECUTE FUNCTION odp_runtime.refuse_binding_commit();
            """)
    with pytest.raises(RecoveryRefused, match="^RECOVERY_BINDING_UNAVAILABLE$"):
        if operation == "intent":
            s.binding.binding_intent(s.principal, root)
        else:
            s.binding.binding_acknowledge(s.principal, root, intent_event_id=intent["audit_event_id"], put_status=201)
    assert snapshot(s) == before and old_events(s) == old
    assert len(s.audit.list_events(correlation_id=RECOVERY_BINDING_CORRELATION)) == (1 if intent else 0)
    if intent:
        assert not binding_proven(s, root, cap, intent)
    assert s.audit.verify_chain().ok


@pytest.mark.parametrize("operation", ["intent", "ack"])
def test_binding_two_pg_pools_cannot_duplicate_intent_or_ack(recovery: Any, operation: str) -> None:
    s = recovery
    root, _cap = binding_ready(s)
    intent = s.binding.binding_intent(s.principal, root) if operation == "ack" else None
    engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    try:
        other = RecoveryBindingJournal(engine=engine, audit_log=DurableAuditLog(engine), environment="dev",
            release_profile="dev-admin", release_sha=SHA, manifest_digest=MANIFEST)

        def append(service: Any) -> str:
            try:
                if operation == "intent":
                    service.binding_intent(s.principal, root)
                else:
                    service.binding_acknowledge(s.principal, root, intent_event_id=intent["audit_event_id"], put_status=201)
                return "success"
            except RecoveryRefused:
                return "refused"

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(append, (s.binding, other))) == ["refused", "success"]
        assert len(s.audit.list_events(correlation_id=RECOVERY_BINDING_CORRELATION)) == (2 if intent else 1)
        assert s.audit.verify_chain().ok
    finally:
        engine.close()


def test_actual_web_postgres_stores_refuse_old_verified_revision_after_rotation(recovery: Any) -> None:
    import os
    import subprocess
    from shared.identity.credential_service import CredentialService
    s = recovery
    root_path = Path.cwd()
    binary = root_path / "node_modules/.bin/vitest"
    if not binary.is_file():
        pytest.skip("production Web store PG proof requires the existing frozen-lock Node test environment")
    # Two actual credential revisions: fixture1 -> old verified PHC2 -> rotation3.
    # No real human password/capability/token is provided to this worker or child.
    service = CredentialService()
    old_phc = service.hash_password("Old-offline-password-729!")
    s.engine.execute("UPDATE identity.password_credentials SET phc_hash = ? WHERE account_id = ?", (old_phc, TARGET_ACCOUNT_ID))
    assert service.verify_password(old_phc, "Old-offline-password-729!")
    root, cap = issue(s)
    assert consume(s, root, cap)["status"] == "rotated"
    assert s.capabilities._credential_revisions() == ("1", "3")
    result = subprocess.run(
        [str(binary), "run", "src/lib/auth/__tests__/identityStore.test.ts"],
        cwd=root_path / "apps/web", env={**os.environ, "NODE_ENV": "test",
            "ODP_TEST_RECOVERY_DATABASE_URL": s.db.url()},
        timeout=60, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert s.capabilities._credential_revisions() == ("1", "4")
