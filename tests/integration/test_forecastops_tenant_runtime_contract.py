from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from threading import Event, Lock
from time import sleep
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import apps.api.app.routes.forecastops as forecastops_routes
import modules.forecastops.workers as forecastops_workers
from apps.api.app.routes.forecastops import (
    ForecastOpsForecastJobPayload,
    ForecastOpsJobStore,
    create_forecastops_router,
)
from apps.api.oday_api.main import (
    JobCreatePayload,
    create_app,
    production_feature_schema_versions,
)
from apps.worker.oday_worker.handlers import handle_forecast
from apps.worker.oday_worker.main import ODayWorker
from models.shared_ml import (
    MlflowProductionModelRuntime,
    ModelAlias,
    ModelBinding,
    ModelInferenceResult,
    ModelStage,
    ModelVersion,
)
from modules.forecastops import (
    ForecastInput,
    ForecastOpsError,
    ForecastOpsService,
    StoreDayObservation,
)
from modules.forecastops.infrastructure import InMemoryForecastOpsRepository
from modules.forecastops.model_contract import FORECASTOPS_FEATURE_SCHEMA_ID
from modules.forecastops.workers import ForecastOpsBatchResult
from shared.auth import Principal, Role, Scope
from shared.infrastructure.persistence.factory import build_persistence
from shared.jobs import (
    InMemoryJobQueue,
    JobRecord,
    JobRequest,
    JobStatus,
    NonRetryableJobError,
)

TENANT_ID = "tenant-forecast-live"
OTHER_TENANT_ID = "tenant-forecast-other"
FORECAST_HEADERS = {
    "x-subject-id": "forecast-operator",
    "x-roles": "operations_manager",
    "x-tenant-id": TENANT_ID,
}


def _forecast_input(*, tenant_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "store_id": "store-forecast-001",
        "observations": [
            {
                "store_id": "store-forecast-001",
                "business_date": "2026-07-01",
                "actual_revenue": 100_000,
                "source_snapshot_ids": ["pos-20260701"],
            }
        ],
    }
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    return payload


def _live_forecast_input(
    *,
    tenant_id: str = TENANT_ID,
    store_id: str = "store-forecast-001",
) -> ForecastInput:
    prediction_origin = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
    start = prediction_origin.date() - timedelta(days=35)
    return ForecastInput(
        tenant_id=tenant_id,
        store_id=store_id,
        observations=tuple(
            StoreDayObservation(
                store_id=store_id,
                business_date=start + timedelta(days=index),
                actual_revenue=90_000.0 + index * 500.0,
                data_quality_score=0.99,
                source_snapshot_ids=(f"snapshot-{index:03d}",),
            )
            for index in range(35)
        ),
        prediction_origin_time=prediction_origin,
    )


def _declining_forecast_input(
    *,
    tenant_id: str = TENANT_ID,
    store_id: str = "store-forecast-red",
) -> ForecastInput:
    """A store far below its SiteScore baseline, so a red alert and handoff open."""

    prediction_origin = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
    start = prediction_origin.date() - timedelta(days=35)
    return ForecastInput(
        tenant_id=tenant_id,
        store_id=store_id,
        observations=tuple(
            StoreDayObservation(
                store_id=store_id,
                business_date=start + timedelta(days=index),
                actual_revenue=200_000.0 - index * 4_000.0,
                site_score_baseline_p50=250_000.0,
                data_quality_score=0.99,
                source_snapshot_ids=(f"snapshot-red-{index:03d}",),
            )
            for index in range(35)
        ),
        prediction_origin_time=prediction_origin,
    )


def _live_forecast_mapping() -> dict[str, Any]:
    item = _live_forecast_input()
    return {
        "tenant_id": item.tenant_id,
        "store_id": item.store_id,
        "prediction_origin_time": item.prediction_origin_time.isoformat(),
        "observations": [
            {
                "store_id": observation.store_id,
                "business_date": observation.business_date.isoformat(),
                "actual_revenue": observation.actual_revenue,
                "data_quality_score": observation.data_quality_score,
                "source_snapshot_ids": list(observation.source_snapshot_ids),
            }
            for observation in item.observations
        ],
    }


class _DeterministicRegisteredRuntime:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def infer(
        self,
        *,
        service: str,
        rows: list[dict[str, Any]],
        expected_feature_schema_version: str,
    ) -> ModelInferenceResult:
        self.rows.extend(dict(row) for row in rows)
        binding = ModelBinding.from_model_version(
            service,
            ModelVersion(
                model_name="forecast-revenue-lgbm",
                version="2026.07.26",
                artifact_uri="file:///models/forecast-revenue-lgbm.zip",
                dataset_snapshot_id="forecast-training-live",
                feature_schema_version=expected_feature_schema_version,
                label_version="forecast-horizon-average-revenue-v1",
                metrics={},
                stage=ModelStage.PRODUCTION,
                aliases=frozenset({ModelAlias.PRODUCTION}),
                run_id="forecast-runtime-parity",
                git_sha="c72804b8",
                approved_by="forecast-reviewer",
                approved_at=datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
            ),
            artifact_sha256="sha256:" + "a" * 64,
            engine="lightgbm.LGBMRegressor",
            mlflow_run_id="mlflow-forecast-runtime-parity",
        )
        points = tuple(100_000.0 + int(row["horizon_weeks"]) * 1_000.0 for row in rows)
        return ModelInferenceResult(
            binding=binding,
            point=points,
            lower=tuple(point * 0.9 for point in points),
            upper=tuple(point * 1.1 for point in points),
            engine="lightgbm.LGBMRegressor",
            artifact_sha256="sha256:" + "a" * 64,
        )


def _focused_forecast_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    captured_inputs: list[dict[str, Any]],
) -> Any:
    def fake_run_forecast(
        *,
        inputs: list[dict[str, Any]],
        **_: Any,
    ) -> ForecastOpsBatchResult:
        captured_inputs.extend(inputs)
        return ForecastOpsBatchResult(
            job_id="forecast-job-001",
            status="succeeded",
            result={"forecasts": [], "count": 0},
            completed_at=datetime.now(UTC),
        )

    monkeypatch.setattr(
        forecastops_routes,
        "run_forecastops_batch_forecast",
        fake_run_forecast,
    )
    router = create_forecastops_router(
        repository=InMemoryForecastOpsRepository(),
        job_store=ForecastOpsJobStore(),
        require_production_model=False,
        require_durable_jobs=False,
        runtime_mode="local",
    )
    return next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/forecastops/forecast-jobs"
    )


def _request(*, tenant_id: str | None = TENANT_ID) -> Request:
    principal = Principal(
        subject_id="forecast-operator",
        roles=frozenset({Role.OPERATIONS_MANAGER}),
        scope=Scope(tenant_id=tenant_id),
    )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/forecastops/forecast-jobs",
            "headers": [],
            "state": {
                "correlation_id": "corr-forecast-tenant",
                "operator_principal": principal,
            },
        }
    )


def _find_endpoint(router: Any, name: str) -> Any:
    for route in getattr(router, "routes", ()):
        endpoint = getattr(route, "endpoint", None)
        if getattr(endpoint, "__name__", None) == name:
            return endpoint
        nested = getattr(route, "original_router", None)
        if nested is not None:
            try:
                return _find_endpoint(nested, name)
            except LookupError:
                pass
    raise LookupError(name)


def _request_with_headers(headers: dict[str, str], *, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in headers.items()
            ],
            "state": {"correlation_id": "corr-forecast-enqueue"},
        }
    )


def test_api_readiness_uses_canonical_forecast_training_schema() -> None:
    assert (
        production_feature_schema_versions()["forecastops"]
        == FORECASTOPS_FEATURE_SCHEMA_ID
        == "forecast-training-view-v2"
    )


def test_forecast_api_injects_authenticated_tenant_into_runtime_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_inputs: list[dict[str, Any]] = []
    endpoint = _focused_forecast_endpoint(monkeypatch, captured_inputs)

    response = endpoint(
        ForecastOpsForecastJobPayload(inputs=[_forecast_input()]),
        _request(),
        None,
    )

    assert response["status"] == "succeeded"
    assert captured_inputs[0]["tenant_id"] == TENANT_ID


def test_forecast_api_fails_closed_without_tenant_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _focused_forecast_endpoint(monkeypatch, [])

    with pytest.raises(HTTPException) as exc_info:
        endpoint(
            ForecastOpsForecastJobPayload(inputs=[_forecast_input()]),
            _request(tenant_id=None),
            None,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "TENANT_SCOPE_REQUIRED"


def test_forecast_api_rejects_payload_tenant_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _focused_forecast_endpoint(monkeypatch, [])

    with pytest.raises(HTTPException) as exc_info:
        endpoint(
            ForecastOpsForecastJobPayload(inputs=[_forecast_input(tenant_id="tenant-other")]),
            _request(),
            None,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "TENANT_SCOPE_MISMATCH"


def test_forecast_job_enqueue_binds_authenticated_tenant() -> None:
    queue = InMemoryJobQueue()
    app = create_app(job_queue=queue)
    endpoint = _find_endpoint(app, "enqueue_job")

    response = endpoint(
        JobCreatePayload(
            job_type="forecast",
            payload={"store_id": "store-forecast-001"},
        ),
        _request_with_headers(FORECAST_HEADERS, path="/jobs"),
        None,
    )

    assert response["job"]["payload"]["tenant_id"] == TENANT_ID


def test_forecast_job_enqueue_fails_closed_without_tenant_scope() -> None:
    app = create_app(job_queue=InMemoryJobQueue())
    endpoint = _find_endpoint(app, "enqueue_job")
    headers = {key: value for key, value in FORECAST_HEADERS.items() if key != "x-tenant-id"}

    with pytest.raises(HTTPException) as exc_info:
        endpoint(
            JobCreatePayload(
                job_type="forecast",
                payload={"store_id": "store-forecast-001"},
            ),
            _request_with_headers(headers, path="/jobs"),
            None,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "TENANT_SCOPE_REQUIRED"


def test_generic_forecast_job_query_is_tenant_scoped() -> None:
    queue = InMemoryJobQueue()
    app = create_app(job_queue=queue)
    enqueue_endpoint = _find_endpoint(app, "enqueue_job")
    get_endpoint = _find_endpoint(app, "get_job")
    created = enqueue_endpoint(
        JobCreatePayload(
            job_type="forecast",
            payload={"store_id": "store-forecast-001"},
        ),
        _request_with_headers(FORECAST_HEADERS, path="/jobs"),
        None,
    )

    own_receipt = get_endpoint(
        created["job_id"],
        _request_with_headers(FORECAST_HEADERS, path=f"/jobs/{created['job_id']}"),
    )
    assert own_receipt["payload"]["tenant_id"] == TENANT_ID

    tenant_b_headers = {**FORECAST_HEADERS, "x-tenant-id": OTHER_TENANT_ID}
    with pytest.raises(HTTPException) as exc_info:
        get_endpoint(
            created["job_id"],
            _request_with_headers(tenant_b_headers, path=f"/jobs/{created['job_id']}"),
        )
    assert exc_info.value.status_code == 404


def test_generic_forecast_job_query_rejects_unscoped_stored_receipt() -> None:
    queue = InMemoryJobQueue()
    job, created = queue.enqueue(
        JobRequest(
            job_type="forecast",
            payload={"store_id": "store-forecast-001"},
        ),
        correlation_id="corr-unscoped-receipt",
    )
    assert created is True
    endpoint = _find_endpoint(create_app(job_queue=queue), "get_job")

    with pytest.raises(HTTPException) as exc_info:
        endpoint(
            job.job_id,
            _request_with_headers(FORECAST_HEADERS, path=f"/jobs/{job.job_id}"),
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "JOB_TENANT_SCOPE_MISSING"


def test_worker_injects_job_tenant_into_forecast_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = build_persistence()
    start = date(2026, 5, 1)
    ForecastOpsService(repository=bundle.forecastops_repository).ingest_timeseries(
        (
            StoreDayObservation(
                store_id="store-worker-001",
                business_date=start + timedelta(days=index),
                actual_revenue=80_000 + index * 100,
                source_snapshot_ids=(f"snapshot-{index}",),
            )
            for index in range(28)
        ),
        tenant_id=TENANT_ID,
    )
    captured: list[Any] = []

    def fake_run_forecast(*, inputs, **_: Any) -> None:
        captured.extend(inputs)

    monkeypatch.setattr(
        forecastops_workers,
        "run_forecastops_batch_forecast",
        fake_run_forecast,
    )
    handle_forecast(
        JobRecord(
            job_type="forecast",
            payload={"store_id": "store-worker-001", "tenant_id": TENANT_ID},
            correlation_id="corr-worker-tenant",
        ),
        bundle,
    )

    assert captured[0].tenant_id == TENANT_ID
    assert captured[0].prediction_origin_time == datetime(
        2026, 5, 29, tzinfo=UTC
    )


def test_worker_fails_permanently_when_tenant_scope_is_missing() -> None:
    job = JobRecord(
        job_type="forecast",
        payload={"store_id": "store-worker-001"},
        correlation_id="corr-worker-missing-tenant",
    )

    with pytest.raises(NonRetryableJobError, match="authenticated tenant scope"):
        handle_forecast(job, SimpleNamespace(forecastops_repository=None))


def test_worker_does_not_retry_forecast_job_without_tenant_scope() -> None:
    bundle = build_persistence()
    job, created = bundle.job_queue.enqueue(
        JobRequest(
            job_type="forecast",
            payload={"store_id": "store-worker-001"},
        ),
        correlation_id="corr-worker-no-tenant",
    )
    assert created is True

    assert ODayWorker(persistence=bundle).run_once() is True

    failed = bundle.job_queue.get(job.job_id)
    assert failed is not None
    assert failed.status == JobStatus.FAILED
    assert failed.payload.get("_retry_count") is None
    assert failed.error_message == "Forecast job payload missing authenticated tenant scope"


def test_repository_series_outputs_and_queries_are_tenant_scoped() -> None:
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(repository=repository)
    tenant_a_input = _live_forecast_input(tenant_id=TENANT_ID)
    tenant_b_input = _live_forecast_input(tenant_id=OTHER_TENANT_ID)

    service.ingest_timeseries(
        tenant_a_input.observations,
        tenant_id=TENANT_ID,
    )
    service.ingest_timeseries(
        tenant_b_input.observations,
        tenant_id=OTHER_TENANT_ID,
    )
    tenant_a = service.forecast([tenant_a_input])
    tenant_b = service.forecast([tenant_b_input])

    assert repository.get_series(TENANT_ID, tenant_a_input.store_id).tenant_id == TENANT_ID
    assert (
        repository.get_series(OTHER_TENANT_ID, tenant_b_input.store_id).tenant_id == OTHER_TENANT_ID
    )
    assert repository.latest_forecasts(TENANT_ID) == [tenant_a.forecasts[0]]
    assert repository.latest_forecasts(OTHER_TENANT_ID) == [tenant_b.forecasts[0]]
    assert (
        repository.get_prediction_run(
            OTHER_TENANT_ID,
            tenant_a.forecasts[0].prediction_run_id,
        )
        is None
    )
    assert (
        repository.get_canonical_forecast(
            OTHER_TENANT_ID,
            tenant_a.forecasts[0].forecast_output_id,
        )
        is None
    )


def test_repository_fails_closed_without_tenant_scope() -> None:
    repository = InMemoryForecastOpsRepository()

    with pytest.raises(ValueError, match="tenant_id is required"):
        repository.list_series("")
    with pytest.raises(ValueError, match="tenant_id is required"):
        repository.get_predictions("", "prediction-run")
    with pytest.raises(ValueError, match="tenant_id is required"):
        repository.get_canonical_forecast("", "forecast-output")


def test_forecast_batch_rejects_mixed_tenants() -> None:
    service = ForecastOpsService()

    with pytest.raises(ForecastOpsError, match="exactly one authenticated tenant"):
        service.forecast(
            [
                _live_forecast_input(tenant_id=TENANT_ID),
                _live_forecast_input(tenant_id=OTHER_TENANT_ID),
            ]
        )


def test_worker_cannot_read_another_tenants_series() -> None:
    bundle = build_persistence()
    item = _live_forecast_input(tenant_id=TENANT_ID)
    ForecastOpsService(repository=bundle.forecastops_repository).ingest_timeseries(
        item.observations,
        tenant_id=TENANT_ID,
    )

    with pytest.raises(ValueError, match=OTHER_TENANT_ID):
        handle_forecast(
            JobRecord(
                job_type="forecast",
                payload={
                    "store_id": item.store_id,
                    "tenant_id": OTHER_TENANT_ID,
                },
                correlation_id="corr-worker-cross-tenant",
            ),
            bundle,
        )


def test_process_local_job_idempotency_is_tenant_scoped_and_atomic() -> None:
    store = ForecastOpsJobStore()

    def put(tenant_id: str, job_id: str) -> tuple[ForecastOpsBatchResult, bool]:
        return store.put(
            tenant_id,
            ForecastOpsBatchResult(
                job_id=job_id,
                status="succeeded",
                result={"forecasts": []},
                completed_at=datetime.now(UTC),
            ),
            idempotency_key="same-client-key",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        tenant_a_results = list(
            executor.map(
                lambda index: put(TENANT_ID, f"tenant-a-job-{index}"),
                range(8),
            )
        )
    tenant_b, tenant_b_created = put(OTHER_TENANT_ID, "tenant-b-job")

    assert sum(created for _, created in tenant_a_results) == 1
    assert len({result.job_id for result, _ in tenant_a_results}) == 1
    assert tenant_b_created is True
    assert tenant_b.job_id == "tenant-b-job"


def test_concurrent_api_idempotency_reserves_before_forecast_side_effects(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = build_persistence(
        mode="durable",
        db_path=tmp_path / "forecast-command-receipts.sqlite3",
    )
    repository = bundle.forecastops_repository
    original_run = forecastops_routes.run_forecastops_batch_forecast
    started = Event()
    release = Event()
    calls_lock = Lock()
    calls = 0

    def gated_run(**kwargs: Any) -> ForecastOpsBatchResult:
        nonlocal calls
        with calls_lock:
            calls += 1
        started.set()
        assert release.wait(timeout=5)
        return original_run(**kwargs)

    monkeypatch.setattr(
        forecastops_routes,
        "run_forecastops_batch_forecast",
        gated_run,
    )
    router = create_forecastops_router(
        repository=repository,
        job_queue=bundle.job_queue,
        require_production_model=False,
        require_durable_jobs=True,
        runtime_mode="local",
    )
    endpoint = next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/forecastops/forecast-jobs"
    )
    body = ForecastOpsForecastJobPayload(
        inputs=[_live_forecast_mapping()],
        prediction_origin_time=_live_forecast_input().prediction_origin_time.isoformat(),
        idempotency_key="concurrent-forecast-command",
    )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(endpoint, body, _request(), None)
            assert started.wait(timeout=5)
            second = executor.submit(endpoint, body, _request(), None)
            sleep(0.05)
            release.set()
            responses = (first.result(timeout=5), second.result(timeout=5))

        assert calls == 1
        assert {response["created"] for response in responses} == {True, False}
        assert len({response["job_id"] for response in responses}) == 1
        assert len(repository.history(TENANT_ID, "store-forecast-001")) == 1
        prediction_run_id = responses[0]["forecasts"][0]["prediction_run_id"]
        assert len(repository.get_predictions(TENANT_ID, prediction_run_id)) == 1
        assert len(repository.list_alerts(TENANT_ID)) == 1
    finally:
        bundle.engine.close()


def test_worker_replay_after_prediction_write_does_not_duplicate_domain_records(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = build_persistence(
        mode="durable",
        db_path=tmp_path / "forecast-worker-crash.sqlite3",
    )
    repository = bundle.forecastops_repository
    item = _live_forecast_input()
    ForecastOpsService(repository=repository).ingest_timeseries(
        item.observations,
        tenant_id=TENANT_ID,
    )

    class CrashOnceAfterPredictionWrite:
        def __init__(self, wrapped: Any) -> None:
            self.wrapped = wrapped
            self.crashed = False

        def __getattr__(self, name: str) -> Any:
            return getattr(self.wrapped, name)

        def save_prediction(self, tenant_id: str, prediction: Any) -> Any:
            saved = self.wrapped.save_prediction(tenant_id, prediction)
            if not self.crashed:
                self.crashed = True
                raise RuntimeError("simulated crash after prediction write")
            return saved

    flaky = CrashOnceAfterPredictionWrite(repository)
    job = JobRecord(
        job_id="forecast-worker-crash-replay",
        job_type="forecast",
        payload={
            "store_id": item.store_id,
            "tenant_id": TENANT_ID,
            "prediction_origin_time": item.prediction_origin_time.isoformat(),
        },
        correlation_id="corr-forecast-worker-crash",
        created_at=item.prediction_origin_time,
    )
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")

    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            handle_forecast(
                job,
                SimpleNamespace(forecastops_repository=flaky),
            )
        handle_forecast(
            job,
            SimpleNamespace(forecastops_repository=flaky),
        )

        run_id = f"pred-run-forecast-{job.job_id}"
        assert len(repository.history(TENANT_ID, item.store_id)) == 1
        assert repository.get_prediction_run(TENANT_ID, run_id) is not None
        assert len(repository.get_predictions(TENANT_ID, run_id)) == 1
        assert len(repository.list_alerts(TENANT_ID)) == 1
    finally:
        bundle.engine.close()


def test_generic_forecast_queue_idempotency_is_tenant_scoped() -> None:
    queue = InMemoryJobQueue()
    endpoint = _find_endpoint(create_app(job_queue=queue), "enqueue_job")

    first = endpoint(
        JobCreatePayload(
            job_type="forecast",
            payload={"store_id": "store-shared"},
            idempotency_key="same-client-key",
        ),
        _request_with_headers(FORECAST_HEADERS, path="/jobs"),
        None,
    )
    second_headers = {
        **FORECAST_HEADERS,
        "x-tenant-id": OTHER_TENANT_ID,
    }
    second = endpoint(
        JobCreatePayload(
            job_type="forecast",
            payload={"store_id": "store-shared"},
            idempotency_key="same-client-key",
        ),
        _request_with_headers(second_headers, path="/jobs"),
        None,
    )

    assert first["created"] is True
    assert second["created"] is True
    assert first["job_id"] != second["job_id"]


def test_concurrent_durable_forecast_versions_are_unique_per_tenant(tmp_path) -> None:
    bundle = build_persistence(
        mode="durable",
        db_path=tmp_path / "forecast-concurrency.sqlite3",
    )
    repository = bundle.forecastops_repository
    service = ForecastOpsService(repository=repository)
    item = _live_forecast_input()

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: service.forecast([item]), range(12)))

        versions = sorted(result.forecasts[0].forecast_version for result in results)
        assert versions == list(range(1, 13))
        assert len(repository.history(TENANT_ID, item.store_id)) == 12
    finally:
        bundle.engine.close()


def test_prediction_provenance_uses_selected_horizon_and_separate_origin() -> None:
    repository = InMemoryForecastOpsRepository()
    item = replace(_live_forecast_input(), horizon_days=168)
    result = ForecastOpsService(repository=repository).forecast(
        [item],
        prediction_run_id="pred-run-provenance",
        scored_at=item.prediction_origin_time,
    )
    forecast = result.forecasts[0]
    run = repository.get_prediction_run(TENANT_ID, forecast.prediction_run_id)

    assert forecast.horizon_days == 168
    assert (forecast.p10, forecast.p50, forecast.p90) == (
        forecast.w24.p10,
        forecast.w24.p50,
        forecast.w24.p90,
    )
    assert run is not None
    assert run.prediction_horizon == "w24"
    assert run.prediction_origin_time == item.prediction_origin_time
    assert run.feature_snapshot_time == datetime.combine(
        item.observations[-1].business_date + timedelta(days=1),
        datetime.min.time(),
        tzinfo=UTC,
    )
    assert run.feature_snapshot_time < run.prediction_origin_time


def test_api_and_production_worker_use_registered_runtime_with_identical_horizons(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_runtime = _DeterministicRegisteredRuntime()
    router = create_forecastops_router(
        repository=InMemoryForecastOpsRepository(),
        job_store=ForecastOpsJobStore(),
        model_runtime=api_runtime,
        require_production_model=True,
        require_durable_jobs=False,
        runtime_mode="local",
    )
    endpoint = next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/forecastops/forecast-jobs"
    )
    api_payload = endpoint(
        ForecastOpsForecastJobPayload(inputs=[_live_forecast_mapping()]),
        _request(),
        None,
    )

    bundle = build_persistence(
        mode="durable",
        db_path=tmp_path / "forecast-worker-parity.sqlite3",
    )
    worker_runtime = _DeterministicRegisteredRuntime()
    item = _live_forecast_input()
    ForecastOpsService(repository=bundle.forecastops_repository).ingest_timeseries(
        item.observations,
        tenant_id=TENANT_ID,
    )
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setattr(
        MlflowProductionModelRuntime,
        "from_environment",
        classmethod(lambda cls, **_: worker_runtime),
    )
    try:
        handle_forecast(
            JobRecord(
                job_type="forecast",
                payload={
                    "store_id": item.store_id,
                    "tenant_id": TENANT_ID,
                    "prediction_origin_time": item.prediction_origin_time.isoformat(),
                },
                correlation_id="corr-worker-runtime-parity",
            ),
            bundle,
        )
        worker_output = bundle.forecastops_repository.latest_forecasts(TENANT_ID)[0]
    finally:
        bundle.engine.close()

    api_output = api_payload["forecasts"][0]
    assert api_output["engine_name"] == "lightgbm.LGBMRegressor"
    assert worker_output.engine_name == "lightgbm.LGBMRegressor"
    assert [row["horizon_weeks"] for row in api_runtime.rows] == [4, 8, 12, 24]
    assert [row["horizon_weeks"] for row in worker_runtime.rows] == [4, 8, 12, 24]
    assert [api_output[f"w{horizon}"]["p50"] for horizon in (4, 8, 12, 24)] == [
        getattr(worker_output, f"w{horizon}").p50 for horizon in (4, 8, 12, 24)
    ]


def test_worker_replay_preserves_alert_acknowledgement_and_handoff_execution(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An at-least-once redelivery must not rewind operator lifecycle state."""

    bundle = build_persistence(
        mode="durable",
        db_path=tmp_path / "forecast-replay-lifecycle.sqlite3",
    )
    repository = bundle.forecastops_repository
    item = _declining_forecast_input()
    service = ForecastOpsService(repository=repository)
    service.ingest_timeseries(item.observations, tenant_id=TENANT_ID)
    job = JobRecord(
        job_id="forecast-replay-lifecycle",
        job_type="forecast",
        payload={
            "store_id": item.store_id,
            "tenant_id": TENANT_ID,
            "prediction_origin_time": item.prediction_origin_time.isoformat(),
        },
        correlation_id="corr-forecast-replay-lifecycle",
        created_at=item.prediction_origin_time,
    )
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")

    try:
        handle_forecast(job, SimpleNamespace(forecastops_repository=repository))
        alert = repository.list_alerts(TENANT_ID)[0]
        handoff = repository.list_handoffs(TENANT_ID)[0]
        assert alert.status == "open"
        assert handoff.status == "proposed"

        service.acknowledge_alert(TENANT_ID, alert.alert_id, actor="ops-manager", note="triaged")
        service.execute_handoff(
            TENANT_ID,
            handoff.handoff_id,
            actor="ops-dispatcher",
            intervention_id="intervention-replay-001",
        )

        handle_forecast(job, SimpleNamespace(forecastops_repository=repository))

        replayed_alert = repository.get_alert(TENANT_ID, alert.alert_id)
        replayed_handoff = repository.get_handoff(TENANT_ID, handoff.handoff_id)
        assert replayed_alert is not None
        assert replayed_alert.status == "acknowledged"
        assert replayed_alert.acknowledged_by == "ops-manager"
        assert replayed_alert.acknowledged_at is not None
        assert replayed_handoff is not None
        assert replayed_handoff.status == "dispatched"
        assert replayed_handoff.executed_by == "ops-dispatcher"
        assert replayed_handoff.intervention_id == "intervention-replay-001"
        assert len(repository.list_alerts(TENANT_ID)) == 1
        assert len(repository.list_handoffs(TENANT_ID)) == 1
        assert len(repository.history(TENANT_ID, item.store_id)) == 1
    finally:
        bundle.engine.close()


def test_batch_scores_every_requested_horizon_for_one_store() -> None:
    """Two horizons for one store must not collapse into a single forecast."""

    repository = InMemoryForecastOpsRepository()
    item = _live_forecast_input()
    result = ForecastOpsService(repository=repository).forecast(
        [replace(item, horizon_days=28), replace(item, horizon_days=168)],
        prediction_run_id="pred-run-multi-horizon",
        scored_at=item.prediction_origin_time,
    )

    assert [forecast.horizon_days for forecast in result.forecasts] == [28, 168]
    assert len({forecast.forecast_output_id for forecast in result.forecasts}) == 2
    week4, week24 = result.forecasts
    assert (week4.p10, week4.p50, week4.p90) == (week4.w4.p10, week4.w4.p50, week4.w4.p90)
    assert (week24.p10, week24.p50, week24.p90) == (
        week24.w24.p10,
        week24.w24.p50,
        week24.w24.p90,
    )
    assert len(repository.history(TENANT_ID, item.store_id)) == 2
    predictions = repository.get_predictions(TENANT_ID, "pred-run-multi-horizon")
    assert len(predictions) == 2
    assert len({prediction.prediction_id for prediction in predictions}) == 2
