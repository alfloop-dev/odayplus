from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

FORECASTOPS_MODEL_VERSION = "forecastops-baseline-v1"
FORECASTOPS_FEATURE_VERSION = "store-machine-timeseries-view-v1"
FOUR_LIGHT_POLICY_VERSION = "four-light-policy-v1"


class AlertLevel(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


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
) -> tuple[list[ForecastOutput], list[Alert], list[InterventionHandoff]]:
    scored_time = scored_at or datetime.now(UTC)
    outputs: list[ForecastOutput] = []
    alerts: list[Alert] = []
    handoffs: list[InterventionHandoff] = []
    for item in inputs:
        forecast_input = _coerce_forecast_input(item)
        output = _forecast_one(
            forecast_input,
            prediction_origin_time=prediction_origin_time or forecast_input.prediction_origin_time,
            scored_at=scored_time,
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

    spread = 0.18 if len(observations) >= 7 else 0.28
    p10 = round(p50 * (1.0 - spread), 2)
    p90 = round(p50 * (1.0 + spread), 2)
    gap_ratio = _sitescore_gap_ratio(actual=actual, baseline=baseline)
    return ForecastOutput(
        forecast_output_id=f"forecast-output-{uuid4()}",
        store_id=forecast_input.store_id,
        prediction_run_id=f"forecast-run-{uuid4()}",
        horizon_days=forecast_input.horizon_days,
        target_metric=forecast_input.target_metric,
        p10=p10,
        p50=p50,
        p90=p90,
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
