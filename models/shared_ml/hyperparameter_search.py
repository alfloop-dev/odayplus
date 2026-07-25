"""Optuna-backed hyperparameter search shared by production model trainers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from models.shared_ml.oss_capabilities import OssCapability, require_oss_capability

ParameterKind = Literal["float", "int", "categorical"]
Objective = Callable[[Mapping[str, Any]], float]


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    kind: ParameterKind
    low: float | int | None = None
    high: float | int | None = None
    choices: tuple[Any, ...] = ()
    step: float | int | None = None
    log: bool = False


@dataclass(frozen=True)
class HyperparameterTrial:
    number: int
    value: float
    params: dict[str, Any]
    state: str


@dataclass(frozen=True)
class HyperparameterSearchResult:
    study_name: str
    direction: str
    best_value: float
    best_params: dict[str, Any]
    trials: tuple[HyperparameterTrial, ...]
    engine: str = "optuna"


class OptunaSearchRunner:
    def run(
        self,
        *,
        objective: Objective,
        search_space: Sequence[ParameterSpec],
        n_trials: int,
        direction: Literal["minimize", "maximize"] = "minimize",
        study_name: str = "oday-model-search",
        seed: int = 42,
    ) -> HyperparameterSearchResult:
        if n_trials < 1:
            raise ValueError("Optuna search requires at least one trial")
        if not search_space:
            raise ValueError("Optuna search requires a non-empty search space")
        require_oss_capability(OssCapability.HYPERPARAMETER_OPTIMIZATION)
        import optuna

        sampler = optuna.samplers.TPESampler(seed=seed)
        study = optuna.create_study(
            study_name=study_name,
            direction=direction,
            sampler=sampler,
        )

        def optuna_objective(trial: Any) -> float:
            params = {spec.name: _suggest(trial, spec) for spec in search_space}
            return float(objective(params))

        study.optimize(optuna_objective, n_trials=n_trials)
        return HyperparameterSearchResult(
            study_name=study.study_name,
            direction=direction,
            best_value=float(study.best_value),
            best_params=dict(study.best_params),
            trials=tuple(
                HyperparameterTrial(
                    number=trial.number,
                    value=float(trial.value),
                    params=dict(trial.params),
                    state=trial.state.name,
                )
                for trial in study.trials
                if trial.value is not None
            ),
        )


def _suggest(trial: Any, spec: ParameterSpec) -> Any:
    if spec.kind == "categorical":
        if not spec.choices:
            raise ValueError(f"categorical parameter {spec.name!r} requires choices")
        return trial.suggest_categorical(spec.name, list(spec.choices))
    if spec.low is None or spec.high is None:
        raise ValueError(f"parameter {spec.name!r} requires low and high bounds")
    if spec.kind == "int":
        return trial.suggest_int(
            spec.name,
            int(spec.low),
            int(spec.high),
            step=int(spec.step or 1),
            log=spec.log,
        )
    if spec.kind == "float":
        kwargs: dict[str, Any] = {"log": spec.log}
        if spec.step is not None:
            kwargs["step"] = float(spec.step)
        return trial.suggest_float(spec.name, float(spec.low), float(spec.high), **kwargs)
    raise ValueError(f"unsupported parameter kind: {spec.kind}")


__all__ = [
    "HyperparameterSearchResult",
    "HyperparameterTrial",
    "OptunaSearchRunner",
    "ParameterSpec",
]
