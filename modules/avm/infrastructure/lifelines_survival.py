from __future__ import annotations

import base64
import hashlib
import json
import pickle
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib.metadata import version
from importlib.util import find_spec
from typing import Any

import numpy as np

from modules.avm.domain.liquidity import (
    LiquidityPrediction,
    LiquidityTrainingRecord,
    SurvivalModelCapability,
)

LIFELINES_ARTIFACT_SCHEMA_VERSION = 1
LIFELINES_LIQUIDITY_MODEL_VERSION = "avm-liquidity-coxph-v1"


class SurvivalDependencyUnavailableError(RuntimeError):
    """Raised when the lifelines runtime is not installed."""


class SurvivalModelNotFittedError(RuntimeError):
    """Raised when prediction or serialization precedes model fitting."""


class LifelinesLiquiditySurvivalAdapter:
    adapter_name = "lifelines_coxph"
    dependency = "lifelines"

    def __init__(
        self,
        *,
        penalizer: float = 0.1,
        model_version: str = LIFELINES_LIQUIDITY_MODEL_VERSION,
    ) -> None:
        if penalizer < 0:
            raise ValueError("penalizer must be non-negative")
        self.penalizer = float(penalizer)
        self.model_version = model_version
        self._model: Any | None = None
        self._feature_names: tuple[str, ...] = ()
        self._training_metadata: dict[str, Any] = {}

    @classmethod
    def capability(cls) -> SurvivalModelCapability:
        installed = find_spec(cls.dependency) is not None
        return SurvivalModelCapability(
            adapter_name=cls.adapter_name,
            dependency=cls.dependency,
            available=installed,
            reason="available" if installed else f"dependency_missing:{cls.dependency}",
        )

    @property
    def is_fitted(self) -> bool:
        return self._model is not None

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self._feature_names

    @property
    def training_metadata(self) -> dict[str, Any]:
        return dict(self._training_metadata)

    def fit(
        self,
        records: Sequence[LiquidityTrainingRecord | Mapping[str, Any]],
    ) -> LifelinesLiquiditySurvivalAdapter:
        CoxPHFitter, pandas = _require_lifelines()
        items = tuple(_coerce_record(record) for record in records)
        feature_names = _validate_training_records(items)
        rows = [
            {
                **{name: float(record.features[name]) for name in feature_names},
                "duration_days": float(record.duration_days),
                "sold": bool(record.sold),
            }
            for record in items
        ]
        frame = pandas.DataFrame(rows)
        model = CoxPHFitter(penalizer=self.penalizer)
        model.fit(
            frame,
            duration_col="duration_days",
            event_col="sold",
            show_progress=False,
        )
        self._model = model
        self._feature_names = feature_names
        self._training_metadata = {
            "sample_count": len(items),
            "event_count": sum(record.sold for record in items),
            "censored_count": sum(not record.sold for record in items),
            "max_observed_days": max(record.duration_days for record in items),
            "trained_at": datetime.now(UTC).isoformat(),
            "library": "lifelines",
            "library_version": version("lifelines"),
            "estimator": "CoxPHFitter",
        }
        return self

    def predict(self, features: Mapping[str, float]) -> LiquidityPrediction:
        model = self._require_fitted()
        _CoxPHFitter, pandas = _require_lifelines()
        row = _validate_prediction_features(features, self._feature_names)
        frame = pandas.DataFrame([row], columns=self._feature_names)
        survival = model.predict_survival_function(frame, times=[30.0, 90.0])
        survival_30d = float(survival.iloc[0, 0])
        survival_90d = float(survival.iloc[1, 0])
        expected_days = float(model.predict_expectation(frame).iloc[0])
        if not np.isfinite(expected_days) or expected_days < 0:
            raise ValueError("lifelines returned a non-finite expected duration")
        return LiquidityPrediction(
            sale_probability_30d=round(_probability(1.0 - survival_30d), 6),
            sale_probability_90d=round(_probability(1.0 - survival_90d), 6),
            expected_days=round(expected_days, 4),
            model_version=self.model_version,
            feature_names=self._feature_names,
        )

    def serialize_artifact(self) -> str:
        model = self._require_fitted()
        payload = pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)
        document = {
            "artifact_schema_version": LIFELINES_ARTIFACT_SCHEMA_VERSION,
            "adapter_name": self.adapter_name,
            "model_version": self.model_version,
            "feature_names": list(self._feature_names),
            "penalizer": self.penalizer,
            "training_metadata": self._training_metadata,
            "payload_encoding": "base64+pickle",
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "payload": base64.b64encode(payload).decode("ascii"),
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_artifact(cls, artifact: str | bytes) -> LifelinesLiquiditySurvivalAdapter:
        CoxPHFitter, _pandas = _require_lifelines()
        raw = artifact.decode("utf-8") if isinstance(artifact, bytes) else artifact
        document = json.loads(raw)
        if document.get("artifact_schema_version") != LIFELINES_ARTIFACT_SCHEMA_VERSION:
            raise ValueError("unsupported lifelines liquidity artifact schema")
        if document.get("adapter_name") != cls.adapter_name:
            raise ValueError("artifact adapter does not match lifelines CoxPH")
        payload = base64.b64decode(document["payload"], validate=True)
        if hashlib.sha256(payload).hexdigest() != document.get("payload_sha256"):
            raise ValueError("lifelines liquidity artifact checksum mismatch")
        model = pickle.loads(payload)
        if not isinstance(model, CoxPHFitter):
            raise ValueError("artifact payload is not a lifelines CoxPHFitter")
        adapter = cls(
            penalizer=float(document["penalizer"]),
            model_version=str(document["model_version"]),
        )
        adapter._model = model
        adapter._feature_names = tuple(str(name) for name in document["feature_names"])
        adapter._training_metadata = dict(document["training_metadata"])
        return adapter

    def _require_fitted(self) -> Any:
        if self._model is None:
            raise SurvivalModelNotFittedError("fit or load a liquidity survival model first")
        return self._model


def _require_lifelines() -> tuple[Any, Any]:
    try:
        import pandas
        from lifelines import CoxPHFitter
    except ModuleNotFoundError as exc:
        raise SurvivalDependencyUnavailableError(
            "lifelines and pandas are required for AVM liquidity survival modeling"
        ) from exc
    return CoxPHFitter, pandas


def _coerce_record(
    record: LiquidityTrainingRecord | Mapping[str, Any],
) -> LiquidityTrainingRecord:
    if isinstance(record, LiquidityTrainingRecord):
        return record
    return LiquidityTrainingRecord.from_mapping(record)


def _validate_training_records(
    records: Sequence[LiquidityTrainingRecord],
) -> tuple[str, ...]:
    if not records:
        raise ValueError("at least one liquidity training record is required")
    feature_names = tuple(sorted(records[0].features))
    if not feature_names:
        raise ValueError("at least one survival feature is required")
    if len(records) <= len(feature_names) + 2:
        raise ValueError("survival training requires more rows than features plus two")
    event_count = sum(record.sold for record in records)
    if event_count < 2:
        raise ValueError("survival training requires at least two observed sales")
    for record in records:
        if record.duration_days <= 0 or not np.isfinite(record.duration_days):
            raise ValueError("duration_days must be finite and positive")
        if tuple(sorted(record.features)) != feature_names:
            raise ValueError("all survival records must use the same feature set")
        values = np.asarray([record.features[name] for name in feature_names], dtype=float)
        if not np.all(np.isfinite(values)):
            raise ValueError("survival features must be finite")
    return feature_names


def _validate_prediction_features(
    features: Mapping[str, float],
    feature_names: tuple[str, ...],
) -> dict[str, float]:
    if tuple(sorted(features)) != feature_names:
        raise ValueError(f"prediction features must exactly match {list(feature_names)}")
    row = {name: float(features[name]) for name in feature_names}
    if not np.all(np.isfinite(np.asarray(list(row.values()), dtype=float))):
        raise ValueError("prediction features must be finite")
    return row


def _probability(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


__all__ = [
    "LIFELINES_ARTIFACT_SCHEMA_VERSION",
    "LIFELINES_LIQUIDITY_MODEL_VERSION",
    "LifelinesLiquiditySurvivalAdapter",
    "SurvivalDependencyUnavailableError",
    "SurvivalModelNotFittedError",
]
