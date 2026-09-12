"""In-memory persistence for NetPlan scenarios and solve artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field

from modules.netplan.domain.planning import (
    ApprovalRecord,
    ConstraintDisclosureAcknowledgement,
    ExecutionRecord,
    NetPlanScenario,
    OutcomeRecord,
    ScenarioSolveRecord,
)


class ImmutableRecordError(RuntimeError):
    """Raised on an attempt to rewrite a record the store holds as final."""


@dataclass
class InMemoryNetPlanRepository:
    _scenarios: dict[str, NetPlanScenario] = field(default_factory=dict)
    _solves: dict[str, ScenarioSolveRecord] = field(default_factory=dict)
    _approvals: dict[str, ApprovalRecord] = field(default_factory=dict)
    _disclosure_acknowledgements: dict[str, ConstraintDisclosureAcknowledgement] = field(
        default_factory=dict
    )
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

    def save_disclosure_acknowledgement(
        self, acknowledgement: ConstraintDisclosureAcknowledgement
    ) -> ConstraintDisclosureAcknowledgement:
        """Store a signature once, and refuse to store over it.

        This mirrors `trg_netplan_disclosure_ack_immutable` in the database
        rather than relying on it: an in-memory run is the composition most
        tests and the library entry point exercise, and a rule that only holds
        in production is a rule the tests cannot show holding.

        The integrity check is here rather than only at read time so that a
        receipt whose hash never matched cannot be stored at all. A stored
        record that can never be used is a trap for whoever reads the table
        next.
        """
        if not acknowledgement.integrity_verified:
            raise ImmutableRecordError(
                f"acknowledgement {acknowledgement.acknowledgement_id} does not match "
                "its own content hash; refusing to store an unverifiable receipt"
            )
        existing = self._disclosure_acknowledgements.get(
            acknowledgement.acknowledgement_id
        )
        if existing is not None:
            raise ImmutableRecordError(
                f"acknowledgement {acknowledgement.acknowledgement_id} already exists "
                "and is immutable; issue a new acknowledgement instead of rewriting it"
            )
        self._disclosure_acknowledgements[acknowledgement.acknowledgement_id] = (
            acknowledgement
        )
        return acknowledgement

    def get_disclosure_acknowledgement(
        self, acknowledgement_id: str
    ) -> ConstraintDisclosureAcknowledgement | None:
        return self._disclosure_acknowledgements.get(acknowledgement_id)

    def list_disclosure_acknowledgements(
        self, scenario_id: str
    ) -> list[ConstraintDisclosureAcknowledgement]:
        return [
            acknowledgement
            for acknowledgement in self._disclosure_acknowledgements.values()
            if acknowledgement.scenario_id == scenario_id
        ]

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


__all__ = ["ImmutableRecordError", "InMemoryNetPlanRepository"]
