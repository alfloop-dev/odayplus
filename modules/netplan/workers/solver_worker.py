"""Batch worker for NetPlan solver runs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.netplan.application.planning import NetPlanService, ScenarioBuildRequest
from modules.netplan.infrastructure.repositories import InMemoryNetPlanRepository


@dataclass(frozen=True)
class NetPlanBatchResult:
    job_id: str
    status: str
    scenarios: tuple[dict[str, Any], ...]
    completed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "scenarios": list(self.scenarios),
            "completed_at": self.completed_at.isoformat(),
        }


class NetPlanSolverWorker:
    def __init__(self, *, repository: InMemoryNetPlanRepository | None = None) -> None:
        self.service = NetPlanService(repository=repository)

    def run(
        self,
        *,
        requests: Iterable[ScenarioBuildRequest],
        job_id: str | None = None,
        solved_at: datetime | None = None,
    ) -> NetPlanBatchResult:
        moment = solved_at or datetime.now(UTC)
        scenario_payloads: list[dict[str, Any]] = []
        status = "succeeded"
        for request in requests:
            scenario = self.service.create_scenario(
                tenant_id=request.tenant_id,
                scenario_name=request.scenario_name,
                planning_horizon=request.planning_horizon,
                constraints=request.constraints,
                existing_stores=request.existing_stores,
                candidate_sites=request.candidate_sites,
                scenario_id=request.scenario_id,
                correlation_id=request.correlation_id,
            )
            solve = self.service.solve(scenario.scenario_id, solved_at=moment)
            if solve.result.infeasible:
                status = "completed_with_infeasible"
            scenario_payloads.append(
                {
                    "scenario": self.service.repository.get_scenario(scenario.scenario_id).to_dict(),
                    "solve": solve.to_dict(),
                }
            )

        return NetPlanBatchResult(
            job_id=job_id or f"netplan-solver-{uuid4()}",
            status=status,
            scenarios=tuple(scenario_payloads),
            completed_at=moment,
        )


def run_netplan_solver_batch(
    *,
    requests: Iterable[ScenarioBuildRequest],
    job_id: str | None = None,
    solved_at: datetime | None = None,
    repository: InMemoryNetPlanRepository | None = None,
) -> NetPlanBatchResult:
    return NetPlanSolverWorker(repository=repository).run(
        requests=requests,
        job_id=job_id,
        solved_at=solved_at,
    )


__all__ = [
    "NetPlanBatchResult",
    "NetPlanSolverWorker",
    "run_netplan_solver_batch",
]
