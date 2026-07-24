from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

from modules.avm.domain import (
    AVM_FEATURE_VERSION,
    NormalizedMargin,
    ValuationCase,
    ValuationReport,
    build_model_valuation_report,
)
from modules.avm.domain.liquidity import LiquidityPrediction
from modules.avm.infrastructure.lifelines_survival import (
    LifelinesLiquiditySurvivalAdapter,
)


class AVMProductionExecutionError(RuntimeError):
    """Raised when an approved production AVM cannot execute."""


class ModelRuntime(Protocol):
    def infer(
        self,
        *,
        service: str,
        rows: list[Mapping[str, Any]],
        expected_feature_schema_version: str,
    ) -> Any: ...


class LiquidityRuntime(Protocol):
    model_version: str

    @property
    def feature_names(self) -> tuple[str, ...]: ...

    def predict(self, features: Mapping[str, float]) -> LiquidityPrediction: ...


@dataclass(frozen=True)
class LiquidityArtifactEvidence:
    artifact_uri: str
    artifact_sha256: str
    model_version: str
    approved_by: str
    approved_at: datetime
    dataset_snapshot_id: str
    engine: str = "lifelines.CoxPHFitter"

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_uri": self.artifact_uri,
            "artifact_sha256": self.artifact_sha256,
            "model_version": self.model_version,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat(),
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "engine": self.engine,
        }


class AVMProductionExecutor:
    """Execute approved AVM and liquidity artifacts without formula fallback."""

    def __init__(
        self,
        *,
        model_runtime: ModelRuntime,
        liquidity_runtime: LiquidityRuntime,
        liquidity_evidence: LiquidityArtifactEvidence,
    ) -> None:
        self.model_runtime = model_runtime
        self.liquidity_runtime = liquidity_runtime
        self.liquidity_evidence = liquidity_evidence

    @classmethod
    def from_environment(
        cls,
        *,
        model_runtime: ModelRuntime | None = None,
    ) -> AVMProductionExecutor:
        try:
            if model_runtime is None:
                from models.shared_ml.production_runtime import MlflowProductionModelRuntime

                model_runtime = MlflowProductionModelRuntime.from_environment(
                    model_names={"avm": os.getenv("ODP_AVM_MODEL_NAME", "avm")}
                )
            liquidity_runtime, liquidity_evidence = _load_liquidity_artifact()
        except Exception as exc:
            if isinstance(exc, AVMProductionExecutionError):
                raise
            raise AVMProductionExecutionError(
                "AVM production artifacts could not be composed"
            ) from exc
        return cls(
            model_runtime=model_runtime,
            liquidity_runtime=liquidity_runtime,
            liquidity_evidence=liquidity_evidence,
        )

    def execute(
        self,
        case: ValuationCase,
        normalized_margin: NormalizedMargin,
    ) -> ValuationReport:
        row = {
            **case.valuation_input.to_dict(),
            "normalized_gm": normalized_margin.normalized_gm,
            "normalization_confidence": normalized_margin.confidence,
            "view_version": AVM_FEATURE_VERSION,
            "feature_snapshot_time": case.valuation_input.prediction_origin_time.isoformat(),
        }
        liquidity_features: dict[str, float] = {}
        for feature_name in self.liquidity_runtime.feature_names:
            value = row.get(feature_name)
            if value is None:
                raise AVMProductionExecutionError(
                    f"approved liquidity artifact requires missing feature {feature_name!r}"
                )
            try:
                liquidity_features[feature_name] = float(value)
            except (TypeError, ValueError) as exc:
                raise AVMProductionExecutionError(
                    f"liquidity feature {feature_name!r} is not numeric"
                ) from exc
        try:
            liquidity = self.liquidity_runtime.predict(liquidity_features)
        except Exception as exc:
            raise AVMProductionExecutionError(
                "approved lifelines liquidity artifact failed to execute"
            ) from exc

        inference_row = {
            **row,
            "liquidity_sale_probability_30d": liquidity.sale_probability_30d,
            "liquidity_sale_probability_90d": liquidity.sale_probability_90d,
            "liquidity_expected_days": liquidity.expected_days,
        }
        try:
            inference = self.model_runtime.infer(
                service="avm",
                rows=[inference_row],
                expected_feature_schema_version=AVM_FEATURE_VERSION,
            )
            lower = float(inference.lower[0])
            point = float(inference.point[0])
            upper = float(inference.upper[0])
        except Exception as exc:
            raise AVMProductionExecutionError(
                "approved AVM production model failed to execute"
            ) from exc
        if min(lower, point, upper) < 0 or not lower <= point <= upper:
            raise AVMProductionExecutionError(
                "approved AVM model returned an invalid valuation interval"
            )

        model_evidence = inference.to_audit_metadata()
        execution_metadata = {
            "mode": "production_oss",
            "model": model_evidence,
            "liquidity": {
                **self.liquidity_evidence.to_dict(),
                "library_version": version("lifelines"),
                "prediction": liquidity.to_dict(),
            },
            "source_snapshot_ids": list(case.valuation_input.source_snapshot_ids),
        }
        return build_model_valuation_report(
            case,
            normalized_margin,
            p10=lower,
            p50=point,
            p90=upper,
            model_version=str(model_evidence["model_version"]),
            execution_metadata=execution_metadata,
        )


def _load_liquidity_artifact() -> tuple[
    LifelinesLiquiditySurvivalAdapter,
    LiquidityArtifactEvidence,
]:
    artifact_uri = _required_env("ODP_AVM_LIQUIDITY_ARTIFACT_URI")
    expected_sha256 = _normalize_sha256(_required_env("ODP_AVM_LIQUIDITY_ARTIFACT_SHA256"))
    approved_by = _required_env("ODP_AVM_LIQUIDITY_APPROVED_BY")
    approved_at = _parse_datetime(_required_env("ODP_AVM_LIQUIDITY_APPROVED_AT"))
    dataset_snapshot_id = _required_env("ODP_AVM_LIQUIDITY_DATASET_SNAPSHOT_ID")
    artifact = _read_artifact(artifact_uri)
    observed_sha256 = f"sha256:{hashlib.sha256(artifact).hexdigest()}"
    if observed_sha256 != expected_sha256:
        raise AVMProductionExecutionError(
            "approved lifelines liquidity artifact digest verification failed"
        )
    try:
        adapter = LifelinesLiquiditySurvivalAdapter.from_artifact(artifact)
    except Exception as exc:
        raise AVMProductionExecutionError(
            "approved lifelines liquidity artifact is not executable"
        ) from exc
    configured_version = _required_env("ODP_AVM_LIQUIDITY_MODEL_VERSION")
    if adapter.model_version != configured_version:
        raise AVMProductionExecutionError(
            "lifelines artifact model version does not match approval metadata"
        )
    return adapter, LiquidityArtifactEvidence(
        artifact_uri=artifact_uri,
        artifact_sha256=observed_sha256,
        model_version=adapter.model_version,
        approved_by=approved_by,
        approved_at=approved_at,
        dataset_snapshot_id=dataset_snapshot_id,
    )


def _read_artifact(uri: str) -> bytes:
    parsed = urlparse(uri)
    if parsed.scheme in {"", "file"}:
        path = Path(unquote(parsed.path if parsed.scheme else uri))
        if not path.is_file():
            raise AVMProductionExecutionError(
                "approved lifelines liquidity artifact is unavailable"
            )
        return path.read_bytes()
    try:
        from mlflow.artifacts import download_artifacts

        downloaded = Path(download_artifacts(artifact_uri=uri))
    except Exception as exc:
        raise AVMProductionExecutionError(
            "approved lifelines liquidity artifact could not be downloaded"
        ) from exc
    if not downloaded.is_file():
        raise AVMProductionExecutionError(
            "lifelines liquidity artifact URI must resolve to one file"
        )
    return downloaded.read_bytes()


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise AVMProductionExecutionError(f"{name} is required in production")
    return value


def _normalize_sha256(value: str) -> str:
    digest = value.lower().removeprefix("sha256:")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise AVMProductionExecutionError("liquidity artifact SHA-256 is invalid")
    return f"sha256:{digest}"


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AVMProductionExecutionError("liquidity approval timestamp is invalid") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = [
    "AVMProductionExecutionError",
    "AVMProductionExecutor",
    "LiquidityArtifactEvidence",
]
