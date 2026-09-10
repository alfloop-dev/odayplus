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
from models.shared_ml.production_contracts import PRODUCTION_MODEL_CONTRACTS
from models.shared_ml.production_runtime import MlflowProductionModelRuntime
from modules.avm import (
    AVM_DEPRECIATION_LEGACY_VERSION,
    AVM_DEPRECIATION_VERSION,
    AVM_FEATURE_VERSION,
    AVMProductionExecutor,
    AVMService,
    DepreciationCutoverEvidence,
    DepreciationRollbackReceipt,
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
    def __init__(self, inference: _Inference | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._inference = inference or _Inference()

    def infer(self, **kwargs: Any) -> _Inference:
        self.calls.append(kwargs)
        return self._inference


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
        "equipment_depreciation_basis": "appraised_fair_value",
        "quality_score": 0.95,
        "source_snapshot_ids": ["finance-snapshot-live"],
        "prediction_origin_time": datetime(2026, 7, 24, tzinfo=UTC),
    }


def _executor(
    *,
    model: _ModelRuntime | None = None,
    with_cutover_evidence: bool = True,
) -> tuple[AVMProductionExecutor, _ModelRuntime, _LiquidityRuntime]:
    model_rt = model or _ModelRuntime()
    liquidity = _LiquidityRuntime()
    cutover = (
        DepreciationCutoverEvidence(
            approved_by="finance-vp",
            approved_at=datetime(2026, 9, 3, tzinfo=UTC),
            thresholds_reference="docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md#r-4",
            model_version="avm-depreciation-straight-line-v1",
            numerical_threshold_asset_delta_ratio=0.20,
        )
        if with_cutover_evidence
        else None
    )
    return (
        AVMProductionExecutor(
            model_runtime=model_rt,
            liquidity_runtime=liquidity,
            liquidity_evidence=LiquidityArtifactEvidence(
                artifact_uri="gs://models/liquidity-v3.json",
                artifact_sha256="sha256:" + ("b" * 64),
                model_version="liquidity-v3",
                approved_by="model-risk",
                approved_at=datetime(2026, 7, 23, tzinfo=UTC),
                dataset_snapshot_id="liquidity-training-2026-07",
            ),
            depreciation_cutover_evidence=cutover,
        ),
        model_rt,
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

    with pytest.raises(valuation_service.AVMError):
        service.value(case.case_id, actor="worker", correlation_id="corr-avm")

    assert repository.latest_report(case.case_id) is None
    # R5: Persisted case transitions to REVIEW_REQUIRED upon valuation failure
    assert repository.get_case(case.case_id).status is ValuationCaseStatus.REVIEW_REQUIRED


def test_production_avm_reloads_and_executes_real_oss_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
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
            model_name=PRODUCTION_MODEL_CONTRACTS["avm"].model_name or "",
            version="2026.07.24",
            artifact_uri=artifact_path.as_uri(),
            dataset_snapshot_id="avm-training-live",
            feature_schema_version=AVM_FEATURE_VERSION,
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
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
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
        model_runtime=MlflowProductionModelRuntime(
            tracking_uri="https://mlflow.internal.example",
            client=registry.client,
            artifact_loader=lambda _uri, _tracking_uri: artifact_path.read_bytes(),
            model_names={
                "avm": PRODUCTION_MODEL_CONTRACTS["avm"].model_name or "dealroom_avm"
            },
        ),
        liquidity_runtime=liquidity,
        liquidity_evidence=LiquidityArtifactEvidence(
            artifact_uri="gs://models/avm-liquidity.json",
            artifact_sha256="sha256:" + ("c" * 64),
            model_version=liquidity.model_version,
            approved_by="model-risk",
            approved_at=datetime(2026, 7, 23, tzinfo=UTC),
            dataset_snapshot_id="liquidity-training-live",
        ),
        depreciation_cutover_evidence=DepreciationCutoverEvidence(
            approved_by="finance-vp",
            approved_at=datetime(2026, 9, 3, tzinfo=UTC),
            thresholds_reference="docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md#r-4",
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


def test_production_avm_executes_with_straight_line_depreciation_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding F1: Production executor calculates depreciation and adjusts interval by delta_from_undepreciated."""
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setattr(
        valuation_service,
        "value_store",
        lambda *_args, **_kwargs: pytest.fail("heuristic AVM fallback was called"),
    )
    executor, model, liquidity = _executor()
    repository = InMemoryAVMRepository()
    service = AVMService(repository=repository, production_executor=executor)

    dep_input = {
        "store_id": "store-live-dep",
        "gm_ttm": 400_000,
        "forecast_gm_next_12m": 450_000,
        "asset_book_value": 200_000,
        "equipment_fair_value": 100_000,
        "equipment_depreciation_basis": "original_cost",
        "equipment_original_cost": 100_000.0,
        "useful_life_months": 84,
        "residual_value_ratio": 0.10,
        "depreciation_method": "straight_line",
        "asset_in_service_date": "2023-01-01",
        "depreciation_effective_date": "2026-07-01",
        "quality_score": 0.95,
        "source_snapshot_ids": ["finance-snapshot-live"],
        "prediction_origin_time": datetime(2026, 7, 24, tzinfo=UTC),
    }
    case = service.create_case(dep_input, created_by="finance", correlation_id="corr-avm-dep")
    report = service.value(case.case_id, actor="worker", correlation_id="corr-avm-dep")

    assert report.fair_price.to_dict() == {
        "p10": 755_000.0,
        "p50": 955_000.0,
        "p90": 1_205_000.0,
    }
    assert report.depreciation_applied is True
    assert report.depreciation_version == "avm-depreciation-straight-line-v1"
    assert "depreciation" in report.execution_metadata
    dep_meta = report.execution_metadata["depreciation"]
    assert dep_meta["basis"] == "original_cost"
    assert dep_meta["elapsed_months"] == 42
    assert dep_meta["accumulated_depreciation"] == 45_000.0
    assert "depreciation_cutover_approval" in report.execution_metadata


def test_production_avm_cutover_fails_without_finance_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1: Production cutover is gated; absent Finance approval rejects v1 depreciation."""
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    executor, _model, _liquidity = _executor(with_cutover_evidence=False)
    repository = InMemoryAVMRepository()
    service = AVMService(repository=repository, production_executor=executor)

    dep_input = {
        "store_id": "store-live-dep-ungated",
        "gm_ttm": 400_000,
        "forecast_gm_next_12m": 450_000,
        "asset_book_value": 200_000,
        "equipment_fair_value": 100_000,
        "equipment_depreciation_basis": "original_cost",
        "equipment_original_cost": 100_000.0,
        "useful_life_months": 84,
        "residual_value_ratio": 0.10,
        "depreciation_method": "straight_line",
        "asset_in_service_date": "2023-01-01",
        "depreciation_effective_date": "2026-07-01",
        "quality_score": 0.95,
        "source_snapshot_ids": ["finance-snapshot-live"],
        "prediction_origin_time": datetime(2026, 7, 24, tzinfo=UTC),
    }
    case = service.create_case(dep_input, created_by="finance", correlation_id="corr-ungated")

    with pytest.raises(valuation_service.AVMError, match="authentic Finance approval"):
        service.value(case.case_id, actor="worker", correlation_id="corr-ungated")

    assert repository.latest_report(case.case_id) is None
    assert repository.get_case(case.case_id).status is ValuationCaseStatus.REVIEW_REQUIRED


def test_production_avm_rejects_negative_lower_bound_even_with_depreciation_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3: Raw model intervals are validated before depreciation adjustment."""
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    raw_invalid_model = _ModelRuntime(
        inference=_Inference(lower=(-1.0,), point=(100_000.0,), upper=(200_000.0,))
    )
    executor, _model, _liquidity = _executor(model=raw_invalid_model)
    repository = InMemoryAVMRepository()
    service = AVMService(repository=repository, production_executor=executor)

    dep_input = {
        "store_id": "store-raw-invalid",
        "gm_ttm": 400_000,
        "forecast_gm_next_12m": 450_000,
        "asset_book_value": 200_000,
        "equipment_fair_value": 100_000,
        "equipment_depreciation_basis": "original_cost",
        "equipment_original_cost": 100_000.0,
        "useful_life_months": 84,
        "residual_value_ratio": 0.10,
        "depreciation_method": "straight_line",
        "asset_in_service_date": "2023-01-01",
        "depreciation_effective_date": "2026-07-01",
        "quality_score": 0.95,
        "source_snapshot_ids": ["finance-snapshot-live"],
        "prediction_origin_time": datetime(2026, 7, 24, tzinfo=UTC),
    }
    case = service.create_case(dep_input, created_by="finance", correlation_id="corr-raw-invalid")

    with pytest.raises(valuation_service.AVMError, match="invalid valuation interval"):
        service.value(case.case_id, actor="worker", correlation_id="corr-raw-invalid")


def test_production_avm_paired_inputs_differing_only_in_age_produce_different_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6: Paired production inputs differing only in age produce distinct expected prices."""
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    executor, _model, _liquidity = _executor()
    repository = InMemoryAVMRepository()
    service = AVMService(repository=repository, production_executor=executor)

    base_payload = {
        "store_id": "store-paired-age",
        "gm_ttm": 400_000,
        "forecast_gm_next_12m": 450_000,
        "asset_book_value": 200_000,
        "equipment_fair_value": 100_000,
        "equipment_depreciation_basis": "original_cost",
        "equipment_original_cost": 100_000.0,
        "useful_life_months": 84,
        "residual_value_ratio": 0.10,
        "depreciation_method": "straight_line",
        "depreciation_effective_date": "2026-07-01",
        "quality_score": 0.95,
        "source_snapshot_ids": ["finance-snapshot-live"],
        "prediction_origin_time": datetime(2026, 7, 24, tzinfo=UTC),
    }
    young_case = service.create_case(
        {**base_payload, "asset_in_service_date": "2026-01-01"},
        created_by="finance",
        correlation_id="corr-young",
    )
    old_case = service.create_case(
        {**base_payload, "asset_in_service_date": "2023-01-01"},
        created_by="finance",
        correlation_id="corr-old",
    )
    young_rep = service.value(young_case.case_id, actor="worker", correlation_id="corr-young")
    old_rep = service.value(old_case.case_id, actor="worker", correlation_id="corr-old")

    # Young asset: 6 months elapsed, monthly=90k/84=1071.42857 -> accum=6428.57, delta=-6428.57
    # Old asset: 42 months elapsed, accum=45000.0, delta=-45000.0
    assert old_rep.fair_price.p50 < young_rep.fair_price.p50
    assert old_rep.fair_price.p50 == 955_000.0
    assert young_rep.fair_price.p50 == 993_571.43


def test_production_avm_operational_rollback_to_v0_with_valid_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2 & R6: Operational rollback to v0 requires a matching valid R-3 receipt and reproduces v0 prices while preserving issued v1 cards."""
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    executor, _model, _liquidity = _executor()
    repository = InMemoryAVMRepository()
    service = AVMService(repository=repository, production_executor=executor)

    dep_input = {
        "store_id": "store-rollback-test",
        "gm_ttm": 400_000,
        "forecast_gm_next_12m": 450_000,
        "asset_book_value": 200_000,
        "equipment_fair_value": 100_000,
        "equipment_depreciation_basis": "original_cost",
        "equipment_original_cost": 100_000.0,
        "useful_life_months": 84,
        "residual_value_ratio": 0.10,
        "depreciation_method": "straight_line",
        "asset_in_service_date": "2023-01-01",
        "depreciation_effective_date": "2026-07-01",
        "quality_score": 0.95,
        "source_snapshot_ids": ["finance-snapshot-live"],
        "prediction_origin_time": datetime(2026, 7, 24, tzinfo=UTC),
    }
    case = service.create_case(dep_input, created_by="finance", correlation_id="corr-rb-1")
    v1_report = service.value(case.case_id, actor="worker", correlation_id="corr-rb-1")
    assert v1_report.depreciation_applied is True
    assert v1_report.depreciation_version == AVM_DEPRECIATION_VERSION
    assert v1_report.fair_price.p50 == 955_000.0

    # 1. Rollback without receipt fails
    with pytest.raises(valuation_service.AVMError, match="requires a valid matching DepreciationRollbackReceipt"):
        service.value(
            case.case_id,
            actor="ops-admin",
            correlation_id="corr-rb-no-receipt",
            depreciation_version_pin=AVM_DEPRECIATION_LEGACY_VERSION,
        )

    # 2. Rollback with valid receipt succeeds
    receipt = DepreciationRollbackReceipt(
        decider="finance-director",
        decision_time=datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
        reason="reverting to v0 due to numerical anomaly investigation",
        target_expiry=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
        depreciation_version_pin=AVM_DEPRECIATION_LEGACY_VERSION,
    )
    v0_report = service.value(
        case.case_id,
        actor="ops-admin",
        correlation_id="corr-rb-2",
        depreciation_version_pin=AVM_DEPRECIATION_LEGACY_VERSION,
        rollback_receipt=receipt,
    )
    assert v0_report.depreciation_applied is False
    assert v0_report.depreciation_version == AVM_DEPRECIATION_LEGACY_VERSION
    assert v0_report.fair_price.to_dict() == {
        "p10": 800_000.0,
        "p50": 1_000_000.0,
        "p90": 1_250_000.0,
    }
    assert "depreciation_rollback_receipt" in v0_report.execution_metadata
    assert (
        v0_report.execution_metadata["depreciation_rollback_receipt"]["decider"]
        == "finance-director"
    )

    # 3. Preserves previously issued v1 report in history
    history = service.report_history(case.case_id)
    assert len(history) == 2
    assert history[0].valuation_version == 1
    assert history[0].depreciation_version == AVM_DEPRECIATION_VERSION
    assert history[0].depreciation_applied is True
    assert history[0].fair_price.p50 == 955_000.0
    assert history[1].valuation_version == 2
    assert history[1].depreciation_version == AVM_DEPRECIATION_LEGACY_VERSION
    assert history[1].depreciation_applied is False
    assert history[1].fair_price.p50 == 1_000_000.0

