"""Executable production model resolution backed by an OSS MLflow registry.

The registry binding used by the API is evidence, not an inference engine. This
module closes that distinction: production callers only receive predictions
after the ``production`` alias, governance approval, lineage, artifact digest,
artifact format, OSS engine, and live feature inputs have all been verified.
"""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import unquote, urlparse

from models.shared_ml.oss_estimators import (
    EstimatorArtifactError,
    EstimatorUnavailableError,
    LoadedOSSEstimator,
    OSSEstimatorError,
    load_estimator_artifact,
)
from models.shared_ml.registry import ModelAlias, ModelStage, ModelVersion
from models.shared_ml.scoring_binding import ModelBinding

_TAG_PREFIX = "oday.model_version."
_PRODUCTION_ENVS = {"stage", "staging", "prod", "production"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


class ProductionModelRuntimeError(RuntimeError):
    """Base class for fail-closed production model execution errors."""

    code = "PRODUCTION_MODEL_RUNTIME_UNAVAILABLE"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ProductionModelRegistryError(ProductionModelRuntimeError):
    code = "PRODUCTION_MODEL_REGISTRY_UNAVAILABLE"


class ProductionModelApprovalError(ProductionModelRuntimeError):
    code = "PRODUCTION_MODEL_NOT_APPROVED"


class ProductionModelLineageError(ProductionModelRuntimeError):
    code = "PRODUCTION_MODEL_LINEAGE_INCOMPLETE"


class ProductionModelArtifactError(ProductionModelRuntimeError):
    code = "PRODUCTION_MODEL_ARTIFACT_UNAVAILABLE"


class ProductionModelInputError(ProductionModelRuntimeError):
    code = "PRODUCTION_MODEL_INPUT_INVALID"


class ProductionModelInferenceError(ProductionModelRuntimeError):
    code = "PRODUCTION_MODEL_INFERENCE_FAILED"


class ProductionExecutionConfigurationError(RuntimeError):
    """Raised when a production domain was composed with local-only dependencies."""

    code = "PRODUCTION_EXECUTION_BINDING_REQUIRED"


@dataclass(frozen=True)
class ModelInferenceResult:
    """Predictions plus the exact executable model evidence used to make them."""

    binding: ModelBinding
    point: tuple[float, ...]
    lower: tuple[float, ...]
    upper: tuple[float, ...]
    engine: str
    artifact_sha256: str

    def to_audit_metadata(self) -> dict[str, Any]:
        return {
            **self.binding.to_audit_metadata(),
            "model_engine": self.engine,
            "artifact_sha256": self.artifact_sha256,
            "prediction_count": len(self.point),
        }


@runtime_checkable
class ProductionModelRuntime(Protocol):
    """Runtime port consumed by scoring application services and API routes."""

    def infer(
        self,
        *,
        service: str,
        rows: Sequence[Mapping[str, Any]],
        expected_feature_schema_version: str,
    ) -> ModelInferenceResult: ...


ArtifactLoader = Callable[[str, str], bytes]


@dataclass(frozen=True)
class _ResolvedExecutableModel:
    binding: ModelBinding
    estimator: LoadedOSSEstimator
    artifact_sha256: str


class MlflowProductionModelRuntime:
    """Resolve and execute only approved MLflow ``production`` aliases."""

    def __init__(
        self,
        *,
        tracking_uri: str | None = None,
        client: Any | None = None,
        artifact_loader: ArtifactLoader | None = None,
        model_names: Mapping[str, str] | None = None,
    ) -> None:
        configured_uri = tracking_uri or os.getenv("MLFLOW_TRACKING_URI")
        if not configured_uri:
            raise ProductionModelRegistryError(
                "MLFLOW_TRACKING_URI is required for production model execution"
            )
        self.tracking_uri = configured_uri
        if client is None:
            try:
                from mlflow.tracking import MlflowClient

                client = MlflowClient(tracking_uri=configured_uri)
            except Exception as exc:  # pragma: no cover - dependency/runtime boundary
                raise ProductionModelRegistryError(
                    "cannot initialize the configured MLflow registry"
                ) from exc
        self.client = client
        self.artifact_loader = artifact_loader or _download_artifact_bytes
        self.model_names = dict(model_names or {})
        self._cache: dict[tuple[str, str, str], _ResolvedExecutableModel] = {}

    @classmethod
    def from_environment(
        cls,
        *,
        model_names: Mapping[str, str] | None = None,
    ) -> MlflowProductionModelRuntime:
        return cls(model_names=model_names)

    def infer(
        self,
        *,
        service: str,
        rows: Sequence[Mapping[str, Any]],
        expected_feature_schema_version: str,
    ) -> ModelInferenceResult:
        executable = self.resolve(
            service=service,
            expected_feature_schema_version=expected_feature_schema_version,
        )
        _validate_live_rows(
            rows,
            service=service,
            expected_feature_schema_version=expected_feature_schema_version,
            required_features=executable.estimator.encoder.feature_names,
        )
        try:
            point = executable.estimator.predict(rows)
            lower, upper = executable.estimator.predict_interval(rows)
        except (OSSEstimatorError, KeyError, TypeError, ValueError) as exc:
            raise ProductionModelInferenceError(
                f"{service}: registered OSS estimator could not execute live inputs"
            ) from exc
        _validate_predictions(service=service, point=point, lower=lower, upper=upper)
        return ModelInferenceResult(
            binding=executable.binding,
            point=point,
            lower=lower,
            upper=upper,
            engine=executable.estimator.spec.engine,
            artifact_sha256=executable.artifact_sha256,
        )

    def resolve(
        self,
        *,
        service: str,
        expected_feature_schema_version: str,
    ) -> _ResolvedExecutableModel:
        model_name = self.model_names.get(service, service)
        try:
            registered = self.client.get_model_version_by_alias(
                model_name,
                ModelAlias.PRODUCTION.value,
            )
        except Exception as exc:
            raise ProductionModelRegistryError(
                f"{model_name}: configured MLflow registry has no production alias"
            ) from exc

        tags = dict(getattr(registered, "tags", None) or {})
        aliases = tuple(str(value) for value in (getattr(registered, "aliases", None) or ()))
        domain_stage = tags.get(_tag("stage"))
        if (
            getattr(registered, "current_stage", None) != "Production"
            or domain_stage != ModelStage.PRODUCTION.value
            or ModelAlias.PRODUCTION.value not in aliases
        ):
            raise ProductionModelApprovalError(
                f"{model_name}: production alias is not bound to a production-stage model"
            )

        approved_by = _required_tag(tags, "approved_by", model_name=model_name)
        approved_at_raw = _required_tag(tags, "approved_at", model_name=model_name)
        approved_at = _parse_datetime(approved_at_raw, field_name="approved_at")
        domain_version = _required_tag(tags, "domain_version", model_name=model_name)
        dataset_snapshot_id = _required_tag(
            tags, "dataset_snapshot_id", model_name=model_name
        )
        feature_schema_version = _required_tag(
            tags, "feature_schema_version", model_name=model_name
        )
        label_version = _required_tag(tags, "label_version", model_name=model_name)
        artifact_uri = _required_tag(tags, "artifact_uri", model_name=model_name)
        artifact_sha256 = _normalize_sha256(
            _required_tag(tags, "artifact_sha256", model_name=model_name)
        )
        mlflow_run_id = _required_tag(tags, "mlflow_run_id", model_name=model_name)
        source_run_id = _required_tag(tags, "source_run_id", model_name=model_name)
        git_sha = _required_tag(tags, "git_sha", model_name=model_name)
        if feature_schema_version != expected_feature_schema_version:
            raise ProductionModelLineageError(
                f"{model_name}: registry feature schema {feature_schema_version!r} "
                f"does not match {expected_feature_schema_version!r}"
            )
        if artifact_uri != str(getattr(registered, "source", "")):
            raise ProductionModelLineageError(
                f"{model_name}: MLflow source and immutable artifact lineage disagree"
            )
        try:
            self.client.get_run(mlflow_run_id)
        except Exception as exc:
            raise ProductionModelLineageError(
                f"{model_name}: MLflow training run lineage is unavailable"
            ) from exc

        cache_key = (model_name, str(registered.version), artifact_sha256)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            artifact_bytes = self.artifact_loader(artifact_uri, self.tracking_uri)
        except Exception as exc:
            raise ProductionModelArtifactError(
                f"{model_name}: registered model artifact cannot be downloaded"
            ) from exc
        observed_sha256 = f"sha256:{hashlib.sha256(artifact_bytes).hexdigest()}"
        if observed_sha256 != artifact_sha256:
            raise ProductionModelArtifactError(
                f"{model_name}: registered model artifact digest verification failed"
            )
        try:
            estimator = load_estimator_artifact(artifact_bytes)
        except EstimatorUnavailableError as exc:
            raise ProductionModelArtifactError(
                f"{model_name}: registered OSS estimator engine is unavailable"
            ) from exc
        except (EstimatorArtifactError, OSSEstimatorError) as exc:
            raise ProductionModelArtifactError(
                f"{model_name}: registered artifact is not an executable OSS estimator"
            ) from exc

        model_version = ModelVersion(
            model_name=model_name,
            version=domain_version,
            artifact_uri=artifact_uri,
            dataset_snapshot_id=dataset_snapshot_id,
            feature_schema_version=feature_schema_version,
            label_version=label_version,
            metrics=_parse_metrics(tags.get(_tag("metrics"))),
            stage=ModelStage.PRODUCTION,
            aliases=frozenset({ModelAlias.PRODUCTION}),
            run_id=source_run_id,
            git_sha=git_sha,
            approved_by=approved_by,
            approved_at=approved_at,
        )
        binding = ModelBinding.from_model_version(
            service,
            model_version,
            artifact_sha256=artifact_sha256,
            engine=estimator.spec.engine,
            mlflow_run_id=mlflow_run_id,
        )
        resolved = _ResolvedExecutableModel(
            binding=binding,
            estimator=estimator,
            artifact_sha256=artifact_sha256,
        )
        self._cache[cache_key] = resolved
        return resolved


def production_model_execution_required() -> bool:
    """Return whether production model execution must fail closed."""

    product_mode = os.getenv("ODP_PRODUCT_MODE", "").strip().lower()
    if os.getenv("ODP_REQUIRE_LIVE_DATA", "").strip().lower() in _TRUE_VALUES:
        return True
    if os.getenv("ODP_DEPLOY_ENV", "").strip().lower() in _PRODUCTION_ENVS:
        return True
    return product_mode == "production"


def production_execution_required(runtime_mode: str | None = None) -> bool:
    """Resolve an explicit runtime mode without downgrading a production process."""

    environment_requires_production = production_model_execution_required()
    if runtime_mode is None:
        return environment_requires_production
    normalized = runtime_mode.strip().lower()
    if normalized in _PRODUCTION_ENVS:
        return True
    if normalized in {"local", "test", "testing", "development", "dev", "poc"}:
        return environment_requires_production
    raise ProductionExecutionConfigurationError(
        f"unsupported production execution runtime mode {runtime_mode!r}"
    )


def require_production_runtime(
    runtime: ProductionModelRuntime | None,
    *,
    service: str,
) -> ProductionModelRuntime:
    if runtime is None:
        raise ProductionModelRegistryError(
            f"{service}: executable production model runtime was not composed"
        )
    return runtime


def _validate_live_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    service: str,
    expected_feature_schema_version: str,
    required_features: Sequence[str],
) -> None:
    if not rows:
        raise ProductionModelInputError(
            f"{service}: production inference requires live feature rows"
        )
    for index, row in enumerate(rows):
        snapshots = row.get("source_snapshot_ids")
        if not isinstance(snapshots, (list, tuple)) or not snapshots:
            raise ProductionModelInputError(
                f"{service}: row {index} has no source snapshot lineage"
            )
        snapshot_time = row.get("feature_snapshot_time")
        if snapshot_time in {None, ""}:
            raise ProductionModelInputError(
                f"{service}: row {index} has no feature snapshot time"
            )
        _parse_datetime(str(snapshot_time), field_name="feature_snapshot_time")
        row_schema = row.get("view_version") or row.get("feature_schema_version")
        if row_schema != expected_feature_schema_version:
            raise ProductionModelInputError(
                f"{service}: row {index} feature schema does not match the approved model"
            )
        missing = [name for name in required_features if row.get(name) is None]
        if missing:
            raise ProductionModelInputError(
                f"{service}: row {index} is missing model features {sorted(missing)}"
            )


def _validate_predictions(
    *,
    service: str,
    point: Sequence[float],
    lower: Sequence[float],
    upper: Sequence[float],
) -> None:
    if not point or not (len(point) == len(lower) == len(upper)):
        raise ProductionModelInferenceError(
            f"{service}: registered estimator returned an invalid prediction shape"
        )
    for index, values in enumerate(zip(lower, point, upper, strict=True)):
        if not all(math.isfinite(float(value)) for value in values):
            raise ProductionModelInferenceError(
                f"{service}: prediction {index} contains a non-finite value"
            )
        if float(values[0]) > float(values[1]) or float(values[1]) > float(values[2]):
            raise ProductionModelInferenceError(
                f"{service}: prediction {index} interval is not ordered"
            )


def _required_tag(tags: Mapping[str, str], name: str, *, model_name: str) -> str:
    value = str(tags.get(_tag(name), "")).strip()
    if not value:
        error = (
            ProductionModelApprovalError
            if name in {"approved_by", "approved_at"}
            else ProductionModelLineageError
        )
        raise error(f"{model_name}: required registry field {name!r} is absent")
    return value


def _tag(name: str) -> str:
    return f"{_TAG_PREFIX}{name}"


def _parse_metrics(value: str | None) -> Mapping[str, float]:
    if not value:
        return {}
    try:
        import json

        decoded = json.loads(value)
        return {str(key): float(metric) for key, metric in decoded.items()}
    except (TypeError, ValueError) as exc:
        raise ProductionModelLineageError(
            "registered model metrics lineage is invalid"
        ) from exc


def _normalize_sha256(value: str) -> str:
    normalized = value.lower()
    if normalized.startswith("sha256:"):
        hexdigest = normalized.removeprefix("sha256:")
    else:
        hexdigest = normalized
    if len(hexdigest) != 64 or any(character not in "0123456789abcdef" for character in hexdigest):
        raise ProductionModelLineageError("registered artifact SHA-256 is invalid")
    return f"sha256:{hexdigest}"


def _parse_datetime(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProductionModelLineageError(
            f"registered {field_name} is not an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _download_artifact_bytes(uri: str, tracking_uri: str) -> bytes:
    parsed = urlparse(uri)
    if parsed.scheme in {"", "file"}:
        path = Path(unquote(parsed.path if parsed.scheme else uri))
        if not path.is_file():
            raise FileNotFoundError(path)
        return path.read_bytes()

    from mlflow.artifacts import download_artifacts

    downloaded = Path(
        download_artifacts(artifact_uri=uri, tracking_uri=tracking_uri)
    )
    if downloaded.is_file():
        return downloaded.read_bytes()
    candidates = sorted(
        path
        for path in downloaded.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".zip", ".model", ".artifact", ".bin"}
    )
    if len(candidates) != 1:
        raise ProductionModelArtifactError(
            "registered MLflow artifact directory must contain exactly one estimator artifact"
        )
    return candidates[0].read_bytes()


__all__ = [
    "MlflowProductionModelRuntime",
    "ModelInferenceResult",
    "ProductionModelApprovalError",
    "ProductionModelArtifactError",
    "ProductionModelInferenceError",
    "ProductionModelInputError",
    "ProductionModelLineageError",
    "ProductionModelRegistryError",
    "ProductionModelRuntime",
    "ProductionModelRuntimeError",
    "production_model_execution_required",
    "require_production_runtime",
]
