"""Runtime capability checks for the OSS model and optimization stack."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib import util
from importlib.metadata import PackageNotFoundError, version


class OssCapability(StrEnum):
    MODEL_TRAINING = "model_training"
    FORECASTING = "forecasting"
    EXPERIMENT_TRACKING = "experiment_tracking"
    CAUSAL_INFERENCE = "causal_inference"
    SURVIVAL_ANALYSIS = "survival_analysis"
    HYPERPARAMETER_OPTIMIZATION = "hyperparameter_optimization"
    EVOLUTIONARY_OPTIMIZATION = "evolutionary_optimization"
    OPTIMIZATION = "optimization"
    DATA_QUALITY = "data_quality"
    MODEL_MONITORING = "model_monitoring"
    TRAINING_ORCHESTRATION = "training_orchestration"


CAPABILITY_PACKAGES: dict[OssCapability, tuple[str, ...]] = {
    OssCapability.MODEL_TRAINING: ("catboost", "lightgbm"),
    OssCapability.FORECASTING: ("statsforecast", "mlforecast"),
    OssCapability.EXPERIMENT_TRACKING: ("mlflow",),
    OssCapability.CAUSAL_INFERENCE: ("statsmodels",),
    OssCapability.SURVIVAL_ANALYSIS: ("lifelines",),
    OssCapability.HYPERPARAMETER_OPTIMIZATION: ("optuna",),
    OssCapability.EVOLUTIONARY_OPTIMIZATION: ("pymoo",),
    OssCapability.OPTIMIZATION: ("ortools", "cvxpy", "pyomo"),
    OssCapability.DATA_QUALITY: ("great_expectations",),
    OssCapability.MODEL_MONITORING: ("evidently",),
    OssCapability.TRAINING_ORCHESTRATION: ("dagster",),
}


class OssCapabilityUnavailable(RuntimeError):
    def __init__(self, capability: OssCapability, missing_packages: tuple[str, ...]) -> None:
        self.capability = capability
        self.missing_packages = missing_packages
        missing = ", ".join(missing_packages)
        super().__init__(f"OSS capability {capability.value!r} requires missing packages: {missing}")


@dataclass(frozen=True)
class OssCapabilityStatus:
    capability: OssCapability
    available: bool
    packages: dict[str, str | None]

    @property
    def missing_packages(self) -> tuple[str, ...]:
        return tuple(name for name, package_version in self.packages.items() if package_version is None)

    def to_dict(self) -> dict[str, object]:
        return {
            "capability": self.capability.value,
            "available": self.available,
            "packages": self.packages,
            "missing_packages": list(self.missing_packages),
        }


def inspect_oss_capability(capability: OssCapability) -> OssCapabilityStatus:
    packages: dict[str, str | None] = {}
    for package in CAPABILITY_PACKAGES[capability]:
        if util.find_spec(package) is None:
            packages[package] = None
            continue
        try:
            packages[package] = version(package.replace("_", "-"))
        except PackageNotFoundError:
            packages[package] = "installed"
    return OssCapabilityStatus(
        capability=capability,
        available=all(package_version is not None for package_version in packages.values()),
        packages=packages,
    )


def inspect_oss_stack() -> tuple[OssCapabilityStatus, ...]:
    return tuple(inspect_oss_capability(capability) for capability in OssCapability)


def require_oss_capability(capability: OssCapability) -> OssCapabilityStatus:
    status = inspect_oss_capability(capability)
    if not status.available:
        raise OssCapabilityUnavailable(capability, status.missing_packages)
    return status


__all__ = [
    "CAPABILITY_PACKAGES",
    "OssCapability",
    "OssCapabilityStatus",
    "OssCapabilityUnavailable",
    "inspect_oss_capability",
    "inspect_oss_stack",
    "require_oss_capability",
]
