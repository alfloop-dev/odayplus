"""Production AVM OSS execution contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("lightgbm")
pytest.importorskip("mlflow")

import modules.avm.application.valuation as valuation_service
from models.shared_ml import ModelAlias, ModelStage, ModelVersion
from models.shared_ml.oss_estimators import LoadedOSSEstimator, train_oss_estimator
from models.shared_ml.production_runtime import MlflowProductionModelRuntime
from modules.avm import (
    AVMProductionExecutionError,
    AVMProductionExecutor,
    AVMService,
    InMemoryAVMRepository,
    LifelinesLiquiditySurvivalAdapter,
    LiquidityArtifactEvidence,
    LiquidityPrediction,
    LiquidityTrainingRecord,
    ValuationCaseStatus,
)
from modules.learninghub.infrastructure import (
    InMemoryLearningHubRepository,
    MlflowRegistryAdapter,
)


@dataclass
class _Inference:
    lower: tuple[float, ...] = (800_000.0,)
    point: tuple[float, ...] = (1_000_000.0,)
    upper: tuple[float, ...] = (1_250_000.0,)

    def to_audit_metadata(self) -> dict[str, Any]:
        return {
            "model_version": "approved-avm-v7",
            "model_engine": "lightgbm.LGBMRegressor",
            "artifact_sha256": "sha256:" + ("a" * 64),
            "dataset_snapshot_id": "avm-training-2026-07",
            "approved_by": "model-risk",
        }


class _ModelRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def infer(self, **kwargs: Any) -> _Inference:
        self.calls.append(kwargs)
        return _Inference()


class _LiquidityRuntime:
    model_version = "liquidity-v3"
    feature_names = ("normalized_gm", "quality_score")

    def __init__(self) -> None:
        self.calls: list[dict[str, float]] = []

    def predict(self, features: dict[str, float]) -> LiquidityPrediction:
        self.calls.append(features)
        return LiquidityPrediction(
            sale_probability_30d=0.35,
            sale_probability_90d=0.75,
            expected_days=61.0,
            model_version=self.model_version,
            feature_names=self.feature_names,
        )


def _input() -> dict[str, Any]:
    return {
        "store_id": "store-live",
        "gm_ttm": 400_000,
        "forecast_gm_next_12m": 450_000,
        "asset_book_value": 200_000,
        "equipment_fair_value": 100_000,
        "quality_score": 0.95,
        "source_snapshot_ids": ["finance-snapshot-live"],
        "prediction_origin_time": datetime(2026, 7, 24, tzinfo=UTC),
    }


def _executor() -> tuple[AVMProductionExecutor, _ModelRuntime, _LiquidityRuntime]:
    model = _ModelRuntime()
    liquidity = _LiquidityRuntime()
    return (
        AVMProductionExecutor(
            model_runtime=model,
            liquidity_runtime=liquidity,
            liquidity_evidence=LiquidityArtifactEvidence(
                artifact_uri="gs://models/liquidity-v3.json",
                artifact_sha256="sha256:" + ("b" * 64),
                model_version="liquidity-v3",
                approved_by="model-risk",
                approved_at=datetime(2026, 7, 23, tzinfo=UTC),
                dataset_snapshot_id="liquidity-training-2026-07",
            ),
        ),
        model,
        liquidity,
    )


def test_production_avm_executes_approved_model_and_lifelines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setattr(
        valuation_service,
        "value_store",
        lambda *_args, **_kwargs: pytest.fail("heuristic AVM fallback was called"),
    )
    executor, model, liquidity = _executor()
    repository = InMemoryAVMRepository()
    service = AVMService(repository=repository, production_executor=executor)
    case = service.create_case(_input(), created_by="finance", correlation_id="corr-avm")

    report = service.value(case.case_id, actor="worker", correlation_id="corr-avm")

    assert len(model.calls) == 1
    assert len(liquidity.calls) == 1
    assert model.calls[0]["rows"][0]["liquidity_expected_days"] == 61.0
    assert report.fair_price.to_dict() == {
        "p10": 800_000.0,
        "p50": 1_000_000.0,
        "p90": 1_250_000.0,
    }
    assert report.model_version == "approved-avm-v7"
    assert report.execution_metadata["model"]["model_engine"] == "lightgbm.LGBMRegressor"
    assert report.execution_metadata["liquidity"]["engine"] == "lifelines.CoxPHFitter"
    assert report.execution_metadata["source_snapshot_ids"] == ["finance-snapshot-live"]


def test_production_avm_failure_does_not_persist_fake_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    executor, _model, _liquidity = _executor()
    executor.model_runtime.infer = lambda **_kwargs: (_ for _ in ()).throw(
        RuntimeError("registry unavailable")
    )
    repository = InMemoryAVMRepository()
    service = AVMService(repository=repository, production_executor=executor)
    case = service.create_case(_input(), created_by="finance", correlation_id="corr-avm")

    with pytest.raises(AVMProductionExecutionError):
        service.value(case.case_id, actor="worker", correlation_id="corr-avm")

    assert repository.latest_report(case.case_id) is None
    assert repository.get_case(case.case_id).status is ValuationCaseStatus.DATA_READY


def test_production_avm_reloads_and_executes_real_oss_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    training_rows = [
        {
            "normalized_gm": float(300_000 + index * 10_000),
            "quality_score": float(0.7 + index * 0.01),
            "liquidity_expected_days": float(80 - index),
        }
        for index in range(20)
    ]
    labels = [
        row["normalized_gm"] * 2.2
        + row["quality_score"] * 100_000
        - row["liquidity_expected_days"] * 1_000
        for row in training_rows
    ]
    trained = train_oss_estimator(
        algorithm="lightgbm_regressor",
        feature_rows=training_rows,
        labels=labels,
        feature_names=(
            "normalized_gm",
            "quality_score",
            "liquidity_expected_days",
        ),
    )
    artifact_path = tmp_path / "avm-lightgbm.zip"
    artifact_path.write_bytes(trained.estimator.to_artifact_bytes())
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    registry = MlflowRegistryAdapter(
        InMemoryLearningHubRepository(),
        tracking_uri=tracking_uri,
        experiment_name="avm-production-execution",
    )
    registry.register_model_version(
        ModelVersion(
            model_name="avm",
            version="2026.07.24",
            artifact_uri=artifact_path.as_uri(),
            dataset_snapshot_id="avm-training-live",
            feature_schema_version="valuation-view-v1",
            label_version="avm-sale-price-v2",
            metrics={"mae": 20_000.0},
            stage=ModelStage.PRODUCTION,
            aliases=frozenset({ModelAlias.PRODUCTION}),
            run_id="avm-training-run",
            git_sha="test-sha",
            approved_by="model-risk",
            approved_at=datetime(2026, 7, 23, tzinfo=UTC),
        )
    )
    liquidity = LifelinesLiquiditySurvivalAdapter().fit(
        [
            LiquidityTrainingRecord(
                duration_days=float(20 + index * 4),
                sold=index % 3 != 0,
                features={
                    "quality_score": 0.7 + index * 0.015,
                    "liquidity_discount": 0.05 + index * 0.005,
                },
            )
            for index in range(12)
        ]
    )
    calls = {"lightgbm": 0, "lifelines": 0}
    original_estimator_predict = LoadedOSSEstimator.predict
    original_liquidity_predict = LifelinesLiquiditySurvivalAdapter.predict

    def estimator_spy(self, rows):
        calls["lightgbm"] += 1
        return original_estimator_predict(self, rows)

    def liquidity_spy(self, features):
        calls["lifelines"] += 1
        return original_liquidity_predict(self, features)

    monkeypatch.setattr(LoadedOSSEstimator, "predict", estimator_spy)
    monkeypatch.setattr(
        LifelinesLiquiditySurvivalAdapter,
        "predict",
        liquidity_spy,
    )
    executor = AVMProductionExecutor(
        model_runtime=MlflowProductionModelRuntime(tracking_uri=tracking_uri),
        liquidity_runtime=liquidity,
        liquidity_evidence=LiquidityArtifactEvidence(
            artifact_uri="gs://models/avm-liquidity.json",
            artifact_sha256="sha256:" + ("c" * 64),
            model_version=liquidity.model_version,
            approved_by="model-risk",
            approved_at=datetime(2026, 7, 23, tzinfo=UTC),
            dataset_snapshot_id="liquidity-training-live",
        ),
    )
    service = AVMService(production_executor=executor)
    case = service.create_case(
        _input(),
        created_by="finance",
        correlation_id="corr-avm-real",
    )

    report = service.value(
        case.case_id,
        actor="worker",
        correlation_id="corr-avm-real",
    )

    assert calls == {"lightgbm": 1, "lifelines": 1}
    assert report.execution_metadata["model"]["model_engine"] == ("lightgbm.LGBMRegressor")
    assert report.execution_metadata["model"]["model_approved_by"] == "model-risk"
    assert report.execution_metadata["liquidity"]["engine"] == ("lifelines.CoxPHFitter")
    assert report.execution_metadata["liquidity"]["library_version"]
