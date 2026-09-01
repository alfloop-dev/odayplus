from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

from modules.forecastops.domain.forecasting import (
    FORECAST_ALERT_POLICY_KIND,
    Alert,
    AlertDisposition,
    AlertLevel,
    ForecastAlertPolicyError,
    ForecastOpsError,
    ForecastOutput,
    StoreDayObservation,
    _parse_date,
    _parse_datetime,
    _policy_thresholds,
    _sitescore_gap_ratio,
    _utc_datetime,
)
from shared.governance import DecisionPolicyRepository, resolve_policy


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

    def __post_init__(self) -> None:
        """Enforce the per-day correction invariant for every construction path."""
        if (
            self.feedback_type is FeedbackType.OUTCOME_CORRECTION
            and self.target_date_start != self.target_date_end
        ):
            raise ForecastOpsError(
                "OUTCOME_CORRECTION must target exactly one date; "
                "a corrected revenue value cannot be applied to a date range"
            )

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

        if f_type is FeedbackType.OUTCOME_CORRECTION and parsed_start != parsed_end:
            raise ForecastOpsError(
                "OUTCOME_CORRECTION must target exactly one date; "
                "a corrected revenue value cannot be applied to a date range"
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
    # Materialize one-shot inputs once.  Filtering then operates on the stable
    # list, and the same list supplies the unfiltered count.
    materialized_observations = list(actual_observations)
    clean_observations = filter_training_observations(materialized_observations, feedbacks)
    all_obs_count = len(materialized_observations)

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


def calculate_alert_precision_metrics(
    alerts: Iterable[Alert | Mapping[str, Any]],
) -> dict[str, Any]:
    """Calculate precision and advance warning lead time metrics across alerts (ODP-FR-FCT-006).

    Precision = TRUE_POSITIVE / (TRUE_POSITIVE + FALSE_POSITIVE)
    The denominator strictly excludes KNOWN_CONTEXT and UNRESOLVED alerts.
    Lead time (days) = deterioration_confirmed_at - opened_at, only valid for TRUE_POSITIVE alerts.
    """
    tp_count = 0
    fp_count = 0
    kc_count = 0
    un_count = 0
    lead_times: list[int] = []
    total_count = 0

    for item in alerts:
        raw_level = item.alert_level if isinstance(item, Alert) else item.get("alert_level")
        if (
            isinstance(raw_level, AlertLevel) and raw_level is AlertLevel.GREEN
        ) or str(raw_level or "").strip().lower() == AlertLevel.GREEN.value:
            # GREEN is a normal forecast outcome, not an alert eligible for
            # precision evaluation.  Counting it as UNRESOLVED would pollute
            # both the alert inventory and the maturity denominator.
            continue
        total_count += 1
        disp_str: str | None = None
        lead_time_val: int | None = None

        if isinstance(item, Alert):
            lead_time_val = item.lead_time_days
            if item.disposition is not None:
                disp_str = (
                    item.disposition.value
                    if isinstance(item.disposition, AlertDisposition)
                    else str(item.disposition).strip().upper()
                )
        elif isinstance(item, Mapping):
            raw_disp = item.get("disposition")
            if raw_disp is not None:
                disp_str = str(raw_disp).strip().upper()
            raw_lead = item.get("lead_time_days")
            if raw_lead is not None:
                try:
                    lead_time_val = int(raw_lead)
                except (ValueError, TypeError):
                    pass
            elif disp_str == AlertDisposition.TRUE_POSITIVE.value:
                det_raw = item.get("deterioration_confirmed_at")
                open_raw = item.get("opened_at")
                if det_raw and open_raw:
                    try:
                        d_date = _parse_datetime(det_raw).date()
                        o_date = _parse_datetime(open_raw).date()
                        lead_time_val = (d_date - o_date).days
                    except Exception:
                        pass

        if disp_str == AlertDisposition.TRUE_POSITIVE.value:
            tp_count += 1
            if lead_time_val is not None:
                lead_times.append(lead_time_val)
        elif disp_str == AlertDisposition.FALSE_POSITIVE.value:
            fp_count += 1
        elif disp_str == AlertDisposition.KNOWN_CONTEXT.value:
            kc_count += 1
        else:
            un_count += 1

    evaluated_count = tp_count + fp_count
    precision = round(tp_count / evaluated_count, 4) if evaluated_count > 0 else None
    mean_lead_time = round(sum(lead_times) / len(lead_times), 2) if lead_times else None
    min_lead_time = min(lead_times) if lead_times else None
    max_lead_time = max(lead_times) if lead_times else None

    return {
        "total_alerts": total_count,
        "true_positive_count": tp_count,
        "false_positive_count": fp_count,
        "known_context_count": kc_count,
        "unresolved_count": un_count,
        "evaluated_alert_count": evaluated_count,
        "precision": precision,
        "mean_lead_time_days": mean_lead_time,
        "min_lead_time_days": min_lead_time,
        "max_lead_time_days": max_lead_time,
        "lead_time_sample_count": len(lead_times),
    }


def backfill_alert_precision(
    alerts: Iterable[Alert | Mapping[str, Any]],
    *,
    observations: Iterable[StoreDayObservation | Mapping[str, Any]],
    feedbacks: Iterable[ForecastFeedback | Mapping[str, Any]] = (),
    policy_repository: DecisionPolicyRepository | None = None,
    evaluation_horizon_days: int = 28,
    min_observations: int | None = None,
    as_of: datetime | None = None,
    actor: str = "precision_backfill_job",
    now: datetime | None = None,
) -> tuple[list[Alert], dict[str, Any]]:
    """Backfill deterioration_confirmed_at and disposition for alerts per policy thresholds (ODP-FR-FCT-006).

    - deterioration_confirmed_at: The earliest date at or after opened_at where actual
      performance breached the policy threshold for that alert level within the evaluation horizon.
    - disposition:
      - TRUE_POSITIVE: deterioration breached threshold within evaluation window.
      - FALSE_POSITIVE: evaluation window elapsed with sufficient observations and no deterioration.
      - KNOWN_CONTEXT: alert period overlaps with active CONTEXT_ANNOTATION (e.g. remodeling).
      - UNRESOLVED: observation window not yet complete or insufficient observations.
    """
    if policy_repository is None:
        raise ForecastAlertPolicyError(
            "forecast alert policy_repository is required; refusing to evaluate alert precision"
        )
    if evaluation_horizon_days <= 0:
        raise ForecastOpsError("evaluation_horizon_days must be greater than zero")
    if min_observations is not None and min_observations <= 0:
        raise ForecastOpsError("min_observations must be greater than zero")

    eval_now = now or datetime.now(UTC)
    min_obs_required = (
        max(1, min_observations)
        if min_observations is not None
        else (min(evaluation_horizon_days, 14) if evaluation_horizon_days > 0 else 1)
    )

    # 1. Parse and group context annotations by store
    context_annotations: list[tuple[str, str, date, date]] = []
    for fb in feedbacks:
        fb_obj = fb if isinstance(fb, ForecastFeedback) else ForecastFeedback.from_mapping(fb)
        if (
            fb_obj.feedback_type is FeedbackType.CONTEXT_ANNOTATION
            and fb_obj.status in {FeedbackStatus.ACCEPTED, FeedbackStatus.APPROVED}
        ):
            context_annotations.append(
                (
                    fb_obj.tenant_id,
                    fb_obj.store_id,
                    fb_obj.target_date_start,
                    fb_obj.target_date_end,
                )
            )

    # 2. Parse and group observations by store
    obs_by_store: dict[str, list[StoreDayObservation]] = defaultdict(list)
    for obs in observations:
        obs_obj = (
            obs
            if isinstance(obs, StoreDayObservation)
            else StoreDayObservation.from_mapping(obs)
        )
        obs_by_store[obs_obj.store_id].append(obs_obj)
    for s_id in obs_by_store:
        obs_by_store[s_id].sort(key=lambda o: o.business_date)

    updated_alerts: list[Alert] = []

    for item in alerts:
        alert = item if isinstance(item, Alert) else Alert.from_mapping(item)
        if alert.alert_level is AlertLevel.GREEN or alert.alert_level == "green":
            updated_alerts.append(alert)
            continue

        # A batch backfill may be rerun, but it must never supersede an
        # operator's adjudication (including the existing feedback path's
        # free-form dispositions).  UNRESOLVED is the only prior result that
        # remains eligible for re-evaluation as more observations arrive.
        existing_disposition = (
            alert.disposition.value
            if isinstance(alert.disposition, AlertDisposition)
            else str(alert.disposition or "").strip().upper()
        )
        if existing_disposition and existing_disposition != AlertDisposition.UNRESOLVED.value:
            updated_alerts.append(alert)
            continue

        prior_confirmation = alert.deterioration_confirmed_at

        open_dt = _utc_datetime(alert.opened_at)
        open_d = open_dt.date()
        horizon_end_d = open_d + timedelta(days=evaluation_horizon_days)

        # Check if overlapping with a CONTEXT_ANNOTATION
        has_known_context = any(
            tenant_id == alert.tenant_id
            and store_id == alert.store_id
            and start_d <= horizon_end_d
            and end_d >= open_d
            for tenant_id, store_id, start_d, end_d in context_annotations
        )

        if has_known_context:
            updated = alert.with_evaluation(
                disposition=AlertDisposition.KNOWN_CONTEXT,
                deterioration_confirmed_at=prior_confirmation,
                actor=actor,
                now=eval_now,
            )
            updated_alerts.append(updated)
            continue

        # Resolve policy threshold (fail-closed per ODP-SD-AMD-001 §3.3)
        policy = resolve_policy(
            policy_repository,
            policy_kind=FORECAST_ALERT_POLICY_KIND,
            tenant_id=alert.tenant_id,
            at=open_dt,
        )
        thresholds = _policy_thresholds(policy)

        lvl = (
            alert.alert_level
            if isinstance(alert.alert_level, AlertLevel)
            else AlertLevel(str(alert.alert_level).lower())
        )
        threshold_value = thresholds.get(lvl, thresholds[AlertLevel.YELLOW])

        # Observations strictly within the evaluation horizon [open_d, horizon_end_d]
        store_obs_in_window = [
            o for o in obs_by_store.get(alert.store_id, [])
            if open_d <= o.business_date <= horizon_end_d
        ]

        # Valid observations in window: must have a positive baseline to evaluate sitescore gap
        valid_obs_in_window = [
            o for o in store_obs_in_window
            if o.site_score_baseline_p50 is not None and o.site_score_baseline_p50 > 0
        ]

        deterioration_found = False
        deterioration_date: date | None = None

        for o in valid_obs_in_window:
            gap = _sitescore_gap_ratio(actual=o.actual_revenue, baseline=o.site_score_baseline_p50)
            if gap <= threshold_value:
                deterioration_found = True
                deterioration_date = o.business_date
                break

        if deterioration_found and deterioration_date is not None:
            det_dt = datetime(
                deterioration_date.year,
                deterioration_date.month,
                deterioration_date.day,
                open_dt.hour,
                open_dt.minute,
                open_dt.second,
                tzinfo=UTC,
            )
            if prior_confirmation is not None:
                prior_dt = _utc_datetime(prior_confirmation)
                if prior_dt <= det_dt:
                    det_dt = prior_confirmation
            updated = alert.with_evaluation(
                disposition=AlertDisposition.TRUE_POSITIVE,
                deterioration_confirmed_at=det_dt,
                actor=actor,
                now=eval_now,
            )
        else:
            all_store_obs = obs_by_store.get(alert.store_id, [])
            max_obs_d = max((o.business_date for o in all_store_obs), default=open_d)
            curr_eval_d = as_of.date() if as_of else max_obs_d
            window_elapsed = max_obs_d >= horizon_end_d or curr_eval_d >= horizon_end_d
            has_sufficient_obs = len(valid_obs_in_window) >= min_obs_required

            if window_elapsed and has_sufficient_obs:
                updated = alert.with_evaluation(
                    disposition=AlertDisposition.FALSE_POSITIVE,
                    deterioration_confirmed_at=prior_confirmation,
                    actor=actor,
                    now=eval_now,
                )
            else:
                updated = alert.with_evaluation(
                    disposition=AlertDisposition.UNRESOLVED,
                    deterioration_confirmed_at=prior_confirmation,
                    actor=actor,
                    now=eval_now,
                )

        updated_alerts.append(updated)

    metrics = calculate_alert_precision_metrics(updated_alerts)
    return updated_alerts, metrics


__all__ = [
    "FORBIDDEN_FEEDBACK_OVERRIDE_KEYS",
    "FeedbackStatus",
    "FeedbackType",
    "ForecastFeedback",
    "backfill_alert_precision",
    "calculate_alert_precision_metrics",
    "calculate_forecast_precision",
    "filter_training_observations",
    "validate_feedback_payload",
]
