"""Security tests for the OpsBoard authentication boundary (ODP-GAP-AUTH-001).

Covers SEC-AUTH-001 ("Unauthenticated, expired, and invalid token requests are
denied") for the *live verification* boundary: signature/alg enforcement,
issuer/audience/expiry validation, fail-closed configuration, service identity,
and the audit hook.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    AuthenticationError,
    AuthFailureReason,
    Credentials,
    ServiceIdentity,
    ServiceIdentityVerifier,
    SigningKey,
    config_from_env,
    encode_compact_jwt,
)
from modules.opsboard.auth.boundary import AUTHENTICATION_EVENT_TYPE
from shared.auth import Role, Scope
from shared.observability import MetricsRegistry

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
ISSUER = "https://idp.oday.example"
AUDIENCE = "oday-api"


@pytest.fixture
def key() -> SigningKey:
    return SigningKey(kid="k1", algorithm="HS256", secret=b"a-shared-signing-secret")


@pytest.fixture
def config(key: SigningKey) -> AuthBoundaryConfig:
    return AuthBoundaryConfig(
        issuer=ISSUER,
        audiences=frozenset({AUDIENCE}),
        signing_keys={key.kid: key},
        leeway_seconds=60,
    )


def _claims(**overrides: object) -> dict[str, object]:
    base = {
        "sub": "user-1",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": NOW.timestamp(),
        "exp": (NOW + timedelta(hours=1)).timestamp(),
        "roles": ["operations_manager"],
        "tenant_id": "tenant-a",
        "region_ids": ["north"],
    }
    base.update(overrides)
    return base


def _boundary(config: AuthBoundaryConfig, **kw: object) -> AuthenticationBoundary:
    return AuthenticationBoundary(config, **kw)  # type: ignore[arg-type]


# --- happy path -------------------------------------------------------------

def test_valid_token_authenticates_and_maps_claims(config, key):
    boundary = _boundary(config)
    token = encode_compact_jwt(_claims(), key)

    outcome = boundary.authenticate(Credentials(bearer_token=token), now=NOW)

    assert outcome.authenticated is True
    assert outcome.reason is None
    assert outcome.principal.subject_id == "user-1"
    assert outcome.principal.authenticated is True
    assert Role.OPERATIONS_MANAGER in outcome.principal.roles
    assert outcome.principal.tenant_id == "tenant-a"
    assert outcome.principal.scope.region_ids == frozenset({"north"})


def test_valid_authentication_writes_success_audit_event(config, key):
    boundary = _boundary(config)
    token = encode_compact_jwt(_claims(), key)

    outcome = boundary.authenticate(
        Credentials(bearer_token=token, correlation_id="corr-1"), now=NOW
    )

    events = boundary.audit_log.list_events(correlation_id="corr-1")
    assert len(events) == 1
    event = events[0]
    assert event.event_type == AUTHENTICATION_EVENT_TYPE
    assert event.outcome == "success"
    assert event.actor == "user-1"
    assert event.metadata["token_type"] == "oidc"
    assert outcome.audit_event is event


# --- fail-closed configuration ---------------------------------------------

def test_unconfigured_boundary_denies_every_token(key):
    boundary = _boundary(AuthBoundaryConfig())
    token = encode_compact_jwt(_claims(), key)

    outcome = boundary.authenticate(Credentials(bearer_token=token), now=NOW)

    assert outcome.authenticated is False
    assert outcome.reason is AuthFailureReason.BOUNDARY_NOT_CONFIGURED
    assert outcome.principal.authenticated is False


def test_partial_config_is_not_configured(key):
    # issuer + keys but no audience -> still fail-closed.
    cfg = AuthBoundaryConfig(issuer=ISSUER, signing_keys={key.kid: key})
    assert cfg.is_configured is False


def test_no_credentials_denied():
    boundary = _boundary(
        AuthBoundaryConfig(
            issuer=ISSUER, audiences=frozenset({AUDIENCE}),
            signing_keys={"k1": SigningKey("k1", "HS256", b"x")},
        )
    )
    outcome = boundary.authenticate(Credentials(), now=NOW)
    assert outcome.authenticated is False
    assert outcome.reason is AuthFailureReason.NO_CREDENTIALS
    assert outcome.token_type == "none"


# --- invalid tokens ---------------------------------------------------------

def test_expired_token_denied(config, key):
    boundary = _boundary(config)
    token = encode_compact_jwt(_claims(exp=(NOW - timedelta(hours=1)).timestamp()), key)
    outcome = boundary.authenticate(Credentials(bearer_token=token), now=NOW)
    assert outcome.reason is AuthFailureReason.TOKEN_EXPIRED


def test_token_without_exp_fails_closed(config, key):
    claims = _claims()
    del claims["exp"]
    token = encode_compact_jwt(claims, key)
    outcome = _boundary(config).authenticate(Credentials(bearer_token=token), now=NOW)
    assert outcome.reason is AuthFailureReason.TOKEN_EXPIRED


def test_not_yet_valid_token_denied(config, key):
    token = encode_compact_jwt(_claims(nbf=(NOW + timedelta(hours=1)).timestamp()), key)
    outcome = _boundary(config).authenticate(Credentials(bearer_token=token), now=NOW)
    assert outcome.reason is AuthFailureReason.TOKEN_NOT_YET_VALID


def test_issuer_mismatch_denied(config, key):
    token = encode_compact_jwt(_claims(iss="https://evil.example"), key)
    outcome = _boundary(config).authenticate(Credentials(bearer_token=token), now=NOW)
    assert outcome.reason is AuthFailureReason.ISSUER_MISMATCH


def test_audience_mismatch_denied(config, key):
    token = encode_compact_jwt(_claims(aud="some-other-service"), key)
    outcome = _boundary(config).authenticate(Credentials(bearer_token=token), now=NOW)
    assert outcome.reason is AuthFailureReason.AUDIENCE_MISMATCH


def test_missing_subject_denied(config, key):
    claims = _claims()
    del claims["sub"]
    token = encode_compact_jwt(claims, key)
    outcome = _boundary(config).authenticate(Credentials(bearer_token=token), now=NOW)
    assert outcome.reason is AuthFailureReason.MISSING_SUBJECT


def test_tampered_signature_denied(config):
    # Attacker escalates roles and re-signs with their own secret; the boundary
    # holds the real key, so the signature will not verify.
    forged = encode_compact_jwt(
        _claims(roles=["platform_admin"]),
        SigningKey("k1", "HS256", b"attacker-secret"),
    )
    outcome = _boundary(config).authenticate(Credentials(bearer_token=forged), now=NOW)
    assert outcome.reason is AuthFailureReason.BAD_SIGNATURE


def test_alg_none_is_rejected(config):
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header = b64(json.dumps({"alg": "none", "kid": "k1"}).encode())
    payload = b64(json.dumps(_claims()).encode())
    token = f"{header}.{payload}."
    outcome = _boundary(config).authenticate(Credentials(bearer_token=token), now=NOW)
    assert outcome.reason is AuthFailureReason.UNSUPPORTED_ALGORITHM


def test_algorithm_confusion_rejected(config):
    # Key is HS256; a token claiming RS256 must not verify against it.
    token = encode_compact_jwt(_claims(), SigningKey("k1", "HS256", b"x"))
    header, payload, sig = token.split(".")

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    rs_header = b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
    forged = f"{rs_header}.{payload}.{sig}"
    outcome = _boundary(config).authenticate(Credentials(bearer_token=forged), now=NOW)
    assert outcome.reason is AuthFailureReason.UNSUPPORTED_ALGORITHM


def test_unknown_kid_denied(config, key):
    other = SigningKey("k2", "HS256", key.secret)
    token = encode_compact_jwt(_claims(), other)  # header kid = k2, not in config
    outcome = _boundary(config).authenticate(Credentials(bearer_token=token), now=NOW)
    assert outcome.reason is AuthFailureReason.UNKNOWN_KEY


def test_malformed_token_denied(config):
    outcome = _boundary(config).authenticate(
        Credentials(bearer_token="not-a-jwt"), now=NOW
    )
    assert outcome.reason is AuthFailureReason.MALFORMED_TOKEN


def test_denied_authentication_is_audited_as_failure(config, key):
    boundary = _boundary(config)
    token = encode_compact_jwt(_claims(exp=(NOW - timedelta(hours=1)).timestamp()), key)
    boundary.authenticate(
        Credentials(bearer_token=token, correlation_id="corr-x"), now=NOW
    )
    events = boundary.audit_log.list_events(correlation_id="corr-x")
    assert len(events) == 1
    assert events[0].outcome == "failure"
    assert events[0].metadata["reason"] == AuthFailureReason.TOKEN_EXPIRED.value


def test_raise_for_status_raises_on_denial(config):
    outcome = _boundary(config).authenticate(
        Credentials(bearer_token="bad"), now=NOW
    )
    with pytest.raises(AuthenticationError) as exc:
        outcome.raise_for_status()
    assert exc.value.reason is AuthFailureReason.MALFORMED_TOKEN


# --- service identity -------------------------------------------------------

def _service_boundary(config: AuthBoundaryConfig) -> AuthenticationBoundary:
    verifier = ServiceIdentityVerifier(
        {
            "scheduler": ServiceIdentity(
                service_id="scheduler",
                secret=b"scheduler-secret",
                roles=frozenset({Role.RELEASE_OWNER}),
                scope=Scope(tenant_id="tenant-a"),
            )
        }
    )
    return AuthenticationBoundary(config, service_verifier=verifier)


def test_valid_service_identity_authenticates(config):
    outcome = _service_boundary(config).authenticate(
        Credentials(service_id="scheduler", service_secret=b"scheduler-secret"), now=NOW
    )
    assert outcome.authenticated is True
    assert outcome.token_type == "service"
    assert outcome.principal.subject_id == "service:scheduler"
    assert Role.RELEASE_OWNER in outcome.principal.roles


def test_wrong_service_secret_denied(config):
    outcome = _service_boundary(config).authenticate(
        Credentials(service_id="scheduler", service_secret=b"wrong"), now=NOW
    )
    assert outcome.reason is AuthFailureReason.BAD_SERVICE_SECRET


def test_unknown_service_denied(config):
    outcome = _service_boundary(config).authenticate(
        Credentials(service_id="ghost", service_secret=b"x"), now=NOW
    )
    assert outcome.reason is AuthFailureReason.UNKNOWN_SERVICE


def test_empty_service_registry_fails_closed(config):
    boundary = AuthenticationBoundary(config, service_verifier=ServiceIdentityVerifier())
    outcome = boundary.authenticate(
        Credentials(service_id="scheduler", service_secret=b"scheduler-secret"), now=NOW
    )
    assert outcome.reason is AuthFailureReason.BOUNDARY_NOT_CONFIGURED


# --- config + metrics + headers --------------------------------------------

def test_config_from_env_reads_hs256_keys():
    cfg = config_from_env(
        {
            "ODP_AUTH_ISSUER": ISSUER,
            "ODP_AUTH_AUDIENCES": "oday-api, oday-web",
            "ODP_AUTH_HS256_KEYS": "k1:secret-one,k2:secret-two",
            "ODP_AUTH_LEEWAY_SECONDS": "30",
        }
    )
    assert cfg.is_configured is True
    assert cfg.audiences == frozenset({"oday-api", "oday-web"})
    assert cfg.resolve_key("k2").secret == b"secret-two"
    assert cfg.leeway_seconds == 30


def test_config_from_empty_env_is_fail_closed():
    assert config_from_env({}).is_configured is False


def test_metrics_counter_records_outcomes(config, key):
    metrics = MetricsRegistry()
    boundary = AuthenticationBoundary(config, metrics=metrics)
    boundary.authenticate(
        Credentials(bearer_token=encode_compact_jwt(_claims(), key)), now=NOW
    )
    boundary.authenticate(Credentials(bearer_token="bad"), now=NOW)
    snapshot = metrics.snapshot()
    assert "auth.attempts_total" in snapshot


def test_credentials_from_headers_parses_bearer_and_service():
    creds = Credentials.from_headers(
        {
            "authorization": "Bearer abc.def.ghi",
            "x-service-id": "scheduler",
            "x-service-secret": "s3cr3t",
            "x-correlation-id": "corr-9",
        }
    )
    assert creds.bearer_token == "abc.def.ghi"
    assert creds.service_id == "scheduler"
    assert creds.service_secret == b"s3cr3t"
    assert creds.correlation_id == "corr-9"
