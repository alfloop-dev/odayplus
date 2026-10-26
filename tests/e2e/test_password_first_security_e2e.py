"""Password-first security E2E acceptance for the Web/API auth boundary.

Task: ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002
Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §§3, 4, 8, 10

The browser-side login journey lives in ``apps/web/tests/login-route.test.ts``.
This companion suite drives the real Python API authentication boundary,
operator dependency, deployment preflight, and audit recorder together. It is
deliberately small: it verifies the cross-runtime seams that a Web-only test
cannot observe, without introducing a second authentication implementation.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from apps.api.oday_api.security.dependencies import (
    OPERATOR_CONSOLE_RESOURCE,
    require_operator_permission,
    reset_bound_persistence,
)
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    Credentials,
    SigningKey,
    encode_compact_jwt,
)
from shared.audit import InMemoryAuditLog
from shared.auth import Action, AuthorizationEngine, Role, Scope
from shared.identity import (
    Account,
    InMemoryIdentityStore,
    InMemorySessionRepository,
    SessionConfig,
    SessionService,
)

ROOT = Path(__file__).resolve().parents[2]
RECEIPT = ROOT / "docs/evidence/e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md"
ROLLOUT_CHECKLIST = ROOT / "docs/deployment/AUTH_MIGRATION_ROLLOUT_CHECKLIST.md"

_validator_spec = importlib.util.spec_from_file_location(
    "password_first_deployment_validator",
    ROOT / "product_ops/deployment/validate_cloud_run_live_deployment.py",
)
assert _validator_spec and _validator_spec.loader
validator = importlib.util.module_from_spec(_validator_spec)
sys.modules[_validator_spec.name] = validator
_validator_spec.loader.exec_module(validator)

LOCAL_KEY = SigningKey(
    kid="e2e-local",
    algorithm="HS256",
    secret=b"e2e-local-signing-key-secret-32-bytes!",
)
OIDC_KEY = SigningKey(
    kid="e2e-oidc",
    algorithm="HS256",
    secret=b"e2e-oidc-signing-key-secret-32-bytes!!",
)
LOCAL_ISSUER = "urn:odp:identity:local"
OIDC_ISSUER = "https://accounts.example.com"
AUDIENCE = "oday-api"


def _jwt(key: SigningKey, *, issuer: str, subject: str, **claims: Any) -> str:
    import time

    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": issuer,
        "sub": subject,
        "aud": AUDIENCE,
        "iat": now,
        "nbf": now - 10,
        "exp": now + 3600,
    }
    payload.update(claims)
    return encode_compact_jwt(payload, key)


class AuthStack:
    """Compose the production auth boundary, session service, and audit sink."""

    def __init__(self, *, oidc_enabled: bool) -> None:
        self.identity = InMemoryIdentityStore()
        self.sessions = SessionService(
            repository=InMemorySessionRepository(), config=SessionConfig()
        )
        self.audit = InMemoryAuditLog()
        self.engine = AuthorizationEngine(audit_log=self.audit)
        config_kwargs: dict[str, Any] = {
            "local_issuer": LOCAL_ISSUER,
            "local_signing_keys": {LOCAL_KEY.kid: LOCAL_KEY},
            "local_audiences": frozenset({AUDIENCE}),
            "principal_mappings": {},
            "identity_store": self.identity,
            "session_service": self.sessions,
        }
        if oidc_enabled:
            config_kwargs.update(
                {
                    "oidc_issuer": OIDC_ISSUER,
                    "oidc_signing_keys": {OIDC_KEY.kid: OIDC_KEY},
                    "oidc_audiences": frozenset({AUDIENCE}),
                }
            )
        self.boundary = AuthenticationBoundary(
            AuthBoundaryConfig(**config_kwargs), audit_log=self.audit
        )

    def account(
        self,
        *,
        username: str,
        tenant_id: UUID | None = None,
        roles: list[Role] | None = None,
    ) -> Account:
        account = Account(
            account_id=uuid4(),
            tenant_id=tenant_id or uuid4(),
            username=username,
            email=f"{username}@example.invalid",
            status="active",
        )
        self.identity.save_account(account)
        self.identity.set_account_roles(account.account_id, roles or [Role.OPERATIONS_MANAGER])
        self.identity.set_account_scope(
            account.account_id, Scope(tenant_id=str(account.tenant_id))
        )
        return account

    def sign_in(self, account: Account) -> tuple[UUID, str]:
        session = self.sessions.create_session(
            account_id=account.account_id, provider="local_password"
        )
        token = _jwt(
            LOCAL_KEY,
            issuer=LOCAL_ISSUER,
            subject=str(account.account_id),
            sid=str(session.session_id),
            tenant_id=str(account.tenant_id),
        )
        return session.session_id, token


def _operator_app(stack: AuthStack, *, tenant_id: UUID | None = None) -> TestClient:
    app = FastAPI()

    @app.get(
        "/operator/console",
        dependencies=[
            Depends(
                require_operator_permission(
                    resource_type=OPERATOR_CONSOLE_RESOURCE,
                    action=Action.VIEW,
                    tenant_id=str(tenant_id) if tenant_id else None,
                    engine=stack.engine,
                    boundary=stack.boundary,
                    session_service=stack.sessions,
                )
            )
        ],
    )
    def console() -> dict[str, str]:
        return {"status": "ok"}

    return TestClient(app)


def _password_first_env(**overrides: str) -> dict[str, str]:
    env = {
        name: f"configured-{name.lower()}"
        for name in validator.REQUIRED_PUBLIC_CONFIG
    }
    env.update(
        {
            name: f"secret-name-{index}:latest"
            for index, name in enumerate(validator.REQUIRED_SECRET_REFERENCES)
        }
    )
    env.update(validator.REQUIRED_RUNTIME_VALUES)
    env.update(
        {
            "ODP_DEPLOY_ENV": "dev",
            "ODP_FORECAST_ENGINE": "statsforecast",
            "ODP_FORECAST_MODEL": "seasonal_naive",
            "ODP_SCHEDULED_INGESTION_TENANT_ID": "tenant-dev",
            "ODP_TENANT_ID": "tenant-dev",
            "ODAY_RELEASE_SHA": "0" * 40,
        }
    )
    env.update(overrides)
    return env


def test_password_first_preflight_passes_without_oidc() -> None:
    env = _password_first_env()
    assert not [name for name in env if "OIDC" in name]

    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=env["ODAY_RELEASE_SHA"],
        root=ROOT,
    )
    failed = [check.name for check in checks if not check.ok]
    assert failed == []
    assert {check.name: check for check in checks}["auth-mode"].detail == "local"


def test_local_mode_rejects_oidc_token_and_records_failure() -> None:
    stack = AuthStack(oidc_enabled=False)
    account = stack.account(username="local-user")
    stack.identity.link_federated_identity(
        account.account_id, OIDC_ISSUER, "untrusted-subject"
    )

    outcome = stack.boundary.authenticate(
        Credentials(
            bearer_token=_jwt(
                OIDC_KEY, issuer=OIDC_ISSUER, subject="untrusted-subject"
            ),
            correlation_id="security-e2e-local-oidc-disabled",
        )
    )

    assert outcome.authenticated is False
    event = stack.audit.list_events(
        correlation_id="security-e2e-local-oidc-disabled"
    )
    assert len(event) == 1
    assert event[0].outcome == "failure"
    assert event[0].actor == "anonymous"


def test_complete_oidc_and_local_tokens_resolve_one_principal() -> None:
    stack = AuthStack(oidc_enabled=True)
    account = stack.account(username="dual-provider")
    stack.identity.link_federated_identity(
        account.account_id, OIDC_ISSUER, "linked-subject"
    )
    _, local_token = stack.sign_in(account)

    local = stack.boundary.authenticate(Credentials(bearer_token=local_token))
    oidc = stack.boundary.authenticate(
        Credentials(
            bearer_token=_jwt(
                OIDC_KEY, issuer=OIDC_ISSUER, subject="linked-subject"
            )
        )
    )

    assert local.authenticated is True
    assert oidc.authenticated is True
    assert local.principal.subject_id == oidc.principal.subject_id == str(account.account_id)
    assert local.principal.roles == oidc.principal.roles
    assert local.principal.tenant_id == oidc.principal.tenant_id == str(account.tenant_id)
    assert local.principal.scope == oidc.principal.scope


def test_cross_tenant_read_is_denied_and_audited() -> None:
    reset_bound_persistence()
    stack = AuthStack(oidc_enabled=False)
    tenant_a, tenant_b = uuid4(), uuid4()
    account = stack.account(username="tenant-a-manager", tenant_id=tenant_a)
    _, token = stack.sign_in(account)
    client = _operator_app(stack, tenant_id=tenant_b)

    response = client.get(
        "/operator/console",
        headers={
            "authorization": f"Bearer {token}",
            "x-correlation-id": "security-e2e-cross-tenant-read",
        },
    )

    assert response.status_code == 403
    assert response.json() != {"status": "ok"}
    events = stack.audit.list_events(
        correlation_id="security-e2e-cross-tenant-read"
    )
    # The same request also emits the successful authentication event. The
    # authorization event is the evidence for the tenant-isolation decision.
    authorization_events = [
        event for event in events if event.event_type == "security.authorization"
    ]
    assert len(authorization_events) == 1
    assert authorization_events[0].outcome == "deny"
    assert authorization_events[0].actor == str(account.account_id)
    assert authorization_events[0].metadata["policy_id"] == "operator.tenant_isolation"


_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)\b(
        ODP_WEB_SESSION_SECRET
      | ODP_WEB_OIDC_CLIENT_SECRET
      | ODP_IDENTITY_TOKEN_SIGNING_KEY
      | ODP_IDENTITY_BOOTSTRAP_SECRET
      | ODP_AUTH_PRINCIPAL_MAP
      | ODAY_DATABASE_URL
    )\s*[=:]\s*(?!<REDACTED>|\(redacted\)|REDACTED|Secret\ Manager|\S*secret-name|$)\S+"""
)
_CREDENTIAL_SHAPES = (
    re.compile(r"GOCSPX-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\$argon2id\$v=\d+\$m=\d+,t=\d+,p=\d+\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+"),
    re.compile(r"\bey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"postgres(?:ql)?://[^\s:]+:[^\s@]+@"),
)


def test_receipt_and_rollout_checklist_are_present_and_redacted() -> None:
    assert RECEIPT.exists()
    assert ROLLOUT_CHECKLIST.exists()
    receipt = RECEIPT.read_text(encoding="utf-8")
    assert "secret_values_redacted: true" in receipt

    for path in (RECEIPT, ROLLOUT_CHECKLIST):
        text = path.read_text(encoding="utf-8")
        assert _SECRET_ASSIGNMENT.findall(text) == []
        for pattern in _CREDENTIAL_SHAPES:
            assert pattern.findall(text) == []
