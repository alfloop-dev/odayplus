"""Evidently-backed drift snapshots for released model inputs."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pandas as pd

from models.shared_ml import OssCapability, require_oss_capability


@dataclass(frozen=True)
class EvidentlyDriftResult:
    snapshot_id: str
    drift_detected: bool
    drifted_columns: int
    drift_share: float
    report_json: str
    engine: str = "evidently"
    reference_snapshot_id: str | None = None
    current_snapshot_id: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    cohort_key: str | None = None
    prediction_columns: tuple[str, ...] = ()
    prediction_output_types: Mapping[str, str] | None = None
    drifted_column_names: tuple[str, ...] = ()
    decision_policy_version_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "snapshot_id": self.snapshot_id,
            "drift_detected": self.drift_detected,
            "drifted_columns": self.drifted_columns,
            "drift_share": self.drift_share,
            "engine": self.engine,
            "report": json.loads(self.report_json),
        }
        for key, value in (
            ("reference_snapshot_id", self.reference_snapshot_id),
            ("current_snapshot_id", self.current_snapshot_id),
            ("model_name", self.model_name),
            ("model_version", self.model_version),
            ("cohort_key", self.cohort_key),
            ("decision_policy_version_id", self.decision_policy_version_id),
        ):
            if value is not None:
                data[key] = value
        if self.prediction_columns:
            data["prediction_columns"] = list(self.prediction_columns)
        if self.prediction_output_types:
            data["prediction_output_types"] = dict(self.prediction_output_types)
        if self.drifted_column_names:
            data["drifted_column_names"] = list(self.drifted_column_names)
        return data


class EvidentlyDriftMonitor:
    def run(
        self,
        *,
        reference_rows: Sequence[Mapping[str, Any]],
        current_rows: Sequence[Mapping[str, Any]],
        drift_share_threshold: float = 0.5,
        snapshot_id: str | None = None,
    ) -> EvidentlyDriftResult:
        if not reference_rows or not current_rows:
            raise ValueError("Evidently drift monitoring requires reference and current rows")
        require_oss_capability(OssCapability.MODEL_MONITORING)
        from evidently import Report
        from evidently.presets import DataDriftPreset

        reference = pd.DataFrame(reference_rows)
        current = pd.DataFrame(current_rows)
        if set(reference.columns) != set(current.columns):
            raise ValueError("reference and current drift datasets must have identical columns")

        evaluation = Report([DataDriftPreset(drift_share=drift_share_threshold)]).run(
            current, reference
        )
        return self._result(
            evaluation=evaluation,
            drift_share_threshold=drift_share_threshold,
            snapshot_id=snapshot_id,
        )

    def run_prediction(
        self,
        *,
        reference_rows: Sequence[Mapping[str, Any]],
        current_rows: Sequence[Mapping[str, Any]],
        model_name: str,
        model_version: str,
        cohort_key: str,
        prediction_columns: Sequence[str],
        output_types: Mapping[str, str] | None = None,
        reference_snapshot_id: str,
        current_snapshot_id: str,
        policy: Any | None = None,
        drift_share_threshold: float | None = None,
        reference_cohort_key: str | None = None,
        current_cohort_key: str | None = None,
    ) -> EvidentlyDriftResult:
        """Evaluate only prediction outputs within one governed cohort.

        Evidently 0.7 does not provide a dedicated prediction preset. Prediction
        drift is therefore represented by a ``DataDriftPreset`` over an
        explicitly selected output-only frame. The caller must provide a
        versioned policy (or an already-resolved threshold for low-level
        tooling); the application production entry requires the policy.
        """

        normalized_model_name = _required_text(model_name, "model_name")
        normalized_model_version = _required_text(model_version, "model_version")
        normalized_cohort = _required_text(cohort_key, "cohort_key")
        reference_id = _required_text(reference_snapshot_id, "reference_snapshot_id")
        current_id = _required_text(current_snapshot_id, "current_snapshot_id")
        if reference_id == current_id:
            raise ValueError("reference and current snapshot ids must differ")
        if reference_cohort_key is not None and reference_cohort_key != normalized_cohort:
            raise ValueError("reference cohort does not match the requested cohort")
        if current_cohort_key is not None and current_cohort_key != normalized_cohort:
            raise ValueError("current cohort does not match the requested cohort")

        columns = _normalize_prediction_columns(prediction_columns)
        types = _normalize_output_types(
            output_types,
            columns=columns,
            reference_rows=reference_rows,
            current_rows=current_rows,
        )
        _validate_prediction_rows(
            reference_rows,
            model_name=normalized_model_name,
            model_version=normalized_model_version,
            cohort_key=normalized_cohort,
            columns=columns,
            output_types=types,
            snapshot_label="reference",
        )
        _validate_prediction_rows(
            current_rows,
            model_name=normalized_model_name,
            model_version=normalized_model_version,
            cohort_key=normalized_cohort,
            columns=columns,
            output_types=types,
            snapshot_label="current",
        )
        threshold = _effective_drift_share_threshold(
            policy=policy,
            model_name=normalized_model_name,
            requested=drift_share_threshold,
        )
        require_oss_capability(OssCapability.MODEL_MONITORING)
        from evidently import Report
        from evidently.presets import DataDriftPreset

        reference = pd.DataFrame(
            [{column: row[column] for column in columns} for row in reference_rows],
            columns=list(columns),
        )
        current = pd.DataFrame(
            [{column: row[column] for column in columns} for row in current_rows],
            columns=list(columns),
        )
        evaluation = Report([DataDriftPreset(drift_share=threshold)]).run(
            current, reference
        )
        return self._result(
            evaluation=evaluation,
            drift_share_threshold=threshold,
            reference_snapshot_id=reference_id,
            current_snapshot_id=current_id,
            model_name=normalized_model_name,
            model_version=normalized_model_version,
            cohort_key=normalized_cohort,
            prediction_columns=columns,
            prediction_output_types=types,
            decision_policy_version_id=(
                str(policy.policy_version_id) if policy is not None else None
            ),
        )

    def run_prediction_drift(
        self,
        *,
        reference_rows: Sequence[Mapping[str, Any]],
        current_rows: Sequence[Mapping[str, Any]],
        model_name: str,
        model_version: str,
        cohort_key: str | None = None,
        cohort: str | None = None,
        prediction_columns: Sequence[str] | None = None,
        prediction_output_columns: Sequence[str] | None = None,
        output_types: Mapping[str, str] | None = None,
        reference_snapshot_id: str,
        current_snapshot_id: str,
        policy: Any | None = None,
        decision_policy: Any | None = None,
        drift_share_threshold: float | None = None,
    ) -> EvidentlyDriftResult:
        """Compatibility-named entry point for prediction drift callers."""

        selected_cohort = cohort_key or cohort
        selected_columns = prediction_columns or prediction_output_columns
        selected_policy = policy or decision_policy
        if selected_cohort is None:
            raise ValueError("cohort_key is required")
        if selected_columns is None:
            raise ValueError("prediction_columns is required")
        return self.run_prediction(
            reference_rows=reference_rows,
            current_rows=current_rows,
            model_name=model_name,
            model_version=model_version,
            cohort_key=selected_cohort,
            prediction_columns=selected_columns,
            output_types=output_types,
            reference_snapshot_id=reference_snapshot_id,
            current_snapshot_id=current_snapshot_id,
            policy=selected_policy,
            drift_share_threshold=drift_share_threshold,
        )

    @staticmethod
    def _result(
        *,
        evaluation: Any,
        drift_share_threshold: float,
        snapshot_id: str | None = None,
        reference_snapshot_id: str | None = None,
        current_snapshot_id: str | None = None,
        model_name: str | None = None,
        model_version: str | None = None,
        cohort_key: str | None = None,
        prediction_columns: tuple[str, ...] = (),
        prediction_output_types: Mapping[str, str] | None = None,
        decision_policy_version_id: str | None = None,
    ) -> EvidentlyDriftResult:
        payload = evaluation.dict()
        summary = next(
            (
                metric.get("value", {})
                for metric in payload.get("metrics", [])
                if metric.get("metric_name", "").startswith("DriftedColumnsCount")
            ),
            {},
        )
        count = int(summary.get("count", 0) or 0)
        share = float(summary.get("share", 0.0) or 0.0)
        drifted_column_names = _drifted_column_names(payload)
        return EvidentlyDriftResult(
            snapshot_id=snapshot_id or f"evidently-{uuid4()}",
            drift_detected=share >= drift_share_threshold,
            drifted_columns=count,
            drift_share=share,
            report_json=evaluation.json(),
            reference_snapshot_id=reference_snapshot_id,
            current_snapshot_id=current_snapshot_id,
            model_name=model_name,
            model_version=model_version,
            cohort_key=cohort_key,
            prediction_columns=prediction_columns,
            prediction_output_types=prediction_output_types,
            drifted_column_names=drifted_column_names,
            decision_policy_version_id=decision_policy_version_id,
        )


def _required_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _normalize_prediction_columns(columns: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(_required_text(column, "prediction column") for column in columns)
    if not normalized:
        raise ValueError("prediction_columns must contain at least one output column")
    if len(set(normalized)) != len(normalized):
        raise ValueError("prediction_columns must not contain duplicates")
    return normalized


def _normalize_output_type(value: Any, column: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"numeric", "number", "float", "int", "integer", "regression"}:
        return "numeric"
    if normalized in {
        "categorical",
        "category",
        "string",
        "str",
        "bool",
        "boolean",
        "classification",
    }:
        return "categorical"
    raise ValueError(
        f"prediction output type for {column!r} must be numeric or categorical"
    )


def _normalize_output_types(
    output_types: Mapping[str, str] | None,
    *,
    columns: tuple[str, ...],
    reference_rows: Sequence[Mapping[str, Any]],
    current_rows: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    if output_types is not None:
        unknown = set(output_types) - set(columns)
        missing = set(columns) - set(output_types)
        if unknown or missing:
            raise ValueError(
                "prediction output types must exactly match prediction columns"
            )
        return {
            column: _normalize_output_type(output_types[column], column)
            for column in columns
        }

    inferred: dict[str, str] = {}
    for column in columns:
        values = [
            row[column]
            for row in (*reference_rows, *current_rows)
            if column in row and row[column] is not None
        ]
        if not values:
            raise ValueError(f"prediction output column {column!r} has no values")
        inferred[column] = (
            "numeric"
            if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values)
            else "categorical"
        )
    return inferred


def _validate_prediction_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    model_name: str,
    model_version: str,
    cohort_key: str,
    columns: tuple[str, ...],
    output_types: Mapping[str, str],
    snapshot_label: str,
) -> None:
    if not rows:
        raise ValueError(f"{snapshot_label} prediction snapshot must not be empty")
    for index, row in enumerate(rows):
        for metadata_field, expected in (
            ("model_name", model_name),
            ("model_version", model_version),
            ("cohort_key", cohort_key),
        ):
            if metadata_field in row and str(row[metadata_field]) != expected:
                raise ValueError(
                    f"{snapshot_label} row {index} has {metadata_field} outside the requested cohort"
                )
        for column in columns:
            if column not in row or row[column] is None:
                raise ValueError(
                    f"{snapshot_label} row {index} is missing prediction output {column!r}"
                )
            value = row[column]
            if output_types[column] == "numeric":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(
                        f"{snapshot_label} output {column!r} must be numeric"
                    )
                if not math.isfinite(float(value)):
                    raise ValueError(
                        f"{snapshot_label} output {column!r} must be finite numeric data"
                    )


def _effective_drift_share_threshold(
    *,
    policy: Any | None,
    model_name: str,
    requested: float | None,
) -> float:
    governed: float | None = None
    if policy is not None:
        governed = prediction_drift_threshold_from_policy(policy, model_name=model_name)
    if governed is None and requested is None:
        raise ValueError(
            "prediction drift requires a DecisionPolicy with drift_share_threshold"
        )
    threshold = governed if governed is not None else float(requested)
    if requested is not None and governed is not None:
        requested_value = float(requested)
        if requested_value > governed:
            raise ValueError(
                "prediction drift threshold cannot weaken the DecisionPolicy threshold"
            )
        threshold = requested_value
    if not 0 < threshold <= 1:
        raise ValueError("prediction drift share threshold must be greater than 0 and at most 1")
    return threshold


def prediction_drift_threshold_from_policy(
    policy: Any,
    *,
    model_name: str | None = None,
) -> float:
    """Read the prediction-drift threshold from a versioned policy row."""

    parameters = getattr(policy, "parameters", None)
    if not isinstance(parameters, Mapping):
        raise ValueError("DecisionPolicy parameters must be an object")
    config: Any = parameters.get("prediction_drift")
    if (
        isinstance(config, Mapping)
        and model_name is not None
        and model_name in config
        and not any(
            key in config for key in ("drift_share_threshold", "threshold", "value")
        )
    ):
        config = config[model_name]
    if config is None:
        config = parameters.get("prediction_drift_by_model")
        if isinstance(config, Mapping) and model_name is not None:
            config = config.get(model_name)
    if config is None:
        config = parameters.get("prediction_drift_threshold")
    if config is None:
        config = parameters.get("drift_share_threshold")
    if isinstance(config, Mapping):
        config_mapping = config
        config = config_mapping.get("drift_share_threshold")
        if config is None:
            config = config_mapping.get("threshold")
        if config is None:
            config = config_mapping.get("value")
    if config is None:
        metric_configs = parameters.get("metric_thresholds_by_model")
        if isinstance(metric_configs, Mapping) and model_name is not None:
            metric_configs = metric_configs.get(model_name)
        if isinstance(metric_configs, Mapping):
            for metric_name in ("prediction_drift_share", "drift_share"):
                metric = metric_configs.get(metric_name)
                if isinstance(metric, Mapping):
                    config = metric.get("min_value") or metric.get("threshold")
                    if config is not None:
                        break
    if config is None:
        metric_configs = parameters.get("metric_thresholds")
        if isinstance(metric_configs, Mapping):
            for metric_name in ("prediction_drift_share", "drift_share"):
                metric = metric_configs.get(metric_name)
                if isinstance(metric, Mapping):
                    config = metric.get("min_value") or metric.get("threshold")
                    if config is not None:
                        break
    if isinstance(config, Mapping):
        config = config.get("threshold") or config.get("value")
    if isinstance(config, bool) or config is None:
        raise ValueError(
            f"DecisionPolicy {getattr(policy, 'policy_version_id', '<unknown>')} "
            "does not declare prediction drift threshold"
        )
    try:
        threshold = float(config)
    except (TypeError, ValueError) as exc:
        raise ValueError("DecisionPolicy prediction drift threshold must be numeric") from exc
    if not 0 < threshold <= 1:
        raise ValueError("DecisionPolicy prediction drift threshold must be greater than 0 and at most 1")
    return threshold


def _drifted_column_names(payload: Mapping[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    for metric in payload.get("metrics", []):
        if not isinstance(metric, Mapping):
            continue
        name = str(metric.get("metric_name", ""))
        if "ValueDrift" not in name:
            continue
        column = name.split("(", 1)[-1].rstrip(")")
        if "=" in column:
            column = column.split("=", 1)[-1]
        if column and column not in names:
            value = metric.get("value")
            if isinstance(value, Mapping) and value.get("drift_detected") is False:
                continue
            names.append(column)
    return tuple(names)


__all__ = [
    "EvidentlyDriftMonitor",
    "EvidentlyDriftResult",
    "prediction_drift_threshold_from_policy",
]
