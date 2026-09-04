"""In-memory persistence for PriceOps.

Mirrors the other ODay Plus modules: a dependency-free store that keeps the
module independently testable. The plan aggregate is immutable, so ``save_plan``
replaces the latest snapshot for a ``plan_id`` while ``status_history`` on the
plan carries the full audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from modules.priceops.domain.exploration import (
    ActivationReceipt,
    ExplorationBudgetExceededError,
    ExplorationDecision,
    ExplorationGate,
    ExplorationGateExpiredError,
    ExplorationGateRevokedError,
    PriceScope,
)
from modules.priceops.domain.pricing import (
    ApprovalRecord,
    DecisionWritebackRecord,
    InterventionTreatmentHandoff,
    LabelRegistryEntry,
    ObservationWindow,
    PlanOptimization,
    PlanScenarioSimulation,
    PlanSimulation,
    PricingEffectEvaluation,
    PricingExecution,
    PricingPlan,
    RollbackPlan,
)


@dataclass
class InMemoryPriceOpsRepository:
    _plans: dict[str, PricingPlan] = field(default_factory=dict)
    _simulations: dict[str, PlanSimulation] = field(default_factory=dict)
    _optimizations: dict[str, PlanOptimization] = field(default_factory=dict)
    _approvals: dict[str, ApprovalRecord] = field(default_factory=dict)
    _windows: dict[str, ObservationWindow] = field(default_factory=dict)
    _executions: dict[str, PricingExecution] = field(default_factory=dict)
    _rollback_plans: dict[str, RollbackPlan] = field(default_factory=dict)
    _handoffs: dict[str, InterventionTreatmentHandoff] = field(default_factory=dict)
    _label_entries: dict[str, LabelRegistryEntry] = field(default_factory=dict)
    _evaluations: dict[str, PricingEffectEvaluation] = field(default_factory=dict)
    _decision_writebacks: dict[str, DecisionWritebackRecord] = field(default_factory=dict)
    _scenario_simulations: dict[str, PlanScenarioSimulation] = field(default_factory=dict)
    _gates: dict[str, ExplorationGate] = field(default_factory=dict)
    _exploration_decisions: dict[str, ExplorationDecision] = field(default_factory=dict)
    _activation_receipts: dict[str, ActivationReceipt] = field(default_factory=dict)

    def save_plan(self, plan: PricingPlan) -> PricingPlan:
        self._plans[plan.plan_id] = plan
        return plan

    def get_plan(self, plan_id: str) -> PricingPlan | None:
        return self._plans.get(plan_id)

    def list_plans(self) -> list[PricingPlan]:
        return list(self._plans.values())

    def save_simulation(self, simulation: PlanSimulation) -> PlanSimulation:
        self._simulations[simulation.plan_id] = simulation
        return simulation

    def get_simulation(self, plan_id: str) -> PlanSimulation | None:
        return self._simulations.get(plan_id)

    def save_optimization(self, optimization: PlanOptimization) -> PlanOptimization:
        self._optimizations[optimization.plan_id] = optimization
        return optimization

    def get_optimization(self, plan_id: str) -> PlanOptimization | None:
        return self._optimizations.get(plan_id)

    def save_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        self._approvals[approval.decision_id] = approval
        return approval

    def list_approvals(self, plan_id: str) -> list[ApprovalRecord]:
        return [a for a in self._approvals.values() if a.plan_id == plan_id]

    def save_window(self, window: ObservationWindow) -> ObservationWindow:
        self._windows[window.window_id] = window
        return window

    def get_window(self, plan_id: str) -> ObservationWindow | None:
        for window in self._windows.values():
            if window.plan_id == plan_id:
                return window
        return None

    def save_rollback_plan(self, rollback_plan: RollbackPlan) -> RollbackPlan:
        self._rollback_plans[rollback_plan.plan_id] = rollback_plan
        return rollback_plan

    def get_rollback_plan(self, plan_id: str) -> RollbackPlan | None:
        return self._rollback_plans.get(plan_id)

    def save_execution(self, execution: PricingExecution) -> PricingExecution:
        self._executions[execution.execution_id] = execution
        return execution

    def get_execution(self, plan_id: str) -> PricingExecution | None:
        for execution in self._executions.values():
            if execution.plan_id == plan_id:
                return execution
        return None

    def save_handoff(
        self, handoff: InterventionTreatmentHandoff
    ) -> InterventionTreatmentHandoff:
        self._handoffs[handoff.handoff_id] = handoff
        return handoff

    def list_handoffs(self, plan_id: str) -> list[InterventionTreatmentHandoff]:
        return [h for h in self._handoffs.values() if h.plan_id == plan_id]

    def save_label_entry(self, entry: LabelRegistryEntry) -> LabelRegistryEntry:
        self._label_entries[entry.entry_id] = entry
        return entry

    def list_label_entries(self, plan_id: str) -> list[LabelRegistryEntry]:
        return [e for e in self._label_entries.values() if e.plan_id == plan_id]

    def save_evaluation(
        self, evaluation: PricingEffectEvaluation
    ) -> PricingEffectEvaluation:
        self._evaluations[evaluation.plan_id] = evaluation
        return evaluation

    def get_evaluation(self, plan_id: str) -> PricingEffectEvaluation | None:
        return self._evaluations.get(plan_id)

    def save_decision_writeback(
        self, decision: DecisionWritebackRecord
    ) -> DecisionWritebackRecord:
        self._decision_writebacks[decision.decision_id] = decision
        return decision

    def list_decision_writebacks(self, plan_id: str) -> list[DecisionWritebackRecord]:
        return [d for d in self._decision_writebacks.values() if d.plan_id == plan_id]

    def save_scenario_simulation(
        self, scenario: PlanScenarioSimulation
    ) -> PlanScenarioSimulation:
        self._scenario_simulations[scenario.scenario_id] = scenario
        return scenario

    def get_scenario_simulation(self, scenario_id: str) -> PlanScenarioSimulation | None:
        return self._scenario_simulations.get(scenario_id)

    # -- Gate and Exploration persistence (ODP-FR-PRICE-006) -------------
    def save_gate(self, gate: ExplorationGate) -> ExplorationGate:
        self._gates[gate.gate_id] = gate
        return gate

    def get_gate(self, gate_id: str, tenant_id: str | None = None) -> ExplorationGate | None:
        gate = self._gates.get(gate_id)
        if gate is not None and tenant_id is not None and gate.tenant_id != tenant_id:
            return None
        return gate

    def find_active_gate(
        self, scope: PriceScope, at: datetime | None = None
    ) -> ExplorationGate | None:
        now = at or datetime.now(UTC)
        for gate in self._gates.values():
            if scope.matches(gate) and gate.is_valid_at(now) and gate.remaining_budget > 0:
                return gate
        return None

    def list_gates(self, tenant_id: str | None = None) -> list[ExplorationGate]:
        if tenant_id is not None:
            return [g for g in self._gates.values() if g.tenant_id == tenant_id]
        return list(self._gates.values())

    def revoke_gate(
        self, gate_id: str, tenant_id: str, revoked_at: datetime | None = None
    ) -> ExplorationGate:
        gate = self.get_gate(gate_id, tenant_id=tenant_id)
        if gate is None:
            raise LookupError(f"Gate {gate_id} not found for tenant {tenant_id}")
        if gate.revoked_at is not None:
            raise ExplorationGateRevokedError(f"Gate {gate_id} is already revoked at {gate.revoked_at}")
        now = revoked_at or datetime.now(UTC)
        revoked = ExplorationGate(
            gate_id=gate.gate_id,
            tenant_id=gate.tenant_id,
            budget_limit=gate.budget_limit,
            budget_consumed=gate.budget_consumed,
            effective_from=gate.effective_from,
            effective_to=gate.effective_to,
            approved_by=gate.approved_by,
            approval_decision_id=gate.approval_decision_id,
            approval_id=gate.approval_id,
            rollback_condition=gate.rollback_condition,
            decision_policy_version_id=gate.decision_policy_version_id,
            scope_brand_id=gate.scope_brand_id,
            scope_store_group=gate.scope_store_group,
            scope_sku_group=gate.scope_sku_group,
            revoked_at=now,
            created_at=gate.created_at,
        )
        self._gates[gate_id] = revoked
        return revoked

    def record_exploration_decision(
        self, decision: ExplorationDecision
    ) -> ExplorationDecision:
        gate = self._gates.get(decision.gate_id)
        if gate is None or gate.tenant_id != decision.tenant_id:
            raise LookupError(f"Gate {decision.gate_id} not found for tenant {decision.tenant_id}")
        if gate.revoked_at is not None:
            raise ExplorationGateRevokedError(f"Gate {gate.gate_id} is revoked")
        if not gate.is_valid_at(decision.created_at):
            raise ExplorationGateExpiredError(
                f"Gate {gate.gate_id} is outside active window at {decision.created_at}"
            )
        new_consumed = round(gate.budget_consumed + decision.budget_consumed, 4)
        if new_consumed > gate.budget_limit + 1e-6:
            raise ExplorationBudgetExceededError(
                f"Gate {gate.gate_id} budget exceeded: consumed {new_consumed} > limit {gate.budget_limit}"
            )

        # Update gate budget consumed atomically
        updated_gate = ExplorationGate(
            gate_id=gate.gate_id,
            tenant_id=gate.tenant_id,
            budget_limit=gate.budget_limit,
            budget_consumed=new_consumed,
            effective_from=gate.effective_from,
            effective_to=gate.effective_to,
            approved_by=gate.approved_by,
            approval_decision_id=gate.approval_decision_id,
            approval_id=gate.approval_id,
            rollback_condition=gate.rollback_condition,
            decision_policy_version_id=gate.decision_policy_version_id,
            scope_brand_id=gate.scope_brand_id,
            scope_store_group=gate.scope_store_group,
            scope_sku_group=gate.scope_sku_group,
            revoked_at=gate.revoked_at,
            created_at=gate.created_at,
        )
        self._gates[gate.gate_id] = updated_gate
        self._exploration_decisions[decision.decision_id] = decision
        return decision

    def list_exploration_decisions(
        self, gate_id: str | None = None
    ) -> list[ExplorationDecision]:
        if gate_id is not None:
            return [d for d in self._exploration_decisions.values() if d.gate_id == gate_id]
        return list(self._exploration_decisions.values())

    def save_activation_receipt(
        self, receipt: ActivationReceipt
    ) -> ActivationReceipt:
        self._activation_receipts[receipt.plan_id] = receipt
        return receipt

    def get_activation_receipt(self, plan_id: str) -> ActivationReceipt | None:
        return self._activation_receipts.get(plan_id)


__all__ = ["InMemoryPriceOpsRepository"]
