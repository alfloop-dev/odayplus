"""Bounded, fail-closed JWKS resolution for production OIDC tokens."""

from __future__ import annotations

import base64
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from modules.opsboard.auth.jwt import SigningKey


class KeyResolver(Protocol):
    def resolve(self, kid: str | None) -> SigningKey | None: ...


class JwksResolver:
    """Resolve RSA verification keys with a bounded cache and rotation retry.

    A cache miss refreshes the JWKS once, which permits normal IdP key rotation.
    Fetch, parse, TLS, or key-shape failures return ``None`` and authentication
    fails closed as ``unknown_key``. The last valid cache is retained when a
    refresh fails, so an IdP control-plane outage does not invalidate an
    unexpired key already verified from that endpoint.
    """

    def __init__(
        self,
        uri: str,
        *,
        cache_ttl_seconds: int = 300,
        fetch: Callable[[str], dict[str, Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._uri = uri
        self._ttl = max(30, cache_ttl_seconds)
        self._fetch = fetch or self._fetch_http
        self._clock = clock
        self._keys: dict[str, SigningKey] = {}
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def resolve(self, kid: str | None) -> SigningKey | None:
        if not kid:
            return None
        now = self._clock()
        with self._lock:
            key = self._keys.get(kid)
            if key is not None and now < self._expires_at:
                return key
            self._refresh(now)
            return self._keys.get(kid)

    def _refresh(self, now: float) -> None:
        try:
            payload = self._fetch(self._uri)
            parsed = {
                key.kid: key
                for item in payload.get("keys", [])
                if isinstance(item, dict)
                for key in [_parse_rsa_jwk(item)]
                if key is not None
            }
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return
        if parsed:
            self._keys = parsed
            self._expires_at = now + self._ttl

    @staticmethod
    def _fetch_http(uri: str) -> dict[str, Any]:
        response = httpx.get(uri, timeout=5.0, follow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("JWKS response must be an object")
        return payload


def _decode_uint(value: str) -> int:
    padding = "=" * (-len(value) % 4)
    raw = base64.urlsafe_b64decode(value + padding)
    return int.from_bytes(raw, "big")


def _parse_rsa_jwk(item: dict[str, Any]) -> SigningKey | None:
    if (
        item.get("kty") != "RSA"
        or item.get("use", "sig") != "sig"
        or item.get("alg", "RS256") != "RS256"
    ):
        return None
    kid = item.get("kid")
    modulus = item.get("n")
    exponent = item.get("e")
    if not all(isinstance(value, str) and value for value in (kid, modulus, exponent)):
        return None
    public_key = rsa.RSAPublicNumbers(
        e=_decode_uint(exponent),
        n=_decode_uint(modulus),
    ).public_key()
    pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return SigningKey(kid=kid, algorithm="RS256", secret=pem)


__all__ = ["JwksResolver", "KeyResolver"]
