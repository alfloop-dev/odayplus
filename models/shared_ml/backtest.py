from __future__ import annotations

import math
from collections.abc import Callable


def calculate_mape(actuals: list[float], predictions: list[float]) -> float:
    """Calculate Mean Absolute Percentage Error (MAPE)."""
    valid_count = 0
    total_abs_pct_error = 0.0
    for act, pred in zip(actuals, predictions, strict=False):
        if abs(act) > 1e-9:
            total_abs_pct_error += abs((act - pred) / act)
            valid_count += 1
    return total_abs_pct_error / valid_count if valid_count > 0 else 0.0

def calculate_rmse(actuals: list[float], predictions: list[float]) -> float:
    """Calculate Root Mean Squared Error (RMSE)."""
    n = len(actuals)
    if n == 0:
        return 0.0
    mse = sum((act - pred) ** 2 for act, pred in zip(actuals, predictions, strict=False)) / n
    return math.sqrt(mse)

def calculate_mae(actuals: list[float], predictions: list[float]) -> float:
    """Calculate Mean Absolute Error (MAE)."""
    n = len(actuals)
    if n == 0:
        return 0.0
    return sum(abs(act - pred) for act, pred in zip(actuals, predictions, strict=False)) / n

def run_rolling_backtest(
    model_predict_fn: Callable[[list[float], int], list[float]],
    series: list[float],
    horizons: list[int],
    min_train_size: int,
    step_size: int = 1,
) -> dict[int, dict[str, float]]:
    """Execute rolling-origin backtesting over a single time series.
    
    Args:
        model_predict_fn: A function that takes (history: list[float], horizon: int) 
                         and returns a list of H predictions.
        series: The full historical time series of float values.
        horizons: List of horizons (e.g. [4, 8, 12, 24]) to calculate metrics for.
        min_train_size: The minimum size of the history to start making predictions.
        step_size: How many steps to roll forward the origin.
        
    Returns:
        A dictionary mapping horizon (int) to a metrics dict containing 'mape', 'rmse', 'mae'.
    """
    if not series or not horizons:
        return {}
        
    max_h = max(horizons)
    n_points = len(series)
    
    # Store predictions and actuals per horizon
    # Key: horizon, Value: (actuals, predictions)
    horizon_data: dict[int, tuple[list[float], list[float]]] = {
        h: ([], []) for h in horizons
    }
    
    # Rolling origin loop
    # The origin is the last index of the available training data
    origin = min_train_size
    while origin + max_h <= n_points:
        history = series[:origin]
        
        # Predict up to max horizon
        predictions = model_predict_fn(history, max_h)
        
        # Collect predictions for each horizon
        for h in horizons:
            pred_val = predictions[h - 1]
            act_val = series[origin + h - 1]
            horizon_data[h][0].append(act_val)
            horizon_data[h][1].append(pred_val)
            
        origin += step_size
        
    # Calculate metrics for each horizon
    results = {}
    for h in horizons:
        actuals, predictions = horizon_data[h]
        if not actuals:
            continue
            
        results[h] = {
            "mape": round(calculate_mape(actuals, predictions), 6),
            "rmse": round(calculate_rmse(actuals, predictions), 6),
            "mae": round(calculate_mae(actuals, predictions), 6),
        }
        
    return results


from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from shared.governance.decision_policy import DecisionPolicy

from models.shared_ml.validation import (
    MetricThreshold,
    ValidationRuleFailure,
    ValidationStatus,
    effective_thresholds,
)


@dataclass(frozen=True)
class BacktestReceipt:
    """Auditable, immutable receipt of a model candidate backtest evaluation.

    Binds the model version, dataset snapshot, code version (git SHA), and
    governing DecisionPolicy version. Required for FULL and CANARY release admission.
    """

    receipt_id: str
    model_name: str
    model_version: str
    dataset_snapshot_id: str
    code_version: str
    decision_policy_version_id: str
    status: ValidationStatus = ValidationStatus.PASSED
    metrics: Mapping[str, float] = field(default_factory=dict)
    baseline_metrics: Mapping[str, float] = field(default_factory=dict)
    thresholds: Sequence[MetricThreshold] = ()
    failed_rules: Sequence[ValidationRuleFailure] = ()
    horizon_metrics: Mapping[str | int, Mapping[str, float]] = field(default_factory=dict)
    calibration_summary: Mapping[str, Any] = field(default_factory=dict)
    requested_by: str = "system"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    audit_event_id: str | None = None
    report_artifact_uri: str | None = None
    report_sha256: str | None = None

    def __post_init__(self) -> None:
        required = {
            "receipt_id": self.receipt_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "code_version": self.code_version,
            "decision_policy_version_id": self.decision_policy_version_id,
        }
        missing = [k for k, v in required.items() if not str(v or "").strip()]
        if missing:
            raise ValueError(f"backtest receipt requires {', '.join(missing)}")

    @property
    def passed(self) -> bool:
        return self.status is ValidationStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "receipt_id": self.receipt_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "code_version": self.code_version,
            "decision_policy_version_id": self.decision_policy_version_id,
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "passed": self.passed,
            "metrics": dict(self.metrics),
            "baseline_metrics": dict(self.baseline_metrics),
            "thresholds": [
                threshold.to_dict() if hasattr(threshold, "to_dict") else threshold
                for threshold in self.thresholds
            ],
            "failed_rules": [
                failure.to_dict() if hasattr(failure, "to_dict") else failure
                for failure in self.failed_rules
            ],
            "horizon_metrics": {
                str(k): dict(v) for k, v in self.horizon_metrics.items()
            },
            "calibration_summary": dict(self.calibration_summary),
            "requested_by": self.requested_by,
            "created_at": self.created_at.isoformat(),
        }
        if self.audit_event_id is not None:
            data["audit_event_id"] = self.audit_event_id
        if self.report_artifact_uri is not None:
            data["report_artifact_uri"] = self.report_artifact_uri
        if self.report_sha256 is not None:
            data["report_sha256"] = self.report_sha256
        return data


def evaluate_backtest_run(
    *,
    model_name: str,
    model_version: str,
    dataset_snapshot_id: str,
    code_version: str,
    metrics: Mapping[str, float],
    baseline_metrics: Mapping[str, float] | None = None,
    thresholds: Sequence[MetricThreshold] = (),
    decision_policy: DecisionPolicy | None = None,
    horizon_metrics: Mapping[str | int, Mapping[str, float]] | None = None,
    calibration_summary: Mapping[str, Any] | None = None,
    requested_by: str = "system",
    receipt_id: str | None = None,
    report_artifact_uri: str | None = None,
    report_sha256: str | None = None,
) -> BacktestReceipt:
    effective = effective_thresholds(
        thresholds,
        decision_policy,
        model_name=model_name,
    )
    baseline = dict(baseline_metrics or {})
    failures: list[ValidationRuleFailure] = []
    worst_status = ValidationStatus.PASSED

    for threshold in effective:
        if threshold.metric_name not in metrics:
            failures.append(
                ValidationRuleFailure(
                    rule_name=f"backtest:{threshold.metric_name}",
                    severity=ValidationStatus.FAILED,
                    message=f"backtest metric {threshold.metric_name} missing from evaluation inputs",
                )
            )
            worst_status = ValidationStatus.FAILED
            continue
        baseline_val = float(baseline[threshold.metric_name]) if threshold.metric_name in baseline else None
        status, message = threshold.evaluate(
            float(metrics[threshold.metric_name]),
            baseline_value=baseline_val,
        )
        if status is ValidationStatus.PASSED:
            continue
        failures.append(
            ValidationRuleFailure(
                rule_name=f"backtest:{threshold.metric_name}",
                severity=status,
                message=message or f"backtest metric {threshold.metric_name} threshold breached",
            )
        )
        if status is ValidationStatus.FAILED:
            worst_status = ValidationStatus.FAILED
        elif worst_status is ValidationStatus.PASSED:
            worst_status = ValidationStatus.WARNING

    policy_version_id = decision_policy.policy_version_id if decision_policy else None
    if not policy_version_id:
        raise ValueError("backtest evaluation requires a governing DecisionPolicy")

    return BacktestReceipt(
        receipt_id=receipt_id or f"backtest-{uuid4()}",
        model_name=model_name,
        model_version=model_version,
        dataset_snapshot_id=dataset_snapshot_id,
        code_version=code_version,
        decision_policy_version_id=policy_version_id,
        status=worst_status,
        metrics=dict(metrics),
        baseline_metrics=baseline,
        thresholds=tuple(effective),
        failed_rules=tuple(failures),
        horizon_metrics=dict(horizon_metrics or {}),
        calibration_summary=dict(calibration_summary or {}),
        requested_by=requested_by,
        report_artifact_uri=report_artifact_uri,
        report_sha256=report_sha256,
    )


__all__ = [
    "BacktestReceipt",
    "calculate_mae",
    "calculate_mape",
    "calculate_rmse",
    "evaluate_backtest_run",
    "run_rolling_backtest",
]
