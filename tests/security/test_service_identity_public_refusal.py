"""Public indistinguishability with precise internal test-owned verifier/audit."""
import pytest
from fastapi import HTTPException

from apps.api.oday_api.security import dependencies
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary, Credentials
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
            dependencies._raise_unauthenticated(reason)
        assert error.value.reason is AuthFailureReason.BAD_SERVICE_SECRET
