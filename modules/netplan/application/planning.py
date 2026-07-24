"""NetPlan application service."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from models.shared_ml.production_runtime import production_model_execution_required
from modules.netplan.application.production import NetPlanProductionExecutor
from modules.netplan.domain.planning import (
    ApprovalRecord,
    CandidateSiteInput,
    ExecutionRecord,
    ExistingStoreInput,
    NetPlanScenario,
    NetPlanScenarioStatus,
    OutcomeRecord,
    ScenarioSolveRecord,
    build_outcome_record,
    build_scenario_options,
)
from modules.netplan.infrastructure.repositories import InMemoryNetPlanRepository
from solver.netplan import STATUS_INFEASIBLE, NetPlanConstraints, solve_network_plan


class NetPlanNotFoundError(LookupError):
    """Raised when a scenario or solve record is missing."""


class NetPlanApprovalError(ValueError):
    """Raised when a high-risk approval request is incomplete."""


@dataclass(frozen=True)
class ScenarioBuildRequest:
    tenant_id: str
    scenario_name: str
    planning_horizon: str
    constraints: NetPlanConstraints
    existing_stores: Sequence[ExistingStoreInput | Mapping[str, Any]] = ()
    candidate_sites: Sequence[CandidateSiteInput | Mapping[str, Any]] = ()
    scenario_id: str | None = None
    correlation_id: str = "netplan-correlation"


class NetPlanService:
    def __init__(
        self,
        *,
        repository: InMemoryNetPlanRepository | None = None,
        production_executor: NetPlanProductionExecutor | None = None,
    ) -> None:
        self.repository = repository or InMemoryNetPlanRepository()
        self.production_executor = production_executor

    def create_scenario(
        self,
        *,
        tenant_id: str,
        scenario_name: str,
        planning_horizon: str,
        constraints: NetPlanConstraints | Mapping[str, Any],
        existing_stores: Sequence[ExistingStoreInput | Mapping[str, Any]] = (),
        candidate_sites: Sequence[CandidateSiteInput | Mapping[str, Any]] = (),
        scenario_id: str | None = None,
        correlation_id: str,
        created_at: datetime | None = None,
    ) -> NetPlanScenario:
        parsed_constraints = (
            constraints
            if isinstance(constraints, NetPlanConstraints)
            else NetPlanConstraints.from_mapping(constraints)
        )
        scenario = NetPlanScenario.create(
            tenant_id=tenant_id,
            scenario_name=scenario_name,
            planning_horizon=planning_horizon,
            options_by_entity=build_scenario_options(
                existing_stores=existing_stores,
                candidate_sites=candidate_sites,
            ),
            constraints=parsed_constraints,
            correlation_id=correlation_id,
            scenario_id=scenario_id,
            created_at=created_at,
        )
        return self.repository.save_scenario(scenario)

    def solve(
        self,
        scenario_id: str,
        *,
        actor: str = "system",
        reason: str = "netplan constrained network solve",
        solved_at: datetime | None = None,
        alternative_limit: int = 3,
    ) -> ScenarioSolveRecord:
        scenario = self._require_scenario(scenario_id)
        now = solved_at or datetime.now(UTC)
        execution_metadata: dict[str, Any] = {}
        if production_model_execution_required():
            executor = self.production_executor or NetPlanProductionExecutor()
            execution = executor.execute(
                scenario,
                alternative_limit=alternative_limit,
            )
            result = execution.result
            execution_metadata = execution.metadata
        else:
            result = solve_network_plan(
                options_by_entity=scenario.options_by_entity,
                constraints=scenario.constraints,
                alternative_limit=alternative_limit,
            )
        solve = self.repository.save_solve(
            ScenarioSolveRecord(
                scenario_id=scenario.scenario_id,
                result=result,
                solved_at=now,
                execution_metadata=execution_metadata,
            )
        )
        target = (
            NetPlanScenarioStatus.INFEASIBLE
            if result.solver_status == STATUS_INFEASIBLE
            else NetPlanScenarioStatus.SOLVED
        )
        self._advance(scenario, target, actor=actor, reason=reason, occurred_at=now)
        return solve

    def submit_for_approval(
        self,
        scenario_id: str,
        *,
        actor: str = "system",
        reason: str = "submitted for network planning approval",
        occurred_at: datetime | None = None,
    ) -> NetPlanScenario:
        scenario = self._require_scenario(scenario_id)
        return self._advance(
            scenario,
            NetPlanScenarioStatus.PENDING_APPROVAL,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )

    def decide(
        self,
        scenario_id: str,
        *,
        actor_id: str,
        reason: str,
        decision: str = "approved",
        decided_at: datetime | None = None,
    ) -> ApprovalRecord:
        if not reason:
            raise NetPlanApprovalError("netplan decisions require a reason")
        scenario = self._require_scenario(scenario_id)
        now = decided_at or datetime.now(UTC)
        normalized = decision.lower()
        approval = self.repository.save_approval(
            ApprovalRecord(
                approval_id=f"netplan-approval-{uuid4()}",
                scenario_id=scenario.scenario_id,
                actor_id=actor_id,
                decision=normalized,
                reason=reason,
                decided_at=now,
                policy_version=scenario.constraints.policy_version,
            )
        )
        target = (
            NetPlanScenarioStatus.APPROVED
            if approval.is_approved
            else NetPlanScenarioStatus.REJECTED
        )
        self._advance(scenario, target, actor=actor_id, reason=reason, occurred_at=now)
        return approval

    def execute(
        self,
        scenario_id: str,
        *,
        executed_by: str = "system",
        executed_at: datetime | None = None,
    ) -> ExecutionRecord:
        scenario = self._require_scenario(scenario_id)
        solve = self._require_solve(scenario_id)
        now = executed_at or datetime.now(UTC)
        execution = self.repository.save_execution(
            ExecutionRecord(
                execution_id=f"netplan-execution-{uuid4()}",
                scenario_id=scenario_id,
                actions=solve.result.selected_actions,
                executed_by=executed_by,
                executed_at=now,
            )
        )
        self._advance(
            scenario,
            NetPlanScenarioStatus.EXECUTED,
            actor=executed_by,
            reason="network plan actions executed",
            occurred_at=now,
        )
        return execution

    def record_outcome(
        self,
        scenario_id: str,
        *,
        actual_gross_margin: float,
        observed_at: datetime | None = None,
        source_snapshot_ids: Sequence[str] = (),
        actor: str = "system",
    ) -> OutcomeRecord:
        scenario = self._require_scenario(scenario_id)
        solve = self._require_solve(scenario_id)
        now = observed_at or datetime.now(UTC)
        outcome = self.repository.save_outcome(
            build_outcome_record(
                scenario_id=scenario_id,
                solve_result=solve.result,
                actual_gross_margin=actual_gross_margin,
                observed_at=now,
                source_snapshot_ids=source_snapshot_ids,
            )
        )
        self._advance(
            scenario,
            NetPlanScenarioStatus.OUTCOME_OBSERVED,
            actor=actor,
            reason="network plan outcome observed",
            occurred_at=now,
        )
        return outcome

    def close(
        self,
        scenario_id: str,
        *,
        actor: str = "system",
        reason: str = "netplan outcome written to label registry",
        occurred_at: datetime | None = None,
    ) -> NetPlanScenario:
        scenario = self._require_scenario(scenario_id)
        return self._advance(
            scenario,
            NetPlanScenarioStatus.CLOSED,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )

    def _advance(
        self,
        scenario: NetPlanScenario,
        to_status: NetPlanScenarioStatus,
        *,
        actor: str,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> NetPlanScenario:
        updated = scenario.transition(
            to_status,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )
        return self.repository.save_scenario(updated)

    def _require_scenario(self, scenario_id: str) -> NetPlanScenario:
        scenario = self.repository.get_scenario(scenario_id)
        if scenario is None:
            raise NetPlanNotFoundError(f"scenario {scenario_id} not found")
        return scenario

    def _require_solve(self, scenario_id: str) -> ScenarioSolveRecord:
        solve = self.repository.get_solve(scenario_id)
        if solve is None:
            raise NetPlanNotFoundError(f"scenario {scenario_id} has no solve record")
        return solve


__all__ = [
    "NetPlanApprovalError",
    "NetPlanNotFoundError",
    "NetPlanService",
    "ScenarioBuildRequest",
]
