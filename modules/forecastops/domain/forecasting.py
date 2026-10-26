from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

import numpy as np

from modules.forecastops.model_contract import FORECASTOPS_HORIZON_WEEKS
from shared.governance import (
    DecisionPolicy,
    DecisionPolicyRepository,
    resolve_policy,
)


class ForecastOpsError(ValueError):
    """Raised when a ForecastOps lifecycle transition is invalid."""


class ForecastOpsNotFoundError(ForecastOpsError):
    """Raised when an alert or handoff referenced by id does not exist."""


class ForecastEngineError(ForecastOpsError):
    """Raised when a selected forecast engine cannot produce a forecast."""


class ForecastEngineUnavailableError(ForecastEngineError):
    """Raised when an explicitly selected OSS engine is unavailable."""


class ForecastEngineInputError(ForecastEngineError):
    """Raised when an OSS engine cannot safely fit the supplied history."""


FORECASTOPS_MODEL_VERSION = "forecastops-baseline-v1"
FORECASTOPS_FEATURE_VERSION = "store-machine-timeseries-view-v1"
FOUR_LIGHT_POLICY_VERSION = "four-light-policy-v1"
FORECAST_ALERT_POLICY_ID = "four-light-policy"
FORECAST_ALERT_POLICY_KIND = "forecast_alert"
FORECAST_ALERT_POLICY_SEMVER = "1.0.0"
FORECAST_HORIZON_WEEKS = FORECASTOPS_HORIZON_WEEKS

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


class AlertDisposition(StrEnum):
    """The canonical alert evaluation dispositions (ODP-FR-FCT-006)."""

    TRUE_POSITIVE = "TRUE_POSITIVE"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    KNOWN_CONTEXT = "KNOWN_CONTEXT"
    UNRESOLVED = "UNRESOLVED"

    @classmethod
    def from_str(cls, value: str | AlertDisposition) -> AlertDisposition:
        if isinstance(value, cls):
            return value
        # Accept the human-facing spelling used by the API/UI while persisting
        # the enum's canonical wire value (for example, ``Known Context`` ->
        # ``KNOWN_CONTEXT``).  Keeping one representation is important because
        # precision backfill uses the disposition as its idempotency boundary.
        normalized = "_".join(str(value).strip().upper().replace("-", " ").split())
        for member in cls:
            if member.value == normalized or member.name.upper() == normalized:
                return member
        valid = ", ".join(m.name for m in cls)
        raise ForecastOpsError(f"Invalid alert disposition '{value}'. Must be one of: {valid}")


class ForecastAlertPolicyError(ForecastOpsError):
    """A forecast alert policy is missing or cannot be evaluated safely."""


@dataclass(frozen=True)
class ForecastAlertPolicyEvaluation:
    """The immutable result of evaluating one alert policy against one output."""

    alert_level: AlertLevel
    alert_reason_code: str
    quality_guard: dict[str, Any] = field(default_factory=dict)
    threshold_alert_level: AlertLevel = AlertLevel.GREEN


def default_forecast_alert_policy(tenant_id: str) -> DecisionPolicy:
    """Build the explicit v1 policy record used by local/test runtimes.

    The thresholds are intentionally data in a ``DecisionPolicy`` record, not
    branches in the alert producer. Production callers should inject the
    registry-backed repository; this factory only supplies the same seeded v1
    row that the policy migration creates for a tenant.
    """

    normalized_tenant_id = str(tenant_id or "").strip()
    if not normalized_tenant_id:
        raise ForecastOpsError("tenant_id is required for the forecast alert policy")
    label = FOUR_LIGHT_POLICY_VERSION
    return DecisionPolicy(
        policy_version_id=f"{label}:{normalized_tenant_id}",
        policy_label=label,
        policy_id=FORECAST_ALERT_POLICY_ID,
        policy_version=FORECAST_ALERT_POLICY_SEMVER,
        policy_kind=FORECAST_ALERT_POLICY_KIND,
        tenant_id=normalized_tenant_id,
        effective_from=datetime(1970, 1, 1, tzinfo=UTC),
        parameters={
            "thresholds": [
                {
                    "level": "RED",
                    "input": "sitescore_gap_ratio",
                    "op": "<=",
                    "value": -0.35,
                },
                {
                    "level": "ORANGE",
                    "input": "sitescore_gap_ratio",
                    "op": "<=",
                    "value": -0.20,
                },
                {
                    "level": "YELLOW",
                    "input": "sitescore_gap_ratio",
                    "op": "<=",
                    "value": -0.10,
                },
            ],
            "data_quality_guard": {
                "max_staleness_days": 2,
                "on_violation": "SUPPRESS_HIGH_CONFIDENCE",
            },
        },
        declared_inputs=("sitescore_gap_ratio",),
        change_reason="mechanism導入，門檻沿用既有四燈常數並納入資料品質守衛",
        rollback_policy_version=None,
        approved_by="architecture_owner",
        owner_role="ops",
    )


@dataclass(frozen=True)
class ForecastBand:
    p10: float
    p50: float
    p90: float

    def to_dict(self) -> dict[str, float]:
        return {"p10": self.p10, "p50": self.p50, "p90": self.p90}


@dataclass(frozen=True)
class ForecastEngineResult:
    """Engine-owned forecasts and immutable execution metadata."""

    bands: dict[int, ForecastBand]
    engine_name: str
    model_name: str
    model_version: str
    metadata: dict[str, Any]


class ForecastEngine(Protocol):
    """Contract implemented by optional ForecastOps engines."""

    engine_name: str
    model_name: str

    def fit_predict(self, forecast_input: ForecastInput) -> ForecastEngineResult:
        """Fit on one store history and predict all canonical horizons."""


@dataclass(frozen=True)
class StoreDayObservation:
    store_id: str
    business_date: date
    actual_revenue: float
    machine_cycles: int = 0
    site_score_baseline_p50: float | None = None
    active_intervention_ids: tuple[str, ...] = ()
    data_quality_score: float | None = None
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
            data_quality_score=_optional_bounded_float(
                _first_present(data, "data_quality_score", "data_quality", default=None)
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
    tenant_id: str
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
            "tenant_id": self.tenant_id,
            "store_id": self.store_id,
            "feature_version": self.feature_version,
            "points": [observation.to_dict() for observation in self.observations],
            "point_count": len(self.observations),
        }


@dataclass(frozen=True)
class ForecastInput:
    store_id: str
    observations: tuple[StoreDayObservation, ...]
    tenant_id: str | None = None
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
            tenant_id=(
                str(data["tenant_id"]).strip() if data.get("tenant_id") not in {None, ""} else None
            ),
            horizon_days=int(data.get("horizon_days", 28)),
            target_metric=str(data.get("target_metric") or "revenue"),
            prediction_origin_time=_parse_datetime(
                data.get("prediction_origin_time") or datetime.now(UTC)
            ),
        )


@dataclass(frozen=True)
class ForecastOutput:
    forecast_output_id: str
    tenant_id: str
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
    engine_name: str = "baseline"
    model_name: str = "trailing_average"
    model_metadata: dict[str, Any] = field(default_factory=dict)
    data_staleness_days: int | None = None
    data_quality_score: float | None = None

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
            "tenant_id": self.tenant_id,
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
            "engine_name": self.engine_name,
            "model_name": self.model_name,
            "model_metadata": dict(self.model_metadata),
            "feature_version": self.feature_version,
            "policy_version": self.policy_version,
            "prediction_origin_time": self.prediction_origin_time.isoformat(),
            "scored_at": self.scored_at.isoformat(),
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "data_staleness_days": self.data_staleness_days,
            "data_quality_score": self.data_quality_score,
        }


def evaluate_forecast_alert_policy(
    policy: DecisionPolicy,
    output: ForecastOutput,
) -> ForecastAlertPolicyEvaluation:
    """Evaluate a versioned four-light policy without any threshold fallback.

    The policy's threshold rows are the only source of alert levels. The data
    quality guard is evaluated inside this policy evaluator so a stale series
    cannot be treated as a high-confidence red alert by a separate caller-side
    branch.
    """

    if policy.policy_kind != FORECAST_ALERT_POLICY_KIND:
        raise ForecastAlertPolicyError(
            f"cannot evaluate policy kind {policy.policy_kind!r} as a forecast alert policy"
        )
    if not policy.reads("sitescore_gap_ratio"):
        raise ForecastAlertPolicyError(
            f"policy {policy.policy_version_id!r} does not declare sitescore_gap_ratio"
        )

    thresholds = _policy_thresholds(policy)
    gap = output.sitescore_gap_ratio
    if not math.isfinite(gap):
        raise ForecastAlertPolicyError(
            f"policy {policy.policy_version_id!r} cannot evaluate a non-finite sitescore gap"
        )
    if gap <= thresholds[AlertLevel.RED]:
        threshold_level = AlertLevel.RED
    elif gap <= thresholds[AlertLevel.ORANGE]:
        threshold_level = AlertLevel.ORANGE
    elif gap <= thresholds[AlertLevel.YELLOW]:
        threshold_level = AlertLevel.YELLOW
    else:
        threshold_level = AlertLevel.GREEN

    guard = _policy_quality_guard(policy)
    guard_evidence: dict[str, Any] = {}
    level = threshold_level
    reason = "sitescore_gap" if threshold_level is not AlertLevel.GREEN else "within_expected_band"
    if guard is not None:
        max_staleness_days, action = guard
        staleness_days = output.data_staleness_days
        violated = staleness_days is None or staleness_days > max_staleness_days
        guard_evidence = {
            "max_staleness_days": max_staleness_days,
            "staleness_days": staleness_days,
            "violated": violated,
            "on_violation": action,
        }
        if violated and action == "SUPPRESS_HIGH_CONFIDENCE" and threshold_level is AlertLevel.RED:
            # Red is the high-confidence/high-severity branch. Preserve the
            # signal as orange while making the quality limitation explicit.
            level = AlertLevel.ORANGE
            reason = "data_quality_stale"
        elif violated and action == "SUPPRESS_ALERT":
            level = AlertLevel.GREEN
            reason = "data_quality_stale"

    return ForecastAlertPolicyEvaluation(
        alert_level=level,
        alert_reason_code=reason,
        quality_guard=guard_evidence,
        threshold_alert_level=threshold_level,
    )


def _policy_thresholds(policy: DecisionPolicy) -> dict[AlertLevel, float]:
    raw_thresholds = policy.parameters.get("thresholds")
    entries: list[tuple[str, Any]] = []
    if isinstance(raw_thresholds, Mapping):
        entries = [(str(level), value) for level, value in raw_thresholds.items()]
    elif isinstance(raw_thresholds, Sequence) and not isinstance(raw_thresholds, (str, bytes)):
        entries = [
            (str(item.get("level", "")), item)
            for item in raw_thresholds
            if isinstance(item, Mapping)
        ]
    else:
        raise ForecastAlertPolicyError(
            f"policy {policy.policy_version_id!r} has no valid thresholds"
        )

    thresholds: dict[AlertLevel, float] = {}
    for raw_level, raw_entry in entries:
        try:
            level = AlertLevel(str(raw_level).strip().lower())
        except ValueError as exc:
            raise ForecastAlertPolicyError(
                f"policy {policy.policy_version_id!r} contains an invalid alert level"
            ) from exc
        if level is AlertLevel.GREEN:
            raise ForecastAlertPolicyError("forecast alert policy thresholds cannot define GREEN")

        input_name = "sitescore_gap_ratio"
        operator = "<="
        value = raw_entry
        if isinstance(raw_entry, Mapping):
            input_name = str(raw_entry.get("input") or "sitescore_gap_ratio").strip()
            operator = str(raw_entry.get("op") or "<=").strip()
            value = raw_entry.get("value")
        if input_name != "sitescore_gap_ratio" or not policy.reads(input_name):
            raise ForecastAlertPolicyError(
                f"policy {policy.policy_version_id!r} threshold reads an undeclared input"
            )
        if operator != "<=":
            raise ForecastAlertPolicyError(
                f"policy {policy.policy_version_id!r} only supports '<=' thresholds"
            )
        try:
            numeric_value = float(value)
        except (TypeError, ValueError) as exc:
            raise ForecastAlertPolicyError(
                f"policy {policy.policy_version_id!r} has a non-numeric threshold"
            ) from exc
        if not math.isfinite(numeric_value):
            raise ForecastAlertPolicyError(
                f"policy {policy.policy_version_id!r} has a non-finite threshold"
            )
        if level in thresholds:
            raise ForecastAlertPolicyError(
                f"policy {policy.policy_version_id!r} defines {level.value} more than once"
            )
        thresholds[level] = numeric_value

    missing = [level.value for level in (AlertLevel.RED, AlertLevel.ORANGE, AlertLevel.YELLOW) if level not in thresholds]
    if missing:
        raise ForecastAlertPolicyError(
            f"policy {policy.policy_version_id!r} is missing thresholds: {', '.join(missing)}"
        )
    if not (
        thresholds[AlertLevel.RED]
        <= thresholds[AlertLevel.ORANGE]
        <= thresholds[AlertLevel.YELLOW]
    ):
        raise ForecastAlertPolicyError(
            f"policy {policy.policy_version_id!r} thresholds are not ordered from red to yellow"
        )
    return thresholds


def _policy_quality_guard(policy: DecisionPolicy) -> tuple[int, str] | None:
    raw_guard = policy.parameters.get("data_quality_guard")
    if raw_guard is None:
        return None
    if not isinstance(raw_guard, Mapping):
        raise ForecastAlertPolicyError(
            f"policy {policy.policy_version_id!r} has an invalid data quality guard"
        )
    try:
        max_staleness_days = int(raw_guard["max_staleness_days"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ForecastAlertPolicyError(
            f"policy {policy.policy_version_id!r} has an invalid max_staleness_days"
        ) from exc
    action = str(raw_guard.get("on_violation") or "").strip().upper()
    if max_staleness_days < 0 or action not in {"SUPPRESS_HIGH_CONFIDENCE", "SUPPRESS_ALERT"}:
        raise ForecastAlertPolicyError(
            f"policy {policy.policy_version_id!r} has an invalid data quality guard action"
        )
    return max_staleness_days, action


@dataclass(frozen=True)
class Alert:
    alert_id: str
    tenant_id: str
    store_id: str
    alert_level: AlertLevel
    alert_reason_code: str
    evidence_json: dict[str, Any]
    opened_at: datetime
    # ``policy_version`` is the stable cross-tenant policy label used by the
    # existing ForecastOps API (for example ``four-light-policy-v1``). The
    # tenant-bound registry key is also retained in evidence_json as
    # ``policy_version_id`` for consumers that read the evidence envelope.
    policy_id: str
    policy_version: str
    policy_version_id: str | None = None
    status: str = "open"
    closed_at: datetime | None = None
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    acknowledgement_note: str | None = None
    disposition: str | None = None
    disposition_set_by: str | None = None
    disposition_set_at: datetime | None = None
    deterioration_confirmed_at: datetime | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Alert:
        """Build an alert from its serialized API/document representation.

        ``to_dict`` includes the derived ``lead_time_days`` field, so passing
        that payload directly to the dataclass constructor is not a valid
        round-trip.  Keep deserialization here so batch jobs can accept both
        domain alerts and durable/API mappings without silently dropping the
        alert evaluation fields.
        """

        def optional_datetime(value: Any) -> datetime | None:
            if value is None or value == "":
                return None
            return _parse_datetime(value)

        raw_level = data["alert_level"]
        alert_level = (
            raw_level
            if isinstance(raw_level, AlertLevel)
            else AlertLevel(str(raw_level).strip().lower())
        )
        raw_policy_version_id = data.get("policy_version_id")
        return cls(
            alert_id=str(data["alert_id"]),
            tenant_id=str(data["tenant_id"]),
            store_id=str(data["store_id"]),
            alert_level=alert_level,
            alert_reason_code=str(data.get("alert_reason_code") or ""),
            evidence_json=dict(data.get("evidence_json") or {}),
            opened_at=_parse_datetime(data["opened_at"]),
            policy_id=str(data.get("policy_id") or ""),
            policy_version=str(data.get("policy_version") or ""),
            policy_version_id=(
                str(raw_policy_version_id) if raw_policy_version_id is not None else None
            ),
            status=str(data.get("status") or "open"),
            closed_at=optional_datetime(data.get("closed_at")),
            acknowledged_by=(
                str(data["acknowledged_by"])
                if data.get("acknowledged_by") is not None
                else None
            ),
            acknowledged_at=optional_datetime(data.get("acknowledged_at")),
            acknowledgement_note=(
                str(data["acknowledgement_note"])
                if data.get("acknowledgement_note") is not None
                else None
            ),
            disposition=(
                data["disposition"].value
                if isinstance(data.get("disposition"), AlertDisposition)
                else (
                    cls._normalize_disposition(data["disposition"])
                    if data.get("disposition") is not None
                    else None
                )
            ),
            disposition_set_by=(
                str(data["disposition_set_by"])
                if data.get("disposition_set_by") is not None
                else None
            ),
            disposition_set_at=optional_datetime(data.get("disposition_set_at")),
            deterioration_confirmed_at=optional_datetime(
                data.get("deterioration_confirmed_at")
            ),
        )

    @staticmethod
    def _normalize_disposition(value: Any) -> str:
        """Normalize known dispositions without breaking legacy free-form rows."""
        try:
            return AlertDisposition.from_str(value).value
        except ForecastOpsError:
            # Older API rows allowed free-form text.  Keep those rows readable
            # for migration/metrics while all new manual writes are validated
            # by ``close_with_disposition`` and ``ForecastFeedback.create``.
            return str(value).strip()

    @property
    def lead_time_days(self) -> int | None:
        """Advance warning lead time in days.

        Defined as deterioration_confirmed_at - opened_at, and only valid
        when disposition is TRUE_POSITIVE (ODP-FR-FCT-006).
        """
        if self.disposition is None or self.deterioration_confirmed_at is None:
            return None
        try:
            disp = (
                self.disposition
                if isinstance(self.disposition, AlertDisposition)
                else AlertDisposition.from_str(self.disposition)
            )
        except Exception:
            return None
        if disp is not AlertDisposition.TRUE_POSITIVE:
            return None
        det_date = (
            self.deterioration_confirmed_at.date()
            if isinstance(self.deterioration_confirmed_at, datetime)
            else self.deterioration_confirmed_at
        )
        open_date = (
            self.opened_at.date()
            if isinstance(self.opened_at, datetime)
            else self.opened_at
        )
        lead_time = (det_date - open_date).days
        return lead_time if lead_time >= 0 else None

    def acknowledge(self, *, actor: str, note: str | None = None, now: datetime) -> Alert:
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

    def close_with_disposition(
        self,
        *,
        disposition: str | AlertDisposition,
        actor: str,
        now: datetime,
        note: str | None = None,
        deterioration_confirmed_at: datetime | None = None,
    ) -> Alert:
        """Return a closed copy of this alert with recorded disposition."""
        if not disposition or not str(disposition).strip():
            raise ForecastOpsError("alert disposition requires a disposition value")
        if not actor or not actor.strip():
            raise ForecastOpsError("alert disposition requires an actor")
        disp_val = AlertDisposition.from_str(disposition).value
        return replace(
            self,
            status="closed",
            closed_at=now,
            disposition=disp_val,
            disposition_set_by=actor.strip(),
            disposition_set_at=now,
            acknowledgement_note=note or self.acknowledgement_note,
            deterioration_confirmed_at=(
                deterioration_confirmed_at
                if deterioration_confirmed_at is not None
                else self.deterioration_confirmed_at
            ),
        )

    def with_evaluation(
        self,
        *,
        disposition: str | AlertDisposition,
        deterioration_confirmed_at: datetime | None = None,
        actor: str | None = None,
        now: datetime | None = None,
        close: bool = False,
    ) -> Alert:
        """Return a copy of this alert updated with evaluation disposition and deterioration."""
        disp_val = AlertDisposition.from_str(disposition).value
        if (
            self.disposition == disp_val
            and self.deterioration_confirmed_at == deterioration_confirmed_at
        ):
            # Backfill is repeatable.  Do not rewrite audit timestamps or count
            # an unchanged evaluation as an update on every retry.
            return self
        changes: dict[str, Any] = {
            "disposition": disp_val,
            "deterioration_confirmed_at": deterioration_confirmed_at,
        }
        if actor is not None:
            changes["disposition_set_by"] = actor.strip()
        if now is not None:
            changes["disposition_set_at"] = now
        if close:
            changes["status"] = "closed"
            if now is not None and self.closed_at is None:
                changes["closed_at"] = now
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "tenant_id": self.tenant_id,
            "store_id": self.store_id,
            "alert_level": self.alert_level.value,
            "alert_reason_code": self.alert_reason_code,
            "evidence_json": self.evidence_json,
            "opened_at": self.opened_at.isoformat(),
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_version_id": self.policy_version_id,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "status": self.status,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "acknowledgement_note": self.acknowledgement_note,
            "disposition": (
                self.disposition.value
                if isinstance(self.disposition, AlertDisposition)
                else self.disposition
            ),
            "disposition_set_by": self.disposition_set_by,
            "disposition_set_at": (
                self.disposition_set_at.isoformat() if self.disposition_set_at else None
            ),
            "deterioration_confirmed_at": (
                self.deterioration_confirmed_at.isoformat()
                if self.deterioration_confirmed_at
                else None
            ),
            "lead_time_days": self.lead_time_days,
        }


@dataclass(frozen=True)
class InterventionHandoff:
    handoff_id: str
    tenant_id: str
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
            "tenant_id": self.tenant_id,
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
    *,
    tenant_id: str,
) -> list[ForecastSeries]:
    normalized_tenant_id = str(tenant_id or "").strip()
    if not normalized_tenant_id:
        raise ForecastOpsError("tenant_id is required to build ForecastOps timeseries")
    grouped: dict[str, list[StoreDayObservation]] = defaultdict(list)
    for observation in observations:
        item = _coerce_observation(observation)
        grouped[item.store_id].append(item)
    return [
        ForecastSeries(
            tenant_id=normalized_tenant_id,
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
    engine: ForecastEngine | None = None,
    policy_repository: DecisionPolicyRepository | None = None,
) -> tuple[list[ForecastOutput], list[Alert], list[InterventionHandoff]]:
    scored_time = scored_at or datetime.now(UTC)
    scored_time = _utc_datetime(scored_time)
    run_id = prediction_run_id or f"forecast-run-{uuid4()}"
    normalized_inputs = tuple(
        item if isinstance(item, ForecastInput) else ForecastInput.from_mapping(item)
        for item in inputs
    )
    if policy_repository is None:
        raise ForecastAlertPolicyError(
            "forecast alert policy_repository is required; refusing to produce alerts"
        )
    outputs: list[ForecastOutput] = []
    alerts: list[Alert] = []
    handoffs: list[InterventionHandoff] = []
    for item in normalized_inputs:
        forecast_input = _coerce_forecast_input(item)
        if not str(forecast_input.tenant_id or "").strip():
            raise ForecastOpsError("tenant_id is required for ForecastOps forecasting")
        output = _forecast_one(
            forecast_input,
            prediction_origin_time=prediction_origin_time or forecast_input.prediction_origin_time,
            scored_at=scored_time,
            prediction_run_id=run_id,
            engine=engine,
        )
        outputs.append(output)
        alert = _alert_for(
            output,
            opened_at=scored_time,
            policy_repository=policy_repository,
        )
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
    engine: ForecastEngine | None = None,
) -> ForecastOutput:
    observations = forecast_input.observations
    data_staleness_days: int | None = None
    data_quality_score: float | None = None
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
        data_staleness_days = max(
            (_utc_datetime(prediction_origin_time).date() - latest.business_date).days,
            0,
        )
        if any(observation.data_quality_score is None for observation in observations):
            data_quality_score = None
        else:
            data_quality_score = min(
                float(observation.data_quality_score) for observation in observations
            )
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

    if engine is None:
        spread = _prediction_spread(observations)
        bands = _forecast_bands(p50=p50, spread=spread, trajectory_class=trajectory_class)
        model_version = FORECASTOPS_MODEL_VERSION
        engine_name = "baseline"
        model_name = "trailing_average"
        model_metadata = {
            "algorithm": "trailing_average_with_residual_interval",
            "interval": "residual_cv_central_80",
        }
    else:
        engine_result = engine.fit_predict(forecast_input)
        missing_horizons = set(FORECAST_HORIZON_WEEKS) - set(engine_result.bands)
        if missing_horizons:
            missing = ", ".join(str(value) for value in sorted(missing_horizons))
            raise ForecastEngineError(
                f"{engine_result.engine_name} did not return forecast horizons: {missing}"
            )
        bands = {
            f"w{weeks}": _ordered_band(engine_result.bands[weeks])
            for weeks in FORECAST_HORIZON_WEEKS
        }
        model_version = engine_result.model_version
        engine_name = engine_result.engine_name
        model_name = engine_result.model_name
        model_metadata = dict(engine_result.metadata)

    if forecast_input.horizon_days % 7:
        raise ForecastOpsError("ForecastOps horizon_days must be a whole number of weeks")
    selected_horizon_weeks = forecast_input.horizon_days // 7
    if selected_horizon_weeks not in FORECAST_HORIZON_WEEKS:
        supported = ", ".join(str(weeks * 7) for weeks in FORECAST_HORIZON_WEEKS)
        raise ForecastOpsError(
            f"ForecastOps horizon_days must be one of the canonical values: {supported}"
        )
    selected_band = bands[f"w{selected_horizon_weeks}"]
    gap_ratio = _sitescore_gap_ratio(actual=actual, baseline=baseline)
    return ForecastOutput(
        forecast_output_id=_stable_id(
            "forecast-output",
            prediction_run_id,
            str(forecast_input.tenant_id),
            forecast_input.store_id,
            # One store may be scored for several canonical horizons inside a
            # single run; without the horizon in the identity the later inputs
            # collide with the first and are silently dropped by the
            # idempotent repository write.
            f"w{selected_horizon_weeks}",
        ),
        tenant_id=str(forecast_input.tenant_id),
        store_id=forecast_input.store_id,
        prediction_run_id=prediction_run_id,
        horizon_days=forecast_input.horizon_days,
        target_metric=forecast_input.target_metric,
        p10=selected_band.p10,
        p50=selected_band.p50,
        p90=selected_band.p90,
        w4=bands["w4"],
        w8=bands["w8"],
        w12=bands["w12"],
        w24=bands["w24"],
        trajectory_class=trajectory_class,
        turning_point_probability=turning_point_probability,
        sitescore_gap_ratio=gap_ratio,
        actual_revenue=actual,
        sitescore_baseline_p50=baseline,
        model_version=model_version,
        feature_version=FORECASTOPS_FEATURE_VERSION,
        policy_version=FOUR_LIGHT_POLICY_VERSION,
        prediction_origin_time=prediction_origin_time,
        scored_at=scored_at,
        source_snapshot_ids=source_snapshot_ids,
        engine_name=engine_name,
        model_name=model_name,
        model_metadata=model_metadata,
        data_staleness_days=data_staleness_days,
        data_quality_score=data_quality_score,
    )


def _alert_for(
    output: ForecastOutput,
    *,
    opened_at: datetime,
    policy_repository: DecisionPolicyRepository | None = None,
) -> Alert:
    if policy_repository is None:
        raise ForecastAlertPolicyError(
            "forecast alert policy_repository is required; refusing to produce an alert"
        )
    policy = resolve_policy(
        policy_repository,
        policy_kind=FORECAST_ALERT_POLICY_KIND,
        tenant_id=output.tenant_id,
        at=_utc_datetime(opened_at),
    )
    evaluation = evaluate_forecast_alert_policy(policy, output)
    return Alert(
        alert_id=_stable_id(
            "forecast-alert",
            output.forecast_output_id,
            policy.policy_version_id,
        ),
        tenant_id=output.tenant_id,
        store_id=output.store_id,
        alert_level=evaluation.alert_level,
        alert_reason_code=evaluation.alert_reason_code,
        evidence_json={
            "actual_revenue": output.actual_revenue,
            "forecast_p50": output.p50,
            "sitescore_baseline_p50": output.sitescore_baseline_p50,
            "sitescore_gap_ratio": output.sitescore_gap_ratio,
            "trajectory_class": output.trajectory_class,
            "engine_name": output.engine_name,
            "model_name": output.model_name,
            "model_version": output.model_version,
            # Keep the existing public label while also preserving the
            # tenant-bound registry identity and semver that governed this
            # alert.
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_label,
            "policy_version_id": policy.policy_version_id,
            "policy_semver": policy.policy_version,
            "declared_inputs": list(policy.declared_inputs),
            "policy_parameters": dict(policy.parameters),
            "data_quality_guard": evaluation.quality_guard,
            "threshold_alert_level": evaluation.threshold_alert_level.value,
        },
        opened_at=opened_at,
        policy_id=policy.policy_id,
        policy_version=policy.policy_label,
        policy_version_id=policy.policy_version_id,
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
        handoff_id=_stable_id("intervention-handoff", alert.alert_id),
        tenant_id=output.tenant_id,
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


def _stable_id(prefix: str, *parts: str) -> str:
    value = ":".join((prefix, *(str(part) for part in parts)))
    return f"{prefix}-{uuid5(NAMESPACE_URL, value)}"


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


def _ordered_band(band: ForecastBand) -> ForecastBand:
    p10, p50, p90 = sorted((max(0.0, band.p10), max(0.0, band.p50), max(0.0, band.p90)))
    return ForecastBand(p10=round(p10, 2), p50=round(p50, 2), p90=round(p90, 2))


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


def _utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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


def _optional_bounded_float(
    value: Any, lower: float = 0.0, upper: float = 1.0
) -> float | None:
    if value is None:
        return None
    return _bounded(value, lower=lower, upper=upper)
