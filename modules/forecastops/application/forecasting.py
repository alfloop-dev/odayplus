from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from models.shared_ml.production_runtime import ProductionModelRuntime
from modules.forecastops.application.production_model import (
    RegisteredEstimatorForecastEngine,
)
from modules.forecastops.domain.feedback import (
    FeedbackStatus,
    FeedbackType,
    ForecastFeedback,
    backfill_alert_precision,
    calculate_alert_precision_metrics,
    calculate_forecast_precision,
    filter_training_observations,
    validate_feedback_payload,
)
from modules.forecastops.domain.forecasting import (
    Alert,
    ForecastEngine,
    ForecastInput,
    ForecastOpsError,
    ForecastOpsNotFoundError,
    ForecastOutput,
    ForecastSeries,
    InterventionHandoff,
    StoreDayObservation,
    build_store_timeseries,
    forecast_stores,
)
from modules.forecastops.infrastructure.forecast_engines import create_forecast_engine
from modules.forecastops.infrastructure.repositories import (
    ForecastOpsRepository,
    InMemoryForecastOpsRepository,
)
from modules.forecastops.runtime import (
    ForecastOpsRuntimeConfigurationError,
    forecastops_production_required,
)
from shared.governance import DecisionPolicyRepository


@dataclass(frozen=True)
class ForecastOpsResult:
    forecasts: tuple[ForecastOutput, ...]
    alerts: tuple[Alert, ...]
    handoffs: tuple[InterventionHandoff, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "forecasts": [forecast.to_dict() for forecast in self.forecasts],
            "alerts": [alert.to_dict() for alert in self.alerts],
            "handoffs": [handoff.to_dict() for handoff in self.handoffs],
        }


class ForecastOpsService:
    def __init__(
        self,
        *,
        repository: ForecastOpsRepository | None = None,
        engine: str | ForecastEngine | None = None,
        model_name: str | None = None,
        engine_options: Mapping[str, Any] | None = None,
        model_runtime: ProductionModelRuntime | None = None,
        runtime_mode: str | None = None,
        policy_repository: DecisionPolicyRepository | None = None,
    ) -> None:
        self.production_required = forecastops_production_required(runtime_mode)
        if self.production_required and (
            repository is None or isinstance(repository, InMemoryForecastOpsRepository)
        ):
            raise ForecastOpsRuntimeConfigurationError(
                "ForecastOps production requires an injected durable repository"
            )
        self.repository = repository or InMemoryForecastOpsRepository()
        self.policy_repository = policy_repository
        self.model_runtime = model_runtime
        selected_engine: str | ForecastEngine | None = engine
        if selected_engine is None and self.production_required and model_runtime is not None:
            selected_engine = RegisteredEstimatorForecastEngine(model_runtime)
        if selected_engine is None and not self.production_required:
            selected_engine = "baseline"
        self.engine = _resolve_engine(
            selected_engine,
            model_name=model_name,
            engine_options=engine_options,
        )
        _require_production_engine(
            self.engine,
            production_required=self.production_required,
        )

    def ingest_timeseries(
        self,
        observations: Iterable[StoreDayObservation | Mapping[str, Any]],
        *,
        tenant_id: str,
    ) -> list[ForecastSeries]:
        series = build_store_timeseries(observations, tenant_id=tenant_id)
        return [self.repository.save_series(item) for item in series]

    def forecast(
        self,
        inputs: Iterable[ForecastInput | Mapping[str, Any]],
        *,
        prediction_origin_time: datetime | None = None,
        prediction_run_id: str | None = None,
        scored_at: datetime | None = None,
        engine: str | ForecastEngine | None = None,
        model_name: str | None = None,
        engine_options: Mapping[str, Any] | None = None,
        policy_repository: DecisionPolicyRepository | None = None,
    ) -> ForecastOpsResult:
        from datetime import UTC
        from uuid import NAMESPACE_URL, uuid4, uuid5

        from shared.domain import ForecastOutput as CanonicalForecastOutput
        from shared.domain import Prediction, PredictionRun

        normalized_inputs = tuple(
            item if isinstance(item, ForecastInput) else ForecastInput.from_mapping(item)
            for item in inputs
        )
        tenant_ids = {
            str(item.tenant_id or "").strip()
            for item in normalized_inputs
            if str(item.tenant_id or "").strip()
        }
        if len(tenant_ids) != 1 or any(
            not str(item.tenant_id or "").strip() for item in normalized_inputs
        ):
            raise ForecastOpsError(
                "ForecastOps forecast batch requires exactly one authenticated tenant scope"
            )
        tenant_id = next(iter(tenant_ids))
        origins = {
            _utc_datetime(prediction_origin_time or item.prediction_origin_time)
            for item in normalized_inputs
        }
        if len(origins) != 1:
            raise ForecastOpsError(
                "ForecastOps forecast batch requires one prediction origin"
            )
        origin = next(iter(origins))

        # Feedback annotations are applied at the application boundary so all
        # normal forecast callers, including the worker path, train on the same
        # filtered series used by the feedback-aware precision evaluator.
        normalized_inputs = tuple(
            self._filter_forecast_input(item, tenant_id=tenant_id)
            for item in normalized_inputs
        )

        selected_engine = (
            self.engine
            if engine is None
            else _resolve_engine(
                engine,
                model_name=model_name,
                engine_options=engine_options,
            )
        )
        _require_production_engine(
            selected_engine,
            production_required=self.production_required,
        )
        run_id = prediction_run_id or f"pred-run-forecast-{uuid4()}"
        active_policy_repository = (
            policy_repository
            if policy_repository is not None
            else self.policy_repository
        )
        forecasts, alerts, handoffs = forecast_stores(
            normalized_inputs,
            prediction_origin_time=origin,
            scored_at=scored_at,
            prediction_run_id=run_id,
            engine=selected_engine,
            policy_repository=active_policy_repository,
        )
        saved_forecasts = tuple(self.repository.save_forecast(forecast) for forecast in forecasts)

        if saved_forecasts:
            feature_snapshots = [
                datetime.combine(
                    max(item.observations, key=lambda value: value.business_date).business_date
                    + timedelta(days=1),
                    time.min,
                    tzinfo=UTC,
                )
                for item in normalized_inputs
                if item.observations
            ]
            feature_snapshot_time = max(feature_snapshots, default=origin)
            selected_horizons = sorted(
                {f"w{item.horizon_days // 7}" for item in normalized_inputs}
            )
            run = PredictionRun(
                prediction_run_id=run_id,
                model_version_id=saved_forecasts[0].model_version,
                feature_snapshot_time=feature_snapshot_time,
                prediction_origin_time=origin,
                prediction_horizon=",".join(selected_horizons),
                run_status="succeeded",
            )
            self.repository.save_prediction_run(tenant_id, run)

            for f in saved_forecasts:
                canonical_forecast = CanonicalForecastOutput(
                    forecast_output_id=f.forecast_output_id,
                    store_id=f.store_id,
                    prediction_run_id=run_id,
                    horizon_days=f.horizon_days,
                    target_metric=f.target_metric,
                    p10=f.p10,
                    p50=f.p50,
                    p90=f.p90,
                    trajectory_class=f.trajectory_class,
                    turning_point_probability=f.turning_point_probability,
                    sitescore_gap_ratio=f.sitescore_gap_ratio,
                )
                self.repository.save_canonical_forecast(tenant_id, canonical_forecast)

                pred = Prediction(
                    prediction_id=(
                        "prediction-"
                        f"{uuid5(NAMESPACE_URL, f'{run_id}:{f.store_id}:w{f.horizon_days // 7}')}"
                    ),
                    prediction_run_id=run_id,
                    entity_type="store",
                    entity_id=f.store_id,
                    target_name="revenue",
                    p10_value=f.p10,
                    p50_value=f.p50,
                    p90_value=f.p90,
                    unit="TWD",
                    explanation_json={
                        "engine_name": f.engine_name,
                        "model_name": f.model_name,
                        "model_version": f.model_version,
                        "model_metadata": dict(f.model_metadata),
                    },
                )
                self.repository.save_prediction(tenant_id, pred)

        return ForecastOpsResult(
            forecasts=saved_forecasts,
            alerts=tuple(self._persist_generated_alert(tenant_id, alert) for alert in alerts),
            handoffs=tuple(
                self._persist_generated_handoff(tenant_id, handoff) for handoff in handoffs
            ),
        )

    def _filter_forecast_input(
        self,
        forecast_input: ForecastInput,
        *,
        tenant_id: str,
    ) -> ForecastInput:
        feedbacks = self.repository.list_feedbacks(
            tenant_id,
            store_id=forecast_input.store_id,
        )
        filtered_observations = filter_training_observations(
            forecast_input.observations,
            feedbacks,
        )
        return replace(forecast_input, observations=tuple(filtered_observations))

    def _persist_generated_alert(self, tenant_id: str, alert: Alert) -> Alert:
        """Persist a generated alert without rewinding an already-stored one.

        Alert identity is derived from the deduplicated forecast, so an
        at-least-once job replay regenerates an alert that is already persisted
        in its initial ``open`` state. Writing it again would silently discard
        the operator acknowledgement recorded between the two deliveries.
        """

        existing = self.repository.get_alert(tenant_id, alert.alert_id)
        if existing is not None:
            return existing
        return self.repository.save_alert(alert)

    def _persist_generated_handoff(
        self, tenant_id: str, handoff: InterventionHandoff
    ) -> InterventionHandoff:
        """Persist a generated handoff without rewinding a dispatched one."""

        existing = self.repository.get_handoff(tenant_id, handoff.handoff_id)
        if existing is not None:
            return existing
        return self.repository.save_handoff(handoff)

    def acknowledge_alert(
        self,
        tenant_id: str,
        alert_id: str,
        *,
        actor: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> Alert:
        """Acknowledge a persisted four-light alert and persist the acknowledgement."""

        alert = self.repository.get_alert(tenant_id, alert_id)
        if alert is None:
            raise ForecastOpsNotFoundError(f"alert {alert_id} not found")
        acknowledged = alert.acknowledge(actor=actor, note=note, now=now or datetime.now(UTC))
        return self.repository.save_alert(acknowledged)

    def execute_handoff(
        self,
        tenant_id: str,
        handoff_id: str,
        *,
        actor: str,
        intervention_id: str | None = None,
        now: datetime | None = None,
    ) -> InterventionHandoff:
        """Dispatch a proposed intervention handoff, linking the opened case."""

        handoff = self.repository.get_handoff(tenant_id, handoff_id)
        if handoff is None:
            raise ForecastOpsNotFoundError(f"handoff {handoff_id} not found")
        executed = handoff.execute(
            actor=actor, intervention_id=intervention_id, now=now or datetime.now(UTC)
        )
        return self.repository.save_handoff(executed)

    def submit_feedback(
        self,
        tenant_id: str,
        *,
        store_id: str,
        feedback_type: FeedbackType | str,
        reason: str,
        actor: str,
        target_date_start: date | str | None = None,
        target_date_end: date | str | None = None,
        target_date: date | str | None = None,
        corrected_revenue: float | None = None,
        alert_id: str | None = None,
        disposition: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        raw_payload: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ForecastFeedback:
        """Submit a feedback record adhering to ODP-FR-FCT-008 and ODP-BR-GOV-001."""
        if raw_payload is not None:
            validate_feedback_payload(raw_payload)

        current_time = now or datetime.now(UTC)
        feedback = ForecastFeedback.create(
            tenant_id=tenant_id,
            store_id=store_id,
            feedback_type=feedback_type,
            reason=reason,
            created_by=actor,
            target_date_start=target_date_start,
            target_date_end=target_date_end,
            target_date=target_date,
            corrected_revenue=corrected_revenue,
            alert_id=alert_id,
            disposition=disposition,
            now=current_time,
            metadata=metadata,
        )

        if feedback.feedback_type is FeedbackType.ALERT_DISPOSITION:
            alert = self.repository.get_alert(tenant_id, feedback.alert_id)
            if alert is None:
                raise ForecastOpsNotFoundError(f"alert {feedback.alert_id} not found")
            closed_alert = alert.close_with_disposition(
                disposition=str(feedback.disposition),
                actor=actor,
                now=current_time,
                note=reason,
            )
            self.repository.save_alert(closed_alert)

        return self.repository.save_feedback(feedback)

    def approve_outcome_correction(
        self,
        tenant_id: str,
        feedback_id: str,
        *,
        actor: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> tuple[ForecastFeedback, ForecastOutput | None]:
        """Approve an OUTCOME_CORRECTION feedback, update canonical actuals, and trigger re-forecast."""
        feedback = self.repository.get_feedback(tenant_id, feedback_id)
        if feedback is None:
            raise ForecastOpsNotFoundError(f"feedback {feedback_id} not found")
        if feedback.feedback_type is not FeedbackType.OUTCOME_CORRECTION:
            raise ForecastOpsError(
                f"feedback {feedback_id} is of type {feedback.feedback_type.value}; only OUTCOME_CORRECTION can be approved"
            )
        if feedback.status is not FeedbackStatus.PENDING_APPROVAL:
            raise ForecastOpsError(
                f"feedback {feedback_id} is in status {feedback.status.value} and cannot be approved"
            )

        current_time = now or datetime.now(UTC)
        updated_feedback = replace(
            feedback,
            status=FeedbackStatus.APPROVED,
            approved_by=actor,
            approved_at=current_time,
        )
        saved_feedback = self.repository.save_feedback(updated_feedback)

        # Update canonical series observation
        series = self.repository.get_series(tenant_id, feedback.store_id)
        if series is not None and feedback.corrected_revenue is not None:
            new_observations: list[StoreDayObservation] = []
            matched = False
            for obs in series.observations:
                if feedback.target_date_start <= obs.business_date <= feedback.target_date_end:
                    new_obs = replace(obs, actual_revenue=feedback.corrected_revenue)
                    new_observations.append(new_obs)
                    matched = True
                else:
                    new_observations.append(obs)
            if not matched:
                new_obs = StoreDayObservation(
                    store_id=feedback.store_id,
                    business_date=feedback.target_date_start,
                    actual_revenue=feedback.corrected_revenue,
                )
                new_observations.append(new_obs)
                new_observations.sort(key=lambda o: o.business_date)

            updated_series = replace(series, observations=tuple(new_observations))
            self.repository.save_series(updated_series)

            # Trigger recalculation / re-forecast through the same filtered
            # training series used by the normal forecast path.  The raw series
            # remains canonical; CONTEXT_ANNOTATION only excludes observations
            # from model input and precision evaluation.
            training_series = self.get_training_series(tenant_id, feedback.store_id)
            forecast_input = ForecastInput(
                tenant_id=tenant_id,
                store_id=feedback.store_id,
                observations=(
                    training_series.observations
                    if training_series is not None
                    else updated_series.observations
                ),
                prediction_origin_time=current_time,
            )
            forecast_result = self.forecast([forecast_input], scored_at=current_time)
            recalculated_forecast = (
                forecast_result.forecasts[0] if forecast_result.forecasts else None
            )
            return saved_feedback, recalculated_forecast

        return saved_feedback, None

    def reject_outcome_correction(
        self,
        tenant_id: str,
        feedback_id: str,
        *,
        actor: str,
        rejection_reason: str | None = None,
        now: datetime | None = None,
    ) -> ForecastFeedback:
        """Reject an OUTCOME_CORRECTION feedback."""
        feedback = self.repository.get_feedback(tenant_id, feedback_id)
        if feedback is None:
            raise ForecastOpsNotFoundError(f"feedback {feedback_id} not found")
        if feedback.feedback_type is not FeedbackType.OUTCOME_CORRECTION:
            raise ForecastOpsError(
                f"feedback {feedback_id} is of type {feedback.feedback_type.value}; only OUTCOME_CORRECTION can be rejected"
            )
        if feedback.status is not FeedbackStatus.PENDING_APPROVAL:
            raise ForecastOpsError(
                f"feedback {feedback_id} is in status {feedback.status.value} and cannot be rejected"
            )

        updated_feedback = replace(
            feedback,
            status=FeedbackStatus.REJECTED,
            rejection_reason=rejection_reason,
        )
        return self.repository.save_feedback(updated_feedback)

    def list_feedbacks(
        self,
        tenant_id: str,
        *,
        store_id: str | None = None,
        feedback_type: str | None = None,
        status: str | None = None,
    ) -> list[ForecastFeedback]:
        return self.repository.list_feedbacks(
            tenant_id,
            store_id=store_id,
            feedback_type=feedback_type,
            status=status,
        )

    def get_feedback(self, tenant_id: str, feedback_id: str) -> ForecastFeedback | None:
        return self.repository.get_feedback(tenant_id, feedback_id)

    def get_training_series(self, tenant_id: str, store_id: str) -> ForecastSeries | None:
        """Get series observations with CONTEXT_ANNOTATION periods excluded for training."""
        series = self.repository.get_series(tenant_id, store_id)
        if series is None:
            return None
        feedbacks = self.repository.list_feedbacks(tenant_id, store_id=store_id)
        clean_obs = filter_training_observations(series.observations, feedbacks)
        return replace(series, observations=tuple(clean_obs))

    def evaluate_precision(
        self,
        tenant_id: str,
        store_id: str,
        forecast_output_id: str,
    ) -> dict[str, Any]:
        """Evaluate forecast precision, excluding periods with active CONTEXT_ANNOTATION."""
        canonical = self.repository.get_canonical_forecast(tenant_id, forecast_output_id)
        if canonical is None:
            forecasts = [
                f
                for f in self.repository.latest_forecasts(tenant_id)
                if f.forecast_output_id == forecast_output_id
            ]
            if not forecasts:
                raise ForecastOpsNotFoundError(f"forecast {forecast_output_id} not found")
            forecast = forecasts[0]
        else:
            forecast = next(
                (
                    f
                    for f in self.repository.latest_forecasts(tenant_id)
                    if f.forecast_output_id == forecast_output_id
                ),
                None,
            )
            if forecast is None:
                raise ForecastOpsNotFoundError(f"forecast {forecast_output_id} not found")

        series = self.repository.get_series(tenant_id, store_id)
        observations = series.observations if series is not None else ()
        feedbacks = self.repository.list_feedbacks(tenant_id, store_id=store_id)
        return calculate_forecast_precision(forecast, observations, feedbacks)

    def evaluate_alert_precision(
        self,
        tenant_id: str,
        *,
        store_id: str | None = None,
    ) -> dict[str, Any]:
        """Calculate precision and lead time metrics across alerts for a tenant (ODP-FR-FCT-006)."""
        if store_id is not None:
            alerts = self.repository.list_alerts_by_store(tenant_id, store_id)
        else:
            alerts = self.repository.list_alerts(tenant_id)
        return calculate_alert_precision_metrics(alerts)

    def backfill_alert_precision(
        self,
        tenant_id: str,
        *,
        store_id: str | None = None,
        as_of: datetime | None = None,
        evaluation_horizon_days: int = 28,
        min_observations: int | None = None,
        actor: str = "precision_backfill_job",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Batch backfill deterioration_confirmed_at and disposition for alerts (ODP-FR-FCT-006)."""
        if store_id is not None:
            alerts = self.repository.list_alerts_by_store(tenant_id, store_id)
            series = self.repository.get_series(tenant_id, store_id)
            observations = series.observations if series is not None else ()
            feedbacks = self.repository.list_feedbacks(tenant_id, store_id=store_id)
        else:
            alerts = self.repository.list_alerts(tenant_id)
            all_series = self.repository.list_series(tenant_id)
            observations = [obs for s in all_series for obs in s.observations]
            feedbacks = self.repository.list_feedbacks(tenant_id)

        updated_alerts, metrics = backfill_alert_precision(
            alerts,
            observations=observations,
            feedbacks=feedbacks,
            policy_repository=self.policy_repository,
            evaluation_horizon_days=evaluation_horizon_days,
            min_observations=min_observations,
            as_of=as_of,
            actor=actor,
            now=now,
        )

        for alert in updated_alerts:
            self.repository.save_alert(alert)

        return {
            "tenant_id": tenant_id,
            "store_id": store_id,
            "updated_count": len(updated_alerts),
            "metrics": metrics,
            "alerts": [a.to_dict() for a in updated_alerts],
        }


def _utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _resolve_engine(
    engine: str | ForecastEngine | None,
    *,
    model_name: str | None,
    engine_options: Mapping[str, Any] | None,
) -> ForecastEngine | None:
    if engine is None:
        return None
    if isinstance(engine, str):
        return create_forecast_engine(
            engine,
            model_name=model_name,
            options=dict(engine_options or {}),
        )
    if model_name is not None or engine_options:
        raise ValueError(
            "model_name and engine_options are only valid when engine is selected by name"
        )
    return engine


def _require_production_engine(
    engine: ForecastEngine | None,
    *,
    production_required: bool,
) -> None:
    if not production_required:
        return
    if engine is None:
        raise ForecastOpsRuntimeConfigurationError(
            "ForecastOps production requires the registered MLflow estimator runtime"
        )
    engine_name = str(getattr(engine, "engine_name", "")).strip().lower()
    if engine_name != "mlflow_registered_oss":
        raise ForecastOpsRuntimeConfigurationError(
            "ForecastOps production requires engine 'mlflow_registered_oss'; "
            f"received {engine_name or '<missing>'!r}"
        )


__all__ = ["ForecastOpsResult", "ForecastOpsService"]
