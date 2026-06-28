"""PriceOps application service.

Orchestrates the full plan lifecycle (ODP-MOD-06 §7): create → simulate →
optimize → approve → activate → observe → evaluate → continue/adjust/stop/
rollback. Every state-changing method drives a single ``PricingPlan`` transition
so the audit trail (``status_history``) is complete, and writes the matching
record (simulation, optimization, approval, execution, evaluation) to the
repository.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from modules.priceops.domain.pricing import (
    DEFAULT_NEGATIVE_IMPACT_THRESHOLD,
    PRICEOPS_SOLVER_VERSION,
    ApprovalRecord,
    InterventionTreatmentHandoff,
    ItemOptimization,
    ItemSimulation,
    LabelRegistryEntry,
    ObservationWindow,
    PlanOptimization,
    PlanSimulation,
    PlanStatus,
    PriceTreatment,
    PricingEffectEvaluation,
    PricingExecution,
    PricingPlan,
    PricingPlanItem,
    RollbackPlan,
    build_observation_window,
    build_rollback_plan,
    count_hard_violations,
    evaluate_effect,
    optimize_item,
    simulate_item,
)
from modules.priceops.infrastructure.repositories import InMemoryPriceOpsRepository
from solver.pricing.optimizer import STATUS_INFEASIBLE, STATUS_OPTIMAL

# Label maturity horizon when the caller does not supply one explicitly.
DEFAULT_LABEL_MATURITY_DAYS = 28


class PlanNotFoundError(LookupError):
    """Raised when a plan_id does not resolve to a stored plan."""


class MissingRollbackPlanError(RuntimeError):
    """Raised when a plan would execute without a pre-existing rollback plan."""


@dataclass(frozen=True)
class ActivationResult:
    plan: PricingPlan
    execution: PricingExecution
    label_entry: LabelRegistryEntry
    handoff: InterventionTreatmentHandoff
    rollback_plan: RollbackPlan

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "execution": self.execution.to_dict(),
            "label_entry": self.label_entry.to_dict(),
            "handoff": self.handoff.to_dict(),
            "rollback_plan": self.rollback_plan.to_dict(),
        }


@dataclass(frozen=True)
class EvaluationResult:
    plan: PricingPlan
    evaluation: PricingEffectEvaluation

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "evaluation": self.evaluation.to_dict(),
        }


class PriceOpsService:
    def __init__(self, *, repository: InMemoryPriceOpsRepository | None = None) -> None:
        self.repository = repository or InMemoryPriceOpsRepository()

    # -- creation ---------------------------------------------------------
    def create_plan(
        self,
        *,
        tenant_id: str,
        items: Iterable[PricingPlanItem],
        correlation_id: str,
        plan_id: str | None = None,
        created_at: datetime | None = None,
    ) -> PricingPlan:
        plan = PricingPlan.create(
            tenant_id=tenant_id,
            items=tuple(items),
            correlation_id=correlation_id,
            plan_id=plan_id,
            created_at=created_at,
        )
        return self.repository.save_plan(plan)

    # -- simulation -------------------------------------------------------
    def simulate(
        self,
        plan_id: str,
        *,
        actor: str = "system",
        reason: str = "demand and margin simulation",
        generated_at: datetime | None = None,
    ) -> PlanSimulation:
        plan = self._require_plan(plan_id)
        now = generated_at or datetime.now(UTC)
        items = tuple(
            ItemSimulation(
                item_id=item.item_id,
                store_id=item.store_id,
                simulation=simulate_item(item),
            )
            for item in plan.items
        )
        simulation = PlanSimulation(plan_id=plan.plan_id, items=items, generated_at=now)
        self.repository.save_simulation(simulation)
        self._advance(plan, PlanStatus.SIMULATED, actor=actor, reason=reason, occurred_at=now)
        return simulation

    # -- optimization -----------------------------------------------------
    def optimize(
        self,
        plan_id: str,
        *,
        actor: str = "system",
        reason: str = "constrained price optimization",
        optimized_at: datetime | None = None,
    ) -> PlanOptimization:
        plan = self._require_plan(plan_id)
        now = optimized_at or datetime.now(UTC)
        pairs = tuple((item, optimize_item(item)) for item in plan.items)
        item_optimizations = tuple(
            ItemOptimization(item_id=item.item_id, store_id=item.store_id, result=result)
            for item, result in pairs
        )
        total_incremental = round(
            sum(result.incremental_gross_margin for _, result in pairs), 4
        )
        violation_count = count_hard_violations(pairs)
        any_infeasible = any(result.infeasible for _, result in pairs)
        requires_approval = any(result.requires_approval for _, result in pairs)
        solver_status = STATUS_INFEASIBLE if any_infeasible else STATUS_OPTIMAL
        optimization = PlanOptimization(
            plan_id=plan.plan_id,
            items=item_optimizations,
            total_incremental_gross_margin=total_incremental,
            hard_constraint_violation_count=violation_count,
            solver_status=solver_status,
            requires_approval=requires_approval,
            solver_version=PRICEOPS_SOLVER_VERSION,
            optimized_at=now,
        )
        self.repository.save_optimization(optimization)
        # A rollback plan must exist before any price is executed (AC: rollback
        # plan exists before execution; ODP-OR-01 OR-007).
        self.repository.save_rollback_plan(
            build_rollback_plan(plan=plan, created_at=now)
        )
        self._advance(plan, PlanStatus.OPTIMIZED, actor=actor, reason=reason, occurred_at=now)
        return optimization

    # -- approval ---------------------------------------------------------
    def submit_for_approval(
        self,
        plan_id: str,
        *,
        actor: str = "system",
        reason: str = "submitted for pilot approval",
        occurred_at: datetime | None = None,
    ) -> PricingPlan:
        plan = self._require_plan(plan_id)
        return self._advance(
            plan,
            PlanStatus.PENDING_APPROVAL,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )

    def approve(
        self,
        plan_id: str,
        *,
        actor_id: str,
        reason: str,
        decision: str = "approved",
        approved_at: datetime | None = None,
    ) -> ApprovalRecord:
        plan = self._require_plan(plan_id)
        now = approved_at or datetime.now(UTC)
        record = ApprovalRecord(
            decision_id=f"pricing-approval-{uuid4()}",
            plan_id=plan.plan_id,
            actor_id=actor_id,
            decision=decision,
            decision_reason=reason,
            approved_at=now,
        )
        self.repository.save_approval(record)
        target = PlanStatus.APPROVED_FOR_PILOT if record.is_approved else PlanStatus.STOP
        self._advance(plan, target, actor=actor_id, reason=reason, occurred_at=now)
        return record

    # -- activation / execution ------------------------------------------
    def activate(
        self,
        plan_id: str,
        *,
        executor: str = "system",
        intervention_type: str = "price_adjustment",
        measurement_method: str = "before_after",
        correlation_id: str | None = None,
        executed_at: datetime | None = None,
        label_maturity_time: datetime | None = None,
    ) -> ActivationResult:
        plan = self._require_plan(plan_id)
        optimization = self.repository.get_optimization(plan_id)
        if optimization is None:
            raise PlanNotFoundError(f"plan {plan_id} has no optimization to activate")
        rollback_plan = self.repository.get_rollback_plan(plan_id)
        if rollback_plan is None:
            # Enforce "rollback plan exists before execution" (ODP-OR-01 OR-007).
            raise MissingRollbackPlanError(
                f"plan {plan_id} cannot be executed without a rollback plan"
            )
        now = executed_at or datetime.now(UTC)
        corr = correlation_id or plan.correlation_id
        item_by_id = {item.item_id: item for item in plan.items}

        treatments = tuple(
            PriceTreatment(
                item_id=item_opt.item_id,
                store_id=item_opt.store_id,
                machine_type=item_by_id[item_opt.item_id].machine_type,
                from_price=item_opt.result.current_price,
                to_price=item_opt.result.recommended_price,
                expected_incremental_gross_margin=item_opt.result.incremental_gross_margin,
            )
            for item_opt in optimization.items
            if item_opt.result.price_changed
        )
        execution = PricingExecution(
            execution_id=f"pricing-execution-{uuid4()}",
            plan_id=plan.plan_id,
            executor=executor,
            status="succeeded",
            executed_at=now,
            correlation_id=corr,
            treatments=treatments,
        )
        self.repository.save_execution(execution)

        maturity = label_maturity_time or (now + timedelta(days=DEFAULT_LABEL_MATURITY_DAYS))
        label_entry = LabelRegistryEntry(
            entry_id=f"pricing-label-{uuid4()}",
            plan_id=plan.plan_id,
            execution_id=execution.execution_id,
            label_key=f"pricing/{plan.plan_id}",
            measurement_method=measurement_method,
            label_maturity_time=maturity,
        )
        self.repository.save_label_entry(label_entry)

        handoff = InterventionTreatmentHandoff(
            handoff_id=f"pricing-intervention-handoff-{uuid4()}",
            plan_id=plan.plan_id,
            execution_id=execution.execution_id,
            intervention_type=intervention_type,
            treatments=treatments,
            label_registry_entry_id=label_entry.entry_id,
            correlation_id=corr,
            created_at=now,
        )
        self.repository.save_handoff(handoff)

        activated = self._advance(
            plan,
            PlanStatus.ACTIVE,
            actor=executor,
            reason="price treatment executed",
            correlation_id=corr,
            occurred_at=now,
        )
        return ActivationResult(
            plan=activated,
            execution=execution,
            label_entry=label_entry,
            handoff=handoff,
            rollback_plan=rollback_plan,
        )

    # -- observation ------------------------------------------------------
    def start_observation(
        self,
        plan_id: str,
        *,
        actor: str = "system",
        start_time: datetime | None = None,
        stop_conditions: dict[str, Any] | None = None,
    ) -> ObservationWindow:
        plan = self._require_plan(plan_id)
        start = start_time or datetime.now(UTC)
        window = build_observation_window(
            plan_id=plan.plan_id,
            start_time=start,
            stop_conditions=stop_conditions,
        )
        self.repository.save_window(window)
        self._advance(
            plan,
            PlanStatus.OBSERVING,
            actor=actor,
            reason="pilot observation window opened",
            occurred_at=start,
        )
        return window

    # -- evaluation -------------------------------------------------------
    def evaluate(
        self,
        plan_id: str,
        *,
        actual_gross_margin: float,
        actor: str = "system",
        measurement_method: str = "before_after",
        evidence_level: str = "medium",
        negative_impact_threshold: float = DEFAULT_NEGATIVE_IMPACT_THRESHOLD,
        outcome_window: tuple[datetime, datetime] | None = None,
        generated_at: datetime | None = None,
    ) -> EvaluationResult:
        plan = self._require_plan(plan_id)
        simulation = self.repository.get_simulation(plan_id)
        optimization = self.repository.get_optimization(plan_id)
        if simulation is None or optimization is None:
            raise PlanNotFoundError(
                f"plan {plan_id} must be simulated and optimized before evaluation"
            )
        now = generated_at or datetime.now(UTC)
        window = self.repository.get_window(plan_id)
        if outcome_window is not None:
            resolved_window = outcome_window
        elif window is not None:
            resolved_window = (window.start_time, window.end_time)
        else:
            resolved_window = (plan.created_at, now)

        baseline_gross_margin = simulation.expected_gross_margin
        evaluation = evaluate_effect(
            plan_id=plan.plan_id,
            baseline_gross_margin=baseline_gross_margin,
            expected_incremental_gross_margin=optimization.total_incremental_gross_margin,
            actual_gross_margin=actual_gross_margin,
            outcome_window=resolved_window,
            label_maturity_time=resolved_window[1],
            measurement_method=measurement_method,
            evidence_level=evidence_level,
            negative_impact_threshold=negative_impact_threshold,
            generated_at=now,
        )
        self.repository.save_evaluation(evaluation)

        evaluated = self._advance(
            plan,
            PlanStatus.EVALUATED,
            actor=actor,
            reason="pilot effect evaluated",
            occurred_at=now,
        )
        final = self._advance(
            evaluated,
            evaluation.recommended_next_status,
            actor=actor,
            reason=evaluation.rollback.detail
            if evaluation.rollback.recommended
            else f"effect impact_ratio={evaluation.impact_ratio}",
            occurred_at=now,
        )
        return EvaluationResult(plan=final, evaluation=evaluation)

    # -- explicit rollback ------------------------------------------------
    def rollback(
        self,
        plan_id: str,
        *,
        actor: str,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> PricingPlan:
        plan = self._require_plan(plan_id)
        return self._advance(
            plan, PlanStatus.ROLLBACK, actor=actor, reason=reason, occurred_at=occurred_at
        )

    # -- internals --------------------------------------------------------
    def _require_plan(self, plan_id: str) -> PricingPlan:
        plan = self.repository.get_plan(plan_id)
        if plan is None:
            raise PlanNotFoundError(f"plan {plan_id} not found")
        return plan

    def _advance(
        self,
        plan: PricingPlan,
        to_status: PlanStatus,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> PricingPlan:
        moved = plan.transition(
            to_status,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
        )
        return self.repository.save_plan(moved)


__all__ = [
    "DEFAULT_LABEL_MATURITY_DAYS",
    "ActivationResult",
    "EvaluationResult",
    "MissingRollbackPlanError",
    "PlanNotFoundError",
    "PriceOpsService",
]
