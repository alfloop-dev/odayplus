"""JWT/OIDC token verifier for ODay Plus API auth.

Replaces the header-trust stub in ``apps/api/oday_api/security/dependencies.py``
with real token verification (ODP-SD-09 §3, ODP-FIN-AUTH-001).

Supported IdP configuration modes
──────────────────────────────────
1. **HMAC (HS256)** — shared secret, for local dev / integration tests only.
   Set ``secret_key`` in :class:`JwtVerifierConfig`.

2. **RSA (RS256)** — public key from PEM string (``public_key_pem``), for
   deployed environments with a real OIDC IdP (Google, Okta, Keycloak …).

3. **Stub / bypass mode** — when ``stub_mode=True`` the verifier returns
   an "anonymous-verified" principal and skips all signature checking.
   This should ONLY be enabled in unit-test environments; a real deployment
   must not ship with this on.

Claim mapping
─────────────
Expected JWT payload structure (IdP may differ — adjust ``claim_*`` attrs):

    {
      "sub":   "<subject-id>",
      "iss":   "<expected-issuer>",
      "aud":   ["<expected-audience>"],
      "exp":   <unix-ts>,
      "iat":   <unix-ts>,
      "roles": ["expansion_user", "site_reviewer"],    # ODP canonical roles
      "tid":   "<tenant-id>",          # optional
      "bids":  ["brand-a"],            # optional brand scope
      "rids":  ["region-1"],           # optional region scope
      "sids":  ["store-42"]            # optional store scope
    }

All scope/role claims are optional and their absence does not raise an error;
unknown role strings are silently dropped (matching the existing stub behaviour).

Error handling
──────────────
All verification failures raise :class:`TokenVerificationError` with a brief
reason string.  The caller (dependencies.py) maps this to HTTP 401.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

try:
    import jwt as _pyjwt
    from jwt import DecodeError, ExpiredSignatureError, InvalidAudienceError, InvalidIssuerError
    from jwt.exceptions import InvalidTokenError

    _JWT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    _pyjwt = None  # type: ignore[assignment]
    _JWT_AVAILABLE = False


class TokenVerificationError(Exception):
    """Raised when a bearer token cannot be verified.

    The API layer should map this to HTTP 401 (not 403 — 403 is for
    authorisation failures, 401 is for authentication failures).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class TokenClaims:
    """Structured claims extracted from a verified JWT.

    Only fields consumed by the API auth layer are surfaced here;
    the full raw payload is available via :attr:`raw_payload`.
    """

    subject_id: str
    roles: frozenset[str]
    tenant_id: str | None
    brand_ids: frozenset[str]
    region_ids: frozenset[str]
    store_ids: frozenset[str]
    raw_payload: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)


@dataclass
class JwtVerifierConfig:
    """Configuration for :class:`OidcJwtVerifier`.

    All fields that accept an environment-variable fallback are documented
    with their env-var name so operators can inject them via workload identity
    or k8s secrets without changing code.

    Attributes
    ----------
    issuer:
        Expected ``iss`` claim value.  Required unless ``stub_mode=True``.
        Env: ``JWT_ISSUER``.
    audience:
        Expected ``aud`` claim value.  Required unless ``stub_mode=True``.
        Env: ``JWT_AUDIENCE``.
    algorithms:
        List of accepted signing algorithms.  Defaults to ``["RS256"]``.
    public_key_pem:
        RSA public key in PEM format (``-----BEGIN PUBLIC KEY-----…``).
        Mutually exclusive with ``secret_key``.
        Env: ``JWT_PUBLIC_KEY_PEM``.
    secret_key:
        HMAC shared secret (HS256).  For dev/test only — never use in
        production.  Mutually exclusive with ``public_key_pem``.
        Env: ``JWT_SECRET_KEY``.
    stub_mode:
        If ``True``, skip all verification and return synthetic claims.
        Must not be used in any environment where ``JWT_STUB_MODE`` is
        not explicitly set to ``"true"``.
        Env: ``JWT_STUB_MODE``.
    claim_roles:
        Name of the JWT claim that carries role strings.  Default: ``"roles"``.
    claim_tenant_id:
        Name of the JWT claim that carries the tenant id.  Default: ``"tid"``.
    claim_brand_ids:
        Name of the JWT claim that carries brand ids.  Default: ``"bids"``.
    claim_region_ids:
        Name of the JWT claim that carries region ids.  Default: ``"rids"``.
    claim_store_ids:
        Name of the JWT claim that carries store ids.  Default: ``"sids"``.
    """

    issuer: str = field(default_factory=lambda: os.getenv("JWT_ISSUER", ""))
    audience: str = field(default_factory=lambda: os.getenv("JWT_AUDIENCE", ""))
    algorithms: list[str] = field(default_factory=lambda: ["RS256"])
    public_key_pem: str | None = field(
        default_factory=lambda: os.getenv("JWT_PUBLIC_KEY_PEM") or None
    )
    secret_key: str | None = field(
        default_factory=lambda: os.getenv("JWT_SECRET_KEY") or None
    )
    stub_mode: bool = field(
        default_factory=lambda: os.getenv("JWT_STUB_MODE", "").lower() == "true"
    )

    # Claim name configuration
    claim_roles: str = "roles"
    claim_tenant_id: str = "tid"
    claim_brand_ids: str = "bids"
    claim_region_ids: str = "rids"
    claim_store_ids: str = "sids"

    def __post_init__(self) -> None:
        if self.stub_mode:
            return  # no further validation needed in stub mode
        if not _JWT_AVAILABLE:
            raise RuntimeError(
                "PyJWT is not installed. Add 'PyJWT[crypto]>=2.7' to your dependencies."
            )
        if self.public_key_pem and self.secret_key:
            raise ValueError(
                "JwtVerifierConfig: set either public_key_pem or secret_key, not both."
            )
        if not self.public_key_pem and not self.secret_key:
            raise ValueError(
                "JwtVerifierConfig: one of public_key_pem or secret_key must be set "
                "(or enable stub_mode for test environments)."
            )
        if not self.issuer:
            raise ValueError(
                "JwtVerifierConfig: issuer is required. Set JWT_ISSUER or pass issuer=."
            )
        if not self.audience:
            raise ValueError(
                "JwtVerifierConfig: audience is required. Set JWT_AUDIENCE or pass audience=."
            )


class OidcJwtVerifier:
    """Verifies a bearer token and returns structured :class:`TokenClaims`.

    Usage::

        config = JwtVerifierConfig(
            issuer="https://accounts.example.com",
            audience="odayplus-api",
            public_key_pem=open("idp_public.pem").read(),
        )
        verifier = OidcJwtVerifier(config)
        claims = verifier.verify("eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9…")
        # claims.subject_id, claims.roles, …

    Raises
    ------
    TokenVerificationError
        For any authentication failure (missing token, expired, bad signature,
        wrong issuer/audience, missing ``sub`` claim, etc.).
    """

    def __init__(self, config: JwtVerifierConfig) -> None:
        self._config = config
        self._signing_key: Any = self._resolve_key()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify(self, token: str | None) -> TokenClaims:
        """Verify *token* and return its claims.

        Parameters
        ----------
        token:
            Raw bearer token string (without the ``Bearer `` prefix).
            ``None`` or empty string raises :class:`TokenVerificationError`.

        Returns
        -------
        TokenClaims
            Verified, structured claims.

        Raises
        ------
        TokenVerificationError
            On any verification failure.
        """
        if not token:
            raise TokenVerificationError("missing bearer token")

        if self._config.stub_mode:
            return self._stub_claims()

        return self._decode_and_map(token)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_key(self) -> Any:
        if self._config.stub_mode:
            return None
        if not _JWT_AVAILABLE:
            return None  # will be caught at verify() time
        if self._config.public_key_pem:
            # Load once at construction time; load_pem_public_key validates format.
            try:
                from cryptography.hazmat.primitives.serialization import load_pem_public_key

                return load_pem_public_key(self._config.public_key_pem.encode())
            except Exception as exc:
                raise ValueError(f"JwtVerifierConfig: invalid public_key_pem — {exc}") from exc
        # HMAC path — return the secret string directly (PyJWT accepts str)
        return self._config.secret_key

    def _decode_and_map(self, token: str) -> TokenClaims:
        """Decode and validate a JWT, returning mapped claims."""
        if not _JWT_AVAILABLE:  # pragma: no cover
            raise TokenVerificationError("PyJWT library is not installed")

        options: dict[str, Any] = {
            "require": ["sub", "exp", "iat", "iss", "aud"],
            "verify_signature": True,
            "verify_exp": True,
            "verify_iss": True,
            "verify_aud": True,
        }

        try:
            payload: dict[str, Any] = _pyjwt.decode(
                token,
                key=self._signing_key,
                algorithms=self._config.algorithms,
                audience=self._config.audience,
                issuer=self._config.issuer,
                options=options,
            )
        except ExpiredSignatureError as exc:
            raise TokenVerificationError("token has expired") from exc
        except InvalidAudienceError as exc:
            raise TokenVerificationError("token audience does not match") from exc
        except InvalidIssuerError as exc:
            raise TokenVerificationError("token issuer does not match") from exc
        except DecodeError as exc:
            raise TokenVerificationError(f"token decode error: {exc}") from exc
        except InvalidTokenError as exc:
            raise TokenVerificationError(f"invalid token: {exc}") from exc

        subject = payload.get("sub")
        if not subject:
            raise TokenVerificationError("token missing 'sub' claim")

        return TokenClaims(
            subject_id=str(subject),
            roles=self._extract_string_set(payload, self._config.claim_roles),
            tenant_id=payload.get(self._config.claim_tenant_id) or None,
            brand_ids=self._extract_string_set(payload, self._config.claim_brand_ids),
            region_ids=self._extract_string_set(payload, self._config.claim_region_ids),
            store_ids=self._extract_string_set(payload, self._config.claim_store_ids),
            raw_payload=payload,
        )

    @staticmethod
    def _extract_string_set(payload: dict[str, Any], key: str) -> frozenset[str]:
        """Return a frozenset of non-empty strings from a list-valued claim."""
        raw = payload.get(key)
        if not raw:
            return frozenset()
        if isinstance(raw, str):
            # Some IdPs send a space-separated string instead of a list
            return frozenset(s.strip() for s in raw.split() if s.strip())
        if isinstance(raw, list):
            return frozenset(str(s).strip() for s in raw if str(s).strip())
        return frozenset()

    def _stub_claims(self) -> TokenClaims:
        """Return a synthetic stub claim set for test environments."""
        return TokenClaims(
            subject_id="stub-subject",
            roles=frozenset({"platform_admin"}),
            tenant_id=None,
            brand_ids=frozenset(),
            region_ids=frozenset(),
            store_ids=frozenset(),
            raw_payload={"sub": "stub-subject", "stub": True},
        )
