"""Durable, SQLite-backed implementations of the product module repositories.

Each class mirrors the public surface of its ``InMemory*`` counterpart exactly
(same method names, same return values, same versioning semantics) so it is a
drop-in replacement behind the existing repository interfaces — domain and
application tests stay compatible. State lives in ``durable_documents`` via
:class:`SqliteDocumentStore`, so writes survive a process restart.
"""

from __future__ import annotations

from collections.abc import Mapping
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
from modules.intervention.domain.lifecycle import Intervention, LabelRecord
from modules.learninghub.domain import DatasetSnapshot
from modules.learninghub.infrastructure.repositories import ReleaseDecisionRecord
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
from shared.infrastructure.persistence.document_store import SqliteDocumentStore


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
            self._store.replace_latest_in_group(
                self._REPORTS, report, group_key=report.case_id
            )
        return report

    def latest_report(self, case_id: str) -> ValuationReport | None:
        return self._store.latest_in_group(self._REPORTS, case_id)

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
        self._store.put(self._ALERTS, alert.alert_id, alert)
        return alert

    def list_alerts(self) -> list[Alert]:
        return self._store.list_all(self._ALERTS)

    def save_handoff(self, handoff: InterventionHandoff) -> InterventionHandoff:
        self._store.put(self._HANDOFFS, handoff.handoff_id, handoff)
        return handoff

    def list_handoffs(self) -> list[InterventionHandoff]:
        return self._store.list_all(self._HANDOFFS)

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

    def save_canonical_forecast(
        self, forecast: CanonicalForecastOutput
    ) -> CanonicalForecastOutput:
        self._store.put(self._CANONICAL_FORECASTS, forecast.forecast_output_id, forecast)
        return forecast

    def get_canonical_forecast(
        self, forecast_output_id: str
    ) -> CanonicalForecastOutput | None:
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
        self._store.put(
            self._C, label.intervention_id, label, group_key=label.store_id
        )

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

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

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
        self._store.put(
            self._VALIDATIONS, validation_run.validation_run_id, validation_run
        )
        return validation_run

    def get_validation_run(self, validation_run_id: str) -> ValidationRun | None:
        return self._store.get(self._VALIDATIONS, validation_run_id)

    # -- aliases ----------------------------------------------------------

    def _alias_doc_id(self, model_name: str, alias: ModelAlias) -> str:
        return f"{model_name}:{alias.value}"

    def _get_alias_pointer(self, model_name: str, alias: ModelAlias) -> str | None:
        return self._store.get(self._ALIASES, self._alias_doc_id(model_name, alias))

    def _set_alias_pointer(
        self, model_name: str, alias: ModelAlias, version: str | None
    ) -> None:
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

    def save_release_decision(
        self, decision: ReleaseDecisionRecord
    ) -> ReleaseDecisionRecord:
        self._store.put(self._RELEASES, decision.release_id, decision)
        return decision

    def get_release_decision(self, release_id: str) -> object | None:
        return self._store.get(self._RELEASES, release_id)

    def list_release_decisions(self) -> list[object]:
        return self._store.list_all(self._RELEASES)


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
        self._store.put(
            self._RECORDS, record.artifact_id, record, group_key=model_name
        )
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

    def list_artifacts_for_version(
        self, model_name: str, version: str
    ) -> list[ArtifactRecord]:
        return [
            record
            for record in self.list_artifacts(model_name)
            if record.version == version
        ]

    def verify(self, artifact_id: str) -> bool:
        record = self.get_artifact(artifact_id)
        if record is None:
            return False
        blob = self._store.get(self._BLOBS, record.content_digest)
        if blob is None:
            return False
        return compute_content_digest(blob) == record.content_digest


__all__ = [
    "DurableAVMRepository",
    "DurableAdLiftRepository",
    "DurableArtifactStore",
    "DurableForecastOpsRepository",
    "DurableInterventionRepository",
    "DurableLabelRegistry",
    "DurableLearningHubRepository",
    "DurableNetPlanRepository",
    "DurablePriceOpsRepository",
    "DurableSiteScoreRepository",
]
