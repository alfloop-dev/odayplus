"""Synthetic signed claims/fake JWKS clocks and thread barriers, no network."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    Credentials,
    JwksResolver,
    SigningKey,
    encode_compact_jwt,
)
from tests.security.test_opsboard_jwks_auth import _jwk


@pytest.mark.parametrize("claim", ["exp", "iat", "nbf"])
@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), float("-inf"), 10**400, None, "not-a-number"]
)
def test_signed_nonfinite_or_present_malformed_time_never_authenticates(claim, value):
    key = SigningKey("offline", "HS256", b"offline-signing-authority")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    config = AuthBoundaryConfig(
        issuer="https://offline.invalid",
        audiences=frozenset({"oday-api"}),
        signing_keys={key.kid: key},
        principal_mappings={
            "offline-user": {"roles": ["auditor"], "scope": {"tenant_id": "offline"}}
        },
    )
    boundary = AuthenticationBoundary(config)
    claims = {
        "iss": config.issuer,
        "aud": "oday-api",
        "sub": "offline-user",
        "iat": now.timestamp(),
        "exp": now.timestamp() + 300,
    }
    assert boundary.authenticate(
        Credentials(bearer_token=encode_compact_jwt(claims, key)), now=now
    ).authenticated
    claims[claim] = value
    result = boundary.authenticate(
        Credentials(bearer_token=encode_compact_jwt(claims, key)), now=now
    )
    assert not result.authenticated and result.reason is not None


def test_random_missing_kids_cannot_amplify_fetch_frequency():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    calls = []

    def fetch(_):
        calls.append(True)
        return {"keys": [_jwk(key, "known")]}

    resolver = JwksResolver("https://offline.invalid", fetch=fetch, clock=lambda: 0.0)
    assert resolver.resolve("known") is not None
    for i in range(20):
        assert resolver.resolve(f"random-{i}") is None
    assert len(calls) == 1


def test_unknown_kids_coalesce_and_cannot_block_unexpired_cached_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ticks = [0.0]
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def fetch(_):
        calls.append(ticks[0])
        if len(calls) > 1:
            entered.set()
            assert release.wait(2), "test must release its fetch barrier"
        return {"keys": [_jwk(key, "known")]}

    resolver = JwksResolver(
        "https://offline.invalid", fetch=fetch, clock=lambda: ticks[0], cache_ttl_seconds=30
    )
    known = resolver.resolve("known")
    assert known is not None
    ticks[0] = 1.0
    with ThreadPoolExecutor(max_workers=2) as workers:
        refresh = workers.submit(resolver.resolve, "untrusted-miss")
        assert entered.wait(1)
        cached = workers.submit(resolver.resolve, "known")
        try:
            assert cached.result(timeout=0.25) is known, (
                "cached authority must not wait on untrusted refresh"
            )
        finally:
            release.set()
        assert refresh.result(timeout=1) is None
    # A shared finite cooldown, not per-kid negative entries: random IDs cannot
    # cause another fetch during the window. Then rotation/expiry may refresh.
    for i in range(20):
        assert resolver.resolve(f"random-{i}") is None
    assert len(calls) == 2
    ticks[0] = 2.0
    assert resolver.resolve("rotated-missing") is None and len(calls) == 3
    assert resolver.resolve("known") is known or resolver.resolve("known") is not None
