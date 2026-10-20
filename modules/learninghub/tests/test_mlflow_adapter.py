from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

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


@pytest.mark.parametrize("digest_metadata", ["local", "prefixed", "uppercase", "alias"])
def test_register_model_version_persists_run_tags_artifact_and_registry_lineage(
    tmp_path: Path,
    tracking_uri: str,
    digest_metadata: str,
) -> None:
    model_version = _model_version(tmp_path)
    digest = hashlib.sha256(b"model-bytes").hexdigest()
    declared = {
        "local": {},
        "prefixed": {"artifact_sha256": "sha256:" + digest},
        "uppercase": {"artifact_sha256": digest.upper()},
        "alias": {"artifact_digest": digest},
    }[digest_metadata]
    model_version = replace(model_version, monitoring_config={**model_version.monitoring_config, **declared})
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
    assert stored.tags["oday.model_version.artifact_sha256"] == "sha256:" + digest

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


def test_different_models_register_concurrently_in_one_new_experiment(
    tmp_path: Path, tracking_uri: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    client = MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)
    client.search_experiments()  # Initialize the actual backend before racing.
    repository = InMemoryLearningHubRepository()
    adapter = MlflowRegistryAdapter(
        repository, tracking_uri=tracking_uri, client=client, runtime_mode="poc",
        experiment_name="concurrent-model-registration",
    )
    barrier = Barrier(2)
    original = client.get_experiment_by_name

    def observe_missing(name):
        experiment = original(name)
        if experiment is None:
            barrier.wait(timeout=10)
        return experiment

    monkeypatch.setattr(client, "get_experiment_by_name", observe_missing)
    base = _model_version(tmp_path, stage=ModelStage.DEV, aliases=frozenset())
    candidates = [replace(base, model_name=name, run_id=None) for name in ("model-a", "model-b")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        registered = list(pool.map(adapter.register_model_version, candidates))

    experiment = original(adapter.experiment_name)
    assert experiment is not None
    runs = client.search_runs([experiment.experiment_id])
    assert len(runs) == 2 and len({run.info.run_id for run in runs}) == 2
    assert {run.data.tags["oday.model_name"] for run in runs} == {"model-a", "model-b"}
    for candidate, result in zip(candidates, registered, strict=True):
        assert result.model_name == candidate.model_name
        assert result.stage is ModelStage.DEV and not result.aliases
        assert repository.get_model_version(candidate.model_name, candidate.version) == result
        versions = client.search_model_versions(f"name = '{candidate.model_name}'")
        assert len(versions) == 1
        assert client.get_run(versions[0].run_id).info.experiment_id == experiment.experiment_id


@pytest.mark.parametrize("error_code", ["INTERNAL_ERROR", "RESOURCE_ALREADY_EXISTS"])
def test_registration_propagates_experiment_creation_failure_without_a_resolved_experiment(
    tmp_path: Path, tracking_uri: str, monkeypatch: pytest.MonkeyPatch, error_code: str,
) -> None:
    from mlflow.exceptions import MlflowException

    monkeypatch.chdir(tmp_path)
    client = MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)
    repository = InMemoryLearningHubRepository()
    adapter = MlflowRegistryAdapter(
        repository, tracking_uri=tracking_uri, client=client, runtime_mode="poc",
        experiment_name="unresolved-registration",
    )
    error = MlflowException("Experiment creation failed", error_code=error_code)

    def fail_creation(*args, **kwargs):
        raise error

    monkeypatch.setattr(client, "create_experiment", fail_creation)
    candidate = replace(_model_version(tmp_path, stage=ModelStage.DEV, aliases=frozenset()), run_id=None)
    with pytest.raises(MlflowException) as caught:
        adapter.register_model_version(candidate)
    assert caught.value is error
    assert client.get_experiment_by_name(adapter.experiment_name) is None
    assert client.search_model_versions(f"name = '{candidate.model_name}'") == []
    assert repository.get_model_version(candidate.model_name, candidate.version) is None


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
