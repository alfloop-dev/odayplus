"""Synthetic local proofs of the inference/commit boundary, not model admission."""

from datetime import timedelta
from pathlib import Path
from threading import Event

import pytest

from modules.sitescore.application.intake_score_worker import _JobResultRepository
from modules.sitescore.application.reporting import SiteScoreReportService
from modules.sitescore.tests.test_sitescore_production_runtime import (
    NOW,
    RecordingRuntime,
    _feature,
    _repository,
)
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore


def test_inference_has_no_persistence_before_authorized_commit(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path / "prepared.sqlite3")
    runtime = RecordingRuntime()
    service = SiteScoreReportService(
        repository=repository, model_runtime=runtime, runtime_mode="production",
    )
    try:
        execution = service.infer_candidates(
            [_feature()], prediction_origin_time=NOW, scored_at=NOW,
        )
        assert len(runtime.calls) == 1
        assert execution.reports[0].m12.p50 == 106_531.25
        assert engine.query_one("SELECT count(*) AS n FROM durable_documents")["n"] == 0

        with engine.transaction():
            saved = service.persist_execution(
                execution, prediction_origin_time=NOW,
                prediction_run_id="synthetic-committed-prediction-run",
            )
        assert repository.latest(_feature()["candidate_site_id"]) == saved.reports[0]
        run = repository.get_prediction_run("synthetic-committed-prediction-run")
        assert run is not None and run.model_version_id == runtime.binding.model_id
        assert len(repository.get_predictions(run.prediction_run_id)) == 1
    finally:
        engine.close()


def test_report_commit_rolls_back_with_caller_transaction(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path / "rollback.sqlite3")
    service = SiteScoreReportService(
        repository=repository, model_runtime=RecordingRuntime(), runtime_mode="production",
    )
    try:
        execution = service.infer_candidates([_feature()], prediction_origin_time=NOW)
        with pytest.raises(RuntimeError, match="synthetic receipt failure"):
            with engine.transaction():
                service.persist_execution(execution, prediction_origin_time=NOW)
                raise RuntimeError("synthetic receipt failure")
        assert engine.query_one("SELECT count(*) AS n FROM durable_documents")["n"] == 0
    finally:
        engine.close()


def test_prepared_inference_keeps_production_runtime_fail_closed(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path / "missing-runtime.sqlite3")
    service = SiteScoreReportService(repository=repository, runtime_mode="production")
    try:
        with pytest.raises(RuntimeError, match="runtime was not composed"):
            service.infer_candidates([_feature()], prediction_origin_time=NOW)
        assert engine.query_one("SELECT count(*) AS n FROM durable_documents")["n"] == 0
    finally:
        engine.close()


def test_job_owned_result_replay_has_stable_ids_and_no_duplicate_lineage(tmp_path: Path) -> None:
    database = tmp_path / "job-result.sqlite3"
    job_id = "e6b6b70f-a81a-46d4-ab9f-e1ae797425b1"
    engine, base = _repository(database)
    repository = _JobResultRepository(
        TenantScopedDocumentStore(base._store, "synthetic-tenant"),
        job_id, _feature()["candidate_site_id"],
    )
    service = SiteScoreReportService(
        repository=repository, model_runtime=RecordingRuntime(), runtime_mode="production",
    )
    feature = {**_feature(), "feature_snapshot_time": (NOW - timedelta(days=1)).isoformat()}
    try:
        execution = service.infer_candidates(
            [feature], prediction_origin_time=NOW, scored_at=NOW,
        )
        with engine.transaction():
            first = service.persist_execution(
                execution, prediction_origin_time=NOW,
                prediction_run_id=repository.prediction_run_id,
            )
        first_report = first.reports[0]
        run = repository.get_prediction_run(repository.prediction_run_id)
        assert run.feature_snapshot_time == NOW - timedelta(days=1)
        assert run.prediction_origin_time == NOW
    finally:
        engine.close()

    reopened_engine, base = _repository(database)
    reopened = _JobResultRepository(
        TenantScopedDocumentStore(base._store, "synthetic-tenant"),
        job_id, _feature()["candidate_site_id"],
    )
    replay_service = SiteScoreReportService(
        repository=reopened, model_runtime=RecordingRuntime(), runtime_mode="production",
    )
    try:
        repeated = replay_service.infer_candidates(
            [feature], prediction_origin_time=NOW, scored_at=NOW,
        )
        with reopened_engine.transaction():
            replayed = replay_service.persist_execution(
                repeated, prediction_origin_time=NOW,
                prediction_run_id=reopened.prediction_run_id,
            )
        assert replayed.reports[0] == first_report
        assert len(reopened.history(_feature()["candidate_site_id"])) == 1
        assert len(reopened.get_predictions(reopened.prediction_run_id)) == 1
        assert reopened_engine.query_one("SELECT count(*) AS n FROM durable_documents")["n"] == 4

        other_tenant = _JobResultRepository(
            TenantScopedDocumentStore(base._store, "other-synthetic-tenant"),
            job_id, _feature()["candidate_site_id"],
        )
        assert other_tenant.report_id != reopened.report_id
        assert other_tenant.latest(_feature()["candidate_site_id"]) is None
    finally:
        reopened_engine.close()


@pytest.mark.parametrize("raises", [False, True], ids=["uncommitted-return", "storage-error"])
def test_runtime_never_finishes_only_the_delivery_for_canonical_score_jobs(raises: bool) -> None:
    from apps.worker.oday_worker.main import ODayWorker
    from modules.listing.application.intake_score_delivery import INTAKE_SITESCORE_JOB_TYPE
    from shared.infrastructure.persistence.factory import build_persistence
    from shared.jobs.queue import JobRequest, JobStatus
    from shared.jobs.registry import JobRegistry

    bundle = build_persistence()
    registry = JobRegistry()

    def unfinished_handler(_job, _persistence):
        if raises:
            raise RuntimeError("synthetic receipt storage failure")

    registry.register(INTAKE_SITESCORE_JOB_TYPE, unfinished_handler)
    job, _ = bundle.job_queue.enqueue(
        JobRequest(job_type=INTAKE_SITESCORE_JOB_TYPE, payload={}),
        correlation_id="synthetic-worker-ownership-check",
    )
    assert ODayWorker(persistence=bundle, registry=registry).run_once()
    retained = bundle.job_queue.get(job.job_id)
    assert retained.status == JobStatus.RUNNING
    assert retained.version == job.version + 1
    assert retained.attempts == 1
    assert "_retry_count" not in retained.payload


def test_runtime_routes_score_heartbeats_through_canonical_lease_adapter(monkeypatch) -> None:
    from apps.worker.oday_worker.main import ODayWorker
    from modules.listing.application import intake_score_delivery
    from shared.infrastructure.persistence.factory import build_persistence
    from shared.jobs.queue import JobRequest, JobStatus
    from shared.jobs.registry import JobRegistry

    bundle = build_persistence()
    heartbeat_seen = Event()
    calls = []

    class LeaseProbe:
        def __init__(self, persistence):
            assert persistence is bundle

        def heartbeat(self, job_id, *, expected_version, fence_token):
            calls.append((job_id, expected_version, fence_token))
            version = bundle.job_queue.heartbeat(
                job_id, expected_version=expected_version, fence_token=fence_token,
            )
            heartbeat_seen.set()
            return version

    monkeypatch.setattr(intake_score_delivery, "IntakeScoreJobRepository", LeaseProbe)
    registry = JobRegistry()

    def wait_for_heartbeat(_job, _persistence):
        assert heartbeat_seen.wait(timeout=5)

    registry.register(intake_score_delivery.INTAKE_SITESCORE_JOB_TYPE, wait_for_heartbeat)
    job, _ = bundle.job_queue.enqueue(
        JobRequest(job_type=intake_score_delivery.INTAKE_SITESCORE_JOB_TYPE, payload={}),
        correlation_id="synthetic-canonical-heartbeat-adapter",
    )
    assert ODayWorker(
        persistence=bundle, registry=registry, heartbeat_interval_seconds=0.01,
    ).run_once()
    retained = bundle.job_queue.get(job.job_id)
    assert calls[0] == (job.job_id, job.version + 1, retained.fence_token)
    assert retained.status == JobStatus.RUNNING
    assert retained.version >= job.version + 2
