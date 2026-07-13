"""OpsBoard auth — JWT/OIDC token verification layer.

Provides a framework-agnostic JWT verifier that can be used by the API security
layer (apps/api/oday_api/security/dependencies.py) to replace the header-trust
stub with real signature verification.

Design constraints (ODP-SD-09 §3):
- Validates signature using RS256 / HS256 depending on IdP config.
- Validates `exp`, `iat`, `iss`, `aud` standard claims.
- Invalid / expired / missing tokens raise :class:`TokenVerificationError`
  so the caller can map this to HTTP 401.
- RBAC claim mapping is **read-only** here; RBAC enforcement stays in
  shared.auth and apps/api/oday_api/security.
"""

from .jwt_verifier import JwtVerifierConfig, OidcJwtVerifier, TokenClaims, TokenVerificationError

__all__ = [
    "JwtVerifierConfig",
    "OidcJwtVerifier",
    "TokenClaims",
    "TokenVerificationError",
]
