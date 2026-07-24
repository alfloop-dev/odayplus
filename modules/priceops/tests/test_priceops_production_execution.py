"""Production PriceOps OSS execution contract tests."""

from __future__ import annotations

from datetime import UTC, datetime

import cvxpy
import optuna
import pytest

import modules.priceops.application.pricing as pricing
from modules.priceops import (
    PRICEOPS_MODEL_VERSION,
    InMemoryPriceOpsRepository,
    PlanStatus,
    PriceConstraints,
    PriceElasticityEstimate,
    PriceOpsProductionExecutionError,
    PriceOpsService,
    PricingPlanItem,
)


def _item(*, snapshots: tuple[str, ...] = ("price-history-live",)) -> PricingPlanItem:
    return PricingPlanItem.create(
        store_id="store-live",
        machine_type="washer",
        constraints=PriceConstraints(
            unit_cost=3.0,
            current_price=5.0,
            margin_floor_ratio=0.2,
            max_increase_pct=0.2,
            max_decrease_pct=0.2,
            price_ladder_step=0.5,
        ),
        baseline_demand=1_000.0,
        elasticity=PriceElasticityEstimate(
            elasticity_value=-1.2,
            confidence=0.9,
            prediction_origin_time=datetime(2026, 7, 24, tzinfo=UTC),
        ),
        source_snapshot_ids=snapshots,
    )


def test_production_priceops_executes_optuna_and_cvxpy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setattr(
        pricing,
        "optimize_item",
        lambda _item: pytest.fail("legacy PriceOps optimizer fallback was called"),
    )
    calls = {"optuna": 0, "cvxpy": 0}
    original_create_study = optuna.create_study
    original_solve = cvxpy.Problem.solve

    def create_study_spy(*args, **kwargs):
        calls["optuna"] += 1
        return original_create_study(*args, **kwargs)

    def solve_spy(self, *args, **kwargs):
        calls["cvxpy"] += 1
        return original_solve(self, *args, **kwargs)

    monkeypatch.setattr(optuna, "create_study", create_study_spy)
    monkeypatch.setattr(cvxpy.Problem, "solve", solve_spy)
    service = PriceOpsService()
    plan = service.create_plan(
        tenant_id="tenant-live",
        items=[_item()],
        correlation_id="corr-price-live",
    )
    service.simulate(plan.plan_id)

    result = service.optimize(plan.plan_id)

    assert calls == {"optuna": 1, "cvxpy": 1}
    assert result.solver_version == "priceops-optuna-cvxpy-v1"
    assert result.items[0].result.solver_version == result.solver_version
    assert result.solver_metadata["engines"]["search"]["library"] == "optuna"
    assert result.solver_metadata["engines"]["portfolio"]["library"] == "cvxpy"
    assert result.solver_metadata["model_versions"] == [PRICEOPS_MODEL_VERSION]
    assert result.solver_metadata["feature_versions"]
    assert result.solver_metadata["source_snapshot_ids"] == ["price-history-live"]


def test_production_priceops_missing_lineage_has_no_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    repository = InMemoryPriceOpsRepository()
    service = PriceOpsService(repository=repository)
    plan = service.create_plan(
        tenant_id="tenant-live",
        items=[_item(snapshots=())],
        correlation_id="corr-price-live",
    )

    with pytest.raises(PriceOpsProductionExecutionError):
        service.optimize(plan.plan_id)

    assert repository.get_optimization(plan.plan_id) is None
    assert repository.get_plan(plan.plan_id).status is PlanStatus.CANDIDATE
