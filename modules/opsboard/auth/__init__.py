"""OpsBoard server-side authentication boundary (ODP-GAP-AUTH-001).

The verification boundary that produces a trusted
:class:`shared.auth.Principal` from inbound credentials -- live OIDC JWT
verification and service-to-service identity checks -- so the R0-007
RBAC/ABAC engine (:class:`shared.auth.AuthorizationEngine`) authorizes only
cryptographically authenticated subjects. Fails closed when the live IdP /
service-registry inputs are absent.
"""

from modules.opsboard.auth.boundary import (
    AUTHENTICATION_EVENT_TYPE,
    AuthenticationBoundary,
    AuthOutcome,
    Credentials,
)
from modules.opsboard.auth.claims import principal_from_claims
from modules.opsboard.auth.config import AuthBoundaryConfig, config_from_env
from modules.opsboard.auth.errors import AuthenticationError, AuthFailureReason
from modules.opsboard.auth.jwt import (
    BadSignatureError,
    JwtError,
    MalformedTokenError,
    SigningKey,
    UnsupportedAlgorithmError,
    encode_compact_jwt,
    register_verifier,
    verify_compact_jwt,
)
from modules.opsboard.auth.service_identity import (
    ServiceIdentity,
    ServiceIdentityVerifier,
    ServiceVerification,
)

__all__ = [
    "AUTHENTICATION_EVENT_TYPE",
    "AuthBoundaryConfig",
    "AuthOutcome",
    "AuthenticationBoundary",
    "AuthenticationError",
    "AuthFailureReason",
    "BadSignatureError",
    "Credentials",
    "JwtError",
    "MalformedTokenError",
    "ServiceIdentity",
    "ServiceIdentityVerifier",
    "ServiceVerification",
    "SigningKey",
    "UnsupportedAlgorithmError",
    "config_from_env",
    "encode_compact_jwt",
    "principal_from_claims",
    "register_verifier",
    "verify_compact_jwt",
]
