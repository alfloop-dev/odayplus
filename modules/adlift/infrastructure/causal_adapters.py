from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
from statistics import NormalDist
from typing import Any, Protocol

import numpy as np


class ChallengerUnavailableError(RuntimeError):
    """Raised when an optional causal challenger cannot be executed."""


@dataclass(frozen=True)
class CausalChallengerCapability:
    adapter_name: str
    dependency: str
    dependency_installed: bool
    estimator_configured: bool
    available: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_name": self.adapter_name,
            "dependency": self.dependency,
            "dependency_installed": self.dependency_installed,
            "estimator_configured": self.estimator_configured,
            "available": self.available,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CausalChallengerRequest:
    outcome: tuple[float, ...]
    treatment: tuple[float, ...]
    features: tuple[tuple[float, ...], ...]
    feature_names: tuple[str, ...]
    confidence_level: float = 0.90

    def __post_init__(self) -> None:
        sample_size = len(self.outcome)
        if sample_size == 0 or len(self.treatment) != sample_size:
            raise ValueError("outcome and treatment must contain the same non-zero sample size")
        if len(self.features) != sample_size:
            raise ValueError("features must contain one row per outcome")
        if any(len(row) != len(self.feature_names) for row in self.features):
            raise ValueError("every feature row must match feature_names")
        if not 0.0 < self.confidence_level < 1.0:
            raise ValueError("confidence_level must be between zero and one")


@dataclass(frozen=True)
class CausalChallengerEstimate:
    adapter_name: str
    effect: float
    low: float
    high: float
    standard_error: float | None
    sample_size: int


class CausalChallengerAdapter(Protocol):
    def capability(self) -> CausalChallengerCapability: ...

    def fit_estimate(self, request: CausalChallengerRequest) -> CausalChallengerEstimate: ...


EstimatorFactory = Callable[[CausalChallengerRequest], Any]


class DoubleMLStyleAdapter:
    """Dependency-gated adapter for a configured DoubleML estimator factory.

    The factory owns learner and ``DoubleMLData`` construction. It must return
    an estimator exposing ``fit()``, ``coef`` and optionally ``se``/``confint``.
    """

    adapter_name = "doubleml"
    dependency = "doubleml"

    def __init__(self, estimator_factory: EstimatorFactory | None = None) -> None:
        self._estimator_factory = estimator_factory

    def capability(self) -> CausalChallengerCapability:
        return _capability(
            adapter_name=self.adapter_name,
            dependency=self.dependency,
            configured=self._estimator_factory is not None,
        )

    def fit_estimate(self, request: CausalChallengerRequest) -> CausalChallengerEstimate:
        _require_available(self.capability())
        assert self._estimator_factory is not None
        fitted = self._estimator_factory(request).fit()
        effect = _first_float(fitted.coef)
        standard_error = _optional_first_float(getattr(fitted, "se", None))
        low, high = _doubleml_interval(
            fitted,
            effect=effect,
            standard_error=standard_error,
            confidence_level=request.confidence_level,
        )
        return CausalChallengerEstimate(
            adapter_name=self.adapter_name,
            effect=effect,
            low=low,
            high=high,
            standard_error=standard_error,
            sample_size=len(request.outcome),
        )


class EconMLStyleAdapter:
    """Dependency-gated adapter for EconML estimators with fit/effect methods."""

    adapter_name = "econml"
    dependency = "econml"

    def __init__(self, estimator_factory: EstimatorFactory | None = None) -> None:
        self._estimator_factory = estimator_factory

    def capability(self) -> CausalChallengerCapability:
        return _capability(
            adapter_name=self.adapter_name,
            dependency=self.dependency,
            configured=self._estimator_factory is not None,
        )

    def fit_estimate(self, request: CausalChallengerRequest) -> CausalChallengerEstimate:
        _require_available(self.capability())
        assert self._estimator_factory is not None
        estimator = self._estimator_factory(request)
        outcome = np.asarray(request.outcome, dtype=float)
        treatment = np.asarray(request.treatment, dtype=float)
        features = np.asarray(request.features, dtype=float)
        estimator.fit(outcome, treatment, X=features)
        effects = np.asarray(estimator.effect(features), dtype=float).reshape(-1)
        effect = float(np.mean(effects))
        standard_error: float | None = None
        low = high = effect
        if hasattr(estimator, "effect_interval"):
            alpha = 1.0 - request.confidence_level
            lower, upper = estimator.effect_interval(features, alpha=alpha)
            low = float(np.mean(np.asarray(lower, dtype=float)))
            high = float(np.mean(np.asarray(upper, dtype=float)))
        return CausalChallengerEstimate(
            adapter_name=self.adapter_name,
            effect=effect,
            low=low,
            high=high,
            standard_error=standard_error,
            sample_size=len(request.outcome),
        )


def _capability(
    *,
    adapter_name: str,
    dependency: str,
    configured: bool,
) -> CausalChallengerCapability:
    installed = find_spec(dependency) is not None
    if not installed:
        reason = f"dependency_missing:{dependency}"
    elif not configured:
        reason = "estimator_factory_not_configured"
    else:
        reason = "available"
    return CausalChallengerCapability(
        adapter_name=adapter_name,
        dependency=dependency,
        dependency_installed=installed,
        estimator_configured=configured,
        available=installed and configured,
        reason=reason,
    )


def _require_available(capability: CausalChallengerCapability) -> None:
    if not capability.available:
        raise ChallengerUnavailableError(
            f"{capability.adapter_name} challenger unavailable: {capability.reason}"
        )


def _first_float(value: Any) -> float:
    values = np.asarray(value, dtype=float).reshape(-1)
    if values.size == 0:
        raise ValueError("challenger estimator returned an empty coefficient")
    return float(values[0])


def _optional_first_float(value: Any) -> float | None:
    if value is None:
        return None
    return _first_float(value)


def _doubleml_interval(
    fitted: Any,
    *,
    effect: float,
    standard_error: float | None,
    confidence_level: float,
) -> tuple[float, float]:
    if hasattr(fitted, "confint"):
        interval = np.asarray(fitted.confint(level=confidence_level), dtype=float)
        if interval.size >= 2:
            return float(interval.reshape(-1, 2)[0, 0]), float(interval.reshape(-1, 2)[0, 1])
    if standard_error is None:
        return effect, effect
    z_score = NormalDist().inv_cdf(0.5 + confidence_level / 2.0)
    return effect - z_score * standard_error, effect + z_score * standard_error


__all__ = [
    "CausalChallengerAdapter",
    "CausalChallengerCapability",
    "CausalChallengerEstimate",
    "CausalChallengerRequest",
    "ChallengerUnavailableError",
    "DoubleMLStyleAdapter",
    "EconMLStyleAdapter",
]
