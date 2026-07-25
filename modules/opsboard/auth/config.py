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

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from modules.opsboard.auth.jwt import SigningKey


@dataclass(frozen=True)
class AuthBoundaryConfig:
    """Trusted issuer, audiences, signing keys, and validation leeway.

    ``signing_keys`` is keyed by ``kid``. A token whose header names an unknown
    ``kid`` fails closed. When exactly one key is configured and a token omits
    ``kid``, that single key is used (common single-tenant IdP case).
    """

    issuer: str | None = None
    audiences: frozenset[str] = frozenset()
    signing_keys: Mapping[str, SigningKey] = field(default_factory=dict)
    jwks_uri: str | None = None
    jwks_cache_ttl_seconds: int = 300
    leeway_seconds: int = 60
    live_input_declared: bool = False

    @property
    def is_configured(self) -> bool:
        """True only when the live IdP inputs required to verify a token exist."""

        return (
            bool(self.issuer)
            and bool(self.audiences)
            and (bool(self.signing_keys) or bool(self.jwks_uri))
        )

    @property
    def has_live_inputs(self) -> bool:
        """True when *any* live IdP input is present (even a partial set).

        Distinct from :attr:`is_configured`: a partial config (e.g. issuer +
        audiences but no signing keys) is *not* configured, yet it still signals
        the deployer's **intent** to run a live auth boundary. Callers use this
        to decide whether the boundary is active at all -- an active-but-partial
        boundary fails closed on every request, whereas treating a partial
        config as "no boundary" would silently re-enable the insecure
        header-trust stub (ODP-FIN-AUTH-001).

        :attr:`live_input_declared` is honoured in addition to the parsed
        fields: an ``ODP_AUTH_*`` env var that was *set* but failed to parse
        into a usable field (e.g. a malformed ``ODP_AUTH_HS256_KEYS=k1`` with no
        ``kid:secret`` pair) still counts as intent, so a deployer typo fails
        closed instead of silently downgrading to header trust.
        """

        return (
            self.live_input_declared
            or bool(self.issuer)
            or bool(self.audiences)
            or bool(self.signing_keys)
            or bool(self.jwks_uri)
        )

    def resolve_key(self, kid: str | None) -> SigningKey | None:
        """Resolve a verification key by ``kid`` (fail-closed on miss).

        Returns ``None`` when no key matches -- the boundary maps that to
        :class:`~modules.opsboard.auth.errors.AuthFailureReason.UNKNOWN_KEY`.
        """

        if kid is not None:
            return dict(self.signing_keys).get(kid)
        keys = list(self.signing_keys.values())
        if len(keys) == 1:
            return keys[0]
        return None


def config_from_env(
    env: Mapping[str, str] | None = None,
) -> AuthBoundaryConfig:
    """Build a config from environment variables (fail-closed by default).

    Recognised keys (all optional; absence yields an unconfigured, fail-closed
    boundary):

    - ``ODP_AUTH_ISSUER``
    - ``ODP_AUTH_AUDIENCES`` (comma-separated)
    - ``ODP_AUTH_HS256_KEYS`` (``kid:secret`` pairs, comma-separated; local/test)
    - ``ODP_AUTH_JWKS_URI`` (production IdP JSON Web Key Set endpoint)
    - ``ODP_AUTH_JWKS_CACHE_TTL_SECONDS``
    - ``ODP_AUTH_LEEWAY_SECONDS``

    Only symmetric (HS256) keys are read from the environment; asymmetric JWKS
    material is injected programmatically via :class:`AuthBoundaryConfig` so
    secrets are not required to live in process env in production.
    """

    source = os.environ if env is None else env
    issuer = source.get("ODP_AUTH_ISSUER") or None
    audiences = frozenset(_split_csv(source.get("ODP_AUTH_AUDIENCES")))
    jwks_uri = (source.get("ODP_AUTH_JWKS_URI") or "").strip() or None
    keys: dict[str, SigningKey] = {}
    for pair in _split_csv(source.get("ODP_AUTH_HS256_KEYS")):
        kid, sep, secret = pair.partition(":")
        if not sep or not kid or not secret:
            continue
        keys[kid] = SigningKey(kid=kid, algorithm="HS256", secret=secret.encode("utf-8"))
    # Record raw live-input presence *before* parsing can discard it. A set but
    # malformed ODP_AUTH_HS256_KEYS (or an issuer/audiences typo) must keep the
    # boundary active and fail closed, never downgrade to header trust
    # (ODP-FIN-AUTH-001). Parsed-field checks alone miss a value that dropped
    # during parsing.
    live_input_declared = any(
        (source.get(var) or "").strip()
        for var in (
            "ODP_AUTH_ISSUER",
            "ODP_AUTH_AUDIENCES",
            "ODP_AUTH_HS256_KEYS",
            "ODP_AUTH_JWKS_URI",
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
    return AuthBoundaryConfig(
        issuer=issuer,
        audiences=audiences,
        signing_keys=keys,
        jwks_uri=jwks_uri,
        jwks_cache_ttl_seconds=max(30, jwks_ttl),
        leeway_seconds=max(0, leeway),
        live_input_declared=live_input_declared,
    )


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]
