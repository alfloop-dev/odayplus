"""Actual native drift + owned SQLite atomicity/HTTP; no PG/GCS/model-artifact claim."""
from dataclasses import replace

import pytest

from modules.learninghub.application.canonical_monitoring import (
    EVENT_TYPE,
    CanonicalForecastMonitoring,
)
from modules.learninghub.application.forecast_monitoring_source import (
    CanonicalMonitoringUnavailable,
)
from modules.learninghub.infrastructure.evidently_monitor import EvidentlyDriftMonitor
from shared.audit.durable_delivery import COLLECTION, JOB_TYPE, DurableAuditDelivery
from shared.audit.integrity import verify_audit_event_integrity
from shared.auth.identity import Role
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from tests.integration import test_learninghub_forecast_monitoring_source as source_controls

STORE, TENANT = source_controls.STORE, source_controls.TENANT
case = source_controls.case


def _service(case):
    return CanonicalForecastMonitoring(source=case["loader"], audit=case["audit"],
        queue=DurableJobQueue(case["engine"]), clock=lambda: case["as_of"])


def _evaluate(case, service=None, **changes):
    values = {"principal": case["principal"], "release_id": case["release"].release_id,
        "store_id": STORE, "horizon_days": 28,
        "reference_ids": (case["outputs"][0].forecast_output_id,),
        "current_ids": (case["outputs"][1].forecast_output_id,), "correlation_id": "owned-monitor-native-cid"}
    values.update(changes)
    return (service or _service(case)).evaluate(**values)


def _business(case):
    engine = case["engine"]
    return {
        "evaluations": case["learning"].list_monitoring_evaluations(),
        "jobs": [dict(row) for row in engine.query("SELECT * FROM durable_jobs ORDER BY job_id")],
        "intents": [dict(row) for row in engine.query("SELECT * FROM durable_documents WHERE collection LIKE ? ORDER BY collection, doc_id", ("%audit.durable_delivery%",))],
        "audit": [event.to_dict() for event in case["audit"].list_events() if event.event_type == EVENT_TYPE],
    }


def test_actual_native_drift_persists_exact_forecast_manifest_policy_and_pending_audit_atomically(case):
    calls = case["inference"].calls
    previous_jobs = {row["job_id"] for row in _business(case)["jobs"]}
    row = _evaluate(case)
    assert row["observed_metrics"] == {"prediction_drift_share": 0.0}
    assert row["drift_detected"] is False
    assert row["recommended_action"] == "CONTINUE_OBSERVING"
    assert row["source_provenance"]["mature_error_available"] is False
    assert row["source_provenance"]["full_window_observed"] is False
    assert row["dataset_snapshot_id"] == case["release"].dataset_snapshot_id
    assert row["reference_snapshot_id"].startswith("forecast-record-set:")
    assert case["learning"].get_dataset_snapshot(row["reference_snapshot_id"]) is None
    assert row["source_provenance"]["source_manifest"][0]["forecast_output_id"] == case["outputs"][0].forecast_output_id
    assert row["source_provenance"]["source_manifest"][1]["forecast_output_id"] == case["outputs"][1].forecast_output_id
    saved = case["learning"].get_monitoring_evaluation(row["evaluation_id"])
    assert saved.to_dict()["source_provenance"] == row["source_provenance"]
    event = case["audit"].get_event(row["audit_event_id"], tenant_id=TENANT)
    assert verify_audit_event_integrity(event)
    assert event.actor == case["principal"].subject_id
    assert event.correlation_id == "owned-monitor-native-cid"
    assert event.metadata["audit_delivery_job_type"] == JOB_TYPE
    assert event.event_type == EVENT_TYPE and event.outcome == "drift_not_detected"
    jobs = [job for job in _business(case)["jobs"] if job["job_id"] not in previous_jobs]
    assert len(jobs) == 1 and jobs[0]["job_type"] == "durable-audit-delivery"
    intents = TenantScopedDocumentStore(case["docs"], TENANT)
    assert intents.get(COLLECTION, event.event_id)["status"] == "pending"
    # The original consumer can deliver the actual recorded AuditEvent. This
    # local sink control is not a Cloud/GCS acknowledgement or model artifact.
    delivery = DurableAuditDelivery(case["engine"], TENANT, _service(case).queue, case["audit"]._worm_sink)
    job = delivery.queue.get(jobs[0]["job_id"])
    receipt = delivery.deliver(job)
    assert receipt["record_id"] == event.event_id
    assert intents.get(COLLECTION, event.event_id)["status"] == "delivered"
    assert delivery.deliver(job) == receipt
    assert case["inference"].calls == calls
    before_replay = _business(case)
    assert _evaluate(case) == row
    assert _business(case) == before_replay
    read = _service(case).read(principal=case["principal"], release_id=case["release"].release_id)
    assert read["state"] == "available" and read["items"][0]["evaluation_id"] == row["evaluation_id"]


@pytest.mark.parametrize("failure", ["save", "audit", "enqueue"])
def test_actual_native_evaluation_audit_and_delivery_reservation_rollback_as_one_sql_transaction(case, monkeypatch, failure):
    service = _service(case)
    before = _business(case)
    def fail(*_args, **_kwargs):
        raise RuntimeError("engineering atomic boundary failure")
    if failure == "save":
        monkeypatch.setattr(case["learning"], "save_monitoring_evaluation", fail)
    elif failure == "audit":
        original = case["audit"].record
        def audit(event):
            if event.event_type == EVENT_TYPE:
                original(event)
                fail()
            return original(event)
        monkeypatch.setattr(case["audit"], "record", audit)
    else:
        monkeypatch.setattr(service.queue, "enqueue", fail)
    with pytest.raises(RuntimeError, match="engineering atomic boundary"):
        _evaluate(case, service)
    assert _business(case) == before


def test_native_computation_rechecks_real_release_before_any_evaluation_commit(case, monkeypatch):
    before = _business(case)
    native = EvidentlyDriftMonitor.run_prediction
    def change(self, **kwargs):
        result = native(self, **kwargs)
        model = case["learning"].get_model_version(case["release"].model_name, case["release"].to_version)
        case["learning"].save_model_version(replace(model, monitoring_config={**model.monitoring_config, "release_revision": 999}))
        return result
    monkeypatch.setattr(EvidentlyDriftMonitor, "run_prediction", change)
    with pytest.raises(CanonicalMonitoringUnavailable, match="MONITORING_CURRENT_RELEASE_MISMATCH"):
        _evaluate(case)
    assert _business(case) == before


@pytest.mark.parametrize("change", ["tenant", "store", "module", "artifact", "policy", "audit"])
def test_read_revalidates_current_scope_actual_sources_policy_and_shared_immutable_audit(case, monkeypatch, change):
    saved = _evaluate(case)
    principal = case["principal"]
    if change == "tenant":
        principal = replace(principal, scope=replace(principal.scope, tenant_id="other-tenant"))
        def forbidden(*_args, **_kwargs):
            raise AssertionError("foreign evaluation must be filtered before source facts")
        monkeypatch.setattr(case["loader"], "load", forbidden)
    elif change == "store":
        principal = replace(principal, scope=replace(principal.scope, store_ids=frozenset({"other-store"})))
    elif change == "module":
        principal = replace(principal, scope=replace(principal.scope, modules=frozenset({"sitescore"})))
    elif change == "artifact":
        output = case["outputs"][1]
        repository = case["forecasts"]
        collection = repository._collection(repository._FORECASTS, TENANT)
        value = case["docs"].get(collection, output.forecast_output_id)
        assert value == output
        changed = replace(value, model_metadata={**value.model_metadata, "model_metadata": {"release_id": "other-release"}})
        case["docs"].put(collection, output.forecast_output_id, changed, group_key=STORE, seq=output.forecast_version)
    elif change == "policy":
        case["engine"].execute("UPDATE workflow.decision_policies SET parameters = ?", ('{}',))
    else:
        monkeypatch.setattr(case["audit"], "get_event", lambda *_args, **_kwargs: None)
    result = _service(case).read(principal=principal, release_id=case["release"].release_id)
    assert result["state"] == "unavailable" and result["items"] == []
    assert case["learning"].get_monitoring_evaluation(saved["evaluation_id"]) is not None
    assert len([event for event in case["audit"].list_events() if event.event_type == EVENT_TYPE]) == 1


def _signed_mapping_headers(key, subject):
    from datetime import UTC, datetime, timedelta

    from modules.opsboard.auth import encode_compact_jwt
    now = datetime.now(UTC)
    token = encode_compact_jwt({"sub": subject, "iss": "urn:owned:monitor-http",
        "aud": "owned-monitor-http", "iat": now.timestamp(),
        "exp": (now + timedelta(minutes=10)).timestamp()}, key)
    return {"Authorization": "Bearer " + token, "x-correlation-id": "owned-monitor-http-cid"}


def _client(case, monkeypatch, *, missing=False):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from apps.api.app.routes.learninghub import create_learninghub_router
    from apps.api.oday_api.security import dependencies
    from modules.learninghub import MlflowRegistryAdapter
    from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary, SigningKey
    for key in ("ODP_AUTH_JWKS_URL", "ODP_AUTH_JWKS_JSON", "ODP_AUTH_ISSUER", "ODP_AUTH_AUDIENCE", "ODP_AUTH_MODE"):
        monkeypatch.delenv(key, raising=False)
    key = SigningKey(kid="owned-monitor-http", algorithm="HS256",
                     secret=b"synthetic-owned-monitor-HTTP-HMAC-key")
    boundary = AuthenticationBoundary(AuthBoundaryConfig(issuer="urn:owned:monitor-http",
        audiences=frozenset({"owned-monitor-http"}), signing_keys={key.kid: key},
        principal_mappings={
            case["principal"].subject_id: {"roles": [Role.MODEL_OWNER.value, Role.OPERATIONS_MANAGER.value],
                "tenant_id": TENANT, "store_ids": [STORE], "brand_ids": ["monitor-brand"],
                "region_ids": ["north"], "modules": ["forecastops"]},
            "owned-read-only-operator": {"roles": [Role.OPERATIONS_MANAGER.value],
                "tenant_id": TENANT, "store_ids": [STORE], "modules": ["forecastops"]},
        }), audit_log=case["audit"])
    # This explicit stateless signed boundary has no session layer. The lane
    # permits it only while it is also the selected process owner; Main tests
    # below cover actual bound identity/session stores across two applications.
    monkeypatch.setattr(dependencies, "_default_boundary", boundary)
    app = FastAPI()
    @app.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.correlation_id = "owned-monitor-http-cid"
        return await call_next(request)
    app.include_router(create_learninghub_router(repository=case["learning"],
        registry=MlflowRegistryAdapter(case["learning"]), audit_log=case["audit"],
        job_queue=DurableJobQueue(case["engine"]), runtime_mode="poc",
        canonical_forecast_repository=None if missing else case["forecasts"],
        canonical_store_repository=case["stores"], canonical_policy_repository=case["policies"],
        canonical_monitoring_boundary=boundary))
    headers = _signed_mapping_headers(key, case["principal"].subject_id)
    return TestClient(app, raise_server_exceptions=False), headers


def _body(case):
    return {"store_id": STORE, "horizon_days": 28,
            "reference_ids": [case["outputs"][0].forecast_output_id],
            "current_ids": [case["outputs"][1].forecast_output_id]}


def test_real_http_selected_ids_call_actual_native_writer_and_readback_not_body_metrics(case, monkeypatch):
    client, headers = _client(case, monkeypatch)
    # Actual writer clock is server-owned. Test observations were produced at
    # an engineering future time; pin only that server clock, never HTTP body.
    from modules.learninghub.application import canonical_monitoring
    actual = canonical_monitoring.CanonicalForecastMonitoring.__init__
    def clock(self, **kwargs):
        actual(self, **kwargs, clock=lambda: case["as_of"])
    monkeypatch.setattr(canonical_monitoring.CanonicalForecastMonitoring, "__init__", clock)
    path = f"/learninghub/releases/{case['release'].release_id}/monitoring"
    with client:
        response = client.post(path, headers=headers, json=_body(case))
        assert response.status_code == 201, response.text
        saved = response.json()["item"]
        assert saved["requested_by"] == case["principal"].subject_id
        assert saved["observed_metrics"]["prediction_drift_share"] == 0.0
        read = client.get(path, headers=headers)
        assert read.status_code == 200 and read.json()["items"][0]["evaluation_id"] == saved["evaluation_id"]
    event = case["audit"].get_event(saved["audit_event_id"], tenant_id=TENANT)
    assert event.correlation_id == "owned-monitor-http-cid"


@pytest.mark.parametrize("extra", [{"observed_metrics": {"mape": 0}}, {"evaluated_by": "caller-actor"},
    {"release_revision": 1}, {"as_of": "2026-10-16T00:00:00Z"}, {"guardrails": [{"metric_name": "mape", "max_value": .1}]}])
def test_http_rejects_untrusted_observations_actor_release_policy_or_time_claims(case, monkeypatch, extra):
    client, headers = _client(case, monkeypatch)
    before = _business(case)
    with client:
        response = client.post(f"/learninghub/releases/{case['release'].release_id}/monitoring",
                               headers=headers, json={**_body(case), **extra})
        assert response.status_code == 422, response.text
    assert _business(case) == before


def test_http_missing_real_same_engine_composition_is503_not_empty_or_healthy(case, monkeypatch):
    client, headers = _client(case, monkeypatch, missing=True)
    before = _business(case)
    with client:
        response = client.get(f"/learninghub/releases/{case['release'].release_id}/monitoring", headers=headers)
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "MONITORING_DURABLE_OWNER_MISMATCH"
    assert _business(case) == before


@pytest.mark.parametrize("wrong_store_engine", [False, True])
def test_actual_main_mount_passes_the_real_bundle_forecast_store_policy_and_queue_owners(tmp_path, monkeypatch, wrong_store_engine):
    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from apps.api.oday_api.security.dependencies import reset_default_boundary
    from shared.audit.worm import LocalAppendOnlyWormSink
    from shared.infrastructure.persistence.decision_policy import SqlDecisionPolicyRepository
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.factory import _durable_bundle
    from shared.infrastructure.persistence.repositories import DurableStoreRepository
    for key, value in {"ODP_PRODUCT_MODE": "poc", "ODP_DEPLOY_ENV": "test", "ODP_ENV": "test",
                       "ODP_REQUIRE_LIVE_DATA": "false", "ODP_PERSISTENCE_MODE": "sqlite"}.items():
        monkeypatch.setenv(key, value)
    for key in ("ODP_AUTH_JWKS_URL", "ODP_AUTH_JWKS_JSON", "ODP_AUTH_ISSUER", "ODP_AUTH_AUDIENCE", "ODP_AUTH_MODE"):
        monkeypatch.delenv(key, raising=False)
    _configure_main_auth(monkeypatch, "mount")
    reset_default_boundary()
    bundle = _durable_bundle(tmp_path / "actual-main-owned.sqlite", worm_sink=LocalAppendOnlyWormSink(tmp_path / "main-worm"))
    other = SqliteEngine(tmp_path / "foreign-engine.sqlite")
    try:
        if wrong_store_engine:
            bundle = replace(bundle, store_repository=DurableStoreRepository(other))
        headers, _session = _main_identity(bundle, "mount")
        app = create_app(persistence=bundle, forecastops_policy_repository=SqlDecisionPolicyRepository(bundle.engine))
        with TestClient(app, raise_server_exceptions=False) as client:
            models_before = client.get("/api/v1/learninghub/models", headers=headers)
            assert models_before.status_code == 200, models_before.text
            original_listing = models_before.json()
            response = client.get("/api/v1/learninghub/releases/unknown-owned-release/monitoring", headers=headers)
            assert response.status_code == (503 if wrong_store_engine else 404), response.text
            assert response.json()["detail"]["code"] == ("MONITORING_DURABLE_OWNER_MISMATCH" if wrong_store_engine else "MONITORING_RELEASE_UNAVAILABLE")
            models = client.get("/api/v1/learninghub/models", headers=headers)
            assert models.status_code == 200, models.text
            assert models.json() == original_listing
        assert bundle.learninghub_repository.list_monitoring_evaluations() == []
        assert bundle.engine.query_one("SELECT count(*) AS n FROM durable_jobs")["n"] == 0
        assert not [event for event in bundle.audit_log.list_events() if event.event_type == EVENT_TYPE]
    finally:
        bundle.engine.close()
        other.close()
        reset_default_boundary()


def test_actual_native_drift_breach_recommends_review_without_executing_a_model_rollback(case):
    from datetime import timedelta

    from models.shared_ml import ModelAlias
    from modules.forecastops.domain.forecasting import ForecastInput, StoreDayObservation
    reference, current = [], []
    for index in range(24):
        at = case["release"].executed_at + timedelta(minutes=index + 3)
        case["inference"].point_value = 0.0 if index < 12 else 100.0
        result = case["producer"].forecast([ForecastInput(store_id=STORE, tenant_id=TENANT,
            prediction_origin_time=at, observations=tuple(StoreDayObservation(store_id=STORE,
                business_date=(at - timedelta(days=60-day)).date(), actual_revenue=100.0,
                data_quality_score=1.0, source_snapshot_ids=(f"owned-pos-{day}",)) for day in range(60)))],
            prediction_run_id=f"owned-breach-run-{index}", scored_at=at)
        (reference if index < 12 else current).append(result.forecasts[0].forecast_output_id)
    before = case["learning"].get_alias(case["release"].model_name, ModelAlias.PRODUCTION)
    decisions = case["learning"].list_release_decisions()
    row = _evaluate(case, reference_ids=tuple(reference), current_ids=tuple(current))
    assert row["drift_detected"] is True and row["observed_metrics"]["prediction_drift_share"] == 1.0
    assert row["recommended_action"] == "REVIEW_ROLLBACK"
    assert row["source_provenance"]["mature_error_available"] is False
    assert len(row["source_provenance"]["source_manifest"]) == 24
    assert row["source_provenance"]["source_manifest"][0]["prediction_value"] == 0.0
    assert row["source_provenance"]["source_manifest"][-1]["prediction_value"] == 100.0
    assert case["learning"].get_alias(before.model_name, ModelAlias.PRODUCTION) == before
    assert case["learning"].list_release_decisions() == decisions
    assert case["audit"].get_event(row["audit_event_id"], tenant_id=TENANT).outcome == "drift_detected"


def test_http_original_publish_permission_still_denies_non_model_owner_before_computation(case, monkeypatch):
    from modules.opsboard.auth import SigningKey
    from shared.audit.policy import SECURITY_EVENT_TYPE
    client, headers = _client(case, monkeypatch)
    headers.update(_signed_mapping_headers(SigningKey(kid="owned-monitor-http", algorithm="HS256",
        secret=b"synthetic-owned-monitor-HTTP-HMAC-key"), "owned-read-only-operator"))
    before = _business(case)
    ids = {event.event_id for event in case["audit"].list_events()}
    with client:
        response = client.post(f"/learninghub/releases/{case['release'].release_id}/monitoring", headers=headers, json=_body(case))
        assert response.status_code == 403, response.text
    assert _business(case) == before
    added = [event for event in case["audit"].list_events() if event.event_id not in ids]
    denied = [event for event in added if event.event_type == SECURITY_EVENT_TYPE]
    authentication = [event for event in added if event.event_type == "security.authentication"]
    assert len(added) == 2 and len(authentication) == 1
    assert authentication[0].actor == "owned-read-only-operator"
    assert authentication[0].action == "authenticate" and authentication[0].resource == "auth/oidc"
    assert authentication[0].outcome == "success" and authentication[0].metadata["tenant_id"] == TENANT
    assert authentication[0].correlation_id == "owned-monitor-http-cid"
    assert len(denied) == 1 and denied[0].event_type == SECURITY_EVENT_TYPE
    assert denied[0].actor == "owned-read-only-operator" and denied[0].outcome == "deny"
    assert denied[0].action == "publish" and denied[0].resource == "model"
    assert denied[0].correlation_id == "owned-monitor-http-cid"


_MAIN_TENANT = "c1010000-0000-4000-8000-000000000001"
_MAIN_ACTOR = "c1010000-0000-4000-8000-000000000002"
_MAIN_STORE = "owned-main-monitor-store"


def _configure_main_auth(monkeypatch, tag):
    import os

    from apps.api.oday_api.security import dependencies
    # Remember the original process bindings for teardown without substituting
    # any owner used by create_app, authentication, permission or source reads.
    for name in ("_default_boundary", "_bound_persistence", "_bound_audit_log"):
        monkeypatch.setattr(dependencies, name, getattr(dependencies, name))
    for key in tuple(os.environ):
        if key.startswith("ODP_AUTH_") or key == "ODP_IDENTITY_TOKEN_SIGNING_KEY":
            monkeypatch.delenv(key, raising=False)
    for key, value in {"ODP_PRODUCT_MODE": "poc", "ODP_DEPLOY_ENV": "test", "ODP_ENV": "test",
                       "ODP_REQUIRE_LIVE_DATA": "false", "ODP_PERSISTENCE_MODE": "sqlite"}.items():
        monkeypatch.setenv(key, value)
    if tag is not None:
        monkeypatch.setenv("ODP_AUTH_MODE", "local")
        monkeypatch.setenv("ODP_AUTH_LOCAL_ISSUER", "urn:owned:main-monitor:" + tag)
        monkeypatch.setenv("ODP_AUTH_LOCAL_AUDIENCES", "owned-main-monitor-" + tag)
        monkeypatch.setenv("ODP_IDENTITY_TOKEN_SIGNING_KEY", "monitor-" + tag + ":synthetic-main-monitor-key-" + tag)


def _main_identity(bundle, tag):
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from modules.opsboard.auth import SigningKey, encode_compact_jwt
    from shared.auth.identity import Scope
    from shared.identity import Account
    scope = Scope(tenant_id=_MAIN_TENANT, brand_ids=frozenset({"main-monitor-brand"}),
        region_ids=frozenset({"north"}), store_ids=frozenset({_MAIN_STORE}),
        modules=frozenset({"forecastops"}))
    bundle.identity_store.save_account(Account(account_id=UUID(_MAIN_ACTOR), tenant_id=UUID(_MAIN_TENANT),
        username="synthetic-monitor-reader", email="synthetic-monitor-reader@example.invalid"))
    bundle.identity_store.set_account_roles(_MAIN_ACTOR, [Role.MODEL_OWNER, Role.OPERATIONS_MANAGER])
    bundle.identity_store.set_account_scope(_MAIN_ACTOR, scope)
    session = bundle.session_service.create_session(account_id=UUID(_MAIN_ACTOR), provider="local_password")
    now = datetime.now(UTC)
    key = SigningKey(kid="monitor-" + tag, algorithm="HS256",
                     secret=("synthetic-main-monitor-key-" + tag).encode())
    token = encode_compact_jwt({"sub": _MAIN_ACTOR, "sid": str(session.session_id),
        "tenant_id": _MAIN_TENANT, "iss": "urn:owned:main-monitor:" + tag,
        "aud": "owned-main-monitor-" + tag, "iat": now.timestamp(),
        "exp": (now + timedelta(minutes=10)).timestamp()}, key)
    # API credentials are Bearer tokens. The browser BFF's cookie-to-Bearer
    # transport is outside this HTTP owner test; no raw Cookie authentication
    # or live identity-provider/GCS/model-artifact acceptance is claimed.
    return {"Authorization": "Bearer " + token, "x-correlation-id": "owned-main-monitor-" + tag}, session


def _seed_main_monitoring(bundle, tag):
    """Actual release/Forecast/evaluation writers; synthetic inference only."""
    import json
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from modules.forecastops.application.forecasting import ForecastOpsService
    from modules.forecastops.application.production_model import RegisteredEstimatorForecastEngine
    from modules.forecastops.domain.forecasting import (
        ForecastInput,
        StoreDayObservation,
        default_forecast_alert_policy,
    )
    from modules.learninghub import LearningHubService, MlflowRegistryAdapter, ReleaseType
    from shared.auth.engine import AuthorizationEngine
    from shared.auth.identity import Principal
    from shared.domain.models import AddressLocation, Brand, Store, Tenant
    from shared.governance import (
        InMemoryDecisionPolicyRepository,
        default_model_performance_drift_policy,
    )
    from shared.infrastructure.persistence.decision_policy import SqlDecisionPolicyRepository
    from tests.integration._learninghub_fixtures import prepare_candidate
    from tests.integration._model_publish_flag_fixture import enable_model_publish
    learning, engine = bundle.learninghub_repository, bundle.engine
    service = LearningHubService(repository=learning, registry=MlflowRegistryAdapter(learning),
        audit_log=bundle.audit_log, feature_flags=bundle.feature_flag_registry, job_queue=bundle.job_queue)
    enable_model_publish(service, tenant_id=_MAIN_TENANT)
    model = prepare_candidate(service, "1.0.0")
    release = service.request_release(model_name=model.model_name, version=model.version,
        release_type=ReleaseType.FULL, reason="Synthetic main-owned monitoring release",
        approval_id="engineering-main-monitor-approval", rollback_target=model.version,
        monitoring_window="24h", success_criteria=("declared",), fail_criteria=("declared",),
        requested_by="ml-owner", approved_by="reviewer-a",
        expected_release_revision=learning.get_release_revision(model.model_name),
        idempotency_key="main-monitor-release-" + tag, correlation_id="main-monitor-release-" + tag)
    assert release.executed_at is not None
    bundle.tenant_repository.save_tenant(Tenant(tenant_id=_MAIN_TENANT, tenant_name="Synthetic main monitor tenant"))
    bundle.brand_repository.save_brand(Brand(brand_id="main-monitor-brand", tenant_id=_MAIN_TENANT,
        brand_code="main-monitor-brand", brand_name="Synthetic main monitor brand"))
    bundle.address_location_repository.save_address(AddressLocation(address_id="main-monitor-address",
        tenant_id=_MAIN_TENANT, raw_address="Synthetic main monitor address"))
    bundle.store_repository.save_store(Store(store_id=_MAIN_STORE, tenant_id=_MAIN_TENANT,
        brand_id="main-monitor-brand", address_id="main-monitor-address", region_code="north"))
    inference = source_controls.ReleasedRegistryInference(learning, model.model_name)
    producer = ForecastOpsService(repository=bundle.forecastops_repository, runtime_mode="poc",
        engine=RegisteredEstimatorForecastEngine(inference),
        policy_repository=InMemoryDecisionPolicyRepository([default_forecast_alert_policy(_MAIN_TENANT)]))
    outputs = []
    for index in (1, 2):
        at = datetime.now(UTC)
        source = ForecastInput(store_id=_MAIN_STORE, tenant_id=_MAIN_TENANT, prediction_origin_time=at,
            observations=tuple(StoreDayObservation(store_id=_MAIN_STORE,
                business_date=(at - timedelta(days=60-day)).date(), actual_revenue=100.0,
                data_quality_score=1.0, source_snapshot_ids=(f"main-owned-pos-{day}",)) for day in range(60)))
        outputs.append(producer.forecast([source], prediction_run_id=f"main-monitor-{tag}-{index}",
                                        scored_at=at).forecasts[0])
    assert outputs[0].scored_at < outputs[1].scored_at
    engine.execute("ATTACH DATABASE ':memory:' AS workflow")
    engine.execute("""CREATE TABLE workflow.decision_policies (
        policy_version_id TEXT, policy_label TEXT, policy_id TEXT, policy_version TEXT,
        policy_kind TEXT, tenant_id TEXT, effective_from TEXT, effective_to TEXT,
        change_reason TEXT, rollback_policy_version TEXT, parameters TEXT,
        declared_inputs TEXT, approved_by TEXT, owner_role TEXT, approved_at TEXT)""")
    values = default_model_performance_drift_policy().to_dict()
    columns = tuple(values)
    engine.execute("INSERT INTO workflow.decision_policies (" + ",".join(columns)
        + ") VALUES (" + ",".join("?" for _ in columns) + ")", tuple(
            json.dumps(values[key]) if key in {"parameters", "declared_inputs"} else values[key] for key in columns))
    policies = SqlDecisionPolicyRepository(engine)
    from modules.learninghub.application.forecast_monitoring_source import (
        CanonicalForecastMonitoringSource,
    )
    source = CanonicalForecastMonitoringSource(learning=learning, forecasts=bundle.forecastops_repository,
        stores=bundle.store_repository, policies=policies, authorization=AuthorizationEngine(audit_log=bundle.audit_log))
    principal = Principal(_MAIN_ACTOR, roles=frozenset({Role.MODEL_OWNER, Role.OPERATIONS_MANAGER}),
                          scope=bundle.identity_store.get_account_scope(_MAIN_ACTOR))
    body = {"store_id": _MAIN_STORE, "horizon_days": 28,
            "reference_ids": [outputs[0].forecast_output_id], "current_ids": [outputs[1].forecast_output_id]}
    saved = CanonicalForecastMonitoring(source=source, audit=bundle.audit_log, queue=bundle.job_queue).evaluate(
        principal=principal, release_id=release.release_id, store_id=_MAIN_STORE, horizon_days=28,
        reference_ids=tuple(body["reference_ids"]), current_ids=tuple(body["current_ids"]),
        correlation_id="main-monitor-internal-producer-" + tag)
    return SimpleNamespace(release=release, saved=saved, body=body, policies=policies,
                           inference=inference, source=source)


def _main_business(bundle):
    return {"evaluations": [item.to_dict() for item in bundle.learninghub_repository.list_monitoring_evaluations()],
        "documents": [tuple(row) for row in bundle.engine.query(
            "SELECT * FROM durable_documents ORDER BY collection,doc_id")],
        "jobs": [tuple(row) for row in bundle.engine.query("SELECT * FROM durable_jobs ORDER BY job_id")],
        "business_audit": [event.to_dict() for event in bundle.audit_log.list_events() if event.event_type == EVENT_TYPE]}


def test_two_actual_main_apps_keep_signed_identity_sessions_sources_and_both_monitoring_methods_on_their_own_owners(tmp_path, monkeypatch):
    from contextlib import ExitStack

    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from apps.api.oday_api.security import dependencies
    from shared.audit.worm import LocalAppendOnlyWormSink
    from shared.infrastructure.persistence.factory import _durable_bundle
    with ExitStack() as stack:
        owners = []
        for tag in ("first", "second"):
            _configure_main_auth(monkeypatch, tag)
            bundle = _durable_bundle(tmp_path / (tag + ".sqlite"),
                                     worm_sink=LocalAppendOnlyWormSink(tmp_path / (tag + "-worm")))
            stack.callback(bundle.engine.close)
            headers, session = _main_identity(bundle, tag)
            seeded = _seed_main_monitoring(bundle, tag)
            app = create_app(persistence=bundle, forecastops_policy_repository=seeded.policies)
            boundary = dependencies.default_boundary()
            assert boundary._identity_store is bundle.identity_store
            assert boundary._session_service is bundle.session_service
            owners.append((bundle, headers, session, seeded, boundary,
                           stack.enter_context(TestClient(app, raise_server_exceptions=False))))
        assert owners[0][4] is not owners[1][4]
        assert dependencies.default_boundary() is owners[1][4]
        before = [_main_business(item[0]) for item in owners]
        calls = [item[3].inference.calls for item in owners]
        for index, (bundle, headers, _session, seeded, _boundary, client) in enumerate(owners):
            path = f"/api/v1/learninghub/releases/{seeded.release.release_id}/monitoring"
            own = client.get(path, headers=headers)
            assert own.status_code == 200, own.text
            assert own.json()["state"] == "available"
            assert own.json()["items"][0]["evaluation_id"] == seeded.saved["evaluation_id"]
            assert own.json()["items"][0]["requested_by"] == _MAIN_ACTOR
            # The same verified account/tenant/store IDs in the other app do
            # not grant access: issuer/key/audience and sessions are app-owned.
            foreign_headers = owners[1-index][1]
            for method in ("get", "post"):
                response = getattr(client, method)(path, headers=foreign_headers,
                    **({"json": seeded.body} if method == "post" else {}))
                assert response.status_code == 401, response.text
            replay = client.post(path, headers=headers, json=seeded.body)
            assert replay.status_code == 201, replay.text
            assert replay.json()["item"]["evaluation_id"] == seeded.saved["evaluation_id"]
            assert _main_business(bundle) == before[index]
            assert seeded.inference.calls == calls[index]
        first, second = owners
        scope = first[0].identity_store.get_account_scope(_MAIN_ACTOR)
        first[0].identity_store.set_account_scope(_MAIN_ACTOR, replace(scope, store_ids=frozenset({"revoked-store"})))
        path = f"/api/v1/learninghub/releases/{first[3].release.release_id}/monitoring"
        revoked = first[5].get(path, headers=first[1])
        assert revoked.status_code == 200 and revoked.json()["state"] == "unavailable"
        assert revoked.json()["items"] == []
        first[0].identity_store.set_account_scope(_MAIN_ACTOR, scope)
        second[0].session_service.revoke_session(second[2].session_id, "engineering current-session revocation")
        assert second[5].get(f"/api/v1/learninghub/releases/{second[3].release.release_id}/monitoring",
                             headers=second[1]).status_code == 401
        assert first[5].get(path, headers=first[1]).json()["state"] == "available"
        assert [_main_business(item[0]) for item in owners] == before
        assert [item[3].inference.calls for item in owners] == calls


@pytest.mark.parametrize("method", ["get", "post"])
def test_actual_main_captured_missing_auth_owner_never_inherits_later_app_boundary(tmp_path, monkeypatch, method):
    from contextlib import ExitStack

    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from apps.api.oday_api.security import dependencies
    from shared.audit.worm import LocalAppendOnlyWormSink
    from shared.infrastructure.persistence.decision_policy import SqlDecisionPolicyRepository
    from shared.infrastructure.persistence.factory import _durable_bundle
    with ExitStack() as stack:
        _configure_main_auth(monkeypatch, None)
        missing = _durable_bundle(tmp_path / "missing-owner.sqlite",
            worm_sink=LocalAppendOnlyWormSink(tmp_path / "missing-worm"))
        stack.callback(missing.engine.close)
        missing_app = create_app(persistence=missing,
            forecastops_policy_repository=SqlDecisionPolicyRepository(missing.engine))
        assert dependencies.default_boundary() is None
        missing_client = stack.enter_context(TestClient(missing_app, raise_server_exceptions=False))
        _configure_main_auth(monkeypatch, "later")
        later = _durable_bundle(tmp_path / "later-owner.sqlite",
            worm_sink=LocalAppendOnlyWormSink(tmp_path / "later-worm"))
        stack.callback(later.engine.close)
        headers, _session = _main_identity(later, "later")
        later_app = create_app(persistence=later,
            forecastops_policy_repository=SqlDecisionPolicyRepository(later.engine))
        stack.enter_context(TestClient(later_app, raise_server_exceptions=False))
        assert dependencies.default_boundary() is not None
        before = _main_business(missing)
        def forbidden_source(*_args, **_kwargs):
            raise AssertionError("missing authentication owner must stop before source access")
        from modules.learninghub.application import forecast_monitoring_source
        monkeypatch.setattr(forecast_monitoring_source.CanonicalForecastMonitoringSource, "__init__", forbidden_source)
        response = getattr(missing_client, method)("/api/v1/learninghub/releases/unknown-release/monitoring",
            headers=headers, **({"json": {"store_id": _MAIN_STORE, "horizon_days": 28,
                "reference_ids": ["ref"], "current_ids": ["cur"]}} if method == "post" else {}))
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "MONITORING_AUTHENTICATION_OWNER_UNAVAILABLE"
        assert _main_business(missing) == before
