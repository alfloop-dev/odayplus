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

from models.shared_ml.production_runtime import (
    ProductionExecutionConfigurationError,
    production_execution_required,
)
from modules.priceops.domain.pricing import (
    DEFAULT_NEGATIVE_IMPACT_THRESHOLD,
    PRICEOPS_SOLVER_VERSION,
    ApprovalRecord,
    InterventionTreatmentHandoff,
    InvalidTransitionError,
    ItemOptimization,
    ItemPlanComparison,
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
    PricingPlanComparison,
    PricingPlanItem,
    RollbackPlan,
    build_observation_window,
    build_rollback_plan,
    count_hard_violations,
    evaluate_effect,
    optimize_item,
    simulate_item,
)
from modules.priceops.infrastructure.oss_optimizer import (
    PRICEOPS_OSS_SOLVER_VERSION,
    PriceOpsProductionOptimizer,
)
from modules.priceops.infrastructure.repositories import InMemoryPriceOpsRepository
from solver.pricing.optimizer import STATUS_INFEASIBLE, STATUS_OPTIMAL

# Label maturity horizon when the caller does not supply one explicitly.
DEFAULT_LABEL_MATURITY_DAYS = 28


class PlanNotFoundError(LookupError):
    """Raised when a plan_id does not resolve to a stored plan."""


class MissingRollbackPlanError(RuntimeError):
    """Raised when a plan would execute without a pre-existing rollback plan."""


class ApprovalBlockedError(RuntimeError):
    """Raised when approval would violate hard PriceOps safety gates."""


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
    def __init__(
        self,
        *,
        repository: InMemoryPriceOpsRepository | None = None,
        production_optimizer: PriceOpsProductionOptimizer | None = None,
        runtime_mode: str | None = None,
    ) -> None:
        self.production_required = production_execution_required(runtime_mode)
        self.strict_production_composition = runtime_mode is not None and self.production_required
        if self.strict_production_composition and (
            repository is None or isinstance(repository, InMemoryPriceOpsRepository)
        ):
            raise ProductionExecutionConfigurationError(
                "PriceOps production requires an injected durable repository"
            )
        if self.strict_production_composition and production_optimizer is None:
            raise ProductionExecutionConfigurationError(
                "PriceOps production requires an injected Optuna/CVXPY optimizer"
            )
        self.repository = repository or InMemoryPriceOpsRepository()
        self.production_optimizer = production_optimizer

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
        solver_metadata: dict[str, Any] = {}
        solver_version = PRICEOPS_SOLVER_VERSION
        if self.production_required:
            executor = self.production_optimizer or PriceOpsProductionOptimizer()
            execution = executor.optimize(plan)
            pairs = execution.results
            solver_metadata = execution.metadata
            solver_version = PRICEOPS_OSS_SOLVER_VERSION
        else:
            pairs = tuple((item, optimize_item(item)) for item in plan.items)
        item_optimizations = tuple(
            ItemOptimization(item_id=item.item_id, store_id=item.store_id, result=result)
            for item, result in pairs
        )
        total_incremental = round(sum(result.incremental_gross_margin for _, result in pairs), 4)
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
            solver_version=solver_version,
            optimized_at=now,
            solver_metadata=solver_metadata,
        )
        self.repository.save_optimization(optimization)
        # A rollback plan must exist before any price is executed (AC: rollback
        # plan exists before execution; ODP-OR-01 OR-007).
        self.repository.save_rollback_plan(build_rollback_plan(plan=plan, created_at=now))
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
        normalized_decision = _normalize_approval_decision(decision)
        target = (
            PlanStatus.APPROVED_FOR_PILOT if normalized_decision == "approved" else PlanStatus.STOP
        )
        if not plan.can_transition_to(target):
            raise InvalidTransitionError(
                f"cannot move plan {plan.plan_id} from {plan.status.value} to {target.value}"
            )
        if normalized_decision == "approved":
            blockers = self._approval_blockers(plan_id)
            if blockers:
                raise ApprovalBlockedError(
                    f"plan {plan_id} cannot be approved: {'; '.join(blockers)}"
                )
        now = approved_at or datetime.now(UTC)
        record = ApprovalRecord(
            decision_id=f"pricing-approval-{uuid4()}",
            plan_id=plan.plan_id,
            actor_id=actor_id,
            decision=normalized_decision,
            decision_reason=reason,
            approved_at=now,
        )
        self.repository.save_approval(record)
        self._advance(plan, target, actor=actor_id, reason=reason, occurred_at=now)
        return record

    # -- comparison / status snapshot ------------------------------------
    def get_plan_comparison(self, plan_id: str) -> PricingPlanComparison:
        plan = self._require_plan(plan_id)
        optimization = self.repository.get_optimization(plan_id)
        if optimization is None:
            raise PlanNotFoundError(f"plan {plan_id} has no optimization comparison")

        item_by_id = {item.item_id: item for item in plan.items}
        comparison_items: list[ItemPlanComparison] = []
        for item_optimization in optimization.items:
            item = item_by_id[item_optimization.item_id]
            result = item_optimization.result
            baseline = result.baseline_simulation
            candidate = result.recommended_simulation
            hard_failed = result.infeasible or bool(result.constraint_violations)
            constraint_status = (
                "HARD_CONSTRAINT_FAILED"
                if hard_failed
                else "SOFT_WARNING"
                if result.requires_approval
                else "PASS"
            )
            comparison_items.append(
                ItemPlanComparison(
                    item_id=item.item_id,
                    store_id=item.store_id,
                    machine_type=item.machine_type,
                    current_price=result.current_price,
                    candidate_price=result.recommended_price,
                    price_changed=result.price_changed,
                    baseline_simulation=baseline,
                    candidate_simulation=candidate,
                    expected_demand_change=result.expected_demand_change,
                    expected_revenue_change=round(candidate.revenue.p50 - baseline.revenue.p50, 4),
                    expected_gross_margin_change=result.incremental_gross_margin,
                    risk_level=result.risk_level,
                    constraint_status=constraint_status,
                    requires_approval=result.requires_approval,
                    binding_constraints=result.binding_constraints,
                    constraint_violations=result.constraint_violations,
                    safe_action_set=result.safe_action_set,
                )
            )

        approvals = self.repository.list_approvals(plan_id)
        latest_approval = approvals[-1] if approvals else None
        if latest_approval is not None:
            approval_status = (
                "approved" if latest_approval.is_approved else latest_approval.decision
            )
        elif plan.status is PlanStatus.PENDING_APPROVAL:
            approval_status = "pending_review"
        elif plan.status in {
            PlanStatus.APPROVED_FOR_PILOT,
            PlanStatus.ACTIVE,
            PlanStatus.OBSERVING,
            PlanStatus.EVALUATED,
            PlanStatus.CONTINUE,
            PlanStatus.ADJUST,
            PlanStatus.ROLLBACK,
        }:
            approval_status = "approved"
        else:
            approval_status = "not_submitted"

        rollback_plan = self.repository.get_rollback_plan(plan_id)
        execution = self.repository.get_execution(plan_id)
        window = self.repository.get_window(plan_id)
        evaluation = self.repository.get_evaluation(plan_id)
        total_current_gross_margin = round(
            sum(item.baseline_simulation.expected_gross_margin for item in comparison_items),
            4,
        )
        total_candidate_gross_margin = round(
            sum(item.candidate_simulation.expected_gross_margin for item in comparison_items),
            4,
        )
        return PricingPlanComparison(
            plan_id=plan.plan_id,
            plan_status=plan.status,
            generated_at=optimization.optimized_at,
            items=tuple(comparison_items),
            total_current_gross_margin=total_current_gross_margin,
            total_candidate_gross_margin=total_candidate_gross_margin,
            total_expected_incremental_gross_margin=optimization.total_incremental_gross_margin,
            hard_constraint_violation_count=optimization.hard_constraint_violation_count,
            is_constraint_safe=optimization.is_constraint_safe,
            is_feasible=optimization.is_feasible,
            is_approvable=optimization.is_approvable and rollback_plan is not None,
            requires_approval=optimization.requires_approval
            or any(item.price_changed for item in comparison_items),
            approval_status=approval_status,
            rollback_ready=rollback_plan is not None,
            execution_status=execution.status if execution is not None else None,
            monitoring_status=window.status if window is not None else None,
            outcome_status=evaluation.recommended_next_status.value
            if evaluation is not None
            else None,
            rollback_recommended=evaluation.rollback.recommended
            if evaluation is not None
            else False,
        )

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

    def _approval_blockers(self, plan_id: str) -> list[str]:
        blockers: list[str] = []
        optimization = self.repository.get_optimization(plan_id)
        if optimization is None:
            blockers.append("optimization result is missing")
        else:
            if not optimization.is_feasible:
                blockers.append("hard pricing constraint region is infeasible")
            if not optimization.is_constraint_safe:
                blockers.append("recommended price has hard constraint violations")
        if self.repository.get_rollback_plan(plan_id) is None:
            blockers.append("rollback plan is missing")
        return blockers


def _normalize_approval_decision(decision: str) -> str:
    normalized = decision.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"approve", "approved"}:
        return "approved"
    if normalized in {"reject", "rejected", "request_revision", "revision_requested"}:
        return "rejected"
    return normalized


__all__ = [
    "DEFAULT_LABEL_MATURITY_DAYS",
    "ApprovalBlockedError",
    "ActivationResult",
    "EvaluationResult",
    "MissingRollbackPlanError",
    "PlanNotFoundError",
    "PriceOpsService",
]
