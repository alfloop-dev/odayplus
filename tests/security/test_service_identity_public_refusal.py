"""Public indistinguishability with precise internal test-owned verifier/audit."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from apps.api.oday_api.security import dependencies
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    Credentials,
    SigningKey,
    encode_compact_jwt,
)
from modules.opsboard.auth.errors import AuthenticationError, AuthFailureReason
from modules.opsboard.auth.service_identity import ServiceIdentity, ServiceIdentityVerifier
from shared.auth import Role, Scope


def boundary():
    identity = ServiceIdentity("test-owned-service", b"test-only-synthetic-secret",
                               roles=frozenset({Role.AUDITOR}),
                               scope=Scope(tenant_id="tenant-a"))
    return AuthenticationBoundary(AuthBoundaryConfig(), service_verifier=ServiceIdentityVerifier(
        {identity.service_id: identity}))


def test_unknown_and_wrong_service_secret_have_same_public_401_but_precise_audit():
    auth = boundary()
    responses = []
    for service_id, reason in [("absent", AuthFailureReason.UNKNOWN_SERVICE),
                               ("test-owned-service", AuthFailureReason.BAD_SERVICE_SECRET)]:
        credentials = Credentials(service_id=service_id, service_secret=b"wrong-secret")
        outcome = auth.authenticate(credentials)
        assert not outcome.authenticated
        assert outcome.reason is reason
        assert reason.value in str(outcome.audit_event.metadata)
        with pytest.raises(HTTPException) as error:
            dependencies.principal_from_headers(
                {"x-service-id": service_id, "x-service-secret": "wrong-secret"}, boundary=auth)
        responses.append((error.value.status_code, error.value.detail, error.value.headers))
    assert responses[0] == responses[1]
    assert responses[0] == (401, "bad_service_secret", {"WWW-Authenticate": "Bearer"})


def test_successful_service_keeps_authoritative_scope():
    outcome = boundary().authenticate(Credentials(
        service_id="test-owned-service", service_secret=b"test-only-synthetic-secret"))
    assert outcome.authenticated
    assert outcome.principal.tenant_id == "tenant-a"
    assert outcome.principal.roles == frozenset({Role.AUDITOR})


def test_lean_public_error_also_hides_service_existence(monkeypatch):
    monkeypatch.setattr(dependencies, "HTTPException", None)
    for reason in (AuthFailureReason.UNKNOWN_SERVICE, AuthFailureReason.BAD_SERVICE_SECRET):
        with pytest.raises(AuthenticationError) as error:
            dependencies._raise_unauthenticated(reason, credential_protocol="service_secret")
        assert error.value.reason is AuthFailureReason.BAD_SERVICE_SECRET


@pytest.mark.parametrize("also_supply_secret", [False, True])
def test_signed_service_jwt_keeps_principal_map_refusal_and_bearer_precedence(also_supply_secret):
    key = SigningKey(kid="protocol-owning", algorithm="HS256", secret=b"offline-protocol-key")
    auth = AuthenticationBoundary(AuthBoundaryConfig(
        issuer="urn:test:service-protocol", audiences=frozenset({"test-api"}),
        signing_keys={key.kid: key}, principal_mappings={},
    ))
    now = datetime.now(UTC)
    token = encode_compact_jwt({
        "iss": "urn:test:service-protocol", "sub": "undeclared-subject",
        "aud": "test-api", "iat": now.timestamp(),
        "exp": (now + timedelta(minutes=5)).timestamp(),
        "roles": ["platform_admin"], "tenant_id": "spoofed-tenant",
    }, key)
    headers = {"authorization": f"Bearer {token}"}
    if also_supply_secret:
        headers.update({"x-service-id": "absent", "x-service-secret": "wrong-secret"})
    outcome = auth.authenticate(Credentials.from_headers(headers))
    assert not outcome.authenticated
    assert outcome.reason is AuthFailureReason.UNKNOWN_SERVICE
    assert outcome.token_type in {"service", "oidc"}
    assert outcome.credential_protocol == "bearer"
    with pytest.raises(HTTPException) as error:
        dependencies.principal_from_headers(headers, boundary=auth)
    assert error.value.status_code == 401
    assert error.value.detail == AuthFailureReason.UNKNOWN_SERVICE.value
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}


def test_lean_signed_protocol_preserves_original_reason(monkeypatch):
    monkeypatch.setattr(dependencies, "HTTPException", None)
    with pytest.raises(AuthenticationError) as error:
        dependencies._raise_unauthenticated(
            AuthFailureReason.UNKNOWN_SERVICE, credential_protocol="bearer",
        )
    assert error.value.reason is AuthFailureReason.UNKNOWN_SERVICE
