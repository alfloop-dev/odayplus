"""Contract tests for API Trust, Multi-Issuer Boundary, and Authorization Audit.

Task: ODP-WEB-LOCAL-AUTH-API-TRUST-001
Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §4, §5, §8, §10 (T16-T21)

Test Matrix:
- T16: API 拒絕瀏覽器自帶 x-subject-id / x-roles / x-tenant-id (production 模式下一律不採信)
- T17: 多 issuer 驗證器：local / oidc / service 三類 token 各自正確組裝 Principal
- T18: 未 link 的 OIDC (iss, sub) 一律拒絕 (401 + federated_identity_not_linked 稽核事件)
- T19: provider 未設定或驗證失敗 fail closed
- T20: RBAC allow 與 deny 都產生稽核事件 (兩種 outcome 都有事件與 correlation_id)
- T21: 撤銷傳播：高風險動作即時檢查 sid (撤銷後第一個寫入請求即 401)
"""

from __future__ import annotations

import os
import time
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.api.oday_api.security.dependencies import (
    OPERATOR_CONSOLE_RESOURCE,
    principal_from_headers,
    require_operator_permission,
    require_permission,
    reset_default_boundary,
)
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    AuthFailureReason,
    Credentials,
)
from modules.opsboard.auth.jwt import SigningKey, encode_compact_jwt
from shared.audit import InMemoryAuditLog
from shared.auth import (
    Action,
    AuthorizationEngine,
    DataClassification,
    Role,
    Scope,
)
from shared.identity import (
    Account,
    InMemoryIdentityStore,
    InMemorySessionRepository,
    RevocationReason,
    SessionConfig,
    SessionService,
)

# --- Test Helpers ---

LOCAL_SECRET = b"test-local-signing-key-secret-32b-long!"
OIDC_SECRET = b"test-oidc-signing-key-secret-32b-long!!"
SERVICE_SECRET = b"test-service-signing-key-secret-32b-long"

LOCAL_KEY = SigningKey(kid="local-k1", algorithm="HS256", secret=LOCAL_SECRET)
OIDC_KEY = SigningKey(kid="oidc-k1", algorithm="HS256", secret=OIDC_SECRET)
SERVICE_KEY = SigningKey(kid="service-k1", algorithm="HS256", secret=SERVICE_SECRET)

LOCAL_ISSUER = "urn:odp:identity:local"
OIDC_ISSUER = "https://accounts.google.com"
SERVICE_ISSUER = "urn:odp:service:cron"
AUDIENCE = "oday-api"


def make_jwt(
    key: SigningKey,
    *,
    iss: str,
    sub: str,
    aud: str = AUDIENCE,
    exp_offset: int = 3600,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": iss,
        "sub": sub,
        "aud": aud,
        "iat": now,
        "nbf": now - 10,
        "exp": now + exp_offset,
    }
    if extra_claims:
        payload.update(extra_claims)
    return encode_compact_jwt(payload, key)


@pytest.fixture
def identity_store() -> InMemoryIdentityStore:
    return InMemoryIdentityStore()


@pytest.fixture
def session_service() -> SessionService:
    repo = InMemorySessionRepository()
    config = SessionConfig()
    return SessionService(repository=repo, config=config)


@pytest.fixture
def boundary_config(
    identity_store: InMemoryIdentityStore, session_service: SessionService
) -> AuthBoundaryConfig:
    return AuthBoundaryConfig(
        audiences=frozenset([AUDIENCE]),
        local_issuer=LOCAL_ISSUER,
        local_signing_keys={"local-k1": LOCAL_KEY},
        local_audiences=frozenset([AUDIENCE]),
        oidc_issuer=OIDC_ISSUER,
        oidc_signing_keys={"oidc-k1": OIDC_KEY},
        oidc_audiences=frozenset([AUDIENCE]),
        service_issuer=SERVICE_ISSUER,
        service_signing_keys={"service-k1": SERVICE_KEY},
        service_audiences=frozenset([AUDIENCE]),
        principal_mappings={
            "service-cron-sa": {
                "roles": ["platform_admin"],
                "scope": {"tenant_id": "00000000-0000-0000-0000-000000000001"},
            }
        },
        identity_store=identity_store,
        session_service=session_service,
    )


@pytest.fixture
def boundary(boundary_config: AuthBoundaryConfig) -> AuthenticationBoundary:
    return AuthenticationBoundary(boundary_config)


@pytest.fixture(autouse=True)
def clean_boundary_state():
    reset_default_boundary()
    yield
    reset_default_boundary()


# --- T16: Production Mode Rejects Spoofable Browser Headers ---


def test_t16_production_browser_headers_rejected(boundary: AuthenticationBoundary):
    """T16: When ODP_PRODUCT_MODE=production, API never falls back to header trust."""
    headers = {
        "x-subject-id": "spoofed-user-id",
        "x-roles": "platform_admin",
        "x-tenant-id": "00000000-0000-0000-0000-000000000001",
    }

    # 1. With boundary active in production mode -> 401 NO_CREDENTIALS
    with patch.dict(os.environ, {"ODP_PRODUCT_MODE": "production"}):
        with pytest.raises(HTTPException) as exc_info:
            principal_from_headers(headers, boundary=boundary)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == AuthFailureReason.NO_CREDENTIALS.value

    # 2. Even when no boundary is configured in production mode -> 401 NO_CREDENTIALS (fail-closed)
    with patch.dict(os.environ, {"ODP_PRODUCT_MODE": "production"}):
        reset_default_boundary()
        with pytest.raises(HTTPException) as exc_info:
            principal_from_headers(headers, boundary=None)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == AuthFailureReason.NO_CREDENTIALS.value

    # 3. In non-production mode without boundary -> legacy header trust remains available for dev/unit tests
    with patch.dict(os.environ, {"ODP_PRODUCT_MODE": "development"}):
        reset_default_boundary()
        principal = principal_from_headers(headers, boundary=None)
        assert principal.subject_id == "spoofed-user-id"
        assert Role.PLATFORM_ADMIN in principal.roles


# --- T17: Multi-Issuer Verification & Principal Assembly ---


def test_t17_multi_issuer_local_token(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
    session_service: SessionService,
):
    """T17 (Local): Local JWT verifies signature and authoritative roles/scope come from IdentityStore."""
    account_id = uuid4()
    tenant_id = uuid4()
    account = Account(
        account_id=account_id,
        tenant_id=tenant_id,
        username="local_admin",
        email="local_admin@example.com",
        status="active",
    )
    identity_store.save_account(account)
    identity_store.set_account_roles(account_id, [Role.PLATFORM_ADMIN])
    custom_scope = Scope(
        tenant_id=str(tenant_id),
        brand_ids=frozenset(["brand-1"]),
        clearance=DataClassification.RESTRICTED,
    )
    identity_store.set_account_scope(account_id, custom_scope)

    session = session_service.create_session(
        account_id=account_id,
        provider="local_password",
    )

    # Token claims might try to self-assert extra roles (which must be ignored per contract §4.4)
    token = make_jwt(
        LOCAL_KEY,
        iss=LOCAL_ISSUER,
        sub=str(account_id),
        extra_claims={
            "sid": str(session.session_id),
            "roles": ["operations_manager"],  # Must be ignored!
        },
    )

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is True
    assert outcome.token_type == "local"
    principal = outcome.principal
    assert principal.subject_id == str(account_id)
    assert principal.tenant_id == str(tenant_id)
    # Roles and scope loaded from identity_store, not token claims
    assert principal.roles == frozenset([Role.PLATFORM_ADMIN])
    assert Role.OPERATIONS_MANAGER not in principal.roles
    assert principal.scope.brand_ids == frozenset(["brand-1"])
    assert principal.attributes.get("provider") == "local_password"
    assert principal.attributes.get("sid") == str(session.session_id)


def test_t17_multi_issuer_oidc_token(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
):
    """T17 (OIDC): OIDC JWT resolves account via federated identity link, roles/scope from IdentityStore."""
    account_id = uuid4()
    tenant_id = uuid4()
    account = Account(
        account_id=account_id,
        tenant_id=tenant_id,
        username="federated_user",
        email="federated@example.com",
        status="active",
    )
    identity_store.save_account(account)
    identity_store.set_account_roles(account_id, [Role.OPERATIONS_MANAGER])
    identity_store.link_federated_identity(account_id, OIDC_ISSUER, "google-sub-98765")

    token = make_jwt(
        OIDC_KEY,
        iss=OIDC_ISSUER,
        sub="google-sub-98765",
        extra_claims={"email": "federated@example.com", "email_verified": True},
    )

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is True
    assert outcome.token_type == "oidc"
    principal = outcome.principal
    assert principal.subject_id == str(account_id)
    assert principal.roles == frozenset([Role.OPERATIONS_MANAGER])
    assert principal.attributes.get("provider") == "oidc"


def test_t17_multi_issuer_service_token(boundary: AuthenticationBoundary):
    """T17 (Service): Service token maps roles and scope via ODP_AUTH_PRINCIPAL_MAP."""
    token = make_jwt(
        SERVICE_KEY,
        iss=SERVICE_ISSUER,
        sub="service-cron-sa",
    )

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is True
    assert outcome.token_type == "service"
    principal = outcome.principal
    assert principal.subject_id == "service-cron-sa"
    assert principal.roles == frozenset([Role.PLATFORM_ADMIN])
    assert principal.tenant_id == "00000000-0000-0000-0000-000000000001"


# --- T18: Unlinked OIDC Identity Fails Closed ---


def test_t18_unlinked_oidc_identity_rejected(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
):
    """T18: Unlinked OIDC (iss, sub) is rejected with 401 and federated_identity_not_linked audit event."""
    token = make_jwt(
        OIDC_KEY,
        iss=OIDC_ISSUER,
        sub="unlinked-google-user-000",
        extra_claims={"email": "unlinked@example.com"},
    )

    outcome = boundary.authenticate(Credentials(bearer_token=token, correlation_id="cid-t18"))
    assert outcome.authenticated is False
    assert outcome.reason == AuthFailureReason.FEDERATED_IDENTITY_NOT_LINKED

    # Verify audit event
    audit_events = boundary.audit_log.list_events(correlation_id="cid-t18")
    assert len(audit_events) == 1
    event = audit_events[0]
    assert event.event_type == "security.authentication"
    assert event.outcome == "failure"
    assert event.metadata.get("reason") == "federated_identity_not_linked"
    assert event.resource == "auth/oidc"


# --- T19: Fail-Closed on Incomplete Config and Invalid Credentials ---


def test_t19_fail_closed_on_incomplete_config():
    """T19: Boundary with partial config fails closed without crashing or downgrading."""
    config = AuthBoundaryConfig(
        issuer=LOCAL_ISSUER,
        audiences=frozenset([AUDIENCE]),
        # No signing keys, no JWKS URI -> unconfigured
    )
    boundary = AuthenticationBoundary(config)
    outcome = boundary.authenticate(Credentials(bearer_token="some.fake.token"))
    assert outcome.authenticated is False
    assert outcome.reason == AuthFailureReason.BOUNDARY_NOT_CONFIGURED


def test_t19_fail_closed_on_bad_signature_or_expired(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
):
    """T19: Bad signature, expired token, and unrecognized issuer fail closed."""
    # Bad signature (same kid as local key, but signed with wrong secret)
    bad_sig_key = SigningKey(
        kid="local-k1",
        algorithm="HS256",
        secret=b"wrong-signing-secret-32-chars-long!",
    )
    bad_sig_token = make_jwt(
        bad_sig_key,
        iss=LOCAL_ISSUER,
        sub=str(uuid4()),
    )
    outcome = boundary.authenticate(Credentials(bearer_token=bad_sig_token))
    assert outcome.authenticated is False
    assert outcome.reason == AuthFailureReason.BAD_SIGNATURE

    # Expired token
    expired_token = make_jwt(
        LOCAL_KEY,
        iss=LOCAL_ISSUER,
        sub=str(uuid4()),
        exp_offset=-100,
    )
    outcome = boundary.authenticate(Credentials(bearer_token=expired_token))
    assert outcome.authenticated is False
    assert outcome.reason == AuthFailureReason.TOKEN_EXPIRED

    # Unrecognized issuer
    unrecognized_token = make_jwt(
        LOCAL_KEY,
        iss="https://attacker-idp.example.com",
        sub=str(uuid4()),
    )
    outcome = boundary.authenticate(Credentials(bearer_token=unrecognized_token))
    assert outcome.authenticated is False
    assert outcome.reason == AuthFailureReason.ISSUER_MISMATCH


# --- T20: RBAC Allow & Deny Audit Logging ---


def test_t20_rbac_allow_and_deny_audit_logging(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
    session_service: SessionService,
):
    """T20: Both RBAC allow and deny decisions generate security.authorization audit events."""
    engine = AuthorizationEngine(audit_log=InMemoryAuditLog())
    app = FastAPI()

    @app.get(
        "/api/operator/view",
        dependencies=[
            Depends(
                require_operator_permission(
                    resource_type=OPERATOR_CONSOLE_RESOURCE,
                    action=Action.VIEW,
                    engine=engine,
                    boundary=boundary,
                )
            )
        ],
    )
    def view_route():
        return {"status": "ok"}

    @app.post(
        "/api/operator/execute",
        dependencies=[
            Depends(
                require_operator_permission(
                    resource_type=OPERATOR_CONSOLE_RESOURCE,
                    action=Action.EXECUTE,
                    engine=engine,
                    boundary=boundary,
                )
            )
        ],
    )
    def execute_route():
        return {"status": "executed"}

    client = TestClient(app)

    # Setup user with AUDITOR role (allows VIEW on operator_console, denies EXECUTE)
    account_id = uuid4()
    tenant_id = uuid4()
    account = Account(
        account_id=account_id,
        tenant_id=tenant_id,
        username="auditor_user",
        email="auditor@example.com",
        status="active",
    )
    identity_store.save_account(account)
    identity_store.set_account_roles(account_id, [Role.AUDITOR])
    identity_store.set_account_scope(account_id, Scope(tenant_id=str(tenant_id)))

    # Session is required for local tokens when session_service is wired
    session = session_service.create_session(
        account_id=account_id, provider="local_password"
    )

    token = make_jwt(
        LOCAL_KEY,
        iss=LOCAL_ISSUER,
        sub=str(account_id),
        extra_claims={"sid": str(session.session_id)},
    )

    # 1. Allowed request (VIEW) -> 200 + audit outcome=allow
    resp_allow = client.get(
        "/api/operator/view",
        headers={
            "Authorization": f"Bearer {token}",
            "x-correlation-id": "cid-allow-t20",
        },
    )
    assert resp_allow.status_code == 200

    allow_events = engine.audit_log.list_events(correlation_id="cid-allow-t20")
    assert len(allow_events) == 1
    assert allow_events[0].outcome == "allow"
    assert allow_events[0].action == "view"
    assert allow_events[0].actor == str(account_id)

    # 2. Denied request (EXECUTE) -> 403 + audit outcome=deny
    resp_deny = client.post(
        "/api/operator/execute",
        headers={
            "Authorization": f"Bearer {token}",
            "x-correlation-id": "cid-deny-t20",
        },
    )
    assert resp_deny.status_code == 403

    deny_events = engine.audit_log.list_events(correlation_id="cid-deny-t20")
    assert len(deny_events) == 1
    assert deny_events[0].outcome == "deny"
    assert deny_events[0].action == "execute"
    assert deny_events[0].actor == str(account_id)


# --- T21: High-Risk Action Session Revocation Propagation ---


def test_t21_high_risk_session_revocation_propagation(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
    session_service: SessionService,
):
    """T21: High-risk write/approve action immediately checks sid; revocation causes immediate 401."""
    engine = AuthorizationEngine(audit_log=InMemoryAuditLog())
    app = FastAPI()

    @app.post(
        "/api/operator/approve",
        dependencies=[
            Depends(
                require_permission(
                    resource_type="intervention",
                    action=Action.APPROVE,
                    engine=engine,
                    boundary=boundary,
                    session_service=session_service,
                )
            )
        ],
    )
    def approve_action():
        return {"status": "approved"}

    client = TestClient(app)

    # User with OPERATIONS_MANAGER role (grants intervention APPROVE)
    account_id = uuid4()
    tenant_id = uuid4()
    account = Account(
        account_id=account_id,
        tenant_id=tenant_id,
        username="ops_mgr",
        email="ops_mgr@example.com",
        status="active",
    )
    identity_store.save_account(account)
    identity_store.set_account_roles(account_id, [Role.OPERATIONS_MANAGER])
    identity_store.set_account_scope(account_id, Scope(tenant_id=str(tenant_id)))

    # Create active session
    session = session_service.create_session(
        account_id=account_id,
        provider="local_password",
    )

    token = make_jwt(
        LOCAL_KEY,
        iss=LOCAL_ISSUER,
        sub=str(account_id),
        extra_claims={"sid": str(session.session_id)},
    )

    # 1. Before revocation: High-risk action succeeds (200)
    resp1 = client.post(
        "/api/operator/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp1.status_code == 200
    assert resp1.json() == {"status": "approved"}

    # 2. Revoke session (e.g. security admin revocation or logout)
    session_service.revoke_session(session.session_id, RevocationReason.ADMIN_REVOKE)

    # 3. Immediate next high-risk request with same token fails with 401 session_revoked
    resp2 = client.post(
        "/api/operator/approve",
        headers={
            "Authorization": f"Bearer {token}",
            "x-correlation-id": "cid-revoked-t21",
        },
    )
    assert resp2.status_code == 401
    assert resp2.json()["detail"] == AuthFailureReason.SESSION_REVOKED.value


# --- T19b: Local Token Without Required sid Fails Closed ---


def test_t19b_local_token_without_sid_rejected(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
):
    """T19b: Local token without sid claim is rejected when session_service is wired."""
    account_id = uuid4()
    tenant_id = uuid4()
    account = Account(
        account_id=account_id,
        tenant_id=tenant_id,
        username="no_sid_user",
        email="nosid@example.com",
        status="active",
    )
    identity_store.save_account(account)
    identity_store.set_account_roles(account_id, [Role.OPERATIONS_MANAGER])

    # Token is signed correctly but omits the sid claim
    token = make_jwt(LOCAL_KEY, iss=LOCAL_ISSUER, sub=str(account_id))

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is False
    assert outcome.reason == AuthFailureReason.SESSION_NOT_FOUND
    assert outcome.token_type == "local"


# --- T19c: Malformed UUID Subject Fails Closed ---


def test_t19c_malformed_uuid_subject_fails_closed(
    boundary: AuthenticationBoundary,
    session_service: SessionService,
):
    """T19c: Local token with a non-UUID subject returns 401 instead of raising ValueError."""
    # Create a session for the token (sid required)
    dummy_account_id = uuid4()
    session = session_service.create_session(
        account_id=dummy_account_id,
        provider="local_password",
    )

    # Token with a non-UUID subject like "not-a-uuid"
    token = make_jwt(
        LOCAL_KEY,
        iss=LOCAL_ISSUER,
        sub="not-a-valid-uuid",
        extra_claims={"sid": str(session.session_id)},
    )

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is False
    assert outcome.reason == AuthFailureReason.MALFORMED_TOKEN
    assert outcome.token_type == "local"


# --- T21b: High-Risk Guard Denies When sid Missing ---


def test_t21b_high_risk_guard_denies_missing_sid(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
    session_service: SessionService,
):
    """T21b: High-risk action guard denies when session_service is wired but sid is absent."""
    engine = AuthorizationEngine(audit_log=InMemoryAuditLog())
    app = FastAPI()

    @app.post(
        "/api/operator/approve",
        dependencies=[
            Depends(
                require_permission(
                    resource_type="intervention",
                    action=Action.APPROVE,
                    engine=engine,
                    boundary=boundary,
                    session_service=session_service,
                )
            )
        ],
    )
    def approve_action():
        return {"status": "approved"}

    # Create an account with a session, but build a boundary WITHOUT
    # session_service so the token passes initial auth without sid
    account_id = uuid4()
    tenant_id = uuid4()
    account = Account(
        account_id=account_id,
        tenant_id=tenant_id,
        username="ops_no_sid",
        email="ops_nosid@example.com",
        status="active",
    )
    identity_store.save_account(account)
    identity_store.set_account_roles(account_id, [Role.OPERATIONS_MANAGER])
    identity_store.set_account_scope(account_id, Scope(tenant_id=str(tenant_id)))

    # Build a boundary without session_service so the initial authentication
    # succeeds without requiring sid (but the route guard has session_service
    # and should catch it).
    from modules.opsboard.auth.config import AuthBoundaryConfig as _Config

    no_session_config = _Config(
        audiences=frozenset([AUDIENCE]),
        local_issuer=LOCAL_ISSUER,
        local_signing_keys={"local-k1": LOCAL_KEY},
        local_audiences=frozenset([AUDIENCE]),
        identity_store=identity_store,
        session_service=None,  # No session enforcement at boundary level
    )
    no_session_boundary = AuthenticationBoundary(no_session_config)

    # Rebuild the route with this boundary
    app2 = FastAPI()

    @app2.post(
        "/api/operator/approve2",
        dependencies=[
            Depends(
                require_permission(
                    resource_type="intervention",
                    action=Action.APPROVE,
                    engine=engine,
                    boundary=no_session_boundary,
                    session_service=session_service,
                )
            )
        ],
    )
    def approve_action2():
        return {"status": "approved"}

    client2 = TestClient(app2)

    # Token without sid claim
    token = make_jwt(LOCAL_KEY, iss=LOCAL_ISSUER, sub=str(account_id))

    resp = client2.post(
        "/api/operator/approve2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == AuthFailureReason.SESSION_NOT_FOUND.value


# --- Regression: Defect #1 PersistenceBundle Carries Identity/Session Stores ---


def test_regression_persistence_bundle_carries_identity_stores():
    """Defect #1: PersistenceBundle must expose identity_store and session_service.

    Previously the PersistenceBundle never carried identity or session stores,
    so default_boundary() constructed empty InMemory doubles that could never
    resolve persisted accounts or sessions.
    """
    from shared.infrastructure.persistence import build_persistence

    bundle = build_persistence(mode="memory")
    assert bundle.identity_store is not None, (
        "PersistenceBundle.identity_store must not be None"
    )
    assert bundle.session_service is not None, (
        "PersistenceBundle.session_service must not be None"
    )
    # Verify the identity store implements the required protocol
    assert hasattr(bundle.identity_store, "find_account_by_id")
    assert hasattr(bundle.identity_store, "get_account_roles")
    # Verify the session service implements validate_session
    assert hasattr(bundle.session_service, "validate_session")
    assert hasattr(bundle.session_service, "create_session")


# --- Regression: Defect #2 Cross-Identity Session Binding ---


def test_regression_cross_identity_session_binding_rejected(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
    session_service: SessionService,
):
    """Defect #2: sub=A with active sid(B) must fail closed.

    Previously the boundary validated that sid was active but never checked
    that session.account_id matched the token sub. This allowed a token with
    sub=A to authenticate using any active session belonging to account B.
    """
    # Create two accounts
    account_a_id = uuid4()
    account_b_id = uuid4()
    tenant_id = uuid4()

    account_a = Account(
        account_id=account_a_id,
        tenant_id=tenant_id,
        username="user_a",
        email="user_a@example.com",
        status="active",
    )
    account_b = Account(
        account_id=account_b_id,
        tenant_id=tenant_id,
        username="user_b",
        email="user_b@example.com",
        status="active",
    )
    identity_store.save_account(account_a)
    identity_store.save_account(account_b)
    identity_store.set_account_roles(account_a_id, [Role.OPERATIONS_MANAGER])
    identity_store.set_account_roles(account_b_id, [Role.OPERATIONS_MANAGER])

    # Create session for account B
    session_b = session_service.create_session(
        account_id=account_b_id,
        provider="local_password",
    )

    # Token has sub=A but sid=session_B (cross-identity attack)
    token = make_jwt(
        LOCAL_KEY,
        iss=LOCAL_ISSUER,
        sub=str(account_a_id),
        extra_claims={"sid": str(session_b.session_id)},
    )

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is False, (
        "Cross-identity session binding must be rejected: "
        "sub=A should not authenticate with sid belonging to account B"
    )
    assert outcome.reason == AuthFailureReason.SESSION_NOT_FOUND


def test_regression_wrong_provider_session_rejected(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
    session_service: SessionService,
):
    """Defect #2 variant: A local token using an OIDC session must be rejected."""
    account_id = uuid4()
    tenant_id = uuid4()
    account = Account(
        account_id=account_id,
        tenant_id=tenant_id,
        username="provider_mismatch",
        email="pmismatch@example.com",
        status="active",
    )
    identity_store.save_account(account)
    identity_store.set_account_roles(account_id, [Role.OPERATIONS_MANAGER])

    # Create session with provider='oidc' (not 'local_password')
    session = session_service.create_session(
        account_id=account_id,
        provider="oidc",
    )

    # Local token referencing an OIDC session
    token = make_jwt(
        LOCAL_KEY,
        iss=LOCAL_ISSUER,
        sub=str(account_id),
        extra_claims={"sid": str(session.session_id)},
    )

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is False, (
        "Local token must not use a session with provider='oidc'"
    )
    assert outcome.reason == AuthFailureReason.SESSION_NOT_FOUND


# --- Regression: Defect #3 Required iat Claim ---


def test_regression_missing_iat_fails_closed(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
    session_service: SessionService,
):
    """Defect #3: A token missing the iat claim must fail closed.

    Previously iat was optional and never required. A token without iat was
    accepted. Now iat is required on all token types.
    """
    account_id = uuid4()
    tenant_id = uuid4()
    account = Account(
        account_id=account_id,
        tenant_id=tenant_id,
        username="no_iat_user",
        email="noiat@example.com",
        status="active",
    )
    identity_store.save_account(account)
    identity_store.set_account_roles(account_id, [Role.OPERATIONS_MANAGER])

    session = session_service.create_session(
        account_id=account_id,
        provider="local_password",
    )

    # Build a JWT without iat by directly constructing the payload
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": LOCAL_ISSUER,
        "sub": str(account_id),
        "aud": AUDIENCE,
        "nbf": now - 10,
        "exp": now + 3600,
        "sid": str(session.session_id),
        # iat intentionally omitted
    }
    token = encode_compact_jwt(payload, LOCAL_KEY)

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is False, (
        "A token missing the required iat claim must fail closed"
    )
    assert outcome.reason == AuthFailureReason.MALFORMED_TOKEN

