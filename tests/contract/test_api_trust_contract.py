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
    reset_bound_persistence,
    reset_default_boundary,
)
from modules.opsboard.auth import (
    AUTHENTICATION_EVENT_TYPE,
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


def make_local_jwt(
    *,
    sub: str,
    sid: str,
    tenant_id: str,
    exp_offset: int = 3600,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Local access token carrying every Contract §4.3 required claim.

    §4.3 requires sub, sid, iat, exp and tenant_id; tests that probe a missing
    required claim build their payload explicitly instead of using this helper.
    """
    claims: dict[str, Any] = {"sid": sid, "tenant_id": tenant_id}
    if extra_claims:
        claims.update(extra_claims)
    return make_jwt(
        LOCAL_KEY,
        iss=LOCAL_ISSUER,
        sub=sub,
        exp_offset=exp_offset,
        extra_claims=claims,
    )


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
    reset_bound_persistence()
    yield
    reset_bound_persistence()


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
    token = make_local_jwt(
        sub=str(account_id),
        sid=str(session.session_id),
        tenant_id=str(tenant_id),
        extra_claims={"roles": ["operations_manager"]},  # Must be ignored!
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

    token = make_local_jwt(
        sub=str(account_id),
        sid=str(session.session_id),
        tenant_id=str(tenant_id),
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

    token = make_local_jwt(
        sub=str(account_id),
        sid=str(session.session_id),
        tenant_id=str(tenant_id),
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
    """T19b: Local token without the required sid claim is rejected."""
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
    token = make_jwt(
        LOCAL_KEY,
        iss=LOCAL_ISSUER,
        sub=str(account_id),
        extra_claims={"tenant_id": str(tenant_id)},
    )

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is False
    assert outcome.reason == AuthFailureReason.MISSING_REQUIRED_CLAIM
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
    token = make_local_jwt(
        sub="not-a-valid-uuid",
        sid=str(session.session_id),
        tenant_id=str(uuid4()),
    )

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is False
    assert outcome.reason == AuthFailureReason.MALFORMED_TOKEN
    assert outcome.token_type == "local"


# --- T21b: Boundary Without a Session Verifier Fails Closed ---


def test_t21b_boundary_without_session_service_fails_closed(
    identity_store: InMemoryIdentityStore,
):
    """T21b: A local token is denied when no server-side session verifier is wired.

    Contract §5.4 makes server-side session trust mandatory for local tokens.
    A boundary built without a session_service cannot establish that the sid
    still maps to a live session, so it must deny rather than accept the
    token's own sid claim. Previously it skipped sid verification entirely,
    which let a signed token carry an arbitrary sid.
    """
    engine = AuthorizationEngine(audit_log=InMemoryAuditLog())

    account_id = uuid4()
    tenant_id = uuid4()
    account = Account(
        account_id=account_id,
        tenant_id=tenant_id,
        username="ops_no_verifier",
        email="ops_noverifier@example.com",
        status="active",
    )
    identity_store.save_account(account)
    identity_store.set_account_roles(account_id, [Role.OPERATIONS_MANAGER])
    identity_store.set_account_scope(account_id, Scope(tenant_id=str(tenant_id)))

    from modules.opsboard.auth.config import AuthBoundaryConfig as _Config

    no_session_boundary = AuthenticationBoundary(
        _Config(
            audiences=frozenset([AUDIENCE]),
            local_issuer=LOCAL_ISSUER,
            local_signing_keys={"local-k1": LOCAL_KEY},
            local_audiences=frozenset([AUDIENCE]),
            identity_store=identity_store,
            session_service=None,  # No server-side session verifier
        )
    )

    app = FastAPI()

    @app.post(
        "/api/operator/approve",
        dependencies=[
            Depends(
                require_permission(
                    resource_type="intervention",
                    action=Action.APPROVE,
                    engine=engine,
                    boundary=no_session_boundary,
                )
            )
        ],
    )
    def approve_action():
        return {"status": "approved"}

    client = TestClient(app)

    # An arbitrary, never-issued sid must not be taken at face value.
    token = make_local_jwt(
        sub=str(account_id),
        sid=str(uuid4()),
        tenant_id=str(tenant_id),
    )

    outcome = no_session_boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is False
    assert outcome.reason == AuthFailureReason.BOUNDARY_NOT_CONFIGURED

    resp = client.post(
        "/api/operator/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == AuthFailureReason.BOUNDARY_NOT_CONFIGURED.value


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
    token = make_local_jwt(
        sub=str(account_a_id),
        sid=str(session_b.session_id),
        tenant_id=str(tenant_id),
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
    token = make_local_jwt(
        sub=str(account_id),
        sid=str(session.session_id),
        tenant_id=str(tenant_id),
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
        "tenant_id": str(tenant_id),
        # iat intentionally omitted
    }
    token = encode_compact_jwt(payload, LOCAL_KEY)

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is False, (
        "A token missing the required iat claim must fail closed"
    )
    assert outcome.reason == AuthFailureReason.MALFORMED_TOKEN



# --- Regression: Contract §4.3 required tenant_id claim ---


def test_regression_local_token_without_tenant_id_rejected(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
    session_service: SessionService,
):
    """§4.3 lists tenant_id as a required claim on local access tokens.

    Previously a correctly signed token that omitted tenant_id still
    authenticated, and the tenant was silently taken from the account row —
    so the token never had to state which tenant it was issued for.
    """
    account_id = uuid4()
    tenant_id = uuid4()
    identity_store.save_account(
        Account(
            account_id=account_id,
            tenant_id=tenant_id,
            username="no_tenant_claim",
            email="no_tenant@example.com",
            status="active",
        )
    )
    identity_store.set_account_roles(account_id, [Role.OPERATIONS_MANAGER])
    session = session_service.create_session(
        account_id=account_id, provider="local_password"
    )

    token = make_jwt(
        LOCAL_KEY,
        iss=LOCAL_ISSUER,
        sub=str(account_id),
        extra_claims={"sid": str(session.session_id)},  # tenant_id omitted
    )

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is False
    assert outcome.reason == AuthFailureReason.MISSING_REQUIRED_CLAIM
    assert outcome.token_type == "local"


def test_regression_local_token_tenant_claim_must_match_account(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
    session_service: SessionService,
):
    """A tenant_id claim disagreeing with the account row fails closed."""
    account_id = uuid4()
    tenant_id = uuid4()
    identity_store.save_account(
        Account(
            account_id=account_id,
            tenant_id=tenant_id,
            username="tenant_mismatch",
            email="tenant_mismatch@example.com",
            status="active",
        )
    )
    identity_store.set_account_roles(account_id, [Role.OPERATIONS_MANAGER])
    session = session_service.create_session(
        account_id=account_id, provider="local_password"
    )

    token = make_local_jwt(
        sub=str(account_id),
        sid=str(session.session_id),
        tenant_id=str(uuid4()),  # a tenant the account does not belong to
    )

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is False
    assert outcome.reason == AuthFailureReason.TENANT_MISMATCH


def test_regression_authoritative_tenant_comes_from_account_not_token(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
    session_service: SessionService,
):
    """The account row stays authoritative for tenant even once the claim matches."""
    account_id = uuid4()
    tenant_id = uuid4()
    identity_store.save_account(
        Account(
            account_id=account_id,
            tenant_id=tenant_id,
            username="tenant_ok",
            email="tenant_ok@example.com",
            status="active",
        )
    )
    identity_store.set_account_roles(account_id, [Role.AUDITOR])
    identity_store.set_account_scope(account_id, Scope(tenant_id=str(tenant_id)))
    session = session_service.create_session(
        account_id=account_id, provider="local_password"
    )

    token = make_local_jwt(
        sub=str(account_id),
        sid=str(session.session_id),
        tenant_id=str(tenant_id),
    )

    outcome = boundary.authenticate(Credentials(bearer_token=token))
    assert outcome.authenticated is True
    assert outcome.principal.tenant_id == str(tenant_id)
    assert outcome.principal.attributes["tenant_id"] == str(tenant_id)


# --- Regression: create_app binds its persistence bundle to the boundary ---


def test_regression_bound_persistence_backs_the_default_boundary(monkeypatch):
    """The boundary must read the same stores the app writes through.

    Previously ``default_boundary()`` called ``build_persistence()`` itself, so
    a session created through the app's bundle was invisible to the boundary
    and every local token was rejected (or, worse, revocations were never
    seen). ``create_app`` now binds its bundle.
    """
    from apps.api.oday_api.security.dependencies import (
        bind_persistence,
        default_boundary,
    )
    from shared.infrastructure.persistence import build_persistence

    monkeypatch.setenv("ODP_AUTH_LOCAL_ISSUER", LOCAL_ISSUER)
    monkeypatch.setenv("ODP_AUTH_LOCAL_HS256_KEYS", f"local-k1:{LOCAL_SECRET.decode()}")
    monkeypatch.setenv("ODP_AUTH_AUDIENCES", AUDIENCE)

    bundle = build_persistence(mode="memory")
    bind_persistence(bundle)

    account_id = uuid4()
    tenant_id = uuid4()
    bundle.identity_store.save_account(
        Account(
            account_id=account_id,
            tenant_id=tenant_id,
            username="bundle_user",
            email="bundle_user@example.com",
            status="active",
        )
    )
    bundle.identity_store.set_account_roles(account_id, [Role.AUDITOR])
    session = bundle.session_service.create_session(
        account_id=account_id, provider="local_password"
    )

    active_boundary = default_boundary()
    assert active_boundary is not None

    token = make_local_jwt(
        sub=str(account_id),
        sid=str(session.session_id),
        tenant_id=str(tenant_id),
    )
    principal = principal_from_headers({"authorization": f"Bearer {token}"})
    assert principal.authenticated is True
    assert principal.subject_id == str(account_id)

    # A revocation written through the app's bundle is seen by the boundary.
    bundle.session_service.revoke_session(
        session.session_id, RevocationReason.ADMIN_REVOKE
    )
    with pytest.raises(HTTPException) as excinfo:
        principal_from_headers({"authorization": f"Bearer {token}"})
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == AuthFailureReason.SESSION_REVOKED.value


# --- Regression: the running app's audit sink backs the auth boundary ---


def test_regression_create_app_binds_its_audit_sink_to_the_boundary(monkeypatch):
    """``security.authentication`` events must reach the app's durable sink.

    ``default_boundary()`` used to construct ``AuthenticationBoundary(config)``
    with no sink, so the boundary kept a private ``InMemoryAuditLog`` while
    every router recorded through ``bundle.audit_log``. Authentication
    successes and failures were therefore written where nothing could read
    them: the contract's authentication audit requirement was satisfied in
    form only.
    """
    from apps.api.oday_api.security.dependencies import (
        bind_audit_log,
        bind_persistence,
        default_boundary,
    )
    from shared.infrastructure.persistence import build_persistence

    monkeypatch.setenv("ODP_AUTH_LOCAL_ISSUER", LOCAL_ISSUER)
    monkeypatch.setenv("ODP_AUTH_LOCAL_HS256_KEYS", f"local-k1:{LOCAL_SECRET.decode()}")
    monkeypatch.setenv("ODP_AUTH_AUDIENCES", AUDIENCE)

    bundle = build_persistence(mode="memory")
    bind_persistence(bundle)
    # create_app resolves one effective sink and hands it to every router; it
    # binds that same object to the boundary.
    bind_audit_log(bundle.audit_log)

    active_boundary = default_boundary()
    assert active_boundary is not None
    assert active_boundary.audit_log is bundle.audit_log

    # A real failed authentication lands in the app's sink, not a private one.
    with pytest.raises(HTTPException) as excinfo:
        principal_from_headers(
            {"authorization": "Bearer not-a-jwt", "x-correlation-id": "cid-auth-sink"}
        )
    assert excinfo.value.status_code == 401

    events = bundle.audit_log.list_events(correlation_id="cid-auth-sink")
    assert [event.event_type for event in events] == [AUTHENTICATION_EVENT_TYPE]
    assert events[0].outcome == "failure"


def test_regression_default_boundary_falls_back_to_the_bundle_audit_log(monkeypatch):
    """Without an explicit sink the boundary still uses the bundle's, never a private log."""
    from apps.api.oday_api.security.dependencies import (
        bind_persistence,
        default_boundary,
    )
    from shared.infrastructure.persistence import build_persistence

    monkeypatch.setenv("ODP_AUTH_LOCAL_ISSUER", LOCAL_ISSUER)
    monkeypatch.setenv("ODP_AUTH_LOCAL_HS256_KEYS", f"local-k1:{LOCAL_SECRET.decode()}")
    monkeypatch.setenv("ODP_AUTH_AUDIENCES", AUDIENCE)

    bundle = build_persistence(mode="memory")
    bind_persistence(bundle)

    active_boundary = default_boundary()
    assert active_boundary is not None
    assert active_boundary.audit_log is bundle.audit_log


# --- Regression: an allow that cannot be audited is not granted ---


class _FailingAuditLog:
    """Audit sink whose ``record`` always fails."""

    def __init__(self) -> None:
        self.attempts = 0

    def record(self, event):
        self.attempts += 1
        raise RuntimeError("audit sink unavailable")

    def list_events(self, **_kwargs):
        return []


class _CapturingDeadLetter:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def record(self, event):
        self.events.append(event)
        return event


def _operator_view_client(engine, boundary) -> TestClient:
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

    return TestClient(app, raise_server_exceptions=False)


def _seed_auditor(identity_store, session_service) -> str:
    account_id = uuid4()
    tenant_id = uuid4()
    identity_store.save_account(
        Account(
            account_id=account_id,
            tenant_id=tenant_id,
            username="allow_audit_user",
            email="allow_audit@example.com",
            status="active",
        )
    )
    identity_store.set_account_roles(account_id, [Role.AUDITOR])
    identity_store.set_account_scope(account_id, Scope(tenant_id=str(tenant_id)))
    session = session_service.create_session(account_id=account_id, provider="local_password")
    return make_local_jwt(
        sub=str(account_id),
        sid=str(session.session_id),
        tenant_id=str(tenant_id),
    )


def test_regression_allow_audit_sink_failure_is_dead_lettered(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
    session_service: SessionService,
):
    """A failing primary sink must not produce an unaudited allow.

    The guard used to log the exception and grant access anyway, so a sink
    outage silently turned every permitted request into a grant with no
    auditable event behind it. The event is now dead-lettered instead.
    """
    from apps.api.oday_api.security.dependencies import (
        reset_audit_dead_letter,
        set_audit_dead_letter,
    )

    failing = _FailingAuditLog()
    dead_letter = _CapturingDeadLetter()
    engine = AuthorizationEngine(audit_log=failing)
    token = _seed_auditor(identity_store, session_service)

    set_audit_dead_letter(dead_letter)
    try:
        client = _operator_view_client(engine, boundary)
        resp = client.get(
            "/api/operator/view",
            headers={
                "Authorization": f"Bearer {token}",
                "x-correlation-id": "cid-allow-dead-letter",
            },
        )
    finally:
        reset_audit_dead_letter()

    assert resp.status_code == 200
    assert failing.attempts == 1
    assert len(dead_letter.events) == 1
    assert dead_letter.events[0].outcome == "allow"
    assert dead_letter.events[0].correlation_id == "cid-allow-dead-letter"


def test_regression_allow_fails_closed_when_audit_is_unrecoverable(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
    session_service: SessionService,
):
    """With both the sink and the dead-letter gone, the allow is refused, not granted."""
    from apps.api.oday_api.security.dependencies import (
        AUDIT_UNAVAILABLE_DETAIL,
        reset_audit_dead_letter,
        set_audit_dead_letter,
    )

    engine = AuthorizationEngine(audit_log=_FailingAuditLog())
    token = _seed_auditor(identity_store, session_service)

    set_audit_dead_letter(_FailingAuditLog())
    try:
        client = _operator_view_client(engine, boundary)
        resp = client.get(
            "/api/operator/view",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        reset_audit_dead_letter()

    assert resp.status_code == 503
    assert resp.json()["detail"] == AUDIT_UNAVAILABLE_DETAIL


def test_regression_require_permission_allow_fails_closed_without_audit(
    boundary: AuthenticationBoundary,
    identity_store: InMemoryIdentityStore,
    session_service: SessionService,
):
    """The same rule applies to the plain ``require_permission`` guard."""
    from apps.api.oday_api.security.dependencies import (
        AUDIT_UNAVAILABLE_DETAIL,
        reset_audit_dead_letter,
        set_audit_dead_letter,
    )

    engine = AuthorizationEngine(audit_log=_FailingAuditLog())
    token = _seed_auditor(identity_store, session_service)

    app = FastAPI()

    @app.get(
        "/api/listing",
        dependencies=[
            Depends(
                require_permission(
                    "listing",
                    Action.VIEW,
                    engine=engine,
                    boundary=boundary,
                    data_classification=DataClassification.INTERNAL,
                )
            )
        ],
    )
    def listing_route():
        return {"status": "ok"}

    set_audit_dead_letter(_FailingAuditLog())
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/listing", headers={"Authorization": f"Bearer {token}"})
    finally:
        reset_audit_dead_letter()

    assert resp.status_code == 503
    assert resp.json()["detail"] == AUDIT_UNAVAILABLE_DETAIL


def test_regression_default_dead_letter_logs_the_event(caplog):
    """The default dead-letter emits the full canonical event as one ERROR line."""
    import json
    import logging

    from apps.api.oday_api.security.dependencies import LoggingAuditDeadLetter
    from shared.audit.events import AuditEvent

    event = AuditEvent(
        event_type="security.authorization",
        actor="actor-1",
        action="view",
        resource="operator_console",
        outcome="allow",
        correlation_id="cid-dead-letter-log",
    )
    with caplog.at_level(logging.ERROR):
        LoggingAuditDeadLetter().record(event)

    records = [r for r in caplog.records if LoggingAuditDeadLetter.marker in r.getMessage()]
    assert len(records) == 1
    payload = json.loads(records[0].getMessage().split(" ", 1)[1])
    assert payload["correlation_id"] == "cid-dead-letter-log"
    assert payload["outcome"] == "allow"


# --- Regression: ODP_AUTH_MODE is the authoritative OIDC gate ---


def test_regression_local_mode_discards_complete_oidc_inputs():
    """mode=local + complete OIDC inputs must not report OIDC as configured."""
    from modules.opsboard.auth.config import config_from_env

    env = {
        "ODP_AUTH_MODE": "local",
        "ODP_AUTH_OIDC_ENABLED": "false",
        "ODP_AUTH_OIDC_ISSUER": OIDC_ISSUER,
        "ODP_AUTH_OIDC_AUDIENCES": AUDIENCE,
        "ODP_AUTH_OIDC_JWKS_URI": "https://accounts.google.com/jwks",
    }
    config = config_from_env(env)

    assert config.oidc_enabled is False
    assert config.oidc_issuer is None
    assert config.oidc_jwks_uri is None
    assert config.oidc_audiences == frozenset()
    assert config.is_configured is False


def test_regression_local_mode_rejects_an_oidc_token(
    identity_store: InMemoryIdentityStore, session_service: SessionService
):
    """An OIDC token is refused when the deployment selected password-first.

    The boundary previously verified and trusted it: only the deploy path read
    ODP_AUTH_MODE, so an environment that turned OIDC off while leaving its
    issuer/JWKS values behind kept accepting OIDC-issued identities.
    """
    from modules.opsboard.auth.config import config_from_env

    env = {
        "ODP_AUTH_MODE": "local",
        "ODP_AUTH_LOCAL_ISSUER": LOCAL_ISSUER,
        "ODP_AUTH_LOCAL_HS256_KEYS": f"local-k1:{LOCAL_SECRET.decode()}",
        "ODP_AUTH_AUDIENCES": AUDIENCE,
        "ODP_AUTH_OIDC_ISSUER": OIDC_ISSUER,
        "ODP_AUTH_OIDC_AUDIENCES": AUDIENCE,
    }
    config = config_from_env(
        env, identity_store=identity_store, session_service=session_service
    )
    # The local provider still works, so this is a gate on OIDC, not an outage.
    assert config.is_configured is True

    gated = AuthenticationBoundary(config)
    oidc_token = make_jwt(OIDC_KEY, iss=OIDC_ISSUER, sub="google-user-1")
    outcome = gated.authenticate(Credentials(bearer_token=oidc_token))

    assert outcome.authenticated is False
    assert outcome.reason is AuthFailureReason.ISSUER_MISMATCH


def test_regression_conflicting_mode_and_flag_disable_oidc():
    """A self-contradicting configuration narrows trust instead of picking a winner."""
    from modules.opsboard.auth.config import config_from_env

    env = {
        "ODP_AUTH_MODE": "oidc",
        "ODP_AUTH_OIDC_ENABLED": "false",
        "ODP_AUTH_OIDC_ISSUER": OIDC_ISSUER,
        "ODP_AUTH_OIDC_AUDIENCES": AUDIENCE,
        "ODP_AUTH_OIDC_JWKS_URI": "https://accounts.google.com/jwks",
    }
    config = config_from_env(env)

    assert config.oidc_enabled is False
    assert config.oidc_issuer is None


def test_regression_oidc_mode_still_accepts_oidc_inputs(
    identity_store: InMemoryIdentityStore, session_service: SessionService
):
    """The gate keeps OIDC working when the deployment actually selected it."""
    from modules.opsboard.auth.config import config_from_env

    env = {
        "ODP_AUTH_MODE": "oidc",
        "ODP_AUTH_OIDC_ISSUER": OIDC_ISSUER,
        "ODP_AUTH_OIDC_AUDIENCES": AUDIENCE,
        "ODP_AUTH_OIDC_JWKS_URI": "https://accounts.google.com/jwks",
    }
    config = config_from_env(
        env, identity_store=identity_store, session_service=session_service
    )

    assert config.oidc_enabled is True
    assert config.oidc_issuer == OIDC_ISSUER
    assert config.is_configured is True


def test_regression_unset_mode_keeps_pre_contract_oidc_deployments_working(
    identity_store: InMemoryIdentityStore, session_service: SessionService
):
    """No explicit mode + a configured API OIDC issuer stays on OIDC.

    The API process never receives ODP_WEB_OIDC_ISSUER, so the deployment
    resolver's pre-contract signal is read from ODP_AUTH_OIDC_ISSUER here. A
    deployment that configured OIDC before ODP_AUTH_MODE existed must not lose
    its provider to this gate.
    """
    from modules.opsboard.auth.config import config_from_env

    env = {
        "ODP_AUTH_OIDC_ISSUER": OIDC_ISSUER,
        "ODP_AUTH_OIDC_AUDIENCES": AUDIENCE,
        "ODP_AUTH_OIDC_JWKS_URI": "https://accounts.google.com/jwks",
    }
    config = config_from_env(
        env, identity_store=identity_store, session_service=session_service
    )

    assert config.oidc_enabled is True
    assert config.oidc_issuer == OIDC_ISSUER


def test_regression_placeholder_oidc_issuer_does_not_enable_the_provider():
    """A placeholder issuer is not a configuration, on either side of the release."""
    from modules.opsboard.auth.config import config_from_env

    config = config_from_env({"ODP_AUTH_OIDC_ISSUER": "placeholder"})

    assert config.oidc_enabled is False
    assert config.oidc_issuer is None
