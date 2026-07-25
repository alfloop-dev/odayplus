from __future__ import annotations

import io
import json
import math
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np

ARTIFACT_FORMAT = "oday-oss-estimator-v1"
DEFAULT_RANDOM_SEED = 20260724


class OSSEstimatorError(RuntimeError):
    pass


class UnknownEstimatorError(OSSEstimatorError):
    pass


class EstimatorUnavailableError(OSSEstimatorError):
    pass


class EstimatorArtifactError(OSSEstimatorError):
    pass


@dataclass(frozen=True)
class EstimatorSpec:
    algorithm: str
    engine: str
    package: str
    objective: str
    quantiles: tuple[float, ...] = ()

    @property
    def quantile_capable(self) -> bool:
        return bool(self.quantiles)


ESTIMATOR_SPECS: Mapping[str, EstimatorSpec] = {
    "catboost_regressor": EstimatorSpec(
        algorithm="catboost_regressor",
        engine="catboost.CatBoostRegressor",
        package="catboost",
        objective="regression",
    ),
    "catboost_quantile": EstimatorSpec(
        algorithm="catboost_quantile",
        engine="catboost.CatBoostRegressor",
        package="catboost",
        objective="quantile",
        quantiles=(0.1, 0.5, 0.9),
    ),
    "lightgbm_regressor": EstimatorSpec(
        algorithm="lightgbm_regressor",
        engine="lightgbm.LGBMRegressor",
        package="lightgbm",
        objective="regression",
    ),
    "lightgbm_quantile": EstimatorSpec(
        algorithm="lightgbm_quantile",
        engine="lightgbm.LGBMRegressor",
        package="lightgbm",
        objective="quantile",
        quantiles=(0.1, 0.5, 0.9),
    ),
}

ALGORITHM_ALIASES: Mapping[str, str] = {
    # Backwards-compatible call name. The persisted contract always records the
    # resolved OSS engine instead of claiming a deterministic synthetic model.
    "deterministic_backtest_regressor": "catboost_regressor",
    "catboost": "catboost_regressor",
    "lightgbm": "lightgbm_regressor",
}


def resolve_estimator_spec(algorithm: str) -> EstimatorSpec:
    resolved = ALGORITHM_ALIASES.get(algorithm, algorithm)
    spec = ESTIMATOR_SPECS.get(resolved)
    if spec is None:
        supported = ", ".join(sorted((*ESTIMATOR_SPECS, *ALGORITHM_ALIASES)))
        raise UnknownEstimatorError(
            f"unknown estimator algorithm {algorithm!r}; supported algorithms: {supported}"
        )
    _load_package(spec)
    return spec


@dataclass(frozen=True)
class FeatureEncoder:
    feature_names: tuple[str, ...]
    feature_kinds: Mapping[str, str]
    categories: Mapping[str, tuple[str, ...]]

    @classmethod
    def fit(
        cls,
        rows: Sequence[Mapping[str, Any]],
        feature_names: Sequence[str],
    ) -> FeatureEncoder:
        names = tuple(feature_names)
        if not names:
            raise OSSEstimatorError("estimator requires at least one feature")
        kinds: dict[str, str] = {}
        categories: dict[str, tuple[str, ...]] = {}
        for name in names:
            observed = [row.get(name) for row in rows if row.get(name) is not None]
            if all(_is_numeric(value) for value in observed):
                kinds[name] = "numeric"
                continue
            kinds[name] = "categorical"
            categories[name] = tuple(sorted({_category_token(value) for value in observed}))
        return cls(feature_names=names, feature_kinds=kinds, categories=categories)

    def transform(self, rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
        matrix = np.empty((len(rows), len(self.feature_names)), dtype=float)
        category_indexes = {
            name: {value: index for index, value in enumerate(values)}
            for name, values in self.categories.items()
        }
        for row_index, row in enumerate(rows):
            for column_index, name in enumerate(self.feature_names):
                value = row.get(name)
                if self.feature_kinds[name] == "numeric":
                    matrix[row_index, column_index] = (
                        float(value) if value is not None else np.nan
                    )
                    continue
                if value is None:
                    matrix[row_index, column_index] = -1.0
                    continue
                matrix[row_index, column_index] = float(
                    category_indexes[name].get(_category_token(value), -2)
                )
        return matrix

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_names": list(self.feature_names),
            "feature_kinds": dict(self.feature_kinds),
            "categories": {name: list(values) for name, values in self.categories.items()},
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FeatureEncoder:
        return cls(
            feature_names=tuple(str(name) for name in value["feature_names"]),
            feature_kinds={
                str(name): str(kind)
                for name, kind in dict(value["feature_kinds"]).items()
            },
            categories={
                str(name): tuple(str(item) for item in values)
                for name, values in dict(value["categories"]).items()
            },
        )


@dataclass(frozen=True)
class BacktestPredictions:
    labels: tuple[float, ...]
    predictions: tuple[float, ...]
    lower_predictions: tuple[float, ...]
    upper_predictions: tuple[float, ...]
    baseline_predictions: tuple[float, ...]
    fold_ids: tuple[int, ...]


@dataclass
class LoadedOSSEstimator:
    spec: EstimatorSpec
    encoder: FeatureEncoder
    models: Mapping[str, Any]
    conformal_radius: float | None
    package_version: str

    def predict(self, rows: Sequence[Mapping[str, Any]]) -> tuple[float, ...]:
        matrix = self.encoder.transform(rows)
        return tuple(float(value) for value in _predict_model(self.models["point"], matrix))

    def predict_interval(
        self,
        rows: Sequence[Mapping[str, Any]],
    ) -> tuple[tuple[float, ...], tuple[float, ...]]:
        matrix = self.encoder.transform(rows)
        if self.spec.quantile_capable:
            lower = np.asarray(_predict_model(self.models["lower"], matrix), dtype=float)
            upper = np.asarray(_predict_model(self.models["upper"], matrix), dtype=float)
            return (
                tuple(float(value) for value in np.minimum(lower, upper)),
                tuple(float(value) for value in np.maximum(lower, upper)),
            )
        if self.conformal_radius is None:
            raise EstimatorArtifactError("point estimator artifact has no interval calibration")
        point = np.asarray(_predict_model(self.models["point"], matrix), dtype=float)
        return (
            tuple(float(value) for value in point - self.conformal_radius),
            tuple(float(value) for value in point + self.conformal_radius),
        )

    def to_artifact_bytes(self) -> bytes:
        model_files: dict[str, str] = {}
        model_payloads: dict[str, bytes] = {}
        for role, model in self.models.items():
            suffix, payload = _serialize_model(self.spec, model)
            filename = f"models/{role}.{suffix}"
            model_files[role] = filename
            model_payloads[filename] = payload

        manifest = {
            "artifact_format": ARTIFACT_FORMAT,
            "algorithm": self.spec.algorithm,
            "engine": self.spec.engine,
            "package": self.spec.package,
            "package_version": self.package_version,
            "objective": self.spec.objective,
            "quantiles": list(self.spec.quantiles),
            "encoder": self.encoder.to_dict(),
            "conformal_radius": self.conformal_radius,
            "model_files": model_files,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write_zip_entry(
                archive,
                "manifest.json",
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(),
            )
            for filename, payload in sorted(model_payloads.items()):
                _write_zip_entry(archive, filename, payload)
        return buffer.getvalue()


@dataclass(frozen=True)
class EstimatorTrainingResult:
    estimator: LoadedOSSEstimator
    backtest: BacktestPredictions
    requested_algorithm: str

    @property
    def resolved_algorithm(self) -> str:
        return self.estimator.spec.algorithm


def train_oss_estimator(
    *,
    algorithm: str,
    feature_rows: Sequence[Mapping[str, Any]],
    labels: Sequence[float],
    feature_names: Sequence[str],
) -> EstimatorTrainingResult:
    spec = resolve_estimator_spec(algorithm)
    if len(feature_rows) != len(labels):
        raise OSSEstimatorError("feature and label row counts differ")
    if len(labels) < 2:
        raise OSSEstimatorError("OSS estimator backtest requires at least two rows")
    label_array = np.asarray(labels, dtype=float)
    if not np.all(np.isfinite(label_array)):
        raise OSSEstimatorError("labels must be finite numeric values")

    fold_ids = _fold_assignments(len(labels))
    predictions = np.empty(len(labels), dtype=float)
    lower = np.empty(len(labels), dtype=float)
    upper = np.empty(len(labels), dtype=float)
    baseline = np.empty(len(labels), dtype=float)

    for fold_id in sorted(set(fold_ids)):
        test_indexes = np.flatnonzero(np.asarray(fold_ids) == fold_id)
        train_indexes = np.flatnonzero(np.asarray(fold_ids) != fold_id)
        if train_indexes.size == 0:
            raise OSSEstimatorError("backtest fold has no training rows")
        baseline[test_indexes] = float(np.mean(label_array[train_indexes]))
        fold_training_rows = [feature_rows[int(index)] for index in train_indexes]
        fold_test_rows = [feature_rows[int(index)] for index in test_indexes]
        fold_encoder = FeatureEncoder.fit(fold_training_rows, feature_names)
        fold_training_matrix = fold_encoder.transform(fold_training_rows)
        fold_test_matrix = fold_encoder.transform(fold_test_rows)
        fold_models = _fit_models(
            spec,
            fold_training_matrix,
            label_array[train_indexes],
        )
        predictions[test_indexes] = _predict_model(fold_models["point"], fold_test_matrix)
        if spec.quantile_capable:
            fold_lower = _predict_model(fold_models["lower"], fold_test_matrix)
            fold_upper = _predict_model(fold_models["upper"], fold_test_matrix)
            lower[test_indexes] = np.minimum(fold_lower, fold_upper)
            upper[test_indexes] = np.maximum(fold_lower, fold_upper)

    conformal_radius: float | None = None
    if not spec.quantile_capable:
        conformal_radius = _finite_sample_quantile(
            np.abs(label_array - predictions),
            coverage=0.8,
        )
        lower = predictions - conformal_radius
        upper = predictions + conformal_radius

    encoder = FeatureEncoder.fit(feature_rows, feature_names)
    matrix = encoder.transform(feature_rows)
    final_models = _fit_models(spec, matrix, label_array)
    package = _load_package(spec)
    estimator = LoadedOSSEstimator(
        spec=spec,
        encoder=encoder,
        models=final_models,
        conformal_radius=conformal_radius,
        package_version=str(getattr(package, "__version__", "unknown")),
    )
    return EstimatorTrainingResult(
        estimator=estimator,
        backtest=BacktestPredictions(
            labels=tuple(float(value) for value in label_array),
            predictions=tuple(float(value) for value in predictions),
            lower_predictions=tuple(float(value) for value in lower),
            upper_predictions=tuple(float(value) for value in upper),
            baseline_predictions=tuple(float(value) for value in baseline),
            fold_ids=tuple(fold_ids),
        ),
        requested_algorithm=algorithm,
    )


def load_estimator_artifact(data: bytes) -> LoadedOSSEstimator:
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("artifact_format") != ARTIFACT_FORMAT:
                raise EstimatorArtifactError("unsupported OSS estimator artifact format")
            spec = resolve_estimator_spec(str(manifest["algorithm"]))
            if manifest.get("engine") != spec.engine:
                raise EstimatorArtifactError("artifact engine does not match algorithm contract")
            models = {
                role: _deserialize_model(spec, archive.read(filename))
                for role, filename in dict(manifest["model_files"]).items()
            }
    except EstimatorArtifactError:
        raise
    except (KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise EstimatorArtifactError("invalid OSS estimator artifact") from exc

    required_roles = {"point", "lower", "upper"} if spec.quantile_capable else {"point"}
    if set(models) != required_roles:
        raise EstimatorArtifactError(
            f"artifact model roles {sorted(models)} do not match {sorted(required_roles)}"
        )
    radius_value = manifest.get("conformal_radius")
    radius = float(radius_value) if radius_value is not None else None
    if not spec.quantile_capable and radius is None:
        raise EstimatorArtifactError("point estimator artifact is missing conformal radius")
    return LoadedOSSEstimator(
        spec=spec,
        encoder=FeatureEncoder.from_dict(manifest["encoder"]),
        models=models,
        conformal_radius=radius,
        package_version=str(manifest["package_version"]),
    )


def _fit_models(
    spec: EstimatorSpec,
    matrix: np.ndarray,
    labels: np.ndarray,
) -> dict[str, Any]:
    if not spec.quantile_capable:
        return {"point": _fit_model(spec, matrix, labels, quantile=None)}
    return {
        "lower": _fit_model(spec, matrix, labels, quantile=spec.quantiles[0]),
        "point": _fit_model(spec, matrix, labels, quantile=spec.quantiles[1]),
        "upper": _fit_model(spec, matrix, labels, quantile=spec.quantiles[2]),
    }


def _fit_model(
    spec: EstimatorSpec,
    matrix: np.ndarray,
    labels: np.ndarray,
    *,
    quantile: float | None,
) -> Any:
    package = _load_package(spec)
    if spec.package == "catboost":
        estimator_type = package.CatBoostRegressor
        loss = "RMSE" if quantile is None else f"Quantile:alpha={quantile}"
        estimator = estimator_type(
            iterations=100,
            depth=6,
            learning_rate=0.05,
            loss_function=loss,
            random_seed=DEFAULT_RANDOM_SEED,
            verbose=False,
            allow_writing_files=False,
            thread_count=1,
        )
    elif spec.package == "lightgbm":
        estimator_type = package.LGBMRegressor
        parameters: dict[str, Any] = {
            "objective": "regression" if quantile is None else "quantile",
            "n_estimators": 100,
            "learning_rate": 0.05,
            "num_leaves": 15,
            "min_child_samples": 1,
            "min_data_in_bin": 1,
            "random_state": DEFAULT_RANDOM_SEED,
            "deterministic": True,
            "force_col_wise": True,
            "verbosity": -1,
            "n_jobs": 1,
        }
        if quantile is not None:
            parameters["alpha"] = quantile
        estimator = estimator_type(**parameters)
    else:
        raise UnknownEstimatorError(f"no estimator factory for package {spec.package}")
    estimator.fit(matrix, labels)
    return estimator


def _load_package(spec: EstimatorSpec) -> Any:
    try:
        return import_module(spec.package)
    except (ImportError, ModuleNotFoundError) as exc:
        raise EstimatorUnavailableError(
            f"estimator {spec.algorithm} requires unavailable OSS package {spec.package}"
        ) from exc


def _serialize_model(spec: EstimatorSpec, model: Any) -> tuple[str, bytes]:
    if spec.package == "catboost":
        path = _temporary_path(".cbm")
        try:
            model.save_model(str(path), format="cbm")
            return "cbm", path.read_bytes()
        finally:
            path.unlink(missing_ok=True)
    if spec.package == "lightgbm":
        return "txt", model.booster_.model_to_string().encode()
    raise EstimatorArtifactError(f"unsupported estimator package {spec.package}")


def _deserialize_model(spec: EstimatorSpec, payload: bytes) -> Any:
    package = _load_package(spec)
    if spec.package == "catboost":
        path = _temporary_path(".cbm")
        try:
            path.write_bytes(payload)
            model = package.CatBoostRegressor()
            model.load_model(str(path), format="cbm")
            return model
        finally:
            path.unlink(missing_ok=True)
    if spec.package == "lightgbm":
        return package.Booster(model_str=payload.decode())
    raise EstimatorArtifactError(f"unsupported estimator package {spec.package}")


def _predict_model(model: Any, matrix: np.ndarray) -> np.ndarray:
    return np.asarray(model.predict(matrix), dtype=float).reshape(-1)


def _fold_assignments(row_count: int) -> list[int]:
    fold_count = min(5, row_count)
    permutation = np.random.default_rng(DEFAULT_RANDOM_SEED).permutation(row_count)
    assignments = [0] * row_count
    for position, row_index in enumerate(permutation):
        assignments[int(row_index)] = position % fold_count
    return assignments


def _finite_sample_quantile(residuals: np.ndarray, *, coverage: float) -> float:
    if residuals.size == 0:
        raise OSSEstimatorError("cannot calibrate interval without residuals")
    rank = min(residuals.size, math.ceil((residuals.size + 1) * coverage))
    return float(np.sort(residuals)[rank - 1])


def _temporary_path(suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    path = Path(handle.name)
    handle.close()
    return path


def _write_zip_entry(archive: zipfile.ZipFile, filename: str, payload: bytes) -> None:
    entry = zipfile.ZipInfo(filename=filename, date_time=(1980, 1, 1, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.external_attr = 0o600 << 16
    archive.writestr(entry, payload)


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (bool, int, float, np.number)) and not isinstance(
        value, complex
    )


def _category_token(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return f"{type(value).__name__}:{value.isoformat()}"
    if isinstance(value, (str, bool, int, float)):
        return f"{type(value).__name__}:{value}"
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        encoded = str(value)
    return f"{type(value).__name__}:{encoded}"


__all__ = [
    "ALGORITHM_ALIASES",
    "ARTIFACT_FORMAT",
    "ESTIMATOR_SPECS",
    "BacktestPredictions",
    "EstimatorArtifactError",
    "EstimatorSpec",
    "EstimatorTrainingResult",
    "EstimatorUnavailableError",
    "FeatureEncoder",
    "LoadedOSSEstimator",
    "OSSEstimatorError",
    "UnknownEstimatorError",
    "load_estimator_artifact",
    "resolve_estimator_spec",
    "train_oss_estimator",
]
