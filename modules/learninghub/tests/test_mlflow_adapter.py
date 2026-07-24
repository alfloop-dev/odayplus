from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mlflow.tracking import MlflowClient

from models.shared_ml.registry import ModelAlias, ModelStage, ModelVersion
from modules.learninghub.infrastructure.mlflow_adapter import MlflowRegistryAdapter
from modules.learninghub.infrastructure.repositories import InMemoryLearningHubRepository


@pytest.fixture
def tracking_uri(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'mlflow.db'}"


def _model_version(
    tmp_path: Path,
    *,
    version: str = "2026.07.24",
    stage: ModelStage = ModelStage.CANARY,
    aliases: frozenset[ModelAlias] = frozenset({ModelAlias.CHALLENGER}),
) -> ModelVersion:
    artifact = tmp_path / "models" / version / "model.pkl"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"model-bytes")
    return ModelVersion(
        model_name="site_revenue_path",
        version=version,
        artifact_uri=artifact.as_uri(),
        dataset_snapshot_id="snapshot-2026-07-23",
        feature_schema_version="site-features-v4",
        label_version="revenue-w24-v2",
        metrics={"mae": 12.5, "p80_coverage": 0.83},
        stage=stage,
        aliases=aliases,
        run_id="external-training-run-42",
        git_sha="abc123def",
        created_at=datetime(2026, 7, 24, 8, 30, tzinfo=UTC),
        approved_by="model-review-board",
        approved_at=datetime(2026, 7, 24, 9, 0, tzinfo=UTC),
        rollback_target="2026.07.10",
        monitoring_config={"psi_threshold": 0.2, "segments": ["district", "format"]},
    )


def _adapter(tracking_uri: str) -> MlflowRegistryAdapter:
    return MlflowRegistryAdapter(
        InMemoryLearningHubRepository(),
        tracking_uri=tracking_uri,
        experiment_name="mlflow-adapter-tests",
    )


def test_register_model_version_persists_run_tags_artifact_and_registry_lineage(
    tmp_path: Path,
    tracking_uri: str,
) -> None:
    model_version = _model_version(tmp_path)
    adapter = _adapter(tracking_uri)

    registered = adapter.register_model_version(model_version)

    assert registered.to_dict() == model_version.to_dict()

    client = MlflowClient(tracking_uri=tracking_uri)
    stored_versions = client.search_model_versions("name = 'site_revenue_path'")
    assert len(stored_versions) == 1
    stored = client.get_model_version("site_revenue_path", stored_versions[0].version)
    assert stored.source == model_version.artifact_uri
    assert stored.current_stage == "Staging"
    assert stored.tags["oday.model_version.domain_version"] == model_version.version
    assert stored.tags["oday.model_version.source_run_id"] == model_version.run_id
    assert stored.tags["oday.model_version.dataset_snapshot_id"] == "snapshot-2026-07-23"
    assert stored.tags["oday.model_version.feature_schema_version"] == "site-features-v4"
    assert stored.tags["oday.model_version.label_version"] == "revenue-w24-v2"

    run = client.get_run(stored.run_id)
    assert run.data.tags["oday.model_version.git_sha"] == "abc123def"
    assert run.data.metrics["mae"] == 12.5
    assert run.data.metrics["p80_coverage"] == 0.83

    lineage_path = Path(
        client.download_artifacts(
            stored.run_id,
            "oday-lineage/model-versions/2026.07.24.json",
            str(tmp_path / "download"),
        )
    )
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    assert lineage["artifact_uri"] == model_version.artifact_uri
    assert lineage["run_id"] == "external-training-run-42"
    assert lineage["mlflow_run_id"] == stored.run_id


def test_stage_and_alias_round_trip_from_mlflow_with_a_fresh_repository(
    tmp_path: Path,
    tracking_uri: str,
) -> None:
    adapter = _adapter(tracking_uri)
    model_version = _model_version(tmp_path)
    adapter.register_model_version(model_version)

    promoted = adapter.transition_stage(
        model_name=model_version.model_name,
        version=model_version.version,
        stage=ModelStage.PRODUCTION,
    )
    aliased = adapter.set_alias(
        model_name=model_version.model_name,
        alias=ModelAlias.PRODUCTION,
        version=model_version.version,
    )

    assert promoted.stage is ModelStage.PRODUCTION
    assert aliased.aliases == frozenset({ModelAlias.CHALLENGER, ModelAlias.PRODUCTION})

    restarted = _adapter(tracking_uri)
    restored = restarted.get_by_alias(
        model_name=model_version.model_name,
        alias=ModelAlias.PRODUCTION,
    )
    assert restored is not None
    assert restored.version == model_version.version
    assert restored.stage is ModelStage.PRODUCTION
    assert restored.aliases == frozenset({ModelAlias.CHALLENGER, ModelAlias.PRODUCTION})
    assert restored.run_id == model_version.run_id
    assert restored.artifact_uri == model_version.artifact_uri
    assert restored.metrics == model_version.metrics
    assert restored.monitoring_config == model_version.monitoring_config

    client = MlflowClient(tracking_uri=tracking_uri)
    mlflow_version = client.get_model_version_by_alias(
        model_version.model_name,
        ModelAlias.PRODUCTION.value,
    )
    assert mlflow_version.current_stage == "Production"


def test_registration_is_idempotent_but_rejects_lineage_rewrites(
    tmp_path: Path,
    tracking_uri: str,
) -> None:
    adapter = _adapter(tracking_uri)
    model_version = _model_version(tmp_path)

    adapter.register_model_version(model_version)
    adapter.register_model_version(model_version)

    client = MlflowClient(tracking_uri=tracking_uri)
    assert len(client.search_model_versions("name = 'site_revenue_path'")) == 1

    conflicting_artifact = tmp_path / "other-model.pkl"
    conflicting_artifact.write_bytes(b"different")
    with pytest.raises(ValueError, match="immutable artifact lineage conflict"):
        adapter.register_model_version(
            model_version._replace(artifact_uri=conflicting_artifact.as_uri())
        )

    Path(model_version.artifact_uri.removeprefix("file://")).write_bytes(
        b"same-uri-different-content"
    )
    with pytest.raises(ValueError, match="immutable artifact digest conflict"):
        adapter.register_model_version(model_version)


def test_unknown_domain_version_and_alias_are_reported_without_repository_fallback(
    tracking_uri: str,
) -> None:
    adapter = _adapter(tracking_uri)

    with pytest.raises(ValueError, match="unknown model version"):
        adapter.transition_stage(
            model_name="missing-model",
            version="404",
            stage=ModelStage.PRODUCTION,
        )
    assert (
        adapter.get_by_alias(
            model_name="missing-model",
            alias=ModelAlias.PRODUCTION,
        )
        is None
    )


def test_default_in_memory_adapters_use_isolated_real_mlflow_stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    first = MlflowRegistryAdapter(InMemoryLearningHubRepository())
    second = MlflowRegistryAdapter(InMemoryLearningHubRepository())

    first_model = _model_version(tmp_path / "first")
    second_model = _model_version(tmp_path / "second")
    first.register_model_version(first_model)
    second.register_model_version(second_model)

    assert first.tracking_uri
    assert second.tracking_uri
    assert first.tracking_uri != second.tracking_uri
    assert isinstance(first.client, MlflowClient)
    assert isinstance(second.client, MlflowClient)
    assert first.get_by_alias(
        model_name=first_model.model_name,
        alias=ModelAlias.CHALLENGER,
    )
    assert second.get_by_alias(
        model_name=second_model.model_name,
        alias=ModelAlias.CHALLENGER,
    )
