"""Production AVM OSS execution contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import intake_blank_db, intake_pg_server  # noqa: F401

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
    AVMProductionExecutionError,
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
    expected_feature_schema_version: str = AVM_FEATURE_VERSION,
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
            numerical_threshold_unexplained_cohort_ratio=0.05,
            structural_completeness_required=True,
            calibration_coverage_degradation_threshold=0.05,
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
            expected_feature_schema_version=expected_feature_schema_version,
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


@pytest.mark.parametrize("artifact_schema", ["valuation-view-v1", "valuation-view-v2"])
def test_production_avm_reloads_and_executes_real_oss_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, artifact_schema: str, intake_blank_db, request,  # noqa: F811
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
            feature_schema_version=artifact_schema,
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
        expected_feature_schema_version=artifact_schema,
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
            model_version="avm-depreciation-straight-line-v1",
            numerical_threshold_asset_delta_ratio=0.20,
            numerical_threshold_unexplained_cohort_ratio=0.05,
            structural_completeness_required=True,
            calibration_coverage_degradation_threshold=0.05,
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

    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
    # Exercise the real bootstrap and environment fallback with the same
    # registered artifact; no fake runtime may bypass registry schema checks.
    import json
    from dataclasses import replace

    from fastapi.testclient import TestClient

    import modules.avm.application.production as production_module
    from apps.api.oday_api import main as app_module
    from shared.infrastructure.persistence import build_persistence
    from tests.integration._authz import AVM_HEADERS

    monkeypatch.delenv("ODP_AVM_ARTIFACT_SCHEMA_VERSION", raising=False)
    if artifact_schema == "valuation-view-v2":
        monkeypatch.setenv("ODP_AVM_ARTIFACT_SCHEMA_VERSION", artifact_schema)
    assert app_module.production_feature_schema_versions()["avm"] == artifact_schema
    monkeypatch.setattr(MlflowProductionModelRuntime, "from_environment", lambda **kwargs: executor.model_runtime)
    monkeypatch.setattr(production_module, "_load_liquidity_artifact", lambda: (liquidity, executor.liquidity_evidence))
    monkeypatch.setattr(production_module, "_load_depreciation_cutover_evidence_optional", lambda: executor.depreciation_cutover_evidence)
    contracts = dict(app_module.PRODUCTION_MODEL_CONTRACTS)
    contracts["avm"] = replace(contracts["avm"], governed_disabled_binding=None)
    monkeypatch.setattr(app_module, "PRODUCTION_MODEL_CONTRACTS", contracts)
    monkeypatch.setattr(app_module, "governed_disabled_services", lambda: set(contracts) - {"avm"})
    now = datetime.now(UTC)
    receipt = DepreciationRollbackReceipt(decider="fixture-ops", decision_time=now, reason="fixture approved v0 rollback", target_expiry=now + timedelta(hours=1), depreciation_version_pin=AVM_DEPRECIATION_LEGACY_VERSION)
    monkeypatch.setenv("ODP_AVM_DEPRECIATION_VERSION_PIN", AVM_DEPRECIATION_LEGACY_VERSION)
    monkeypatch.setenv("ODP_AVM_DEPRECIATION_ROLLBACK_RECEIPT_JSON", json.dumps(receipt.to_dict()))
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
    from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
    from tests.integration.test_avm_valuation import _provision_canonical_schema

    _provision_canonical_schema(intake_blank_db)
    monkeypatch.setenv("ODAY_DATABASE_URL", intake_blank_db.url())
    apply_upgrade_to_database(intake_blank_db.url())
    bundle = build_persistence(mode="postgresql")
    request.addfinalizer(bundle.engine.close)
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    repository = bundle.avm_repository
    app = app_module.create_app(avm_repository=repository, persistence=bundle)
    assert app.state.production_model_capabilities["avm"]["available"], app.state.production_model_capabilities["avm"]
    case = AVMService(repository=repository).create_case(_input(), created_by="fixture-finance", correlation_id="bootstrap-case")
    client = TestClient(app, headers=AVM_HEADERS)
    response = client.post(f"/avm/cases/{case.case_id}/value", json={"actor": "fixture-worker"})
    assert response.status_code == 200, response.text
    assert response.json()["feature_version"] == artifact_schema
    assert response.json()["normalized_margin"]["feature_version"] == "valuation-view-v2"
    assert case.valuation_input.to_dict()["feature_version"] == "valuation-view-v2"
    assert response.json()["depreciation_version"] == AVM_DEPRECIATION_LEGACY_VERSION
    assert response.json()["execution_metadata"]["model"]["feature_schema_version"] == artifact_schema

    fallback = AVMService(rollback_receipt=receipt, depreciation_version_pin=AVM_DEPRECIATION_LEGACY_VERSION)
    case = fallback.create_case(_input(), created_by="fixture-finance", correlation_id="fallback-case")
    report = fallback.value(case.case_id, actor="fixture-worker", correlation_id="fallback-case")
    assert report.feature_version == artifact_schema
    assert report.depreciation_version == AVM_DEPRECIATION_LEGACY_VERSION


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


def test_production_avm_cutover_fails_with_incomplete_or_wrong_version_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N1: Complete, valid approval matching active depreciation policy is required; incomplete/mismatched rejects."""
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")

    # 1. Wrong model version in cutover evidence
    wrong_version_evidence = DepreciationCutoverEvidence(
        approved_by="finance-vp",
        approved_at=datetime(2026, 9, 3, tzinfo=UTC),
        thresholds_reference="docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md#r-4",
        model_version="avm-depreciation-experimental-v9",
        numerical_threshold_asset_delta_ratio=0.20,
        numerical_threshold_unexplained_cohort_ratio=0.05,
        structural_completeness_required=True,
        calibration_coverage_degradation_threshold=0.05,
    )
    exec_wrong, _model, _liquidity = _executor()
    exec_wrong.depreciation_cutover_evidence = wrong_version_evidence
    repo = InMemoryAVMRepository()
    svc = AVMService(repository=repo, production_executor=exec_wrong)

    dep_input = {
        "store_id": "store-live-dep-mismatch",
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
    case1 = svc.create_case(dep_input, created_by="finance", correlation_id="corr-mismatch")
    with pytest.raises(valuation_service.AVMError, match="does not match active depreciation policy"):
        svc.value(case1.case_id, actor="worker", correlation_id="corr-mismatch")
    assert repo.get_case(case1.case_id).status is ValuationCaseStatus.REVIEW_REQUIRED

    # 2. Invalid / non-finite threshold rejection in dataclass
    with pytest.raises(AVMProductionExecutionError, match="finite positive number"):
        DepreciationCutoverEvidence(
            approved_by="finance-vp",
            approved_at=datetime(2026, 9, 3, tzinfo=UTC),
            thresholds_reference="docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md#r-4",
            model_version="avm-depreciation-straight-line-v1",
            numerical_threshold_asset_delta_ratio=0.0,
            numerical_threshold_unexplained_cohort_ratio=0.05,
            structural_completeness_required=True,
            calibration_coverage_degradation_threshold=0.05,
        )

    with pytest.raises(AVMProductionExecutionError, match="finite positive number"):
        DepreciationCutoverEvidence(
            approved_by="finance-vp",
            approved_at=datetime(2026, 9, 3, tzinfo=UTC),
            thresholds_reference="docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md#r-4",
            model_version="avm-depreciation-straight-line-v1",
            numerical_threshold_asset_delta_ratio=float("nan"),
            numerical_threshold_unexplained_cohort_ratio=0.05,
            structural_completeness_required=True,
            calibration_coverage_degradation_threshold=0.05,
        )

    # 3. Invalid unexplained cohort ratio rejection
    with pytest.raises(AVMProductionExecutionError, match="unexplained cohort threshold"):
        DepreciationCutoverEvidence(
            approved_by="finance-vp",
            approved_at=datetime(2026, 9, 3, tzinfo=UTC),
            thresholds_reference="docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md#r-4",
            model_version="avm-depreciation-straight-line-v1",
            numerical_threshold_asset_delta_ratio=0.20,
            numerical_threshold_unexplained_cohort_ratio=1.5,
            structural_completeness_required=True,
            calibration_coverage_degradation_threshold=0.05,
        )

    # 4. Structural completeness required rejection when not True
    with pytest.raises(AVMProductionExecutionError, match="structural completeness must be required"):
        DepreciationCutoverEvidence(
            approved_by="finance-vp",
            approved_at=datetime(2026, 9, 3, tzinfo=UTC),
            thresholds_reference="docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md#r-4",
            model_version="avm-depreciation-straight-line-v1",
            numerical_threshold_asset_delta_ratio=0.20,
            numerical_threshold_unexplained_cohort_ratio=0.05,
            structural_completeness_required=False,
            calibration_coverage_degradation_threshold=0.05,
        )

    # 5. Invalid calibration degradation threshold rejection
    with pytest.raises(AVMProductionExecutionError, match="calibration degradation threshold"):
        DepreciationCutoverEvidence(
            approved_by="finance-vp",
            approved_at=datetime(2026, 9, 3, tzinfo=UTC),
            thresholds_reference="docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md#r-4",
            model_version="avm-depreciation-straight-line-v1",
            numerical_threshold_asset_delta_ratio=0.20,
            numerical_threshold_unexplained_cohort_ratio=0.05,
            structural_completeness_required=True,
            calibration_coverage_degradation_threshold=0.0,
        )

    # 6. Incomplete env vars
    monkeypatch.setenv("ODP_AVM_DEPRECIATION_CUTOVER_APPROVED_BY", "finance-vp")
    monkeypatch.setenv("ODP_AVM_DEPRECIATION_CUTOVER_APPROVED_AT", "2026-09-03T00:00:00Z")
    monkeypatch.setenv("ODP_AVM_DEPRECIATION_CUTOVER_THRESHOLDS_REFERENCE", "docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md#r-4")
    monkeypatch.setenv("ODP_AVM_DEPRECIATION_CUTOVER_MODEL_VERSION", "avm-depreciation-straight-line-v1")
    monkeypatch.setenv("ODP_AVM_DEPRECIATION_CUTOVER_DELTA_THRESHOLD", "0.20")
    monkeypatch.setenv("ODP_AVM_DEPRECIATION_CUTOVER_UNEXPLAINED_COHORT_THRESHOLD", "0.05")
    monkeypatch.setenv("ODP_AVM_DEPRECIATION_CUTOVER_STRUCTURAL_COMPLETENESS_REQUIRED", "true")
    monkeypatch.delenv("ODP_AVM_DEPRECIATION_CUTOVER_CALIBRATION_DEGRADATION_THRESHOLD", raising=False)
    with pytest.raises(AVMProductionExecutionError, match="ODP_AVM_DEPRECIATION_CUTOVER_CALIBRATION_DEGRADATION_THRESHOLD is required for cutover"):
        AVMProductionExecutor.from_environment()


def test_production_avm_feature_schema_versions_and_report_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding P2: Support both v2 active input schema and v1 legacy artifact execution with correct provenance."""
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")

    # 1. Default executor uses AVM_FEATURE_VERSION (v2)
    exec_v2, model_v2, _ = _executor(expected_feature_schema_version="valuation-view-v2")
    service_v2 = AVMService(repository=InMemoryAVMRepository(), production_executor=exec_v2)
    case_v2 = service_v2.create_case(_input(), created_by="finance", correlation_id="corr-v2")
    report_v2 = service_v2.value(case_v2.case_id, actor="worker", correlation_id="corr-v2")

    assert model_v2.calls[0]["expected_feature_schema_version"] == "valuation-view-v2"
    assert report_v2.feature_version == "valuation-view-v2"

    # 2. Legacy v1 artifact rollback executor uses valuation-view-v1
    exec_v1, model_v1, _ = _executor(expected_feature_schema_version="valuation-view-v1")
    service_v1 = AVMService(repository=InMemoryAVMRepository(), production_executor=exec_v1)
    case_v1 = service_v1.create_case(_input(), created_by="finance", correlation_id="corr-v1")
    report_v1 = service_v1.value(case_v1.case_id, actor="worker", correlation_id="corr-v1")

    assert model_v1.calls[0]["expected_feature_schema_version"] == "valuation-view-v1"
    assert report_v1.feature_version == "valuation-view-v1"


def test_production_avm_cutover_fails_when_depreciation_evidence_is_structurally_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4 / P1: Structural condition requires full depreciation evidence keys when depreciation is applied."""
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    executor, _model, _liquidity = _executor()
    repository = InMemoryAVMRepository()
    service = AVMService(repository=repository, production_executor=executor)

    dep_input = {
        "store_id": "store-live-dep-struct",
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

    from modules.avm.domain import calculate_depreciation as orig_calc_dep
    from modules.avm.domain.valuation import DepreciationCalculationResult

    def broken_calc_dep(*args: Any, **kwargs: Any) -> DepreciationCalculationResult:
        res = orig_calc_dep(*args, **kwargs)
        incomplete_evidence = dict(res.evidence or {})
        incomplete_evidence.pop("accumulated_depreciation", None)
        incomplete_evidence.pop("useful_life_months", None)
        return DepreciationCalculationResult(
            depreciation_version=res.depreciation_version,
            depreciation_applied=res.depreciation_applied,
            equipment_value_after_depreciation=res.equipment_value_after_depreciation,
            asset_p50=res.asset_p50,
            evidence=incomplete_evidence,
            delta_from_undepreciated=res.delta_from_undepreciated,
            accumulated_depreciation=res.accumulated_depreciation,
            residual=res.residual,
            elapsed_months=res.elapsed_months,
        )

    monkeypatch.setattr("modules.avm.application.production.calculate_depreciation", broken_calc_dep)
    case = service.create_case(dep_input, created_by="finance", correlation_id="corr-struct")
    with pytest.raises(valuation_service.AVMError, match="depreciation evidence is structurally incomplete"):
        service.value(case.case_id, actor="worker", correlation_id="corr-struct")


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

    # 2. Rollback with expired receipt fails
    now = datetime.now(UTC)
    expired_receipt = DepreciationRollbackReceipt(
        decider="finance-director",
        decision_time=now - timedelta(days=10),
        reason="reverting to v0 due to numerical anomaly investigation",
        target_expiry=now - timedelta(minutes=5),
        depreciation_version_pin=AVM_DEPRECIATION_LEGACY_VERSION,
    )
    with pytest.raises(valuation_service.AVMError, match="has already expired"):
        service.value(
            case.case_id,
            actor="ops-admin",
            correlation_id="corr-rb-expired",
            depreciation_version_pin=AVM_DEPRECIATION_LEGACY_VERSION,
            rollback_receipt=expired_receipt,
        )

    # 3. Rollback with target_expiry before decision_time fails
    inverted_receipt = DepreciationRollbackReceipt(
        decider="finance-director",
        decision_time=now + timedelta(days=2),
        reason="reverting to v0 due to numerical anomaly investigation",
        target_expiry=now + timedelta(days=1),
        depreciation_version_pin=AVM_DEPRECIATION_LEGACY_VERSION,
    )
    with pytest.raises(valuation_service.AVMError, match="target_expiry must be after decision_time"):
        service.value(
            case.case_id,
            actor="ops-admin",
            correlation_id="corr-rb-inverted",
            depreciation_version_pin=AVM_DEPRECIATION_LEGACY_VERSION,
            rollback_receipt=inverted_receipt,
        )

    # 4. Rollback with mismatched version pin fails
    mismatched_receipt = DepreciationRollbackReceipt(
        decider="finance-director",
        decision_time=now - timedelta(hours=1),
        reason="reverting to v0 due to numerical anomaly investigation",
        target_expiry=now + timedelta(days=7),
        depreciation_version_pin="avm-depreciation-straight-line-v1",
    )
    with pytest.raises(valuation_service.AVMError, match="does not match active pin"):
        service.value(
            case.case_id,
            actor="ops-admin",
            correlation_id="corr-rb-mismatched",
            depreciation_version_pin=AVM_DEPRECIATION_LEGACY_VERSION,
            rollback_receipt=mismatched_receipt,
        )

    # 5. Rollback with valid receipt succeeds
    valid_receipt = DepreciationRollbackReceipt(
        decider="finance-director",
        decision_time=now - timedelta(hours=1),
        reason="reverting to v0 due to numerical anomaly investigation",
        target_expiry=now + timedelta(days=10),
        depreciation_version_pin=AVM_DEPRECIATION_LEGACY_VERSION,
    )
    v0_report = service.value(
        case.case_id,
        actor="ops-admin",
        correlation_id="corr-rb-2",
        depreciation_version_pin=AVM_DEPRECIATION_LEGACY_VERSION,
        rollback_receipt=valid_receipt,
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

    # 6. Preserves previously issued v1 report in history
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

