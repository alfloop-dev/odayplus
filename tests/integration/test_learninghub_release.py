from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from unittest.mock import patch

import pytest

from models.shared_ml import (
    MetricThreshold,
    ModelAlias,
    ModelCard,
    ModelCardApproval,
    ModelRiskLevel,
    ModelStage,
    ModelVersion,
    SegmentMetric,
)
from modules.learninghub import (
    InMemoryLearningHubRepository,
    LearningHubError,
    LearningHubService,
    MlflowRegistryAdapter,
    ModelReleaseDecision,
    MonitorStatus,
    RecommendedAction,
    ReleaseSagaState,
    ReleaseType,
    run_learninghub_release,
    run_learninghub_release_monitor,
)
from shared.audit import InMemoryAuditLog
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.infrastructure.persistence import (
    DurableAuditLog,
    DurableLearningHubRepository,
    SqliteDocumentStore,
    SqliteEngine,
)

SNAPSHOT_TIME = datetime(2026, 6, 27, 8, 0, tzinfo=UTC)
PREDICTION_TIME = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)


def _rows() -> list[dict[str, object]]:
    return [
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "store-machine-timeseries-view-v1",
            "entity_id": "store-001",
            "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "source_snapshot_ids": ["pos-20260627", "machine-20260627"],
            "labels": {"w4_revenue": 410_000},
            "label_maturity_time": SNAPSHOT_TIME.isoformat(),
            "features": {"event_time": SNAPSHOT_TIME.isoformat(), "revenue_lag_7d": 92_000},
        },
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "store-machine-timeseries-view-v1",
            "entity_id": "store-002",
            "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "source_snapshot_ids": ["pos-20260627"],
            "labels": {"w4_revenue": 380_000},
            "label_maturity_time": SNAPSHOT_TIME.isoformat(),
            "features": {"event_time": SNAPSHOT_TIME.isoformat(), "revenue_lag_7d": 88_000},
        },
    ]


def _model_version(version: str, dataset_snapshot_id: str) -> ModelVersion:
    return ModelVersion(
        model_name="forecast_revenue_interval",
        version=version,
        artifact_uri=f"gs://oday-artifacts/models/forecast_revenue_interval/{version}/model",
        dataset_snapshot_id=dataset_snapshot_id,
        feature_schema_version="store-machine-timeseries-view-v1",
        label_version="forecast-w4-revenue-v1",
        metrics={"w4_smape": 0.11, "p80_coverage": 0.82},
        run_id=f"mlflow-run-{version}",
        git_sha="abc1234",
    )


def _model_card(version: str, dataset_snapshot_id: str, validation_run_id: str) -> ModelCard:
    return ModelCard(
        model_name="forecast_revenue_interval",
        model_version=version,
        owner="ml-platform",
        risk_level=ModelRiskLevel.R3,
        intended_use="ForecastOps 4/8/12/24 week revenue interval input",
        not_intended_use="Direct store closure, pricing, or campaign execution",
        dataset_snapshot_id=dataset_snapshot_id,
        validation_run_id=validation_run_id,
        feature_set_id="fs_forecastops_v1",
        label_set_id="ls_forecastops_w4_v1",
        training_period="2026-01-01/2026-05-31",
        validation_period="2026-06-01/2026-06-27",
        algorithm="seasonal_baseline_plus_gradient_boosting",
        baseline="seasonal_naive_v1",
        metrics_summary={"w4_smape": 0.11, "p80_coverage": 0.82},
        segment_metrics=({"segment_name": "region", "segment_value": "north", "w4_smape": 0.10},),
        calibration_summary={"p80_coverage": 0.82},
        explainability_method="feature_importance",
        limitations=("synthetic fixture validation only",),
        known_biases=("low volume stores have wider error bands",),
        rollback_conditions=(
            "p80_coverage < 0.75 for 2 consecutive monitoring windows",
            "red_alert_precision drops below approved threshold",
        ),
        approvals=(ModelCardApproval(approver="reviewer-a", role="model-review-board"),),
    )


def _prepare_candidate(service: LearningHubService, version: str) -> ModelVersion:
    snapshot = service.register_dataset_snapshot(
        _rows(), dataset_snapshot_id=f"forecast-training-{version}"
    )
    validation = service.validate_candidate(
        model_name="forecast_revenue_interval",
        model_version=version,
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        metrics={"w4_smape": 0.11, "p80_coverage": 0.82},
        baseline_metrics={"w4_smape": 0.15, "p80_coverage": 0.78},
        thresholds=(
            MetricThreshold("w4_smape", max_value=0.12, warning_max_value=0.115),
            MetricThreshold("p80_coverage", min_value=0.80, warning_min_value=0.81),
        ),
        segment_metrics=(
            SegmentMetric(
                segment_name="region",
                segment_value="north",
                metrics={"w4_smape": 0.10},
                record_count=1,
            ),
        ),
        calibration_summary={"p80_coverage": 0.82},
    )
    assert validation.passed
    return service.register_model_version(
        model_version=_model_version(version, snapshot.dataset_snapshot_id),
        model_card=_model_card(version, snapshot.dataset_snapshot_id, validation.validation_run_id),
        validation_run=validation,
    )


def _request_release(service: LearningHubService, **kwargs) -> ModelReleaseDecision:
    kwargs.setdefault(
        "expected_release_revision",
        service.repository.get_release_revision(str(kwargs["model_name"])),
    )
    kwargs.setdefault("idempotency_key", str(kwargs["approval_id"]))
    return service.request_release(**kwargs)


def test_learninghub_validates_releases_and_rolls_back_model_aliases() -> None:
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    service = LearningHubService(
        repository=repository,
        registry=MlflowRegistryAdapter(repository),
        audit_log=audit_log,
    )
    v1 = _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")

    shadow = _request_release(
        service,
        model_name=v1.model_name,
        version=v1.version,
        release_type=ReleaseType.SHADOW,
        reason="run shadow predictions before promotion",
        approval_id="approval-shadow-001",
        rollback_target=None,
        monitoring_window="24h",
        success_criteria=("schema valid", "latency normal"),
        fail_criteria=("missing model card",),
        affected_modules=("ForecastOps",),
        requested_by="ml-owner",
        correlation_id="corr-learninghub-1",
    )
    assert shadow.release_type is ReleaseType.SHADOW
    assert repository.get_alias(v1.model_name, ModelAlias.SHADOW).version == "1.0.0"

    _request_release(
        service,
        model_name=v1.model_name,
        version=v1.version,
        release_type=ReleaseType.FULL,
        reason="promote validated champion",
        approval_id="approval-full-001",
        rollback_target=v1.version,
        monitoring_window="48h",
        success_criteria=("smoke prediction passed",),
        fail_criteria=("p80 coverage below threshold",),
        affected_modules=("ForecastOps", "InterventionOps"),
        requested_by="ml-owner",
        correlation_id="corr-learninghub-2",
    )
    assert repository.get_alias(v1.model_name, ModelAlias.PRODUCTION).version == "1.0.0"
    assert repository.get_alias(v1.model_name, ModelAlias.CHAMPION).version == "1.0.0"

    _request_release(
        service,
        model_name=v2.model_name,
        version=v2.version,
        release_type=ReleaseType.FULL,
        reason="better error and coverage than champion",
        approval_id="approval-full-002",
        rollback_target=v1.version,
        monitoring_window="48h",
        success_criteria=("smoke prediction passed", "model_version present in output"),
        fail_criteria=("coverage regression",),
        affected_modules=("ForecastOps",),
        requested_by="ml-owner",
        correlation_id="corr-learninghub-3",
    )
    assert repository.get_alias(v2.model_name, ModelAlias.PRODUCTION).version == "1.1.0"
    assert repository.get_alias(v2.model_name, ModelAlias.PREVIOUS_PRODUCTION).version == "1.0.0"
    assert repository.get_model_version(v2.model_name, "1.1.0").stage is ModelStage.PRODUCTION

    rollback = run_learninghub_release(
        {
            "model_name": v2.model_name,
            "version": v2.version,
            "release_type": "ROLLBACK",
            "reason": "coverage watch window breached",
            "approval_id": "approval-rollback-001",
            "rollback_target": v1.version,
            "monitoring_window": "immediate",
            "success_criteria": ["alias points at previous model"],
            "fail_criteria": ["smoke prediction fails"],
            "affected_modules": ["ForecastOps"],
            "requested_by": "on-call",
            "correlation_id": "corr-learninghub-4",
            "expected_release_revision": repository.get_release_revision(v2.model_name),
            "idempotency_key": "approval-rollback-001",
        },
        service=service,
    )
    assert rollback.release_type is ReleaseType.ROLLBACK
    assert rollback.from_version == "1.1.0"
    assert rollback.to_version == "1.0.0"
    assert repository.get_alias(v2.model_name, ModelAlias.PRODUCTION).version == "1.0.0"
    assert repository.get_model_version(v2.model_name, "1.1.0").stage is ModelStage.ROLLED_BACK
    assert any(event.action == "rollback" for event in audit_log.list_events())


def test_governed_release_invokes_remote_mlflow_alias_updates() -> None:
    repository = InMemoryLearningHubRepository()
    registry = MlflowRegistryAdapter(repository)
    service = LearningHubService(
        repository=repository,
        registry=registry,
        audit_log=InMemoryAuditLog(),
    )
    candidate = _prepare_candidate(service, "1.0.0")
    client = registry._require_client()

    with patch.object(
        client,
        "set_registered_model_alias",
        wraps=client.set_registered_model_alias,
    ) as remote_set_alias:
        _request_release(
            service,
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.SHADOW,
            reason="validate shadow alias projection",
            approval_id="approval-shadow-remote-001",
            rollback_target=None,
            monitoring_window="24h",
            success_criteria=("remote shadow alias resolves",),
            fail_criteria=("remote shadow alias is absent",),
        )
        _request_release(
            service,
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.FULL,
            reason="validate production alias projection",
            approval_id="approval-full-remote-001",
            rollback_target=candidate.version,
            monitoring_window="48h",
            success_criteria=("remote production alias resolves",),
            fail_criteria=("remote production alias is absent",),
        )

    invoked_aliases = {
        invocation.args[1]
        for invocation in remote_set_alias.call_args_list
        if invocation.args[0] == candidate.model_name
    }
    assert {
        ModelAlias.SHADOW.value,
        ModelAlias.PRODUCTION.value,
        ModelAlias.CHAMPION.value,
    } <= invoked_aliases
    assert (
        registry.get_by_alias(
            model_name=candidate.model_name,
            alias=ModelAlias.SHADOW,
        ).version
        == candidate.version
    )
    assert (
        registry.get_by_alias(
            model_name=candidate.model_name,
            alias=ModelAlias.PRODUCTION,
        ).version
        == candidate.version
    )


def test_full_alias_failure_restores_aliases_stages_and_model_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryLearningHubRepository()
    registry = MlflowRegistryAdapter(repository)
    service = LearningHubService(
        repository=repository,
        registry=registry,
        audit_log=InMemoryAuditLog(),
    )
    v1 = _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")
    _release_full(service, version=v1.version, rollback_target=v1.version)
    v1_before = repository.get_model_version(v1.model_name, v1.version)
    v2_before = repository.get_model_version(v2.model_name, v2.version)
    decisions_before = tuple(repository.list_release_decisions())
    durable_set_alias = repository.set_alias
    failure_injected = False

    def fail_after_champion_write(
        model_name: str,
        alias: ModelAlias,
        version: str,
    ) -> ModelVersion:
        nonlocal failure_injected
        result = durable_set_alias(model_name, alias, version)
        if alias is ModelAlias.CHAMPION and not failure_injected:
            failure_injected = True
            raise RuntimeError("injected durable alias failure")
        return result

    monkeypatch.setattr(repository, "set_alias", fail_after_champion_write)

    with pytest.raises(
        LearningHubError,
        match="aliases, remote stages, and model metadata were compensated",
    ):
        _request_release(
            service,
            model_name=v2.model_name,
            version=v2.version,
            release_type=ReleaseType.FULL,
            reason="exercise alias compensation",
            approval_id="approval-full-compensation-001",
            rollback_target=v1.version,
            monitoring_window="48h",
            success_criteria=("aliases remain consistent",),
            fail_criteria=("durable alias write fails",),
        )

    assert failure_injected
    assert repository.get_model_version(v1.model_name, v1.version) == v1_before
    assert repository.get_model_version(v2.model_name, v2.version) == v2_before
    assert (
        registry._to_domain(registry._require_model_version(v1.model_name, v1.version)).stage
        is v1_before.stage
    )
    assert (
        registry._to_domain(registry._require_model_version(v2.model_name, v2.version)).stage
        is v2_before.stage
    )
    assert (
        repository.get_alias(v1.model_name, ModelAlias.PRODUCTION).version == v1.version
    )
    assert repository.get_alias(v1.model_name, ModelAlias.CHAMPION).version == v1.version
    assert repository.get_alias(v1.model_name, ModelAlias.PREVIOUS_PRODUCTION) is None
    assert (
        registry.get_by_alias(
            model_name=v1.model_name,
            alias=ModelAlias.PRODUCTION,
        ).version
        == v1.version
    )
    assert (
        registry.get_by_alias(
            model_name=v1.model_name,
            alias=ModelAlias.CHAMPION,
        ).version
        == v1.version
    )
    assert (
        registry.get_by_alias(
            model_name=v1.model_name,
            alias=ModelAlias.PREVIOUS_PRODUCTION,
        )
        is None
    )
    assert tuple(repository.list_release_decisions()) == decisions_before


def test_rollback_alias_failure_restores_aliases_stages_and_model_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryLearningHubRepository()
    registry = MlflowRegistryAdapter(repository)
    service = LearningHubService(
        repository=repository,
        registry=registry,
        audit_log=InMemoryAuditLog(),
    )
    v1 = _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")
    _release_full(service, version=v1.version, rollback_target=v1.version)
    _release_full(service, version=v2.version, rollback_target=v1.version)
    v1_before = repository.get_model_version(v1.model_name, v1.version)
    v2_before = repository.get_model_version(v2.model_name, v2.version)
    decisions_before = tuple(repository.list_release_decisions())
    durable_set_alias = repository.set_alias
    failure_injected = False

    def fail_after_champion_write(
        model_name: str,
        alias: ModelAlias,
        version: str,
    ) -> ModelVersion:
        nonlocal failure_injected
        result = durable_set_alias(model_name, alias, version)
        if (
            alias is ModelAlias.CHAMPION
            and version == v1.version
            and not failure_injected
        ):
            failure_injected = True
            raise RuntimeError("injected durable rollback alias failure")
        return result

    monkeypatch.setattr(repository, "set_alias", fail_after_champion_write)

    with pytest.raises(
        LearningHubError,
        match="aliases, remote stages, and model metadata were compensated",
    ):
        _request_release(
            service,
            model_name=v2.model_name,
            version=v2.version,
            release_type=ReleaseType.ROLLBACK,
            reason="exercise rollback compensation",
            approval_id="approval-rollback-compensation-001",
            rollback_target=v1.version,
            monitoring_window="immediate",
            success_criteria=("prior production state remains consistent",),
            fail_criteria=("durable rollback alias write fails",),
        )

    assert failure_injected
    assert repository.get_model_version(v1.model_name, v1.version) == v1_before
    assert repository.get_model_version(v2.model_name, v2.version) == v2_before
    assert (
        registry._to_domain(registry._require_model_version(v1.model_name, v1.version)).stage
        is v1_before.stage
    )
    assert (
        registry._to_domain(registry._require_model_version(v2.model_name, v2.version)).stage
        is v2_before.stage
    )
    for alias, expected_version in (
        (ModelAlias.PRODUCTION, v2.version),
        (ModelAlias.CHAMPION, v2.version),
        (ModelAlias.PREVIOUS_PRODUCTION, v1.version),
    ):
        assert repository.get_alias(v1.model_name, alias).version == expected_version
        assert (
            registry.get_by_alias(model_name=v1.model_name, alias=alias).version
            == expected_version
        )
    assert tuple(repository.list_release_decisions()) == decisions_before


def test_audit_failure_compensates_release_state_and_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    registry = MlflowRegistryAdapter(repository)
    service = LearningHubService(
        repository=repository,
        registry=registry,
        audit_log=audit_log,
    )
    v1 = _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")
    _release_full(service, version=v1.version, rollback_target=v1.version)
    v1_before = repository.get_model_version(v1.model_name, v1.version)
    v2_before = repository.get_model_version(v2.model_name, v2.version)
    decisions_before = tuple(repository.list_release_decisions())
    revision_before = repository.get_release_revision(v1.model_name)
    durable_record = audit_log.record
    failure_injected = False

    def fail_release_audit_once(event):
        nonlocal failure_injected
        recorded = durable_record(event)
        if event.event_type == "learninghub.model_release.v1" and not failure_injected:
            failure_injected = True
            raise RuntimeError("injected audit write failure")
        return recorded

    monkeypatch.setattr(audit_log, "record", fail_release_audit_once)

    with pytest.raises(LearningHubError, match="durable receipt, aliases"):
        _release_full(service, version=v2.version, rollback_target=v1.version)

    assert failure_injected
    assert repository.get_release_revision(v1.model_name) == revision_before + 1
    assert tuple(repository.list_release_decisions()) == decisions_before
    assert repository.get_model_version(v1.model_name, v1.version) == v1_before
    assert repository.get_model_version(v2.model_name, v2.version) == v2_before
    assert repository.get_alias(v1.model_name, ModelAlias.PRODUCTION).version == v1.version
    assert registry.get_by_alias(
        model_name=v1.model_name,
        alias=ModelAlias.PRODUCTION,
    ).version == v1.version
    assert any(
        event.event_type == "learninghub.model_release_compensation.v1"
        for event in audit_log.list_events()
    )


def test_decision_failure_after_write_removes_receipt_and_compensates_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    registry = MlflowRegistryAdapter(repository)
    service = LearningHubService(
        repository=repository,
        registry=registry,
        audit_log=audit_log,
    )
    v1 = _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")
    _release_full(service, version=v1.version, rollback_target=v1.version)
    v1_before = repository.get_model_version(v1.model_name, v1.version)
    v2_before = repository.get_model_version(v2.model_name, v2.version)
    decisions_before = tuple(repository.list_release_decisions())
    revision_before = repository.get_release_revision(v1.model_name)
    durable_save_decision = repository.save_release_decision
    failure_injected = False

    def fail_after_decision_write(decision):
        nonlocal failure_injected
        saved = durable_save_decision(decision)
        if decision.to_version == v2.version and not failure_injected:
            failure_injected = True
            raise RuntimeError("injected decision receipt failure")
        return saved

    monkeypatch.setattr(repository, "save_release_decision", fail_after_decision_write)

    with pytest.raises(LearningHubError, match="durable receipt, aliases"):
        _release_full(service, version=v2.version, rollback_target=v1.version)

    assert failure_injected
    assert repository.get_release_revision(v1.model_name) == revision_before + 1
    assert tuple(repository.list_release_decisions()) == decisions_before
    assert repository.get_model_version(v1.model_name, v1.version) == v1_before
    assert repository.get_model_version(v2.model_name, v2.version) == v2_before
    assert repository.get_alias(v1.model_name, ModelAlias.PRODUCTION).version == v1.version
    assert registry.get_by_alias(
        model_name=v1.model_name,
        alias=ModelAlias.PRODUCTION,
    ).version == v1.version
    compensation_events = [
        event
        for event in audit_log.list_events()
        if event.event_type == "learninghub.model_release_compensation.v1"
    ]
    assert len(compensation_events) == 1
    assert compensation_events[0].metadata["release_revision"] == revision_before + 1


def test_alias_read_is_side_effect_free_and_reconcile_is_explicit() -> None:
    repository = InMemoryLearningHubRepository()
    registry = MlflowRegistryAdapter(repository)
    service = LearningHubService(
        repository=repository,
        registry=registry,
        audit_log=InMemoryAuditLog(),
    )
    v1 = _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")
    repository.set_alias(v1.model_name, ModelAlias.PRODUCTION, v1.version)
    remote_v2 = registry._require_model_version(v2.model_name, v2.version)
    registry._require_client().set_registered_model_alias(
        v2.model_name,
        ModelAlias.PRODUCTION.value,
        str(remote_v2.version),
    )

    remote_read = registry.get_by_alias(
        model_name=v1.model_name,
        alias=ModelAlias.PRODUCTION,
    )
    assert remote_read.version == v2.version
    assert repository.get_alias(v1.model_name, ModelAlias.PRODUCTION).version == v1.version

    reconciled_remote = service.reconcile_alias(
        model_name=v1.model_name,
        alias=ModelAlias.PRODUCTION,
        authority="remote",
        reason="repair durable projection from MLflow",
        requested_by="registry-operator",
        expected_release_revision=0,
        idempotency_key="reconcile-remote-001",
    )
    assert reconciled_remote.resolved_version == v2.version
    assert repository.get_alias(v1.model_name, ModelAlias.PRODUCTION).version == v2.version
    replayed_remote = service.reconcile_alias(
        model_name=v1.model_name,
        alias=ModelAlias.PRODUCTION,
        authority="remote",
        reason="repair durable projection from MLflow",
        requested_by="registry-operator",
        expected_release_revision=0,
        idempotency_key="reconcile-remote-001",
    )
    assert replayed_remote.release_id == reconciled_remote.release_id
    with pytest.raises(LearningHubError, match="different release command"):
        service.reconcile_alias(
            model_name=v1.model_name,
            alias=ModelAlias.PRODUCTION,
            authority="remote",
            reason="different reconciliation request",
            requested_by="registry-operator",
            expected_release_revision=0,
            idempotency_key="reconcile-remote-001",
        )

    repository.set_alias(v1.model_name, ModelAlias.PRODUCTION, v1.version)
    reconciled_durable = service.reconcile_alias(
        model_name=v1.model_name,
        alias=ModelAlias.PRODUCTION,
        authority="durable",
        reason="repair MLflow projection from durable registry",
        requested_by="registry-operator",
        expected_release_revision=1,
        idempotency_key="reconcile-durable-001",
    )
    assert reconciled_durable.resolved_version == v1.version
    assert registry.get_by_alias(
        model_name=v1.model_name,
        alias=ModelAlias.PRODUCTION,
    ).version == v1.version
    events = [
        event
        for event in service.audit_log.list_events()
        if event.event_type == "learninghub.alias_reconciliation.v1"
    ]
    assert len(events) == 2


def test_governed_alias_reconciliation_compensates_remote_and_durable_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryLearningHubRepository()
    registry = MlflowRegistryAdapter(repository)
    service = LearningHubService(
        repository=repository,
        registry=registry,
        audit_log=InMemoryAuditLog(),
    )
    v1 = _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")
    repository.set_alias(v1.model_name, ModelAlias.PRODUCTION, v1.version)
    remote_v2 = registry._require_model_version(v2.model_name, v2.version)
    registry._require_client().set_registered_model_alias(
        v2.model_name,
        ModelAlias.PRODUCTION.value,
        str(remote_v2.version),
    )
    reconcile = registry._reconcile_alias_unsafe

    def fail_after_reconciliation(**kwargs):
        reconcile(**kwargs)
        raise RuntimeError("injected reconciliation receipt failure")

    monkeypatch.setattr(registry, "_reconcile_alias_unsafe", fail_after_reconciliation)
    with pytest.raises(LearningHubError, match="failed and was compensated"):
        service.reconcile_alias(
            model_name=v1.model_name,
            alias=ModelAlias.PRODUCTION,
            authority="remote",
            reason="exercise governed compensation",
            requested_by="registry-operator",
            expected_release_revision=0,
            idempotency_key="reconcile-compensation-001",
        )

    assert repository.get_alias(
        v1.model_name,
        ModelAlias.PRODUCTION,
    ).version == v1.version
    assert registry.get_by_alias(
        model_name=v1.model_name,
        alias=ModelAlias.PRODUCTION,
    ).version == v2.version
    saga = repository.get_release_saga_by_idempotency(
        v1.model_name,
        "reconcile-compensation-001",
    )
    assert saga is not None
    assert saga.state is ReleaseSagaState.COMPENSATED


def test_release_idempotency_revision_global_scope_and_approval_tags() -> None:
    repository = InMemoryLearningHubRepository()
    registry = MlflowRegistryAdapter(repository)
    service = LearningHubService(
        repository=repository,
        registry=registry,
        audit_log=InMemoryAuditLog(),
    )
    v1 = _prepare_candidate(service, "1.0.0")
    command = {
        "model_name": v1.model_name,
        "version": v1.version,
        "release_type": ReleaseType.FULL,
        "reason": "governed global promotion",
        "approval_id": "approval-idempotent-001",
        "rollback_target": v1.version,
        "monitoring_window": "48h",
        "success_criteria": ("runtime resolves approval",),
        "fail_criteria": ("approval tag missing",),
        "approved_by": "reviewer-a",
        "expected_release_revision": 0,
        "idempotency_key": "release-idempotent-001",
    }

    first = service.request_release(**command)
    replay = service.request_release(
        **{**command, "correlation_id": "retry-after-lost-response"}
    )

    assert replay.release_id == first.release_id
    assert len(repository.list_release_decisions()) == 1
    assert repository.get_release_revision(v1.model_name) == 1
    runtime_version = registry.get_by_alias(
        model_name=v1.model_name,
        alias=ModelAlias.PRODUCTION,
    )
    assert runtime_version is not None
    assert runtime_version.approved_by == "reviewer-a"
    assert runtime_version.approved_at is not None
    assert runtime_version.monitoring_config["release_scope"] == "global"

    with pytest.raises(LearningHubError, match="different release command"):
        service.request_release(**{**command, "reason": "different command"})
    with pytest.raises(LearningHubError, match="production model aliases are global"):
        service.request_release(
            **{
                **command,
                "idempotency_key": "tenant-release-001",
                "release_scope": "tenant",
                "tenant_id": "tenant-a",
            }
        )


def test_release_worker_requires_revision_and_idempotency_binding() -> None:
    service = LearningHubService()
    payload = {
        "model_name": "forecast_revenue_interval",
        "version": "1.0.0",
        "release_type": "FULL",
        "reason": "must remain bound",
        "approval_id": "approval-worker-binding-001",
        "rollback_target": "1.0.0",
    }
    with pytest.raises(ValueError, match="expected_release_revision"):
        run_learninghub_release(payload, service=service)
    with pytest.raises(ValueError, match="idempotency_key"):
        run_learninghub_release(
            {**payload, "expected_release_revision": 0},
            service=service,
        )


def test_rollback_rejects_stale_command_and_produces_one_production_version() -> None:
    repository = InMemoryLearningHubRepository()
    registry = MlflowRegistryAdapter(repository)
    service = LearningHubService(
        repository=repository,
        registry=registry,
        audit_log=InMemoryAuditLog(),
    )
    v1 = _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")
    _release_full(service, version=v1.version, rollback_target=v1.version)
    _release_full(service, version=v2.version, rollback_target=v1.version)

    with pytest.raises(
        LearningHubError,
        match="rollback command version must equal current production",
    ):
        _request_release(
            service,
            model_name=v1.model_name,
            version=v1.version,
            release_type=ReleaseType.ROLLBACK,
            reason="stale rollback command",
            approval_id="approval-stale-rollback-001",
            rollback_target=v1.version,
            monitoring_window="immediate",
            success_criteria=("never executes",),
            fail_criteria=("stale command accepted",),
        )

    receipt = _request_release(
        service,
        model_name=v2.model_name,
        version=v2.version,
        release_type=ReleaseType.ROLLBACK,
        reason="restore approved previous production",
        approval_id="approval-rollback-correct-001",
        rollback_target=v1.version,
        monitoring_window="immediate",
        success_criteria=("one production version",),
        fail_criteria=("multiple production versions",),
        approved_by="rollback-reviewer",
    )

    assert receipt.from_version == v2.version
    assert receipt.to_version == v1.version
    assert receipt.dataset_snapshot_id == v1.dataset_snapshot_id
    production_versions = [
        version
        for version in repository.list_model_versions(v1.model_name)
        if version.stage is ModelStage.PRODUCTION
    ]
    assert [version.version for version in production_versions] == [v1.version]
    assert registry.get_by_alias(
        model_name=v1.model_name,
        alias=ModelAlias.PRODUCTION,
    ).approved_by == "rollback-reviewer"


class _ProcessInterrupted(BaseException):
    pass


@pytest.mark.parametrize(
    ("crash_state", "expected_state"),
    (
        (ReleaseSagaState.INTENT_RECORDED, ReleaseSagaState.COMPENSATED),
        (ReleaseSagaState.MODEL_STATE_APPLIED, ReleaseSagaState.COMPENSATED),
        (ReleaseSagaState.ALIASES_APPLIED, ReleaseSagaState.COMPENSATED),
        (ReleaseSagaState.AUDIT_RECORDED, ReleaseSagaState.COMPENSATED),
        (ReleaseSagaState.RECEIPT_RECORDED, ReleaseSagaState.COMPLETED),
    ),
)
def test_release_saga_survives_process_interruption_and_recovers(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    crash_state: ReleaseSagaState,
    expected_state: ReleaseSagaState,
) -> None:
    database = tmp_path / f"release-{crash_state.value}.sqlite3"
    worm_root = tmp_path / f"worm-{crash_state.value}"
    engine = SqliteEngine(database)
    repository = DurableLearningHubRepository(SqliteDocumentStore(engine))
    service = LearningHubService(
        repository=repository,
        registry=MlflowRegistryAdapter(repository),
        audit_log=DurableAuditLog(
            engine,
            worm_sink=LocalAppendOnlyWormSink(worm_root),
        ),
    )
    v1 = _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")
    _release_full(service, version=v1.version, rollback_target=v1.version)
    save_saga = repository.save_release_saga
    crashed = False

    def interrupt_after_persist(saga):
        nonlocal crashed
        saved = save_saga(saga)
        if (
            saga.command.get("version") == v2.version
            and saga.state is crash_state
            and (crash_state is not ReleaseSagaState.INTENT_RECORDED or saga.attempt == 1)
            and not crashed
        ):
            crashed = True
            raise _ProcessInterrupted(crash_state.value)
        return saved

    monkeypatch.setattr(repository, "save_release_saga", interrupt_after_persist)
    with pytest.raises(_ProcessInterrupted):
        _request_release(
            service,
            model_name=v2.model_name,
            version=v2.version,
            release_type=ReleaseType.FULL,
            reason=f"crash after {crash_state.value}",
            approval_id=f"approval-crash-{crash_state.value}",
            rollback_target=v1.version,
            monitoring_window="48h",
            success_criteria=("recover deterministically",),
            fail_criteria=("orphaned remote mutation",),
        )
    assert crashed
    release_id = repository.list_release_sagas(
        v2.model_name,
        include_terminal=False,
    )[0].release_id
    engine.close()

    reopened_engine = SqliteEngine(database)
    reopened_repository = DurableLearningHubRepository(
        SqliteDocumentStore(reopened_engine)
    )
    reopened_service = LearningHubService(
        repository=reopened_repository,
        registry=MlflowRegistryAdapter(reopened_repository),
        audit_log=DurableAuditLog(
            reopened_engine,
            worm_sink=LocalAppendOnlyWormSink(worm_root),
        ),
    )
    try:
        recovered = reopened_service.recover_incomplete_releases(
            release_id=release_id,
        )
        assert recovered[0].state is expected_state
        production = reopened_repository.get_alias(
            v1.model_name,
            ModelAlias.PRODUCTION,
        )
        assert production is not None
        if expected_state is ReleaseSagaState.COMPLETED:
            assert production.version == v2.version
            assert reopened_repository.get_release_decision(release_id) is not None
        else:
            assert production.version == v1.version
            assert reopened_repository.get_release_decision(release_id) is None
        assert reopened_service.audit_log.verify_chain().ok
        assert list(worm_root.rglob("*.json"))
    finally:
        reopened_engine.close()


def test_compensation_failure_is_persisted_and_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    service = LearningHubService(
        repository=repository,
        registry=MlflowRegistryAdapter(repository),
        audit_log=audit_log,
    )
    v1 = _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")
    _release_full(service, version=v1.version, rollback_target=v1.version)

    monkeypatch.setattr(
        service,
        "_restore_release_state",
        lambda **_: ["remote-stage:1.1.0=RuntimeError: restore failed"],
    )
    monkeypatch.setattr(
        repository,
        "save_release_decision",
        lambda _: (_ for _ in ()).throw(RuntimeError("receipt write failed")),
    )
    with pytest.raises(LearningHubError, match="compensation is incomplete"):
        _release_full(service, version=v2.version, rollback_target=v1.version)

    saga = repository.list_release_sagas(v1.model_name)[-1]
    assert saga.state is ReleaseSagaState.COMPENSATION_FAILED
    events = [
        event
        for event in audit_log.list_events()
        if event.event_type == "learninghub.model_release_compensation.v1"
    ]
    assert events[-1].outcome == "compensation_failed"
    assert events[-1].metadata["compensation_errors"]


def test_concurrent_releases_use_per_model_cas_without_cross_compensation() -> None:
    repository = InMemoryLearningHubRepository()
    registry = MlflowRegistryAdapter(repository)
    service = LearningHubService(
        repository=repository,
        registry=registry,
        audit_log=InMemoryAuditLog(),
    )
    candidates = (
        _prepare_candidate(service, "1.0.0"),
        _prepare_candidate(service, "1.1.0"),
    )
    ready = Barrier(2)

    def release(candidate: ModelVersion):
        ready.wait()
        try:
            return service.request_release(
                model_name=candidate.model_name,
                version=candidate.version,
                release_type=ReleaseType.FULL,
                reason=f"concurrent release {candidate.version}",
                approval_id=f"approval-concurrent-{candidate.version}",
                rollback_target=candidate.version,
                monitoring_window="48h",
                success_criteria=("single CAS winner",),
                fail_criteria=("release revision conflict",),
                expected_release_revision=0,
                idempotency_key=f"concurrent-release-{candidate.version}",
            )
        except LearningHubError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(release, candidates))

    decisions = [result for result in results if isinstance(result, ModelReleaseDecision)]
    conflicts = [result for result in results if isinstance(result, LearningHubError)]
    assert len(decisions) == 1
    assert len(conflicts) == 1
    assert "release revision conflict" in str(conflicts[0])
    winner = decisions[0]
    loser = next(candidate for candidate in candidates if candidate.version != winner.to_version)
    assert winner.release_revision == 1
    assert repository.get_release_revision(winner.model_name) == 1
    assert repository.list_release_decisions() == decisions
    assert repository.get_alias(winner.model_name, ModelAlias.PRODUCTION).version == (
        winner.to_version
    )
    assert repository.get_alias(winner.model_name, ModelAlias.CHAMPION).version == (
        winner.to_version
    )
    assert registry.get_by_alias(
        model_name=winner.model_name,
        alias=ModelAlias.PRODUCTION,
    ).version == winner.to_version
    assert repository.get_model_version(winner.model_name, loser.version).stage is ModelStage.DEV


def test_durable_release_revision_rolls_back_on_failure_and_survives_restart(
    tmp_path,
) -> None:
    database = tmp_path / "learninghub-release-cas.sqlite3"
    engine = SqliteEngine(database)
    repository = DurableLearningHubRepository(SqliteDocumentStore(engine))
    try:
        with pytest.raises(RuntimeError, match="injected guarded failure"):
            with repository.release_guard(
                "forecast_revenue_interval",
                expected_revision=0,
            ) as reserved_revision:
                assert reserved_revision == 1
                raise RuntimeError("injected guarded failure")
        assert repository.get_release_revision("forecast_revenue_interval") == 0

        with repository.release_guard(
            "forecast_revenue_interval",
            expected_revision=0,
        ) as reserved_revision:
            assert reserved_revision == 1
        assert repository.get_release_revision("forecast_revenue_interval") == 1
    finally:
        engine.close()

    reopened_engine = SqliteEngine(database)
    try:
        reopened = DurableLearningHubRepository(SqliteDocumentStore(reopened_engine))
        assert reopened.get_release_revision("forecast_revenue_interval") == 1
    finally:
        reopened_engine.close()


def _release_full(service: LearningHubService, *, version: str, rollback_target: str):
    return _request_release(
        service,
        model_name="forecast_revenue_interval",
        version=version,
        release_type=ReleaseType.FULL,
        reason="promote validated champion",
        approval_id=f"approval-full-{version}",
        rollback_target=rollback_target,
        monitoring_window="48h",
        success_criteria=("p80_coverage >= 0.80",),
        fail_criteria=("p80_coverage < 0.75",),
        affected_modules=("ForecastOps",),
        requested_by="ml-owner",
        correlation_id="corr-monitor-release",
    )


def test_release_monitor_healthy_records_audit_without_recommending_rollback() -> None:
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    service = LearningHubService(repository=repository, audit_log=audit_log)
    _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")
    decision = _release_full(service, version=v2.version, rollback_target="1.0.0")

    assessment = service.monitor_release(
        release_id=decision.release_id,
        observed_metrics={"w4_smape": 0.10, "p80_coverage": 0.83},
        guardrails=(
            MetricThreshold("w4_smape", max_value=0.12),
            MetricThreshold("p80_coverage", min_value=0.75),
        ),
        correlation_id="corr-monitor-1",
    )

    assert assessment.status is MonitorStatus.HEALTHY
    assert assessment.recommended_action is RecommendedAction.NONE
    assert assessment.breaches == ()
    assert assessment.audit_event_id is not None
    monitor_events = [
        event
        for event in audit_log.list_events()
        if event.event_type == "learninghub.release_monitor.v1"
    ]
    assert len(monitor_events) == 1
    assert monitor_events[0].outcome == "healthy"


def test_release_monitor_breach_recommends_rollback_and_leaves_alias_unchanged() -> None:
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    service = LearningHubService(repository=repository, audit_log=audit_log)
    from models.shared_ml.registry import ModelAlias

    _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")
    decision = _release_full(service, version=v2.version, rollback_target="1.0.0")

    assessment = run_learninghub_release_monitor(
        {
            "release_id": decision.release_id,
            "observed_metrics": {"p80_coverage": 0.70},
            "guardrails": [{"metric_name": "p80_coverage", "min_value": 0.75}],
            "evaluated_by": "on-call-monitor",
            "correlation_id": "corr-monitor-2",
        },
        service=service,
    )

    assert assessment.status is MonitorStatus.BREACHED
    assert assessment.recommended_action is RecommendedAction.ROLLBACK
    assert [breach.metric_name for breach in assessment.breaches] == ["p80_coverage"]
    # Monitor is never optimistic: it recommends, it does not mutate the alias.
    assert (
        repository.get_alias("forecast_revenue_interval", ModelAlias.PRODUCTION).version == "1.1.0"
    )
    assert any(
        event.event_type == "learninghub.release_monitor.v1" and event.outcome == "breached"
        for event in audit_log.list_events()
    )


def test_release_monitor_rejects_unknown_release() -> None:
    service = LearningHubService()
    with pytest.raises(LearningHubError, match="unknown release"):
        service.monitor_release(
            release_id="does-not-exist",
            observed_metrics={"p80_coverage": 0.9},
            guardrails=(MetricThreshold("p80_coverage", min_value=0.75),),
        )


def test_learninghub_blocks_release_without_passed_validation_or_model_card() -> None:
    service = LearningHubService()
    snapshot = service.register_dataset_snapshot(_rows(), dataset_snapshot_id="failed-ds")
    validation = service.validate_candidate(
        model_name="forecast_revenue_interval",
        model_version="2.0.0",
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        metrics={"w4_smape": 0.18, "p80_coverage": 0.70},
        baseline_metrics={"w4_smape": 0.15, "p80_coverage": 0.78},
        thresholds=(MetricThreshold("w4_smape", max_value=0.12),),
    )
    assert not validation.passed
    service.register_model_version(
        model_version=_model_version("2.0.0", snapshot.dataset_snapshot_id),
        model_card=_model_card("2.0.0", snapshot.dataset_snapshot_id, validation.validation_run_id),
        validation_run=validation,
    )

    with pytest.raises(LearningHubError, match="passed validation"):
        _request_release(
            service,
            model_name="forecast_revenue_interval",
            version="2.0.0",
            release_type=ReleaseType.FULL,
            reason="should fail",
            approval_id="approval-full-003",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("none",),
            fail_criteria=("validation failed",),
        )
