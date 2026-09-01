from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from modules.forecastops.domain.forecasting import (
    ForecastOpsError,
    ForecastOutput,
    StoreDayObservation,
    _parse_date,
    _parse_datetime,
)


class FeedbackType(StrEnum):
    """The three canonical feedback paths for ForecastOps (ODP-FR-FCT-008)."""

    CONTEXT_ANNOTATION = "context_annotation"
    OUTCOME_CORRECTION = "outcome_correction"
    ALERT_DISPOSITION = "alert_disposition"

    @classmethod
    def from_str(cls, value: str | FeedbackType) -> FeedbackType:
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value == normalized or member.name.lower() == normalized:
                return member
        valid = ", ".join(m.name for m in cls)
        raise ForecastOpsError(f"Invalid feedback_type '{value}'. Must be one of: {valid}")


class FeedbackStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    ACCEPTED = "accepted"
    APPROVED = "approved"
    REJECTED = "rejected"

    @classmethod
    def from_str(cls, value: str | FeedbackStatus) -> FeedbackStatus:
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value == normalized or member.name.lower() == normalized:
                return member
        valid = ", ".join(m.name for m in cls)
        raise ForecastOpsError(f"Invalid feedback status '{value}'. Must be one of: {valid}")


# Governance constraint: ODP-BR-GOV-001
# Feedback must NOT directly overwrite forecast values or decision fields.
FORBIDDEN_FEEDBACK_OVERRIDE_KEYS = frozenset(
    {
        "p10",
        "p50",
        "p90",
        "w4",
        "w8",
        "w12",
        "w24",
        "forecast_bands",
        "trajectory_class",
        "turning_point_probability",
        "sitescore_gap_ratio",
        "decision",
        "decision_field",
        "alert_level",
    }
)


def validate_feedback_payload(payload: Mapping[str, Any]) -> None:
    """Ensure feedback payload does not violate ODP-BR-GOV-001."""
    violating = FORBIDDEN_FEEDBACK_OVERRIDE_KEYS.intersection(payload.keys())
    if violating:
        sorted_keys = ", ".join(sorted(violating))
        raise ForecastOpsError(
            f"ODP-BR-GOV-001 violation: feedback cannot directly overwrite forecast or decision fields: {sorted_keys}"
        )


@dataclass(frozen=True)
class ForecastFeedback:
    """A feedback record modifying or annotating ForecastOps operations."""

    feedback_id: str
    tenant_id: str
    store_id: str
    feedback_type: FeedbackType
    target_date_start: date
    target_date_end: date
    reason: str
    status: FeedbackStatus
    created_at: datetime
    created_by: str
    corrected_revenue: float | None = None
    alert_id: str | None = None
    disposition: str | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    rejection_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        store_id: str,
        feedback_type: FeedbackType | str,
        reason: str,
        created_by: str,
        target_date_start: date | str | None = None,
        target_date_end: date | str | None = None,
        target_date: date | str | None = None,
        corrected_revenue: float | None = None,
        alert_id: str | None = None,
        disposition: str | None = None,
        now: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ForecastFeedback:
        f_type = FeedbackType.from_str(feedback_type)
        created_time = now or datetime.now(UTC)

        start_d = target_date_start or target_date
        end_d = target_date_end or target_date or start_d
        if start_d is None:
            parsed_start = created_time.date()
            parsed_end = parsed_start
        else:
            parsed_start = _parse_date(start_d)
            parsed_end = _parse_date(end_d) if end_d is not None else parsed_start

        if parsed_start > parsed_end:
            raise ForecastOpsError(
                f"target_date_start ({parsed_start}) cannot be after target_date_end ({parsed_end})"
            )

        if not str(tenant_id or "").strip():
            raise ForecastOpsError("tenant_id is required for feedback")
        if not str(store_id or "").strip():
            raise ForecastOpsError("store_id is required for feedback")
        if not str(created_by or "").strip():
            raise ForecastOpsError("created_by actor is required for feedback")
        if not str(reason or "").strip():
            raise ForecastOpsError("reason is required for feedback")

        feedback_id = f"feedback-{uuid4()}"

        if f_type is FeedbackType.CONTEXT_ANNOTATION:
            # Auto-accepted: annotations do not change values directly
            status = FeedbackStatus.ACCEPTED
        elif f_type is FeedbackType.OUTCOME_CORRECTION:
            # Requires Data Owner approval
            if corrected_revenue is None:
                raise ForecastOpsError("corrected_revenue is required for OUTCOME_CORRECTION")
            if corrected_revenue < 0:
                raise ForecastOpsError("corrected_revenue cannot be negative")
            status = FeedbackStatus.PENDING_APPROVAL
        elif f_type is FeedbackType.ALERT_DISPOSITION:
            # Auto-accepted: writes alert disposition and closes alert
            if not str(alert_id or "").strip():
                raise ForecastOpsError("alert_id is required for ALERT_DISPOSITION")
            if not str(disposition or "").strip():
                raise ForecastOpsError("disposition is required for ALERT_DISPOSITION")
            status = FeedbackStatus.ACCEPTED

        return cls(
            feedback_id=feedback_id,
            tenant_id=str(tenant_id).strip(),
            store_id=str(store_id).strip(),
            feedback_type=f_type,
            target_date_start=parsed_start,
            target_date_end=parsed_end,
            reason=str(reason).strip(),
            status=status,
            created_at=created_time,
            created_by=str(created_by).strip(),
            corrected_revenue=float(corrected_revenue) if corrected_revenue is not None else None,
            alert_id=str(alert_id).strip() if alert_id else None,
            disposition=str(disposition).strip() if disposition else None,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ForecastFeedback:
        f_type = FeedbackType.from_str(data["feedback_type"])
        status = FeedbackStatus.from_str(data["status"])
        return cls(
            feedback_id=str(data["feedback_id"]),
            tenant_id=str(data["tenant_id"]),
            store_id=str(data["store_id"]),
            feedback_type=f_type,
            target_date_start=_parse_date(data["target_date_start"]),
            target_date_end=_parse_date(data["target_date_end"]),
            reason=str(data["reason"]),
            status=status,
            created_at=_parse_datetime(data["created_at"]),
            created_by=str(data["created_by"]),
            corrected_revenue=(
                float(data["corrected_revenue"]) if data.get("corrected_revenue") is not None else None
            ),
            alert_id=str(data["alert_id"]) if data.get("alert_id") else None,
            disposition=str(data["disposition"]) if data.get("disposition") else None,
            approved_at=(
                _parse_datetime(data["approved_at"]) if data.get("approved_at") else None
            ),
            approved_by=str(data["approved_by"]) if data.get("approved_by") else None,
            rejection_reason=(
                str(data["rejection_reason"]) if data.get("rejection_reason") else None
            ),
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_id": self.feedback_id,
            "tenant_id": self.tenant_id,
            "store_id": self.store_id,
            "feedback_type": self.feedback_type.value,
            "target_date_start": self.target_date_start.isoformat(),
            "target_date_end": self.target_date_end.isoformat(),
            "reason": self.reason,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "corrected_revenue": self.corrected_revenue,
            "alert_id": self.alert_id,
            "disposition": self.disposition,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "approved_by": self.approved_by,
            "rejection_reason": self.rejection_reason,
            "metadata": dict(self.metadata),
        }


def filter_training_observations(
    observations: Iterable[StoreDayObservation | Mapping[str, Any]],
    feedbacks: Iterable[ForecastFeedback | Mapping[str, Any]],
) -> list[StoreDayObservation]:
    """Filter observations, excluding dates falling within accepted CONTEXT_ANNOTATION intervals."""
    parsed_feedbacks: list[ForecastFeedback] = []
    for f in feedbacks:
        if isinstance(f, ForecastFeedback):
            parsed_feedbacks.append(f)
        elif isinstance(f, Mapping):
            parsed_feedbacks.append(ForecastFeedback.from_mapping(f))

    exclusion_ranges = [
        (fb.store_id, fb.target_date_start, fb.target_date_end)
        for fb in parsed_feedbacks
        if fb.feedback_type is FeedbackType.CONTEXT_ANNOTATION
        and fb.status in {FeedbackStatus.ACCEPTED, FeedbackStatus.APPROVED}
    ]

    filtered: list[StoreDayObservation] = []
    for obs in observations:
        item = (
            obs
            if isinstance(obs, StoreDayObservation)
            else StoreDayObservation.from_mapping(obs)
        )
        excluded = any(
            store_id == item.store_id and start_d <= item.business_date <= end_d
            for store_id, start_d, end_d in exclusion_ranges
        )
        if not excluded:
            filtered.append(item)
    return filtered


def calculate_forecast_precision(
    forecast: ForecastOutput,
    actual_observations: Iterable[StoreDayObservation | Mapping[str, Any]],
    feedbacks: Iterable[ForecastFeedback | Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Calculate forecast precision metrics, excluding periods marked by CONTEXT_ANNOTATION."""
    clean_observations = filter_training_observations(actual_observations, feedbacks)
    all_obs_count = len(list(actual_observations))

    if not clean_observations:
        return {
            "forecast_output_id": forecast.forecast_output_id,
            "store_id": forecast.store_id,
            "observation_count": 0,
            "mean_actual_revenue": 0.0,
            "forecast_p50": forecast.p50,
            "forecast_p10": forecast.p10,
            "forecast_p90": forecast.p90,
            "mean_absolute_error": 0.0,
            "mean_absolute_percentage_error": 0.0,
            "is_within_p10_p90": True,
            "excluded_observation_count": all_obs_count,
        }

    actual_revenues = [obs.actual_revenue for obs in clean_observations]
    avg_actual = sum(actual_revenues) / len(actual_revenues)
    mae = abs(forecast.p50 - avg_actual)
    mape = mae / max(avg_actual, 1.0)
    is_within_band = forecast.p10 <= avg_actual <= forecast.p90

    return {
        "forecast_output_id": forecast.forecast_output_id,
        "store_id": forecast.store_id,
        "observation_count": len(clean_observations),
        "mean_actual_revenue": round(avg_actual, 2),
        "forecast_p50": forecast.p50,
        "forecast_p10": forecast.p10,
        "forecast_p90": forecast.p90,
        "mean_absolute_error": round(mae, 2),
        "mean_absolute_percentage_error": round(mape, 4),
        "is_within_p10_p90": is_within_band,
        "excluded_observation_count": all_obs_count - len(clean_observations),
    }


__all__ = [
    "FORBIDDEN_FEEDBACK_OVERRIDE_KEYS",
    "FeedbackStatus",
    "FeedbackType",
    "ForecastFeedback",
    "calculate_forecast_precision",
    "filter_training_observations",
    "validate_feedback_payload",
]
