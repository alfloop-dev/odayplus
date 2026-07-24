"""Durable, SQLite-backed implementations of the product module repositories.

Each class mirrors the public surface of its ``InMemory*`` counterpart exactly
(same method names, same return values, same versioning semantics) so it is a
drop-in replacement behind the existing repository interfaces — domain and
application tests stay compatible. State lives in ``durable_documents`` via
:class:`SqliteDocumentStore`, so writes survive a process restart.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from models.shared_ml.artifact_store import (
    ArtifactRecord,
    artifact_uri,
    compute_content_digest,
    make_artifact_id,
)
from models.shared_ml.model_card import ModelCard
from models.shared_ml.registry import ModelAlias, ModelRegistryError, ModelVersion
from models.shared_ml.validation import ValidationRun
from modules.adlift.domain.incrementality import IncrementalityReport
from modules.avm.domain import DataRoom, NormalizedMargin, ValuationCase, ValuationReport
from modules.forecastops.domain.forecasting import (
    Alert,
    ForecastOutput,
    ForecastSeries,
    InterventionHandoff,
)
from modules.heatzone.workers import HeatZoneBatchScoreResult
from modules.intervention.domain.lifecycle import Intervention, LabelRecord
from modules.learninghub.domain import (
    DatasetSnapshot,
    InferenceComparison,
    MonitoringEvaluation,
    RetrainingRequest,
)
from modules.learninghub.infrastructure.repositories import ReleaseDecisionRecord
from modules.listing.domain.models import CandidateSiteDraft, ListingDedupKey
from modules.netplan.domain import (
    ApprovalRecord as NetPlanApprovalRecord,
)
from modules.netplan.domain import (
    ExecutionRecord as NetPlanExecutionRecord,
)
from modules.netplan.domain import (
    NetPlanScenario,
)
from modules.netplan.domain import (
    OutcomeRecord as NetPlanOutcomeRecord,
)
from modules.netplan.domain import (
    ScenarioSolveRecord as NetPlanScenarioSolveRecord,
)
from modules.priceops.domain.pricing import (
    ApprovalRecord,
    InterventionTreatmentHandoff,
    LabelRegistryEntry,
    ObservationWindow,
    PlanOptimization,
    PlanSimulation,
    PricingEffectEvaluation,
    PricingExecution,
    PricingPlan,
    RollbackPlan,
)
from modules.sitescore.domain.scoring import SiteScoreReport
from shared.domain import ForecastOutput as CanonicalForecastOutput
from shared.domain import Prediction, PredictionRun, SiteScoreRun
from shared.domain.models import (
    AddressLocation,
    Brand,
    Listing,
    Machine,
    MachineCycle,
    Store,
    Tenant,
    Transaction,
)
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.workflow.sitescore import RealizedSite, SiteScoreDecision


class TenantScopeRequiredError(ValueError):
    """Raised when a production repository read omits its tenant boundary."""


def _requires_tenant_scope(engine: Any) -> bool:
    return str(getattr(engine, "dialect", "")).lower() == "postgresql"


def _require_tenant_scope(engine: Any, tenant_id: str | None) -> str | None:
    normalized = str(tenant_id or "").strip()
    if _requires_tenant_scope(engine) and not normalized:
        raise TenantScopeRequiredError(
            "tenant_id is required for PostgreSQL business-data reads"
        )
    return normalized or None


def _append_in_filter(
    clauses: list[str],
    params: list[Any],
    column: str,
    values: tuple[str, ...] | list[str],
) -> None:
    normalized = tuple(str(value).strip() for value in values if str(value).strip())
    if not normalized:
        return
    clauses.append(f"{column} IN ({', '.join('?' for _ in normalized)})")
    params.extend(normalized)


class DurableSiteScoreRepository:
    """Durable mirror of ``InMemorySiteScoreRepository``."""

    _C = "sitescore.reports"
    _PREDICTION_RUNS = "sitescore.prediction_runs"
    _PREDICTIONS = "sitescore.predictions"
    _SITESCORE_RUNS = "sitescore.sitescore_runs"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save_report(self, report: SiteScoreReport) -> SiteScoreReport:
        version = self._store.count_in_group(self._C, report.candidate_site_id) + 1
        versioned = report.with_version(
            report_version=version,
            report_id=f"sitescore-report-{uuid4()}",
        )
        self._store.append_version(
            self._C,
            versioned.report_id,
            versioned,
            group_key=versioned.candidate_site_id,
        )
        return versioned

    def latest(self, candidate_site_id: str) -> SiteScoreReport | None:
        return self._store.latest_in_group(self._C, candidate_site_id)

    def history(self, candidate_site_id: str) -> list[SiteScoreReport]:
        return self._store.list_by_group(self._C, candidate_site_id)

    def get_report(self, report_id: str) -> SiteScoreReport | None:
        return self._store.get(self._C, report_id)

    def list_latest(self) -> list[SiteScoreReport]:
        return self._store.latest_per_group(self._C)

    def save_prediction_run(self, run: PredictionRun) -> PredictionRun:
        self._store.put(self._PREDICTION_RUNS, run.prediction_run_id, run)
        return run

    def get_prediction_run(self, prediction_run_id: str) -> PredictionRun | None:
        return self._store.get(self._PREDICTION_RUNS, prediction_run_id)

    def save_prediction(self, prediction: Prediction) -> Prediction:
        self._store.append_version(
            self._PREDICTIONS,
            f"prediction-{uuid4()}",
            prediction,
            group_key=prediction.prediction_run_id,
        )
        return prediction

    def get_predictions(self, prediction_run_id: str) -> list[Prediction]:
        return self._store.list_by_group(self._PREDICTIONS, prediction_run_id)

    def save_sitescore_run(self, run: SiteScoreRun) -> SiteScoreRun:
        self._store.put(self._SITESCORE_RUNS, run.sitescore_run_id, run)
        return run

    def get_sitescore_run(self, sitescore_run_id: str) -> SiteScoreRun | None:
        return self._store.get(self._SITESCORE_RUNS, sitescore_run_id)


class DurableAVMRepository:
    """Durable mirror of ``InMemoryAVMRepository``."""

    _CASES = "avm.cases"
    _MARGINS = "avm.margins"
    _REPORTS = "avm.reports"
    _DATAROOMS = "avm.datarooms"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save_case(self, case: ValuationCase) -> ValuationCase:
        self._store.put(self._CASES, case.case_id, case)
        return case

    def get_case(self, case_id: str) -> ValuationCase | None:
        return self._store.get(self._CASES, case_id)

    def list_cases(self) -> list[ValuationCase]:
        return self._store.list_all(self._CASES)

    def save_margin(self, margin: NormalizedMargin) -> NormalizedMargin:
        self._store.put(self._MARGINS, margin.case_id, margin)
        return margin

    def get_margin(self, case_id: str) -> NormalizedMargin | None:
        return self._store.get(self._MARGINS, case_id)

    def save_report(self, report: ValuationReport) -> ValuationReport:
        version = self._store.count_in_group(self._REPORTS, report.case_id) + 1
        versioned = report.with_version(
            valuation_version=version,
            report_id=f"avm-report-{uuid4()}",
        )
        self._store.append_version(
            self._REPORTS,
            versioned.report_id,
            versioned,
            group_key=versioned.case_id,
        )
        return versioned

    def replace_latest_report(self, report: ValuationReport) -> ValuationReport:
        if self._store.count_in_group(self._REPORTS, report.case_id) == 0:
            self._store.put(
                self._REPORTS, report.report_id, report, group_key=report.case_id, seq=1
            )
        else:
            self._store.replace_latest_in_group(self._REPORTS, report, group_key=report.case_id)
        return report

    def latest_report(self, case_id: str) -> ValuationReport | None:
        return self._store.latest_in_group(self._REPORTS, case_id)

    def report_history(self, case_id: str) -> list[ValuationReport]:
        return self._store.list_by_group(self._REPORTS, case_id)

    def save_dataroom(self, dataroom: DataRoom) -> DataRoom:
        self._store.put(self._DATAROOMS, dataroom.case_id, dataroom)
        return dataroom

    def get_dataroom(self, case_id: str) -> DataRoom | None:
        return self._store.get(self._DATAROOMS, case_id)


class DurableForecastOpsRepository:
    """Durable mirror of ``InMemoryForecastOpsRepository``."""

    _SERIES = "forecastops.series"
    _FORECASTS = "forecastops.forecasts"
    _ALERTS = "forecastops.alerts"
    _HANDOFFS = "forecastops.handoffs"
    _PREDICTION_RUNS = "forecastops.prediction_runs"
    _PREDICTIONS = "forecastops.predictions"
    _CANONICAL_FORECASTS = "forecastops.canonical_forecasts"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save_series(self, series: ForecastSeries) -> ForecastSeries:
        self._store.put(self._SERIES, series.store_id, series)
        return series

    def list_series(self) -> list[ForecastSeries]:
        return self._store.list_all(self._SERIES)

    def get_series(self, store_id: str) -> ForecastSeries | None:
        return self._store.get(self._SERIES, store_id)

    def save_forecast(self, forecast: ForecastOutput) -> ForecastOutput:
        version = self._store.count_in_group(self._FORECASTS, forecast.store_id) + 1
        versioned = forecast.with_version(
            forecast_version=version,
            forecast_output_id=f"forecast-output-{uuid4()}",
        )
        self._store.append_version(
            self._FORECASTS,
            versioned.forecast_output_id,
            versioned,
            group_key=versioned.store_id,
        )
        return versioned

    def latest_forecasts(self) -> list[ForecastOutput]:
        return self._store.latest_per_group(self._FORECASTS)

    def history(self, store_id: str) -> list[ForecastOutput]:
        return self._store.list_by_group(self._FORECASTS, store_id)

    def save_alert(self, alert: Alert) -> Alert:
        self._store.put(
            self._ALERTS,
            alert.alert_id,
            alert,
            group_key=alert.store_id,
        )
        return alert

    def list_alerts(self) -> list[Alert]:
        return self._store.list_all(self._ALERTS)

    def list_alerts_by_store(self, store_id: str) -> list[Alert]:
        return self._store.list_by_group(self._ALERTS, store_id)

    def get_alert(self, alert_id: str) -> Alert | None:
        return self._store.get(self._ALERTS, alert_id)

    def save_handoff(self, handoff: InterventionHandoff) -> InterventionHandoff:
        self._store.put(self._HANDOFFS, handoff.handoff_id, handoff)
        return handoff

    def list_handoffs(self) -> list[InterventionHandoff]:
        return self._store.list_all(self._HANDOFFS)

    def get_handoff(self, handoff_id: str) -> InterventionHandoff | None:
        return self._store.get(self._HANDOFFS, handoff_id)

    def save_prediction_run(self, run: PredictionRun) -> PredictionRun:
        self._store.put(self._PREDICTION_RUNS, run.prediction_run_id, run)
        return run

    def get_prediction_run(self, prediction_run_id: str) -> PredictionRun | None:
        return self._store.get(self._PREDICTION_RUNS, prediction_run_id)

    def save_prediction(self, prediction: Prediction) -> Prediction:
        self._store.append_version(
            self._PREDICTIONS,
            f"prediction-{uuid4()}",
            prediction,
            group_key=prediction.prediction_run_id,
        )
        return prediction

    def get_predictions(self, prediction_run_id: str) -> list[Prediction]:
        return self._store.list_by_group(self._PREDICTIONS, prediction_run_id)

    def save_canonical_forecast(self, forecast: CanonicalForecastOutput) -> CanonicalForecastOutput:
        self._store.put(self._CANONICAL_FORECASTS, forecast.forecast_output_id, forecast)
        return forecast

    def get_canonical_forecast(self, forecast_output_id: str) -> CanonicalForecastOutput | None:
        return self._store.get(self._CANONICAL_FORECASTS, forecast_output_id)


class DurableAdLiftRepository:
    """Durable mirror of ``InMemoryAdLiftRepository``."""

    _C = "adlift.reports"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save_report(self, report: IncrementalityReport) -> IncrementalityReport:
        version = self._store.count_in_group(self._C, report.campaign_id) + 1
        versioned = report.with_version(
            report_version=version,
            report_id=f"adlift-report-{uuid4()}",
        )
        self._store.append_version(
            self._C,
            versioned.report_id,
            versioned,
            group_key=versioned.campaign_id,
        )
        return versioned

    def latest_reports(self) -> list[IncrementalityReport]:
        return self._store.latest_per_group(self._C)

    def latest_for_campaign(self, campaign_id: str) -> IncrementalityReport | None:
        return self._store.latest_in_group(self._C, campaign_id)

    def history(self, campaign_id: str) -> list[IncrementalityReport]:
        return self._store.list_by_group(self._C, campaign_id)


class DurableInterventionRepository:
    """Durable mirror of ``InMemoryInterventionRepository``."""

    _C = "intervention.interventions"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save(self, intervention: Intervention) -> Intervention:
        self._store.put(
            self._C,
            intervention.intervention_id,
            intervention,
            group_key=intervention.store_id,
        )
        return intervention

    def get(self, intervention_id: str) -> Intervention | None:
        return self._store.get(self._C, intervention_id)

    def list_all(self) -> list[Intervention]:
        return self._store.list_all(self._C)

    def list_by_store(self, store_id: str) -> list[Intervention]:
        return self._store.list_by_group(self._C, store_id)


class DurablePriceOpsRepository:
    """Durable mirror of ``InMemoryPriceOpsRepository``."""

    _PLANS = "priceops.plans"
    _SIMULATIONS = "priceops.simulations"
    _OPTIMIZATIONS = "priceops.optimizations"
    _APPROVALS = "priceops.approvals"
    _WINDOWS = "priceops.windows"
    _EXECUTIONS = "priceops.executions"
    _ROLLBACKS = "priceops.rollback_plans"
    _HANDOFFS = "priceops.handoffs"
    _LABELS = "priceops.label_entries"
    _EVALUATIONS = "priceops.evaluations"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save_plan(self, plan: PricingPlan) -> PricingPlan:
        self._store.put(self._PLANS, plan.plan_id, plan, group_key=plan.tenant_id)
        return plan

    def get_plan(self, plan_id: str) -> PricingPlan | None:
        return self._store.get(self._PLANS, plan_id)

    def list_plans(self) -> list[PricingPlan]:
        return self._store.list_all(self._PLANS)

    def save_simulation(self, simulation: PlanSimulation) -> PlanSimulation:
        self._store.put(self._SIMULATIONS, simulation.plan_id, simulation)
        return simulation

    def get_simulation(self, plan_id: str) -> PlanSimulation | None:
        return self._store.get(self._SIMULATIONS, plan_id)

    def save_optimization(self, optimization: PlanOptimization) -> PlanOptimization:
        self._store.put(self._OPTIMIZATIONS, optimization.plan_id, optimization)
        return optimization

    def get_optimization(self, plan_id: str) -> PlanOptimization | None:
        return self._store.get(self._OPTIMIZATIONS, plan_id)

    def save_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        self._store.put(self._APPROVALS, approval.decision_id, approval, group_key=approval.plan_id)
        return approval

    def list_approvals(self, plan_id: str) -> list[ApprovalRecord]:
        return self._store.list_by_group(self._APPROVALS, plan_id)

    def save_window(self, window: ObservationWindow) -> ObservationWindow:
        self._store.put(self._WINDOWS, window.window_id, window, group_key=window.plan_id)
        return window

    def get_window(self, plan_id: str) -> ObservationWindow | None:
        windows = self._store.list_by_group(self._WINDOWS, plan_id)
        return windows[-1] if windows else None

    def save_rollback_plan(self, rollback_plan: RollbackPlan) -> RollbackPlan:
        self._store.put(self._ROLLBACKS, rollback_plan.plan_id, rollback_plan)
        return rollback_plan

    def get_rollback_plan(self, plan_id: str) -> RollbackPlan | None:
        return self._store.get(self._ROLLBACKS, plan_id)

    def save_execution(self, execution: PricingExecution) -> PricingExecution:
        self._store.put(
            self._EXECUTIONS, execution.execution_id, execution, group_key=execution.plan_id
        )
        return execution

    def get_execution(self, plan_id: str) -> PricingExecution | None:
        executions = self._store.list_by_group(self._EXECUTIONS, plan_id)
        return executions[-1] if executions else None

    def save_handoff(self, handoff: InterventionTreatmentHandoff) -> InterventionTreatmentHandoff:
        self._store.put(self._HANDOFFS, handoff.handoff_id, handoff, group_key=handoff.plan_id)
        return handoff

    def list_handoffs(self, plan_id: str) -> list[InterventionTreatmentHandoff]:
        return self._store.list_by_group(self._HANDOFFS, plan_id)

    def save_label_entry(self, entry: LabelRegistryEntry) -> LabelRegistryEntry:
        self._store.put(self._LABELS, entry.entry_id, entry, group_key=entry.plan_id)
        return entry

    def list_label_entries(self, plan_id: str) -> list[LabelRegistryEntry]:
        return self._store.list_by_group(self._LABELS, plan_id)

    def save_evaluation(self, evaluation: PricingEffectEvaluation) -> PricingEffectEvaluation:
        self._store.put(self._EVALUATIONS, evaluation.plan_id, evaluation)
        return evaluation

    def get_evaluation(self, plan_id: str) -> PricingEffectEvaluation | None:
        return self._store.get(self._EVALUATIONS, plan_id)


class DurableLabelRegistry:
    """Durable mirror of ``InMemoryLabelRegistry`` (intervention label hook)."""

    _C = "intervention.labels"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def __call__(self, label: LabelRecord) -> None:
        self._store.put(self._C, label.intervention_id, label, group_key=label.store_id)

    def get(self, intervention_id: str) -> LabelRecord | None:
        return self._store.get(self._C, intervention_id)

    def list_labels(self) -> list[LabelRecord]:
        return self._store.list_all(self._C)

    def intervened_windows(self, store_id: str) -> list[LabelRecord]:
        return [
            label
            for label in self._store.list_by_group(self._C, store_id)
            if label.exclude_from_baseline
        ]


class DurableLearningHubRepository:
    """Durable mirror of ``InMemoryLearningHubRepository``.

    Implements the full :class:`LearningHubRepository` surface over
    :class:`SqliteDocumentStore`, so model versions, validation runs, model
    cards, aliases, shadow/canary/promotion/rollback state, and release
    decisions all survive a process restart. Alias pointers are stored as small
    overwritable documents (the document store has no delete; clearing an alias
    writes a ``None`` pointer), matching the in-memory mapping semantics.
    """

    _DATASETS = "learninghub.datasets"
    _VERSIONS = "learninghub.model_versions"
    _CARDS = "learninghub.model_cards"
    _VALIDATIONS = "learninghub.validation_runs"
    _ALIASES = "learninghub.aliases"
    _RELEASES = "learninghub.release_decisions"
    _MONITORING = "learninghub.monitoring_evaluations"
    _RETRAINING = "learninghub.retraining_requests"
    _COMPARISONS = "learninghub.inference_comparisons"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    @property
    def storage_path(self) -> Path:
        """Stable location used by colocated durable OSS service adapters."""
        return self._store.engine.path

    # -- datasets ---------------------------------------------------------

    def save_dataset_snapshot(self, snapshot: DatasetSnapshot) -> DatasetSnapshot:
        self._store.put(self._DATASETS, snapshot.dataset_snapshot_id, snapshot)
        return snapshot

    def get_dataset_snapshot(self, dataset_snapshot_id: str) -> DatasetSnapshot | None:
        return self._store.get(self._DATASETS, dataset_snapshot_id)

    # -- model versions ---------------------------------------------------

    def save_model_version(self, model_version: ModelVersion) -> ModelVersion:
        self._store.put(
            self._VERSIONS,
            model_version.model_id,
            model_version,
            group_key=model_version.model_name,
        )
        return model_version

    def get_model_version(self, model_name: str, version: str) -> ModelVersion | None:
        return self._store.get(self._VERSIONS, f"{model_name}:{version}")

    def list_model_versions(self, model_name: str) -> list[ModelVersion]:
        return self._store.list_by_group(self._VERSIONS, model_name)

    # -- model cards ------------------------------------------------------

    def save_model_card(self, model_card: ModelCard) -> ModelCard:
        self._store.put(
            self._CARDS,
            f"{model_card.model_name}:{model_card.model_version}",
            model_card,
            group_key=model_card.model_name,
        )
        return model_card

    def get_model_card(self, model_name: str, version: str) -> ModelCard | None:
        return self._store.get(self._CARDS, f"{model_name}:{version}")

    # -- validation runs --------------------------------------------------

    def save_validation_run(self, validation_run: ValidationRun) -> ValidationRun:
        self._store.put(self._VALIDATIONS, validation_run.validation_run_id, validation_run)
        return validation_run

    def get_validation_run(self, validation_run_id: str) -> ValidationRun | None:
        return self._store.get(self._VALIDATIONS, validation_run_id)

    # -- aliases ----------------------------------------------------------

    def _alias_doc_id(self, model_name: str, alias: ModelAlias) -> str:
        return f"{model_name}:{alias.value}"

    def _get_alias_pointer(self, model_name: str, alias: ModelAlias) -> str | None:
        return self._store.get(self._ALIASES, self._alias_doc_id(model_name, alias))

    def _set_alias_pointer(self, model_name: str, alias: ModelAlias, version: str | None) -> None:
        self._store.put(
            self._ALIASES,
            self._alias_doc_id(model_name, alias),
            version,
            group_key=model_name,
        )

    def set_alias(self, model_name: str, alias: ModelAlias, version: str) -> ModelVersion:
        model_version = self.get_model_version(model_name, version)
        if model_version is None:
            raise ModelRegistryError(f"unknown model version {model_name}:{version}")

        previous_version = self._get_alias_pointer(model_name, alias)
        if previous_version:
            previous = self.get_model_version(model_name, previous_version)
            if previous is not None:
                self.save_model_version(previous.with_aliases(previous.aliases - {alias}))

        self._set_alias_pointer(model_name, alias, version)
        updated = model_version.with_aliases(model_version.aliases | {alias})
        self.save_model_version(updated)
        return updated

    def clear_alias(self, model_name: str, alias: ModelAlias) -> None:
        version = self._get_alias_pointer(model_name, alias)
        if version is None:
            return
        self._set_alias_pointer(model_name, alias, None)
        model_version = self.get_model_version(model_name, version)
        if model_version is not None:
            self.save_model_version(model_version.with_aliases(model_version.aliases - {alias}))

    def get_alias(self, model_name: str, alias: ModelAlias) -> ModelVersion | None:
        version = self._get_alias_pointer(model_name, alias)
        if version is None:
            return None
        return self.get_model_version(model_name, version)

    # -- release decisions ------------------------------------------------

    def save_release_decision(self, decision: ReleaseDecisionRecord) -> ReleaseDecisionRecord:
        self._store.put(self._RELEASES, decision.release_id, decision)
        return decision

    def get_release_decision(self, release_id: str) -> object | None:
        return self._store.get(self._RELEASES, release_id)

    def list_release_decisions(self) -> list[object]:
        return self._store.list_all(self._RELEASES)

    # -- monitoring and retraining ---------------------------------------

    def save_monitoring_evaluation(
        self, evaluation: MonitoringEvaluation
    ) -> MonitoringEvaluation:
        self._store.put(
            self._MONITORING,
            evaluation.evaluation_id,
            evaluation,
            group_key=evaluation.model_name,
        )
        return evaluation

    def get_monitoring_evaluation(self, evaluation_id: str) -> MonitoringEvaluation | None:
        return self._store.get(self._MONITORING, evaluation_id)

    def list_monitoring_evaluations(
        self, model_name: str | None = None
    ) -> list[MonitoringEvaluation]:
        if model_name is None:
            return self._store.list_all(self._MONITORING)
        return self._store.list_by_group(self._MONITORING, model_name)

    def save_retraining_request(self, request: RetrainingRequest) -> RetrainingRequest:
        self._store.put(
            self._RETRAINING,
            request.request_id,
            request,
            group_key=request.model_name,
        )
        return request

    def get_retraining_request(self, request_id: str) -> RetrainingRequest | None:
        return self._store.get(self._RETRAINING, request_id)

    def list_retraining_requests(self, model_name: str | None = None) -> list[RetrainingRequest]:
        if model_name is None:
            return self._store.list_all(self._RETRAINING)
        return self._store.list_by_group(self._RETRAINING, model_name)

    # -- inference comparisons -------------------------------------------

    def save_inference_comparison(self, comparison: InferenceComparison) -> InferenceComparison:
        self._store.put(
            self._COMPARISONS,
            comparison.comparison_id,
            comparison,
            group_key=comparison.model_name,
        )
        return comparison

    def get_inference_comparison(self, comparison_id: str) -> InferenceComparison | None:
        return self._store.get(self._COMPARISONS, comparison_id)

    def list_inference_comparisons(
        self, model_name: str | None = None
    ) -> list[InferenceComparison]:
        if model_name is None:
            return self._store.list_all(self._COMPARISONS)
        return self._store.list_by_group(self._COMPARISONS, model_name)


class DurableNetPlanRepository:
    """Durable mirror of ``InMemoryNetPlanRepository``."""

    _SCENARIOS = "netplan.scenarios"
    _SOLVES = "netplan.solves"
    _APPROVALS = "netplan.approvals"
    _EXECUTIONS = "netplan.executions"
    _OUTCOMES = "netplan.outcomes"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save_scenario(self, scenario: NetPlanScenario) -> NetPlanScenario:
        self._store.put(self._SCENARIOS, scenario.scenario_id, scenario)
        return scenario

    def get_scenario(self, scenario_id: str) -> NetPlanScenario | None:
        return self._store.get(self._SCENARIOS, scenario_id)

    def list_scenarios(self) -> list[NetPlanScenario]:
        return self._store.list_all(self._SCENARIOS)

    def save_solve(self, solve: NetPlanScenarioSolveRecord) -> NetPlanScenarioSolveRecord:
        self._store.put(self._SOLVES, solve.scenario_id, solve)
        return solve

    def get_solve(self, scenario_id: str) -> NetPlanScenarioSolveRecord | None:
        return self._store.get(self._SOLVES, scenario_id)

    def save_approval(self, approval: NetPlanApprovalRecord) -> NetPlanApprovalRecord:
        self._store.put(
            self._APPROVALS,
            approval.approval_id,
            approval,
            group_key=approval.scenario_id,
        )
        return approval

    def list_approvals(self, scenario_id: str) -> list[NetPlanApprovalRecord]:
        return self._store.list_by_group(self._APPROVALS, scenario_id)

    def save_execution(self, execution: NetPlanExecutionRecord) -> NetPlanExecutionRecord:
        self._store.put(self._EXECUTIONS, execution.scenario_id, execution)
        return execution

    def get_execution(self, scenario_id: str) -> NetPlanExecutionRecord | None:
        return self._store.get(self._EXECUTIONS, scenario_id)

    def save_outcome(self, outcome: NetPlanOutcomeRecord) -> NetPlanOutcomeRecord:
        self._store.put(self._OUTCOMES, outcome.scenario_id, outcome)
        return outcome

    def get_outcome(self, scenario_id: str) -> NetPlanOutcomeRecord | None:
        return self._store.get(self._OUTCOMES, scenario_id)


class DurableArtifactStore:
    """Durable mirror of ``InMemoryArtifactStore``.

    Content-addressed: blobs are deduplicated by SHA-256 digest in one
    collection, record metadata in another (grouped by ``model_name``). Both
    survive a process restart, so ``ModelVersion.artifact_uri`` references real,
    re-hashable bytes and the registry evidence stays tamper-evident.
    """

    _RECORDS = "model_registry.artifacts"
    _BLOBS = "model_registry.artifact_blobs"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def put_artifact(
        self,
        *,
        model_name: str,
        version: str,
        kind: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord:
        digest = compute_content_digest(data)
        self._store.put(self._BLOBS, digest, bytes(data))
        record = ArtifactRecord(
            artifact_id=make_artifact_id(model_name, version, kind),
            model_name=model_name,
            version=version,
            kind=kind,
            content_digest=digest,
            size_bytes=len(data),
            content_type=content_type,
            uri=artifact_uri(digest),
            metadata=dict(metadata or {}),
        )
        self._store.put(self._RECORDS, record.artifact_id, record, group_key=model_name)
        return record

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        return self._store.get(self._RECORDS, artifact_id)

    def open_artifact(self, artifact_id: str) -> bytes | None:
        record = self.get_artifact(artifact_id)
        if record is None:
            return None
        return self._store.get(self._BLOBS, record.content_digest)

    def list_artifacts(self, model_name: str) -> list[ArtifactRecord]:
        return self._store.list_by_group(self._RECORDS, model_name)

    def list_artifacts_for_version(self, model_name: str, version: str) -> list[ArtifactRecord]:
        return [record for record in self.list_artifacts(model_name) if record.version == version]

    def verify(self, artifact_id: str) -> bool:
        record = self.get_artifact(artifact_id)
        if record is None:
            return False
        blob = self._store.get(self._BLOBS, record.content_digest)
        if blob is None:
            return False
        return compute_content_digest(blob) == record.content_digest


@dataclass
class InMemoryTenantRepository:
    _tenants: dict[str, Tenant] = field(default_factory=dict)

    def save_tenant(self, tenant: Tenant) -> Tenant:
        self._tenants[tenant.tenant_id] = tenant
        return tenant

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def list_tenants(self) -> list[Tenant]:
        return list(self._tenants.values())


class DurableTenantRepository:
    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    def save_tenant(self, tenant: Tenant) -> Tenant:
        from datetime import datetime
        self._engine.execute(
            "INSERT INTO tenants (tenant_id, tenant_name, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(tenant_id) DO UPDATE SET "
            "  tenant_name = excluded.tenant_name, "
            "  status = excluded.status, "
            "  updated_at = CURRENT_TIMESTAMP",
            (
                tenant.tenant_id,
                tenant.tenant_name,
                tenant.status,
                tenant.created_at.isoformat() if isinstance(tenant.created_at, datetime) else str(tenant.created_at),
                datetime.now().isoformat()
            )
        )
        return tenant

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        from datetime import datetime
        row = self._engine.query_one("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,))
        if not row:
            return None
        return Tenant(
            tenant_id=row["tenant_id"],
            tenant_name=row["tenant_name"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"])
        )

    def list_tenants(self) -> list[Tenant]:
        from datetime import datetime
        rows = self._engine.query("SELECT * FROM tenants ORDER BY created_at")
        return [
            Tenant(
                tenant_id=row["tenant_id"],
                tenant_name=row["tenant_name"],
                status=row["status"],
                created_at=datetime.fromisoformat(row["created_at"])
            )
            for row in rows
        ]


@dataclass
class InMemoryBrandRepository:
    _brands: dict[str, Brand] = field(default_factory=dict)

    def save_brand(self, brand: Brand) -> Brand:
        self._brands[brand.brand_id] = brand
        return brand

    def get_brand(self, brand_id: str) -> Brand | None:
        return self._brands.get(brand_id)

    def list_brands(self) -> list[Brand]:
        return list(self._brands.values())


class DurableBrandRepository:
    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    def save_brand(self, brand: Brand) -> Brand:
        self._engine.execute(
            "INSERT INTO brands (brand_id, tenant_id, brand_code, brand_name, brand_type, brand_capture_group, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            "ON CONFLICT(brand_id) DO UPDATE SET "
            "  tenant_id = excluded.tenant_id, "
            "  brand_code = excluded.brand_code, "
            "  brand_name = excluded.brand_name, "
            "  brand_type = excluded.brand_type, "
            "  brand_capture_group = excluded.brand_capture_group, "
            "  status = excluded.status, "
            "  updated_at = CURRENT_TIMESTAMP",
            (
                brand.brand_id,
                brand.tenant_id,
                brand.brand_code,
                brand.brand_name,
                brand.brand_type,
                brand.brand_capture_group,
                brand.status
            )
        )
        return brand

    def get_brand(self, brand_id: str) -> Brand | None:
        row = self._engine.query_one("SELECT * FROM brands WHERE brand_id = ?", (brand_id,))
        if not row:
            return None
        return Brand(
            brand_id=row["brand_id"],
            tenant_id=row["tenant_id"],
            brand_code=row["brand_code"],
            brand_name=row["brand_name"],
            brand_type=row["brand_type"],
            brand_capture_group=row["brand_capture_group"] or "",
            status=row["status"]
        )

    def list_brands(self) -> list[Brand]:
        rows = self._engine.query("SELECT * FROM brands")
        return [
            Brand(
                brand_id=row["brand_id"],
                tenant_id=row["tenant_id"],
                brand_code=row["brand_code"],
                brand_name=row["brand_name"],
                brand_type=row["brand_type"],
                brand_capture_group=row["brand_capture_group"] or "",
                status=row["status"]
            )
            for row in rows
        ]


@dataclass
class InMemoryAddressLocationRepository:
    _addresses: dict[str, AddressLocation] = field(default_factory=dict)

    def save_address(self, address: AddressLocation) -> AddressLocation:
        self._addresses[address.address_id] = address
        return address

    def get_address(self, address_id: str) -> AddressLocation | None:
        return self._addresses.get(address_id)

    def list_addresses(self) -> list[AddressLocation]:
        return list(self._addresses.values())


class DurableAddressLocationRepository:
    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    def save_address(self, address: AddressLocation) -> AddressLocation:
        postgres = _requires_tenant_scope(self._engine)
        geom_expression = (
            "ST_SetSRID(ST_MakePoint(?, ?), 4326)" if postgres else "?"
        )
        geom_params: tuple[Any, ...] = (
            (address.longitude, address.latitude)
            if postgres
            else (address.raw_address,)
        )
        self._engine.execute(
            "INSERT INTO address_locations ("
            "  address_id, raw_address, normalized_address, city, district, village, road, "
            "  latitude, longitude, geom, geocode_precision, geocode_confidence, "
            "  h3_res_8, h3_res_9, h3_res_10, manual_override_flag, created_at, updated_at"
            f") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, {geom_expression}, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            "ON CONFLICT(address_id) DO UPDATE SET "
            "  raw_address = excluded.raw_address, "
            "  normalized_address = excluded.normalized_address, "
            "  city = excluded.city, "
            "  district = excluded.district, "
            "  village = excluded.village, "
            "  road = excluded.road, "
            "  latitude = excluded.latitude, "
            "  longitude = excluded.longitude, "
            "  geom = excluded.geom, "
            "  geocode_precision = excluded.geocode_precision, "
            "  geocode_confidence = excluded.geocode_confidence, "
            "  h3_res_8 = excluded.h3_res_8, "
            "  h3_res_9 = excluded.h3_res_9, "
            "  h3_res_10 = excluded.h3_res_10, "
            "  manual_override_flag = excluded.manual_override_flag, "
            "  updated_at = CURRENT_TIMESTAMP",
            (
                address.address_id,
                address.raw_address,
                address.normalized_address,
                address.city,
                address.district,
                address.village,
                address.road,
                address.latitude,
                address.longitude,
                *geom_params,
                address.geocode_precision,
                address.geocode_confidence,
                address.h3_res_8,
                address.h3_res_9,
                address.h3_res_10,
                bool(address.manual_override_flag),
            )
        )
        return address

    def get_address(self, address_id: str) -> AddressLocation | None:
        row = self._engine.query_one("SELECT * FROM address_locations WHERE address_id = ?", (address_id,))
        if not row:
            return None
        return AddressLocation(
            address_id=row["address_id"],
            raw_address=row["raw_address"],
            normalized_address=row["normalized_address"] or "",
            city=row["city"] or "",
            district=row["district"] or "",
            village=row["village"] or "",
            road=row["road"] or "",
            latitude=row["latitude"] or 0.0,
            longitude=row["longitude"] or 0.0,
            geocode_precision=row["geocode_precision"],
            geocode_confidence=row["geocode_confidence"] or 0.0,
            h3_res_8=row["h3_res_8"] or "",
            h3_res_9=row["h3_res_9"] or "",
            h3_res_10=row["h3_res_10"] or "",
            manual_override_flag=bool(row["manual_override_flag"])
        )

    def list_addresses(self) -> list[AddressLocation]:
        rows = self._engine.query("SELECT * FROM address_locations")
        return [
            AddressLocation(
                address_id=row["address_id"],
                raw_address=row["raw_address"],
                normalized_address=row["normalized_address"] or "",
                city=row["city"] or "",
                district=row["district"] or "",
                village=row["village"] or "",
                road=row["road"] or "",
                latitude=row["latitude"] or 0.0,
                longitude=row["longitude"] or 0.0,
                geocode_precision=row["geocode_precision"],
                geocode_confidence=row["geocode_confidence"] or 0.0,
                h3_res_8=row["h3_res_8"] or "",
                h3_res_9=row["h3_res_9"] or "",
                h3_res_10=row["h3_res_10"] or "",
                manual_override_flag=bool(row["manual_override_flag"])
            )
            for row in rows
        ]


@dataclass
class InMemoryStoreRepository:
    _stores: dict[str, Store] = field(default_factory=dict)

    def save_store(self, store: Store) -> Store:
        self._stores[store.store_id] = store
        return store

    def get_store(self, store_id: str) -> Store | None:
        return self._stores.get(store_id)

    def list_stores(
        self,
        *,
        tenant_id: str | None = None,
        brand_ids: tuple[str, ...] = (),
        region_codes: tuple[str, ...] = (),
        store_ids: tuple[str, ...] = (),
    ) -> list[Store]:
        stores = list(self._stores.values())
        if tenant_id:
            stores = [store for store in stores if store.tenant_id == tenant_id]
        if brand_ids:
            stores = [store for store in stores if store.brand_id in brand_ids]
        if region_codes:
            stores = [store for store in stores if store.region_code in region_codes]
        if store_ids:
            stores = [store for store in stores if store.store_id in store_ids]
        return stores


class DurableStoreRepository:
    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    def save_store(self, store: Store) -> Store:
        from datetime import date, datetime, time
        opened_on = store.opened_on.isoformat() if isinstance(store.opened_on, date) else store.opened_on
        closed_on = store.closed_on.isoformat() if isinstance(store.closed_on, date) else store.closed_on
        service_start_time = store.service_start_time.isoformat() if isinstance(store.service_start_time, time) else store.service_start_time
        service_end_time = store.service_end_time.isoformat() if isinstance(store.service_end_time, time) else store.service_end_time
        effective_from = store.effective_from.isoformat() if isinstance(store.effective_from, datetime) else store.effective_from
        effective_to = store.effective_to.isoformat() if isinstance(store.effective_to, datetime) else store.effective_to

        self._engine.execute(
            "INSERT INTO stores ("
            "  store_id, tenant_id, brand_id, source_store_id, store_name, store_status, "
            "  ownership_type, store_format_code, opened_on, closed_on, address_id, region_code, "
            "  service_start_time, service_end_time, effective_from, effective_to, is_current, "
            "  created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            "ON CONFLICT(store_id) DO UPDATE SET "
            "  tenant_id = excluded.tenant_id, "
            "  brand_id = excluded.brand_id, "
            "  source_store_id = excluded.source_store_id, "
            "  store_name = excluded.store_name, "
            "  store_status = excluded.store_status, "
            "  ownership_type = excluded.ownership_type, "
            "  store_format_code = excluded.store_format_code, "
            "  opened_on = excluded.opened_on, "
            "  closed_on = excluded.closed_on, "
            "  address_id = excluded.address_id, "
            "  region_code = excluded.region_code, "
            "  service_start_time = excluded.service_start_time, "
            "  service_end_time = excluded.service_end_time, "
            "  effective_from = excluded.effective_from, "
            "  effective_to = excluded.effective_to, "
            "  is_current = excluded.is_current, "
            "  updated_at = CURRENT_TIMESTAMP",
            (
                store.store_id,
                store.tenant_id,
                store.brand_id,
                store.source_store_id,
                store.store_name,
                store.store_status,
                store.ownership_type,
                store.store_format_code,
                opened_on,
                closed_on,
                store.address_id,
                store.region_code,
                service_start_time,
                service_end_time,
                effective_from,
                effective_to,
                bool(store.is_current),
            )
        )
        return store

    def get_store(self, store_id: str) -> Store | None:
        from datetime import date, datetime, time
        row = self._engine.query_one("SELECT * FROM stores WHERE store_id = ?", (store_id,))
        if not row:
            return None
        return Store(
            store_id=row["store_id"],
            tenant_id=row["tenant_id"],
            brand_id=row["brand_id"],
            source_store_id=row["source_store_id"] or "",
            store_name=row["store_name"],
            store_status=row["store_status"],
            ownership_type=row["ownership_type"],
            store_format_code=row["store_format_code"] or "",
            opened_on=date.fromisoformat(row["opened_on"]) if row["opened_on"] else None,
            closed_on=date.fromisoformat(row["closed_on"]) if row["closed_on"] else None,
            address_id=row["address_id"] or "",
            region_code=row["region_code"] or "",
            service_start_time=time.fromisoformat(row["service_start_time"]),
            service_end_time=time.fromisoformat(row["service_end_time"]),
            effective_from=datetime.fromisoformat(row["effective_from"]),
            effective_to=datetime.fromisoformat(row["effective_to"]),
            is_current=bool(row["is_current"])
        )

    def list_stores(
        self,
        *,
        tenant_id: str | None = None,
        brand_ids: tuple[str, ...] = (),
        region_codes: tuple[str, ...] = (),
        store_ids: tuple[str, ...] = (),
    ) -> list[Store]:
        from datetime import date, datetime, time
        tenant_id = _require_tenant_scope(self._engine, tenant_id)
        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        _append_in_filter(clauses, params, "brand_id", brand_ids)
        _append_in_filter(clauses, params, "region_code", region_codes)
        _append_in_filter(clauses, params, "store_id", store_ids)
        where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._engine.query(
            f"SELECT * FROM stores{where_clause} ORDER BY store_id",
            tuple(params),
        )
        return [
            Store(
                store_id=row["store_id"],
                tenant_id=row["tenant_id"],
                brand_id=row["brand_id"],
                source_store_id=row["source_store_id"] or "",
                store_name=row["store_name"],
                store_status=row["store_status"],
                ownership_type=row["ownership_type"],
                store_format_code=row["store_format_code"] or "",
                opened_on=date.fromisoformat(row["opened_on"]) if row["opened_on"] else None,
                closed_on=date.fromisoformat(row["closed_on"]) if row["closed_on"] else None,
                address_id=row["address_id"] or "",
                region_code=row["region_code"] or "",
                service_start_time=time.fromisoformat(row["service_start_time"]),
                service_end_time=time.fromisoformat(row["service_end_time"]),
                effective_from=datetime.fromisoformat(row["effective_from"]),
                effective_to=datetime.fromisoformat(row["effective_to"]),
                is_current=bool(row["is_current"])
            )
            for row in rows
        ]


@dataclass
class InMemoryMachineRepository:
    _machines: dict[str, Machine] = field(default_factory=dict)

    def save_machine(self, machine: Machine) -> Machine:
        self._machines[machine.machine_id] = machine
        return machine

    def get_machine(self, machine_id: str) -> Machine | None:
        return self._machines.get(machine_id)

    def list_machines(self) -> list[Machine]:
        return list(self._machines.values())


class DurableMachineRepository:
    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    def save_machine(self, machine: Machine) -> Machine:
        from datetime import date, datetime
        installed_on = machine.installed_on.isoformat() if isinstance(machine.installed_on, date) else machine.installed_on
        removed_on = machine.removed_on.isoformat() if isinstance(machine.removed_on, date) else machine.removed_on
        effective_from = machine.effective_from.isoformat() if isinstance(machine.effective_from, datetime) else machine.effective_from
        effective_to = machine.effective_to.isoformat() if isinstance(machine.effective_to, datetime) else machine.effective_to

        self._engine.execute(
            "INSERT INTO machines ("
            "  machine_id, store_id, source_machine_id, machine_serial_no, equipment_brand_id, "
            "  machine_family, machine_type, capacity_kg, capacity_band, installed_on, removed_on, "
            "  machine_status, effective_from, effective_to, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            "ON CONFLICT(machine_id) DO UPDATE SET "
            "  store_id = excluded.store_id, "
            "  source_machine_id = excluded.source_machine_id, "
            "  machine_serial_no = excluded.machine_serial_no, "
            "  equipment_brand_id = excluded.equipment_brand_id, "
            "  machine_family = excluded.machine_family, "
            "  machine_type = excluded.machine_type, "
            "  capacity_kg = excluded.capacity_kg, "
            "  capacity_band = excluded.capacity_band, "
            "  installed_on = excluded.installed_on, "
            "  removed_on = excluded.removed_on, "
            "  machine_status = excluded.machine_status, "
            "  effective_from = excluded.effective_from, "
            "  effective_to = excluded.effective_to, "
            "  updated_at = CURRENT_TIMESTAMP",
            (
                machine.machine_id,
                machine.store_id,
                machine.source_machine_id,
                machine.machine_serial_no,
                machine.equipment_brand_id,
                machine.machine_family,
                machine.machine_type,
                machine.capacity_kg,
                machine.capacity_band,
                installed_on,
                removed_on,
                machine.machine_status,
                effective_from,
                effective_to
            )
        )
        return machine

    def get_machine(self, machine_id: str) -> Machine | None:
        from datetime import date, datetime
        row = self._engine.query_one("SELECT * FROM machines WHERE machine_id = ?", (machine_id,))
        if not row:
            return None
        return Machine(
            machine_id=row["machine_id"],
            store_id=row["store_id"],
            source_machine_id=row["source_machine_id"] or "",
            machine_serial_no=row["machine_serial_no"] or "",
            equipment_brand_id=row["equipment_brand_id"] or "",
            machine_family=row["machine_family"],
            machine_type=row["machine_type"] or "",
            capacity_kg=row["capacity_kg"] or 0.0,
            capacity_band=row["capacity_band"],
            installed_on=date.fromisoformat(row["installed_on"]) if row["installed_on"] else None,
            removed_on=date.fromisoformat(row["removed_on"]) if row["removed_on"] else None,
            machine_status=row["machine_status"],
            effective_from=datetime.fromisoformat(row["effective_from"]),
            effective_to=datetime.fromisoformat(row["effective_to"])
        )

    def list_machines(self) -> list[Machine]:
        from datetime import date, datetime
        rows = self._engine.query("SELECT * FROM machines")
        return [
            Machine(
                machine_id=row["machine_id"],
                store_id=row["store_id"],
                source_machine_id=row["source_machine_id"] or "",
                machine_serial_no=row["machine_serial_no"] or "",
                equipment_brand_id=row["equipment_brand_id"] or "",
                machine_family=row["machine_family"],
                machine_type=row["machine_type"] or "",
                capacity_kg=row["capacity_kg"] or 0.0,
                capacity_band=row["capacity_band"],
                installed_on=date.fromisoformat(row["installed_on"]) if row["installed_on"] else None,
                removed_on=date.fromisoformat(row["removed_on"]) if row["removed_on"] else None,
                machine_status=row["machine_status"],
                effective_from=datetime.fromisoformat(row["effective_from"]),
                effective_to=datetime.fromisoformat(row["effective_to"])
            )
            for row in rows
        ]


@dataclass
class InMemoryTransactionRepository:
    _transactions: dict[str, Transaction] = field(default_factory=dict)

    def save_transaction(self, transaction: Transaction) -> Transaction:
        self._transactions[transaction.transaction_id] = transaction
        return transaction

    def get_transaction(self, transaction_id: str) -> Transaction | None:
        return self._transactions.get(transaction_id)

    def list_transactions(
        self,
        *,
        tenant_id: str | None = None,
        store_ids: tuple[str, ...] = (),
    ) -> list[Transaction]:
        # In-memory transactions carry scope through the tenant-filtered store ids.
        if tenant_id is not None and not store_ids:
            return []
        transactions = list(self._transactions.values())
        if store_ids:
            transactions = [
                transaction
                for transaction in transactions
                if transaction.store_id in store_ids
            ]
        return transactions


class DurableTransactionRepository:
    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    def save_transaction(self, transaction: Transaction) -> Transaction:
        from datetime import datetime
        event_time = transaction.event_time.isoformat() if isinstance(transaction.event_time, datetime) else transaction.event_time
        observation_time = transaction.observation_time.isoformat() if isinstance(transaction.observation_time, datetime) else transaction.observation_time
        payment_time = transaction.payment_time.isoformat() if isinstance(transaction.payment_time, datetime) else transaction.payment_time
        ingested_at = transaction.ingested_at.isoformat() if isinstance(transaction.ingested_at, datetime) else transaction.ingested_at

        self._engine.execute(
            "INSERT INTO transactions ("
            "  transaction_id, source_transaction_id, store_id, machine_id, member_id, "
            "  event_time, observation_time, payment_time, gross_amount, discount_amount, "
            "  net_amount, currency, payment_method, transaction_status, refund_of_transaction_id, "
            "  price_schedule_id, promotion_id, source_system, ingested_at, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            "ON CONFLICT(transaction_id) DO UPDATE SET "
            "  source_transaction_id = excluded.source_transaction_id, "
            "  store_id = excluded.store_id, "
            "  machine_id = excluded.machine_id, "
            "  member_id = excluded.member_id, "
            "  event_time = excluded.event_time, "
            "  observation_time = excluded.observation_time, "
            "  payment_time = excluded.payment_time, "
            "  gross_amount = excluded.gross_amount, "
            "  discount_amount = excluded.discount_amount, "
            "  net_amount = excluded.net_amount, "
            "  currency = excluded.currency, "
            "  payment_method = excluded.payment_method, "
            "  transaction_status = excluded.transaction_status, "
            "  refund_of_transaction_id = excluded.refund_of_transaction_id, "
            "  price_schedule_id = excluded.price_schedule_id, "
            "  promotion_id = excluded.promotion_id, "
            "  source_system = excluded.source_system, "
            "  ingested_at = excluded.ingested_at, "
            "  updated_at = CURRENT_TIMESTAMP",
            (
                transaction.transaction_id,
                transaction.source_transaction_id,
                transaction.store_id,
                transaction.machine_id,
                transaction.member_id,
                event_time,
                observation_time,
                payment_time,
                transaction.gross_amount,
                transaction.discount_amount,
                transaction.net_amount,
                transaction.currency,
                transaction.payment_method,
                transaction.transaction_status,
                transaction.refund_of_transaction_id,
                transaction.price_schedule_id,
                transaction.promotion_id,
                transaction.source_system,
                ingested_at
            )
        )
        return transaction

    def get_transaction(self, transaction_id: str) -> Transaction | None:
        from datetime import datetime
        row = self._engine.query_one("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,))
        if not row:
            return None
        return Transaction(
            transaction_id=row["transaction_id"],
            source_transaction_id=row["source_transaction_id"] or "",
            store_id=row["store_id"],
            machine_id=row["machine_id"] or None,
            member_id=row["member_id"] or None,
            event_time=datetime.fromisoformat(row["event_time"]),
            observation_time=datetime.fromisoformat(row["observation_time"]),
            payment_time=datetime.fromisoformat(row["payment_time"]) if row["payment_time"] else None,
            gross_amount=row["gross_amount"],
            discount_amount=row["discount_amount"],
            net_amount=row["net_amount"],
            currency=row["currency"],
            payment_method=row["payment_method"],
            transaction_status=row["transaction_status"],
            refund_of_transaction_id=row["refund_of_transaction_id"] or None,
            price_schedule_id=row["price_schedule_id"] or None,
            promotion_id=row["promotion_id"] or None,
            source_system=row["source_system"],
            ingested_at=datetime.fromisoformat(row["ingested_at"])
        )

    def list_transactions(
        self,
        *,
        tenant_id: str | None = None,
        store_ids: tuple[str, ...] = (),
    ) -> list[Transaction]:
        from datetime import datetime
        tenant_id = _require_tenant_scope(self._engine, tenant_id)
        clauses: list[str] = []
        params: list[Any] = []
        table_expression = "transactions AS transaction_row"
        if tenant_id is not None:
            table_expression += (
                " JOIN stores AS store_row"
                " ON store_row.store_id = transaction_row.store_id"
            )
            clauses.append("store_row.tenant_id = ?")
            params.append(tenant_id)
        _append_in_filter(
            clauses,
            params,
            "transaction_row.store_id",
            store_ids,
        )
        where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._engine.query(
            "SELECT transaction_row.* FROM "
            f"{table_expression}{where_clause} "
            "ORDER BY transaction_row.event_time, transaction_row.transaction_id",
            tuple(params),
        )
        return [
            Transaction(
                transaction_id=row["transaction_id"],
                source_transaction_id=row["source_transaction_id"] or "",
                store_id=row["store_id"],
                machine_id=row["machine_id"] or None,
                member_id=row["member_id"] or None,
                event_time=datetime.fromisoformat(row["event_time"]),
                observation_time=datetime.fromisoformat(row["observation_time"]),
                payment_time=datetime.fromisoformat(row["payment_time"]) if row["payment_time"] else None,
                gross_amount=row["gross_amount"],
                discount_amount=row["discount_amount"],
                net_amount=row["net_amount"],
                currency=row["currency"],
                payment_method=row["payment_method"],
                transaction_status=row["transaction_status"],
                refund_of_transaction_id=row["refund_of_transaction_id"] or None,
                price_schedule_id=row["price_schedule_id"] or None,
                promotion_id=row["promotion_id"] or None,
                source_system=row["source_system"],
                ingested_at=datetime.fromisoformat(row["ingested_at"])
            )
            for row in rows
        ]


@dataclass
class InMemoryMachineCycleRepository:
    _cycles: dict[str, MachineCycle] = field(default_factory=dict)

    def save_machine_cycle(self, machine_cycle: MachineCycle) -> MachineCycle:
        self._cycles[machine_cycle.cycle_id] = machine_cycle
        return machine_cycle

    def get_machine_cycle(self, cycle_id: str) -> MachineCycle | None:
        return self._cycles.get(cycle_id)

    def list_machine_cycles(self) -> list[MachineCycle]:
        return list(self._cycles.values())


class DurableMachineCycleRepository:
    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    def save_machine_cycle(self, machine_cycle: MachineCycle) -> MachineCycle:
        from datetime import datetime
        cycle_start_time = machine_cycle.cycle_start_time.isoformat() if isinstance(machine_cycle.cycle_start_time, datetime) else machine_cycle.cycle_start_time
        cycle_end_time = machine_cycle.cycle_end_time.isoformat() if isinstance(machine_cycle.cycle_end_time, datetime) else machine_cycle.cycle_end_time

        self._engine.execute(
            "INSERT INTO machine_cycles ("
            "  cycle_id, store_id, machine_id, transaction_id, cycle_start_time, "
            "  cycle_end_time, cycle_type, duration_sec, cycle_status, error_code, "
            "  created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            "ON CONFLICT(cycle_id) DO UPDATE SET "
            "  store_id = excluded.store_id, "
            "  machine_id = excluded.machine_id, "
            "  transaction_id = excluded.transaction_id, "
            "  cycle_start_time = excluded.cycle_start_time, "
            "  cycle_end_time = excluded.cycle_end_time, "
            "  cycle_type = excluded.cycle_type, "
            "  duration_sec = excluded.duration_sec, "
            "  cycle_status = excluded.cycle_status, "
            "  error_code = excluded.error_code, "
            "  updated_at = CURRENT_TIMESTAMP",
            (
                machine_cycle.cycle_id,
                machine_cycle.store_id,
                machine_cycle.machine_id,
                machine_cycle.transaction_id,
                cycle_start_time,
                cycle_end_time,
                machine_cycle.cycle_type,
                machine_cycle.duration_sec,
                machine_cycle.cycle_status,
                machine_cycle.error_code
            )
        )
        return machine_cycle

    def get_machine_cycle(self, cycle_id: str) -> MachineCycle | None:
        from datetime import datetime
        row = self._engine.query_one("SELECT * FROM machine_cycles WHERE cycle_id = ?", (cycle_id,))
        if not row:
            return None
        return MachineCycle(
            cycle_id=row["cycle_id"],
            store_id=row["store_id"],
            machine_id=row["machine_id"],
            transaction_id=row["transaction_id"] or None,
            cycle_start_time=datetime.fromisoformat(row["cycle_start_time"]),
            cycle_end_time=datetime.fromisoformat(row["cycle_end_time"]),
            cycle_type=row["cycle_type"],
            duration_sec=row["duration_sec"],
            cycle_status=row["cycle_status"],
            error_code=row["error_code"] or None
        )

    def list_machine_cycles(self) -> list[MachineCycle]:
        from datetime import datetime
        rows = self._engine.query("SELECT * FROM machine_cycles")
        return [
            MachineCycle(
                cycle_id=row["cycle_id"],
                store_id=row["store_id"],
                machine_id=row["machine_id"],
                transaction_id=row["transaction_id"] or None,
                cycle_start_time=datetime.fromisoformat(row["cycle_start_time"]),
                cycle_end_time=datetime.fromisoformat(row["cycle_end_time"]),
                cycle_type=row["cycle_type"],
                duration_sec=row["duration_sec"],
                cycle_status=row["cycle_status"],
                error_code=row["error_code"] or None
            )
            for row in rows
        ]


class DurableHeatZoneResultStore:
    """Durable mirror of ``HeatZoneResultStore`` (ODP-FLOW-002).

    Persists each HeatZone batch-score result (the ranking) keyed by ``job_id``,
    an idempotency-key -> job_id index, and a ``latest`` pointer, so the HeatZone
    map/list/detail endpoints keep returning the last ranking — and idempotent
    replays keep returning the original job — across a process restart.
    """

    _JOBS = "heatzone.jobs"
    _IDEMPOTENCY = "heatzone.idempotency"
    _META = "heatzone.meta"
    _LATEST = "latest_job_id"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def put(
        self,
        result: HeatZoneBatchScoreResult,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[HeatZoneBatchScoreResult, bool]:
        if idempotency_key:
            existing_job_id = self._store.get(self._IDEMPOTENCY, idempotency_key)
            if existing_job_id is not None:
                return self._store.get(self._JOBS, existing_job_id), False
        self._store.put(self._JOBS, result.job_id, result)
        self._store.put(self._META, self._LATEST, result.job_id)
        if idempotency_key:
            self._store.put(self._IDEMPOTENCY, idempotency_key, result.job_id)
        return result, True

    def find_by_idempotency_key(
        self, idempotency_key: str | None
    ) -> HeatZoneBatchScoreResult | None:
        if not idempotency_key:
            return None
        job_id = self._store.get(self._IDEMPOTENCY, idempotency_key)
        if job_id is None:
            return None
        return self._store.get(self._JOBS, job_id)

    def _latest(self) -> HeatZoneBatchScoreResult | None:
        job_id = self._store.get(self._META, self._LATEST)
        if job_id is None:
            return None
        return self._store.get(self._JOBS, job_id)

    def list_scores(self) -> list[dict[str, Any]]:
        latest = self._latest()
        return [] if latest is None else [score.to_dict() for score in latest.scores]

    def map_features(self) -> list[dict[str, Any]]:
        latest = self._latest()
        return [] if latest is None else [score.to_map_feature() for score in latest.scores]

    def snapshot(self, snapshot_id: str) -> HeatZoneBatchScoreResult | None:
        if snapshot_id == "latest":
            return self._latest()
        return self._store.get(self._JOBS, snapshot_id)


class DurableListingRepository:
    """Durable mirror of ``InMemoryListingRepository`` (ODP-FLOW-002).

    Dedup keys (source and property) live in their own collection so
    ``has_duplicate`` still rejects a re-imported listing after a restart, and
    converted candidate sites persist so the SiteScore inbox survives one too.
    """

    _LISTINGS = "listing.listings"
    _ADDRESSES = "listing.addresses"
    _CANDIDATES = "listing.candidates"
    _DEDUP = "listing.dedup_keys"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def has_duplicate(self, key: ListingDedupKey) -> bool:
        return (
            self._store.get(self._DEDUP, key.source_key) is not None
            or self._store.get(self._DEDUP, key.property_key) is not None
        )

    def save_listing(
        self, listing: Listing, address: AddressLocation, key: ListingDedupKey
    ) -> None:
        self._store.put(self._LISTINGS, listing.listing_id, listing)
        self._store.put(self._ADDRESSES, address.address_id, address)
        self._store.put(self._DEDUP, key.source_key, True)
        self._store.put(self._DEDUP, key.property_key, True)

    def save_candidate(self, candidate: CandidateSiteDraft) -> None:
        self._store.put(
            self._CANDIDATES, candidate.candidate_site.candidate_site_id, candidate
        )

    def list_candidates(self) -> list[CandidateSiteDraft]:
        return self._store.list_all(self._CANDIDATES)

    def list_listings(self) -> list[Listing]:
        return self._store.list_all(self._LISTINGS)

    def get_listing(self, listing_id: str) -> Listing | None:
        return self._store.get(self._LISTINGS, listing_id)

    def get_address(self, address_id: str) -> AddressLocation | None:
        return self._store.get(self._ADDRESSES, address_id)

    def clear(self) -> None:
        for collection in (self._LISTINGS, self._ADDRESSES, self._CANDIDATES, self._DEDUP):
            self._store.delete_collection(collection)


class DurableDecisionStore:
    """Durable mirror of ``InMemoryDecisionStore`` (ODP-FLOW-002).

    Persists each open/terminal SiteScore decision and the frozen source report
    it was opened against, so ``/sitescore/decisions/{id}`` and an approval's
    realization inputs resolve correctly after a restart.
    """

    _DECISIONS = "sitescore.decisions"
    _REPORTS = "sitescore.decision_reports"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save_decision(self, decision: SiteScoreDecision) -> None:
        self._store.put(self._DECISIONS, decision.decision_id, decision)

    def save_report(self, decision_id: str, report: SiteScoreReport) -> None:
        self._store.put(self._REPORTS, decision_id, report)

    def get_decision(self, decision_id: str) -> SiteScoreDecision | None:
        return self._store.get(self._DECISIONS, decision_id)

    def get_report(self, decision_id: str) -> SiteScoreReport | None:
        return self._store.get(self._REPORTS, decision_id)

    def list_decisions(self) -> list[SiteScoreDecision]:
        return self._store.list_all(self._DECISIONS)


class DurableRealizedSiteStore:
    """Durable mirror of ``InMemoryRealizedSiteStore`` (ODP-FLOW-002).

    Keyed by ``candidate_site_id`` so a realized approval keeps appearing in
    ``/sitescore/realized`` after a restart, matching the durable audit trail.
    """

    _C = "sitescore.realized"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def put(self, site: RealizedSite) -> None:
        self._store.put(self._C, site.candidate_site_id, site)

    def get(self, candidate_site_id: str) -> RealizedSite | None:
        return self._store.get(self._C, candidate_site_id)

    def list_realized(self) -> list[RealizedSite]:
        return self._store.list_all(self._C)


__all__ = [
    "DurableAVMRepository",
    "DurableAdLiftRepository",
    "DurableArtifactStore",
    "DurableDecisionStore",
    "DurableForecastOpsRepository",
    "DurableHeatZoneResultStore",
    "DurableInterventionRepository",
    "DurableLabelRegistry",
    "DurableLearningHubRepository",
    "DurableListingRepository",
    "DurableNetPlanRepository",
    "DurablePriceOpsRepository",
    "DurableRealizedSiteStore",
    "DurableSiteScoreRepository",
    "InMemoryTenantRepository",
    "DurableTenantRepository",
    "InMemoryBrandRepository",
    "DurableBrandRepository",
    "InMemoryAddressLocationRepository",
    "DurableAddressLocationRepository",
    "InMemoryStoreRepository",
    "DurableStoreRepository",
    "InMemoryMachineRepository",
    "DurableMachineRepository",
    "InMemoryTransactionRepository",
    "DurableTransactionRepository",
    "InMemoryMachineCycleRepository",
    "DurableMachineCycleRepository",
]
