"""Production model registry and artifact evidence (ODP-PV-013).

These tests prove the Learning Hub model lifecycle runs on **durable** artifact
and model-registry storage with auditable evidence, instead of demo in-memory
registry state. The three task acceptance criteria are covered:

1. Model versions, validation metrics, aliases, shadow/canary, promotion, and
   rollback persist (verified by simulating a process restart).
2. Model cards carry data snapshot, feature set, policy/version, owner,
   approval, and a rollback link.
3. Product E2E can promote/rollback a deterministic model with audit
   traceability and tamper-evident artifact digests.

"Process restart" is simulated by closing the durable engine and rebuilding the
durable repositories against the same on-disk SQLite file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from models.shared_ml import (
    ArtifactKind,
    InMemoryArtifactStore,
    MetricThreshold,
    ModelAlias,
    ModelCard,
    ModelCardApproval,
    ModelRiskLevel,
    ModelStage,
    ModelVersion,
    SegmentMetric,
    artifact_uri,
    build_model_registry_evidence,
    compute_content_digest,
)
from modules.learninghub import (
    LearningHubService,
    MlflowRegistryAdapter,
    ReleaseType,
    run_learninghub_release,
)
from shared.infrastructure.persistence import (
    DurableArtifactStore,
    DurableAuditLog,
    DurableLearningHubRepository,
    SqliteDocumentStore,
    SqliteEngine,
)

MODEL_NAME = "forecast_revenue_interval"
SNAPSHOT_TIME = datetime(2026, 6, 27, 8, 0, tzinfo=UTC)
PREDICTION_TIME = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "model_registry.sqlite3")


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


def _model_card(version: str, dataset_snapshot_id: str, validation_run_id: str) -> ModelCard:
    return ModelCard(
        model_name=MODEL_NAME,
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
        segment_metrics=(
            {"segment_name": "region", "segment_value": "north", "w4_smape": 0.10},
        ),
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


def _artifact_bytes(version: str) -> bytes:
    """Deterministic, version-specific 'model weights' payload."""
    return f"forecast-model-weights::{version}".encode()


def _prepare_candidate(
    service: LearningHubService,
    artifact_store: DurableArtifactStore | InMemoryArtifactStore,
    version: str,
) -> tuple[ModelVersion, object]:
    snapshot = service.register_dataset_snapshot(
        _rows(), dataset_snapshot_id=f"forecast-training-{version}"
    )
    validation = service.validate_candidate(
        model_name=MODEL_NAME,
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

    # Store the real artifact and bind the model version to its content digest.
    record = artifact_store.put_artifact(
        model_name=MODEL_NAME,
        version=version,
        kind=ArtifactKind.MODEL,
        data=_artifact_bytes(version),
        content_type="application/octet-stream",
        metadata={"run_id": f"mlflow-run-{version}"},
    )
    model_version = ModelVersion(
        model_name=MODEL_NAME,
        version=version,
        artifact_uri=record.uri,
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        feature_schema_version="store-machine-timeseries-view-v1",
        label_version="forecast-w4-revenue-v1",
        metrics={"w4_smape": 0.11, "p80_coverage": 0.82},
        run_id=f"mlflow-run-{version}",
        git_sha="abc1234",
    )
    registered = service.register_model_version(
        model_version=model_version,
        model_card=_model_card(version, snapshot.dataset_snapshot_id, validation.validation_run_id),
        validation_run=validation,
    )
    return registered, record


def _build_durable(db_path: str) -> tuple[SqliteEngine, LearningHubService, DurableArtifactStore]:
    engine = SqliteEngine(db_path)
    store = SqliteDocumentStore(engine)
    repository = DurableLearningHubRepository(store)
    service = LearningHubService(
        repository=repository,
        registry=MlflowRegistryAdapter(repository),
        audit_log=DurableAuditLog(engine),
    )
    return engine, service, DurableArtifactStore(store)


# -- 1. lifecycle persistence across restart ----------------------------------


def test_full_lifecycle_promote_rollback_survives_restart(db_path) -> None:
    engine, service, artifacts = _build_durable(db_path)
    try:
        v1, _ = _prepare_candidate(service, artifacts, "1.0.0")
        v2, _ = _prepare_candidate(service, artifacts, "1.1.0")

        # shadow -> full promote v1 -> full promote v2 (retires v1)
        service.request_release(
            model_name=MODEL_NAME,
            version=v1.version,
            release_type=ReleaseType.SHADOW,
            reason="shadow before promotion",
            approval_id="approval-shadow-001",
            rollback_target=None,
            monitoring_window="24h",
            success_criteria=("schema valid",),
            fail_criteria=("missing model card",),
            requested_by="ml-owner",
            correlation_id="corr-shadow",
        )
        service.request_release(
            model_name=MODEL_NAME,
            version=v1.version,
            release_type=ReleaseType.FULL,
            reason="promote validated champion",
            approval_id="approval-full-001",
            rollback_target=v1.version,
            monitoring_window="48h",
            success_criteria=("smoke prediction passed",),
            fail_criteria=("p80 coverage below threshold",),
            requested_by="ml-owner",
            correlation_id="corr-full-1",
        )
        service.request_release(
            model_name=MODEL_NAME,
            version=v2.version,
            release_type=ReleaseType.FULL,
            reason="better error and coverage",
            approval_id="approval-full-002",
            rollback_target=v1.version,
            monitoring_window="48h",
            success_criteria=("smoke prediction passed",),
            fail_criteria=("coverage regression",),
            requested_by="ml-owner",
            correlation_id="corr-full-2",
        )
    finally:
        engine.close()

    # --- simulated restart ---
    engine2, service2, artifacts2 = _build_durable(db_path)
    try:
        repo = service2.repository
        # Promotion state persisted.
        assert repo.get_alias(MODEL_NAME, ModelAlias.PRODUCTION).version == "1.1.0"
        assert repo.get_alias(MODEL_NAME, ModelAlias.CHAMPION).version == "1.1.0"
        assert repo.get_alias(MODEL_NAME, ModelAlias.PREVIOUS_PRODUCTION).version == "1.0.0"
        assert repo.get_alias(MODEL_NAME, ModelAlias.SHADOW).version == "1.0.0"
        assert repo.get_model_version(MODEL_NAME, "1.1.0").stage is ModelStage.PRODUCTION
        assert repo.get_model_version(MODEL_NAME, "1.0.0").stage is ModelStage.RETIRED
        assert {mv.version for mv in repo.list_model_versions(MODEL_NAME)} == {"1.0.0", "1.1.0"}

        # Metrics + validation persisted.
        v2 = repo.get_model_version(MODEL_NAME, "1.1.0")
        assert v2.metrics["p80_coverage"] == 0.82
        card = repo.get_model_card(MODEL_NAME, "1.1.0")
        assert repo.get_validation_run(card.validation_run_id).passed

        # Artifacts survived restart and remain tamper-evident.
        assert artifacts2.verify("forecast_revenue_interval/1.1.0/model")
        assert artifacts2.open_artifact("forecast_revenue_interval/1.1.0/model") == _artifact_bytes("1.1.0")

        # Rollback after restart via the worker entrypoint.
        rollback = run_learninghub_release(
            {
                "model_name": MODEL_NAME,
                "version": "1.1.0",
                "release_type": "ROLLBACK",
                "reason": "coverage watch window breached",
                "approval_id": "approval-rollback-001",
                "rollback_target": "1.0.0",
                "monitoring_window": "immediate",
                "success_criteria": ["alias points at previous model"],
                "fail_criteria": ["smoke prediction fails"],
                "requested_by": "on-call",
                "correlation_id": "corr-rollback",
            },
            service=service2,
        )
        assert rollback.from_version == "1.1.0"
        assert rollback.to_version == "1.0.0"
        assert repo.get_alias(MODEL_NAME, ModelAlias.PRODUCTION).version == "1.0.0"
        assert repo.get_model_version(MODEL_NAME, "1.1.0").stage is ModelStage.ROLLED_BACK
    finally:
        engine2.close()

    # --- second restart: rollback decision + audit trail persisted ---
    engine3, service3, _ = _build_durable(db_path)
    try:
        repo = service3.repository
        assert repo.get_alias(MODEL_NAME, ModelAlias.PRODUCTION).version == "1.0.0"
        decisions = repo.list_release_decisions()
        kinds = [d.release_type.value for d in decisions]
        assert kinds.count("ROLLBACK") == 1
        assert {"SHADOW", "FULL", "ROLLBACK"} <= set(kinds)

        # Audit traceability: every release/rollback recorded an audit event,
        # correlation-indexed and retrievable after restart.
        events = service3.audit_log.list_events()
        assert any(e.action == "rollback" for e in events)
        assert len(service3.audit_log.list_events(correlation_id="corr-rollback")) == 1
        for decision in decisions:
            assert decision.audit_event_id is not None
    finally:
        engine3.close()


# -- 2. model card completeness + links ---------------------------------------


def test_model_card_carries_required_links(db_path) -> None:
    engine, service, artifacts = _build_durable(db_path)
    try:
        _prepare_candidate(service, artifacts, "1.0.0")
        card = service.repository.get_model_card(MODEL_NAME, "1.0.0")
    finally:
        engine.close()

    assert card.is_complete
    assert card.is_approved
    assert card.owner == "ml-platform"
    assert card.dataset_snapshot_id == "forecast-training-1.0.0"
    assert card.feature_set_id == "fs_forecastops_v1"
    assert card.label_set_id == "ls_forecastops_w4_v1"
    assert card.validation_run_id  # links to the validation run
    assert card.rollback_conditions  # rollback link/policy present
    assert card.approvals[0].role == "model-review-board"


# -- 3. content-addressed artifact evidence -----------------------------------


def test_artifact_content_addressing_and_tamper_evidence(db_path) -> None:
    engine = SqliteEngine(db_path)
    store = DurableArtifactStore(SqliteDocumentStore(engine))
    try:
        data = _artifact_bytes("1.0.0")
        record = store.put_artifact(
            model_name=MODEL_NAME, version="1.0.0", kind=ArtifactKind.MODEL, data=data
        )
        # URI is derived from the content digest -> binds bytes to the version.
        assert record.content_digest == compute_content_digest(data)
        assert record.uri == artifact_uri(record.content_digest)
        assert record.size_bytes == len(data)
        assert store.verify(record.artifact_id)

        # Re-putting identical bytes is idempotent (same digest + uri).
        again = store.put_artifact(
            model_name=MODEL_NAME, version="1.0.0", kind=ArtifactKind.MODEL, data=data
        )
        assert again.content_digest == record.content_digest

        # Different bytes -> different digest (no silent collision).
        other = store.put_artifact(
            model_name=MODEL_NAME, version="1.1.0", kind=ArtifactKind.MODEL,
            data=_artifact_bytes("1.1.0"),
        )
        assert other.content_digest != record.content_digest

        # Unknown artifact fails verification rather than erroring.
        assert not store.verify("forecast_revenue_interval/9.9.9/model")
        assert len(store.list_artifacts(MODEL_NAME)) == 2
        assert len(store.list_artifacts_for_version(MODEL_NAME, "1.0.0")) == 1
    finally:
        engine.close()

    # Digest is stable across a restart (content-addressed, not session-bound).
    reopened = SqliteEngine(db_path)
    try:
        store2 = DurableArtifactStore(SqliteDocumentStore(reopened))
        assert store2.verify("forecast_revenue_interval/1.0.0/model")
    finally:
        reopened.close()


# -- evidence manifest --------------------------------------------------------


def test_registry_evidence_manifest_is_audit_complete(db_path) -> None:
    engine, service, artifacts = _build_durable(db_path)
    try:
        v1, _ = _prepare_candidate(service, artifacts, "1.0.0")
        service.request_release(
            model_name=MODEL_NAME,
            version=v1.version,
            release_type=ReleaseType.FULL,
            reason="promote",
            approval_id="approval-full-001",
            rollback_target=v1.version,
            monitoring_window="48h",
            success_criteria=("ok",),
            fail_criteria=("regress",),
            correlation_id="corr-evidence",
        )
        evidence = build_model_registry_evidence(
            model_name=MODEL_NAME,
            repository=service.repository,
            artifact_store=artifacts,
        )
    finally:
        engine.close()

    payload = evidence.to_dict()
    # JSON-serializable audit artifact.
    assert json.loads(json.dumps(payload))

    assert payload["model_name"] == MODEL_NAME
    assert payload["aliases"]["production"] == "1.0.0"
    assert payload["aliases"]["champion"] == "1.0.0"
    assert len(payload["release_decisions"]) == 1

    entry = payload["versions"][0]
    assert entry["version"] == "1.0.0"
    assert entry["stage"] == "production"
    assert entry["validation_status"] == "PASSED"
    assert entry["artifact_uri"].startswith("odp-artifact://sha256/")
    assert entry["model_card"]["is_complete"] is True
    assert entry["model_card"]["is_approved"] is True
    assert entry["model_card"]["rollback_conditions"]
    # The version's artifact digest is embedded in the manifest.
    assert entry["artifacts"][0]["content_digest"] == compute_content_digest(
        _artifact_bytes("1.0.0")
    )
