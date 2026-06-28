"""In-memory persistence for NetPlan scenarios and solve artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field

from modules.netplan.domain.planning import (
    ApprovalRecord,
    ExecutionRecord,
    NetPlanScenario,
    OutcomeRecord,
    ScenarioSolveRecord,
)


@dataclass
class InMemoryNetPlanRepository:
    _scenarios: dict[str, NetPlanScenario] = field(default_factory=dict)
    _solves: dict[str, ScenarioSolveRecord] = field(default_factory=dict)
    _approvals: dict[str, ApprovalRecord] = field(default_factory=dict)
    _executions: dict[str, ExecutionRecord] = field(default_factory=dict)
    _outcomes: dict[str, OutcomeRecord] = field(default_factory=dict)

    def save_scenario(self, scenario: NetPlanScenario) -> NetPlanScenario:
        self._scenarios[scenario.scenario_id] = scenario
        return scenario

    def get_scenario(self, scenario_id: str) -> NetPlanScenario | None:
        return self._scenarios.get(scenario_id)

    def list_scenarios(self) -> list[NetPlanScenario]:
        return list(self._scenarios.values())

    def save_solve(self, solve: ScenarioSolveRecord) -> ScenarioSolveRecord:
        self._solves[solve.scenario_id] = solve
        return solve

    def get_solve(self, scenario_id: str) -> ScenarioSolveRecord | None:
        return self._solves.get(scenario_id)

    def save_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        self._approvals[approval.approval_id] = approval
        return approval

    def list_approvals(self, scenario_id: str) -> list[ApprovalRecord]:
        return [approval for approval in self._approvals.values() if approval.scenario_id == scenario_id]

    def save_execution(self, execution: ExecutionRecord) -> ExecutionRecord:
        self._executions[execution.scenario_id] = execution
        return execution

    def get_execution(self, scenario_id: str) -> ExecutionRecord | None:
        return self._executions.get(scenario_id)

    def save_outcome(self, outcome: OutcomeRecord) -> OutcomeRecord:
        self._outcomes[outcome.scenario_id] = outcome
        return outcome

    def get_outcome(self, scenario_id: str) -> OutcomeRecord | None:
        return self._outcomes.get(scenario_id)


__all__ = ["InMemoryNetPlanRepository"]
