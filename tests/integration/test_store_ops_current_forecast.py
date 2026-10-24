"""Actual signed HTTP and owned SQLite storage, not real PG/model/UAT proof.

The original ForecastOps writer and registered-engine adapter save all joins.
Its explicitly synthetic inference result exercises recorded binding fields;
it is not an artifact load, business approval or production inference claim.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.store_ops import create_operator_store_ops_router
from apps.api.oday_api.security import dependencies
from models.shared_ml.production_runtime import ModelInferenceResult
from models.shared_ml.scoring_binding import ModelBinding
from modules.forecastops.application.forecasting import ForecastOpsService
from modules.forecastops.application.production_model import RegisteredEstimatorForecastEngine
from modules.forecastops.domain.forecasting import (
    ForecastBand,
    ForecastInput,
    StoreDayObservation,
    default_forecast_alert_policy,
)
from modules.opsboard.application.store_ops import DurableStoreOpsRepository
from modules.opsboard.application.store_ops_materializer import materialize_store_ops_state
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary
from shared.api.errors import install_error_handlers
from shared.domain.models import Store
from shared.governance import InMemoryDecisionPolicyRepository
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.repositories import (
    DurableForecastOpsRepository,
    InMemoryStoreRepository,
)
from tests.security.test_feature_flag_admin_authorization import ISSUER, SIGNING_KEY, _headers

TENANT, STORE, OTHER = "forecast-read-tenant", "forecast-read-store", "forecast-other-store"
BASE = "/api/v1/operator/store-ops"
ORIGIN = datetime(2026, 10, 15, tzinfo=UTC)


class AdvisoryObservedSqlite(SqliteEngine):
    """Original route's advisory admission recorded on a real SQLite transaction.

    This declared dialect adapter is engineering coverage only. No PostgreSQL
    lock or isolation claim is inferred from these controls.
    """

    dialect = "postgresql"

    def execute(self, statement, params=()):
        if statement == "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))":
            return super().execute("SELECT 1")
        return super().execute(statement, params)

    def query_one(self, statement, params=()):
        if statement == "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))":
            return super().query_one("SELECT 1")
        return super().query_one(statement, params)


class SyntheticRecordedInference:
    calls = 0

    def infer(self, **_kwargs):
        self.calls += 1
        return ModelInferenceResult(
            binding=ModelBinding(
                service="forecastops", model_name="engineering-recorded-model", version="1",
                model_id="engineering-model-id", stage="production", aliases=("champion",),
                dataset_snapshot_id="engineering-dataset", feature_schema_version="forecast-training-view-v2",
                label_version="engineering-label", git_sha=None, resolved_at=ORIGIN,
                artifact_uri="file:///not-loaded-engineering-artifact", artifact_sha256="a" * 64,
                source_run_id="engineering-source-run", mlflow_run_id=None,
                approved_by=None, approved_at=None, engine="engineering-inference-port",
            ),
            lower=(10.0, 20.0, 30.0, 40.0), point=(15.0, 25.0, 35.0, 45.0),
            upper=(20.0, 30.0, 40.0, 50.0), engine="engineering-inference-port",
            artifact_sha256="a" * 64, registry_version="engineering-registry-version",
        )


@dataclass
class Case:
    client: TestClient
    engine: SqliteEngine
    documents: SqliteDocumentStore
    forecasts: DurableForecastOpsRepository
    stores: InMemoryStoreRepository
    state: DurableStoreOpsRepository
    audit: DurableAuditLog
    inference: SyntheticRecordedInference
    output: object
    alert: object
    issue_id: str
    mappings: dict
    app: FastAPI
    forecast_input: ForecastInput


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("ODP_DEPLOY_ENV", "test")
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")
    engine = AdvisoryObservedSqlite(tmp_path / "owned-forecast-read.sqlite")
    try:
        documents = SqliteDocumentStore(engine)
        forecasts = DurableForecastOpsRepository(documents)
        inference = SyntheticRecordedInference()
        producer = ForecastOpsService(
            repository=forecasts, runtime_mode="poc",
            engine=RegisteredEstimatorForecastEngine(inference),
            policy_repository=InMemoryDecisionPolicyRepository([default_forecast_alert_policy(TENANT)]),
        )
        inputs = ForecastInput(
            store_id=STORE, tenant_id=TENANT, prediction_origin_time=ORIGIN,
            observations=tuple(StoreDayObservation(
                store_id=STORE, business_date=(ORIGIN - timedelta(days=60 - index)).date(),
                actual_revenue=0.0, site_score_baseline_p50=100.0, data_quality_score=1.0,
                source_snapshot_ids=(f"engineering-pos-{index}",),
            ) for index in range(60)),
        )
        produced = producer.forecast([inputs], prediction_run_id="engineering-current-run", scored_at=ORIGIN)
        output, alert = produced.forecasts[0], produced.alerts[0]
        stores = InMemoryStoreRepository()
        stores.save_store(Store(store_id=STORE, tenant_id=TENANT, brand_id="forecast-brand", region_code="north"))
        stores.save_store(Store(store_id=OTHER, tenant_id=TENANT, brand_id="forecast-brand", region_code="south"))
        raw_state = DurableStoreOpsRepository(documents)
        materialize_store_ops_state(
            repository=raw_state, store_repository=stores, forecastops_repository=forecasts,
            tenant_id=TENANT,
        )
        audit = DurableAuditLog(engine)
        mappings = {name: {"roles": ["operations_manager"], "scope": {
            "tenant_id": tenant, "store_ids": [store], "brand_ids": [brand],
            "region_ids": [region], "modules": modules, "clearance": "confidential",
        }} for name, tenant, store, brand, region, modules in (
            ("ops", TENANT, STORE, "forecast-brand", "north", ["operator_console"]),
            ("foreign", "forecast-foreign-tenant", STORE, "forecast-brand", "north", ["operator_console"]),
            ("outside-store", TENANT, OTHER, "forecast-brand", "south", ["operator_console"]),
            ("outside-brand", TENANT, STORE, "other-brand", "north", ["operator_console"]),
            ("outside-region", TENANT, STORE, "forecast-brand", "south", ["operator_console"]),
            ("outside-module", TENANT, STORE, "forecast-brand", "north", ["avm"]),
        )}
        boundary = AuthenticationBoundary(AuthBoundaryConfig(
            issuer=ISSUER, audiences=frozenset({"unit-feature-api"}),
            signing_keys={SIGNING_KEY.kid: SIGNING_KEY}, principal_mappings=mappings,
        ), audit_log=audit)
        monkeypatch.setattr(dependencies, "default_boundary", lambda: boundary)
        app = FastAPI()
        install_error_handlers(app)

        @app.middleware("http")
        async def correlation(request: Request, call_next):
            request.state.correlation_id = request.headers.get("x-correlation-id", "forecast-server-cid")
            return await call_next(request)

        app.include_router(create_operator_store_ops_router(
            repository=raw_state, store_repository=stores, forecastops_repository=forecasts,
            audit_log=audit, require_live_data=True,
        ), prefix="/api/v1")
        with TestClient(app, raise_server_exceptions=False) as client:
            yield Case(client, engine, documents, forecasts, stores, raw_state.for_tenant(TENANT),
                       audit, inference, output, alert, "ISS-FC-" + alert.alert_id, mappings, app, inputs)
    finally:
        engine.close()


def _read(case, actor="ops", issue_id=None, **query):
    return case.client.get(
        f"{BASE}/issues/{issue_id or case.issue_id}/evidence",
        headers=_headers(actor, correlation_id="forecast-read-" + actor), params=query,
    )


def _business_rows(case):
    return {
        "documents": [dict(row) for row in case.engine.query("SELECT * FROM durable_documents ORDER BY collection, doc_id")],
        "jobs": [dict(row) for row in case.engine.query("SELECT * FROM durable_jobs ORDER BY job_id")],
    }


def _assert_read_only(case, before, calls):
    assert _business_rows(case) == before
    assert case.inference.calls == calls
    assert not [event for event in case.audit.list_events(tenant_id=TENANT)
                if not event.event_type.startswith("security.")]


def test_exact_current_alert_four_bands_run_binding_and_zero_actual_are_read_without_inference(case):
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case, forecast_output_id="caller-cannot-select-a-different-output")
    assert response.status_code == 200, response.text
    body = response.json()
    forecast = body["forecast"]
    assert forecast["status"] == "recorded" and forecast["reason"] is None
    assert forecast["issueId"] == case.issue_id and forecast["storeId"] == STORE
    data = forecast["data"]
    assert [row["horizonWeeks"] for row in data["bands"]] == [4, 8, 12, 24]
    assert [row["p50"] for row in data["bands"]] == [15.0, 25.0, 35.0, 45.0]
    assert data["actualRevenue"] == 0.0 and data["siteScoreBaselineP50"] == 100.0
    assert data["forecastOutputId"] == case.output.forecast_output_id
    assert data["predictionRunId"] == case.output.prediction_run_id
    assert data["policy"]["versionId"] == case.alert.policy_version_id
    assert data["modelBinding"]["model_id"] == case.output.model_version
    assert data["modelBinding"]["artifact_sha256"] == "a" * 64
    assert data["sourceSnapshotIds"] == list(case.output.source_snapshot_ids)
    assert "artifact_uri" not in data["modelBinding"]
    assert body["issue"] == case.state.get_state()["issues"][0]
    _assert_read_only(case, before, calls)
    allowed = [event for event in case.audit.list_events(tenant_id=TENANT)
               if event.event_type == "security.authorization"]
    assert len(allowed) == 1 and allowed[0].actor == "ops" and allowed[0].outcome == "allow"


@pytest.mark.parametrize("actor,expected", [
    ("outside-store", 404), ("outside-brand", 404), ("outside-region", 404),
    ("outside-module", 403),
])
def test_current_permission_and_store_scope_are_checked_before_forecast_projection(case, actor, expected):
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case, actor)
    assert response.status_code == expected, response.text
    assert case.output.forecast_output_id not in response.text
    assert case.output.prediction_run_id not in response.text
    _assert_read_only(case, before, calls)


def test_foreign_tenant_with_same_issue_identity_cannot_borrow_raw_global_forecast(case):
    # A foreign tenant's own authorized aggregate may contain a copied source
    # reference, but it cannot confer ownership of the other tenant's forecast.
    foreign = "forecast-foreign-tenant"
    case.stores.save_store(Store(store_id=STORE, tenant_id=foreign, brand_id="forecast-brand", region_code="north"))
    foreign_state = DurableStoreOpsRepository(case.documents).for_tenant(foreign)
    foreign_state.save_state(case.state.get_state())
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case, "foreign")
    assert response.status_code == 200, response.text
    assert response.json()["forecast"]["status"] == "unavailable"
    assert response.json()["forecast"]["data"] is None
    assert case.output.prediction_run_id not in response.text
    _assert_read_only(case, before, calls)


@pytest.mark.parametrize("fault", [
    "duplicate", "missing-run", "wrong-run-model", "wrong-canonical-store", "wrong-prediction",
    "nonfinite-band", "unordered-band", "baseline", "missing-binding", "alert-evidence",
    "foreign-output", "missing-output", "naive-origin",
])
def test_malformed_ambiguous_or_unbound_source_stays_unavailable_without_latest_fallback(case, fault):
    forecast, repo = case.output, case.forecasts
    collection = repo._collection(repo._FORECASTS, TENANT)
    if fault == "duplicate":
        case.documents.put(collection, "duplicate-document-key", forecast, group_key=STORE, seq=999)
    elif fault == "missing-run":
        case.documents.delete_collection(repo._collection(repo._PREDICTION_RUNS, TENANT))
    elif fault == "wrong-run-model":
        run = repo.get_prediction_run(TENANT, forecast.prediction_run_id)
        repo.save_prediction_run(TENANT, replace(run, model_version_id="unrelated-model"))
    elif fault == "wrong-canonical-store":
        canonical = repo.get_canonical_forecast(TENANT, forecast.forecast_output_id)
        repo.save_canonical_forecast(TENANT, replace(canonical, store_id=OTHER))
    elif fault == "wrong-prediction":
        prediction = repo.get_predictions(TENANT, forecast.prediction_run_id)[0]
        case.documents.put(repo._collection(repo._PREDICTIONS, TENANT), prediction.prediction_id,
                           replace(prediction, entity_id=OTHER), group_key=forecast.prediction_run_id)
    elif fault == "alert-evidence":
        repo.save_alert(replace(case.alert, evidence_json={**case.alert.evidence_json, "forecast_p50": 999.0}))
    elif fault == "missing-output":
        case.documents.delete_collection(collection)
    else:
        changes = {
            "nonfinite-band": {"w24": ForecastBand(0.0, float("inf"), float("inf"))},
            "unordered-band": {"w24": ForecastBand(9.0, 1.0, 2.0)},
            "baseline": {"engine_name": "baseline", "model_metadata": {}},
            "missing-binding": {"model_metadata": {}},
            "foreign-output": {"tenant_id": "forecast-foreign-tenant"},
            "naive-origin": {"prediction_origin_time": ORIGIN.replace(tzinfo=None)},
        }[fault]
        case.documents.put(collection, forecast.forecast_output_id, replace(forecast, **changes),
                           group_key=STORE, seq=forecast.forecast_version)
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["forecast"]["status"] == "unavailable" and result["forecast"]["data"] is None
    assert result["issue"]["id"] == case.issue_id and result["evidence"]
    _assert_read_only(case, before, calls)


def test_later_unrelated_forecast_does_not_replace_the_exact_alert_run(case):
    later = replace(case.output, forecast_output_id="unrelated-later-output",
                    prediction_run_id="unrelated-later-run", forecast_version=999)
    case.documents.put(case.forecasts._collection(case.forecasts._FORECASTS, TENANT),
                       later.forecast_output_id, later, group_key=STORE, seq=999)
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case)
    assert response.status_code == 200, response.text
    assert response.json()["forecast"]["data"]["predictionRunId"] == case.output.prediction_run_id
    assert "unrelated-later-run" not in response.text
    _assert_read_only(case, before, calls)


def test_copied_issue_source_reference_cannot_select_an_unrelated_issue_forecast(case):
    state = case.state.get_state()
    state["issues"][0]["id"] = "unrelated-issue"
    case.state.save_state(state)
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case, issue_id="unrelated-issue")
    assert response.status_code == 200, response.text
    assert response.json()["forecast"]["status"] == "unavailable"
    assert response.json()["forecast"]["data"] is None
    _assert_read_only(case, before, calls)


def test_no_body_or_query_can_change_owner_selection_or_persist_forecast(case):
    before, calls = _business_rows(case), case.inference.calls
    response = case.client.request(
        "GET", f"{BASE}/issues/{case.issue_id}/evidence", headers=_headers("ops"),
        json={"tenant_id": "foreign", "store_id": OTHER, "forecast_output_id": "forged"},
        params={"tenant_id": "foreign", "store_id": OTHER, "prediction_run_id": "forged"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["forecast"]["data"]["predictionRunId"] == case.output.prediction_run_id
    _assert_read_only(case, before, calls)


def test_current_store_scope_is_rechecked_on_next_read_after_real_master_change(case):
    first = _read(case)
    assert first.status_code == 200 and first.json()["forecast"]["status"] == "recorded"
    case.stores.save_store(replace(case.stores.get_store(STORE), region_code="revoked-region"))
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case)
    assert response.status_code == 404, response.text
    assert case.output.prediction_run_id not in response.text
    _assert_read_only(case, before, calls)


def test_forecast_repository_cannot_use_another_engine_owner(case, tmp_path, monkeypatch):
    other = SqliteEngine(tmp_path / "other-owned-source.sqlite")
    try:
        monkeypatch.setattr(case.forecasts, "_store", SqliteDocumentStore(other))
        before, calls = _business_rows(case), case.inference.calls
        response = _read(case)
        assert response.status_code == 200, response.text
        assert response.json()["forecast"]["status"] == "unavailable"
        assert response.json()["forecast"]["data"] is None
        assert case.output.prediction_run_id not in response.text
        _assert_read_only(case, before, calls)
        assert other.query_one("SELECT COUNT(*) AS n FROM durable_documents")["n"] == 0
    finally:
        other.close()


def test_original_baseline_writer_with_valid_run_joins_is_not_a_registered_execution(case):
    producer = ForecastOpsService(
        repository=case.forecasts, engine="baseline", runtime_mode="poc",
        policy_repository=InMemoryDecisionPolicyRepository([default_forecast_alert_policy(TENANT)]),
    )
    generated = producer.forecast([case.forecast_input], prediction_run_id="engineering-baseline-run", scored_at=ORIGIN)
    alert = generated.alerts[0]
    materialize_store_ops_state(
        repository=DurableStoreOpsRepository(case.documents), store_repository=case.stores,
        forecastops_repository=case.forecasts, tenant_id=TENANT,
    )
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case, issue_id="ISS-FC-" + alert.alert_id)
    assert response.status_code == 200, response.text
    forecast = response.json()["forecast"]
    assert forecast["status"] == "unavailable" and forecast["data"] is None
    assert forecast["reason"] == "RECORDED_PRODUCTION_MODEL_BINDING_UNAVAILABLE"
    _assert_read_only(case, before, calls)


def test_missing_forecast_port_does_not_disable_the_authorized_issue_workflow(case, monkeypatch):
    monkeypatch.setattr(case.forecasts, "_store", None)
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case)
    assert response.status_code == 200, response.text
    assert response.json()["issue"]["id"] == case.issue_id
    assert response.json()["forecast"]["status"] == "unavailable"
    assert response.json()["forecast"]["data"] is None
    _assert_read_only(case, before, calls)


class DistinctBindingInference(SyntheticRecordedInference):
    def infer(self, **kwargs):
        result = super().infer(**kwargs)
        return replace(result, binding=replace(result.binding, resolved_at=ORIGIN + timedelta(seconds=self.calls)))


def _four_requested_horizons(case):
    inference = DistinctBindingInference()
    producer = ForecastOpsService(
        repository=case.forecasts, runtime_mode="poc",
        engine=RegisteredEstimatorForecastEngine(inference),
        policy_repository=InMemoryDecisionPolicyRepository([default_forecast_alert_policy(TENANT)]),
    )
    generated = producer.forecast(
        [replace(case.forecast_input, horizon_days=weeks * 7) for weeks in (4, 8, 12, 24)],
        prediction_run_id="engineering-four-requested-horizons", scored_at=ORIGIN,
    )
    materialize_store_ops_state(
        repository=DurableStoreOpsRepository(case.documents), store_repository=case.stores,
        forecastops_repository=case.forecasts, tenant_id=TENANT,
    )
    return generated


def test_all_actually_requested_horizons_join_their_own_distinct_binding_metadata(case):
    generated = _four_requested_horizons(case)
    assert len(generated.forecasts) == 4
    assert len({row.model_metadata["binding_resolved_at"] for row in generated.forecasts}) == 4
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case, issue_id="ISS-FC-" + generated.alerts[0].alert_id)
    assert response.status_code == 200, response.text
    forecast = response.json()["forecast"]
    assert forecast["status"] == "recorded"
    assert [band["p50"] for band in forecast["data"]["bands"]] == [row.p50 for row in generated.forecasts]
    _assert_read_only(case, before, calls)


@pytest.mark.parametrize("fault", [
    "prediction-value", "canonical-value", "domain-value", "missing-domain", "orphan-prediction",
])
def test_existing_unselected_horizon_contradiction_or_orphan_cannot_be_hidden_by_valid_w4(case, fault):
    generated = _four_requested_horizons(case)
    other = generated.forecasts[1]
    assert other.horizon_days == 56
    repo = case.forecasts
    canonical = repo.get_canonical_forecast(TENANT, other.forecast_output_id)
    prediction = next(row for row in repo.get_predictions(TENANT, other.prediction_run_id)
                      if row.p50_value == other.p50)
    if fault == "prediction-value":
        case.documents.put(repo._collection(repo._PREDICTIONS, TENANT), prediction.prediction_id,
                           replace(prediction, p50_value=999.0), group_key=other.prediction_run_id)
    elif fault == "canonical-value":
        repo.save_canonical_forecast(TENANT, replace(canonical, p50=999.0))
    elif fault == "domain-value":
        # Keep this counterpart internally consistent, including its actual
        # Prediction and canonical selected values, but contradict w4's w8 band.
        changed = replace(other, p10=900.0, p50=950.0, p90=999.0, w8=ForecastBand(900.0, 950.0, 999.0))
        case.documents.put(repo._collection(repo._FORECASTS, TENANT), other.forecast_output_id,
                           changed, group_key=STORE, seq=other.forecast_version)
        repo.save_canonical_forecast(TENANT, replace(canonical, p10=900.0, p50=950.0, p90=999.0))
        case.documents.put(repo._collection(repo._PREDICTIONS, TENANT), prediction.prediction_id,
                           replace(prediction, p10_value=900.0, p50_value=950.0, p90_value=999.0),
                           group_key=other.prediction_run_id)
    else:
        case.engine.execute("DELETE FROM durable_documents WHERE collection = ? AND doc_id = ?",
                            (repo._collection(repo._FORECASTS, TENANT), other.forecast_output_id))
        if fault == "orphan-prediction":
            case.engine.execute("DELETE FROM durable_documents WHERE collection = ? AND doc_id = ?",
                                (repo._collection(repo._CANONICAL_FORECASTS, TENANT), other.forecast_output_id))
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case, issue_id="ISS-FC-" + generated.alerts[0].alert_id)
    assert response.status_code == 200, response.text
    assert response.json()["forecast"]["status"] == "unavailable"
    assert response.json()["forecast"]["data"] is None
    _assert_read_only(case, before, calls)


# Saved-policy controls use the real registered adapter and original producer.
# The inference port remains explicitly synthetic engineering data, not an
# artifact load or production-model/approval claim.
def _produce_saved_guard(case, *, maximum, action="SUPPRESS_HIGH_CONFIDENCE", stage="production", guard_present=True):
    from copy import deepcopy

    original = default_forecast_alert_policy(TENANT)
    parameters = deepcopy(dict(original.parameters))
    if guard_present:
        parameters["data_quality_guard"] = {"max_staleness_days": maximum, "on_violation": action}
    else:
        parameters.pop("data_quality_guard", None)
    label = f"engineering-quality-{maximum}-{action.lower()}-{stage}-{guard_present}"
    policy = replace(original, policy_label=label, policy_version_id=f"{label}:{TENANT}", parameters=parameters)

    class RecordedStageInference(SyntheticRecordedInference):
        def infer(self, **kwargs):
            inferred = super().infer(**kwargs)
            return replace(inferred, binding=replace(inferred.binding, stage=stage))

    inference = RecordedStageInference()
    producer = ForecastOpsService(
        repository=case.forecasts, runtime_mode="poc",
        engine=RegisteredEstimatorForecastEngine(inference),
        policy_repository=InMemoryDecisionPolicyRepository([policy]),
    )
    generated = producer.forecast([case.forecast_input], prediction_run_id=label, scored_at=ORIGIN)
    materialize_store_ops_state(
        repository=DurableStoreOpsRepository(case.documents), store_repository=case.stores,
        forecastops_repository=case.forecasts, tenant_id=TENANT,
    )
    return generated.forecasts[0], generated.alerts[0]


@pytest.mark.parametrize("maximum,action,violated,actual_level", [
    (2, "SUPPRESS_HIGH_CONFIDENCE", False, "red"),
    (0, "SUPPRESS_HIGH_CONFIDENCE", True, "orange"),
])
def test_registered_adapter_original_saved_guard_is_read_at_execution_not_recomputed(case, maximum, action, violated, actual_level):
    output, alert = _produce_saved_guard(case, maximum=maximum, action=action)
    # Latest source is origin-1, as required by the actual registered adapter.
    # A policy max of zero exercises a real saved violation without stale input.
    assert output.data_staleness_days == 1
    assert alert.evidence_json["data_quality_guard"]["violated"] is violated
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case, issue_id="ISS-FC-" + alert.alert_id)
    assert response.status_code == 200, response.text
    data = response.json()["forecast"]["data"]
    saved = data["qualityAtRun"]
    assert saved["state"] == "recorded" and saved["reason"] is None
    assert saved["data"] == {
        "alertId": alert.alert_id, "forecastOutputId": output.forecast_output_id,
        "predictionRunId": output.prediction_run_id,
        "policy": {"id": alert.policy_id, "version": alert.policy_version, "versionId": alert.policy_version_id},
        "evaluatedAt": alert.opened_at.isoformat(), "featureSnapshotTime": data["featureSnapshotTime"],
        "maxStalenessDays": maximum, "stalenessDays": 1, "violated": violated,
        "onViolation": action, "actualAlertLevel": actual_level,
        "thresholdAlertLevel": "red", "alertReasonCode": alert.alert_reason_code,
    }
    run = case.forecasts.get_prediction_run(TENANT, output.prediction_run_id)
    assert data["featureSnapshotTime"] == run.feature_snapshot_time.isoformat()
    _assert_read_only(case, before, calls)


@pytest.mark.parametrize("fault", [
    "missing", "empty", "string-boolean", "wrong-policy-limit", "wrong-policy-action", "wrong-staleness",
])
def test_unverified_saved_guard_is_unknown_while_original_bands_remain_available(case, fault):
    from copy import deepcopy

    evidence = deepcopy(case.alert.evidence_json)
    if fault == "missing":
        evidence.pop("data_quality_guard")
    elif fault == "empty":
        evidence["data_quality_guard"] = {}
    elif fault == "string-boolean":
        evidence["data_quality_guard"]["violated"] = "false"
    elif fault == "wrong-policy-limit":
        evidence["policy_parameters"]["data_quality_guard"]["max_staleness_days"] = 7
    elif fault == "wrong-policy-action":
        evidence["data_quality_guard"]["on_violation"] = "SUPPRESS_ALERT"
    else:
        evidence["data_quality_guard"]["staleness_days"] = 99
    case.forecasts.save_alert(replace(case.alert, evidence_json=evidence))
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case)
    assert response.status_code == 200, response.text
    forecast = response.json()["forecast"]
    assert forecast["status"] == "recorded" and len(forecast["data"]["bands"]) == 4
    assert forecast["data"]["qualityAtRun"] == {
        "state": "unavailable", "reason": "RECORDED_QUALITY_GUARD_UNAVAILABLE", "data": None,
    }
    _assert_read_only(case, before, calls)


def test_saved_guard_from_shadow_binding_without_policy_guard_stays_unknown_not_false(case):
    output, alert = _produce_saved_guard(case, maximum=0, stage="shadow", guard_present=False)
    assert alert.evidence_json["data_quality_guard"] == {}
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case, issue_id="ISS-FC-" + alert.alert_id)
    assert response.status_code == 200, response.text
    data = response.json()["forecast"]["data"]
    assert data["modelBinding"]["model_stage"] == "shadow"
    assert data["forecastOutputId"] == output.forecast_output_id
    assert data["qualityAtRun"]["state"] == "unavailable"
    assert data["qualityAtRun"]["data"] is None
    _assert_read_only(case, before, calls)


def test_each_saved_policy_retains_its_own_guard_after_another_policy_produces_a_new_alert(case):
    first_output, first_alert = _produce_saved_guard(case, maximum=0)
    second_output, second_alert = _produce_saved_guard(case, maximum=7)
    before, calls = _business_rows(case), case.inference.calls
    first = _read(case, issue_id="ISS-FC-" + first_alert.alert_id)
    second = _read(case, issue_id="ISS-FC-" + second_alert.alert_id)
    assert first.status_code == second.status_code == 200
    first_data, second_data = first.json()["forecast"]["data"], second.json()["forecast"]["data"]
    assert first_data["forecastOutputId"] == first_output.forecast_output_id
    assert second_data["forecastOutputId"] == second_output.forecast_output_id
    assert first_data["qualityAtRun"]["data"]["maxStalenessDays"] == 0
    assert first_data["qualityAtRun"]["data"]["violated"] is True
    assert second_data["qualityAtRun"]["data"]["maxStalenessDays"] == 7
    assert second_data["qualityAtRun"]["data"]["violated"] is False
    _assert_read_only(case, before, calls)


@pytest.mark.parametrize("fault", ["contradictory-boolean", "contradictory-level", "contradictory-reason"])
def test_saved_guard_contradiction_is_unknown_not_a_replacement_decision(case, fault):
    from copy import deepcopy

    from modules.forecastops.domain.forecasting import AlertLevel

    _output, alert = _produce_saved_guard(case, maximum=0)
    assert alert.evidence_json["data_quality_guard"]["violated"] is True
    assert alert.alert_level is AlertLevel.ORANGE
    if fault == "contradictory-boolean":
        evidence = deepcopy(alert.evidence_json)
        evidence["data_quality_guard"]["violated"] = False
        changed = replace(alert, evidence_json=evidence)
    elif fault == "contradictory-level":
        changed = replace(alert, alert_level=AlertLevel.RED)
    else:
        changed = replace(alert, alert_reason_code="sitescore_gap")
    case.forecasts.save_alert(changed)
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case, issue_id="ISS-FC-" + alert.alert_id)
    assert response.status_code == 200, response.text
    data = response.json()["forecast"]["data"]
    assert len(data["bands"]) == 4
    assert data["qualityAtRun"] == {
        "state": "unavailable", "reason": "RECORDED_QUALITY_GUARD_UNAVAILABLE", "data": None,
    }
    _assert_read_only(case, before, calls)


@pytest.mark.parametrize("threshold", [{"untrusted": "red"}, ["red"]])
def test_malformed_saved_threshold_container_is_unknown_not_an_optional_projection_exception(case, threshold):
    from copy import deepcopy

    evidence = deepcopy(case.alert.evidence_json)
    evidence["threshold_alert_level"] = threshold
    case.forecasts.save_alert(replace(case.alert, evidence_json=evidence))
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case)
    assert response.status_code == 200, response.text
    data = response.json()["forecast"]["data"]
    assert len(data["bands"]) == 4
    assert data["qualityAtRun"]["state"] == "unavailable"
    assert data["qualityAtRun"]["data"] is None
    _assert_read_only(case, before, calls)


@pytest.mark.parametrize("maximum", [2, 0])
def test_exact_saved_native_handoff_actions_are_policy_recommendations_not_produced_causes(case, maximum):
    from modules.forecastops.domain.forecasting import _stable_id

    output, alert = _produce_saved_guard(case, maximum=maximum)
    handoff = case.forecasts.get_handoff(TENANT, _stable_id("intervention-handoff", alert.alert_id))
    assert handoff is not None
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case, issue_id="ISS-FC-" + alert.alert_id)
    assert response.status_code == 200, response.text
    data = response.json()["forecast"]["data"]
    assert data["alertAtRun"] == {"state": "recorded", "reason": None, "data": {
        "alertId": alert.alert_id, "level": alert.alert_level.value,
        "reasonCode": alert.alert_reason_code, "openedAt": alert.opened_at.isoformat(),
    }}
    assert data["recommendationAtAlert"] == {"state": "recorded", "reason": None, "data": {
        "handoffId": handoff.handoff_id, "alertId": alert.alert_id,
        "forecastOutputId": output.forecast_output_id, "predictionRunId": output.prediction_run_id,
        "policy": {"id": alert.policy_id, "version": alert.policy_version, "versionId": alert.policy_version_id},
        "createdAt": handoff.created_at.isoformat(), "handoffStatus": "proposed",
        "actionIds": handoff.action_set_json["recommended_actions"],
    }}
    assert data["rootCause"] == {"state": "unavailable", "reason": "ROOT_CAUSE_CANDIDATE_UNPRODUCED",
                                 "causeCandidate": None, "evidenceStrength": None, "dataConfidence": None}
    _assert_read_only(case, before, calls)


@pytest.mark.parametrize("fault", [
    "missing", "tenant", "store", "alert", "identity", "trigger", "evidence", "actions", "duplicate-actions",
])
def test_optional_handoff_with_wrong_current_owner_or_saved_evidence_is_not_advice(case, fault):
    from copy import deepcopy

    from modules.forecastops.domain.forecasting import _stable_id

    identity = _stable_id("intervention-handoff", case.alert.alert_id)
    handoff = case.forecasts.get_handoff(TENANT, identity)
    assert handoff is not None
    if fault == "missing":
        case.engine.execute("DELETE FROM durable_documents WHERE collection = ? AND doc_id = ?",
                            (case.forecasts._collection(case.forecasts._HANDOFFS, TENANT), identity))
    else:
        changes = {}
        if fault in {"tenant", "store", "alert", "identity"}:
            field = {"tenant": "tenant_id", "store": "store_id", "alert": "alert_id", "identity": "handoff_id"}[fault]
            changes[field] = "unrelated-source"
        else:
            saved = deepcopy(handoff.action_set_json)
            if fault == "trigger":
                saved["trigger_alert_level"] = "green"
            elif fault == "evidence":
                saved["evidence"]["forecast_p50"] = 999
            elif fault == "actions":
                saved["recommended_actions"] = ["invented_cause_or_action"]
            else:
                saved["recommended_actions"] = ["inspect_machine_uptime", "inspect_machine_uptime"]
            changes["action_set_json"] = saved
        case.documents.put(case.forecasts._collection(case.forecasts._HANDOFFS, TENANT), identity,
                           replace(handoff, **changes))
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case)
    assert response.status_code == 200, response.text
    data = response.json()["forecast"]["data"]
    assert len(data["bands"]) == 4
    assert data["recommendationAtAlert"] == {
        "state": "unavailable", "reason": "RECORDED_RECOMMENDATION_UNAVAILABLE", "data": None,
    }
    assert data["rootCause"]["causeCandidate"] is None
    assert data["rootCause"]["evidenceStrength"] is None and data["rootCause"]["dataConfidence"] is None
    _assert_read_only(case, before, calls)


def test_later_policy_handoff_does_not_replace_old_selected_alert_recommendation(case):
    from modules.forecastops.domain.forecasting import _stable_id

    first_output, first_alert = _produce_saved_guard(case, maximum=2)
    _produce_saved_guard(case, maximum=0)
    first = case.forecasts.get_handoff(TENANT, _stable_id("intervention-handoff", first_alert.alert_id))
    assert first is not None
    # Actual handoff execution records dispatch only; the original action set
    # remains readable without claiming approval, realization or a root cause.
    dispatched = first.execute(actor="engineering-dispatcher", intervention_id="engineering-intervention", now=ORIGIN)
    case.forecasts.save_handoff(dispatched)
    before, calls = _business_rows(case), case.inference.calls
    response = _read(case, issue_id="ISS-FC-" + first_alert.alert_id)
    assert response.status_code == 200, response.text
    data = response.json()["forecast"]["data"]
    saved = data["recommendationAtAlert"]["data"]
    assert saved["forecastOutputId"] == first_output.forecast_output_id
    assert saved["policy"]["versionId"] == first_alert.policy_version_id
    assert saved["actionIds"] == first.action_set_json["recommended_actions"]
    assert saved["handoffStatus"] == "dispatched"
    assert "executedBy" not in saved and "approved" not in saved
    assert data["rootCause"]["state"] == "unavailable"
    _assert_read_only(case, before, calls)
