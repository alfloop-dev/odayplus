from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime

import numpy as np
import pytest

from models.shared_ml import InMemoryArtifactStore
from models.shared_ml.oss_estimators import (
    EstimatorUnavailableError,
    UnknownEstimatorError,
    load_estimator_artifact,
    resolve_estimator_spec,
    train_oss_estimator,
)
from modules.learninghub import LearningHubService
from pipelines.features import FeaturePipelineRunner
from pipelines.training import TrainingPipelineRunner

FEATURE_ROWS = tuple(
    {
        "visits": float(30 + index * 3),
        "rent": float(80 + (index % 4) * 5),
        "region": "north" if index % 2 == 0 else "south",
    }
    for index in range(12)
)
LABELS = tuple(
    2.4 * row["visits"] - 0.35 * row["rent"] + (8 if row["region"] == "north" else -3)
    for row in FEATURE_ROWS
)


@pytest.mark.parametrize(
    ("algorithm", "engine", "native_suffix"),
    (
        ("catboost_regressor", "catboost.CatBoostRegressor", ".cbm"),
        ("lightgbm_regressor", "lightgbm.LGBMRegressor", ".txt"),
    ),
)
def test_point_estimator_fit_native_artifact_and_reload_round_trip(
    algorithm: str,
    engine: str,
    native_suffix: str,
) -> None:
    pytest.importorskip(algorithm.split("_", maxsplit=1)[0])
    result = train_oss_estimator(
        algorithm=algorithm,
        feature_rows=FEATURE_ROWS,
        labels=LABELS,
        feature_names=("visits", "rent", "region"),
    )

    before = result.estimator.predict(FEATURE_ROWS)
    artifact = result.estimator.to_artifact_bytes()
    reloaded = load_estimator_artifact(artifact)
    after = reloaded.predict(FEATURE_ROWS)
    lower, upper = reloaded.predict_interval(FEATURE_ROWS)

    assert result.resolved_algorithm == algorithm
    assert result.estimator.spec.engine == engine
    assert np.allclose(after, before)
    assert all(low <= point <= high for low, point, high in zip(lower, after, upper, strict=True))
    assert not artifact.startswith(b"{")
    with zipfile.ZipFile(io.BytesIO(artifact)) as archive:
        names = archive.namelist()
        assert "manifest.json" in names
        assert any(name.endswith(native_suffix) for name in names)


@pytest.mark.parametrize("algorithm", ("catboost_quantile", "lightgbm_quantile"))
def test_quantile_contract_trains_and_round_trips_native_interval_models(
    algorithm: str,
) -> None:
    pytest.importorskip(algorithm.split("_", maxsplit=1)[0])
    result = train_oss_estimator(
        algorithm=algorithm,
        feature_rows=FEATURE_ROWS,
        labels=LABELS,
        feature_names=("visits", "rent", "region"),
    )
    artifact = result.estimator.to_artifact_bytes()
    reloaded = load_estimator_artifact(artifact)

    lower, upper = reloaded.predict_interval(FEATURE_ROWS)
    points = reloaded.predict(FEATURE_ROWS)

    assert reloaded.spec.quantile_capable
    assert reloaded.spec.quantiles == (0.1, 0.5, 0.9)
    assert all(low <= high for low, high in zip(lower, upper, strict=True))
    assert len(points) == len(FEATURE_ROWS)
    with zipfile.ZipFile(io.BytesIO(artifact)) as archive:
        names = archive.namelist()
        assert any("/lower." in name for name in names)
        assert any("/point." in name for name in names)
        assert any("/upper." in name for name in names)


def test_legacy_algorithm_maps_to_explicit_oss_engine() -> None:
    pytest.importorskip("catboost")
    spec = resolve_estimator_spec("deterministic_backtest_regressor")

    assert spec.algorithm == "catboost_regressor"
    assert spec.engine == "catboost.CatBoostRegressor"


def test_unknown_algorithm_fails_closed() -> None:
    with pytest.raises(UnknownEstimatorError, match="unknown estimator algorithm"):
        resolve_estimator_spec("homegrown_magic_model")


def test_missing_engine_package_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import models.shared_ml.oss_estimators as estimators

    real_import = estimators.import_module

    def unavailable(name: str):
        if name == "catboost":
            raise ModuleNotFoundError("catboost intentionally unavailable")
        return real_import(name)

    monkeypatch.setattr(estimators, "import_module", unavailable)
    with pytest.raises(EstimatorUnavailableError, match="requires unavailable OSS package"):
        resolve_estimator_spec("catboost_regressor")


def test_training_pipeline_default_call_persists_reloadable_model_and_real_metrics() -> None:
    pytest.importorskip("catboost")
    service = LearningHubService()
    artifacts = InMemoryArtifactStore()
    snapshot_time = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    prediction_time = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    records = [
        {
            "view_name": "oss_training_view",
            "view_version": "v1",
            "entity_id": f"store-{index:03d}",
            "feature_snapshot_time": snapshot_time.isoformat(),
            "prediction_origin_time": prediction_time.isoformat(),
            "source_snapshot_ids": ["synthetic-oss-training"],
            "labels": {"target": LABELS[index]},
            "label_maturity_time": snapshot_time.isoformat(),
            "features": dict(FEATURE_ROWS[index]),
        }
        for index in range(len(FEATURE_ROWS))
    ]
    snapshot = service.register_dataset_snapshot(
        records,
        dataset_snapshot_id="oss-training-snapshot",
    )
    feature_artifact = FeaturePipelineRunner(
        repository=service.repository,
        artifact_store=artifacts,
    ).run(
        model_name="oss_forecast",
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        feature_schema_version="oss-training-v1",
    )
    runner = TrainingPipelineRunner(service=service, artifact_store=artifacts)

    result = runner.run(
        model_name="oss_forecast",
        model_version="1.0.0",
        feature_artifact=feature_artifact,
        label_name="target",
        feature_schema_version="oss-training-v1",
        label_version="target-v1",
        thresholds=(),
    )
    loaded = runner.load_model_artifact(result.model_artifact)
    coverage = result.validation_run.metrics["p80_coverage"]

    assert result.model_artifact.content_type == "application/vnd.oday.oss-estimator+zip"
    assert result.model_artifact.metadata["requested_algorithm"] == (
        "deterministic_backtest_regressor"
    )
    assert result.model_artifact.metadata["resolved_algorithm"] == "catboost_regressor"
    assert result.model_artifact.metadata["engine"] == "catboost.CatBoostRegressor"
    assert len(loaded.predict(FEATURE_ROWS)) == len(FEATURE_ROWS)
    assert result.validation_run.metrics["mae"] > 0
    assert result.validation_run.metrics["normalized_mae"] > 0
    assert result.validation_run.calibration_summary["p80_coverage"] == coverage
    assert result.validation_run.calibration_summary["calibration_error"] == pytest.approx(
        abs(coverage - 0.8)
    )
    assert result.validation_run.calibration_summary["calibration_error"] != 0.02
