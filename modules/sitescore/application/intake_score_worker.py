"""Execute an admitted Intake score job, then commit under its current fence.

The delivery repository owns authorization and SQL checkpoint transactions.
External inference never runs while those locks are held. This handler cannot
turn a local heuristic or an unavailable model into a production score.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, replace
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid5

from models.shared_ml.production_runtime import (
    MlflowProductionModelRuntime,
    ProductionModelApprovalError,
    ProductionModelInputError,
    ProductionModelLineageError,
    ProductionModelRuntimeError,
)
from modules.listing.application.intake_score_delivery import ScoreDeliveryDenied
from modules.sitescore.application.reporting import SiteScoreReportService
from modules.sitescore.domain.scoring import SiteScoreReport
from shared.domain import Prediction, PredictionRun, SiteScoreRun
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurableSiteScoreRepository
from shared.jobs.queue import JobFenceRejectedError, JobRecord, NonRetryableJobError

if TYPE_CHECKING:
    from models.shared_ml.production_runtime import ProductionModelRuntime
    from shared.infrastructure.persistence.factory import PersistenceBundle


class _JobResultRepository(DurableSiteScoreRepository):
    """Immutable per-job identities inside the caller's fenced transaction.

    A retry may recompute an uncommitted result, but it cannot append another
    report or overwrite a previously committed result for the same job.
    """

    def __init__(self, store: TenantScopedDocumentStore, job_id: str, candidate_id: str):
        super().__init__(store)
        self._namespace = UUID(job_id)
        self._candidate_id = candidate_id
        self.report_id = self._id("report")
        self.sitescore_run_id = self._id("sitescore-run")
        self.prediction_run_id = self._id("prediction-run")
        self._feature_snapshot_time = None

    def _id(self, kind: str) -> str:
        return str(uuid5(self._namespace, f"{self.tenant_id}:{self._candidate_id}:{kind}"))

    def _put_once(self, collection: str, key: str, value: Any, **index: Any) -> None:
        if not self._store.put_if_absent(collection, key, value, **index):
            if self._store.get(collection, key) != value:
                raise ValueError("SITESCORE_JOB_RESULT_CONFLICT")

    def save_report(self, report: SiteScoreReport) -> SiteScoreReport:
        with self.report_guard(report.candidate_site_id):
            return self._save_report_locked(report)

    def _save_report_locked(self, report: SiteScoreReport) -> SiteScoreReport:
        if report.candidate_site_id != self._candidate_id:
            raise ValueError("SITESCORE_RESULT_CANDIDATE_MISMATCH")
        existing = self.get_report(self.report_id)
        version = (existing.report_version if existing is not None
                   else self._store.count_in_group(self._C, self._candidate_id) + 1)
        saved = replace(
            report, report_id=self.report_id, report_version=version,
            sitescore_run_id=self.sitescore_run_id,
        )
        self._put_once(
            self._C, self.report_id, saved,
            group_key=self._candidate_id, seq=version,
        )
        self._feature_snapshot_time = saved.feature_snapshot_time
        return saved

    def save_prediction_run(self, run: PredictionRun) -> PredictionRun:
        if run.prediction_run_id != self.prediction_run_id:
            raise ValueError("SITESCORE_RESULT_RUN_MISMATCH")
        if self._feature_snapshot_time is None:
            raise ValueError("SITESCORE_RESULT_FEATURE_TIME_MISSING")
        run = replace(run, feature_snapshot_time=self._feature_snapshot_time)
        self._put_once(self._PREDICTION_RUNS, run.prediction_run_id, run)
        return run

    def save_prediction(self, prediction: Prediction) -> Prediction:
        if (prediction.prediction_run_id != self.prediction_run_id
                or prediction.entity_type != "candidate_site"
                or prediction.entity_id != self._candidate_id):
            raise ValueError("SITESCORE_RESULT_PREDICTION_MISMATCH")
        saved = replace(prediction, prediction_id=self._id(f"prediction:{prediction.target_name}"))
        self._put_once(
            self._PREDICTIONS, saved.prediction_id, saved,
            group_key=self.prediction_run_id, seq=1,
        )
        return saved

    def save_sitescore_run(self, run: SiteScoreRun) -> SiteScoreRun:
        if (run.candidate_site_id != self._candidate_id
                or run.sitescore_run_id != self.sitescore_run_id
                or run.prediction_run_id != self.prediction_run_id):
            raise ValueError("SITESCORE_RESULT_RUN_MISMATCH")
        self._put_once(self._SITESCORE_RUNS, run.sitescore_run_id, run)
        return run


def handle_intake_score(
    job: JobRecord,
    persistence: PersistenceBundle,
    *,
    model_runtime: ProductionModelRuntime | None = None,
    _execution_delivery: Any = None,
    model_admission: Any = None,
) -> None:
    """Registry handler; explicit runtime injection is for local integration proofs."""
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository

    fresh = _execution_delivery is not None
    delivery = _execution_delivery or IntakeScoreJobRepository(persistence)
    attempt = delivery.begin_attempt(job)
    if attempt is None:
        return

    canonical = persistence.assisted_intake_store
    engine = persistence.engine
    if (canonical is None or canonical._engine is not engine
            or getattr(persistence.job_queue, "_engine", None) is not engine):
        raise NonRetryableJobError("SITESCORE_TRANSACTION_BINDING_UNAVAILABLE")
    store = TenantScopedDocumentStore(canonical._store, attempt.tenant_id)
    if store.engine is not engine:
        raise NonRetryableJobError("SITESCORE_TRANSACTION_BINDING_UNAVAILABLE")
    reports = _JobResultRepository(store, job.job_id, attempt.candidate_site_id)
    listing_repository = persistence.listing_repository_for_tenant(attempt.tenant_id)
    if (listing_repository is None
            or getattr(getattr(listing_repository, "_store", None), "engine", None) is not engine):
        raise NonRetryableJobError("SITESCORE_TRANSACTION_BINDING_UNAVAILABLE")

    production_authority = delivery.model_admission if fresh else model_admission
    pinned_model = None
    try:
        runtime = model_runtime if model_runtime is not None else MlflowProductionModelRuntime.from_environment()
        if not fresh and production_authority is None and (model_runtime is None or isinstance(runtime, MlflowProductionModelRuntime)):
            from models.shared_ml.current_model_admission import CurrentModelAdmissionPort
            production_authority = CurrentModelAdmissionPort(runtime=runtime,
                registry_repository=persistence.learninghub_repository, engine=engine)
        if production_authority is not None:
            if getattr(production_authority, "runtime", None) is None:
                production_authority.runtime = runtime
            if fresh:
                pinned_model = delivery.model_receipt(attempt)
            else:
                from modules.sitescore.domain.scoring import SITESCORE_FEATURE_VERSION
                pinned_model = production_authority.read(service="sitescore", expected_feature_schema_version=SITESCORE_FEATURE_VERSION)
                delivery.pin_model_admission(job, attempt, pinned_model)
            from modules.sitescore.domain.scoring import SITESCORE_FEATURE_VERSION
            if production_authority.read(service="sitescore", expected_feature_schema_version=SITESCORE_FEATURE_VERSION) != pinned_model:
                raise ProductionModelApprovalError("SITESCORE_CURRENT_MODEL_CHANGED")
        service = SiteScoreReportService(
            repository=reports, model_runtime=runtime,
            require_production_model=True, runtime_mode="production",
        )
        execution = service.infer_candidates(
            attempt.features, prediction_origin_time=attempt.prediction_origin_time,
            scored_at=attempt.scored_at,
        )
        if (len(execution.reports) != 1
                or execution.reports[0].candidate_site_id != attempt.candidate_site_id
                or execution.model_inference is None):
            raise ValueError("SITESCORE_RESULT_CANDIDATE_MISMATCH")
        if production_authority is not None:
            production_authority.verify_inference(pinned_model, execution.model_inference)
            if production_authority.read(service="sitescore", expected_feature_schema_version=SITESCORE_FEATURE_VERSION) != pinned_model:
                raise ProductionModelApprovalError("SITESCORE_CURRENT_MODEL_CHANGED")
    except JobFenceRejectedError:
        raise
    except (ProductionModelRuntimeError, ScoreDeliveryDenied, ValueError, TimeoutError, ConnectionError) as exc:
        permanent = isinstance(exc, (
            ProductionModelApprovalError, ProductionModelLineageError,
            ProductionModelInputError, ScoreDeliveryDenied, ValueError,
        ))
        delivery.fail_attempt(
            job, attempt, error_code=getattr(exc, "code", "SITESCORE_INFERENCE_UNAVAILABLE"),
            retryable=not permanent,
        )
        return

    def persist_records() -> dict[str, Any]:
        # The bridge invokes this only after its fresh parent/job/fence checks,
        # and rolls every report, candidate, audit and receipt write back together.
        saved = service.persist_execution(
            execution, prediction_origin_time=attempt.prediction_origin_time,
            prediction_run_id=reports.prediction_run_id,
        )
        report = saved.reports[0]
        candidate = next((
            item for item in listing_repository.list_candidates()
            if item.candidate_site.candidate_site_id == attempt.candidate_site_id
        ), None)
        if candidate is None:
            raise ValueError("SITESCORE_CANDIDATE_UNAVAILABLE")
        candidate = deepcopy(candidate)
        original_site = candidate.candidate_site
        candidate.candidate_site = replace(original_site, site_status=original_site.site_status if fresh else "scored")
        formal = {field.name for field in fields(original_site)}
        for name, value in vars(original_site).items():
            if name not in formal:
                object.__setattr__(candidate.candidate_site, name, deepcopy(value))
        # The report defines confidence and financial outcomes, not a scalar
        # suitability score. Never relabel confidence as a business score.
        candidate.score = None
        candidate.recommendation = report.recommendation.value
        candidate.model_version = report.model_version
        listing_repository.save_candidate(candidate)
        return {
            "report_id": report.report_id,
            "report_version": report.report_version,
            "sitescore_run_id": report.sitescore_run_id,
            "prediction_run_id": reports.prediction_run_id,
            "model_version": report.model_version,
            "model_lineage": execution.model_inference.to_audit_metadata(),
        }

    def persist() -> dict[str, Any]:
        if production_authority is None or fresh:
            return persist_records()
        with reports.report_guard(attempt.candidate_site_id):
            with production_authority.guard(pinned_model, tenant_id=attempt.tenant_id):
                delivery.check_publication(job, attempt)
                return persist_records()

    if not fresh:
        try:
            delivery.commit_success(job, attempt, persist)
        except ProductionModelRuntimeError as exc:
            delivery.fail_attempt(job, attempt, error_code=getattr(exc, "code", str(exc)), retryable=False)
        return
    try:
        delivery.commit_success(job, attempt, persist)
    except JobFenceRejectedError:
        if not delivery.settle_cancelled_attempt(job, attempt):
            raise
    except (ProductionModelRuntimeError, ScoreDeliveryDenied, ValueError) as exc:
        delivery.fail_attempt(job, attempt, error_code=getattr(exc, "code", str(exc)), retryable=False)


def handle_score_execution(job: JobRecord, persistence: PersistenceBundle, *,
                           model_runtime: ProductionModelRuntime | None = None,
                           model_admission: Any = None) -> None:
    from modules.sitescore.application.score_delivery import ScoreExecutionJobRepository
    if model_admission is None:
        model_admission = getattr(persistence, "current_model_admission", None)
    if model_runtime is None:
        model_runtime = getattr(model_admission, "runtime", None)
        if model_runtime is None:
            try:
                model_runtime = MlflowProductionModelRuntime.from_environment()
            except ProductionModelRuntimeError:
                # Missing runtime enters the authenticated owner's admission
                # failure/settlement; never generic worker success or inference.
                model_runtime = None
    if model_admission is None:
        from models.shared_ml.current_model_admission import CurrentModelAdmissionPort
        model_admission = CurrentModelAdmissionPort(runtime=model_runtime,
            registry_repository=persistence.learninghub_repository, engine=persistence.engine)
    if getattr(model_admission, "runtime", None) is None and model_runtime is not None:
        model_admission.runtime = model_runtime
    delivery = ScoreExecutionJobRepository(persistence, model_admission=model_admission)
    handle_intake_score(job, persistence, model_runtime=model_runtime, _execution_delivery=delivery)


__all__ = ["handle_intake_score", "handle_score_execution"]
