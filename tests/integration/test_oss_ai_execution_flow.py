from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from models.shared_ml import (
    ModelAlias,
    ModelStage,
    ModelVersion,
    OptunaSearchRunner,
    ParameterSpec,
)
from models.shared_ml.oss_estimators import (
    load_estimator_artifact,
    train_oss_estimator,
)
from modules.avm import (
    LifelinesLiquiditySurvivalAdapter,
    LiquidityTrainingRecord,
)
from modules.forecastops import (
    ForecastInput,
    ForecastOpsService,
    StoreDayObservation,
)
from modules.learninghub.infrastructure import (
    EvidentlyDriftMonitor,
    InMemoryLearningHubRepository,
    MlflowRegistryAdapter,
)
from pipelines.orchestration import DagsterTrainingOrchestrator
from pipelines.quality import GreatExpectationsGate, QualityCheck
from solver.evolutionary import (
    EvolutionaryPortfolioOption,
    solve_portfolio_frontier,
)
from solver.netplan.model import NetworkAction
from solver.netplan.robust import (
    RobustNetPlanConstraints,
    RobustObjective,
    Scenario,
    ScenarioActionOption,
    solve_robust_network_plan,
)
from solver.routeplan import (
    RouteConstraints,
    RouteOption,
    solve_routeplan,
)
from tests.integration._authz import FORECASTOPS_HEADERS


def _model_ready_rows() -> list[dict[str, Any]]:
    return [
        {
            "entity_id": f"store-{index:03d}",
            "demand": float(80 + index * 3),
            "rent": float(30 + (index % 5) * 2),
            "district": "north" if index % 2 == 0 else "south",
            "label": float(110 + index * 4 - (index % 5)),
        }
        for index in range(24)
    ]


def test_governed_training_registry_and_monitoring_flow_uses_real_oss(
    tmp_path: Path,
) -> None:
    rows = _model_ready_rows()
    artifact_path = tmp_path / "models" / "revenue-path-v1.zip"
    artifact_path.parent.mkdir(parents=True)
    repository = InMemoryLearningHubRepository()
    registry = MlflowRegistryAdapter(
        repository,
        tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}",
        experiment_name="oss-ai-e2e",
    )

    def quality_gate(request: dict[str, Any]) -> dict[str, Any]:
        quality = GreatExpectationsGate().validate(
            request["rows"],
            (
                QualityCheck(
                    name="entity-id-present",
                    kind="not_null",
                    column="entity_id",
                ),
                QualityCheck(
                    name="entity-id-unique",
                    kind="unique",
                    column="entity_id",
                ),
                QualityCheck(
                    name="demand-range",
                    kind="between",
                    column="demand",
                    min_value=0,
                    max_value=1_000,
                ),
            ),
            run_id="quality-oss-e2e",
        )
        return {
            "rows": request["rows"],
            "dataset_snapshot_id": request["dataset_snapshot_id"],
            "quality_run_id": quality.run_id,
            "quality_engine": quality.engine,
        }

    def train_model(quality: dict[str, Any]) -> dict[str, Any]:
        training_rows = quality["rows"]
        trained = train_oss_estimator(
            algorithm="lightgbm_regressor",
            feature_rows=training_rows,
            labels=[float(row["label"]) for row in training_rows],
            feature_names=("demand", "rent", "district"),
        )
        artifact_path.write_bytes(trained.estimator.to_artifact_bytes())
        restored = load_estimator_artifact(artifact_path.read_bytes())
        predictions = restored.predict(training_rows)
        mae = sum(
            abs(float(row["label"]) - prediction)
            for row, prediction in zip(training_rows, predictions, strict=True)
        ) / len(training_rows)
        return {
            "artifact_uri": artifact_path.as_uri(),
            "dataset_snapshot_id": quality["dataset_snapshot_id"],
            "algorithm": trained.resolved_algorithm,
            "engine": trained.estimator.spec.engine,
            "mae": mae,
        }

    def register_model(training: dict[str, Any]) -> dict[str, Any]:
        model = ModelVersion(
            model_name="store_revenue_path",
            version="2026.07.24",
            artifact_uri=training["artifact_uri"],
            dataset_snapshot_id=training["dataset_snapshot_id"],
            feature_schema_version="store-revenue-features-v1",
            label_version="w24-revenue-v1",
            metrics={"mae": float(training["mae"])},
            stage=ModelStage.CANARY,
            aliases=frozenset({ModelAlias.CHALLENGER}),
            run_id="oss-e2e-training-run",
            git_sha="e2e",
            monitoring_config={"drift_share_threshold": 0.5},
        )
        registered = registry.register_model_version(model)
        registry.set_alias(
            model_name=registered.model_name,
            alias=ModelAlias.PRODUCTION,
            version=registered.version,
        )
        return {
            "model_id": registered.model_id,
            "artifact_uri": registered.artifact_uri,
            "registry": "mlflow",
        }

    execution = DagsterTrainingOrchestrator().run(
        request={
            "rows": rows,
            "dataset_snapshot_id": "snapshot-oss-e2e",
        },
        quality_gate=quality_gate,
        trainer=train_model,
        registrar=register_model,
    )

    assert execution.success is True
    assert execution.quality_output["quality_engine"] == "great_expectations"
    assert execution.training_output["engine"] == "lightgbm.LGBMRegressor"
    assert execution.registry_output == {
        "model_id": "store_revenue_path:2026.07.24",
        "artifact_uri": artifact_path.as_uri(),
        "registry": "mlflow",
    }
    production = registry.get_by_alias(
        model_name="store_revenue_path",
        alias=ModelAlias.PRODUCTION,
    )
    assert production is not None
    assert production.artifact_uri == artifact_path.as_uri()

    drift = EvidentlyDriftMonitor().run(
        reference_rows=[
            {"demand": row["demand"], "rent": row["rent"]}
            for row in rows
        ],
        current_rows=[
            {"demand": float(row["demand"]) + 500, "rent": row["rent"]}
            for row in rows
        ],
        drift_share_threshold=0.5,
        snapshot_id="drift-oss-e2e",
    )
    assert drift.engine == "evidently"
    assert drift.drift_detected is True
    assert drift.to_dict()["report"]["metrics"]


def _forecast_input() -> ForecastInput:
    start = date(2026, 5, 1)
    observations = tuple(
        StoreDayObservation(
            store_id="store-forecast-e2e",
            business_date=start + timedelta(days=index),
            actual_revenue=100_000.0 + index * 200 + (index % 7) * 1_000,
            source_snapshot_ids=(f"pos-{index:03d}",),
        )
        for index in range(70)
    )
    return ForecastInput(
        store_id="store-forecast-e2e",
        observations=observations,
        prediction_origin_time=datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
    )


def _liquidity_records() -> list[LiquidityTrainingRecord]:
    records: list[LiquidityTrainingRecord] = []
    for index in range(90):
        price_ratio = 0.80 + ((index * 7) % 31) / 50
        demand_score = 0.20 + ((index * 11) % 29) / 35
        duration = max(
            5.0,
            92.0
            + 65.0 * (price_ratio - 1.0)
            - 48.0 * demand_score
            + float((index * 13) % 9),
        )
        records.append(
            LiquidityTrainingRecord(
                duration_days=duration,
                sold=index % 6 != 0,
                features={
                    "asking_price_ratio": price_ratio,
                    "demand_score": demand_score,
                },
            )
        )
    return records


def test_forecast_survival_search_and_optimization_flow_uses_real_oss() -> None:
    forecast = ForecastOpsService(
        engine="statsforecast",
        model_name="seasonal_naive",
    ).forecast([_forecast_input()]).forecasts[0]
    assert forecast.engine_name == "statsforecast"
    assert forecast.model_metadata["library"] == "statsforecast"
    assert forecast.p10 <= forecast.p50 <= forecast.p90

    survival = LifelinesLiquiditySurvivalAdapter(penalizer=0.2).fit(
        _liquidity_records()
    )
    liquidity = survival.predict(
        {"asking_price_ratio": 1.0, "demand_score": 0.75}
    )
    restored_survival = LifelinesLiquiditySurvivalAdapter.from_artifact(
        survival.serialize_artifact()
    )
    assert restored_survival.predict(
        {"asking_price_ratio": 1.0, "demand_score": 0.75}
    ) == liquidity
    assert 0 <= liquidity.sale_probability_30d <= liquidity.sale_probability_90d <= 1

    search = OptunaSearchRunner().run(
        objective=lambda params: (float(params["penalty"]) - 0.35) ** 2,
        search_space=(
            ParameterSpec(
                name="penalty",
                kind="float",
                low=0.0,
                high=1.0,
            ),
        ),
        n_trials=8,
        study_name="oss-ai-e2e-search",
        seed=24,
    )
    assert search.engine == "optuna"
    assert len(search.trials) == 8

    frontier = solve_portfolio_frontier(
        options=(
            EvolutionaryPortfolioOption("site-a", 550, 250, 0.15),
            EvolutionaryPortfolioOption("site-b", 420, 180, 0.08),
            EvolutionaryPortfolioOption("site-c", 700, 400, 0.30),
            EvolutionaryPortfolioOption("site-d", 300, 120, 0.04),
        ),
        max_budget=600,
        max_selected=3,
        population_size=30,
        generations=20,
        seed=24,
    )
    assert frontier.engine == "pymoo-nsga2"
    assert frontier.status == "optimal_frontier"
    assert frontier.candidates

    route = solve_routeplan(
        options=(
            RouteOption("a-q1", "site-a", "2027Q1", "NORTH", 500, 180, 1, 1),
            RouteOption("b-q2", "site-b", "2027Q2", "SOUTH", 450, 170, 1, 1),
        ),
        constraints=RouteConstraints(
            quarters=("2027Q1", "2027Q2"),
            capital_budget_by_quarter={"2027Q1": 200, "2027Q2": 200},
            labor_capacity_by_quarter={"2027Q1": 1, "2027Q2": 1},
            construction_capacity_by_quarter={"2027Q1": 1, "2027Q2": 1},
            min_total_openings=2,
            max_total_openings=2,
        ),
    )
    assert route.solver_name == "OR_TOOLS_CP_SAT"
    assert route.solver_status == "OPTIMAL"

    scenarios = (
        Scenario("DOWNSIDE", 0.2),
        Scenario("BASE", 0.5),
        Scenario("UPSIDE", 0.3),
    )
    robust = solve_robust_network_plan(
        options_by_entity={
            "store-a": (
                ScenarioActionOption(
                    "safe",
                    "store-a",
                    NetworkAction.KEEP,
                    {"DOWNSIDE": 80, "BASE": 80, "UPSIDE": 80},
                    0,
                    0.05,
                ),
                ScenarioActionOption(
                    "risky",
                    "store-a",
                    NetworkAction.IMPROVE,
                    {"DOWNSIDE": 20, "BASE": 140, "UPSIDE": 220},
                    50,
                    0.2,
                    1,
                ),
            )
        },
        scenarios=scenarios,
        constraints=RobustNetPlanConstraints(max_budget=50),
        objective=RobustObjective.MAX_MIN,
    )
    assert robust.solver_status in {"OPTIMAL", "FEASIBLE"}
    assert robust.solver_name
    assert [option.option_id for option in robust.selected_actions] == ["safe"]

    assert search.best_value >= 0
    assert survival.training_metadata["library"] == "lifelines"
    assert liquidity.expected_days > 0


def test_forecast_api_uses_deployment_selected_statsforecast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_FORECAST_ENGINE", "statsforecast")
    monkeypatch.setenv("ODP_FORECAST_MODEL", "seasonal_naive")
    start = date(2026, 5, 1)
    payload = {
        "prediction_origin_time": "2026-07-20T09:00:00+00:00",
        "inputs": [
            {
                "store_id": "store-api-oss-e2e",
                "observations": [
                    {
                        "business_date": (start + timedelta(days=index)).isoformat(),
                        "actual_revenue": 100_000 + index * 200 + (index % 7) * 1_000,
                        "source_snapshot_ids": [f"pos-{index:03d}"],
                    }
                    for index in range(70)
                ],
            }
        ],
    }

    response = TestClient(
        create_app(),
        headers=FORECASTOPS_HEADERS,
    ).post(
        "/api/v1/forecastops/forecast-jobs",
        json=payload,
        headers={"Idempotency-Key": "oss-statsforecast-e2e"},
    )

    assert response.status_code == 202
    forecast = response.json()["forecasts"][0]
    assert forecast["engine_name"] == "statsforecast"
    assert forecast["model_name"] == "seasonal_naive"
    assert forecast["model_metadata"]["library"] == "statsforecast"


def test_oss_execution_flow_rejects_invalid_quality_before_training(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "must-not-exist.zip"

    def reject(_: dict[str, Any]) -> dict[str, Any]:
        GreatExpectationsGate().validate(
            [{"entity_id": "duplicate"}, {"entity_id": "duplicate"}],
            (
                QualityCheck(
                    name="entity-id-unique",
                    kind="unique",
                    column="entity_id",
                ),
            ),
        )
        return {}

    result = DagsterTrainingOrchestrator().run(
        request={"dataset_snapshot_id": "bad-snapshot"},
        quality_gate=reject,
        trainer=lambda _: artifact_path.write_bytes(b"invalid") or {},
        registrar=lambda _: pytest.fail("registry must not run after quality failure"),
    )

    assert result.success is False
    assert result.failed_stage == "quality_gate"
    assert not artifact_path.exists()
