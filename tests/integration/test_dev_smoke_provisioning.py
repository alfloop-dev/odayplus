"""Incremental real-router/PG invitation proof, NOT live provisioning evidence.

The acceptance factory and full runtime composition are exercised offline.
Encrypted bundle staging is covered offline; foreground approval, activation
and deploy/gate orchestration remain to be implemented.
No fake principal or permission dependency override:
all issuer/revoker requests use the existing production auth/session stack.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.identity_invitations import create_invitation_acceptance_router
from apps.api.app.routes.operator_modules.users_roles import create_user_role_sub_router
from modules.opsboard.application.user_role_management import UserRoleManagementService
from shared.auth import Role
from shared.identity.invitation_service import InvitationRefused, InvitationService
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.postgresql import PostgresEngine
from tests.identity.test_identity_user_role_management import (
    OTHER_TENANT, TENANT, _q, _sign_in, stack,  # noqa: F401
)
from tests.security.test_dev_smoke_invitation import (
    EMAIL, PASSWORD, _accept, _issue, _snapshot, invitations,  # noqa: F401
)

PATH = "/api/v1/operator/users/invitations"
ACCEPT = "/api/v1/auth/invitations/accept"


@pytest.fixture
def acceptance(invitations: Any) -> Any:
    s = invitations
    app = FastAPI()
    app.include_router(create_invitation_acceptance_router(s.invites), prefix="/api/v1")
    s.accept_client = TestClient(app)
    return s


def _accept_body(issued: Any) -> dict[str, str]:
    return {"invitation_id": issued.invitation_id, "token": issued.token,
            "username": "release.smoke", "password": PASSWORD}


def _headers(s: Any) -> dict[str, str]:
    # Freshly verify bearer through the canonical AuthenticationBoundary.
    return _sign_in(s, s.admin)


def test_actual_router_invites_and_revokes_as_verified_admin(invitations: Any) -> None:
    s = invitations
    headers = _headers(s)
    response = s.client.post(PATH, headers={**headers, "x-tenant-id": OTHER_TENANT,
        "x-subject-id": "request-actor-forgery", "x-roles": "operations_manager"}, json={"email": EMAIL})
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    issued = response.json()
    assert issued["tenant_id"] == TENANT
    assert len(issued["token"]) == 43
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]
    (actor, tenant), = _q(s, "SELECT created_by, tenant_id::text FROM identity.invitations")
    assert (actor, tenant) == (s.admin, TENANT)
    events = [e for e in s.audit.list_events(tenant_id=TENANT) if e.event_type == "identity.account.invite"]
    assert len(events) == 1 and events[0].actor == s.admin
    assert issued["token"] not in json.dumps(events[0].metadata)

    duplicate = s.client.post(PATH, headers=headers, json={"email": EMAIL})
    assert duplicate.status_code == 409
    revoked = s.client.post(f"{PATH}/{issued['invitation_id']}/revoke", headers=headers, json={})
    assert revoked.status_code == 200, revoked.text
    assert revoked.headers["cache-control"] == "no-store"
    assert issued["token"] not in revoked.text
    with pytest.raises(InvitationRefused, match="INVITATION_UNAVAILABLE"):
        s.invites.accept(invitation_id=issued["invitation_id"], token=issued["token"],
                        username="release.smoke", password=PASSWORD)
    # Explicit revocation makes a new capability possible, never an implicit reset.
    replacement = s.client.post(PATH, headers=headers, json={"email": EMAIL})
    assert replacement.status_code == 201
    assert replacement.json()["token"] != issued["token"]


@pytest.mark.parametrize("body", [
    {"email": EMAIL, "actor": "do-not-echo-this-secret"},
    {"email": EMAIL, "tenant_id": OTHER_TENANT},
    {"email": EMAIL, "roles": ["operations_manager"]},
    {"email": EMAIL, "scope": {"clearance": "RESTRICTED"}},
    {"email": EMAIL, "lifetime_seconds": True},
    {"email": EMAIL, "lifetime_seconds": "3600"},
    {"email": EMAIL, "lifetime_seconds": 259201},
    {"email": ["do-not-echo-this-secret"]},
    ["do-not-echo-this-secret"],
])
def test_actual_router_strict_payload_never_echoes_inputs(invitations: Any, body: Any) -> None:
    s = invitations
    result = s.client.post(PATH, headers=_headers(s), json=body)
    assert result.status_code == 422, result.text
    assert "do-not-echo-this-secret" not in result.text
    assert EMAIL not in result.text and OTHER_TENANT not in result.text
    assert _q(s, "SELECT count(*) FROM identity.invitations") == [(0,)]


def test_actual_router_bounds_body_and_refuses_malformed_json(invitations: Any) -> None:
    s = invitations
    headers = {**_headers(s), "content-type": "application/json"}
    for body, status in (("{private-password-invalid-json", 422), ('"' + "s" * 4097 + '"', 413)):
        result = s.client.post(PATH, headers=headers, content=body)
        assert result.status_code == status
        assert "private-password" not in result.text
    assert s.client.post(PATH, headers=_headers(s), data={"email": EMAIL}).status_code == 415
    assert _q(s, "SELECT count(*) FROM identity.invitations") == [(0,)]


@pytest.mark.parametrize("mode", ["anonymous", "header_forgery", "wrong_role", "must_change", "revoked"])
def test_actual_router_authentication_denials_never_reach_service(invitations: Any, monkeypatch: Any, mode: str) -> None:
    s = invitations
    headers = _headers(s)
    if mode == "anonymous":
        headers = {}
    elif mode == "header_forgery":
        headers = {"x-subject-id": s.admin, "x-roles": "platform_admin", "x-tenant-id": TENANT}
    elif mode == "wrong_role":
        s.engine.execute("DELETE FROM identity.account_roles WHERE account_id = ? AND role = ?",
                         (s.admin, Role.PLATFORM_ADMIN.value))
    elif mode == "must_change":
        s.engine.execute("UPDATE identity.password_credentials SET must_change = true WHERE account_id = ?", (s.admin,))
    else:
        s.engine.execute("UPDATE identity.sessions SET revoked_at = now() WHERE account_id = ?", (s.admin,))
    calls: list[Any] = []
    monkeypatch.setattr(InvitationService, "issue", lambda *args, **kwargs: calls.append(args))
    result = s.client.post(PATH, headers=headers, json={"email": EMAIL})
    assert result.status_code in {401, 403}, result.text
    assert calls == []
    assert _q(s, "SELECT count(*) FROM identity.invitations") == [(0,)]
    if mode != "must_change":
        assert any(e.outcome in {"failure", "deny"} for e in s.audit.list_events())
    else:
        # Existing must-change enforcement rejects before the user permission
        # dependency records its authorization decision. This incremental suite
        # proves refusal/no service call, not a new audit implementation there.
        assert result.json()["detail"] == "PASSWORD_CHANGE_REQUIRED"


def test_no_guard_or_document_service_is_not_a_provisioning_shortcut(invitations: Any) -> None:
    s = invitations
    app = FastAPI()
    app.include_router(create_user_role_sub_router(s.service), prefix="/api/v1/operator")
    assert TestClient(app).post(PATH, json={"email": EMAIL}, headers=_headers(s)).status_code == 403
    document = FastAPI()
    document.include_router(create_user_role_sub_router(UserRoleManagementService()), prefix="/api/v1/operator")
    assert TestClient(document).post(PATH, json={"email": EMAIL}).status_code == 403
    assert _q(s, "SELECT count(*) FROM identity.invitations") == [(0,)]


def test_actual_router_infrastructure_error_rolls_back_without_secret_detail(invitations: Any, monkeypatch: Any) -> None:
    s = invitations
    headers = _headers(s)
    original = s.audit.record

    def fail_invite(event: Any) -> Any:
        if event.event_type == "identity.account.invite":
            raise RuntimeError("private-database-password-or-token")
        return original(event)

    monkeypatch.setattr(s.audit, "record", fail_invite)
    response = s.client.post(PATH, headers=headers, json={"email": EMAIL})
    assert response.status_code == 503
    assert "private-database" not in response.text
    assert _q(s, "SELECT count(*) FROM identity.invitations") == [(0,)]
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]


def test_concurrent_accepts_across_pg_connections_create_exactly_one_account(invitations: Any) -> None:
    s = invitations
    issued = _issue(s)
    before = _snapshot(s)
    # Independent pools/transaction locks: the DB advisory lock, not a Python
    # mutex, must serialize capability consumption across runtime instances.
    other_engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    try:
        other = InvitationService(engine=other_engine, audit_log=DurableAuditLog(other_engine))

        def accept(service: InvitationService) -> str:
            try:
                service.accept(invitation_id=issued.invitation_id, token=issued.token,
                               username="release.smoke", password=PASSWORD)
                return "accepted"
            except InvitationRefused as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(accept, (s.invites, other)))
        assert sorted(outcomes) == ["INVITATION_UNAVAILABLE", "accepted"]
        assert _snapshot(s) == before
        assert _q(s, "SELECT count(*) FROM identity.accounts") == [(2,)]
        assert len([e for e in s.audit.list_events() if e.event_type == "identity.account.accept"]) == 1
        assert s.audit.verify_chain().ok
    finally:
        other_engine.close()


def test_acceptance_budget_serializes_across_independent_pg_pools(invitations: Any) -> None:
    s = invitations
    issued = _issue(s)
    other_engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    try:
        other = InvitationService(engine=other_engine, audit_log=DurableAuditLog(other_engine))

        def reserve(service: InvitationService) -> str:
            try:
                service._reserve_acceptance(issued.invitation_id)
                return "reserved"
            except InvitationRefused as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(reserve, [s.invites, other] * 3))
        assert outcomes.count("reserved") == 5
        assert outcomes.count("INVITATION_RATE_LIMITED") == 1
        assert _q(s, "SELECT failure_count FROM identity.login_attempts "
                  "WHERE attempt_key = 'invitation-accept:global'") == [(6,)]
        assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]
    finally:
        other_engine.close()


def test_acceptance_router_creates_only_with_capability_and_no_session(acceptance: Any) -> None:
    s = acceptance
    issued = _issue(s)
    before = _snapshot(s)
    response = s.accept_client.post(ACCEPT, json=_accept_body(issued), headers={
        "x-tenant-id": OTHER_TENANT, "x-subject-id": s.admin, "x-roles": "operations_manager",
    })
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert response.json()["tenant_id"] == TENANT
    assert _snapshot(s) == before
    for secret in (issued.token, PASSWORD):
        assert secret not in response.text
    assert s.accept_client.post(ACCEPT, json=_accept_body(issued)).status_code == 409
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(2,)]
    assert _q(s, "SELECT count(*) FROM identity.sessions WHERE account_id = %s",
              (response.json()["account_id"],)) == [(0,)]


@pytest.mark.parametrize("change", ["token", "password", "actor", "tenant", "roles", "array"])
def test_acceptance_router_rejects_bad_input_without_secret_echo(acceptance: Any, change: str) -> None:
    s = acceptance
    issued = _issue(s)
    body: Any = _accept_body(issued)
    if change in {"token", "password"}:
        body[change] = ["private-request-secret"]
    elif change == "array":
        body = ["private-request-secret"]
    else:
        body[change] = "private-request-secret"
    response = s.accept_client.post(ACCEPT, json=body)
    assert response.status_code == 422
    for secret in (issued.token, PASSWORD, "private-request-secret"):
        assert secret not in response.text
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]


def test_acceptance_router_wrong_capability_does_not_hash_and_durably_limits(acceptance: Any, monkeypatch: Any) -> None:
    s = acceptance
    issued = _issue(s)
    body = {**_accept_body(issued), "token": "a" * 43 if issued.token != "a" * 43 else "b" * 43}

    def forbidden_hasher() -> Any:
        pytest.fail("invalid capability reached Argon2")

    monkeypatch.setattr("shared.identity.invitation_service.CredentialService", forbidden_hasher)
    for _ in range(5):
        assert s.accept_client.post(ACCEPT, json=body).status_code == 409
    response = s.accept_client.post(ACCEPT, json=_accept_body(issued))
    assert response.status_code == 429
    assert response.json() == {"error": {"code": "INVITATION_RATE_LIMITED"}}
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]
    assert _q(s, "SELECT accepted_at FROM identity.invitations") == [(None,)]


def test_acceptance_router_audit_failure_has_safe_error_and_atomic_recovery(acceptance: Any, monkeypatch: Any) -> None:
    s = acceptance
    issued = _issue(s)
    before = _snapshot(s)
    with monkeypatch.context() as patch:
        patch.setattr(s.audit, "record", lambda event: (_ for _ in ()).throw(RuntimeError(PASSWORD)))
        response = s.accept_client.post(ACCEPT, json=_accept_body(issued))
    assert response.status_code == 503 and PASSWORD not in response.text
    assert _snapshot(s) == before
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]
    assert _q(s, "SELECT accepted_at FROM identity.invitations") == [(None,)]
    assert _q(s, "SELECT failure_count FROM identity.login_attempts "
              "WHERE attempt_key = 'invitation-accept:global'") == [(1,)]
    assert s.accept_client.post(ACCEPT, json=_accept_body(issued)).status_code == 201


@pytest.fixture
def pg_runtime(invitations: Any, monkeypatch: Any) -> Any:
    from apps.api.oday_api.main import create_app
    from apps.api.oday_api.security import dependencies
    from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
    from shared.infrastructure.persistence.factory import build_persistence
    from tests.integration.test_assisted_listing_postgresql_runtime import _install_canonical_runtime

    s = invitations
    # Existing offline fixture supplies unrelated core/workflow relations;
    # bundled pgserver has no PostGIS for full domain migrations. These are
    # test inputs, not live schema or healthy business-operation evidence.
    # Identity/runtime migrations, PG factory and global guard remain real.
    _install_canonical_runtime(s.db.url())
    apply_upgrade_to_database(s.db.url())
    monkeypatch.setenv("ODAY_DATABASE_URL", s.db.url())
    monkeypatch.setenv("ODP_PERSISTENCE", "postgresql")
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    bundle = build_persistence(mode="postgresql")
    # Genuine existing boundary and PG session resolver, not a fabricated
    # Principal or a permission dependency override. Both pools share the DB.
    monkeypatch.setattr(dependencies, "default_boundary", lambda: s.boundary)
    try:
        yield s, TestClient(create_app(persistence=bundle, external_provider_validation=lambda: None))
    finally:
        bundle.engine.close()


def test_full_runtime_mounts_admin_issue_and_pg_capability_acceptance(pg_runtime: Any) -> None:
    s, client = pg_runtime
    headers = _headers(s)
    before = _snapshot(s)
    issued = client.post(PATH, headers=headers, json={"email": EMAIL})
    assert issued.status_code == 201, issued.text
    body = {"invitation_id": issued.json()["invitation_id"], "token": issued.json()["token"],
            "username": "release.smoke", "password": PASSWORD}
    # Capability consumption accepts no user bearer or request actor/tenant.
    accepted = client.post(ACCEPT, json=body, headers={"x-subject-id": "forged",
                           "x-tenant-id": OTHER_TENANT, "x-roles": "operations_manager"})
    assert accepted.status_code == 201, accepted.text
    receipt = accepted.json()
    assert receipt["tenant_id"] == TENANT and receipt["status"] == "accepted"
    assert receipt["account_id"] != s.admin
    assert PASSWORD not in accepted.text and body["token"] not in accepted.text
    assert accepted.headers["cache-control"] == "no-store"
    assert "set-cookie" not in accepted.headers
    assert _q(s, "SELECT role FROM identity.account_roles WHERE account_id = %s",
              (receipt["account_id"],)) == [("platform_admin",)]
    assert _q(s, "SELECT status FROM identity.accounts WHERE account_id = %s",
              (receipt["account_id"],)) == [("active",)]
    assert client.post(ACCEPT, json=body).status_code == 409
    assert _snapshot(s) == before

    # Exercise the actual release predicate against production PG-backed
    # account/audit projections, not just hand-authored gate fixtures.
    from delivery_toolchain.e2e.check_live_e2e_gate import _invitation_provenance
    users = client.get("/api/v1/operator/users", headers=headers)
    trail = client.get("/api/v1/operator/users/audit-trail", headers=headers)
    assert users.status_code == trail.status_code == 200
    own = next(u for u in users.json()["users"] if u["subject_id"] == receipt["account_id"])
    provenance = _invitation_provenance(own, trail.json()["events"])
    assert provenance == {
        "invitation_id": receipt["invitation_id"], "issue_event_id": issued.json()["audit_event_id"],
        "accept_event_id": receipt["audit_event_id"], "issuer_account_id": s.admin,
    }
    assert not any(e["event_type"] == "identity.account.bootstrap"
                   and e["metadata"].get("account_id") == receipt["account_id"]
                   for e in trail.json()["events"])
    assert PASSWORD not in json.dumps(provenance) and body["token"] not in json.dumps(provenance)
    assert _snapshot(s) == before


def test_runtime_contract_is_exported_with_exact_invitation_paths_and_client(monkeypatch: Any) -> None:
    from delivery_toolchain.openapi.export_openapi import ARTIFACT_PATH, build_schema, serialize
    from delivery_toolchain.openapi.generate_client import OUTPUT_PATH, render
    from apps.api.oday_api.main import create_app
    from shared.api.versioning import alias_paths, versioned_paths
    from shared.infrastructure.persistence.factory import build_persistence

    monkeypatch.setenv("ODP_PERSISTENCE", "memory")
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
    schema = build_schema()
    assert ARTIFACT_PATH.read_text() == serialize(schema)
    # The generator reads the deterministically sorted serialized artifact;
    # component property order in the in-memory FastAPI schema is different.
    assert OUTPUT_PATH.read_text() == render(json.loads(serialize(schema)))
    expected = {PATH, f"{PATH}/{{invitation_id}}/revoke", ACCEPT}
    actual = {path for path in schema["paths"] if "/invitations" in path}
    assert actual == expected
    for path in expected:
        assert set(schema["paths"][path]) == {"post"}
        request = schema["paths"][path]["post"]["requestBody"]
        assert request["required"] is True
        assert request["content"]["application/json"]["schema"]["additionalProperties"] is False
    assert set(schema["paths"][ACCEPT]["post"]["responses"]) >= {"201", "409", "429", "503"}
    payload = schema["paths"][ACCEPT]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert payload["properties"]["token"]["writeOnly"] is True
    assert payload["properties"]["password"]["writeOnly"] is True
    app = create_app(persistence=build_persistence(mode="memory"), external_provider_validation=lambda: None)
    assert alias_paths(app) == [path[len("/api/v1"):] for path in versioned_paths(app)]
    client = TestClient(app)
    # No PostgreSQL means safe infrastructure refusal, never fallback account
    # creation; both normal route and deprecated alias reach the same adapter.
    for path in (ACCEPT, ACCEPT[len("/api/v1"):]):
        response = client.post(path, json={"password": PASSWORD})
        assert response.status_code == 503
        assert response.json() == {"error": {"code": "IDENTITY_PERSISTENCE_UNAVAILABLE"}}
        assert PASSWORD not in response.text


@pytest.fixture
def foreground_plan_input() -> Any:
    """Offline proposed request, not a custodian approval or live receipt."""
    from datetime import UTC, datetime
    from delivery_toolchain.release.provision_dev_smoke import (
        AUTHORIZATION_ID, PRESERVED_ACCOUNT_ID, PURPOSE, REPOSITORY, TENANT_ID,
    )
    now = datetime(2026, 10, 10, 5, tzinfo=UTC)
    account = {
        "subject_id": PRESERVED_ACCOUNT_ID, "tenant_id": TENANT_ID, "username": "ajoe734",
        "email": "ajoe734@odayplus.com.tw", "status": "active", "identity_source": "identity.accounts",
        "roles": ["auditor", "operations_manager", "platform_admin"],
        "scope": {"tenant_id": TENANT_ID, "clearance": "CONFIDENTIAL", **{axis: [] for axis in (
            "brand_ids", "region_ids", "store_ids", "assigned_area_ids", "heat_zone_ids", "modules",
        )}},
    }
    plan = {
        "authorization_id": AUTHORIZATION_ID, "repository": REPOSITORY, "environment": "dev",
        "release_profile": "dev-admin", "release_sha": "a" * 40, "manifest_digest": "sha256:" + "b" * 64,
        "tenant_id": TENANT_ID, "actor_account_id": PRESERVED_ACCOUNT_ID, "purpose": PURPOSE,
        "execution_id": "f56189b9-a0c2-4db2-aedb-78e1ff854cbb", "username": "release.smoke",
        "email": "owner-approved@example.invalid", "recipient_custodian": "offline-custodian",
        "recipient_control": "owner-controlled", "expires_at": "2026-10-10T05:30:00+00:00",
    }
    return plan, {"original_account": account, "release_sha": plan["release_sha"],
                  "manifest_digest": plan["manifest_digest"], "now": now}


def test_foreground_preflight_is_pure_and_never_claims_execution(foreground_plan_input: Any) -> None:
    from copy import deepcopy
    from delivery_toolchain.release.provision_dev_smoke import validate_foreground_plan
    plan, context = foreground_plan_input
    before = deepcopy((plan, context))
    checked = validate_foreground_plan(plan, **context)
    assert (plan, context) == before
    assert checked.username == plan["username"] and checked.email == plan["email"]
    assert plan["email"] not in repr(checked)
    receipt = checked.to_receipt()
    assert receipt["stage"] == "preflight-only" and receipt["execution_authorized"] is False
    assert "deployment_success" not in receipt and "account_created" not in receipt
    assert plan["email"] not in json.dumps(receipt)
    # No anonymous CLI or default workflow integration exists. The foreground
    # Web executor is a class, not a module-level activation function.
    import delivery_toolchain.release.provision_dev_smoke as module
    assert not hasattr(module, "main") and not hasattr(module, "execute")


@pytest.mark.parametrize("key,value", [
    ("authorization_id", "other-authorization"), ("repository", "other/repository"),
    ("environment", "production"), ("release_profile", "full"),
    ("tenant_id", OTHER_TENANT), ("actor_account_id", OTHER_TENANT), ("purpose", "business-admin"),
    ("release_sha", "dev"), ("release_sha", "c" * 40), ("manifest_digest", "sha256:" + "c" * 64),
    ("execution_id", "not-a-uuid"), ("execution_id", "00000000-0000-0000-0000-000000000000"),
    ("execution_id", "F56189B9-A0C2-4DB2-AEDB-78E1FF854CBB"),
    ("expires_at", "2026-10-10T05:00:00+00:00"), ("expires_at", "2026-10-10T06:00:01+00:00"),
    ("expires_at", "2026-10-10T05:30:00"), ("expires_at", "bad-time"),
    ("username", "AJOE734"), ("username", "release.smoke\n"), ("username", "ab"),
    ("email", "AJOE734@ODAYPLUS.COM.TW"), ("email", "owner@example.invalid\n"),
    ("recipient_control", "assumed-plus-alias"), ("recipient_custodian", ""),
    ("recipient_custodian", "private\ninput"), ("password", "private-secret-do-not-echo"),
    ("token", "private-secret-do-not-echo"), ("roles", ["platform_admin"]),
    ("environment", True),
])
def test_foreground_preflight_refuses_unbound_or_secret_input(
    foreground_plan_input: Any, key: str, value: Any,
) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused, validate_foreground_plan
    plan, context = foreground_plan_input
    plan[key] = value
    with pytest.raises(ProvisioningRefused) as error:
        validate_foreground_plan(plan, **context)
    assert str(error.value).startswith("PROVISIONING_")
    assert "private" not in str(error.value) and error.value.__cause__ is None


@pytest.mark.parametrize("change", ["roles", "duplicate_role", "status", "tenant", "scope", "identity", "account", "clock"])
def test_foreground_preflight_requires_exact_original_inventory(foreground_plan_input: Any, change: str) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused, validate_foreground_plan
    plan, context = foreground_plan_input
    original = context["original_account"]
    if change == "roles":
        original["roles"].remove("operations_manager")
    elif change == "duplicate_role":
        original["roles"].append("platform_admin")
    elif change == "status":
        original["status"] = "disabled"
    elif change == "tenant":
        original["tenant_id"] = OTHER_TENANT
    elif change == "scope":
        original["scope"]["brand_ids"] = ["enlarged-scope"]
    elif change == "identity":
        original["identity_source"] = "request-headers"
    elif change == "account":
        original["subject_id"] = OTHER_TENANT
    else:
        context["now"] = context["now"].replace(tzinfo=None)
    with pytest.raises(ProvisioningRefused):
        validate_foreground_plan(plan, **context)


@pytest.fixture
def provisioning_journal(invitations: Any, foreground_plan_input: Any) -> Any:
    """Real PG journal with OFFLINE proposed inputs, not foreground approval."""
    from datetime import datetime, timedelta
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningJournal
    s = invitations
    plan, context = foreground_plan_input
    now = datetime.fromisoformat(s.engine.query_one("SELECT clock_timestamp() AS now")["now"])
    plan["expires_at"] = (now + timedelta(minutes=30)).isoformat()
    context.pop("now")
    return s, ProvisioningJournal(engine=s.engine, audit_log=s.audit), plan, context


def test_journal_reservation_is_nonsecret_bookkeeping_not_account_creation(
    provisioning_journal: Any, monkeypatch: Any,
) -> None:
    import subprocess
    s, journal, plan, context = provisioning_journal
    before = _snapshot(s)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("journal launched an executor or an identity lifecycle operation")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(InvitationService, "issue", forbidden)
    monkeypatch.setattr(InvitationService, "accept", forbidden)
    assert journal.inspect() is None
    reserved = journal.reserve(plan, **context)
    assert reserved.stage == "reserved" and reserved.execution_id == plan["execution_id"]
    assert reserved.plan_digest.startswith("sha256:")
    assert journal.inspect() == reserved
    events = s.audit.list_events(correlation_id=journal._CORRELATION)
    assert len(events) == 1
    assert events[0].actor != context["original_account"]["subject_id"]
    encoded = json.dumps({"events": [e.metadata for e in events], "receipt": reserved.to_receipt()})
    assert plan["email"] not in encoded and plan["recipient_custodian"] not in encoded
    assert reserved.to_receipt()["execution_authorized"] is False
    assert "deployment_success" not in encoded and "account_created" not in encoded
    assert _snapshot(s) == before
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]
    assert _q(s, "SELECT count(*) FROM identity.invitations") == [(0,)]
    assert s.audit.verify_chain().ok


@pytest.mark.parametrize("change", [None, "execution_id", "release_sha", "email", "username", "recipient_custodian"])
def test_journal_root_can_never_be_reserved_twice(provisioning_journal: Any, change: str | None) -> None:
    from uuid import uuid4
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, journal, plan, context = provisioning_journal
    reserved = journal.reserve(plan, **context)
    if change == "execution_id":
        plan[change] = str(uuid4())
    elif change == "release_sha":
        plan[change] = context[change] = "c" * 40
    elif change == "email":
        plan[change] = "other-owner@example.invalid"
    elif change == "username":
        plan[change] = "other.smoke"
    elif change == "recipient_custodian":
        plan[change] = "other-custodian"
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_ALREADY_RESERVED"):
        journal.reserve(plan, **context)
    assert journal.inspect() == reserved
    assert len(s.audit.list_events(correlation_id=journal._CORRELATION)) == 1


def test_journal_serializes_single_use_across_independent_pg_connections(provisioning_journal: Any) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningJournal, ProvisioningRefused
    s, journal, plan, context = provisioning_journal
    engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    try:
        other = ProvisioningJournal(engine=engine, audit_log=DurableAuditLog(engine))

        def reserve(current: Any) -> str:
            try:
                return current.reserve(plan, **context).stage
            except ProvisioningRefused as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(reserve, (journal, other)))
        assert sorted(outcomes) == ["PROVISIONING_ALREADY_RESERVED", "reserved"]
        assert journal.inspect() == other.inspect()
        assert len(s.audit.list_events(correlation_id=journal._CORRELATION)) == 1
        assert s.audit.verify_chain().ok
    finally:
        engine.close()


def test_journal_audit_failure_rolls_back_and_reports_no_secret(provisioning_journal: Any, monkeypatch: Any) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, journal, plan, context = provisioning_journal
    record = s.audit.record

    def fail_after_append(event: Any) -> Any:
        record(event)
        raise RuntimeError("private-password-or-token")

    with monkeypatch.context() as patch:
        patch.setattr(s.audit, "record", fail_after_append)
        with pytest.raises(ProvisioningRefused, match="PROVISIONING_JOURNAL_UNAVAILABLE") as error:
            journal.reserve(plan, **context)
    assert "private" not in str(error.value) and error.value.__cause__ is None
    assert journal.inspect() is None
    assert journal.reserve(plan, **context).stage == "reserved"
    assert s.audit.verify_chain().ok


@pytest.mark.parametrize("invalid", ["expired", "secret", "original_roles", "release", "clock"])
def test_journal_revalidates_before_reserving(provisioning_journal: Any, monkeypatch: Any, invalid: str) -> None:
    from datetime import UTC, datetime
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, journal, plan, context = provisioning_journal
    if invalid == "expired":
        plan["expires_at"] = "2000-01-01T00:00:00+00:00"
    elif invalid == "secret":
        plan["password"] = "private-credential-input"
    elif invalid == "original_roles":
        context["original_account"]["roles"].remove("operations_manager")
    elif invalid == "release":
        context["release_sha"] = "c" * 40
    else:
        # Caller cannot supply a convenient old timestamp to extend expiry.
        monkeypatch.setattr(journal, "_now", lambda: datetime(2100, 1, 1, tzinfo=UTC))
    with pytest.raises(ProvisioningRefused) as error:
        journal.reserve(plan, **context)
    assert "private" not in str(error.value)
    assert journal.inspect() is None
    assert _q(s, "SELECT count(*) FROM identity.invitations") == [(0,)]


def test_journal_restart_quarantine_after_expiry_never_releases_root(provisioning_journal: Any, monkeypatch: Any) -> None:
    from datetime import UTC, datetime
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningJournal, ProvisioningRefused
    s, journal, plan, context = provisioning_journal
    reserved = journal.reserve(plan, **context)
    engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    try:
        restarted = ProvisioningJournal(engine=engine, audit_log=DurableAuditLog(engine))
        assert restarted.inspect() == reserved
        monkeypatch.setattr(restarted, "_now", lambda: datetime(2100, 1, 1, tzinfo=UTC))
        recovery = restarted.require_recovery(reserved)
        assert recovery.stage == "recovery-required" and recovery.plan_digest == reserved.plan_digest
        assert recovery.event_id != reserved.event_id
        assert journal.inspect() == recovery
        assert recovery.to_receipt()["execution_authorized"] is False
        with pytest.raises(ProvisioningRefused, match="PROVISIONING_ALREADY_RESERVED"):
            journal.reserve(plan, **context)
        with pytest.raises(ProvisioningRefused, match="PROVISIONING_RECOVERY_ALREADY_RECORDED"):
            journal.require_recovery(reserved)
        assert s.audit.verify_chain().ok
    finally:
        engine.close()


@pytest.mark.parametrize("field", ["execution_id", "plan_digest", "event_id", "stage"])
def test_journal_recovery_requires_exact_original_reservation(provisioning_journal: Any, field: str) -> None:
    from dataclasses import replace
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    _, journal, plan, context = provisioning_journal
    reserved = journal.reserve(plan, **context)
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_RESERVATION_MISMATCH"):
        journal.require_recovery(replace(reserved, **{field: "private-mismatched-input"}))
    assert journal.inspect() == reserved


def test_journal_failed_recovery_append_preserves_reservation(provisioning_journal: Any, monkeypatch: Any) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, journal, plan, context = provisioning_journal
    reserved = journal.reserve(plan, **context)
    record = s.audit.record

    def fail_after_append(event: Any) -> Any:
        record(event)
        raise RuntimeError("private-remote-error")

    with monkeypatch.context() as patch:
        patch.setattr(s.audit, "record", fail_after_append)
        with pytest.raises(ProvisioningRefused, match="PROVISIONING_JOURNAL_UNAVAILABLE"):
            journal.require_recovery(reserved)
    assert journal.inspect() == reserved
    assert journal.require_recovery(reserved).stage == "recovery-required"
    assert s.audit.verify_chain().ok


def test_journal_tampered_readback_fails_closed(provisioning_journal: Any) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, journal, plan, context = provisioning_journal
    reserved = journal.reserve(plan, **context)
    # Offline corruption injection only: never a supported lifecycle operation.
    s.engine.execute("UPDATE durable_audit_events SET actor = 'forged-actor' WHERE event_id = ?",
                     (reserved.event_id,))
    for operation in (journal.inspect, lambda: journal.reserve(plan, **context),
                      lambda: journal.require_recovery(reserved)):
        with pytest.raises(ProvisioningRefused, match="PROVISIONING_JOURNAL_UNAVAILABLE"):
            operation()


def test_journal_refuses_memory_or_different_engine_audit(invitations: Any) -> None:
    from shared.audit.events import InMemoryAuditLog
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningJournal, ProvisioningRefused
    s = invitations
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_JOURNAL_PERSISTENCE_REQUIRED"):
        ProvisioningJournal(engine=s.engine, audit_log=InMemoryAuditLog())
    engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    try:
        with pytest.raises(ProvisioningRefused, match="PROVISIONING_JOURNAL_PERSISTENCE_REQUIRED"):
            ProvisioningJournal(engine=engine, audit_log=s.audit)
    finally:
        engine.close()


@pytest.fixture
def web_lifecycle(
    acceptance: Any, foreground_plan_input: Any, consumed_dev_admission: Any, monkeypatch: Any,
) -> Any:
    """Offline memory BFF adapter, real PG/router/session boundary, NOT Next.

    No request actor or permission override. The actual Next forwarding tests
    remain the declared Vitest selection; this adapter is not live evidence.
    """
    from datetime import datetime, timedelta
    from uuid import UUID, uuid4
    from modules.opsboard.auth import Credentials
    from shared.identity.credential_service import CredentialService
    from delivery_toolchain.e2e.check_live_e2e_gate import HttpResponse
    import delivery_toolchain.release.provision_dev_smoke as module

    s = acceptance
    monkeypatch.setattr(module, "PRESERVED_ACCOUNT_ID", s.admin)
    monkeypatch.setattr(module, "TENANT_ID", TENANT)
    admin_password = "Original-Admin-Credential-7632"
    s.engine.execute("UPDATE identity.accounts SET username = 'ajoe734' WHERE account_id = ?", (s.admin,))
    s.engine.execute("UPDATE identity.password_credentials SET phc_hash = ? WHERE account_id = ?",
                     (CredentialService().hash_password(admin_password), s.admin))
    plan, _ = foreground_plan_input
    plan["actor_account_id"], plan["tenant_id"] = s.admin, TENANT
    plan["release_sha"] = consumed_dev_admission["release_sha"]
    plan["manifest_digest"] = consumed_dev_admission["manifest_digest"]
    admission = module.ConsumedDevAdmissionObserver(**{
        key: value for key, value in consumed_dev_admission.items()
        if key not in {"release_sha", "manifest_digest", "now"}
    })
    now = datetime.fromisoformat(s.engine.query_one("SELECT clock_timestamp() AS now")["now"])
    plan["expires_at"] = (now + timedelta(minutes=30)).isoformat()
    journal = module.ProvisioningJournal(engine=s.engine, audit_log=s.audit)

    class MemoryBff:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []
            self.sessions: dict[str, dict[str, str]] = {}
            self.fault = ""
            self.fault_after = ""
            # Offline serving metadata only, not admission/deployment evidence.
            self.release_identity = {
                "release_sha": plan["release_sha"], "web_release_sha": plan["release_sha"],
                "manifest_digest": plan["manifest_digest"], "web_manifest_digest": plan["manifest_digest"],
                "release_profile": "dev-admin", "web_release_profile": "dev-admin",
                "release_profile_valid": True,
            }
            self.release_change_at = 0
            self.release_reads = 0

        def request(self, method: str, path: str, **kwargs: Any) -> Any:
            self.calls.append((method, path))
            headers = kwargs["headers"]
            assert kwargs["authenticated"] is False and kwargs["follow_redirects"] is False
            assert set(headers) <= {"accept", "origin", "cookie"}
            assert headers["origin"] == "https://web.example.invalid"
            body = kwargs.get("body")
            cookie = headers.get("cookie", "").removeprefix("__Host-oday_web_session=")
            bearer = self.sessions.get(cookie, {})
            if self.fault == path:
                raise RuntimeError("private-password-token-never-expose")
            if path == "/login":
                row = s.engine.query_one("SELECT a.account_id::text AS id, p.phc_hash FROM identity.accounts a "
                                         "JOIN identity.password_credentials p ON p.account_id = a.account_id "
                                         "WHERE a.username = ?", (body["username"],))
                if row is None or not CredentialService().verify_password(row["phc_hash"], body["password"]):
                    return HttpResponse(401, {"error": {"code": "AUTH_INVALID_CREDENTIALS"}})
                cookie = uuid4().hex
                self.sessions[cookie] = _sign_in(s, row["id"])
                return HttpResponse(200, {"ok": True, "subject": body["username"]},
                                    cookies={"__Host-oday_web_session": cookie})
            outcome = s.boundary.authenticate(Credentials.from_headers(bearer))
            if path == "/api/v1/platform/release-identity":
                assert cookie and outcome.authenticated and method == "GET"
                self.release_reads += 1
                payload = dict(self.release_identity)
                if self.release_reads == self.release_change_at:
                    payload["web_release_sha"] = "0" * 40
                return HttpResponse(200, payload)
            if path in {"/auth/session", "/api/v1/auth/principal", "/auth/logout"}:
                if not outcome.authenticated:
                    return HttpResponse(401, {"error": {"code": "WEB_SESSION_REQUIRED"}})
                principal = outcome.principal
                if path == "/auth/logout":
                    s.sessions.revoke_session(UUID(principal.attributes["sid"]), "logout")
                    return HttpResponse(200, {"ok": True})
                if path == "/auth/session":
                    row = s.engine.query_one("SELECT username FROM identity.accounts WHERE account_id = ?",
                                             (principal.subject_id,))
                    return HttpResponse(200, {"subject": row["username"]})
                return HttpResponse(200, {"account_id": principal.subject_id,
                                         "tenant_id": principal.tenant_id,
                                         "roles": sorted(r.value for r in principal.roles)})
            if path == "/auth/invitations":
                assert not cookie and not bearer
                response = s.accept_client.post(ACCEPT, json=body)
            else:
                response = s.client.request(method, path, headers=bearer, json=body)
            if self.fault_after == path:
                raise RuntimeError("private-lost-reply-after-commit")
            return HttpResponse(response.status_code, response.json())

    web = MemoryBff()
    executor = module.WebInvitationExecutor(
        web=web, web_origin="https://web.example.invalid", journal=journal, admission=admission,
    )
    return s, web, executor, journal, plan, dict(admin_password=admin_password, new_password=PASSWORD,
        release_sha=plan["release_sha"], manifest_digest=plan["manifest_digest"])


def test_web_executor_actual_router_lifecycle_provenance_and_session_cleanup(web_lifecycle: Any, monkeypatch: Any) -> None:
    import subprocess
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, executor, journal, plan, args = web_lifecycle
    before = _snapshot(s)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("lifecycle launched a process"))
    receipt = executor.execute(plan, **args)
    assert receipt["stage"] == "web-lifecycle-verified"
    assert receipt["serving_release_observed"] is True and web.release_reads == 3
    assert receipt["issuer_account_id"] == s.admin
    assert receipt["account_id"] != s.admin and receipt["tenant_id"] == TENANT
    assert not receipt["credential_binding_verified"] and not receipt["deployment_success"]
    assert not receipt["live_gate_passed"] and not receipt["execution_authorized"]
    assert journal.inspect().stage == "reserved"
    after = _snapshot(s)
    assert {k: v for k, v in before.items() if k != "sessions"} == {k: v for k, v in after.items() if k != "sessions"}
    for existing in before["sessions"]:
        assert existing in after["sessions"]
    assert _q(s, "SELECT count(*) FROM identity.sessions WHERE revoked_at IS NOT NULL") == [(2,)]
    output = json.dumps(receipt) + json.dumps([e.metadata for e in s.audit.list_events()])
    for secret in (PASSWORD, args["admin_password"], plan["email"]):
        assert secret not in output
    assert web.calls.count(("POST", "/auth/invitations")) == 1
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_LIFECYCLE_REFUSED"):
        executor.execute(plan, **args)
    assert web.calls.count(("POST", PATH)) == 1
    assert web.calls.count(("POST", "/auth/invitations")) == 1
    assert s.audit.verify_chain().ok


@pytest.mark.parametrize("fault", [PATH, "/auth/invitations", "/api/v1/operator/users/audit-trail", "/auth/logout"])
def test_web_executor_uncertainty_quarantines_no_retry_or_reset(web_lifecycle: Any, fault: str) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, executor, journal, plan, args = web_lifecycle
    before = _snapshot(s)
    web.fault = fault
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_LIFECYCLE_RECOVERY_REQUIRED") as error:
        executor.execute(plan, **args)
    assert "private-password" not in str(error.value) and error.value.__cause__ is None
    assert journal.inspect().stage == "recovery-required"
    assert all("password" not in path and "revoke" not in path for _, path in web.calls)
    assert _snapshot(s)["password_credentials"] == before["password_credentials"]
    mutations = [c for c in web.calls if c[0] == "POST" and c[1] in {PATH, "/auth/invitations"}]
    web.fault = ""
    with pytest.raises(ProvisioningRefused):
        executor.execute(plan, **args)
    assert [c for c in web.calls if c[0] == "POST" and c[1] in {PATH, "/auth/invitations"}] == mutations
    count = 2 if fault in {"/api/v1/operator/users/audit-trail", "/auth/logout"} else 1
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(count,)]


@pytest.mark.parametrize("fault", ["/login", "/auth/session", "/api/v1/auth/principal", "/api/v1/operator/users",
                                   "/api/v1/platform/release-identity"])
def test_web_executor_pre_reservation_failure_never_issues(web_lifecycle: Any, fault: str) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, executor, journal, plan, args = web_lifecycle
    web.fault = fault
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_LIFECYCLE_REFUSED"):
        executor.execute(plan, **args)
    assert journal.inspect() is None
    assert ("POST", PATH) not in web.calls and ("POST", "/auth/invitations") not in web.calls
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]


@pytest.mark.parametrize("field", ["release_sha", "web_release_sha", "manifest_digest", "web_manifest_digest",
                                   "release_profile", "web_release_profile", "release_profile_valid"])
@pytest.mark.parametrize("malformed", ["missing", "wrong", "type"])
def test_web_executor_serving_tuple_mismatch_precedes_reservation(
    web_lifecycle: Any, field: str, malformed: str,
) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, executor, journal, plan, args = web_lifecycle
    before = _snapshot(s)
    if malformed == "missing":
        del web.release_identity[field]
    else:
        web.release_identity[field] = (1 if field == "release_profile_valid" else ["private-secret-input"]) \
            if malformed == "type" else (False if field == "release_profile_valid" else "wrong")
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_LIFECYCLE_REFUSED") as error:
        executor.execute(plan, **args)
    assert error.value.__cause__ is None and "private-secret" not in str(error.value)
    assert journal.inspect() is None
    assert ("POST", PATH) not in web.calls and ("POST", "/auth/invitations") not in web.calls
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]
    after = _snapshot(s)
    assert {k: v for k, v in before.items() if k != "sessions"} == {k: v for k, v in after.items() if k != "sessions"}
    assert all(existing in after["sessions"] for existing in before["sessions"])
    assert _q(s, "SELECT count(*) FROM identity.sessions WHERE revoked_at IS NOT NULL") == [(1,)]


@pytest.mark.parametrize("changed_at,accounts", [(2, 1), (3, 2)])
def test_web_executor_mid_lifecycle_release_change_quarantines_without_retry(
    web_lifecycle: Any, changed_at: int, accounts: int,
) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, executor, journal, plan, args = web_lifecycle
    web.release_change_at = changed_at
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_LIFECYCLE_RECOVERY_REQUIRED"):
        executor.execute(plan, **args)
    assert journal.inspect().stage == "recovery-required"
    assert web.calls.count(("POST", PATH)) == 1
    assert web.calls.count(("POST", "/auth/invitations")) == (0 if changed_at == 2 else 1)
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(accounts,)]
    assert _q(s, "SELECT count(*) FROM identity.sessions WHERE revoked_at IS NOT NULL") == [(accounts,)]
    mutations = [c for c in web.calls if c[0] == "POST" and c[1] in {PATH, "/auth/invitations"}]
    web.release_change_at = 0
    with pytest.raises(ProvisioningRefused):
        executor.execute(plan, **args)
    assert [c for c in web.calls if c[0] == "POST" and c[1] in {PATH, "/auth/invitations"}] == mutations


@pytest.mark.parametrize("path,count", [(PATH, 1), ("/auth/invitations", 2)])
def test_web_executor_lost_reply_after_durable_commit_never_retries(web_lifecycle: Any, path: str, count: int) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, executor, journal, plan, args = web_lifecycle
    web.fault_after = path
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_LIFECYCLE_RECOVERY_REQUIRED"):
        executor.execute(plan, **args)
    assert journal.inspect().stage == "recovery-required"
    assert _q(s, "SELECT count(*) FROM identity.invitations") == [(1,)]
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(count,)]
    web.fault_after = ""
    with pytest.raises(ProvisioningRefused):
        executor.execute(plan, **args)
    assert web.calls.count(("POST", path)) == 1
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(count,)]


@pytest.fixture
def consumed_dev_admission(tmp_path: Any) -> Any:
    """Offline canonical admission + real durable lease store; no cloud calls."""
    from datetime import UTC, datetime
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from delivery_toolchain.release.check_runtime_admission import admit_release
    from delivery_toolchain.release.release_lease import LeaseStateStore, build_lease
    from delivery_toolchain.release.release_manifest import compute_manifest_digest
    from tests.release.test_release_profile import base_manifest, DEV_ADMIN, SHA, TASK_ID
    from tests.release.test_runtime_admission import build_registry

    manifest = base_manifest(release_profile=DEV_ADMIN)
    manifest["components"] = {
        name: {"image": f"ghcr.io/example/{name}@sha256:" + str(i) * 64}
        for i, name in enumerate(("api", "web", "worker", "scheduler"), 1)
    }
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    registry = build_registry(candidate_sha=SHA, manifest_digest=manifest["manifest_digest"])
    key = Ed25519PrivateKey.generate()
    store = LeaseStateStore(tmp_path / "supervisor-lease-state")
    lease = build_lease(task_id=TASK_ID, release_id=manifest["release_id"], candidate_sha=SHA,
                        manifest_digest=manifest["manifest_digest"], target_environment="dev",
                        private_key=key)
    store.record_issued(lease)
    images = {name: value["image"] for name, value in manifest["components"].items()}
    consumer = "offline-foreground-rollout"
    ok, errors, _ = admit_release(
        registry, lease, release_sha=SHA, environment="dev", task_id=TASK_ID,
        public_key=key.public_key(), state_store=store, manifest=manifest,
        manifest_digest=manifest["manifest_digest"], component_images=images, consumed_by=consumer,
    )
    assert ok, errors
    return dict(manifest=manifest, registry=registry, lease=lease, public_key=key.public_key(),
                state_store=store, release_sha=SHA, manifest_digest=manifest["manifest_digest"],
                task_id=TASK_ID, consumed_by=consumer, component_images=images, now=datetime.now(UTC))


def test_consumed_dev_admission_rechecks_canonical_predicates_read_only(
    consumed_dev_admission: Any, monkeypatch: Any,
) -> None:
    import subprocess
    from delivery_toolchain.release.provision_dev_smoke import verify_consumed_dev_admission
    args = consumed_dev_admission
    store = args["state_store"]
    before = store.get(args["lease"]["lease_id"])
    monkeypatch.setattr(store, "consume", lambda *a, **k: pytest.fail("admission consumed again"))
    monkeypatch.setattr(store, "record_issued", lambda *a, **k: pytest.fail("minted a lease"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("admission launched process"))
    receipt = verify_consumed_dev_admission(**args)
    assert receipt["stage"] == "consumed-dev-admission-observed"
    assert receipt["execution_authorized"] is False and receipt["deployment_success"] is False
    assert store.get(args["lease"]["lease_id"]) == before
    for value in (args["lease"]["nonce"], args["lease"]["signature"]["value"]):
        assert value not in json.dumps(receipt)


def test_admission_observer_snapshots_documents_but_not_store(consumed_dev_admission: Any) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ConsumedDevAdmissionObserver
    args = consumed_dev_admission
    observer = ConsumedDevAdmissionObserver(**{
        key: value for key, value in args.items() if key not in {"release_sha", "manifest_digest", "now"}
    })
    args["manifest"]["created_by_workflow"] = "changed-after-pinning"
    args["registry"]["release"]["decision"] = "NO-GO"
    args["lease"]["nonce"] = "changed-after-pinning"
    args["component_images"].clear()
    observer.observe(release_sha=args["release_sha"], manifest_digest=args["manifest_digest"], now=args["now"])
    assert "nonce" not in repr(observer) and "signature" not in repr(observer)


@pytest.mark.parametrize("fault", [
    "sha", "digest", "task", "consumer", "wrong-key", "bad-signature", "lease-environment",
    "lease-action", "lease-extra", "manifest-tamper", "manifest-profile", "sources", "image",
    "missing-component", "registry-sha", "registry-digest", "registry-red", "registry-staging",
    "missing-state", "issued", "revoked", "state-lease", "state-consumer", "state-naive-time",
    "state-future-time", "state-before-issue", "state-revocation", "expired", "naive-now", "unavailable",
])
def test_consumed_dev_admission_uncertainty_never_authorizes(
    consumed_dev_admission: Any, monkeypatch: Any, fault: str,
) -> None:
    from datetime import timedelta
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused, verify_consumed_dev_admission
    args = consumed_dev_admission
    if fault in {"sha", "digest", "task", "consumer"}:
        field = {"sha": "release_sha", "digest": "manifest_digest", "task": "task_id", "consumer": "consumed_by"}[fault]
        args[field] = {"sha": "f" * 40, "digest": "sha256:" + "f" * 64,
                       "task": "OTHER-TASK", "consumer": "other-consumer"}[fault]
    elif fault == "wrong-key":
        args["public_key"] = Ed25519PrivateKey.generate().public_key()
    elif fault.startswith("lease-") or fault == "bad-signature":
        field = {"lease-environment": "target_environment", "lease-action": "allowed_action",
                 "lease-extra": "admitted", "bad-signature": "nonce"}[fault]
        args["lease"][field] = "private-input-never-echo"
    elif fault.startswith("manifest-") or fault == "sources":
        field = {"manifest-tamper": "created_by_workflow", "manifest-profile": "release_profile",
                 "sources": "external_sources_expected_enabled"}[fault]
        args["manifest"][field] = "private-input-never-echo"
    elif fault == "image":
        args["component_images"]["api"] = "ghcr.io/example/api@sha256:" + "f" * 64
    elif fault == "missing-component":
        args["component_images"].pop("worker")
    elif fault.startswith("registry-"):
        field = {"registry-sha": "candidate_sha", "registry-digest": "manifest_digest",
                 "registry-red": "decision", "registry-staging": "admission_target"}[fault]
        args["registry"]["release"][field] = "private-input-never-echo"
    elif fault == "expired":
        args["now"] += timedelta(hours=2)
    elif fault == "naive-now":
        args["now"] = args["now"].replace(tzinfo=None)
    else:
        store = args["state_store"]
        original = store.get(args["lease"]["lease_id"])
        if fault in {"issued", "revoked"}:
            original["state"] = fault
        elif fault == "state-lease":
            original["lease"] = {**original["lease"], "candidate_sha": "f" * 40}
        elif fault == "state-consumer":
            original["consumed_by"] = "other-consumer"
        elif fault == "state-revocation":
            original["revoked_at"] = args["now"].isoformat()
        elif fault.startswith("state-"):
            at = args["now"] + timedelta(hours=1) if fault == "state-future-time" else args["now"] - timedelta(hours=1)
            original["consumed_at"] = at.replace(tzinfo=None).isoformat() if fault == "state-naive-time" else at.isoformat()
        def get(*a: Any) -> Any:
            if fault == "unavailable":
                raise RuntimeError("private-input-never-echo")
            return None if fault == "missing-state" else original
        monkeypatch.setattr(store, "get", get)
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_DEV_ADMISSION_UNVERIFIED") as error:
        verify_consumed_dev_admission(**args)
    assert error.value.__cause__ is None and "private-input" not in str(error.value)


@pytest.mark.parametrize("bad", [None, {}, {"admitted": True}, {"stage": "consumed-dev-admission-observed"}])
def test_lifecycle_requires_observer_not_receipt(web_lifecycle: Any, bad: Any) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused, WebInvitationExecutor
    s, web, _, journal, _, _ = web_lifecycle
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_DEV_ADMISSION_UNVERIFIED"):
        WebInvitationExecutor(web=web, web_origin="https://web.example.invalid", journal=journal, admission=bad)
    assert not web.calls and journal.inspect() is None
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]


@pytest.mark.parametrize("changed_at", range(1, 7))
def test_lifecycle_rechecks_real_store_before_each_mutation_and_final_success(
    web_lifecycle: Any, consumed_dev_admission: Any, monkeypatch: Any, changed_at: int,
) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, executor, journal, plan, args = web_lifecycle
    before = _snapshot(s)
    store = consumed_dev_admission["state_store"]
    get = store.get
    reads = 0

    def unavailable_at_stage(lease_id: str) -> Any:
        nonlocal reads
        reads += 1
        if reads == changed_at:
            raise RuntimeError("private-supervisor-error")
        return get(lease_id)

    monkeypatch.setattr(store, "get", unavailable_at_stage)
    with pytest.raises(ProvisioningRefused) as error:
        executor.execute(plan, **args)
    assert "private" not in str(error.value) and error.value.__cause__ is None
    assert reads == changed_at
    assert web.calls.count(("POST", PATH)) == int(changed_at >= 4)
    assert web.calls.count(("POST", "/auth/invitations")) == int(changed_at >= 5)
    assert web.calls.count(("POST", "/login")) == (0 if changed_at == 1 else 2 if changed_at == 6 else 1)
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(2 if changed_at >= 5 else 1,)]
    assert _snapshot(s)["password_credentials"] == before["password_credentials"]
    if changed_at <= 2:
        assert journal.inspect() is None
    else:
        assert journal.inspect().stage == "recovery-required"
        mutations = [c for c in web.calls if c[0] == "POST" and c[1] in {PATH, "/auth/invitations"}]
        monkeypatch.setattr(store, "get", get)
        with pytest.raises(ProvisioningRefused):
            executor.execute(plan, **args)
        assert [c for c in web.calls if c[0] == "POST" and c[1] in {PATH, "/auth/invitations"}] == mutations
    assert all(existing in _snapshot(s)["sessions"] for existing in before["sessions"])
    assert s.audit.verify_chain().ok


@pytest.mark.parametrize("fault", ["issued", "expired", "tuple", "plan-tuple"])
def test_lifecycle_actual_admission_refusal_precedes_login(
    web_lifecycle: Any, consumed_dev_admission: Any, monkeypatch: Any, fault: str,
) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, executor, journal, plan, args = web_lifecycle
    if fault == "issued":
        store = consumed_dev_admission["state_store"]
        record = store.get(consumed_dev_admission["lease"]["lease_id"])
        record["state"] = "issued"
        monkeypatch.setattr(store, "get", lambda *a: record)
    elif fault == "expired":
        from datetime import timedelta
        now = journal._now()
        monkeypatch.setattr(journal, "_now", lambda: now + timedelta(days=2))
    elif fault == "tuple":
        plan["release_sha"] = args["release_sha"] = "f" * 40
    else:
        plan["manifest_digest"] = "sha256:" + "f" * 64
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_LIFECYCLE_REFUSED"):
        executor.execute(plan, **args)
    assert not web.calls and journal.inspect() is None
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]


@pytest.fixture
def encrypted_binding(web_lifecycle: Any) -> Any:
    """Actual sealed-box encryption/decryption + mocked GitHub, never a live token."""
    import base64
    import httpx
    from nacl.public import PrivateKey
    from delivery_toolchain.release.provision_dev_smoke import DevCredentialBundleExecutor, GitHubDevSecretStore

    s, web, lifecycle, journal, plan, args = web_lifecycle
    private_key = PrivateKey.generate()

    class OfflineGitHub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []
            self.uploads: list[dict[str, Any]] = []
            self.fault = ""

        def request(self, request: Any) -> Any:
            path = request.url.path
            self.calls.append((request.method, path))
            assert request.url.scheme == "https" and request.url.host == "api.github.com"
            assert request.headers["authorization"] == "Bearer offline-token"
            if request.method == "PUT":
                # Durable binding intent MUST already exist before a remote PUT.
                events = s.audit.list_events(correlation_id=binding._CORRELATION)
                assert len(events) == 1 and events[0].metadata["stage"] == "binding-intent"
                value = json.loads(request.content)
                assert set(value) == {"encrypted_value", "key_id"}
                for secret in (PASSWORD, args["admin_password"], plan["email"], plan["username"]):
                    assert secret not in request.content.decode()
                if self.fault == "refused":
                    return httpx.Response(403, json={"message": "private-error-payload"})
                self.uploads.append(value)
                if self.fault == "lost-reply":
                    raise httpx.ReadTimeout("private-error-password", request=request)
                return httpx.Response(204 if self.fault == "replaced" else 201)
            if path == "/repos/alfloop-dev/odayplus":
                return httpx.Response(200, json={"id": 123, "full_name":
                    "foreign/repository" if self.fault == "repository" else "alfloop-dev/odayplus"})
            if path.endswith("/public-key"):
                return httpx.Response(200, json={"key_id": "offline-key-id", "key":
                    "bad-private-key" if self.fault == "key" else
                    base64.b64encode(bytes(private_key.public_key)).decode()})
            assert path == "/repositories/123/environments/dev/secrets/ODP_DEV_ADMIN_CREDENTIAL_BUNDLE"
            return httpx.Response(200 if self.fault == "exists" else 302 if self.fault == "redirect" else 404)

    remote = OfflineGitHub()
    store = GitHubDevSecretStore(token="offline-token", transport=httpx.MockTransport(remote.request))
    binding = DevCredentialBundleExecutor(lifecycle=lifecycle, store=store)
    try:
        yield s, web, journal, binding, remote, private_key, plan, args
    finally:
        store.close()


def test_binding_encrypts_one_matched_pair_without_bootstrap_fallback(encrypted_binding: Any, monkeypatch: Any) -> None:
    import base64
    import subprocess
    from nacl.public import SealedBox
    from delivery_toolchain.release.provision_dev_smoke import DevCredentialBundleExecutor, ProvisioningRefused
    s, web, journal, binding, remote, private_key, plan, args = encrypted_binding
    before = _snapshot(s)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("binding launched a process"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("binding launched a process"))
    assert binding.inspect() is None
    receipt = binding.execute(plan, **args)
    assert len(remote.uploads) == 1
    bundle = json.loads(SealedBox(private_key).decrypt(base64.b64decode(remote.uploads[0]["encrypted_value"])))
    assert bundle == {
        "schema_version": 1, "authorization_id": receipt["authorization_id"],
        "execution_id": plan["execution_id"], "repository": "alfloop-dev/odayplus",
        "environment": "dev", "tenant_id": TENANT, "account_id": receipt["account_id"],
        "username": plan["username"], "password": PASSWORD,
    }
    from delivery_toolchain.release.provision_dev_smoke import read_dev_credential_bundle
    consumed = read_dev_credential_bundle(json.dumps(bundle), environment="dev", release_profile="dev-admin")
    assert consumed.username == plan["username"] and consumed.password == args["new_password"]
    assert consumed.account_id == receipt["account_id"] and consumed.tenant_id == TENANT
    assert PASSWORD not in repr(consumed)
    assert receipt["binding_write_acknowledged"] is True
    assert receipt["credential_binding_verified"] is False
    assert not receipt["live_gate_passed"] and not receipt["deployment_success"]
    assert binding.inspect()["stage"] == "binding-acknowledged"
    from delivery_toolchain.release.provision_dev_smoke import credential_bundle_acknowledged
    projected = s.service.get_audit_trail(tenant_id=TENANT)
    assert credential_bundle_acknowledged(consumed, projected)
    assert not credential_bundle_acknowledged(consumed, s.service.get_audit_trail(tenant_id=OTHER_TENANT))
    after = _snapshot(s)
    for key in before.keys() - {"sessions"}:
        assert after[key] == before[key]
    for session in before["sessions"]:
        assert session in after["sessions"]
    output = json.dumps(receipt) + json.dumps([e.metadata for e in s.audit.list_events()])
    for secret in (PASSWORD, args["admin_password"], plan["email"], "offline-token", remote.uploads[0]["encrypted_value"]):
        assert secret not in output
    # Restart sees the exact same durable result, never repeats lifecycle or PUT.
    restarted = DevCredentialBundleExecutor(lifecycle=binding._lifecycle, store=binding._store)
    calls = list(web.calls), list(remote.calls)
    with pytest.raises(ProvisioningRefused):
        restarted.execute(plan, **args)
    assert (web.calls, remote.calls) == calls
    assert journal.inspect().stage == "reserved" and s.audit.verify_chain().ok


@pytest.mark.parametrize("fault", ["repository", "key", "exists", "redirect"])
def test_binding_preflight_failure_never_logs_in_or_creates(encrypted_binding: Any, fault: str) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, journal, binding, remote, _, plan, args = encrypted_binding
    remote.fault = fault
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_BINDING_REFUSED"):
        binding.execute(plan, **args)
    assert not web.calls and not remote.uploads
    assert journal.inspect() is None and binding.inspect() is None
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]


@pytest.mark.parametrize("fault", ["refused", "lost-reply", "replaced"])
def test_binding_uncertain_or_partial_remote_result_never_retries(encrypted_binding: Any, fault: str) -> None:
    from delivery_toolchain.release.provision_dev_smoke import DevCredentialBundleExecutor, ProvisioningRefused
    s, web, journal, binding, remote, _, plan, args = encrypted_binding
    before = _snapshot(s)
    remote.fault = fault
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_BINDING_RECOVERY_REQUIRED") as error:
        binding.execute(plan, **args)
    assert error.value.__cause__ is None and "private" not in str(error.value)
    assert binding.inspect()["stage"] == journal.inspect().stage == "recovery-required"
    assert len([c for c in remote.calls if c[0] == "PUT"]) == 1
    assert len(remote.uploads) == (0 if fault == "refused" else 1)
    if remote.uploads:
        import base64
        from nacl.public import SealedBox
        from delivery_toolchain.release.provision_dev_smoke import read_dev_credential_bundle, credential_bundle_acknowledged
        # Even a remote-committed secret from a lost reply must NOT activate a
        # quarantined root when a subsequent normal workflow sees the bundle.
        bundle = json.loads(SealedBox(encrypted_binding[5]).decrypt(
            base64.b64decode(remote.uploads[0]["encrypted_value"])))
        consumed = read_dev_credential_bundle(json.dumps(bundle), environment="dev", release_profile="dev-admin")
        assert not credential_bundle_acknowledged(consumed, s.service.get_audit_trail(tenant_id=TENANT))
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(2,)]
    assert _snapshot(s)["password_credentials"] == before["password_credentials"]
    restarted = DevCredentialBundleExecutor(lifecycle=binding._lifecycle, store=binding._store)
    calls = list(web.calls), list(remote.calls)
    remote.fault = ""
    with pytest.raises(ProvisioningRefused):
        restarted.execute(plan, **args)
    assert (web.calls, remote.calls) == calls
    assert not any(method == "DELETE" for method, _ in remote.calls)


@pytest.mark.parametrize("failure", ["binding-intent", "binding-acknowledged", "all"])
def test_binding_audit_commit_failure_never_claims_ack_or_retries(
    encrypted_binding: Any, monkeypatch: Any, failure: str,
) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, journal, binding, remote, _, plan, args = encrypted_binding
    original = s.audit.record

    def append_then_fail(event: Any) -> Any:
        written = original(event)
        if event.event_type == binding._TYPE and (failure == "all" or event.metadata["stage"] == failure):
            raise RuntimeError("private-password-write-failure")
        return written

    monkeypatch.setattr(s.audit, "record", append_then_fail)
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_BINDING_RECOVERY_REQUIRED"):
        binding.execute(plan, **args)
    state = binding.inspect()
    assert state is None if failure in {"binding-intent", "all"} else state["stage"] == "recovery-required"
    assert journal.inspect().stage == "recovery-required"
    assert len(remote.uploads) == (1 if failure == "binding-acknowledged" else 0)
    monkeypatch.setattr(s.audit, "record", original)
    calls = list(web.calls)
    with pytest.raises(ProvisioningRefused):
        binding.execute(plan, **args)
    # If no intent survived, root collision/reservation still prevents creation/PUT.
    assert web.calls.count(("POST", PATH)) == calls.count(("POST", PATH))
    assert len(remote.uploads) == (1 if failure == "binding-acknowledged" else 0)
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(2,)]


@pytest.mark.parametrize("changed_at", [1, 8])
def test_binding_admission_loss_precedes_remote_calls_or_put(
    encrypted_binding: Any, consumed_dev_admission: Any, monkeypatch: Any, changed_at: int,
) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, journal, binding, remote, _, plan, args = encrypted_binding
    store = consumed_dev_admission["state_store"]
    get = store.get
    reads = 0

    def unavailable_at_stage(lease_id: str) -> Any:
        nonlocal reads
        reads += 1
        if reads == changed_at:
            raise RuntimeError("private-admission-error")
        return get(lease_id)

    monkeypatch.setattr(store, "get", unavailable_at_stage)
    with pytest.raises(ProvisioningRefused):
        binding.execute(plan, **args)
    assert reads == changed_at and not remote.uploads
    assert not any(method == "PUT" for method, _ in remote.calls)
    if changed_at == 1:
        assert not web.calls and not remote.calls
        assert journal.inspect() is None and binding.inspect() is None
    else:
        assert binding.inspect()["stage"] == journal.inspect().stage == "recovery-required"
        assert _q(s, "SELECT count(*) FROM identity.accounts") == [(2,)]
        calls = list(web.calls), list(remote.calls)
        monkeypatch.setattr(store, "get", get)
        with pytest.raises(ProvisioningRefused):
            binding.execute(plan, **args)
        assert (web.calls, remote.calls) == calls


def _foreground_gate_template(binding: Any, args: Any) -> Any:
    from delivery_toolchain.e2e.check_live_e2e_gate import GateConfig
    return GateConfig(
        api_url="https://offline-api.example", web_url=binding._lifecycle._origin,
        expected_sha=args["release_sha"], expected_manifest_digest=args["manifest_digest"],
        expected_deployment="dev", release_profile="dev-admin", bearer_token="offline-service-token",
        api_transport_token="offline-transport-token", operator_role="ops-lead",
        external_provider_mode="disabled", required_provider_ids=(), dev_admin_denied_role="cs-lead",
        dev_admin_username="ajoe734", dev_admin_password="stale-standing-password",
        dev_admin_initial_password="stale-initial-password", bootstrap_admin_username="ajoe734",
        bootstrap_admin_password="stale-bootstrap-password",
    )


@pytest.mark.parametrize("field,value", [
    ("expected_sha", "f" * 40), ("expected_manifest_digest", "sha256:" + "f" * 64),
    ("expected_deployment", "production"), ("release_profile", "full"),
    ("allow_http", True), ("external_provider_mode", "live"),
    ("web_url", "https://foreign-web.example"), ("api_url", "http://offline-api.example"),
    ("api_transport_token", ""), ("bearer_token", ""), ("dev_admin_denied_role", ""),
])
def test_foreground_gate_invalid_inputs_refuse_before_lifecycle_or_put(
    encrypted_binding: Any, field: str, value: Any,
) -> None:
    from dataclasses import replace
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, journal, binding, remote, _, plan, args = encrypted_binding
    config = replace(_foreground_gate_template(binding, args), **{field: value})
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_GATE_CONFIG_INVALID"):
        binding.execute_and_check_gate(plan, gate_config=config, worker_job="offline-worker",
                                      gcp_region="asia-east1", gcp_project="offline-project", **args)
    assert not web.calls and not remote.calls
    assert journal.inspect() is None and binding.inspect() is None
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]


def test_foreground_gate_receives_actual_matched_pair_after_durable_ack(
    encrypted_binding: Any, monkeypatch: Any,
) -> None:
    """Composition spy only; the stub verdict is NOT live/independent gate evidence."""
    import base64
    import os
    import subprocess
    from nacl.public import SealedBox
    from delivery_toolchain.e2e import check_live_e2e_gate as gate
    s, web, journal, binding, remote, private_key, plan, args = encrypted_binding
    before = dict(os.environ)
    seen = []

    def offline_evaluator(config: Any, **kwargs: Any) -> Any:
        assert binding.inspect()["stage"] == "binding-acknowledged"
        bundle = json.loads(SealedBox(private_key).decrypt(base64.b64decode(remote.uploads[0]["encrypted_value"])))
        assert config.dev_admin_username == bundle["username"] == plan["username"]
        assert config.dev_admin_password == bundle["password"] == args["new_password"]
        assert config.dev_admin_bundle_account_id == bundle["account_id"]
        assert config.dev_admin_bundle_tenant_id == bundle["tenant_id"]
        assert config.dev_admin_bundle_execution_id == plan["execution_id"]
        assert not config.dev_admin_initial_password and not config.bootstrap_admin_password
        assert not config.bootstrap_admin_username
        assert kwargs["worker_driver"]._job == "offline-worker"
        seen.append(config)
        return [gate.CheckResult(True, "offline:composition-only", "not live evidence")], {
            "ok": True, "expected_release_sha": config.expected_sha, "expected_deployment": "dev",
        }

    monkeypatch.setattr(gate, "evaluate_gate", offline_evaluator)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("composition launched a process"))
    result = binding.execute_and_check_gate(
        plan, gate_config=_foreground_gate_template(binding, args), worker_job="offline-worker",
        gcp_region="asia-east1", gcp_project="offline-project", **args,
    )
    assert len(seen) == len(remote.uploads) == 1
    assert result["provisioning"]["live_gate_passed"] is True
    assert result["provisioning"]["credential_binding_verified"] is False
    assert result["provisioning"]["deployment_success"] is False
    assert dict(os.environ) == before and journal.inspect().stage == "reserved"
    for secret in (args["new_password"], args["admin_password"], "stale-standing-password", "offline-service-token"):
        assert secret not in json.dumps(result)


def test_foreground_gate_cannot_claim_success_after_admission_loss(
    encrypted_binding: Any, consumed_dev_admission: Any, monkeypatch: Any,
) -> None:
    from delivery_toolchain.e2e import check_live_e2e_gate as gate
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    _, _, journal, binding, remote, _, plan, args = encrypted_binding
    store = consumed_dev_admission["state_store"]

    def offline_evaluator(config: Any, **kwargs: Any) -> Any:
        record = store.get(consumed_dev_admission["lease"]["lease_id"])
        record["state"] = "revoked"
        monkeypatch.setattr(store, "get", lambda *a: record)
        return [gate.CheckResult(True, "offline", "not live evidence")], {
            "ok": True, "expected_release_sha": config.expected_sha, "expected_deployment": "dev",
        }

    monkeypatch.setattr(gate, "evaluate_gate", offline_evaluator)
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_GATE_RECOVERY_REQUIRED"):
        binding.execute_and_check_gate(
            plan, gate_config=_foreground_gate_template(binding, args), worker_job="offline-worker",
            gcp_region="asia-east1", gcp_project="offline-project", **args,
        )
    assert journal.inspect().stage == "recovery-required" and len(remote.uploads) == 1


def test_foreground_actual_gate_failure_quarantines_without_retry(
    encrypted_binding: Any, monkeypatch: Any,
) -> None:
    import subprocess
    from delivery_toolchain.e2e import check_live_e2e_gate as gate
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, journal, binding, remote, _, plan, args = encrypted_binding

    class UnavailableHTTP:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def request(self, *a: Any, **k: Any) -> Any:
            return gate.HttpResponse(503, {"error": "offline unavailable"})

    # Real canonical evaluator; only its HTTP boundary is an offline unavailable
    # runtime. No evaluator override, success fixture, gate skip or cloud call.
    monkeypatch.setattr(gate, "UrllibHttpClient", UnavailableHTTP)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("failed HTTP reached worker process"))
    result = binding.execute_and_check_gate(
        plan, gate_config=_foreground_gate_template(binding, args), worker_job="offline-worker",
        gcp_region="asia-east1", gcp_project="offline-project", **args,
    )
    assert result["gate"]["ok"] is False and result["gate"]["blockers"]
    assert result["gate"]["full_acceptance"]["status"] == "NOT_ADMITTED"
    assert result["provisioning"]["live_gate_passed"] is False
    assert result["provisioning"]["deployment_success"] is False
    assert journal.inspect().stage == "recovery-required"
    calls = list(web.calls), list(remote.calls)
    with pytest.raises(ProvisioningRefused):
        binding.execute_and_check_gate(
            plan, gate_config=_foreground_gate_template(binding, args), worker_job="offline-worker",
            gcp_region="asia-east1", gcp_project="offline-project", **args,
        )
    assert (web.calls, remote.calls) == calls
    assert len(remote.uploads) == 1


@pytest.mark.parametrize("fault", ["exception", "truthy-ok", "wrong-sha", "empty-checks", "inconsistent"])
def test_foreground_gate_uncertain_result_is_not_success(
    encrypted_binding: Any, monkeypatch: Any, fault: str,
) -> None:
    from delivery_toolchain.e2e import check_live_e2e_gate as gate
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    s, web, journal, binding, remote, _, plan, args = encrypted_binding

    def uncertain(config: Any, **kwargs: Any) -> Any:
        if fault == "exception":
            raise RuntimeError(args["new_password"])
        report = {"ok": 1 if fault == "truthy-ok" else True,
                  "expected_release_sha": "f" * 40 if fault == "wrong-sha" else config.expected_sha,
                  "expected_deployment": "dev"}
        checks = [] if fault == "empty-checks" else [gate.CheckResult(fault != "inconsistent", "offline", "offline")]
        return checks, report

    monkeypatch.setattr(gate, "evaluate_gate", uncertain)
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_GATE_RECOVERY_REQUIRED") as error:
        binding.execute_and_check_gate(
            plan, gate_config=_foreground_gate_template(binding, args), worker_job="offline-worker",
            gcp_region="asia-east1", gcp_project="offline-project", **args,
        )
    assert error.value.__cause__ is None and args["new_password"] not in str(error.value)
    assert journal.inspect().stage == "recovery-required" and len(remote.uploads) == 1
