"""Unsigned parser exhaustion must refuse/audit before any key retrieval."""

import base64
import json
import sys

import pytest

from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    AuthFailureReason,
    Credentials,
    JwksResolver,
)


@pytest.mark.parametrize("shape", ["deep-header", "deep-claims", "oversized"])
def test_unsigned_deep_or_oversized_jwt_is_stable_audited_refusal_without_fetch(shape):
    def b64(raw):
        return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()

    calls = []
    resolver = JwksResolver(
        "https://offline.invalid/jwks", fetch=lambda _: calls.append(True) or {"keys": []}
    )
    config = AuthBoundaryConfig(
        issuer="https://offline.invalid",
        audiences=frozenset({"api"}),
        jwks_uri="https://offline.invalid/jwks",
    )
    boundary = AuthenticationBoundary(config, key_resolver=resolver)
    header = json.dumps({"alg": "RS256", "kid": "untrusted"})
    claims = json.dumps({"iss": config.issuer, "aud": "api", "sub": "offline"})
    depth = max(10000, sys.getrecursionlimit() + 100)
    nested = '{"extra":' + "[" * depth + "0" + "]" * depth + "}"
    if shape == "deep-header":
        header = nested
    elif shape == "deep-claims":
        claims = nested
    else:
        claims = json.dumps({"iss": config.issuer, "extra": "private-untrusted-marker" * 5000})
    token = b64(header) + "." + b64(claims) + ".AA"
    outcome = boundary.authenticate(
        Credentials(bearer_token=token, correlation_id="offline-parser")
    )
    assert not outcome.authenticated and outcome.reason is AuthFailureReason.MALFORMED_TOKEN
    assert calls == []
    events = boundary.audit_log.list_events(correlation_id="offline-parser")
    assert len(events) == 1 and events[0].outcome == "failure"
    assert "private-untrusted-marker" not in repr(events) and token not in repr(events)


def test_signed_healthy_string_delimiters_are_not_parser_nesting():
    from datetime import UTC, datetime

    from modules.opsboard.auth import SigningKey, encode_compact_jwt

    now = datetime(2026, 1, 1, tzinfo=UTC)
    key = SigningKey("offline", "HS256", b"offline-signing-key")
    config = AuthBoundaryConfig(
        issuer="https://offline.invalid",
        audiences=frozenset({"api"}),
        signing_keys={key.kid: key},
        principal_mappings={"offline": {"roles": ["auditor"], "scope": {"tenant_id": "offline"}}},
    )
    boundary = AuthenticationBoundary(config)
    claims = {
        "iss": config.issuer,
        "aud": "api",
        "sub": "offline",
        "iat": now.timestamp(),
        "exp": now.timestamp() + 300,
        "extra": 'quoted\\\\"string' + ("[{]}" * 1000),
    }
    token = encode_compact_jwt(claims, key)
    result = boundary.authenticate(Credentials(bearer_token=token), now=now)
    assert result.authenticated and result.reason is None
