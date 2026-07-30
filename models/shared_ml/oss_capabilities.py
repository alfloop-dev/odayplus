"""Runtime capability checks for the OSS model and optimization stack."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from importlib import util


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


def probe_package_in_isolation(package_name: str) -> tuple[bool, str | None]:
    """Execute a real import and optional minimal solve probe in an isolated subprocess.

    This prevents C++ ABI symbol collisions (e.g. between OR-Tools and CVXPY/HiGHS)
    from polluting the host process state during capability inspection.
    """
    code = f"""
import json, os, sys
from importlib import import_module
from importlib.metadata import version, PackageNotFoundError

res = {{"available": False, "version": None}}
try:
    mod = import_module("{package_name}")
    try:
        ver = version("{package_name}".replace("_", "-"))
    except PackageNotFoundError:
        ver = "installed"
    res["version"] = ver

    if "{package_name}" == "ortools":
        from ortools.linear_solver import pywraplp
        s = pywraplp.Solver.CreateSolver("GLOP")
        if not s:
            raise RuntimeError("GLOP solver unavailable")
        x = s.NumVar(0, 10, "x")
        s.Maximize(x)
        st = s.Solve()
        if st != pywraplp.Solver.OPTIMAL or abs(x.solution_value() - 10.0) > 1e-4:
            raise RuntimeError("ortools minimal solve failed")
    elif "{package_name}" == "cvxpy":
        import cvxpy as cp
        y = cp.Variable()
        prob = cp.Problem(cp.Maximize(y), [y <= 10])
        installed = cp.installed_solvers()
        if "HIGHS" not in installed or os.environ.get("_TEST_MISSING_HIGHS"):
            raise RuntimeError("HIGHS solver unavailable in cvxpy")
        val = prob.solve(solver="HIGHS")
        if val is None or abs(val - 10.0) > 1e-4:
            raise RuntimeError("cvxpy minimal solve failed")

    res["available"] = True
except Exception as e:
    res["error"] = str(e)

print(json.dumps(res))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(sys.path)

    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if proc.returncode == 0:
            for line in reversed(proc.stdout.strip().splitlines()):
                try:
                    data = json.loads(line)
                    if isinstance(data, dict) and "available" in data:
                        if data.get("available"):
                            return True, data.get("version") or "installed"
                        break
                except Exception:
                    continue
    except Exception:
        pass
    return False, None


def inspect_oss_capability(capability: OssCapability) -> OssCapabilityStatus:
    packages: dict[str, str | None] = {}
    for package in CAPABILITY_PACKAGES[capability]:
        if util.find_spec(package) is None:
            packages[package] = None
            continue
        available, pkg_ver = probe_package_in_isolation(package)
        if available:
            packages[package] = pkg_ver
        else:
            packages[package] = None
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
