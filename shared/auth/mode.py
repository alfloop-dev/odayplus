"""The single authoritative resolver for "is the OIDC provider on?".

Password-first is the deployable default and OIDC is an optional provider
(ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001, ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001).
Three consumers have to agree on that answer:

* ``product_ops/deployment/auth_mode.sh`` -- the release path,
* ``product_ops/deployment/validate_cloud_run_live_deployment.py`` -- the
  fail-closed preflight, and
* :func:`modules.opsboard.auth.config.config_from_env` -- the API's own auth
  boundary, which decides whether an OIDC-issued token is verifiable at all.

The first two were already pinned to each other by
``tests/ops/test_conditional_oidc_deployment.py``; the API boundary was not, so
``ODP_AUTH_MODE=local`` plus leftover OIDC inputs disabled OIDC for the deploy
and left the API accepting OIDC tokens anyway
(ODP-WEB-LOCAL-AUTH-API-TRUST-001). This module is the shared half so a mode
decision cannot differ between the release and the running service.

The shell resolver is deliberately not generated from this file -- it runs
before Python is known to be usable on the runner -- so it stays a hand-written
mirror, kept honest by the ops test that executes both halves over the same
matrix of environments.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

AUTH_MODES = ("local", "oidc")

# Values that look configured but are not. Kept byte-identical to
# ``AUTH_MODE_PLACEHOLDER_VALUES`` in product_ops/deployment/auth_mode.sh; the
# empty string is in this set and handled separately by the shell half because
# it cannot survive word splitting there.
PLACEHOLDER_VALUES = {
    "",
    "changeme",
    "change-me",
    "dummy",
    "example",
    "fixture",
    "mock",
    "placeholder",
    "seed",
    "todo",
}

# The pre-contract signal for the release path: deployments that predate
# ODP_AUTH_MODE only ever set the Web OIDC inputs.
DEPLOYMENT_OIDC_ISSUER_VARS: tuple[str, ...] = ("ODP_WEB_OIDC_ISSUER",)

# The API process never receives ODP_WEB_OIDC_ISSUER; its own pre-contract
# signal is the boundary's OIDC issuer. Both are accepted there so an existing
# API deployment that configured OIDC without an explicit mode keeps working.
API_OIDC_ISSUER_VARS: tuple[str, ...] = (
    "ODP_AUTH_OIDC_ISSUER",
    "ODP_WEB_OIDC_ISSUER",
)


def is_configured_value(value: str) -> bool:
    """True when ``value`` is a real configuration rather than a placeholder."""

    return value.strip().lower() not in PLACEHOLDER_VALUES


def resolve_auth_mode(
    env: Mapping[str, str],
    *,
    oidc_issuer_vars: Sequence[str] = DEPLOYMENT_OIDC_ISSUER_VARS,
) -> tuple[str, str | None]:
    """Resolve the authentication mode from ``env``.

    Resolution order, first match wins:

    1. ``ODP_AUTH_MODE`` -- authoritative; ``local`` or ``oidc``.
    2. ``ODP_AUTH_OIDC_ENABLED`` -- its legacy boolean alias.
    3. a configured issuer in ``oidc_issuer_vars`` -- pre-contract deployments
       only ever set the OIDC inputs, so they stay on OIDC until they opt into
       an explicit mode.

    Returns the mode and, when the configuration is invalid or
    self-contradicting, the reason it must not be trusted. Callers decide what
    to do with that reason: the release path aborts, the auth boundary treats
    it as "OIDC is not authoritatively enabled" and refuses OIDC tokens.

    Inputs are normalised (``strip().lower()``) and placeholder issuers are not
    configurations, matching ``auth_mode.sh`` exactly.
    """

    mode = env.get("ODP_AUTH_MODE", "").strip().lower()
    legacy_flag = env.get("ODP_AUTH_OIDC_ENABLED", "").strip().lower()

    if legacy_flag not in ("", "true", "false"):
        return "local", (f"ODP_AUTH_OIDC_ENABLED must be 'true' or 'false', got {legacy_flag!r}")
    if mode and mode not in AUTH_MODES:
        return "local", f"ODP_AUTH_MODE must be 'local' or 'oidc', got {mode!r}"
    if mode:
        expected_flag = "true" if mode == "oidc" else "false"
        if legacy_flag and legacy_flag != expected_flag:
            return mode, (
                f"ODP_AUTH_MODE={mode} conflicts with ODP_AUTH_OIDC_ENABLED={legacy_flag}"
            )
        return mode, None
    if legacy_flag:
        return ("oidc" if legacy_flag == "true" else "local"), None
    if any(is_configured_value(env.get(name, "")) for name in oidc_issuer_vars):
        return "oidc", None
    return "local", None


def oidc_provider_enabled(
    env: Mapping[str, str],
    *,
    oidc_issuer_vars: Sequence[str] = DEPLOYMENT_OIDC_ISSUER_VARS,
) -> bool:
    """True only when the environment *authoritatively* enables OIDC.

    Fail-closed: an invalid or self-contradicting configuration resolves to
    "not enabled" rather than to a guess. The release path refuses to deploy
    such an environment; a service that boots into it anyway must not accept
    OIDC tokens on the strength of leftover issuer inputs.
    """

    mode, error = resolve_auth_mode(env, oidc_issuer_vars=oidc_issuer_vars)
    return mode == "oidc" and error is None
