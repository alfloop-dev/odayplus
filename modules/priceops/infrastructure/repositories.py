"""In-memory persistence for PriceOps.

Mirrors the other ODay Plus modules: a dependency-free store that keeps the
module independently testable. The plan aggregate is immutable, so ``save_plan``
replaces the latest snapshot for a ``plan_id`` while ``status_history`` on the
plan carries the full audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from modules.priceops.domain.pricing import (
    ApprovalRecord,
    InterventionTreatmentHandoff,
    LabelRegistryEntry,
    ObservationWindow,
    PlanOptimization,
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


__all__ = ["InMemoryPriceOpsRepository"]
