"""Incremental real-router/PG invitation proof, NOT live provisioning evidence.

The acceptance factory and full runtime composition are exercised offline.
Foreground binding and deploy/gate orchestration remain to be implemented.
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
