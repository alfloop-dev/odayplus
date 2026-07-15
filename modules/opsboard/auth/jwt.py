"""Minimal, dependency-free JOSE compact JWS/JWT verification.

ODP-GAP-AUTH-001 needs a *live* token-verification boundary that runs in the
lean runtime (no ``PyJWT`` / ``cryptography`` guaranteed). This module verifies
the symmetric ``HS256`` family with the standard library so the boundary is
fully exercisable in tests and CI.

Design constraints (fail-closed):

- ``alg: none`` and every unlisted algorithm are rejected outright. The
  ``alg`` header is never trusted to *select* a verification path beyond the
  explicit allow-list -- this is the classic JWT algorithm-confusion defence.
- Signatures are compared with :func:`hmac.compare_digest` (constant time).
- Asymmetric verification (``RS256``/``ES256`` against a live JWKS) is a
  documented seam: :class:`SigningKey` carries the algorithm, and a deployment
  that installs ``cryptography`` plugs an asymmetric verifier via
  :func:`register_verifier` without changing boundary logic. Absent that, an
  ``RS256`` token fails closed with :class:`UnsupportedAlgorithmError`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

SUPPORTED_HMAC_ALGORITHMS: dict[str, str] = {
    "HS256": "sha256",
    "HS384": "sha384",
    "HS512": "sha512",
}


class JwtError(Exception):
    """Base class for all token-decoding failures."""


class MalformedTokenError(JwtError):
    """The compact serialization is not a well-formed JWS/JWT."""


class UnsupportedAlgorithmError(JwtError):
    """The token's ``alg`` header is not in the verifier allow-list."""


class BadSignatureError(JwtError):
    """The signature did not verify against the resolved key."""


@dataclass(frozen=True)
class SigningKey:
    """A verification key resolved by ``kid``.

    ``algorithm`` pins the expected ``alg`` header so a token cannot downgrade
    an RS256 key to an HS256 verification (algorithm confusion). ``secret`` is
    the shared secret for HMAC families.
    """

    kid: str
    algorithm: str
    secret: bytes


# Optional asymmetric verifier hook: maps algorithm -> callable(key, signing_input,
# signature) -> bool. Populated by a deployment that installs a crypto backend.
_ASYMMETRIC_VERIFIERS: dict[str, Callable[[SigningKey, bytes, bytes], bool]] = {}


def register_verifier(
    algorithm: str, verifier: Callable[[SigningKey, bytes, bytes], bool]
) -> None:
    """Register an asymmetric verifier for ``algorithm`` (e.g. ``RS256``)."""

    _ASYMMETRIC_VERIFIERS[algorithm] = verifier


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def encode_compact_jwt(
    claims: dict[str, Any], key: SigningKey, *, headers: dict[str, Any] | None = None
) -> str:
    """Encode a signed compact JWT with an HMAC ``key`` (issuer/test helper).

    Only the HMAC families are supported for signing here; asymmetric signing
    belongs to the IdP, not this verification-side module.
    """

    if key.algorithm not in SUPPORTED_HMAC_ALGORITHMS:
        raise UnsupportedAlgorithmError(f"cannot sign with {key.algorithm!r}")
    header = {"alg": key.algorithm, "typ": "JWT", "kid": key.kid}
    if headers:
        header.update(headers)
    header_seg = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_seg = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_seg}.{payload_seg}".encode("ascii")
    digestmod = SUPPORTED_HMAC_ALGORITHMS[key.algorithm]
    signature = hmac.new(key.secret, signing_input, getattr(hashlib, digestmod)).digest()
    return f"{header_seg}.{payload_seg}.{_b64url_encode(signature)}"


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise MalformedTokenError("invalid base64url segment") from exc


def decode_header(token: str) -> dict[str, Any]:
    """Decode the JOSE header without verifying (to read ``alg``/``kid``)."""

    parts = token.split(".")
    if len(parts) != 3:
        raise MalformedTokenError("compact JWS must have three segments")
    header = _decode_json(parts[0])
    if not isinstance(header, dict):
        raise MalformedTokenError("JOSE header must be an object")
    return header


def _decode_json(segment: str) -> Any:
    try:
        return json.loads(_b64url_decode(segment))
    except (ValueError, UnicodeDecodeError) as exc:
        raise MalformedTokenError("segment is not valid JSON") from exc


def verify_compact_jwt(token: str, key: SigningKey) -> dict[str, Any]:
    """Verify ``token``'s signature against ``key`` and return its claims.

    Raises a :class:`JwtError` subclass on any structural, algorithm, or
    signature failure. Claim *semantics* (exp/iss/aud) are validated by the
    boundary, not here -- this function only guarantees integrity/authenticity.
    """

    parts = token.split(".")
    if len(parts) != 3:
        raise MalformedTokenError("compact JWS must have three segments")
    header_seg, payload_seg, signature_seg = parts

    header = _decode_json(header_seg)
    if not isinstance(header, dict):
        raise MalformedTokenError("JOSE header must be an object")

    alg = header.get("alg")
    if not isinstance(alg, str) or alg.lower() == "none":
        # `alg: none` is an unsigned token -- always rejected.
        raise UnsupportedAlgorithmError(f"algorithm {alg!r} is not allowed")
    if alg != key.algorithm:
        # The resolved key pins the algorithm; a mismatch is a confusion attempt.
        raise UnsupportedAlgorithmError(
            f"token alg {alg!r} does not match key algorithm {key.algorithm!r}"
        )

    signing_input = f"{header_seg}.{payload_seg}".encode("ascii")
    signature = _b64url_decode(signature_seg)

    if alg in SUPPORTED_HMAC_ALGORITHMS:
        digestmod = SUPPORTED_HMAC_ALGORITHMS[alg]
        expected = hmac.new(key.secret, signing_input, getattr(hashlib, digestmod)).digest()
        if not hmac.compare_digest(expected, signature):
            raise BadSignatureError("HMAC signature mismatch")
    elif alg in _ASYMMETRIC_VERIFIERS:
        if not _ASYMMETRIC_VERIFIERS[alg](key, signing_input, signature):
            raise BadSignatureError("asymmetric signature mismatch")
    else:
        raise UnsupportedAlgorithmError(f"no verifier registered for {alg!r}")

    claims = _decode_json(payload_seg)
    if not isinstance(claims, dict):
        raise MalformedTokenError("JWT claims set must be an object")
    return claims
