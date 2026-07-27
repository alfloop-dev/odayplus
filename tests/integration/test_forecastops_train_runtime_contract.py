from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip(
    "lightgbm",
    reason="ForecastOps train-to-runtime verification requires LightGBM",
)
pytest.importorskip(
    "mlflow",
    reason="ForecastOps train-to-runtime verification requires MLflow",
)

from models.shared_ml import (
    InMemoryArtifactStore,
    MlflowProductionModelRuntime,
    ModelAlias,
    ModelStage,
    ProductionModelInputError,
)
from modules.forecastops.application import RegisteredEstimatorForecastEngine
from modules.forecastops.domain import (
    FORECASTOPS_FEATURE_SCHEMA_ID,
    FORECASTOPS_MODEL_FEATURES,
    ForecastInput,
    StoreDayObservation,
)
from modules.forecastops.model_contract import FORECASTOPS_LABEL_NAME
from modules.learninghub import LearningHubService
from modules.learninghub.infrastructure import (
    InMemoryLearningHubRepository,
    MlflowRegistryAdapter,
)
from pipelines.features import FeaturePipelineRunner
from pipelines.training import TrainingPipelineRunner
from scripts.models.contracts import MODEL_SPECS
from scripts.models.forecast_training import expand_forecast_horizon_rows

NOW = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


def test_forecast_training_artifact_loads_and_scores_through_runtime(
    tmp_path: Path,
) -> None:
    spec = MODEL_SPECS["forecastops"]
    service = LearningHubService()
    artifacts = InMemoryArtifactStore()
    snapshot = service.register_dataset_snapshot(
        _forecast_training_records(),
        dataset_snapshot_id="forecast-training-view-live-20260724",
    )
    feature_artifact = FeaturePipelineRunner(
        repository=service.repository,
        artifact_store=artifacts,
    ).run(
        model_name=spec.model_name,
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        feature_schema_version=spec.feature_schema_version,
        feature_set_id=spec.feature_set_id,
        actor="forecast-training-test",
    )
    training = TrainingPipelineRunner(
        service=service,
        artifact_store=artifacts,
    ).run(
        model_name=spec.model_name,
        model_version="2026.07.24-contract",
        feature_artifact=feature_artifact,
        label_name=spec.label_name,
        feature_schema_version=spec.feature_schema_version,
        label_version=spec.label_version,
        thresholds=(),
        segment_field=spec.segment_column,
        algorithm=spec.algorithm,
        actor="forecast-training-test",
        run_id="forecast-training-runtime-contract",
        git_sha="c72804b8",
    )

    artifact_bytes = artifacts.open_artifact(training.model_artifact.artifact_id)
    assert artifact_bytes is not None
    loaded = TrainingPipelineRunner(
        service=service,
        artifact_store=artifacts,
    ).load_model_artifact(training.model_artifact)

    assert spec.feature_schema_version == FORECASTOPS_FEATURE_SCHEMA_ID
    assert spec.feature_columns == FORECASTOPS_MODEL_FEATURES
    assert feature_artifact.feature_names == FORECASTOPS_MODEL_FEATURES
    assert loaded.encoder.feature_names == FORECASTOPS_MODEL_FEATURES
    assert (
        training.model_artifact.metadata["feature_schema_version"] == FORECASTOPS_FEATURE_SCHEMA_ID
    )
    assert training.model_version.feature_schema_version == FORECASTOPS_FEATURE_SCHEMA_ID

    artifact_path = tmp_path / "forecast-lightgbm.zip"
    artifact_path.write_bytes(artifact_bytes)
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    registry = MlflowRegistryAdapter(
        InMemoryLearningHubRepository(),
        tracking_uri=tracking_uri,
        experiment_name="forecast-train-runtime-contract",
    )
    registry.register_model_version(
        training.model_version._replace(
            artifact_uri=artifact_path.as_uri(),
            stage=ModelStage.PRODUCTION,
            aliases=frozenset({ModelAlias.PRODUCTION}),
            approved_by="independent-model-reviewer",
            approved_at=NOW,
            monitoring_config={
                "artifact_sha256": training.model_artifact.content_digest,
            },
        )
    )

    engine = RegisteredEstimatorForecastEngine(
        MlflowProductionModelRuntime(tracking_uri=tracking_uri)
    )
    result = engine.fit_predict(_live_forecast_input())

    assert set(result.bands) == {4, 8, 12, 24}
    horizon_p50 = [result.bands[horizon].p50 for horizon in (4, 8, 12, 24)]
    assert len(set(horizon_p50)) == 4
    assert horizon_p50 == sorted(horizon_p50)
    assert result.model_name == spec.model_name
    assert result.model_version == f"{spec.model_name}:2026.07.24-contract"
    assert result.metadata["feature_schema_version"] == FORECASTOPS_FEATURE_SCHEMA_ID
    assert engine.last_inference is not None
    assert engine.last_inference.binding.feature_schema_version == (FORECASTOPS_FEATURE_SCHEMA_ID)
    for band in result.bands.values():
        assert 0 <= band.p10 <= band.p50 <= band.p90


def test_forecast_runtime_fails_closed_before_inference_without_live_contract() -> None:
    engine = RegisteredEstimatorForecastEngine(_UnexpectedRuntime())

    with pytest.raises(ProductionModelInputError, match="requires tenant_id"):
        engine.fit_predict(_live_forecast_input(tenant_id=None))
    with pytest.raises(ProductionModelInputError, match="at least 28 days"):
        engine.fit_predict(_live_forecast_input(days=27))


def test_forecast_runtime_rejects_future_stale_and_gapped_history() -> None:
    engine = RegisteredEstimatorForecastEngine(_UnexpectedRuntime())
    valid = _live_forecast_input()

    future = replace(
        valid,
        observations=(
            *valid.observations,
            replace(
                valid.observations[-1],
                business_date=valid.prediction_origin_time.date(),
            ),
        ),
    )
    with pytest.raises(
        ProductionModelInputError,
        match="on or after prediction origin",
    ):
        engine.fit_predict(future)

    stale = replace(
        valid,
        prediction_origin_time=valid.prediction_origin_time + timedelta(days=1),
    )
    with pytest.raises(ProductionModelInputError, match=r"\(stale\)"):
        engine.fit_predict(stale)

    missing_date = valid.prediction_origin_time.date() - timedelta(days=7)
    gapped = replace(
        valid,
        observations=tuple(
            observation
            for observation in valid.observations
            if observation.business_date != missing_date
        ),
    )
    with pytest.raises(ProductionModelInputError, match="contiguous daily history"):
        engine.fit_predict(gapped)


def test_forecast_daily_rows_expand_to_semantic_horizon_average_labels() -> None:
    raw_rows = _forecast_daily_rows(days=180)

    expanded = expand_forecast_horizon_rows(raw_rows)
    origin = date(2026, 1, 1)
    origin_rows = {int(row["horizon_weeks"]): row for row in expanded if row["date"] == origin}

    assert set(origin_rows) == {4, 8, 12, 24}
    for horizon_weeks, row in origin_rows.items():
        horizon_days = horizon_weeks * 7
        expected = sum(70_000.0 + index * 1_000.0 for index in range(horizon_days)) / horizon_days
        assert row["daily_net_revenue"] == pytest.approx(expected)
        assert row["horizon_weeks"] == horizon_weeks
        assert row["feature_snapshot_time"] == raw_rows[0]["feature_snapshot_time"]
        assert row["prediction_origin_time"] == raw_rows[0]["prediction_origin_time"]
        assert row["label_maturity_time"] == raw_rows[horizon_days - 1]["label_maturity_time"]
        assert row["prediction_origin_time"] < row["label_maturity_time"]
    labels = [float(origin_rows[horizon]["daily_net_revenue"]) for horizon in (4, 8, 12, 24)]
    assert labels == sorted(labels)
    assert len(set(labels)) == 4


class _UnexpectedRuntime:
    def infer(self, **_: object) -> object:
        raise AssertionError("runtime must not be called for invalid live inputs")


def _forecast_training_records() -> list[dict[str, object]]:
    return [
        {
            "view_name": "forecast_training_view",
            "view_version": FORECASTOPS_FEATURE_SCHEMA_ID,
            "entity_id": row["entity_id"],
            "feature_snapshot_time": row["feature_snapshot_time"].isoformat(),
            "prediction_origin_time": row["prediction_origin_time"].isoformat(),
            "source_snapshot_ids": row["source_snapshot_ids"],
            "is_training_eligible": True,
            "labels": {
                FORECASTOPS_LABEL_NAME: row["daily_net_revenue"],
            },
            "label_maturity_time": row["label_maturity_time"].isoformat(),
            "features": {
                feature_name: row[feature_name] for feature_name in FORECASTOPS_MODEL_FEATURES
            },
        }
        for row in expand_forecast_horizon_rows(_forecast_daily_rows(days=210))
    ]


def _forecast_daily_rows(*, days: int) -> list[dict[str, object]]:
    start = date(2026, 1, 1)
    records: list[dict[str, object]] = []
    for index in range(days):
        business_date = start + timedelta(days=index)
        feature_snapshot_time = datetime.combine(
            business_date,
            datetime.min.time(),
            tzinfo=UTC,
        )
        revenue_lag_1 = 69_000.0 + index * 1_000.0
        records.append(
            {
                "view_name": "forecast_training_view",
                "view_version": FORECASTOPS_FEATURE_SCHEMA_ID,
                "entity_id": f"tenant-live-001:store-live-001:{business_date}",
                "date": business_date,
                "feature_snapshot_time": feature_snapshot_time,
                "prediction_origin_time": feature_snapshot_time + timedelta(microseconds=1),
                "source_snapshot_ids": [f"orders-live-run-{index:03d}"],
                "tenant_id": "tenant-live-001",
                "store_id": "store-live-001",
                "is_training_eligible": True,
                "daily_net_revenue": revenue_lag_1 + 1_000.0,
                "label_maturity_time": feature_snapshot_time + timedelta(days=1),
                "revenue_lag_1": revenue_lag_1,
                "revenue_lag_7": revenue_lag_1 - 6_000.0,
                "rolling_mean_7": revenue_lag_1 - 3_000.0,
                "rolling_mean_28": revenue_lag_1 - 13_500.0,
            }
        )
    return records


def _live_forecast_input(
    *,
    tenant_id: str | None = "tenant-live-001",
    days: int = 35,
) -> ForecastInput:
    start = NOW.date() - timedelta(days=days)
    return ForecastInput(
        tenant_id=tenant_id,
        store_id="store-live-001",
        observations=tuple(
            StoreDayObservation(
                store_id="store-live-001",
                business_date=start + timedelta(days=index),
                actual_revenue=96_000.0 + index * 300.0,
                data_quality_score=0.98,
                source_snapshot_ids=(f"orders-live-score-{index:03d}",),
            )
            for index in range(days)
        ),
        prediction_origin_time=NOW,
    )
