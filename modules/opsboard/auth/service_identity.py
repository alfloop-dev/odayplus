"""Service-to-service identity verification (fail-closed).

Product APIs and operator workflows call each other with a service identity
rather than a human OIDC token. ODP-GAP-AUTH-001 requires those calls to be
authenticated too: a caller presents a ``service_id`` + shared secret, verified
here against an explicit registry.

Fail-closed rules:

- An empty registry authenticates *nothing* (no ambient trust).
- An unknown ``service_id`` is denied without a secret comparison.
- The secret is compared with :func:`hmac.compare_digest` (constant time), and
  a dummy comparison still runs for unknown services to avoid leaking which
  ``service_id`` values exist via timing.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field

from modules.opsboard.auth.errors import AuthFailureReason
from shared.auth import Principal, Role, Scope


@dataclass(frozen=True)
class ServiceIdentity:
    """A registered service principal and the roles/scope it may act under."""

    service_id: str
    secret: bytes
    roles: frozenset[Role] = frozenset()
    scope: Scope = field(default_factory=Scope)

    def to_principal(self) -> Principal:
        return Principal(
            subject_id=f"service:{self.service_id}",
            roles=self.roles,
            scope=self.scope,
            attributes={"token_type": "service", "service_id": self.service_id},
            authenticated=True,
        )


@dataclass(frozen=True)
class ServiceVerification:
    """Result of a service-identity check."""

    ok: bool
    principal: Principal | None = None
    reason: AuthFailureReason | None = None


class ServiceIdentityVerifier:
    """Verifies presented service credentials against a fixed registry."""

    # Constant used for the timing-equalising dummy compare on unknown services.
    _DUMMY_SECRET = b"\x00" * 32

    def __init__(self, registry: Mapping[str, ServiceIdentity] | None = None) -> None:
        self._registry: dict[str, ServiceIdentity] = dict(registry or {})

    @property
    def is_configured(self) -> bool:
        return bool(self._registry)

    def verify(self, service_id: str | None, secret: bytes | None) -> ServiceVerification:
        if not self._registry:
            return ServiceVerification(
                ok=False, reason=AuthFailureReason.BOUNDARY_NOT_CONFIGURED
            )
        if not service_id or secret is None:
            return ServiceVerification(ok=False, reason=AuthFailureReason.NO_CREDENTIALS)

        identity = self._registry.get(service_id)
        if identity is None:
            # Equalise timing so probing service ids is not observably faster.
            hmac.compare_digest(self._DUMMY_SECRET, secret)
            return ServiceVerification(ok=False, reason=AuthFailureReason.UNKNOWN_SERVICE)

        if not hmac.compare_digest(identity.secret, secret):
            return ServiceVerification(
                ok=False, reason=AuthFailureReason.BAD_SERVICE_SECRET
            )
        return ServiceVerification(ok=True, principal=identity.to_principal())
