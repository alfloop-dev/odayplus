from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    AuthFailureReason,
    Credentials,
    JwksResolver,
)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _uint(value: int) -> str:
    return _b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def _jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _uint(numbers.n),
        "e": _uint(numbers.e),
    }


def _token(key: rsa.RSAPrivateKey, kid: str, **overrides: object) -> str:
    now = datetime.now(UTC)
    header = _b64(
        json.dumps({"alg": "RS256", "kid": kid}, separators=(",", ":")).encode()
    )
    claims: dict[str, object] = {
        "sub": "operator-real-1",
        "iss": "https://idp.example.test",
        "aud": "oday-api",
        "iat": now.timestamp(),
        "exp": (now + timedelta(minutes=10)).timestamp(),
        "roles": ["operations_manager"],
        "tenant_id": "tenant-real-1",
    }
    claims.update(overrides)
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64(signature)}"


def test_rs256_jwks_token_authenticates_and_maps_real_scope() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    resolver = JwksResolver(
        "https://idp.example.test/.well-known/jwks.json",
        fetch=lambda _: {"keys": [_jwk(key, "key-1")]},
    )
    boundary = AuthenticationBoundary(
        AuthBoundaryConfig(
            issuer="https://idp.example.test",
            audiences=frozenset({"oday-api"}),
            jwks_uri="https://idp.example.test/.well-known/jwks.json",
        ),
        key_resolver=resolver,
    )

    outcome = boundary.authenticate(Credentials(bearer_token=_token(key, "key-1")))

    assert outcome.authenticated is True
    assert outcome.principal.subject_id == "operator-real-1"
    assert outcome.principal.tenant_id == "tenant-real-1"


def test_unknown_or_bad_rs256_key_fails_closed() -> None:
    trusted = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    resolver = JwksResolver(
        "https://idp.example.test/jwks",
        fetch=lambda _: {"keys": [_jwk(trusted, "trusted")]},
    )
    config = AuthBoundaryConfig(
        issuer="https://idp.example.test",
        audiences=frozenset({"oday-api"}),
        jwks_uri="https://idp.example.test/jwks",
    )
    boundary = AuthenticationBoundary(config, key_resolver=resolver)

    unknown = boundary.authenticate(Credentials(bearer_token=_token(attacker, "missing")))
    forged = boundary.authenticate(Credentials(bearer_token=_token(attacker, "trusted")))

    assert unknown.reason is AuthFailureReason.UNKNOWN_KEY
    assert forged.reason is AuthFailureReason.BAD_SIGNATURE


def test_jwks_cache_refreshes_for_rotated_kid_and_keeps_last_good_cache() -> None:
    first = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    second = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    responses: list[object] = [
        {"keys": [_jwk(first, "first")]},
        {"keys": [_jwk(first, "first"), _jwk(second, "second")]},
        ValueError("idp unavailable"),
    ]
    calls = 0

    def fetch(_: str) -> dict[str, object]:
        nonlocal calls
        response = responses[calls]
        calls += 1
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[return-value]

    resolver = JwksResolver("https://idp.example.test/jwks", fetch=fetch)

    first_key = resolver.resolve("first")
    second_key = resolver.resolve("second")
    still_cached = resolver.resolve("first")

    assert first_key is not None
    assert second_key is not None
    assert still_cached is first_key or still_cached.secret == first_key.secret
    assert calls == 2


def test_config_from_env_accepts_jwks_without_hmac_secret() -> None:
    from modules.opsboard.auth import config_from_env

    config = config_from_env(
        {
            "ODP_AUTH_ISSUER": "https://idp.example.test",
            "ODP_AUTH_AUDIENCES": "oday-api",
            "ODP_AUTH_JWKS_URI": "https://idp.example.test/jwks",
            "ODP_AUTH_JWKS_CACHE_TTL_SECONDS": "120",
        }
    )

    assert config.is_configured is True
    assert config.jwks_uri == "https://idp.example.test/jwks"
    assert config.jwks_cache_ttl_seconds == 120
