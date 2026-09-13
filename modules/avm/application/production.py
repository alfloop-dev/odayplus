from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

from modules.avm.domain import (
    AVM_FEATURE_VERSION_V1,
    AVM_FEATURE_VERSION_V2,
    NormalizedMargin,
    ValuationCase,
    ValuationReport,
    build_model_valuation_report,
    calculate_depreciation,
)
from modules.avm.domain.liquidity import LiquidityPrediction
from modules.avm.infrastructure.lifelines_survival import (
    LifelinesLiquiditySurvivalAdapter,
)


class AVMProductionExecutionError(RuntimeError):
    """Raised when an approved production AVM cannot execute."""


def avm_artifact_schema_from_environment() -> str:
    """Select the deployed model contract independently of domain input versions.

    Existing approved artifacts use v1. Deployments selecting v2 must still
    satisfy the registry's exact schema and approval checks.
    """
    schema = os.getenv("ODP_AVM_ARTIFACT_SCHEMA_VERSION", AVM_FEATURE_VERSION_V1).strip()
    if schema not in {AVM_FEATURE_VERSION_V1, AVM_FEATURE_VERSION_V2}:
        raise AVMProductionExecutionError(f"Unsupported AVM artifact schema: {schema!r}")
    return schema


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


REQUIRED_DEPRECIATION_EVIDENCE_KEYS: frozenset[str] = frozenset({
    "basis",
    "in_service_date",
    "effective_date",
    "elapsed_months",
    "useful_life_months",
    "residual_value_ratio",
    "residual",
    "accumulated_depreciation",
    "equipment_value_after_depreciation",
    "method",
    "version",
})


@dataclass(frozen=True)
class DepreciationCutoverEvidence:
    approved_by: str
    approved_at: datetime
    thresholds_reference: str
    model_version: str
    numerical_threshold_asset_delta_ratio: float
    numerical_threshold_unexplained_cohort_ratio: float
    structural_completeness_required: bool
    calibration_coverage_degradation_threshold: float

    def __post_init__(self) -> None:
        if not self.approved_by or not str(self.approved_by).strip():
            raise AVMProductionExecutionError("depreciation cutover approved_by must be specified")
        if not isinstance(self.approved_at, datetime):
            raise AVMProductionExecutionError("depreciation cutover approved_at must be a valid datetime")
        if not self.thresholds_reference or not str(self.thresholds_reference).strip():
            raise AVMProductionExecutionError("depreciation cutover thresholds_reference must be specified")
        if not self.model_version or not str(self.model_version).strip():
            raise AVMProductionExecutionError("depreciation cutover model_version must be specified")

        try:
            ratio = float(self.numerical_threshold_asset_delta_ratio)
        except (TypeError, ValueError) as exc:
            raise AVMProductionExecutionError("depreciation cutover delta threshold must be numeric") from exc
        if not math.isfinite(ratio) or ratio <= 0.0:
            raise AVMProductionExecutionError("depreciation cutover delta threshold must be a finite positive number")

        try:
            unexplained_ratio = float(self.numerical_threshold_unexplained_cohort_ratio)
        except (TypeError, ValueError) as exc:
            raise AVMProductionExecutionError("depreciation cutover unexplained cohort threshold must be numeric") from exc
        if not math.isfinite(unexplained_ratio) or not (0.0 < unexplained_ratio <= 1.0):
            raise AVMProductionExecutionError(
                "depreciation cutover unexplained cohort threshold must be a finite number between 0.0 and 1.0"
            )

        if self.structural_completeness_required is not True:
            raise AVMProductionExecutionError(
                "depreciation cutover structural completeness must be required (structural_completeness_required=True)"
            )

        try:
            calib_degradation = float(self.calibration_coverage_degradation_threshold)
        except (TypeError, ValueError) as exc:
            raise AVMProductionExecutionError(
                "depreciation cutover calibration degradation threshold must be numeric"
            ) from exc
        if not math.isfinite(calib_degradation) or not (0.0 < calib_degradation <= 1.0):
            raise AVMProductionExecutionError(
                "depreciation cutover calibration degradation threshold must be a finite positive number <= 1.0"
            )

        object.__setattr__(self, "approved_by", self.approved_by.strip())
        object.__setattr__(self, "thresholds_reference", self.thresholds_reference.strip())
        object.__setattr__(self, "model_version", self.model_version.strip())
        object.__setattr__(self, "numerical_threshold_asset_delta_ratio", ratio)
        object.__setattr__(self, "numerical_threshold_unexplained_cohort_ratio", unexplained_ratio)
        object.__setattr__(self, "structural_completeness_required", True)
        object.__setattr__(self, "calibration_coverage_degradation_threshold", calib_degradation)

    def validate_for_policy(self, active_depreciation_version: str) -> None:
        if self.model_version != active_depreciation_version:
            raise AVMProductionExecutionError(
                f"depreciation cutover evidence model_version {self.model_version!r} "
                f"does not match active depreciation policy {active_depreciation_version!r}"
            )
        if (
            not math.isfinite(self.numerical_threshold_asset_delta_ratio)
            or self.numerical_threshold_asset_delta_ratio <= 0.0
        ):
            raise AVMProductionExecutionError(
                "depreciation cutover evidence numerical_threshold_asset_delta_ratio is not valid"
            )
        if (
            not math.isfinite(self.numerical_threshold_unexplained_cohort_ratio)
            or not (0.0 < self.numerical_threshold_unexplained_cohort_ratio <= 1.0)
        ):
            raise AVMProductionExecutionError(
                "depreciation cutover evidence numerical_threshold_unexplained_cohort_ratio is not valid"
            )
        if self.structural_completeness_required is not True:
            raise AVMProductionExecutionError(
                "depreciation cutover evidence structural_completeness_required must be True"
            )
        if (
            not math.isfinite(self.calibration_coverage_degradation_threshold)
            or not (0.0 < self.calibration_coverage_degradation_threshold <= 1.0)
        ):
            raise AVMProductionExecutionError(
                "depreciation cutover evidence calibration_coverage_degradation_threshold is not valid"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved_by": self.approved_by,
            "approved_at": (
                self.approved_at.isoformat()
                if hasattr(self.approved_at, "isoformat")
                else str(self.approved_at)
            ),
            "thresholds_reference": self.thresholds_reference,
            "model_version": self.model_version,
            "numerical_threshold_asset_delta_ratio": self.numerical_threshold_asset_delta_ratio,
            "numerical_threshold_unexplained_cohort_ratio": self.numerical_threshold_unexplained_cohort_ratio,
            "structural_completeness_required": self.structural_completeness_required,
            "calibration_coverage_degradation_threshold": self.calibration_coverage_degradation_threshold,
        }


class AVMProductionExecutor:
    """Execute approved AVM and liquidity artifacts without formula fallback."""

    def __init__(
        self,
        *,
        model_runtime: ModelRuntime,
        liquidity_runtime: LiquidityRuntime,
        liquidity_evidence: LiquidityArtifactEvidence,
        depreciation_cutover_evidence: DepreciationCutoverEvidence | None = None,
        expected_feature_schema_version: str | None = None,
    ) -> None:
        self.model_runtime = model_runtime
        self.liquidity_runtime = liquidity_runtime
        self.liquidity_evidence = liquidity_evidence
        self.depreciation_cutover_evidence = depreciation_cutover_evidence
        self.expected_feature_schema_version = (
            expected_feature_schema_version or avm_artifact_schema_from_environment()
        )

    @classmethod
    def from_environment(
        cls,
        *,
        model_runtime: ModelRuntime | None = None,
        expected_feature_schema_version: str | None = None,
    ) -> AVMProductionExecutor:
        try:
            cutover_evidence = _load_depreciation_cutover_evidence_optional()
            if model_runtime is None:
                from models.shared_ml.production_contracts import (
                    production_model_names,
                )
                from models.shared_ml.production_runtime import MlflowProductionModelRuntime

                model_runtime = MlflowProductionModelRuntime.from_environment(
                    model_names=production_model_names(("avm",))
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
            depreciation_cutover_evidence=cutover_evidence,
            expected_feature_schema_version=expected_feature_schema_version,
        )

    def execute(
        self,
        case: ValuationCase,
        normalized_margin: NormalizedMargin,
        *,
        depreciation_version_pin: str | None = None,
    ) -> ValuationReport:
        dep_calc = calculate_depreciation(
            case.valuation_input,
            depreciation_version_pin=depreciation_version_pin,
        )

        if dep_calc.depreciation_applied:
            if self.depreciation_cutover_evidence is None:
                raise AVMProductionExecutionError(
                    "production v1 depreciation activation requires authentic Finance approval and threshold evidence"
                )
            self.depreciation_cutover_evidence.validate_for_policy(dep_calc.depreciation_version)
            if (
                self.depreciation_cutover_evidence.structural_completeness_required
                and (
                    dep_calc.evidence is None
                    or not REQUIRED_DEPRECIATION_EVIDENCE_KEYS <= set(dep_calc.evidence)
                )
            ):
                raise AVMProductionExecutionError(
                    "depreciation evidence is structurally incomplete"
                )

        row = {
            **case.valuation_input.to_dict(),
            "normalized_gm": normalized_margin.normalized_gm,
            "normalization_confidence": normalized_margin.confidence,
            "view_version": self.expected_feature_schema_version,
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
                expected_feature_schema_version=self.expected_feature_schema_version,
            )
            lower = float(inference.lower[0])
            point = float(inference.point[0])
            upper = float(inference.upper[0])
        except Exception as exc:
            raise AVMProductionExecutionError(
                "approved AVM production model failed to execute"
            ) from exc

        # R3: Validate the raw model interval before adjustment.
        if min(lower, point, upper) < 0 or not lower <= point <= upper:
            raise AVMProductionExecutionError(
                "approved AVM model returned an invalid valuation interval"
            )

        if dep_calc.delta_from_undepreciated != 0.0:
            lower = max(0.0, round(lower + dep_calc.delta_from_undepreciated, 2))
            point = max(0.0, round(point + dep_calc.delta_from_undepreciated, 2))
            upper = max(0.0, round(upper + dep_calc.delta_from_undepreciated, 2))

        if not lower <= point <= upper:
            raise AVMProductionExecutionError(
                "depreciation-adjusted valuation interval is not ordered"
            )

        model_evidence = inference.to_audit_metadata()
        execution_metadata: dict[str, Any] = {
            "mode": "production_oss",
            "model": model_evidence,
            "liquidity": {
                **self.liquidity_evidence.to_dict(),
                "library_version": version("lifelines"),
                "prediction": liquidity.to_dict(),
            },
            "source_snapshot_ids": list(case.valuation_input.source_snapshot_ids),
        }
        if dep_calc.evidence is not None:
            execution_metadata["depreciation"] = dep_calc.evidence
        if self.depreciation_cutover_evidence is not None and dep_calc.depreciation_applied:
            execution_metadata["depreciation_cutover_approval"] = (
                self.depreciation_cutover_evidence.to_dict()
            )

        return build_model_valuation_report(
            case,
            normalized_margin,
            p10=lower,
            p50=point,
            p90=upper,
            model_version=str(model_evidence["model_version"]),
            execution_metadata=execution_metadata,
            depreciation_version=dep_calc.depreciation_version,
            depreciation_applied=dep_calc.depreciation_applied,
            asset_p50=dep_calc.asset_p50,
            depreciation_evidence=dep_calc.evidence,
            feature_version=self.expected_feature_schema_version,
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
        raise AVMProductionExecutionError("approval timestamp is invalid") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _load_depreciation_cutover_evidence_optional() -> DepreciationCutoverEvidence | None:
    approved_by = os.getenv("ODP_AVM_DEPRECIATION_CUTOVER_APPROVED_BY", "").strip()
    approved_at_raw = os.getenv("ODP_AVM_DEPRECIATION_CUTOVER_APPROVED_AT", "").strip()
    thresholds_ref = os.getenv("ODP_AVM_DEPRECIATION_CUTOVER_THRESHOLDS_REFERENCE", "").strip()
    ratio_raw = os.getenv("ODP_AVM_DEPRECIATION_CUTOVER_DELTA_THRESHOLD", "").strip()
    unexplained_raw = os.getenv("ODP_AVM_DEPRECIATION_CUTOVER_UNEXPLAINED_COHORT_THRESHOLD", "").strip()
    structural_raw = os.getenv("ODP_AVM_DEPRECIATION_CUTOVER_STRUCTURAL_COMPLETENESS_REQUIRED", "").strip()
    calib_raw = os.getenv("ODP_AVM_DEPRECIATION_CUTOVER_CALIBRATION_DEGRADATION_THRESHOLD", "").strip()
    model_ver = os.getenv("ODP_AVM_DEPRECIATION_CUTOVER_MODEL_VERSION", "").strip()

    cutover_keys_present = [
        bool(v)
        for v in (
            approved_by,
            approved_at_raw,
            thresholds_ref,
            ratio_raw,
            unexplained_raw,
            structural_raw,
            calib_raw,
            model_ver,
        )
    ]
    if not any(cutover_keys_present):
        return None

    if not approved_by:
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_APPROVED_BY is required for cutover")
    if not approved_at_raw:
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_APPROVED_AT is required for cutover")
    if not thresholds_ref:
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_THRESHOLDS_REFERENCE is required for cutover")
    if not ratio_raw:
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_DELTA_THRESHOLD is required for cutover")
    if not unexplained_raw:
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_UNEXPLAINED_COHORT_THRESHOLD is required for cutover")
    if not structural_raw:
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_STRUCTURAL_COMPLETENESS_REQUIRED is required for cutover")
    if not calib_raw:
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_CALIBRATION_DEGRADATION_THRESHOLD is required for cutover")
    if not model_ver:
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_MODEL_VERSION is required for cutover")

    approved_at = _parse_datetime(approved_at_raw)
    try:
        ratio = float(ratio_raw)
    except ValueError as exc:
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_DELTA_THRESHOLD must be numeric") from exc
    if not math.isfinite(ratio) or ratio <= 0.0:
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_DELTA_THRESHOLD must be finite and positive")

    try:
        unexplained = float(unexplained_raw)
    except ValueError as exc:
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_UNEXPLAINED_COHORT_THRESHOLD must be numeric") from exc
    if not math.isfinite(unexplained) or not (0.0 < unexplained <= 1.0):
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_UNEXPLAINED_COHORT_THRESHOLD must be finite between 0.0 and 1.0")

    structural_bool = structural_raw.lower() in {"true", "1", "yes"}
    if not structural_bool:
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_STRUCTURAL_COMPLETENESS_REQUIRED must be true")

    try:
        calib = float(calib_raw)
    except ValueError as exc:
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_CALIBRATION_DEGRADATION_THRESHOLD must be numeric") from exc
    if not math.isfinite(calib) or not (0.0 < calib <= 1.0):
        raise AVMProductionExecutionError("ODP_AVM_DEPRECIATION_CUTOVER_CALIBRATION_DEGRADATION_THRESHOLD must be finite positive <= 1.0")

    return DepreciationCutoverEvidence(
        approved_by=approved_by,
        approved_at=approved_at,
        thresholds_reference=thresholds_ref,
        model_version=model_ver,
        numerical_threshold_asset_delta_ratio=ratio,
        numerical_threshold_unexplained_cohort_ratio=unexplained,
        structural_completeness_required=structural_bool,
        calibration_coverage_degradation_threshold=calib,
    )


__all__ = [
    "AVMProductionExecutionError",
    "AVMProductionExecutor",
    "DepreciationCutoverEvidence",
    "LiquidityArtifactEvidence",
    "REQUIRED_DEPRECIATION_EVIDENCE_KEYS",
]
