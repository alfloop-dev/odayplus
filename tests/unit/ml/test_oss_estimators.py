from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from typing import Any

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
            "data_quality_score": 0.98,
            "confidence": 0.95,
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


def test_grouped_backtest_keeps_every_group_in_one_fold() -> None:
    from models.shared_ml.oss_estimators import _fold_assignments

    groups = [f"store-{index % 7}" for index in range(70)]
    folds = _fold_assignments(len(groups), groups=groups)

    fold_of_group: dict[str, set[int]] = {}
    for group, fold in zip(groups, folds, strict=True):
        fold_of_group.setdefault(group, set()).add(fold)
    assert all(len(assigned) == 1 for assigned in fold_of_group.values())
    assert set(folds) == {0, 1, 2, 3, 4}
    assert folds == _fold_assignments(len(groups), groups=groups)


@pytest.mark.parametrize(
    ("groups", "message"),
    (
        (["store-1"] * 4, "at least two distinct groups"),
        (["store-1", "store-2", "store-1"], "row counts differ"),
        (["store-1", " ", "store-2", "store-1"], "non-empty"),
    ),
)
def test_grouped_backtest_fails_closed_on_unusable_groups(
    groups: list[str],
    message: str,
) -> None:
    from models.shared_ml.oss_estimators import OSSEstimatorError, _fold_assignments

    with pytest.raises(OSSEstimatorError, match=message):
        _fold_assignments(4, groups=groups)


def test_grouped_backtest_calibrates_interval_on_unseen_groups() -> None:
    """Overlapping rows of one entity must not calibrate each other's interval.

    Each store has a level the features do not explain and many near-duplicate
    rows (like overlapping forecast horizon windows). Random folds see the
    store's own duplicates in training, so out-of-fold residuals are tiny and
    the interval collapses; grouped folds measure error on unseen stores.
    """
    pytest.importorskip("lightgbm")
    rng = np.random.default_rng(7)
    rows: list[dict[str, Any]] = []
    labels: list[float] = []
    groups: list[str] = []
    for store in range(10):
        level = float(rng.uniform(100.0, 400.0))
        for day in range(40):
            rows.append({"store_id": f"store-{store}", "day": float(day)})
            labels.append(level + float(rng.normal(0.0, 1.0)))
            groups.append(f"store-{store}")
    names = ("store_id", "day")

    random_folds = train_oss_estimator(
        algorithm="lightgbm_regressor",
        feature_rows=rows,
        labels=labels,
        feature_names=names,
    )
    grouped = train_oss_estimator(
        algorithm="lightgbm_regressor",
        feature_rows=rows,
        labels=labels,
        feature_names=names,
        backtest_groups=groups,
    )

    assert random_folds.estimator.conformal_radius is not None
    assert grouped.estimator.conformal_radius is not None
    assert grouped.estimator.conformal_radius > 10 * random_folds.estimator.conformal_radius
    fold_of_group: dict[str, set[int]] = {}
    for group, fold in zip(groups, grouped.backtest.fold_ids, strict=True):
        fold_of_group.setdefault(group, set()).add(fold)
    assert all(len(assigned) == 1 for assigned in fold_of_group.values())


def test_training_pipeline_forwards_backtest_group_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("catboost")
    import pipelines.training.model_training as model_training

    captured: dict[str, Any] = {}
    real_train = model_training.train_oss_estimator

    def recording_train(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return real_train(**kwargs)

    monkeypatch.setattr(model_training, "train_oss_estimator", recording_train)
    service = LearningHubService()
    artifacts = InMemoryArtifactStore()
    moment = datetime(2026, 7, 1, 8, 0, tzinfo=UTC).isoformat()
    snapshot = service.register_dataset_snapshot(
        [
            {
                "view_name": "oss_training_view",
                "view_version": "v1",
                "entity_id": f"store-{index:03d}",
                "feature_snapshot_time": moment,
                "prediction_origin_time": datetime(2026, 7, 1, 9, 0, tzinfo=UTC).isoformat(),
                "source_snapshot_ids": ["synthetic-oss-training"],
                "data_quality_score": 0.98,
                "confidence": 0.95,
                "labels": {"target": LABELS[index]},
                "label_maturity_time": moment,
                "features": dict(FEATURE_ROWS[index]),
            }
            for index in range(len(FEATURE_ROWS))
        ],
        dataset_snapshot_id="oss-grouped-snapshot",
    )
    feature_artifact = FeaturePipelineRunner(
        repository=service.repository,
        artifact_store=artifacts,
    ).run(
        model_name="oss_forecast",
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        feature_schema_version="oss-training-v1",
    )

    TrainingPipelineRunner(service=service, artifact_store=artifacts).run(
        model_name="oss_forecast",
        model_version="1.0.0",
        feature_artifact=feature_artifact,
        label_name="target",
        feature_schema_version="oss-training-v1",
        label_version="target-v1",
        thresholds=(),
        backtest_group_fields=("region",),
    )

    assert sorted(set(captured["backtest_groups"])) == ["north", "south"]
    assert len(captured["backtest_groups"]) == len(FEATURE_ROWS)
