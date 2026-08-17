from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

pytest.importorskip(
    "lightgbm",
    reason="production model runtime verification requires LightGBM",
)
pytest.importorskip(
    "mlflow",
    reason="production model runtime verification requires MLflow",
)

from apps.api.app.routes.sitescore import (
    SiteScoreScoreJobPayload,
    create_sitescore_router,
)
from models.shared_ml import (
    MlflowProductionModelRuntime,
    ModelAlias,
    ModelBinding,
    ModelInferenceResult,
    ModelStage,
    ModelVersion,
    ProductionModelApprovalError,
    ProductionModelArtifactError,
    ProductionModelInputError,
    ProductionModelRegistryError,
    production_model_execution_required,
)
from models.shared_ml.oss_estimators import train_oss_estimator
from models.shared_ml.output_contracts import (
    HEATZONE_OUTPUT_TRANSFORM,
    SITESCORE_OUTPUT_TRANSFORM,
)
from models.shared_ml.production_contracts import PRODUCTION_MODEL_CONTRACTS
from modules.forecastops.application import RegisteredEstimatorForecastEngine
from modules.forecastops.domain import ForecastInput
from modules.heatzone.domain import HEATZONE_FEATURE_VERSION
from modules.heatzone.workers import run_heatzone_batch_score
from modules.learninghub.infrastructure import (
    InMemoryLearningHubRepository,
    MlflowRegistryAdapter,
)
from modules.sitescore.application.reporting import SiteScoreReportService
from modules.sitescore.domain import SITESCORE_FEATURE_VERSION, score_sites
from product_ops.modeling.contracts import MODEL_SPECS

NOW = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
SITESCORE_MODEL_NAME = PRODUCTION_MODEL_CONTRACTS["sitescore"].model_name or ""


def _training_rows() -> list[dict[str, Any]]:
    return [
        {
            "tenant_id": f"tenant-{index % 2 + 1}",
            "target_format_code": "ODAY_G2",
            "h3_index": f"892630828{index % 5:01d}ffff",
            "latitude": 25.03 + index * 0.001,
            "longitude": 121.56 + index * 0.001,
            "geocode_confidence": 0.8 + (index % 5) * 0.03,
            "prior_90d_cell_net_revenue": float(300_000 + index * 18_000),
            "prior_90d_cell_transaction_count": 40 + index * 3,
            "prior_90d_cell_store_count": 1 + index % 4,
        }
        for index in range(20)
    ]


def _live_sitescore_row() -> dict[str, Any]:
    return {
        "candidate_site_id": "candidate-live-001",
        "tenant_id": "tenant-1",
        "target_format_code": "ODAY_G2",
        "h3_index": "8926308280fffff",
        "latitude": 25.033,
        "longitude": 121.565,
        "geocode_confidence": 0.98,
        "prior_90d_cell_net_revenue": 612_000.0,
        "prior_90d_cell_transaction_count": 92,
        "prior_90d_cell_store_count": 3,
        "heat_zone_score": 82.0,
        "monthly_rent": 52_000.0,
        "area_ping": 24.0,
        "comparable_store_count": 4,
        "source_snapshot_ids": ["poi-live-001", "listing-live-001"],
        "feature_snapshot_time": NOW.isoformat(),
        "view_version": SITESCORE_FEATURE_VERSION,
    }


def _registered_runtime(
    tmp_path: Path,
    *,
    stage: ModelStage = ModelStage.PRODUCTION,
    approved: bool = True,
) -> tuple[MlflowProductionModelRuntime, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    rows = _training_rows()
    labels = [
        180_000.0
        + row["prior_90d_cell_net_revenue"] * 0.9
        + row["prior_90d_cell_transaction_count"] * 1_500.0
        - row["prior_90d_cell_store_count"] * 20_000.0
        for row in rows
    ]
    trained = train_oss_estimator(
        algorithm="lightgbm_regressor",
        feature_rows=rows,
        labels=labels,
        feature_names=MODEL_SPECS["sitescore"].feature_columns,
    )
    artifact_path = tmp_path / "sitescore-lightgbm.zip"
    artifact_path.write_bytes(trained.estimator.to_artifact_bytes())
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    adapter = MlflowRegistryAdapter(
        InMemoryLearningHubRepository(),
        tracking_uri=tracking_uri,
        experiment_name="production-model-runtime-tests",
    )
    adapter.register_model_version(
        ModelVersion(
            model_name=SITESCORE_MODEL_NAME,
            version="2026.07.24",
            artifact_uri=artifact_path.as_uri(),
            dataset_snapshot_id="sitescore-training-live-20260724",
            feature_schema_version=SITESCORE_FEATURE_VERSION,
            label_version="sitescore-mature-revenue-v3",
            metrics={"mae": 9_000.0, "p80_coverage": 0.82},
            stage=stage,
            aliases=frozenset({ModelAlias.PRODUCTION}),
            run_id="training-sitescore-20260724",
            git_sha="4d5e5e0",
            approved_by="model-risk-reviewer" if approved else None,
            approved_at=NOW if approved else None,
            monitoring_config={
                "output_transform": dict(SITESCORE_OUTPUT_TRANSFORM),
            },
        )
    )
    return (
        MlflowProductionModelRuntime(
            tracking_uri=tracking_uri,
            # Explicitly declare the sitescore name mapping for this integration
            # test. Since sitescore is governed-disabled in production, the default
            # production_model_names() no longer includes it; the test exercises the
            # runtime directly with a real registered artifact.
            model_names={"sitescore": SITESCORE_MODEL_NAME},
        ),
        artifact_path,
    )


def test_real_lightgbm_artifact_reload_and_sitescore_inference(tmp_path: Path) -> None:
    runtime, _ = _registered_runtime(tmp_path)
    row = _live_sitescore_row()

    inference = runtime.infer(
        service="sitescore",
        rows=[row],
        expected_feature_schema_version=SITESCORE_FEATURE_VERSION,
    )
    execution = SiteScoreReportService(
        model_runtime=runtime,
        require_production_model=True,
    ).score_candidates_with_execution([row], prediction_origin_time=NOW, scored_at=NOW)
    baseline = score_sites([row], prediction_origin_time=NOW, scored_at=NOW)[0]
    report = execution.reports[0]

    assert inference.engine == "lightgbm.LGBMRegressor"
    assert inference.binding.stage == ModelStage.PRODUCTION.value
    assert inference.binding.approved_by == "model-risk-reviewer"
    assert inference.binding.artifact_sha256
    assert inference.lower[0] <= inference.point[0] <= inference.upper[0]
    expected_monthly = max(0.0, inference.point[0]) * 30.4375 / 90.0
    assert report.m12.p50 == round(expected_monthly, 2)
    assert report.model_version == f"{SITESCORE_MODEL_NAME}:2026.07.24"
    assert report.m12.p50 != baseline.m12.p50


def test_production_sitescore_route_rejects_metadata_without_runtime() -> None:
    router = create_sitescore_router(
        model_binding=_binding("sitescore"),
        require_production_model=True,
    )
    endpoint = _route_endpoint(router, "/sitescore/score-jobs")

    with pytest.raises(HTTPException) as exc_info:
        endpoint(
            SiteScoreScoreJobPayload(features=[_live_sitescore_row()]),
            _request(),
            None,
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "PRODUCTION_MODEL_REGISTRY_UNAVAILABLE"


def test_production_sitescore_route_executes_registered_artifact(tmp_path: Path) -> None:
    runtime, _ = _registered_runtime(tmp_path)
    router = create_sitescore_router(
        model_runtime=runtime,
        require_production_model=True,
    )
    payload = _route_endpoint(router, "/sitescore/score-jobs")(
        SiteScoreScoreJobPayload(features=[_live_sitescore_row()]),
        _request(),
        None,
    )

    assert payload["model_binding"]["model_engine"] == "lightgbm.LGBMRegressor"
    assert payload["model_binding"]["model_approved_by"] == "model-risk-reviewer"
    assert payload["reports"][0]["model_version"] == f"{SITESCORE_MODEL_NAME}:2026.07.24"


def test_production_runtime_fails_closed_without_registry_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    with pytest.raises(ProductionModelRegistryError, match="MLFLOW_TRACKING_URI"):
        MlflowProductionModelRuntime()


def test_live_data_requirement_cannot_be_downgraded_to_poc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    assert production_model_execution_required() is True


def test_production_runtime_rejects_unapproved_or_nonproduction_alias(
    tmp_path: Path,
) -> None:
    unapproved, _ = _registered_runtime(tmp_path / "unapproved", approved=False)
    with pytest.raises(ProductionModelApprovalError):
        unapproved.infer(
            service="sitescore",
            rows=[_live_sitescore_row()],
            expected_feature_schema_version=SITESCORE_FEATURE_VERSION,
        )

    canary, _ = _registered_runtime(
        tmp_path / "canary",
        stage=ModelStage.CANARY,
    )
    with pytest.raises(ProductionModelApprovalError):
        canary.infer(
            service="sitescore",
            rows=[_live_sitescore_row()],
            expected_feature_schema_version=SITESCORE_FEATURE_VERSION,
        )


def test_production_runtime_rejects_tampered_artifact(tmp_path: Path) -> None:
    runtime, artifact_path = _registered_runtime(tmp_path)
    artifact_path.write_bytes(b"tampered")

    with pytest.raises(ProductionModelArtifactError, match="digest"):
        runtime.infer(
            service="sitescore",
            rows=[_live_sitescore_row()],
            expected_feature_schema_version=SITESCORE_FEATURE_VERSION,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_snapshot_ids", []),
        ("feature_snapshot_time", None),
        ("view_version", "candidate-site-view-v0"),
        ("prior_90d_cell_net_revenue", None),
    ],
)
def test_production_runtime_rejects_incomplete_live_input_lineage(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    runtime, _ = _registered_runtime(tmp_path)
    row = {**_live_sitescore_row(), field: value}

    with pytest.raises(ProductionModelInputError):
        runtime.infer(
            service="sitescore",
            rows=[row],
            expected_feature_schema_version=SITESCORE_FEATURE_VERSION,
        )


def test_heatzone_and_forecast_adapters_use_runtime_outputs() -> None:
    heatzone_runtime = _StubRuntime(points=(91.0,))
    heat_result = run_heatzone_batch_score(
        features=[
            {
                "h3_index": "h3-live-001",
                "tenant_id": "tenant-live-001",
                "h3_resolution": 9,
                "cell_latitude": 25.033,
                "cell_longitude": 121.565,
                "average_geocode_confidence": 0.98,
                "prior_opened_store_count": 1,
                "prior_28d_cell_net_revenue": 80_000.0,
                "prior_90d_cell_net_revenue": 250_000.0,
                "prior_28d_transaction_count": 12,
                "prior_90d_transaction_count": 40,
                "prior_90d_transaction_days": 24,
                "poi_count": 4,
                "source_snapshot_ids": ["poi-live"],
                "feature_snapshot_time": NOW.isoformat(),
                "view_version": HEATZONE_FEATURE_VERSION,
            }
        ],
        model_runtime=heatzone_runtime,
        require_production_model=True,
    )
    assert heat_result.scores[0].score == 50.0
    assert heat_result.scores[0].model_version == "heatzone:2026.07.24"

    forecast_runtime = _StubRuntime(points=(120_000.0, 130_000.0, 140_000.0, 150_000.0))
    engine = RegisteredEstimatorForecastEngine(forecast_runtime)
    result = engine.fit_predict(ForecastInput.from_mapping(_forecast_input()))
    assert result.bands[4].p50 == 120_000.0
    assert result.bands[24].p50 == 150_000.0
    assert result.model_version == "forecastops:2026.07.24"


def test_heatzone_production_adapter_rejects_legacy_v1_feature_rows() -> None:
    with pytest.raises(ValueError, match="v2 model input is missing"):
        run_heatzone_batch_score(
            features=[
                {
                    "h3_index": "h3-legacy-001",
                    "poi_count": 4,
                    "source_snapshot_ids": ["legacy-snapshot"],
                    "feature_snapshot_time": NOW.isoformat(),
                    "view_version": "geo-grid-view-v1",
                }
            ],
            model_runtime=_StubRuntime(points=(91.0,)),
            require_production_model=True,
        )


class _StubRuntime:
    def __init__(self, *, points: tuple[float, ...]) -> None:
        self.points = points

    def infer(
        self,
        *,
        service: str,
        rows: list[dict[str, Any]],
        expected_feature_schema_version: str,
    ) -> ModelInferenceResult:
        assert len(rows) == len(self.points)
        binding = _binding(service)
        return ModelInferenceResult(
            binding=binding,
            point=self.points,
            lower=tuple(value * 0.9 for value in self.points),
            upper=tuple(value * 1.1 for value in self.points),
            engine="lightgbm.LGBMRegressor",
            artifact_sha256="sha256:" + "a" * 64,
            model_metadata={
                "output_transform": dict(
                    HEATZONE_OUTPUT_TRANSFORM
                    if service == "heatzone"
                    else SITESCORE_OUTPUT_TRANSFORM
                    if service == "sitescore"
                    else {}
                )
            },
        )


def _binding(service: str) -> ModelBinding:
    return ModelBinding.from_model_version(
        service,
        ModelVersion(
            model_name=service,
            version="2026.07.24",
            artifact_uri=f"file:///models/{service}.zip",
            dataset_snapshot_id=f"{service}-training-live",
            feature_schema_version={
                "sitescore": SITESCORE_FEATURE_VERSION,
                "heatzone": HEATZONE_FEATURE_VERSION,
                "forecastops": "store-machine-timeseries-view-v1",
            }[service],
            label_version=f"{service}-label-v1",
            metrics={},
            stage=ModelStage.PRODUCTION,
            aliases=frozenset({ModelAlias.PRODUCTION}),
            run_id=f"{service}-run",
            git_sha="4d5e5e0",
            approved_by="reviewer",
            approved_at=NOW,
        ),
        artifact_sha256="sha256:" + "a" * 64,
        engine="lightgbm.LGBMRegressor",
        mlflow_run_id=f"mlflow-{service}-run",
    )


def _forecast_input() -> dict[str, Any]:
    start = NOW.date() - timedelta(days=28)
    return {
        "tenant_id": "tenant-live-001",
        "store_id": "store-live-001",
        "prediction_origin_time": NOW.isoformat(),
        "observations": [
            {
                "business_date": (start + timedelta(days=index)).isoformat(),
                "actual_revenue": 100_000 + index * 1_000,
                "machine_cycles": 20 + index,
                "data_quality_score": 0.95,
                "source_snapshot_ids": [f"pos-{index:02d}"],
            }
            for index in range(28)
        ],
    }


def _route_endpoint(router: Any, path: str) -> Any:
    return next(route.endpoint for route in router.routes if route.path == path)


def _request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/sitescore/score-jobs",
            "headers": [],
            "client": ("test", 123),
        }
    )
    request.state.correlation_id = "corr-production-runtime"
    return request
