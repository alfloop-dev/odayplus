"""Authority must stay fresh even when immutable estimator bytes are cached."""

import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from models.shared_ml.production_runtime import MlflowProductionModelRuntime
from tests.integration._model_publish_flag_fixture import enable_model_publish

_postgres = pytest.mark.skipif(
    not os.environ.get("INTAKE_TEST_DATABASE_URL"),
    reason="requires an explicitly configured existing PostgreSQL scratch server",
)

PREFIX = "oday.model_version."
SCHEMA = "candidate-site-view-v2"


class MetadataClient:
    def __init__(self):
        fields = dict(
            stage="production",
            approved_by="reviewer-a",
            approved_at=datetime(2026, 7, 24, tzinfo=UTC).isoformat(),
            domain_version="v1",
            dataset_snapshot_id="dataset-1",
            feature_schema_version=SCHEMA,
            label_version="label-1",
            artifact_uri="gs://local-test/model.zip",
            artifact_sha256="sha256:" + "a" * 64,
            mlflow_run_id="run-1",
            source_run_id="training-1",
            git_sha="abc123",
            monitoring_config='{"output_transform":{"name":"original"}}',
        )
        self.version = SimpleNamespace(
            name="sitescore",
            version="1",
            current_stage="Production",
            aliases=["production"],
            source=fields["artifact_uri"],
            tags={PREFIX + k: v for k, v in fields.items()},
        )
        self.reads = 0

    def get_model_version_by_alias(self, name, alias):
        self.reads += 1
        return self.version

    def get_run(self, run_id):
        return SimpleNamespace(info=SimpleNamespace(run_id=run_id))


def test_cache_never_retains_old_approval_or_monitoring_metadata(monkeypatch):
    client = MetadataClient()
    estimator = SimpleNamespace(spec=SimpleNamespace(engine="synthetic-local-estimator"))
    downloads = []
    artifact = b"local-only-test-artifact"
    client.version.tags[PREFIX + "artifact_sha256"] = (
        "sha256:" + hashlib.sha256(artifact).hexdigest()
    )
    runtime = MlflowProductionModelRuntime(
        tracking_uri="https://local-test.invalid",
        client=client,
        artifact_loader=lambda *args: downloads.append(args) or artifact,
        model_names={"sitescore": "sitescore"},
    )
    monkeypatch.setattr(
        "models.shared_ml.production_runtime.load_estimator_artifact", lambda raw: estimator
    )
    first = runtime.resolve(service="sitescore", expected_feature_schema_version=SCHEMA)
    client.version.tags[PREFIX + "approved_by"] = "reviewer-b"
    client.version.tags[PREFIX + "monitoring_config"] = '{"output_transform":{"name":"changed"}}'
    second = runtime.resolve(service="sitescore", expected_feature_schema_version=SCHEMA)
    assert first.binding.approved_by == "reviewer-a"
    assert second.binding.approved_by == "reviewer-b"
    assert second.model_metadata == {"output_transform": {"name": "changed"}}
    assert second.estimator is first.estimator
    assert len(downloads) == 1
    assert client.reads == 2


import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from uuid import uuid4

from models.shared_ml.current_model_admission import (
    CurrentModelAdmissionChanged,
    CurrentModelAdmissionPort,
)
from models.shared_ml.production_runtime import (
    ModelInferenceResult,
    ProductionModelApprovalError,
    ProductionModelLineageError,
    ProductionModelRegistryError,
)
from models.shared_ml.registry import ModelAlias
from models.shared_ml.scoring_binding import ModelBinding
from shared.infrastructure.persistence import (
    DurableLearningHubRepository,
    SqliteDocumentStore,
    SqliteEngine,
)


def test_metadata_read_never_downloads_or_resolves_artifacts():
    client = MetadataClient()
    runtime = MlflowProductionModelRuntime(
        tracking_uri="https://local-test.invalid",
        client=client,
        artifact_loader=lambda *args: pytest.fail("metadata admission downloaded artifact"),
        model_names={"sitescore": "sitescore"},
    )
    facts = runtime.read_admission(service="sitescore", expected_feature_schema_version=SCHEMA)
    assert facts["model_approved_by"] == "reviewer-a"
    assert facts["artifact_sha256"] == "sha256:" + "a" * 64
    assert facts["registry_version"] == "1"
    assert runtime._cache == {}


@pytest.mark.parametrize("mutation", ["stage", "approval", "schema", "run"])
def test_metadata_read_rejects_current_revocation(mutation):
    client = MetadataClient()
    runtime = MlflowProductionModelRuntime(
        tracking_uri="https://local-test.invalid",
        client=client,
        model_names={"sitescore": "sitescore"},
    )
    runtime.read_admission(service="sitescore", expected_feature_schema_version=SCHEMA)
    if mutation == "stage":
        client.version.current_stage = "Staging"
    elif mutation == "approval":
        client.version.tags[PREFIX + "approved_by"] = ""
    elif mutation == "schema":
        client.version.tags[PREFIX + "feature_schema_version"] = "another-schema"
    else:
        client.get_run = lambda *args: (_ for _ in ()).throw(
            ConnectionError("synthetic lineage unavailable")
        )
    with pytest.raises((ProductionModelApprovalError, ProductionModelLineageError)):
        runtime.read_admission(service="sitescore", expected_feature_schema_version=SCHEMA)


def test_sqlite_cannot_supply_production_model_authority(tmp_path):
    engine = SqliteEngine(tmp_path / "authority.sqlite3")
    repository = DurableLearningHubRepository(SqliteDocumentStore(engine))
    runtime = SimpleNamespace(
        model_names={"sitescore": "sitescore"}, read_admission=lambda **kwargs: {}
    )
    port = CurrentModelAdmissionPort(runtime=runtime, registry_repository=repository, engine=engine)
    try:
        with pytest.raises(ProductionModelRegistryError):
            port.read(expected_feature_schema_version=SCHEMA)
    finally:
        engine.close()


def test_held_recovery_guard_blocks_actual_durable_writer_without_increment(tmp_path):
    engine = SqliteEngine(tmp_path / "held.sqlite3")
    repository = DurableLearningHubRepository(SqliteDocumentStore(engine))
    from models.shared_ml.registry import ModelVersion

    model = ModelVersion(
        model_name="local-guard-model",
        version="v1",
        artifact_uri="gs://local-test/model",
        dataset_snapshot_id="local-dataset",
        feature_schema_version=SCHEMA,
        label_version="label-1",
        metrics={},
    )
    entered, finished = Event(), Event()

    def write():
        entered.set()
        repository.save_model_version(model)
        finished.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with engine.lock:
                with repository.release_recovery_guard(model.model_name):
                    future = executor.submit(write)
                    assert entered.wait(2)
                    assert not finished.wait(0.05)
                    assert repository.get_release_revision(model.model_name) == 0
            future.result(3)
        assert finished.is_set()
        assert repository.get_model_version(model.model_name, model.version) == model
        assert repository.get_release_revision(model.model_name) == 0
    finally:
        engine.close()


@pytest.fixture
def released_pg_model(intake_blank_db, tmp_path, monkeypatch):
    """Actual local producer: governed MLflow release + new scratch PostgreSQL."""
    import tests.integration._learninghub_fixtures as release_fixtures
    from modules.learninghub import LearningHubService, ReleaseType
    from modules.learninghub.infrastructure.mlflow_adapter import MlflowRegistryAdapter
    from shared.audit.worm import LocalAppendOnlyWormSink
    from shared.infrastructure.persistence import DurableAuditLog
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    engine = PostgresEngine(
        intake_blank_db.url(),
        bootstrap=True,
        validate_schema=False,
        min_pool_size=1,
        max_pool_size=2,
    )
    repository = DurableLearningHubRepository(SqliteDocumentStore(engine))
    name = "forecast_revenue_interval_" + uuid4().hex
    original_model_version = release_fixtures.model_version

    def with_digest(*args, **kwargs):
        return replace(
            original_model_version(*args, **kwargs),
            monitoring_config={"artifact_sha256": "sha256:" + "a" * 64},
        )

    monkeypatch.setattr(release_fixtures, "model_version", with_digest)
    adapter = MlflowRegistryAdapter(
        repository, tracking_uri=f"sqlite:///{tmp_path / 'local-release.mlflow.sqlite3'}"
    )
    service = LearningHubService(
        repository=repository,
        registry=adapter,
        audit_log=DurableAuditLog(
            engine, worm_sink=LocalAppendOnlyWormSink(tmp_path / "local-worm")
        ),
    )
    enable_model_publish(service, tenant_id='tenant-modeling')
    model = release_fixtures.prepare_candidate(
        service,
        "1.0.0",
        model_name=name,
        decision_policy=release_fixtures.model_performance_policy_for_model(name),
    )
    decision = service.request_release(
        model_name=name,
        version=model.version,
        release_type=ReleaseType.FULL,
        reason="synthetic local test release",
        approval_id="local-test-approval",
        rollback_target=model.version,
        monitoring_window="48h",
        success_criteria=("production alias resolves",),
        fail_criteria=("test regression",),
        requested_by="ml-owner",
        approved_by="reviewer-a",
        correlation_id="local-test-correlation",
        expected_release_revision=0,
        idempotency_key="local-test-release",
    )
    runtime = MlflowProductionModelRuntime(
        tracking_uri=adapter.tracking_uri,
        client=adapter.client,
        artifact_loader=lambda *args: pytest.fail("current admission downloaded artifact"),
        model_names={"forecastops": name},
    )
    port = CurrentModelAdmissionPort(
        runtime=runtime,
        registry_repository=repository,
        engine=engine,
        tenant_id="local-test-tenant",
    )
    try:
        yield engine, repository, adapter, runtime, port, decision
    finally:
        engine.close()


def _read(port):
    return port.read(
        service="forecastops", expected_feature_schema_version="store-machine-timeseries-view-v1"
    )


@pytest.mark.requires_live_env
@_postgres
def test_actual_pg_approved_release_metadata_receipt_stable_and_held_read_no_revision(
    released_pg_model,
):
    engine, repository, adapter, runtime, port, decision = released_pg_model
    before = repository.get_release_revision(decision.model_name)
    first = _read(port)
    assert _read(port) == first
    assert first["release_id"] == decision.release_id
    assert first["authority_revision"] == 1
    with engine.lock:
        with port.guard(first, tenant_id="local-test-tenant"):
            assert _read(port) == first
    assert repository.get_release_revision(decision.model_name) == before == 1
    assert runtime._cache == {}
    first["model_metadata"]["approval_id"] = "corrupt-local-copy"
    with pytest.raises(CurrentModelAdmissionChanged):
        with port.guard(first, tenant_id="local-test-tenant"):
            pass


@pytest.mark.parametrize(
    "withdrawal", ["alias", "approval", "card", "validation", "backtest", "decision"]
)
@pytest.mark.requires_live_env
@_postgres
def test_actual_pg_current_model_withdrawal_cannot_publish(released_pg_model, withdrawal):
    engine, repository, adapter, runtime, port, decision = released_pg_model
    receipt = _read(port)
    current = repository.get_alias(decision.model_name, ModelAlias.PRODUCTION)
    if withdrawal == "alias":
        adapter.clear_alias(model_name=current.model_name, alias=ModelAlias.PRODUCTION)
    elif withdrawal == "approval":
        adapter.sync_release_metadata(replace(current, approved_by=None, approved_at=None))
    elif withdrawal == "card":
        repository.save_model_card(
            replace(repository.get_model_card(current.model_name, current.version), approvals=())
        )
    elif withdrawal == "validation":
        from models.shared_ml.validation import ValidationStatus

        existing = repository.get_validation_run(current.monitoring_config["validation_run_id"])
        repository.save_validation_run(replace(existing, status=ValidationStatus.FAILED))
    elif withdrawal == "backtest":
        from models.shared_ml.validation import ValidationStatus

        existing = repository.get_backtest_receipt(current.model_name, current.version)
        repository.save_backtest_receipt(replace(existing, status=ValidationStatus.FAILED))
    else:
        repository.delete_release_decision(decision.release_id)
    with pytest.raises((ProductionModelApprovalError, ProductionModelRegistryError)):
        with port.guard(receipt, tenant_id="local-test-tenant"):
            pytest.fail("withdrawn model reached publication callback")
    assert repository.get_release_revision(current.model_name) == 1


@pytest.mark.requires_live_env
@_postgres
def test_actual_inference_evidence_must_match_admitted_registry_and_artifact(released_pg_model):
    _, repository, _, runtime, port, decision = released_pg_model
    receipt = _read(port)
    model = repository.get_alias(decision.model_name, ModelAlias.PRODUCTION)
    binding = ModelBinding.from_model_version(
        "forecastops",
        model,
        artifact_sha256=receipt["artifact_sha256"],
        engine="synthetic-local-estimator",
        mlflow_run_id=receipt["mlflow_run_id"],
    )
    result = ModelInferenceResult(
        binding=binding,
        point=(1.0,),
        lower=(0.0,),
        upper=(2.0,),
        engine="synthetic-local-estimator",
        artifact_sha256=receipt["artifact_sha256"],
        model_metadata=receipt["model_metadata"],
        registry_version=receipt["registry_version"],
    )
    port.verify_inference(receipt, result)
    for changed in (
        replace(result, artifact_sha256="sha256:" + "b" * 64),
        replace(result, registry_version="another-registry-version"),
        replace(result, binding=replace(binding, approved_by="another-approver")),
        replace(result, model_metadata={}),
    ):
        with pytest.raises(CurrentModelAdmissionChanged):
            port.verify_inference(receipt, changed)
    with pytest.raises(ProductionModelRegistryError):
        with port.guard(receipt, tenant_id="foreign-tenant"):
            pass


@pytest.mark.parametrize("primitive", ["stage", "tag", "alias"])
@pytest.mark.requires_live_env
@_postgres
def test_actual_pg_held_publication_guard_blocks_remote_effect_before_writer_calls_client(
    released_pg_model, monkeypatch, primitive
):
    """Independent engine writer waits on the actual publication model key."""
    from models.shared_ml.registry import ModelStage
    from modules.learninghub.infrastructure.mlflow_adapter import MlflowRegistryAdapter
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    engine, repository, adapter, _, port, decision = released_pg_model
    receipt = _read(port)
    peer_engine = PostgresEngine(
        engine.database_url,
        bootstrap=False,
        validate_schema=False,
        min_pool_size=1,
        max_pool_size=2,
    )
    peer_repo = DurableLearningHubRepository(SqliteDocumentStore(peer_engine))
    peer = MlflowRegistryAdapter(peer_repo, tracking_uri=adapter.tracking_uri)
    started, effect = Event(), Event()
    method_name = {
        "stage": "transition_model_version_stage",
        "tag": "set_model_version_tag",
        "alias": "set_registered_model_alias",
    }[primitive]
    original = getattr(peer.client, method_name)

    def observe(*args, **kwargs):
        effect.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(peer.client, method_name, observe)

    def write():
        started.set()
        if primitive == "stage":
            peer._transition_mlflow_stage(
                decision.model_name, receipt["registry_version"], ModelStage.BLOCKED
            )
        elif primitive == "tag":
            peer._write_model_version_tags(
                decision.model_name, receipt["registry_version"], {PREFIX + "approved_by": ""}
            )
        else:
            peer.set_alias(
                model_name=decision.model_name, alias=ModelAlias.SHADOW, version=decision.to_version
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with engine.lock:
                with port.guard(receipt, tenant_id="local-test-tenant"):
                    future = executor.submit(write)
                    assert started.wait(2)
                    assert not effect.wait(0.05)
                    assert not future.done()
            future.result(5)
        assert effect.is_set()
        assert repository.get_release_revision(decision.model_name) == 1
        with pytest.raises(ProductionModelApprovalError):
            with port.guard(receipt, tenant_id="local-test-tenant"):
                pytest.fail("changed remote metadata reached publication")
    finally:
        peer_engine.close()


@pytest.mark.parametrize("same_tenant", [True, False])
@pytest.mark.requires_live_env
@_postgres
def test_actual_pg_report_group_serializes_version_allocation_with_tenant_independence(
    intake_blank_db, monkeypatch, same_tenant
):
    from modules.sitescore.domain.scoring import SiteScoreFeatureInput, score_site
    from shared.infrastructure.persistence import DurableSiteScoreRepository
    from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    engine_a = PostgresEngine(
        intake_blank_db.url(),
        bootstrap=True,
        validate_schema=False,
        min_pool_size=1,
        max_pool_size=2,
    )
    engine_b = PostgresEngine(
        intake_blank_db.url(),
        bootstrap=False,
        validate_schema=False,
        min_pool_size=1,
        max_pool_size=2,
    )
    repo_a = DurableSiteScoreRepository(
        TenantScopedDocumentStore(SqliteDocumentStore(engine_a), "local-report-tenant-a")
    )
    repo_b = DurableSiteScoreRepository(
        TenantScopedDocumentStore(
            SqliteDocumentStore(engine_b),
            "local-report-tenant-a" if same_tenant else "local-report-tenant-b",
        )
    )
    report = score_site(
        SiteScoreFeatureInput(
            candidate_site_id="local-report-candidate",
            heat_zone_score=80.0,
            monthly_rent=52000.0,
            area_ping=24.0,
            comparable_store_count=5,
        ),
        prediction_origin_time=datetime(2026, 7, 24, tzinfo=UTC),
    )
    first_count, second_count, continue_first = Event(), Event(), Event()
    count_a, count_b = repo_a._store.count_in_group, repo_b._store.count_in_group

    def pause_a(*args):
        value = count_a(*args)
        first_count.set()
        assert continue_first.wait(5)
        return value

    def observe_b(*args):
        second_count.set()
        return count_b(*args)

    monkeypatch.setattr(repo_a._store, "count_in_group", pause_a)
    monkeypatch.setattr(repo_b._store, "count_in_group", observe_b)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(repo_a.save_report, report)
            assert first_count.wait(2)
            second = executor.submit(repo_b.save_report, report)
            try:
                assert second_count.wait(0.1) is (not same_tenant)
            finally:
                continue_first.set()
            a, b = first.result(5), second.result(5)
        assert a.report_version == 1
        assert b.report_version == (2 if same_tenant else 1)
        assert a.report_id != b.report_id
        assert len(repo_a.history(report.candidate_site_id)) == (2 if same_tenant else 1)
        assert len(repo_b.history(report.candidate_site_id)) == (2 if same_tenant else 1)
    finally:
        continue_first.set()
        engine_a.close()
        engine_b.close()


@pytest.mark.parametrize("missing", ["reader", "guard", "engine"])
@pytest.mark.requires_live_env
@_postgres
def test_actual_pg_missing_reader_guard_or_same_engine_cannot_publish(
    released_pg_model, monkeypatch, missing
):
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    engine, repository, _, runtime, port, _ = released_pg_model
    receipt = _read(port)
    peer_engine = None
    if missing == "reader":
        monkeypatch.setattr(runtime, "read_admission", None)
    elif missing == "guard":
        monkeypatch.setattr(repository, "release_recovery_guard", None)
    else:
        peer_engine = PostgresEngine(
            engine.database_url,
            bootstrap=False,
            validate_schema=False,
            min_pool_size=1,
            max_pool_size=2,
        )
        port.engine = peer_engine
    try:
        with pytest.raises(ProductionModelRegistryError):
            with port.guard(receipt, tenant_id="local-test-tenant"):
                pytest.fail("missing publication authority reached callback")
    finally:
        if peer_engine is not None:
            peer_engine.close()


@pytest.mark.requires_live_env
@_postgres
def test_actual_pg_unconfigured_service_cannot_be_admitted_by_name_fallback(
    released_pg_model, monkeypatch
):
    _, _, _, runtime, port, _ = released_pg_model
    runtime.model_names.clear()
    monkeypatch.setattr(
        runtime,
        "read_admission",
        lambda **kwargs: pytest.fail("unconfigured service contacted registry"),
    )
    with pytest.raises(ProductionModelRegistryError):
        _read(port)


@pytest.mark.requires_live_env
@_postgres
def test_actual_pg_governed_sitescore_artifact_metadata_and_inference_match(
    intake_blank_db, tmp_path
):
    from shared.infrastructure.persistence.postgresql import PostgresEngine
    from tests.integration.test_production_model_runtime import (
        _live_sitescore_row,
        _registered_governed_runtime,
    )

    engine = PostgresEngine(
        intake_blank_db.url(),
        bootstrap=True,
        validate_schema=False,
        min_pool_size=1,
        max_pool_size=2,
    )
    repository = DurableLearningHubRepository(SqliteDocumentStore(engine))
    try:
        runtime, artifact = _registered_governed_runtime(
            tmp_path / "actual-local-artifact", repository
        )
        port = CurrentModelAdmissionPort(
            runtime=runtime,
            registry_repository=repository,
            engine=engine,
            tenant_id="local-sitescore-tenant",
        )
        receipt = port.read(service="sitescore", expected_feature_schema_version=SCHEMA)
        assert runtime._cache == {}
        assert artifact.exists()
        result = runtime.infer(
            service="sitescore",
            rows=[_live_sitescore_row()],
            expected_feature_schema_version=SCHEMA,
        )
        port.verify_inference(receipt, result)
        assert result.engine == "lightgbm.LGBMRegressor"
        assert len(result.point) == 1
        assert result.lower[0] <= result.point[0] <= result.upper[0]
        with engine.lock:
            with port.guard(receipt, tenant_id="local-sitescore-tenant"):
                assert repository.get_release_revision(receipt["model_name"]) == 1
    finally:
        engine.close()


@pytest.mark.requires_live_env
@_postgres
def test_actual_pg_held_publication_guard_blocks_backtest_receipt_withdrawal(
    released_pg_model, monkeypatch
):
    from models.shared_ml.validation import ValidationStatus
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    engine, repository, _, _, port, decision = released_pg_model
    receipt = _read(port)
    peer_engine = PostgresEngine(
        engine.database_url,
        bootstrap=False,
        validate_schema=False,
        min_pool_size=1,
        max_pool_size=2,
    )
    peer_repo = DurableLearningHubRepository(SqliteDocumentStore(peer_engine))
    current = peer_repo.get_backtest_receipt(decision.model_name, decision.to_version)
    started, effect = Event(), Event()
    original = peer_repo._store.put

    def observe(collection, *args, **kwargs):
        if collection == peer_repo._BACKTEST_RECEIPTS:
            effect.set()
        return original(collection, *args, **kwargs)

    monkeypatch.setattr(peer_repo._store, "put", observe)

    def withdraw():
        started.set()
        peer_repo.save_backtest_receipt(replace(current, status=ValidationStatus.FAILED))

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with engine.lock:
                with port.guard(receipt, tenant_id="local-test-tenant"):
                    future = executor.submit(withdraw)
                    assert started.wait(2)
                    assert not effect.wait(0.05)
                    assert not future.done()
            future.result(5)
        assert effect.is_set()
        assert repository.get_release_revision(decision.model_name) == 1
        with pytest.raises(ProductionModelApprovalError):
            with port.guard(receipt, tenant_id="local-test-tenant"):
                pytest.fail("withdrawn backtest reached publication callback")
    finally:
        peer_engine.close()
