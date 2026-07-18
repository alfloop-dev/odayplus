"""End-to-end: OpsBoard auth boundary -> shared RBAC/ABAC engine.

ODP-GAP-AUTH-001 delivers the *authentication* half; R0-007's
:class:`shared.auth.AuthorizationEngine` is the *authorization* half. This test
proves they compose: a cryptographically verified principal is what the engine
authorizes, and an unauthenticated (fail-closed) principal is denied before any
role check, with a shared audit trail across both stages.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    Credentials,
    SigningKey,
    encode_compact_jwt,
)
from shared.audit import InMemoryAuditLog
from shared.auth import (
    AccessRequest,
    Action,
    AuthorizationEngine,
    Environment,
    ResourceDescriptor,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
ISSUER = "https://idp.oday.example"
AUDIENCE = "oday-api"
KEY = SigningKey(kid="k1", algorithm="HS256", secret=b"integration-secret")


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def boundary(audit_log: InMemoryAuditLog) -> AuthenticationBoundary:
    config = AuthBoundaryConfig(
        issuer=ISSUER,
        audiences=frozenset({AUDIENCE}),
        signing_keys={KEY.kid: KEY},
    )
    return AuthenticationBoundary(config, audit_log=audit_log)


@pytest.fixture
def engine(audit_log: InMemoryAuditLog) -> AuthorizationEngine:
    # Same audit log: authentication and authorization events share one trail.
    return AuthorizationEngine(audit_log=audit_log)


def _token(roles: list[str], **claims: object) -> str:
    payload = {
        "sub": "user-1",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": NOW.timestamp(),
        "exp": (NOW + timedelta(hours=1)).timestamp(),
        "roles": roles,
        "tenant_id": "tenant-a",
        "region_ids": ["north"],
    }
    payload.update(claims)
    return encode_compact_jwt(payload, KEY)


def _view_forecast(principal, engine, *, region_id: str = "north"):
    # operations_manager may VIEW forecastops (RBAC); ABAC then enforces the
    # principal's region scope.
    request = AccessRequest(
        principal=principal,
        action=Action.VIEW,
        resource=ResourceDescriptor(type="forecastops", tenant_id="tenant-a", region_id=region_id),
        environment=Environment(attributes={"correlation_id": "corr-int"}),
    )
    return engine.authorize(request)


def test_verified_principal_is_authorized_by_rbac(boundary, engine):
    outcome = boundary.authenticate(
        Credentials(bearer_token=_token(["operations_manager"])), now=NOW
    )
    assert outcome.authenticated is True

    decision = _view_forecast(outcome.principal, engine)
    assert decision.allowed is True


def test_unauthenticated_principal_is_denied_by_engine(boundary, engine):
    # Expired token -> fail-closed ANONYMOUS principal -> engine denies.
    expired = _token(["operations_manager"], exp=(NOW - timedelta(hours=1)).timestamp())
    outcome = boundary.authenticate(Credentials(bearer_token=expired), now=NOW)
    assert outcome.authenticated is False

    decision = _view_forecast(outcome.principal, engine)
    assert decision.allowed is False


def test_verified_principal_scope_still_enforced(boundary, engine):
    # Authentication succeeds, but ABAC scope containment blocks another region.
    outcome = boundary.authenticate(
        Credentials(bearer_token=_token(["operations_manager"])), now=NOW
    )
    decision = _view_forecast(outcome.principal, engine, region_id="south")
    assert decision.allowed is False
    assert decision.policy_id == "scope.region"


def test_authentication_and_authorization_share_audit_trail(boundary, engine, audit_log):
    outcome = boundary.authenticate(
        Credentials(bearer_token=_token(["operations_manager"]), correlation_id="corr-int"),
        now=NOW,
    )
    _view_forecast(outcome.principal, engine)

    event_types = {e.event_type for e in audit_log.list_events()}
    assert "security.authentication" in event_types
