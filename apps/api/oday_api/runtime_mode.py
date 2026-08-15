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


__all__ = ["deployment_mode", "live_data_required"]
