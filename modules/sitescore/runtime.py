from __future__ import annotations

from models.shared_ml.production_runtime import production_model_execution_required

_PRODUCTION_MODES = {"prod", "production", "stage", "staging"}
_LOCAL_MODES = {"local", "test", "testing", "development", "dev", "poc"}


class SiteScoreRuntimeConfigurationError(RuntimeError):
    code = "SITESCORE_PRODUCTION_BINDING_REQUIRED"


def sitescore_production_required(runtime_mode: str | None = None) -> bool:
    """Resolve runtime mode without allowing production envs to be downgraded."""

    environment_requires_production = production_model_execution_required()
    if runtime_mode is None:
        return environment_requires_production
    normalized = runtime_mode.strip().lower()
    if normalized in _PRODUCTION_MODES:
        return True
    if normalized in _LOCAL_MODES:
        return environment_requires_production
    raise SiteScoreRuntimeConfigurationError(f"unsupported SiteScore runtime mode {runtime_mode!r}")


__all__ = [
    "SiteScoreRuntimeConfigurationError",
    "sitescore_production_required",
]
