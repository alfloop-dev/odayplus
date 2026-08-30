"""The OpsBoard authentication boundary.

This is the single server-side entry point that turns *credentials on the wire*
into a verified :class:`shared.auth.Principal`, which the existing
:class:`shared.auth.AuthorizationEngine` (RBAC/ABAC, R0-007) then authorizes.

Responsibilities (ODP-GAP-AUTH-001):

1. **Live OIDC verification** -- cryptographically verify a bearer JWT
   (signature + issuer + audience + expiry) before trusting any claim.
2. **Fail-closed** -- when the boundary is not configured with live IdP inputs,
   or the service registry is empty, deny every request. Never fall back to the
   header-trust stub.
3. **Service identity** -- verify service-to-service credentials.
4. **Audit hooks** -- every authentication decision (allow *and* deny) writes a
   canonical :class:`shared.audit.AuditEvent`; denials are also logged/metered
   through ``shared.observability`` when those sinks are supplied.

The boundary never raises on a failed authentication by default: it returns a
denying :class:`AuthOutcome` whose ``principal`` is
:data:`shared.auth.ANONYMOUS`. Callers wanting exceptions use
:meth:`AuthOutcome.raise_for_status`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from modules.opsboard.auth.claims import principal_from_claims
from modules.opsboard.auth.config import AuthBoundaryConfig
from modules.opsboard.auth.errors import AuthenticationError, AuthFailureReason
from modules.opsboard.auth.jwks import JwksResolver, KeyResolver
from modules.opsboard.auth.jwt import (
    BadSignatureError,
    JwtError,
    UnsupportedAlgorithmError,
    decode_header,
    decode_unverified_claims,
    verify_compact_jwt,
)
from modules.opsboard.auth.service_identity import ServiceIdentityVerifier
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.audit.policy import AuditRecorder
from shared.auth import ANONYMOUS, Principal, Role
from shared.observability import (
    MetricCategory,
    MetricDefinition,
    MetricsRegistry,
    MetricType,
    StructuredLogger,
    new_correlation_id,
)

AUTHENTICATION_EVENT_TYPE = "security.authentication"

# Auth outcome counter, registered on a caller-supplied MetricsRegistry.
AUTH_ATTEMPTS_METRIC = MetricDefinition(
    name="auth.attempts_total",
    type=MetricType.COUNTER,
    category=MetricCategory.ERROR,
    description="Authentication attempts by token type and outcome.",
    labels=("token_type", "outcome", "reason"),
    owner="security-audit",
)


@dataclass(frozen=True)
class Credentials:
    """Credentials extracted from an inbound request.

    Exactly one path is taken: a ``bearer_token`` (OIDC user) is tried first,
    then a ``service_id`` + ``service_secret`` (service identity). Absence of
    both is ``NO_CREDENTIALS``.
    """

    bearer_token: str | None = None
    service_id: str | None = None
    service_secret: bytes | None = None
    correlation_id: str | None = None
    source_ip: str | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> Credentials:
        """Extract credentials from HTTP headers (lowercase, Starlette-style)."""

        authorization = headers.get("authorization") or ""
        bearer = None
        if authorization.lower().startswith("bearer "):
            bearer = authorization[7:].strip() or None
        secret = headers.get("x-service-secret")
        return cls(
            bearer_token=bearer,
            service_id=headers.get("x-service-id"),
            service_secret=secret.encode("utf-8") if secret else None,
            correlation_id=headers.get("x-correlation-id"),
            source_ip=None,
        )


@dataclass(frozen=True)
class AuthOutcome:
    """The result of an authentication attempt."""

    authenticated: bool
    principal: Principal
    token_type: str
    correlation_id: str
    reason: AuthFailureReason | None = None
    audit_event: AuditEvent | None = field(default=None, repr=False)

    def raise_for_status(self) -> Principal:
        """Return the principal, or raise :class:`AuthenticationError` on denial."""

        if not self.authenticated and self.reason is not None:
            raise AuthenticationError(self.reason)
        return self.principal


class AuthenticationBoundary:
    """Verifies credentials and emits audit/observability signals."""

    def __init__(
        self,
        config: AuthBoundaryConfig,
        *,
        service_verifier: ServiceIdentityVerifier | None = None,
        audit_log: AuditRecorder | None = None,
        logger: StructuredLogger | None = None,
        metrics: MetricsRegistry | None = None,
        claim_prefix: str = "odp",
        key_resolver: KeyResolver | None = None,
        identity_store: Any = None,
        session_service: Any = None,
    ) -> None:
        self._config = config
        self._services = service_verifier or ServiceIdentityVerifier()
        self._audit = audit_log if audit_log is not None else InMemoryAuditLog()
        self._logger = logger
        self._metrics = metrics
        self._claim_prefix = claim_prefix
        self._identity_store = (
            identity_store if identity_store is not None else config.identity_store
        )
        self._session_service = (
            session_service if session_service is not None else config.session_service
        )

        self._key_resolver = key_resolver
        if self._key_resolver is None and config.jwks_uri:
            self._key_resolver = JwksResolver(
                config.jwks_uri,
                cache_ttl_seconds=config.jwks_cache_ttl_seconds,
            )
        self._oidc_key_resolver = None
        if config.oidc_jwks_uri:
            self._oidc_key_resolver = JwksResolver(
                config.oidc_jwks_uri,
                cache_ttl_seconds=config.jwks_cache_ttl_seconds,
            )
        self._service_key_resolver = None
        if config.service_jwks_uri:
            self._service_key_resolver = JwksResolver(
                config.service_jwks_uri,
                cache_ttl_seconds=config.jwks_cache_ttl_seconds,
            )

        if metrics is not None:
            metrics.register(AUTH_ATTEMPTS_METRIC)

    @property
    def audit_log(self) -> AuditRecorder:
        return self._audit

    def authenticate(self, credentials: Credentials, *, now: datetime | None = None) -> AuthOutcome:
        """Authenticate ``credentials`` and record the decision."""

        correlation_id = credentials.correlation_id or new_correlation_id()
        moment = now or datetime.now(UTC)

        if credentials.bearer_token is not None:
            principal, reason, token_type = self._authenticate_token(
                credentials.bearer_token, moment
            )
        elif credentials.service_id is not None:
            token_type = "service"
            principal, reason = self._authenticate_service(credentials)
        else:
            token_type = "none"
            principal, reason = ANONYMOUS, AuthFailureReason.NO_CREDENTIALS

        return self._finalize(credentials, correlation_id, token_type, principal, reason)

    # -- Token verification (multi-issuer) -----------------------------------

    def _authenticate_token(
        self, token: str, now: datetime
    ) -> tuple[Principal, AuthFailureReason | None, str]:
        if not self._config.is_configured:
            # Fail-closed: no live auth inputs -> trust nothing.
            return ANONYMOUS, AuthFailureReason.BOUNDARY_NOT_CONFIGURED, "unknown"

        try:
            header = decode_header(token)
            unverified_claims = decode_unverified_claims(token)
        except JwtError:
            return ANONYMOUS, AuthFailureReason.MALFORMED_TOKEN, "unknown"

        iss = unverified_claims.get("iss")
        if not isinstance(iss, str) or not iss.strip():
            return ANONYMOUS, AuthFailureReason.MISSING_SUBJECT, "unknown"

        kid = header.get("kid")

        local_issuer = self._config.local_issuer or "urn:odp:identity:local"
        is_local = (iss == local_issuer) or (
            bool(self._config.local_signing_keys) and iss == "urn:odp:identity:local"
        )
        is_oidc = bool(self._config.oidc_issuer and iss == self._config.oidc_issuer)
        is_service = bool(
            (self._config.service_issuer and iss == self._config.service_issuer)
            or (
                not self._config.service_issuer
                and self._config.issuer
                and iss == self._config.issuer
            )
        )
        is_legacy = bool(self._config.issuer and iss == self._config.issuer)

        if is_local:
            return self._authenticate_local_token(token, header, kid, local_issuer, now)
        elif is_oidc:
            return self._authenticate_oidc_token(token, header, kid, now)
        elif is_service or is_legacy:
            return self._authenticate_service_or_legacy_token(token, header, kid, now)
        else:
            return ANONYMOUS, AuthFailureReason.ISSUER_MISMATCH, "unknown"

    def _authenticate_local_token(
        self,
        token: str,
        header: Mapping[str, Any],
        kid: Any,
        local_issuer: str,
        now: datetime,
    ) -> tuple[Principal, AuthFailureReason | None, str]:
        token_type = "local"
        key = self._config.resolve_key(kid if isinstance(kid, str) else None, category="local")
        if key is None:
            return ANONYMOUS, AuthFailureReason.UNKNOWN_KEY, token_type

        try:
            claims = verify_compact_jwt(token, key)
        except UnsupportedAlgorithmError:
            return ANONYMOUS, AuthFailureReason.UNSUPPORTED_ALGORITHM, token_type
        except BadSignatureError:
            return ANONYMOUS, AuthFailureReason.BAD_SIGNATURE, token_type
        except JwtError:
            return ANONYMOUS, AuthFailureReason.MALFORMED_TOKEN, token_type

        reason = self._validate_claims_generic(
            claims,
            now,
            expected_issuer=local_issuer,
            allowed_audiences=self._config.local_audiences or self._config.audiences,
        )
        if reason is not None:
            return ANONYMOUS, reason, token_type

        # Check session validity (Contract §5.4 / T21)
        sid = claims.get("sid")
        if sid is not None and self._session_service is not None:
            from uuid import UUID

            try:
                sid_uuid = UUID(str(sid))
                session = self._session_service.validate_session(sid_uuid)
                if session is None:
                    return ANONYMOUS, AuthFailureReason.SESSION_REVOKED, token_type
            except (ValueError, TypeError):
                return ANONYMOUS, AuthFailureReason.MALFORMED_TOKEN, token_type

        subject = str(claims["sub"])
        if self._identity_store is not None:
            from uuid import UUID

            try:
                aid = UUID(subject)
                account = self._identity_store.find_account_by_id(aid)
            except (ValueError, TypeError):
                account = self._identity_store.find_account_by_id(subject)

            if account is None:
                return ANONYMOUS, AuthFailureReason.ACCOUNT_NOT_FOUND, token_type
            if not account.is_active:
                return ANONYMOUS, AuthFailureReason.ACCOUNT_INACTIVE, token_type

            roles = self._identity_store.get_account_roles(account.account_id)
            scope = self._identity_store.get_account_scope(account.account_id)
            principal = Principal(
                subject_id=str(account.account_id),
                roles=roles,
                scope=scope,
                attributes={
                    "email": account.email,
                    "username": account.username,
                    "tenant_id": str(account.tenant_id),
                    "sid": str(sid) if sid else None,
                    "provider": "local_password",
                    "token_type": "local",
                    "iss": claims.get("iss"),
                },
                authenticated=True,
            )
            return principal, None, token_type
        else:
            return ANONYMOUS, AuthFailureReason.ACCOUNT_NOT_FOUND, token_type

    def _authenticate_oidc_token(
        self,
        token: str,
        header: Mapping[str, Any],
        kid: Any,
        now: datetime,
    ) -> tuple[Principal, AuthFailureReason | None, str]:
        token_type = "oidc"
        key = self._config.resolve_key(kid if isinstance(kid, str) else None, category="oidc")
        if key is None and self._oidc_key_resolver is not None:
            key = self._oidc_key_resolver.resolve(kid if isinstance(kid, str) else None)
        if key is None and self._key_resolver is not None:
            key = self._key_resolver.resolve(kid if isinstance(kid, str) else None)
        if key is None:
            return ANONYMOUS, AuthFailureReason.UNKNOWN_KEY, token_type

        try:
            claims = verify_compact_jwt(token, key)
        except UnsupportedAlgorithmError:
            return ANONYMOUS, AuthFailureReason.UNSUPPORTED_ALGORITHM, token_type
        except BadSignatureError:
            return ANONYMOUS, AuthFailureReason.BAD_SIGNATURE, token_type
        except JwtError:
            return ANONYMOUS, AuthFailureReason.MALFORMED_TOKEN, token_type

        reason = self._validate_claims_generic(
            claims,
            now,
            expected_issuer=self._config.oidc_issuer,
            allowed_audiences=self._config.oidc_audiences or self._config.audiences,
        )
        if reason is not None:
            return ANONYMOUS, reason, token_type

        subject = str(claims["sub"])
        iss = str(claims["iss"])
        if self._identity_store is not None:
            account = self._identity_store.find_account_by_federated_identity(iss, subject)
            if account is None:
                return ANONYMOUS, AuthFailureReason.FEDERATED_IDENTITY_NOT_LINKED, token_type
            if not account.is_active:
                return ANONYMOUS, AuthFailureReason.ACCOUNT_INACTIVE, token_type

            roles = self._identity_store.get_account_roles(account.account_id)
            scope = self._identity_store.get_account_scope(account.account_id)
            principal = Principal(
                subject_id=str(account.account_id),
                roles=roles,
                scope=scope,
                attributes={
                    "email": account.email,
                    "username": account.username,
                    "tenant_id": str(account.tenant_id),
                    "provider": "oidc",
                    "token_type": "oidc",
                    "iss": iss,
                    "sub": subject,
                },
                authenticated=True,
            )
            return principal, None, token_type
        else:
            return ANONYMOUS, AuthFailureReason.FEDERATED_IDENTITY_NOT_LINKED, token_type

    def _authenticate_service_or_legacy_token(
        self,
        token: str,
        header: Mapping[str, Any],
        kid: Any,
        now: datetime,
    ) -> tuple[Principal, AuthFailureReason | None, str]:
        token_type = "service" if self._config.service_issuer else "oidc"
        key = self._config.resolve_key(kid if isinstance(kid, str) else None, category="service")
        if key is None and self._service_key_resolver is not None:
            key = self._service_key_resolver.resolve(kid if isinstance(kid, str) else None)
        if key is None and self._key_resolver is not None:
            key = self._key_resolver.resolve(kid if isinstance(kid, str) else None)
        if key is None:
            return ANONYMOUS, AuthFailureReason.UNKNOWN_KEY, token_type

        try:
            claims = verify_compact_jwt(token, key)
        except UnsupportedAlgorithmError:
            return ANONYMOUS, AuthFailureReason.UNSUPPORTED_ALGORITHM, token_type
        except BadSignatureError:
            return ANONYMOUS, AuthFailureReason.BAD_SIGNATURE, token_type
        except JwtError:
            return ANONYMOUS, AuthFailureReason.MALFORMED_TOKEN, token_type

        expected_iss = self._config.service_issuer or self._config.issuer
        reason = self._validate_claims_generic(
            claims,
            now,
            expected_issuer=expected_iss,
            allowed_audiences=self._config.service_audiences or self._config.audiences,
        )
        if reason is not None:
            return ANONYMOUS, reason, token_type

        subject = str(claims["sub"])
        principal_mapping = self._principal_mapping(claims, subject)
        principal = principal_from_claims(
            claims,
            subject=subject,
            claim_prefix=self._claim_prefix,
            principal_mapping=principal_mapping,
        )
        known_roles = {role.value: role for role in Role}
        bound_roles = frozenset(
            known_roles[role]
            for role in self._config.subject_role_bindings.get(subject, ())
            if role in known_roles
        )
        if bound_roles:
            principal = replace(principal, roles=principal.roles | bound_roles)
        return principal, None, token_type

    def _principal_mapping(
        self, claims: Mapping[str, Any], subject: str
    ) -> Mapping[str, object] | None:
        if not self._config.principal_mapping_declared and not self._config.principal_mappings:
            return None
        direct = self._config.principal_mappings.get(subject)
        if direct is not None:
            return direct
        email = claims.get("email")
        if claims.get("email_verified") is True and isinstance(email, str) and email.strip():
            mapped = self._config.principal_mappings.get(email.strip())
            if mapped is not None:
                return mapped
        return {}

    def _validate_claims_generic(
        self,
        claims: Mapping[str, Any],
        now: datetime,
        *,
        expected_issuer: str | None,
        allowed_audiences: frozenset[str],
    ) -> AuthFailureReason | None:
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            return AuthFailureReason.MISSING_SUBJECT

        if expected_issuer and claims.get("iss") != expected_issuer:
            return AuthFailureReason.ISSUER_MISMATCH

        if not self._audience_ok_for(claims.get("aud"), allowed_audiences):
            return AuthFailureReason.AUDIENCE_MISMATCH

        epoch = now.timestamp()
        leeway = self._config.leeway_seconds

        exp = _as_epoch(claims.get("exp"))
        # A token without a bounded lifetime fails closed.
        if exp is None or epoch > exp + leeway:
            return AuthFailureReason.TOKEN_EXPIRED

        nbf = _as_epoch(claims.get("nbf"))
        if nbf is not None and epoch < nbf - leeway:
            return AuthFailureReason.TOKEN_NOT_YET_VALID

        iat = _as_epoch(claims.get("iat"))
        if iat is not None and epoch < iat - leeway:
            return AuthFailureReason.TOKEN_NOT_YET_VALID

        return None

    def _audience_ok_for(self, aud: Any, allowed: frozenset[str]) -> bool:
        if aud is None:
            return False
        if isinstance(aud, str):
            presented = {aud}
        elif isinstance(aud, (list, tuple, set, frozenset)):
            presented = {str(item) for item in aud}
        else:
            return False
        return bool(presented & set(allowed))

    # -- service identity ---------------------------------------------------

    def _authenticate_service(
        self, credentials: Credentials
    ) -> tuple[Principal, AuthFailureReason | None]:
        result = self._services.verify(credentials.service_id, credentials.service_secret)
        if result.ok and result.principal is not None:
            return result.principal, None
        return ANONYMOUS, result.reason

    # -- audit + observability ---------------------------------------------

    def _finalize(
        self,
        credentials: Credentials,
        correlation_id: str,
        token_type: str,
        principal: Principal,
        reason: AuthFailureReason | None,
    ) -> AuthOutcome:
        authenticated = reason is None
        outcome_label = "success" if authenticated else "failure"
        metadata: dict[str, Any] = {
            "token_type": token_type,
            "reason": reason.value if reason else None,
            "source_ip": credentials.source_ip,
            "issuer": self._config.issuer
            or self._config.local_issuer
            or self._config.oidc_issuer
            or self._config.service_issuer,
        }
        if principal.tenant_id:
            metadata["tenant_id"] = principal.tenant_id

        event = AuditEvent(
            event_type=AUTHENTICATION_EVENT_TYPE,
            actor=principal.subject_id,
            action="authenticate",
            resource=f"auth/{token_type}",
            outcome=outcome_label,
            correlation_id=correlation_id,
            metadata=metadata,
        )
        self._audit.record(event)

        if self._metrics is not None:
            self._metrics.increment(
                AUTH_ATTEMPTS_METRIC.name,
                labels={
                    "token_type": token_type,
                    "outcome": outcome_label,
                    "reason": reason.value if reason else "ok",
                },
            )

        if self._logger is not None and not authenticated:
            self._logger.warning(
                "authentication denied",
                correlation_id=correlation_id,
                actor=principal.subject_id,
                result="deny",
                reason=reason.value if reason else None,
                token_type=token_type,
            )

        return AuthOutcome(
            authenticated=authenticated,
            principal=principal,
            token_type=token_type,
            correlation_id=correlation_id,
            reason=reason,
            audit_event=event,
        )


def _as_epoch(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
