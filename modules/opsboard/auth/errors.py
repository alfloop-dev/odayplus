"""Authentication failure taxonomy for the OpsBoard auth boundary.

ODP-GAP-AUTH-001: the boundary must fail *closed*. Every rejection path maps to
a stable :class:`AuthFailureReason` so audit events, structured logs, and API
responses can classify the denial without leaking token contents.
"""

from __future__ import annotations

from enum import StrEnum


class AuthFailureReason(StrEnum):
    """Why an authentication attempt was denied.

    Values are stable strings safe to emit in audit metadata and logs. They
    never contain token material.
    """

    # Fail-closed: the live IdP / service registry inputs are absent.
    BOUNDARY_NOT_CONFIGURED = "boundary_not_configured"
    NO_CREDENTIALS = "no_credentials"

    # Token shape / algorithm problems.
    MALFORMED_TOKEN = "malformed_token"
    UNSUPPORTED_ALGORITHM = "unsupported_algorithm"
    UNKNOWN_KEY = "unknown_key"
    BAD_SIGNATURE = "bad_signature"

    # Claim validation problems (OIDC ID/access token).
    ISSUER_MISMATCH = "issuer_mismatch"
    AUDIENCE_MISMATCH = "audience_mismatch"
    TOKEN_EXPIRED = "token_expired"
    TOKEN_NOT_YET_VALID = "token_not_yet_valid"
    MISSING_SUBJECT = "missing_subject"

    # Service identity problems.
    UNKNOWN_SERVICE = "unknown_service"
    BAD_SERVICE_SECRET = "bad_service_secret"


class AuthenticationError(Exception):
    """Raised when authentication fails and a caller opts into exceptions.

    The boundary's default surface returns a denying
    :class:`~modules.opsboard.auth.boundary.AuthOutcome`; callers that prefer
    exception control flow use :meth:`AuthOutcome.raise_for_status`.
    """

    def __init__(self, reason: AuthFailureReason, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason.value)
