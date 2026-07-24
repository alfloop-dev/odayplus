"""Batch optimizer worker (`pricing-optimizer`, ODP-MOD-06 §10).

Runs simulate + constrained-optimize for a set of plan requests and reports an
aggregate result. The batch fails fast if any plan would breach a hard
constraint so AC-06-01 (zero hard-constraint violations) is enforced at the job
boundary, not just per item.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.priceops.application.pricing import PriceOpsService
from modules.priceops.domain.pricing import PricingPlanItem
from modules.priceops.infrastructure.oss_optimizer import PriceOpsProductionOptimizer
from modules.priceops.infrastructure.repositories import InMemoryPriceOpsRepository


@dataclass(frozen=True)
class PlanRequest:
    """One plan to build, simulate and optimize within a batch run."""

    tenant_id: str
    correlation_id: str
    items: Sequence[PricingPlanItem]
    plan_id: str | None = None


@dataclass(frozen=True)
class PriceOpsBatchResult:
    job_id: str
    status: str
    result: dict[str, Any]
    completed_at: datetime

    @property
    def hard_constraint_violation_count(self) -> int:
        return int(self.result.get("hard_constraint_violation_count", 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            **self.result,
            "completed_at": self.completed_at.isoformat(),
        }


class PriceOpsOptimizerWorker:
    def __init__(
        self,
        *,
        repository: InMemoryPriceOpsRepository | None = None,
        production_optimizer: PriceOpsProductionOptimizer | None = None,
        runtime_mode: str | None = None,
    ) -> None:
        self.service = PriceOpsService(
            repository=repository,
            production_optimizer=production_optimizer,
            runtime_mode=runtime_mode,
        )

    def run(
        self,
        *,
        requests: Iterable[PlanRequest],
        job_id: str | None = None,
        optimized_at: datetime | None = None,
    ) -> PriceOpsBatchResult:
        completed_at = datetime.now(UTC)
        moment = optimized_at or completed_at
        plan_results: list[dict[str, Any]] = []
        total_violations = 0
        total_incremental = 0.0
        for request in requests:
            plan = self.service.create_plan(
                tenant_id=request.tenant_id,
                items=request.items,
                correlation_id=request.correlation_id,
                plan_id=request.plan_id,
            )
            self.service.simulate(plan.plan_id, generated_at=moment)
            optimization = self.service.optimize(plan.plan_id, optimized_at=moment)
            total_violations += optimization.hard_constraint_violation_count
            total_incremental += optimization.total_incremental_gross_margin
            plan_results.append(
                {
                    "plan_id": plan.plan_id,
                    "optimization": optimization.to_dict(),
                }
            )

        status = "succeeded" if total_violations == 0 else "failed"
        result = {
            "plan_count": len(plan_results),
            "hard_constraint_violation_count": total_violations,
            "total_incremental_gross_margin": round(total_incremental, 4),
            "plans": plan_results,
        }
        return PriceOpsBatchResult(
            job_id=job_id or f"priceops-optimizer-{uuid4()}",
            status=status,
            result=result,
            completed_at=completed_at,
        )


def run_priceops_optimizer_batch(
    *,
    requests: Iterable[PlanRequest],
    job_id: str | None = None,
    optimized_at: datetime | None = None,
    repository: InMemoryPriceOpsRepository | None = None,
    production_optimizer: PriceOpsProductionOptimizer | None = None,
    runtime_mode: str | None = None,
) -> PriceOpsBatchResult:
    return PriceOpsOptimizerWorker(
        repository=repository,
        production_optimizer=production_optimizer,
        runtime_mode=runtime_mode,
    ).run(
        requests=requests,
        job_id=job_id,
        optimized_at=optimized_at,
    )


__all__ = [
    "PlanRequest",
    "PriceOpsBatchResult",
    "PriceOpsOptimizerWorker",
    "run_priceops_optimizer_batch",
]
