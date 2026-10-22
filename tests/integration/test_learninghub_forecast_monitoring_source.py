"""Owned SQLite/source-join controls; no real model/GCS/PG/UAT evidence.

The actual release and Forecast writers produce the stored IDs/metadata. Only
inference is an explicit engineering provider: its binding and governance are
read from the canonical released model, not invented by the HTTP/body selector.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from models.shared_ml import ModelAlias
from models.shared_ml.production_runtime import ModelInferenceResult
from models.shared_ml.scoring_binding import ModelBinding
from modules.forecastops.application.forecasting import ForecastOpsService
from modules.forecastops.application.production_model import RegisteredEstimatorForecastEngine
from modules.forecastops.domain.forecasting import (
    ForecastInput,
    StoreDayObservation,
    default_forecast_alert_policy,
)
from modules.learninghub import LearningHubService, MlflowRegistryAdapter, ReleaseType
from modules.learninghub.application.forecast_monitoring_source import (
    CanonicalForecastMonitoringSource,
    CanonicalMonitoringUnavailable,
)
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.auth.engine import AuthorizationEngine
from shared.auth.identity import Principal, Role, Scope
from shared.domain.models import AddressLocation, Brand, Store, Tenant
from shared.governance import (
    InMemoryDecisionPolicyRepository,
    default_model_performance_drift_policy,
)
from shared.infrastructure.persistence import (
    DurableLearningHubRepository,
    SqliteDocumentStore,
    SqliteEngine,
)
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.decision_policy import SqlDecisionPolicyRepository
from shared.infrastructure.persistence.repositories import (
    DurableAddressLocationRepository,
    DurableBrandRepository,
    DurableForecastOpsRepository,
    DurableStoreRepository,
    DurableTenantRepository,
)
from tests.integration._learninghub_fixtures import prepare_candidate
from tests.integration._model_publish_flag_fixture import enable_model_publish

TENANT, STORE = "monitor-source-tenant", "monitor-source-store"


class ReleasedRegistryInference:
    """Synthetic numbers, actual registry-to-Forecast metadata propagation."""
    def __init__(self, learning, model_name):
        self.learning, self.model_name, self.calls = learning, model_name, 0
        self.point_value = 0.0

    def infer(self, **_kwargs):
        self.calls += 1
        model = self.learning.get_alias(self.model_name, ModelAlias.PRODUCTION)
        return ModelInferenceResult(
            binding=ModelBinding.from_model_version("forecastops", model, artifact_sha256="a" * 64),
            lower=(self.point_value,) * 4, point=(self.point_value,) * 4, upper=(self.point_value,) * 4,
            engine="engineering-recorded-inference", artifact_sha256="a" * 64,
            registry_version="engineering-registry-version", model_metadata=dict(model.monitoring_config),
        )


@pytest.fixture
def case(tmp_path, monkeypatch):
    for key, value in {"ODP_PRODUCT_MODE": "poc", "ODP_DEPLOY_ENV": "test",
                       "ODP_REQUIRE_LIVE_DATA": "false", "ODP_ENV": "test"}.items():
        monkeypatch.setenv(key, value)
    engine = SqliteEngine(tmp_path / "owned-canonical-monitoring.sqlite")
    try:
        docs = SqliteDocumentStore(engine)
        learning = DurableLearningHubRepository(docs)
        audit = DurableAuditLog(engine, worm_sink=LocalAppendOnlyWormSink(tmp_path / "worm"))
        service = LearningHubService(repository=learning, registry=MlflowRegistryAdapter(learning), audit_log=audit)
        enable_model_publish(service, tenant_id="tenant-modeling")
        model = prepare_candidate(service, "1.0.0")
        release = service.request_release(
            model_name=model.model_name, version=model.version, release_type=ReleaseType.FULL,
            reason="engineering controlled release", approval_id="engineering-approval-monitoring",
            rollback_target=model.version, monitoring_window="24h", success_criteria=("declared",),
            fail_criteria=("declared",), requested_by="ml-owner", approved_by="reviewer-a",
            expected_release_revision=learning.get_release_revision(model.model_name),
            idempotency_key="engineering-release-monitoring", correlation_id="engineering-monitoring-cid",
        )
        assert release.executed_at is not None
        stores = DurableStoreRepository(engine)
        # Preserve the actual SQLite FK contract: this is an owned master
        # hierarchy, rather than disabling FK enforcement for a source fixture.
        DurableTenantRepository(engine).save_tenant(Tenant(tenant_id=TENANT, tenant_name="Synthetic monitoring tenant"))
        DurableBrandRepository(engine).save_brand(Brand(brand_id="monitor-brand", tenant_id=TENANT,
            brand_code="monitor-brand", brand_name="Synthetic monitoring brand"))
        DurableAddressLocationRepository(engine).save_address(AddressLocation(
            address_id="monitor-address", tenant_id=TENANT, raw_address="Synthetic monitoring address"))
        stores.save_store(Store(store_id=STORE, tenant_id=TENANT, brand_id="monitor-brand",
            address_id="monitor-address", region_code="north"))
        forecasts = DurableForecastOpsRepository(docs)
        inference = ReleasedRegistryInference(learning, model.model_name)
        producer = ForecastOpsService(repository=forecasts, runtime_mode="poc",
            engine=RegisteredEstimatorForecastEngine(inference),
            policy_repository=InMemoryDecisionPolicyRepository([default_forecast_alert_policy(TENANT)]))
        produced = []
        for minute in (1, 2):
            origin = release.executed_at + timedelta(minutes=minute)
            value = ForecastInput(store_id=STORE, tenant_id=TENANT, prediction_origin_time=origin,
                observations=tuple(StoreDayObservation(store_id=STORE,
                    business_date=(origin - timedelta(days=60-index)).date(), actual_revenue=100.0,
                    data_quality_score=1.0, source_snapshot_ids=(f"owned-pos-{index}",)) for index in range(60)))
            result = producer.forecast([value], prediction_run_id=f"owned-monitor-run-{minute}", scored_at=origin)
            produced.append(result.forecasts[0])
        # Real SqlDecisionPolicyRepository reads actual owned SQLite rows. These
        # are original policy data, not a production approval or real PG claim.
        engine.execute("ATTACH DATABASE ':memory:' AS workflow")
        engine.execute("""CREATE TABLE workflow.decision_policies (
            policy_version_id TEXT, policy_label TEXT, policy_id TEXT, policy_version TEXT,
            policy_kind TEXT, tenant_id TEXT, effective_from TEXT, effective_to TEXT,
            change_reason TEXT, rollback_policy_version TEXT, parameters TEXT,
            declared_inputs TEXT, approved_by TEXT, owner_role TEXT, approved_at TEXT)""")
        policy = default_model_performance_drift_policy()
        values = policy.to_dict()
        columns = tuple(values)
        engine.execute("INSERT INTO workflow.decision_policies (" + ",".join(columns)
            + ") VALUES (" + ",".join("?" for _ in columns) + ")", tuple(
                json.dumps(values[key]) if key in {"parameters", "declared_inputs"} else values[key]
                for key in columns))
        policies = SqlDecisionPolicyRepository(engine)
        principal = Principal("monitor-reader", roles=frozenset({Role.MODEL_OWNER, Role.OPERATIONS_MANAGER}),
            scope=Scope(tenant_id=TENANT, brand_ids=frozenset({"monitor-brand"}),
                region_ids=frozenset({"north"}), store_ids=frozenset({STORE}), modules=frozenset({"forecastops"})))
        loader = CanonicalForecastMonitoringSource(learning=learning, forecasts=forecasts, stores=stores,
            policies=policies, authorization=AuthorizationEngine(audit_log=audit))
        yield {"producer": producer, "service": service, "stores": stores, "policies": policies, "engine": engine, "docs": docs, "learning": learning, "audit": audit, "forecasts": forecasts,
            "loader": loader, "release": release, "outputs": produced, "inference": inference,
            "principal": principal, "as_of": release.executed_at + timedelta(hours=1)}
    finally:
        engine.close()


def _load(case, **changes):
    values = {"principal": case["principal"], "release_id": case["release"].release_id,
        "store_id": STORE, "horizon_days": 28,
        "reference_ids": (case["outputs"][0].forecast_output_id,),
        "current_ids": (case["outputs"][1].forecast_output_id,), "as_of": case["as_of"]}
    values.update(changes)
    return case["loader"].load(**values)


def _business(case):
    return [dict(row) for row in case["engine"].query("SELECT * FROM durable_documents ORDER BY collection, doc_id")]


def test_original_release_and_forecast_writers_supply_execution_anchor_and_nested_join(case):
    before, calls = _business(case), case["inference"].calls
    result = _load(case)
    assert result.reference[0]["prediction"] == 0.0
    with pytest.raises(TypeError):
        result.current[0]["prediction"] = 999.0
    assert result.current[0]["prediction"] == 0.0
    assert result.release_executed_at == case["release"].executed_at
    assert result.release_window_end - result.release_executed_at == timedelta(hours=24)
    assert result.release_revision == case["release"].release_revision
    assert result.model_dataset_snapshot_id == case["release"].dataset_snapshot_id
    assert result.reference_coverage[1] < result.current_coverage[0]
    assert len(result.source_manifest) == 2 and len(result.source_digest) == 64
    for row in case["outputs"]:
        model = row.model_metadata["model_metadata"]
        assert model["release_id"] == case["release"].release_id
        assert model["release_revision"] == case["release"].release_revision
    assert _business(case) == before
    assert case["inference"].calls == calls
    assert not any(event.event_type == "learninghub.release_monitor.v1" for event in case["audit"].list_events())


@pytest.mark.parametrize("window", ["immediate", "until stable", "0h", "1.5d", "7D", "", "999999999999999999999999d"])
def test_free_text_or_invalid_release_window_is_named_unavailable_without_guess(case, window):
    release = replace(case["release"], monitoring_window=window)
    case["learning"].save_release_decision(release)
    before = _business(case)
    with pytest.raises(CanonicalMonitoringUnavailable, match="RELEASE_MONITORING_PERIOD_UNAVAILABLE"):
        _load(case)
    assert _business(case) == before


def test_legacy_release_has_no_guessed_execution_anchor(case):
    case["learning"].save_release_decision(replace(case["release"], executed_at=None))
    with pytest.raises(CanonicalMonitoringUnavailable, match="RELEASE_EXECUTION_ANCHOR_UNAVAILABLE"):
        _load(case)


@pytest.mark.parametrize("axis,value", [("tenant_id", "foreign"), ("store_ids", frozenset({"foreign"})),
    ("brand_ids", frozenset({"foreign"})), ("region_ids", frozenset({"foreign"})),
    ("modules", frozenset({"avm"})), ("assigned_area_ids", frozenset({"unknown-area"})),
    ("heat_zone_ids", frozenset({"unknown-zone"}))])
def test_actual_current_scope_never_becomes_an_unfiltered_source_read(case, axis, value):
    principal = replace(case["principal"], scope=replace(case["principal"].scope, **{axis: value}))
    before = _business(case)
    with pytest.raises(CanonicalMonitoringUnavailable):
        _load(case, principal=principal)
    assert _business(case) == before


@pytest.mark.parametrize("mutation", ["revision", "release_id", "dataset", "prediction", "outside_period"])
def test_one_canonical_counterpart_mismatch_blocks_every_observation(case, mutation):
    output = case["outputs"][1]
    if mutation == "prediction":
        rows = case["forecasts"].get_predictions(TENANT, output.prediction_run_id)
        case["docs"].put(case["forecasts"]._collection(case["forecasts"]._PREDICTIONS, TENANT),
            rows[0].prediction_id, replace(rows[0], p50_value=999.0), group_key=output.prediction_run_id)
    else:
        metadata = dict(output.model_metadata)
        nested = dict(metadata["model_metadata"])
        if mutation == "revision":
            nested["release_revision"] += 1
        elif mutation == "release_id":
            nested["release_id"] = "foreign-release"
        elif mutation == "dataset":
            metadata["dataset_snapshot_id"] = "foreign-dataset"
        metadata["model_metadata"] = nested
        changed = replace(output, model_metadata=metadata,
            scored_at=(case["release"].executed_at - timedelta(seconds=1)) if mutation == "outside_period" else output.scored_at)
        # Deliberate legacy-corrupt storage for reader negatives; normal Forecast
        # save_forecast refuses replacing an already recorded output.
        key = case["forecasts"]._collection(case["forecasts"]._FORECASTS, TENANT)
        case["docs"].put(key, output.forecast_output_id, changed, group_key=STORE, seq=output.forecast_version)
        predictions = case["forecasts"].get_predictions(TENANT, output.prediction_run_id)
        for prediction in predictions:
            explanation = dict(prediction.explanation_json)
            explanation["model_metadata"] = metadata
            case["docs"].put(case["forecasts"]._collection(case["forecasts"]._PREDICTIONS, TENANT),
                prediction.prediction_id, replace(prediction, explanation_json=explanation),
                group_key=output.prediction_run_id)
    before = _business(case)
    with pytest.raises(CanonicalMonitoringUnavailable):
        _load(case)
    assert _business(case) == before


def test_explicit_ids_are_unique_disjoint_same_horizon_and_ordered(case):
    identity = case["outputs"][0].forecast_output_id
    for changes in ({"reference_ids": identity}, {"reference_ids": {identity: True}},
                    {"reference_ids": (identity, identity)}, {"current_ids": (identity,)},
                    {"reference_ids": ("missing-source",)}, {"horizon_days": 56}):
        with pytest.raises(CanonicalMonitoringUnavailable):
            _load(case, **changes)


def test_failed_fully_compensated_later_release_counter_does_not_revoke_active_origin(case, monkeypatch):
    from modules.learninghub.application.release import LearningHubError

    learning, service, original = case["learning"], case["service"], case["release"]
    replacement = prepare_candidate(service, "1.1.0")
    counter = learning.get_release_revision(original.model_name)
    original_model = learning.get_alias(original.model_name, ModelAlias.PRODUCTION)
    record = case["audit"].record
    injected = False

    def fail_release_once(event):
        nonlocal injected
        saved = record(event)
        if event.event_type == "learninghub.model_release.v1" and not injected:
            injected = True
            raise RuntimeError("engineering failure after actual release audit")
        return saved

    monkeypatch.setattr(case["audit"], "record", fail_release_once)
    with pytest.raises(LearningHubError, match="durable receipt, aliases"):
        service.request_release(model_name=replacement.model_name, version=replacement.version,
            release_type=ReleaseType.FULL, reason="engineering compensated successor", approval_id="engineering-later-approval",
            rollback_target=original.to_version, monitoring_window="24h", success_criteria=("declared",),
            fail_criteria=("declared",), requested_by="ml-owner", approved_by="reviewer-a",
            expected_release_revision=counter, idempotency_key="engineering-compensated-monitor-successor",
            correlation_id="engineering-compensated-monitor-cid")
    assert injected
    assert learning.get_release_revision(original.model_name) == counter + 1
    assert learning.get_alias(original.model_name, ModelAlias.PRODUCTION) == original_model
    assert learning.get_release_decision(original.release_id) == original
    assert len(learning.list_release_decisions()) == 1
    assert any(event.event_type == "learninghub.model_release_compensation.v1" for event in case["audit"].list_events())
    loaded = _load(case)
    assert loaded.release_id == original.release_id
    assert loaded.release_revision == original.release_revision
    assert loaded.release_executed_at == original.executed_at
    assert loaded.reference[0]["prediction"] == 0.0


def test_two_valid_individual_saved_bindings_cannot_mix_artifacts_within_one_release_cohort(case):
    output = case["outputs"][1]
    metadata = {**output.model_metadata, "artifact_sha256": "b" * 64}
    changed = replace(output, model_metadata=metadata)
    case["docs"].put(case["forecasts"]._collection(case["forecasts"]._FORECASTS, TENANT),
        output.forecast_output_id, changed, group_key=STORE, seq=output.forecast_version)
    for prediction in case["forecasts"].get_predictions(TENANT, output.prediction_run_id):
        explanation = {**prediction.explanation_json, "model_metadata": metadata}
        case["docs"].put(case["forecasts"]._collection(case["forecasts"]._PREDICTIONS, TENANT),
            prediction.prediction_id, replace(prediction, explanation_json=explanation), group_key=output.prediction_run_id)
    before = _business(case)
    with pytest.raises(CanonicalMonitoringUnavailable, match="MONITORING_MODEL_BINDING_CHANGED"):
        _load(case)
    assert _business(case) == before
