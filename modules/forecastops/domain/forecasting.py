from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

import numpy as np


class ForecastOpsError(ValueError):
    """Raised when a ForecastOps lifecycle transition is invalid."""


class ForecastOpsNotFoundError(ForecastOpsError):
    """Raised when an alert or handoff referenced by id does not exist."""


FORECASTOPS_MODEL_VERSION = "forecastops-baseline-v1"
FORECASTOPS_FEATURE_VERSION = "store-machine-timeseries-view-v1"
FOUR_LIGHT_POLICY_VERSION = "four-light-policy-v1"
FORECAST_HORIZON_WEEKS = (4, 8, 12, 24)

# Standard-normal quantile z_{0.90}; the P10/P90 band half-width is z * residual
# coefficient of variation, i.e. a proper 80% central prediction interval.
_P10_P90_Z = 1.2815515594457831
# The empirically derived spread is clamped to a sane band so a perfectly linear
# (residual-free) series still shows a non-zero interval and a very noisy series
# does not explode the band.
_MIN_PREDICTION_SPREAD = 0.05
_MAX_PREDICTION_SPREAD = 0.45
# Series too short to estimate volatility reliably fall back to a wide default.
_SMALL_SAMPLE_SPREAD = 0.28
_MIN_VOLATILITY_POINTS = 3


class AlertLevel(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


@dataclass(frozen=True)
class ForecastBand:
    p10: float
    p50: float
    p90: float

    def to_dict(self) -> dict[str, float]:
        return {"p10": self.p10, "p50": self.p50, "p90": self.p90}


@dataclass(frozen=True)
class StoreDayObservation:
    store_id: str
    business_date: date
    actual_revenue: float
    machine_cycles: int = 0
    site_score_baseline_p50: float | None = None
    active_intervention_ids: tuple[str, ...] = ()
    data_quality_score: float = 1.0
    source_snapshot_ids: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> StoreDayObservation:
        return cls(
            store_id=str(data["store_id"]),
            business_date=_parse_date(data.get("business_date") or data.get("date")),
            actual_revenue=float(_first_present(data, "actual_revenue", "revenue", default=0.0)),
            machine_cycles=int(_first_present(data, "machine_cycles", "cycles", default=0)),
            site_score_baseline_p50=_optional_float(
                _first_present(
                    data,
                    "site_score_baseline_p50",
                    "sitescore_baseline_p50",
                    "baseline_p50",
                    default=None,
                )
            ),
            active_intervention_ids=tuple(
                str(value) for value in data.get("active_intervention_ids", ())
            ),
            data_quality_score=_bounded(
                _first_present(data, "data_quality_score", "data_quality", default=1.0)
            ),
            source_snapshot_ids=tuple(str(value) for value in data.get("source_snapshot_ids", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "business_date": self.business_date.isoformat(),
            "actual_revenue": self.actual_revenue,
            "machine_cycles": self.machine_cycles,
            "site_score_baseline_p50": self.site_score_baseline_p50,
            "active_intervention_ids": list(self.active_intervention_ids),
            "data_quality_score": self.data_quality_score,
            "source_snapshot_ids": list(self.source_snapshot_ids),
        }


@dataclass(frozen=True)
class ForecastSeries:
    store_id: str
    observations: tuple[StoreDayObservation, ...]
    feature_version: str = FORECASTOPS_FEATURE_VERSION

    @property
    def latest(self) -> StoreDayObservation | None:
        if not self.observations:
            return None
        return self.observations[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "feature_version": self.feature_version,
            "points": [observation.to_dict() for observation in self.observations],
            "point_count": len(self.observations),
        }


@dataclass(frozen=True)
class ForecastInput:
    store_id: str
    observations: tuple[StoreDayObservation, ...]
    horizon_days: int = 28
    target_metric: str = "revenue"
    prediction_origin_time: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ForecastInput:
        store_id = str(data["store_id"])
        observations = tuple(
            _coerce_observation({**item, "store_id": item.get("store_id", store_id)})
            for item in data.get("observations", data.get("series", ()))
        )
        return cls(
            store_id=store_id,
            observations=tuple(sorted(observations, key=lambda item: item.business_date)),
            horizon_days=int(data.get("horizon_days", 28)),
            target_metric=str(data.get("target_metric") or "revenue"),
            prediction_origin_time=_parse_datetime(
                data.get("prediction_origin_time") or datetime.now(UTC)
            ),
        )


@dataclass(frozen=True)
class ForecastOutput:
    forecast_output_id: str
    store_id: str
    prediction_run_id: str
    horizon_days: int
    target_metric: str
    p10: float
    p50: float
    p90: float
    w4: ForecastBand
    w8: ForecastBand
    w12: ForecastBand
    w24: ForecastBand
    trajectory_class: str
    turning_point_probability: float
    sitescore_gap_ratio: float
    actual_revenue: float
    sitescore_baseline_p50: float | None
    model_version: str
    feature_version: str
    policy_version: str
    prediction_origin_time: datetime
    scored_at: datetime
    source_snapshot_ids: tuple[str, ...] = ()
    forecast_version: int = 1

    def with_version(self, *, forecast_version: int, forecast_output_id: str) -> ForecastOutput:
        return ForecastOutput(
            **{
                **self.__dict__,
                "forecast_version": forecast_version,
                "forecast_output_id": forecast_output_id,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        forecast_bands = {
            "w4": self.w4.to_dict(),
            "w8": self.w8.to_dict(),
            "w12": self.w12.to_dict(),
            "w24": self.w24.to_dict(),
        }
        return {
            "forecast_output_id": self.forecast_output_id,
            "forecast_version": self.forecast_version,
            "store_id": self.store_id,
            "prediction_run_id": self.prediction_run_id,
            "horizon_days": self.horizon_days,
            "target_metric": self.target_metric,
            "p10": self.p10,
            "p50": self.p50,
            "p90": self.p90,
            "w4": forecast_bands["w4"],
            "w8": forecast_bands["w8"],
            "w12": forecast_bands["w12"],
            "w24": forecast_bands["w24"],
            "forecast_bands": forecast_bands,
            "trajectory_class": self.trajectory_class,
            "turning_point_probability": self.turning_point_probability,
            "sitescore_gap_ratio": self.sitescore_gap_ratio,
            "actual_revenue": self.actual_revenue,
            "sitescore_baseline_p50": self.sitescore_baseline_p50,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "policy_version": self.policy_version,
            "prediction_origin_time": self.prediction_origin_time.isoformat(),
            "scored_at": self.scored_at.isoformat(),
            "source_snapshot_ids": list(self.source_snapshot_ids),
        }


@dataclass(frozen=True)
class Alert:
    alert_id: str
    store_id: str
    alert_level: AlertLevel
    alert_reason_code: str
    evidence_json: dict[str, Any]
    opened_at: datetime
    status: str = "open"
    closed_at: datetime | None = None
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    acknowledgement_note: str | None = None

    def acknowledge(
        self, *, actor: str, note: str | None = None, now: datetime
    ) -> Alert:
        """Return an acknowledged copy of this alert.

        Acknowledgement is a persisted human action: an alert can only be
        acknowledged once, and a closed alert can no longer be acknowledged.
        """

        if not actor or not actor.strip():
            raise ForecastOpsError("alert acknowledgement requires an actor")
        if self.status == "acknowledged":
            raise ForecastOpsError(f"alert {self.alert_id} is already acknowledged")
        if self.status == "closed":
            raise ForecastOpsError(f"alert {self.alert_id} is closed and cannot be acknowledged")
        return replace(
            self,
            status="acknowledged",
            acknowledged_by=actor,
            acknowledged_at=now,
            acknowledgement_note=note,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "store_id": self.store_id,
            "alert_level": self.alert_level.value,
            "alert_reason_code": self.alert_reason_code,
            "evidence_json": self.evidence_json,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "status": self.status,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "acknowledgement_note": self.acknowledgement_note,
        }


@dataclass(frozen=True)
class InterventionHandoff:
    handoff_id: str
    alert_id: str
    store_id: str
    intervention_type: str
    eligibility_status: str
    action_set_json: dict[str, Any]
    created_at: datetime
    status: str = "proposed"
    executed_by: str | None = None
    executed_at: datetime | None = None
    intervention_id: str | None = None

    def execute(
        self, *, actor: str, intervention_id: str | None = None, now: datetime
    ) -> InterventionHandoff:
        """Return a dispatched copy that links this handoff to an intervention.

        A handoff is *executable*: dispatching it records who acted, when, and
        the InterventionOps case it opened, and moves it out of ``proposed`` so
        it cannot be dispatched twice.
        """

        if not actor or not actor.strip():
            raise ForecastOpsError("handoff execution requires an actor")
        if self.status == "dispatched":
            raise ForecastOpsError(f"handoff {self.handoff_id} is already dispatched")
        return replace(
            self,
            status="dispatched",
            executed_by=actor,
            executed_at=now,
            intervention_id=intervention_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "alert_id": self.alert_id,
            "store_id": self.store_id,
            "intervention_type": self.intervention_type,
            "eligibility_status": self.eligibility_status,
            "action_set_json": self.action_set_json,
            "created_at": self.created_at.isoformat(),
            "status": self.status,
            "executed_by": self.executed_by,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "intervention_id": self.intervention_id,
        }


def build_store_timeseries(
    observations: Iterable[StoreDayObservation | Mapping[str, Any]],
) -> list[ForecastSeries]:
    grouped: dict[str, list[StoreDayObservation]] = defaultdict(list)
    for observation in observations:
        item = _coerce_observation(observation)
        grouped[item.store_id].append(item)
    return [
        ForecastSeries(
            store_id=store_id,
            observations=tuple(sorted(items, key=lambda item: item.business_date)),
        )
        for store_id, items in sorted(grouped.items())
    ]


def forecast_stores(
    inputs: Iterable[ForecastInput | Mapping[str, Any]],
    *,
    prediction_origin_time: datetime | None = None,
    scored_at: datetime | None = None,
    prediction_run_id: str | None = None,
) -> tuple[list[ForecastOutput], list[Alert], list[InterventionHandoff]]:
    scored_time = scored_at or datetime.now(UTC)
    run_id = prediction_run_id or f"forecast-run-{uuid4()}"
    outputs: list[ForecastOutput] = []
    alerts: list[Alert] = []
    handoffs: list[InterventionHandoff] = []
    for item in inputs:
        forecast_input = _coerce_forecast_input(item)
        output = _forecast_one(
            forecast_input,
            prediction_origin_time=prediction_origin_time or forecast_input.prediction_origin_time,
            scored_at=scored_time,
            prediction_run_id=run_id,
        )
        outputs.append(output)
        alert = _alert_for(output, opened_at=scored_time)
        alerts.append(alert)
        handoff = _handoff_for(alert, output, created_at=scored_time)
        if handoff is not None:
            handoffs.append(handoff)
    return outputs, alerts, handoffs


def _forecast_one(
    forecast_input: ForecastInput,
    *,
    prediction_origin_time: datetime,
    scored_at: datetime,
    prediction_run_id: str,
) -> ForecastOutput:
    observations = forecast_input.observations
    if not observations:
        actual = 0.0
        baseline = None
        source_snapshot_ids: tuple[str, ...] = ()
        p50 = 0.0
        trajectory_class = "plateau"
        turning_point_probability = 0.0
    else:
        latest = observations[-1]
        actual = latest.actual_revenue
        baseline = latest.site_score_baseline_p50
        source_snapshot_ids = tuple(
            snapshot_id
            for observation in observations
            for snapshot_id in observation.source_snapshot_ids
        )
        recent = observations[-7:]
        trailing_avg = sum(observation.actual_revenue for observation in recent) / len(recent)
        p50 = round(max(trailing_avg, 0.0), 2)
        first = observations[0].actual_revenue
        delta_ratio = (actual - first) / max(first, 1.0)
        trajectory_class = _trajectory_class(delta_ratio)
        turning_point_probability = round(_bounded(abs(delta_ratio) * 0.8), 4)

    spread = _prediction_spread(observations)
    bands = _forecast_bands(p50=p50, spread=spread, trajectory_class=trajectory_class)
    w4 = bands["w4"]
    gap_ratio = _sitescore_gap_ratio(actual=actual, baseline=baseline)
    return ForecastOutput(
        forecast_output_id=f"forecast-output-{uuid4()}",
        store_id=forecast_input.store_id,
        prediction_run_id=prediction_run_id,
        horizon_days=forecast_input.horizon_days,
        target_metric=forecast_input.target_metric,
        p10=w4.p10,
        p50=w4.p50,
        p90=w4.p90,
        w4=w4,
        w8=bands["w8"],
        w12=bands["w12"],
        w24=bands["w24"],
        trajectory_class=trajectory_class,
        turning_point_probability=turning_point_probability,
        sitescore_gap_ratio=gap_ratio,
        actual_revenue=actual,
        sitescore_baseline_p50=baseline,
        model_version=FORECASTOPS_MODEL_VERSION,
        feature_version=FORECASTOPS_FEATURE_VERSION,
        policy_version=FOUR_LIGHT_POLICY_VERSION,
        prediction_origin_time=prediction_origin_time,
        scored_at=scored_at,
        source_snapshot_ids=source_snapshot_ids,
    )


def _alert_for(output: ForecastOutput, *, opened_at: datetime) -> Alert:
    gap = output.sitescore_gap_ratio
    if gap <= -0.35:
        level = AlertLevel.RED
    elif gap <= -0.20:
        level = AlertLevel.ORANGE
    elif gap <= -0.10:
        level = AlertLevel.YELLOW
    else:
        level = AlertLevel.GREEN
    reason = "sitescore_gap" if level is not AlertLevel.GREEN else "within_expected_band"
    return Alert(
        alert_id=f"forecast-alert-{uuid4()}",
        store_id=output.store_id,
        alert_level=level,
        alert_reason_code=reason,
        evidence_json={
            "actual_revenue": output.actual_revenue,
            "forecast_p50": output.p50,
            "sitescore_baseline_p50": output.sitescore_baseline_p50,
            "sitescore_gap_ratio": output.sitescore_gap_ratio,
            "trajectory_class": output.trajectory_class,
            "policy_version": output.policy_version,
        },
        opened_at=opened_at,
    )


def _handoff_for(
    alert: Alert,
    output: ForecastOutput,
    *,
    created_at: datetime,
) -> InterventionHandoff | None:
    if alert.alert_level not in {AlertLevel.ORANGE, AlertLevel.RED}:
        return None
    intervention_type = "maintenance" if alert.alert_level is AlertLevel.RED else "promotion"
    return InterventionHandoff(
        handoff_id=f"intervention-handoff-{uuid4()}",
        alert_id=alert.alert_id,
        store_id=alert.store_id,
        intervention_type=intervention_type,
        eligibility_status="manual_review" if alert.alert_level is AlertLevel.RED else "eligible",
        action_set_json={
            "trigger_alert_level": alert.alert_level.value,
            "trigger_reason_code": alert.alert_reason_code,
            "recommended_actions": _recommended_actions(alert.alert_level, output),
            "evidence": alert.evidence_json,
        },
        created_at=created_at,
    )


def _recommended_actions(level: AlertLevel, output: ForecastOutput) -> list[str]:
    if level is AlertLevel.RED:
        return ["inspect_machine_uptime", "review_staffing", "open_recovery_plan"]
    if output.trajectory_class == "declining":
        return ["launch_local_promotion", "review_price_packaging"]
    return ["review_local_demand", "create_intervention_candidate"]


def _trajectory_class(delta_ratio: float) -> str:
    if delta_ratio >= 0.20:
        return "growing"
    if delta_ratio >= 0.05:
        return "ramping"
    if delta_ratio <= -0.10:
        return "declining"
    return "plateau"


def _prediction_spread(observations: tuple[StoreDayObservation, ...]) -> float:
    """Relative half-width of the P10/P90 revenue prediction band.

    Rather than a fixed fraction, the band width reflects how noisy the store's
    own revenue series is. A linear trend is fitted with ``numpy.polyfit`` and
    the standard deviation of the residuals around it (the variation the trend
    does not explain) drives the interval: ``spread = z_{0.90} * residual_cv``,
    a proper 80% central prediction interval, clamped to a sane range. Short
    series fall back to a wide default because their volatility estimate is
    unreliable.
    """
    revenues = [observation.actual_revenue for observation in observations]
    if len(revenues) < _MIN_VOLATILITY_POINTS:
        return _SMALL_SAMPLE_SPREAD
    values = np.asarray(revenues, dtype=float)
    level = float(values.mean())
    if level <= 0:
        return _SMALL_SAMPLE_SPREAD
    index = np.arange(values.size, dtype=float)
    slope, intercept = np.polyfit(index, values, 1)
    residuals = values - (slope * index + intercept)
    residual_std = float(np.sqrt(np.mean(residuals**2)))
    spread = _P10_P90_Z * (residual_std / level)
    return float(min(max(spread, _MIN_PREDICTION_SPREAD), _MAX_PREDICTION_SPREAD))


def _forecast_bands(
    *,
    p50: float,
    spread: float,
    trajectory_class: str,
) -> dict[str, ForecastBand]:
    trajectory_growth = {
        "growing": 0.08,
        "ramping": 0.04,
        "plateau": 0.0,
        "declining": -0.06,
    }[trajectory_class]
    bands: dict[str, ForecastBand] = {}
    for weeks in FORECAST_HORIZON_WEEKS:
        multiplier = max(0.0, 1.0 + trajectory_growth * ((weeks - 4) / 4))
        horizon_p50 = round(p50 * multiplier, 2)
        horizon_spread = spread + (weeks / 24) * 0.08
        bands[f"w{weeks}"] = ForecastBand(
            p10=round(horizon_p50 * (1.0 - horizon_spread), 2),
            p50=horizon_p50,
            p90=round(horizon_p50 * (1.0 + horizon_spread), 2),
        )
    return bands


def _sitescore_gap_ratio(*, actual: float, baseline: float | None) -> float:
    if baseline is None or baseline <= 0:
        return 0.0
    return round((actual - baseline) / baseline, 4)


def _coerce_forecast_input(item: ForecastInput | Mapping[str, Any]) -> ForecastInput:
    if isinstance(item, ForecastInput):
        return item
    return ForecastInput.from_mapping(item)


def _coerce_observation(item: StoreDayObservation | Mapping[str, Any]) -> StoreDayObservation:
    if isinstance(item, StoreDayObservation):
        return item
    return StoreDayObservation.from_mapping(item)


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _parse_date(value: date | datetime | str | None) -> date:
    if value is None:
        return datetime.now(UTC).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _first_present(data: Mapping[str, Any], *keys: str, default: Any) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _bounded(value: Any, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))
