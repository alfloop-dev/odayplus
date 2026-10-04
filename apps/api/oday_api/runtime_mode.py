"""Authoritative runtime-mode resolution for API production data guards."""

from __future__ import annotations

import os
from collections.abc import Mapping

_LIVE_MODES = frozenset({"live", "stage", "staging", "prod", "production"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def deployment_mode(environment: Mapping[str, str] = os.environ) -> str:
    """Return the first server-owned deployment mode configured by the runtime."""

    for name in ("ODP_DEPLOY_ENV", "ODAY_ENV", "ODP_ENV", "APP_ENV", "ENVIRONMENT"):
        value = environment.get(name, "").strip().lower()
        if value:
            return value
    return "development"


def live_data_required(environment: Mapping[str, str] = os.environ) -> bool:
    """Production-like modes always require live persistence, providers, and models."""

    require_live = environment.get("ODP_REQUIRE_LIVE_DATA", "").strip().lower()
    product_mode = environment.get("ODP_PRODUCT_MODE", "").strip().lower()
    node_env = environment.get("NODE_ENV", "").strip().lower()
    return (
        require_live in _TRUE_VALUES
        or deployment_mode(environment) in _LIVE_MODES
        or product_mode in _LIVE_MODES
        or node_env == "production"
    )


#: Mirrors ``delivery_toolchain.release.release_manifest.RELEASE_PROFILES``.
#: The runtime must not import the release toolchain, so the vocabulary is
#: pinned here and held to the manifest by the release profile tests.
RELEASE_PROFILE_FULL = "full"
RELEASE_PROFILE_DEV_ADMIN = "dev-admin"
_RELEASE_PROFILE_DEPLOYMENTS: dict[str, frozenset[str] | None] = {
    RELEASE_PROFILE_FULL: None,
    RELEASE_PROFILE_DEV_ADMIN: frozenset({"dev"}),
}


def release_profile(environment: Mapping[str, str] = os.environ) -> dict[str, object]:
    """Report the admitted release profile this revision was deployed with.

    The deploy writes ``ODP_RELEASE_PROFILE`` from the admitted manifest. An
    absent value is the complete ``full`` scope. An unknown value, or a narrowed
    profile running outside the deployments it was admitted for, is reported as
    invalid so readiness fails closed instead of guessing a scope.

    ``modelReadinessClaimed`` is False for every narrowed profile: such a
    release never stands as evidence that production models are ready.
    """

    raw = environment.get("ODP_RELEASE_PROFILE", "").strip().lower()
    name = raw or RELEASE_PROFILE_FULL
    error: str | None = None
    if name not in _RELEASE_PROFILE_DEPLOYMENTS:
        error = f"unknown release profile {raw!r}"
    else:
        allowed = _RELEASE_PROFILE_DEPLOYMENTS[name]
        active = deployment_mode(environment)
        if allowed is not None and active not in allowed:
            error = (
                f"release profile {name!r} may not serve deployment {active!r}"
            )
    return {
        "name": name,
        "valid": error is None,
        "modelReadinessClaimed": error is None and name == RELEASE_PROFILE_FULL,
        "error": error,
    }


__all__ = [
    "RELEASE_PROFILE_DEV_ADMIN",
    "RELEASE_PROFILE_FULL",
    "deployment_mode",
    "live_data_required",
    "release_profile",
]
