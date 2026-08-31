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
from uuid import UUID

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
        key_resolver: KeyResolver | None = None,
        identity_store: Any = None,
        session_service: Any = None,
    ) -> None:
        self._config = config
        self._services = service_verifier or ServiceIdentityVerifier()
        self._audit = audit_log if audit_log is not None else InMemoryAuditLog()
        self._logger = logger
        self._metrics = metrics
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
        # The deployment mode gate is checked before the issuer match, not
        # after: a disabled OIDC provider must not reach key resolution or
        # claim validation at all (ODP-WEB-LOCAL-AUTH-API-TRUST-001).
        is_oidc = bool(
            self._config.oidc_enabled
            and self._config.oidc_issuer
            and iss == self._config.oidc_issuer
        )
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
        elif is_oidc and is_service:
            # Issuer collision: service_issuer == oidc_issuer (e.g. both
            # https://accounts.google.com).  Disambiguate with a verified,
            # fail-closed criterion: if the token's sub is pre-declared in
            # ODP_AUTH_PRINCIPAL_MAP, treat it as a service token; otherwise
            # route to the OIDC identity-store lookup.  A service identity
            # that is *not* declared in the principal map is intentionally
            # rejected rather than silently promoted to an OIDC user or
            # silently granted service-level roles.
            #
            # The check is on unverified claims (sub parsed from the JWT body
            # before signature verification).  This is safe because the actual
            # token signature and claims are validated inside the downstream
            # handler — _authenticate_service_or_legacy_token or
            # _authenticate_oidc_token — which will reject a forged token
            # regardless of the routing decision made here.
            subject = unverified_claims.get("sub")
            if isinstance(subject, str) and self._is_declared_service_identity(subject):
                return self._authenticate_service_or_legacy_token(token, header, kid, now)
            return self._authenticate_oidc_token(token, header, kid, now)
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

        # Contract §4.3 required claims: sub, sid, iat, exp, tenant_id.
        # sub/iat/exp are already enforced by _validate_claims_generic; sid and
        # tenant_id are required here, independently of what is injected.
        sid = claims.get("sid")
        if not isinstance(sid, str) or not sid.strip():
            return ANONYMOUS, AuthFailureReason.MISSING_REQUIRED_CLAIM, token_type
        token_tenant = claims.get("tenant_id")
        if not isinstance(token_tenant, str) or not token_tenant.strip():
            return ANONYMOUS, AuthFailureReason.MISSING_REQUIRED_CLAIM, token_type

        try:
            sid_uuid = UUID(sid.strip())
            account_uuid = UUID(str(claims["sub"]))
            tenant_uuid = UUID(token_tenant.strip())
        except (ValueError, TypeError):
            # Malformed identifiers fail closed with a stable 401; a ValueError
            # must never escape the boundary.
            return ANONYMOUS, AuthFailureReason.MALFORMED_TOKEN, token_type

        # Server-side session trust is not optional (Contract §5.4). Without a
        # session verifier the boundary cannot establish that this token still
        # maps to a live session, so it denies instead of trusting the claim.
        if self._session_service is None or self._identity_store is None:
            return ANONYMOUS, AuthFailureReason.BOUNDARY_NOT_CONFIGURED, token_type

        session = self._session_service.validate_session(sid_uuid)
        if session is None:
            return ANONYMOUS, AuthFailureReason.SESSION_REVOKED, token_type
        # Same-identity/session binding: the session must belong to the token's
        # subject and have been issued by the local password provider. Without
        # this, sub=A presented with an active sid owned by account B passes.
        if session.account_id != account_uuid:
            return ANONYMOUS, AuthFailureReason.SESSION_NOT_FOUND, token_type
        if session.provider != "local_password":
            return ANONYMOUS, AuthFailureReason.SESSION_NOT_FOUND, token_type

        account = self._identity_store.find_account_by_id(account_uuid)
        if account is None:
            return ANONYMOUS, AuthFailureReason.ACCOUNT_NOT_FOUND, token_type
        if not account.is_active:
            return ANONYMOUS, AuthFailureReason.ACCOUNT_INACTIVE, token_type
        # The tenant claim is never an authorization fact on its own: it must
        # agree with the authoritative account row, and the row wins.
        if account.tenant_id != tenant_uuid:
            return ANONYMOUS, AuthFailureReason.TENANT_MISMATCH, token_type

        principal = Principal(
            subject_id=str(account.account_id),
            roles=self._identity_store.get_account_roles(account.account_id),
            scope=self._identity_store.get_account_scope(account.account_id),
            attributes={
                "email": account.email,
                "username": account.username,
                "tenant_id": str(account.tenant_id),
                "sid": str(sid_uuid),
                "provider": "local_password",
                "token_type": "local",
                "iss": claims.get("iss"),
            },
            authenticated=True,
        )
        return principal, None, token_type

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

        # Contract §4.4: for the service issuer class -- and for the legacy
        # ODP_AUTH_ISSUER alias, which §8.4 defines as an alias of
        # ODP_AUTH_SERVICE_ISSUER -- the token's `sub` / verified `email` is
        # only the *identity*. Roles and scope come exclusively from the
        # authoritative server-side declaration (ODP_AUTH_PRINCIPAL_MAP, or
        # ODP_AUTH_SUBJECT_ROLE_BINDINGS as a secondary role-grant surface).
        # Token claims are never authorization facts
        # (ODP-WEB-LOCAL-AUTH-API-TRUST-001).
        #
        # An *undeclared* subject therefore fails closed with UNKNOWN_SERVICE
        # rather than authenticating with claim-derived privileges. Before
        # this gate, a signed token from the service/legacy issuer carrying
        # `roles: ["platform_admin"]` and an attacker-chosen `tenant_id` was
        # authenticated and kept those claims -- reachable in production
        # because in local mode config_from_env aliases the shared
        # ODP_AUTH_ISSUER (https://accounts.google.com under the real
        # Terraform shape) to service_issuer.
        declared_mapping = self._declared_service_mapping(claims, subject)
        bound_roles = self._bound_service_roles(subject)
        if declared_mapping is None and not bound_roles:
            return ANONYMOUS, AuthFailureReason.UNKNOWN_SERVICE, token_type

        principal = principal_from_claims(
            claims,
            subject=subject,
            # The mapping is the sole role/scope source; the token's own
            # roles/tenant/scope claims are ignored.
            principal_mapping=declared_mapping or {},
        )
        if bound_roles:
            principal = replace(principal, roles=principal.roles | bound_roles)
        return principal, None, token_type

    def _declared_service_mapping(
        self, claims: Mapping[str, Any], subject: str
    ) -> Mapping[str, object] | None:
        """Return the authoritative principal declaration for *subject*.

        Looks the verified token up in ``ODP_AUTH_PRINCIPAL_MAP`` by ``sub``
        first, then by a **verified** ``email`` (an unverified email claim is
        attacker-controlled and is never used as a lookup key).

        Returns ``None`` when the subject is not declared at all. Callers must
        treat ``None`` as fail-closed: contract §4.4 makes
        ``ODP_AUTH_PRINCIPAL_MAP`` the only role/scope source for the service
        issuer class, so an undeclared subject has no authorization facts and
        must not be authenticated from its own claims.

        An empty mapping (``{}``) is a *declared* identity with no roles and
        no scope, which is different from ``None`` and is allowed.
        """
        direct = self._config.principal_mappings.get(subject)
        if direct is not None:
            return direct
        email = claims.get("email")
        if claims.get("email_verified") is True and isinstance(email, str) and email.strip():
            mapped = self._config.principal_mappings.get(email.strip())
            if mapped is not None:
                return mapped
        return None

    def _bound_service_roles(self, subject: str) -> frozenset[Role]:
        """Roles granted to *subject* by ``ODP_AUTH_SUBJECT_ROLE_BINDINGS``.

        A secondary authoritative grant surface alongside the principal map.
        Unknown role ids are dropped rather than trusted.
        """
        known_roles = {role.value: role for role in Role}
        return frozenset(
            known_roles[role]
            for role in self._config.subject_role_bindings.get(subject, ())
            if role in known_roles
        )

    def _is_declared_service_identity(self, subject: str) -> bool:
        """Return True when *subject* is pre-declared as a service identity.

        Used by the issuer-collision branch to route a token whose issuer
        matches both the OIDC and service paths.  A subject is considered a
        declared service identity when it appears in either:

        * ``ODP_AUTH_PRINCIPAL_MAP`` (``config.principal_mappings``) — the
          canonical way to grant roles/scope to a service account, or
        * ``config.subject_role_bindings`` — a secondary role-grant surface.

        The check is intentionally on the *unverified* ``sub`` claim, which is
        safe because the downstream handler still performs full signature and
        claims validation.  Fail-closed: an undeclared subject is never
        promoted to a service principal; it falls through to the OIDC
        identity-store lookup instead.
        """
        if subject in self._config.principal_mappings:
            return True
        if subject in self._config.subject_role_bindings:
            return True
        return False

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

        # iat is required — a token without an issued-at timestamp fails
        # closed (review defect #3: ODP-WEB-LOCAL-AUTH-API-TRUST-001).
        iat = _as_epoch(claims.get("iat"))
        if iat is None:
            return AuthFailureReason.MALFORMED_TOKEN
        if epoch < iat - leeway:
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
