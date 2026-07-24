from __future__ import annotations

from datetime import UTC, datetime
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
from modules.forecastops.application import RegisteredEstimatorForecastEngine
from modules.forecastops.domain import ForecastInput
from modules.heatzone.workers import run_heatzone_batch_score
from modules.learninghub.infrastructure import (
    InMemoryLearningHubRepository,
    MlflowRegistryAdapter,
)
from modules.sitescore.application.reporting import SiteScoreReportService
from modules.sitescore.domain import SITESCORE_FEATURE_VERSION, score_sites

NOW = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


def _training_rows() -> list[dict[str, Any]]:
    return [
        {
            "heat_zone_score": float(40 + index * 2),
            "monthly_rent": float(35_000 + index * 1_100),
            "area_ping": float(18 + index % 6),
        }
        for index in range(20)
    ]


def _live_sitescore_row() -> dict[str, Any]:
    return {
        "candidate_site_id": "candidate-live-001",
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
        + row["heat_zone_score"] * 3_800.0
        - row["monthly_rent"] * 0.7
        + row["area_ping"] * 2_500.0
        for row in rows
    ]
    trained = train_oss_estimator(
        algorithm="lightgbm_regressor",
        feature_rows=rows,
        labels=labels,
        feature_names=("heat_zone_score", "monthly_rent", "area_ping"),
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
            model_name="sitescore",
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
        )
    )
    return MlflowProductionModelRuntime(tracking_uri=tracking_uri), artifact_path


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
    assert report.m12.p50 == round(max(0.0, inference.point[0]), 2)
    assert report.model_version == "sitescore:2026.07.24"
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
    assert payload["reports"][0]["model_version"] == "sitescore:2026.07.24"


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
        ("monthly_rent", None),
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
                "poi_count": 4,
                "source_snapshot_ids": ["poi-live"],
                "feature_snapshot_time": NOW.isoformat(),
                "view_version": "geo-grid-view-v1",
            }
        ],
        model_runtime=heatzone_runtime,
        require_production_model=True,
    )
    assert heat_result.scores[0].score == 91.0
    assert heat_result.scores[0].model_version == "heatzone:2026.07.24"

    forecast_runtime = _StubRuntime(
        points=(120_000.0, 130_000.0, 140_000.0, 150_000.0)
    )
    engine = RegisteredEstimatorForecastEngine(forecast_runtime)
    result = engine.fit_predict(ForecastInput.from_mapping(_forecast_input()))
    assert result.bands[4].p50 == 120_000.0
    assert result.bands[24].p50 == 150_000.0
    assert result.model_version == "forecastops:2026.07.24"


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
                "heatzone": "geo-grid-view-v1",
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
    return {
        "store_id": "store-live-001",
        "prediction_origin_time": NOW.isoformat(),
        "observations": [
            {
                "business_date": f"2026-07-{day:02d}",
                "actual_revenue": 100_000 + day * 1_000,
                "machine_cycles": 20 + day,
                "data_quality_score": 0.95,
                "source_snapshot_ids": [f"pos-202607{day:02d}"],
            }
            for day in range(1, 15)
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
