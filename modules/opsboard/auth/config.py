"""Auth-boundary configuration and the fail-closed configuration gate.

ODP-GAP-AUTH-001 acceptance: *fail-closed when external live inputs are
absent*. The "live inputs" for the auth boundary are:

- the OIDC issuer + audience the platform trusts, and
- the signing keys (JWKS-equivalent) used to verify tokens.

:class:`AuthBoundaryConfig` is only :attr:`is_configured` when those inputs are
present. An unconfigured boundary denies *every* user-token request rather than
falling back to the insecure header-trust stub (``principal_from_headers``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from modules.opsboard.auth.jwt import SigningKey
from shared.auth.mode import API_OIDC_ISSUER_VARS, oidc_provider_enabled


@dataclass(frozen=True)
class AuthBoundaryConfig:
    """Trusted issuer, audiences, signing keys, and validation leeway.

    Supports multi-issuer authentication (ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §4.3):
    - local issuer: ODP_AUTH_LOCAL_ISSUER (urn:odp:identity:local) + local signing key
    - oidc issuer: ODP_AUTH_OIDC_ISSUER + OIDC JWKS URI / keys
    - service issuer: ODP_AUTH_SERVICE_ISSUER + service JWKS URI / keys
    - legacy single-issuer config for backwards compatibility
    """

    issuer: str | None = None
    issuers: frozenset[str] = frozenset()
    audiences: frozenset[str] = frozenset()
    signing_keys: Mapping[str, SigningKey] = field(default_factory=dict)
    jwks_uri: str | None = None
    jwks_cache_ttl_seconds: int = 300
    leeway_seconds: int = 60
    live_input_declared: bool = False
    subject_role_bindings: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # Informational only: records whether ODP_AUTH_PRINCIPAL_MAP was set at
    # all. It is deliberately *not* a trust switch. It used to select between
    # "mapping is authoritative" and "read roles off the token", which is what
    # let an undeclared subject self-assign roles
    # (ODP-WEB-LOCAL-AUTH-API-TRUST-001). The boundary now always requires a
    # declaration in principal_mappings or subject_role_bindings.
    principal_mapping_declared: bool = False
    principal_mappings: Mapping[str, Mapping[str, object]] = field(default_factory=dict)

    # Multi-issuer extension fields
    local_issuer: str | None = None
    local_signing_keys: Mapping[str, SigningKey] = field(default_factory=dict)
    local_audiences: frozenset[str] = frozenset()
    oidc_issuer: str | None = None
    oidc_signing_keys: Mapping[str, SigningKey] = field(default_factory=dict)
    oidc_jwks_uri: str | None = None
    oidc_audiences: frozenset[str] = frozenset()
    service_issuer: str | None = None
    service_signing_keys: Mapping[str, SigningKey] = field(default_factory=dict)
    service_jwks_uri: str | None = None
    service_audiences: frozenset[str] = frozenset()
    identity_store: Any = None
    session_service: Any = None
    # Whether the deployment *authoritatively* enables the OIDC provider
    # (ODP_AUTH_MODE / ODP_AUTH_OIDC_ENABLED). Defaults to True so a config
    # built directly in code keeps its OIDC fields meaningful; only
    # :func:`config_from_env` consults the deployment gate.
    oidc_enabled: bool = True

    @property
    def trusted_issuers(self) -> frozenset[str]:
        """All trusted issuers (composed from `issuer` and `issuers`)."""
        items = set(self.issuers)
        if self.issuer:
            items.add(self.issuer)
        return frozenset(items)

    @property
    def is_configured(self) -> bool:
        """True only when live inputs required to verify at least one token class exist."""
        # Local provider configured
        effective_local_issuer = self.local_issuer or "urn:odp:identity:local"
        effective_audiences = (
            self.local_audiences or self.audiences or self.service_audiences or self.oidc_audiences
        )
        if (
            bool(effective_local_issuer)
            and bool(self.local_signing_keys)
            and bool(effective_audiences)
        ):
            return True

        # OIDC provider configured. A provider the deployment turned off is not
        # a configuration, however complete its leftover inputs look
        # (ODP-WEB-LOCAL-AUTH-API-TRUST-001).
        if (
            self.oidc_enabled
            and bool(self.oidc_issuer)
            and (bool(self.oidc_audiences) or bool(self.audiences))
            and (bool(self.oidc_signing_keys) or bool(self.oidc_jwks_uri))
        ):
            return True

        # Service provider configured
        if (
            bool(self.service_issuer)
            and (bool(self.service_audiences) or bool(self.audiences))
            and (bool(self.service_signing_keys) or bool(self.service_jwks_uri))
        ):
            return True

        # Legacy single-issuer configured
        return (
            bool(self.trusted_issuers)
            and bool(self.audiences)
            and (bool(self.signing_keys) or bool(self.jwks_uri))
        )

    @property
    def has_live_inputs(self) -> bool:
        """True when *any* live auth input is present (even a partial set)."""
        return (
            self.live_input_declared
            or bool(self.trusted_issuers)
            or bool(self.audiences)
            or bool(self.signing_keys)
            or bool(self.jwks_uri)
            or bool(self.local_issuer)
            or bool(self.local_signing_keys)
            or bool(self.local_audiences)
            or bool(self.oidc_issuer)
            or bool(self.oidc_signing_keys)
            or bool(self.oidc_jwks_uri)
            or bool(self.oidc_audiences)
            or bool(self.service_issuer)
            or bool(self.service_signing_keys)
            or bool(self.service_jwks_uri)
            or bool(self.service_audiences)
        )

    def resolve_key(self, kid: str | None, *, category: str | None = None) -> SigningKey | None:
        """Resolve a verification key by ``kid`` (fail-closed on miss)."""
        keys_pool: Mapping[str, SigningKey]
        if category == "local":
            keys_pool = self.local_signing_keys or self.signing_keys
        elif category == "oidc":
            keys_pool = self.oidc_signing_keys
        elif category == "service":
            keys_pool = self.service_signing_keys or self.signing_keys
        else:
            # Combined / default
            keys_pool = (
                self.signing_keys
                or self.local_signing_keys
                or self.service_signing_keys
                or self.oidc_signing_keys
            )

        if kid is not None:
            return dict(keys_pool).get(kid)
        keys = list(keys_pool.values())
        if len(keys) == 1:
            return keys[0]
        return None


def config_from_env(
    env: Mapping[str, str] | None = None,
    *,
    identity_store: Any = None,
    session_service: Any = None,
) -> AuthBoundaryConfig:
    """Build a config from environment variables (fail-closed by default).

    Recognised keys:
    - ODP_AUTH_LOCAL_ISSUER, ODP_IDENTITY_TOKEN_SIGNING_KEY, ODP_AUTH_LOCAL_HS256_KEYS
    - ODP_AUTH_OIDC_ISSUER, ODP_AUTH_OIDC_AUDIENCES, ODP_AUTH_OIDC_JWKS_URI
    - ODP_AUTH_SERVICE_ISSUER, ODP_AUTH_SERVICE_JWKS_URI
    - ODP_AUTH_ISSUER, ODP_AUTH_AUDIENCES, ODP_AUTH_HS256_KEYS, ODP_AUTH_JWKS_URI
    - ODP_AUTH_PRINCIPAL_MAP, ODP_AUTH_LEEWAY_SECONDS, ODP_AUTH_JWKS_CACHE_TTL_SECONDS
    - ODP_AUTH_MODE / ODP_AUTH_OIDC_ENABLED (the authoritative OIDC gate)

    - ``ODP_AUTH_ISSUER``
    - ``ODP_AUTH_LOCAL_ISSUER``
    - ``ODP_AUTH_OIDC_ISSUER``
    - ``ODP_AUTH_SERVICE_ISSUER``
    - ``ODP_AUTH_AUDIENCES`` (comma-separated)
    - ``ODP_AUTH_LOCAL_AUDIENCES`` (comma-separated)
    - ``ODP_AUTH_HS256_KEYS`` (``kid:secret`` pairs, comma-separated; local/test)
    - ``ODP_AUTH_JWKS_URI`` (production IdP JSON Web Key Set endpoint)
    - ``ODP_AUTH_JWKS_CACHE_TTL_SECONDS``
    - ``ODP_AUTH_LEEWAY_SECONDS``
    - ``ODP_AUTH_PRINCIPAL_MAP`` (deployment-owned JSON keyed by subject/email)
    - ``ODP_IDENTITY_TOKEN_SIGNING_KEY`` (Secret Manager injected local key)

    Only symmetric (HS256) keys are read from the environment; asymmetric JWKS
    material is injected programmatically via :class:`AuthBoundaryConfig` so
    secrets are not required to live in process env in production.

    The OIDC inputs are only accepted when the deployment mode enables the OIDC
    provider; see :mod:`shared.auth.mode`.
    """
    source = os.environ if env is None else env

    # Collect trusted issuers for the legacy trusted_issuers set
    trusted_issuers: set[str] = set()
    for var in (
        "ODP_AUTH_ISSUER",
        "ODP_AUTH_LOCAL_ISSUER",
        "ODP_AUTH_OIDC_ISSUER",
        "ODP_AUTH_SERVICE_ISSUER",
    ):
        val = (source.get(var) or "").strip()
        if val:
            trusted_issuers.add(val)

    identity_token_key = (source.get("ODP_IDENTITY_TOKEN_SIGNING_KEY") or "").strip()
    if identity_token_key:
        local_iss = (source.get("ODP_AUTH_LOCAL_ISSUER") or "").strip() or "urn:odp:identity:local"
        trusted_issuers.add(local_iss)

    primary_issuer = (
        (source.get("ODP_AUTH_ISSUER") or "").strip()
        or (source.get("ODP_AUTH_LOCAL_ISSUER") or "").strip()
        or (source.get("ODP_AUTH_OIDC_ISSUER") or "").strip()
        or (source.get("ODP_AUTH_SERVICE_ISSUER") or "").strip()
        or (next(iter(trusted_issuers)) if trusted_issuers else None)
    )

    # Audiences
    audiences = frozenset(_split_csv(source.get("ODP_AUTH_AUDIENCES")))
    oidc_audiences = frozenset(_split_csv(source.get("ODP_AUTH_OIDC_AUDIENCES")))
    service_audiences = frozenset(_split_csv(source.get("ODP_AUTH_SERVICE_AUDIENCES")))
    local_audiences = frozenset(_split_csv(source.get("ODP_AUTH_LOCAL_AUDIENCES")))

    jwks_uri = (source.get("ODP_AUTH_JWKS_URI") or "").strip() or None

    # Legacy / shared keys
    keys: dict[str, SigningKey] = {}
    for pair in _split_csv(source.get("ODP_AUTH_HS256_KEYS")):
        kid, sep, secret = pair.partition(":")
        if not sep or not kid or not secret:
            continue
        keys[kid] = SigningKey(kid=kid, algorithm="HS256", secret=secret.encode("utf-8"))

    # Local keys
    local_keys: dict[str, SigningKey] = {}
    local_signing_key_raw = (source.get("ODP_IDENTITY_TOKEN_SIGNING_KEY") or "").strip()
    if local_signing_key_raw:
        if ":" in local_signing_key_raw:
            for pair in _split_csv(local_signing_key_raw):
                kid, sep, secret = pair.partition(":")
                if sep and kid and secret:
                    local_keys[kid] = SigningKey(
                        kid=kid, algorithm="HS256", secret=secret.encode("utf-8")
                    )
        else:
            local_keys["local-default"] = SigningKey(
                kid="local-default",
                algorithm="HS256",
                secret=local_signing_key_raw.encode("utf-8"),
            )
    for pair in _split_csv(source.get("ODP_AUTH_LOCAL_HS256_KEYS")):
        kid, sep, secret = pair.partition(":")
        if sep and kid and secret:
            local_keys[kid] = SigningKey(kid=kid, algorithm="HS256", secret=secret.encode("utf-8"))

    local_issuer_raw = source.get("ODP_AUTH_LOCAL_ISSUER")
    local_issuer = (
        local_issuer_raw.strip()
        if local_issuer_raw
        else (
            "urn:odp:identity:local"
            if local_keys or "ODP_IDENTITY_TOKEN_SIGNING_KEY" in source
            else None
        )
    )

    # OIDC config, gated on the authoritative deployment mode. ODP_AUTH_MODE
    # (or its legacy ODP_AUTH_OIDC_ENABLED alias) decides whether this
    # deployment runs the OIDC provider at all; without the gate, an
    # environment that selected password-first still carried its previous OIDC
    # issuer/JWKS values, and the boundary went on verifying and trusting
    # OIDC-issued tokens that the deployment had turned off
    # (ODP-WEB-LOCAL-AUTH-API-TRUST-001). An invalid or self-contradicting mode
    # resolves to "not enabled" so a broken configuration narrows trust rather
    # than widening it.
    #
    # Deployment-shape fallback (ODP-WEB-LOCAL-AUTH-API-TRUST-001 reopen #5):
    # Terraform sends only the global ODP_AUTH_ISSUER / ODP_AUTH_JWKS_URI /
    # ODP_AUTH_AUDIENCES to the API runtime and never the OIDC-specific
    # variables. When ODP_AUTH_MODE=oidc, the global issuer IS the OIDC
    # provider, so the OIDC path must claim it. Without this fallback a token
    # from the OIDC provider matched service_issuer instead and went through
    # principal_from_claims (trusting token roles) rather than through the
    # identity-store lookup in _authenticate_oidc_token.
    oidc_enabled = oidc_provider_enabled(source, oidc_issuer_vars=API_OIDC_ISSUER_VARS)
    oidc_issuer_explicit = (source.get("ODP_AUTH_OIDC_ISSUER") or "").strip() or None
    oidc_jwks_uri_explicit = (source.get("ODP_AUTH_OIDC_JWKS_URI") or "").strip() or None
    if oidc_enabled:
        # Fall back to global issuer/JWKS/audiences when OIDC-specific vars
        # are absent — this matches the actual Terraform deployment shape.
        oidc_issuer = (
            oidc_issuer_explicit
            or (source.get("ODP_AUTH_ISSUER") or "").strip()
            or None
        )
        oidc_jwks_uri = (
            oidc_jwks_uri_explicit
            or (source.get("ODP_AUTH_JWKS_URI") or "").strip()
            or None
        )
        if not oidc_audiences:
            oidc_audiences = audiences
    else:
        oidc_issuer = None
        oidc_jwks_uri = None
        oidc_audiences = frozenset()

    # Service config. When OIDC is enabled, the global ODP_AUTH_ISSUER belongs
    # to the OIDC provider path, so service_issuer must NOT fall back to it —
    # otherwise a token from the OIDC provider matches is_service instead of
    # is_oidc and skips the identity-store lookup. Service tokens must use the
    # explicit ODP_AUTH_SERVICE_ISSUER or remain unconfigured.
    if oidc_enabled:
        service_issuer = (source.get("ODP_AUTH_SERVICE_ISSUER") or "").strip() or None
    else:
        service_issuer = (
            (source.get("ODP_AUTH_SERVICE_ISSUER") or "").strip()
            or (source.get("ODP_AUTH_ISSUER") or "").strip()
            or None
        )
    service_jwks_uri = (
        (source.get("ODP_AUTH_SERVICE_JWKS_URI") or "").strip()
        or (source.get("ODP_AUTH_JWKS_URI") or "").strip()
        or None
    )


    principal_mapping_value = source.get("ODP_AUTH_PRINCIPAL_MAP")
    principal_mappings = _parse_principal_mappings(principal_mapping_value)

    live_input_declared = any(
        (source.get(var) or "").strip()
        for var in (
            "ODP_AUTH_ISSUER",
            "ODP_AUTH_LOCAL_ISSUER",
            "ODP_AUTH_OIDC_ISSUER",
            "ODP_AUTH_SERVICE_ISSUER",
            "ODP_AUTH_AUDIENCES",
            "ODP_AUTH_LOCAL_AUDIENCES",
            "ODP_AUTH_HS256_KEYS",
            "ODP_AUTH_JWKS_URI",
            "ODP_IDENTITY_TOKEN_SIGNING_KEY",
            "ODP_AUTH_LOCAL_HS256_KEYS",
            "ODP_AUTH_OIDC_ISSUER",
            "ODP_AUTH_OIDC_JWKS_URI",
            "ODP_AUTH_OIDC_AUDIENCES",
            "ODP_AUTH_SERVICE_ISSUER",
            "ODP_AUTH_SERVICE_JWKS_URI",
            "ODP_AUTH_SERVICE_AUDIENCES",
        )
    )

    leeway_raw = source.get("ODP_AUTH_LEEWAY_SECONDS")
    try:
        leeway = int(leeway_raw) if leeway_raw else 60
    except ValueError:
        leeway = 60

    jwks_ttl_raw = source.get("ODP_AUTH_JWKS_CACHE_TTL_SECONDS")
    try:
        jwks_ttl = int(jwks_ttl_raw) if jwks_ttl_raw else 300
    except ValueError:
        jwks_ttl = 300

    subject_role_bindings: dict[str, frozenset[str]] = {}
    raw_bindings = (source.get("ODP_AUTH_SUBJECT_ROLE_BINDINGS") or "").strip()
    if raw_bindings:
        try:
            parsed_bindings = json.loads(raw_bindings)
            if isinstance(parsed_bindings, dict):
                for subject, roles in parsed_bindings.items():
                    if isinstance(subject, str) and isinstance(roles, list):
                        subject_role_bindings[subject] = frozenset(
                            role for role in roles if isinstance(role, str) and role
                        )
        except json.JSONDecodeError:
            pass

    return AuthBoundaryConfig(
        issuer=primary_issuer,
        issuers=frozenset(trusted_issuers),
        audiences=audiences,
        signing_keys=keys,
        jwks_uri=jwks_uri,
        jwks_cache_ttl_seconds=max(30, jwks_ttl),
        leeway_seconds=max(0, leeway),
        live_input_declared=live_input_declared,
        subject_role_bindings=subject_role_bindings,
        principal_mapping_declared=principal_mapping_value is not None,
        principal_mappings=principal_mappings,
        local_issuer=local_issuer,
        local_signing_keys=local_keys,
        local_audiences=local_audiences,
        oidc_issuer=oidc_issuer,
        oidc_jwks_uri=oidc_jwks_uri,
        oidc_audiences=oidc_audiences,
        oidc_enabled=oidc_enabled,
        service_issuer=service_issuer,
        service_signing_keys=keys,
        service_jwks_uri=service_jwks_uri,
        service_audiences=service_audiences,
        identity_store=identity_store,
        session_service=session_service,
    )


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_principal_mappings(
    value: str | None,
) -> dict[str, Mapping[str, object]]:
    """Parse deployment-owned mappings without widening authorization on errors."""

    if not value:
        return {}
    try:
        payload = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        identifier.strip(): attributes
        for identifier, attributes in payload.items()
        if isinstance(identifier, str) and identifier.strip() and isinstance(attributes, dict)
    }
