"""PriceOps domain model: plans, lifecycle state machine, approval, execution,
observation, effect evaluation and rollback (ODP-MOD-06).

The numeric work (constraints, demand simulation, optimization) lives in
``solver.pricing``. This module owns the *lifecycle*: the plan aggregate, its
state machine and the audit-bearing records produced at each transition.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

from solver.pricing.constraints import PRICING_POLICY_VERSION, ConstraintViolation, PriceConstraints
from solver.pricing.demand import SimulationResult, simulate_price
from solver.pricing.optimizer import SOLVER_VERSION, OptimizationResult, optimize_price

PRICEOPS_MODEL_VERSION = "priceops-elasticity-baseline-v1"
PRICEOPS_FEATURE_VERSION = "pricing-action-view-v1"
PRICEOPS_POLICY_VERSION = PRICING_POLICY_VERSION
PRICEOPS_SOLVER_VERSION = SOLVER_VERSION

# Default rollback trigger: a realised gross-margin loss of 5% or worse versus
# the pre-treatment baseline produces a rollback recommendation (AC-06-04).
DEFAULT_NEGATIVE_IMPACT_THRESHOLD = 0.05

DEFAULT_STOP_CONDITIONS: dict[str, Any] = {
    "max_gross_margin_drop_ratio": DEFAULT_NEGATIVE_IMPACT_THRESHOLD,
    "min_observation_days": 14,
    "max_observation_days": 28,
    "min_sample_size": 30,
}


class PlanStatus(StrEnum):
    CANDIDATE = "candidate"
    SIMULATED = "simulated"
    OPTIMIZED = "optimized"
    PENDING_APPROVAL = "pending_approval"
    APPROVED_FOR_PILOT = "approved_for_pilot"
    ACTIVE = "active"
    OBSERVING = "observing"
    EVALUATED = "evaluated"
    CONTINUE = "continue"
    ADJUST = "adjust"
    STOP = "stop"
    ROLLBACK = "rollback"


# Allowed forward transitions. ROLLBACK is reachable from any live state so a
# negative pilot can always be unwound; STOP is the terminal reject path.
VALID_TRANSITIONS: dict[PlanStatus, frozenset[PlanStatus]] = {
    PlanStatus.CANDIDATE: frozenset({PlanStatus.SIMULATED, PlanStatus.STOP}),
    PlanStatus.SIMULATED: frozenset({PlanStatus.OPTIMIZED, PlanStatus.STOP}),
    PlanStatus.OPTIMIZED: frozenset({PlanStatus.PENDING_APPROVAL, PlanStatus.STOP}),
    PlanStatus.PENDING_APPROVAL: frozenset({PlanStatus.APPROVED_FOR_PILOT, PlanStatus.STOP}),
    PlanStatus.APPROVED_FOR_PILOT: frozenset({PlanStatus.ACTIVE, PlanStatus.STOP}),
    PlanStatus.ACTIVE: frozenset({PlanStatus.OBSERVING, PlanStatus.ROLLBACK, PlanStatus.STOP}),
    PlanStatus.OBSERVING: frozenset({PlanStatus.EVALUATED, PlanStatus.ROLLBACK, PlanStatus.STOP}),
    PlanStatus.EVALUATED: frozenset(
        {
            PlanStatus.CONTINUE,
            PlanStatus.ADJUST,
            PlanStatus.STOP,
            PlanStatus.ROLLBACK,
        }
    ),
    PlanStatus.CONTINUE: frozenset(),
    PlanStatus.ADJUST: frozenset({PlanStatus.SIMULATED, PlanStatus.STOP}),
    PlanStatus.STOP: frozenset(),
    PlanStatus.ROLLBACK: frozenset(),
}


class InvalidTransitionError(ValueError):
    """Raised when a plan is moved between states the machine forbids."""


@dataclass(frozen=True)
class StatusTransition:
    """One row of a plan's ``status_history`` audit trail (§7.1)."""

    from_status: PlanStatus
    to_status: PlanStatus
    actor: str
    reason: str
    occurred_at: datetime
    correlation_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_status": self.from_status.value,
            "to_status": self.to_status.value,
            "actor": self.actor,
            "reason": self.reason,
            "occurred_at": self.occurred_at.isoformat(),
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class PriceElasticityEstimate:
    """Prediction output; carries the required versioning fields (§5.1)."""

    elasticity_value: float
    confidence: float
    horizon: str = "4week"
    model_version: str = PRICEOPS_MODEL_VERSION
    feature_version: str = PRICEOPS_FEATURE_VERSION
    prediction_origin_time: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_observations(
        cls,
        price_demand_observations: Sequence[tuple[float, float]],
        *,
        horizon: str = "4week",
        prediction_origin_time: datetime | None = None,
    ) -> PriceElasticityEstimate:
        """Estimate the elasticity from ``(price, demand)`` history.

        Delegates to the scikit-learn log-log OLS estimator in
        ``solver.pricing.demand`` and carries the fit's R²-derived confidence, so
        a plan item's elasticity is grounded in observed data rather than a
        hand-supplied constant.
        """
        from solver.pricing.demand import estimate_elasticity

        fit = estimate_elasticity(price_demand_observations)
        return cls(
            elasticity_value=fit.elasticity,
            confidence=fit.confidence,
            horizon=horizon,
            prediction_origin_time=prediction_origin_time or datetime.now(UTC),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "elasticity_value": self.elasticity_value,
            "confidence": self.confidence,
            "horizon": self.horizon,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "prediction_origin_time": self.prediction_origin_time.isoformat(),
        }


@dataclass(frozen=True)
class PricingPlanItem:
    """A single store/machine line within a plan."""

    item_id: str
    store_id: str
    machine_type: str
    constraints: PriceConstraints
    baseline_demand: float
    elasticity: PriceElasticityEstimate
    source_snapshot_ids: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        store_id: str,
        machine_type: str,
        constraints: PriceConstraints,
        baseline_demand: float,
        elasticity: PriceElasticityEstimate,
        source_snapshot_ids: tuple[str, ...] = (),
        item_id: str | None = None,
    ) -> PricingPlanItem:
        return cls(
            item_id=item_id or f"pricing-plan-item-{uuid4()}",
            store_id=store_id,
            machine_type=machine_type,
            constraints=constraints,
            baseline_demand=baseline_demand,
            elasticity=elasticity,
            source_snapshot_ids=source_snapshot_ids,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "store_id": self.store_id,
            "machine_type": self.machine_type,
            "constraints": self.constraints.to_dict(),
            "baseline_demand": self.baseline_demand,
            "elasticity": self.elasticity.to_dict(),
            "source_snapshot_ids": list(self.source_snapshot_ids),
        }


@dataclass(frozen=True)
class PricingPlan:
    """Plan aggregate. Immutable; transitions return a new plan."""

    plan_id: str
    tenant_id: str
    status: PlanStatus
    items: tuple[PricingPlanItem, ...]
    created_at: datetime
    correlation_id: str
    status_history: tuple[StatusTransition, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        items: tuple[PricingPlanItem, ...],
        correlation_id: str,
        plan_id: str | None = None,
        created_at: datetime | None = None,
    ) -> PricingPlan:
        return cls(
            plan_id=plan_id or f"pricing-plan-{uuid4()}",
            tenant_id=tenant_id,
            status=PlanStatus.CANDIDATE,
            items=items,
            created_at=created_at or datetime.now(UTC),
            correlation_id=correlation_id,
            status_history=(),
        )

    def can_transition_to(self, to_status: PlanStatus) -> bool:
        return to_status in VALID_TRANSITIONS.get(self.status, frozenset())

    def transition(
        self,
        to_status: PlanStatus,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> PricingPlan:
        if not self.can_transition_to(to_status):
            raise InvalidTransitionError(
                f"cannot move plan {self.plan_id} from {self.status.value} to {to_status.value}"
            )
        transition = StatusTransition(
            from_status=self.status,
            to_status=to_status,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at or datetime.now(UTC),
            correlation_id=correlation_id or self.correlation_id,
        )
        return replace(
            self,
            status=to_status,
            status_history=self.status_history + (transition,),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "tenant_id": self.tenant_id,
            "status": self.status.value,
            "items": [item.to_dict() for item in self.items],
            "created_at": self.created_at.isoformat(),
            "correlation_id": self.correlation_id,
            "status_history": [t.to_dict() for t in self.status_history],
        }


@dataclass(frozen=True)
class ItemSimulation:
    item_id: str
    store_id: str
    simulation: SimulationResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "store_id": self.store_id,
            **self.simulation.to_dict(),
        }


@dataclass(frozen=True)
class PlanSimulation:
    """Per-item demand/margin/risk envelopes for a plan (AC-06-02)."""

    plan_id: str
    items: tuple[ItemSimulation, ...]
    generated_at: datetime

    @property
    def expected_gross_margin(self) -> float:
        return round(sum(item.simulation.expected_gross_margin for item in self.items), 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "items": [item.to_dict() for item in self.items],
            "expected_gross_margin": self.expected_gross_margin,
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass(frozen=True)
class ItemOptimization:
    item_id: str
    store_id: str
    result: OptimizationResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "store_id": self.store_id,
            **self.result.to_dict(),
        }


@dataclass(frozen=True)
class PlanOptimization:
    """Optimized prices for a plan. ``hard_constraint_violation_count`` is the
    AC-06-01 invariant and must always be 0."""

    plan_id: str
    items: tuple[ItemOptimization, ...]
    total_incremental_gross_margin: float
    hard_constraint_violation_count: int
    solver_status: str
    requires_approval: bool
    solver_version: str
    optimized_at: datetime
    solver_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_constraint_safe(self) -> bool:
        return self.hard_constraint_violation_count == 0

    @property
    def is_feasible(self) -> bool:
        return all(not item.result.infeasible for item in self.items)

    @property
    def is_approvable(self) -> bool:
        return self.is_constraint_safe and self.is_feasible

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "items": [item.to_dict() for item in self.items],
            "total_incremental_gross_margin": self.total_incremental_gross_margin,
            "hard_constraint_violation_count": self.hard_constraint_violation_count,
            "is_constraint_safe": self.is_constraint_safe,
            "is_feasible": self.is_feasible,
            "is_approvable": self.is_approvable,
            "solver_status": self.solver_status,
            "requires_approval": self.requires_approval,
            "solver_version": self.solver_version,
            "solver_metadata": self.solver_metadata,
            "optimized_at": self.optimized_at.isoformat(),
        }


@dataclass(frozen=True)
class ApprovalRecord:
    """Decision output; carries the required decision fields (§5.1)."""

    decision_id: str
    plan_id: str
    actor_id: str
    decision: str  # "approved" | "rejected"
    decision_reason: str
    approved_at: datetime
    policy_version: str = PRICEOPS_POLICY_VERSION

    @property
    def is_approved(self) -> bool:
        return self.decision == "approved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "plan_id": self.plan_id,
            "actor_id": self.actor_id,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "approved_at": self.approved_at.isoformat(),
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class ObservationWindow:
    """Pilot observation window with explicit stop conditions (AC-06-03)."""

    window_id: str
    plan_id: str
    start_time: datetime
    end_time: datetime
    stop_conditions: Mapping[str, Any]
    status: str = "observing"

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "plan_id": self.plan_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "stop_conditions": dict(self.stop_conditions),
            "status": self.status,
        }


@dataclass(frozen=True)
class PriceTreatment:
    """A single executed price change, the unit handed to InterventionOps."""

    item_id: str
    store_id: str
    machine_type: str
    from_price: float
    to_price: float
    expected_incremental_gross_margin: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "store_id": self.store_id,
            "machine_type": self.machine_type,
            "from_price": self.from_price,
            "to_price": self.to_price,
            "expected_incremental_gross_margin": self.expected_incremental_gross_margin,
        }


@dataclass(frozen=True)
class PricingExecution:
    """Execution output; carries the required execution fields (§5.1)."""

    execution_id: str
    plan_id: str
    executor: str
    status: str  # "succeeded" | "failed" | "partial"
    executed_at: datetime
    correlation_id: str
    treatments: tuple[PriceTreatment, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "plan_id": self.plan_id,
            "executor": self.executor,
            "status": self.status,
            "executed_at": self.executed_at.isoformat(),
            "correlation_id": self.correlation_id,
            "treatments": [t.to_dict() for t in self.treatments],
        }


@dataclass(frozen=True)
class LabelRegistryEntry:
    """Label Registry hand-off so the outcome can mature for evaluation (AC-06-05)."""

    entry_id: str
    plan_id: str
    execution_id: str
    label_key: str
    measurement_method: str
    label_maturity_time: datetime
    evidence_level: str = "pending"
    status: str = "registered"

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "plan_id": self.plan_id,
            "execution_id": self.execution_id,
            "label_key": self.label_key,
            "measurement_method": self.measurement_method,
            "label_maturity_time": self.label_maturity_time.isoformat(),
            "evidence_level": self.evidence_level,
            "status": self.status,
        }


@dataclass(frozen=True)
class InterventionTreatmentHandoff:
    """Price treatment written back to InterventionOps (AC-06-05)."""

    handoff_id: str
    plan_id: str
    execution_id: str
    intervention_type: str
    treatments: tuple[PriceTreatment, ...]
    label_registry_entry_id: str
    correlation_id: str
    created_at: datetime
    status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "plan_id": self.plan_id,
            "execution_id": self.execution_id,
            "intervention_type": self.intervention_type,
            "treatments": [t.to_dict() for t in self.treatments],
            "label_registry_entry_id": self.label_registry_entry_id,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at.isoformat(),
            "status": self.status,
        }


@dataclass(frozen=True)
class RollbackRecommendation:
    recommended: bool
    reason_code: str
    impact_ratio: float
    threshold: float
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended": self.recommended,
            "reason_code": self.reason_code,
            "impact_ratio": self.impact_ratio,
            "threshold": self.threshold,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ItemRevert:
    """How to revert a single item if the pilot is rolled back."""

    item_id: str
    store_id: str
    revert_to_price: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "store_id": self.store_id,
            "revert_to_price": self.revert_to_price,
        }


@dataclass(frozen=True)
class RollbackPlan:
    """A concrete revert plan with trigger conditions.

    Created at optimization time so that a rollback plan provably exists *before*
    any price is executed (acceptance: "rollback plan exists before execution";
    ODP-OR-01 OR-007). Reverting restores each item's pre-treatment price.
    """

    rollback_plan_id: str
    plan_id: str
    reverts: tuple[ItemRevert, ...]
    trigger_conditions: Mapping[str, Any]
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "rollback_plan_id": self.rollback_plan_id,
            "plan_id": self.plan_id,
            "reverts": [r.to_dict() for r in self.reverts],
            "trigger_conditions": dict(self.trigger_conditions),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class PricingEffectEvaluation:
    """Outcome output; carries the required outcome fields (§5.1)."""

    evaluation_id: str
    plan_id: str
    outcome_window: tuple[datetime, datetime]
    label_maturity_time: datetime
    measurement_method: str
    evidence_level: str
    baseline_gross_margin: float
    expected_incremental_gross_margin: float
    actual_incremental_gross_margin: float
    impact_ratio: float
    rollback: RollbackRecommendation
    recommended_next_status: PlanStatus
    generated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "plan_id": self.plan_id,
            "outcome_window": [
                self.outcome_window[0].isoformat(),
                self.outcome_window[1].isoformat(),
            ],
            "label_maturity_time": self.label_maturity_time.isoformat(),
            "measurement_method": self.measurement_method,
            "evidence_level": self.evidence_level,
            "baseline_gross_margin": self.baseline_gross_margin,
            "expected_incremental_gross_margin": self.expected_incremental_gross_margin,
            "actual_incremental_gross_margin": self.actual_incremental_gross_margin,
            "impact_ratio": self.impact_ratio,
            "rollback": self.rollback.to_dict(),
            "recommended_next_status": self.recommended_next_status.value,
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass(frozen=True)
class ItemPlanComparison:
    """Current-vs-candidate price view for approval and operator UI."""

    item_id: str
    store_id: str
    machine_type: str
    current_price: float
    candidate_price: float
    price_changed: bool
    baseline_simulation: SimulationResult
    candidate_simulation: SimulationResult
    expected_demand_change: float
    expected_revenue_change: float
    expected_gross_margin_change: float
    risk_level: str
    constraint_status: str
    requires_approval: bool
    binding_constraints: tuple[str, ...]
    constraint_violations: tuple[ConstraintViolation, ...]
    safe_action_set: tuple[float, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "store_id": self.store_id,
            "machine_type": self.machine_type,
            "current_price": self.current_price,
            "candidate_price": self.candidate_price,
            "price_changed": self.price_changed,
            "baseline_simulation": self.baseline_simulation.to_dict(),
            "candidate_simulation": self.candidate_simulation.to_dict(),
            "expected_demand_change": self.expected_demand_change,
            "expected_revenue_change": self.expected_revenue_change,
            "expected_gross_margin_change": self.expected_gross_margin_change,
            "risk_level": self.risk_level,
            "constraint_status": self.constraint_status,
            "requires_approval": self.requires_approval,
            "binding_constraints": list(self.binding_constraints),
            "constraint_violations": [v.to_dict() for v in self.constraint_violations],
            "safe_action_set": list(self.safe_action_set),
        }


@dataclass(frozen=True)
class PricingPlanComparison:
    """Plan-level snapshot used for scheme comparison, approval, monitoring and rollback."""

    plan_id: str
    plan_status: PlanStatus
    generated_at: datetime
    items: tuple[ItemPlanComparison, ...]
    total_current_gross_margin: float
    total_candidate_gross_margin: float
    total_expected_incremental_gross_margin: float
    hard_constraint_violation_count: int
    is_constraint_safe: bool
    is_feasible: bool
    is_approvable: bool
    requires_approval: bool
    approval_status: str
    rollback_ready: bool
    execution_status: str | None = None
    monitoring_status: str | None = None
    outcome_status: str | None = None
    rollback_recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "plan_status": self.plan_status.value,
            "generated_at": self.generated_at.isoformat(),
            "items": [item.to_dict() for item in self.items],
            "total_current_gross_margin": self.total_current_gross_margin,
            "total_candidate_gross_margin": self.total_candidate_gross_margin,
            "total_expected_incremental_gross_margin": self.total_expected_incremental_gross_margin,
            "hard_constraint_violation_count": self.hard_constraint_violation_count,
            "is_constraint_safe": self.is_constraint_safe,
            "is_feasible": self.is_feasible,
            "is_approvable": self.is_approvable,
            "requires_approval": self.requires_approval,
            "approval_status": self.approval_status,
            "rollback_ready": self.rollback_ready,
            "execution_status": self.execution_status,
            "monitoring_status": self.monitoring_status,
            "outcome_status": self.outcome_status,
            "rollback_recommended": self.rollback_recommended,
        }


def build_observation_window(
    *,
    plan_id: str,
    start_time: datetime,
    stop_conditions: Mapping[str, Any] | None = None,
    window_id: str | None = None,
) -> ObservationWindow:
    conditions = dict(DEFAULT_STOP_CONDITIONS)
    if stop_conditions:
        conditions.update(stop_conditions)
    duration_days = int(conditions.get("max_observation_days", 28))
    return ObservationWindow(
        window_id=window_id or f"pricing-observation-{uuid4()}",
        plan_id=plan_id,
        start_time=start_time,
        end_time=start_time + timedelta(days=duration_days),
        stop_conditions=conditions,
    )


def build_rollback_plan(
    *,
    plan: PricingPlan,
    negative_impact_threshold: float = DEFAULT_NEGATIVE_IMPACT_THRESHOLD,
    stop_conditions: Mapping[str, Any] | None = None,
    created_at: datetime | None = None,
    rollback_plan_id: str | None = None,
) -> RollbackPlan:
    """Build the pre-execution rollback plan: revert every item to its current
    price, with the trigger conditions that should fire a rollback."""
    reverts = tuple(
        ItemRevert(
            item_id=item.item_id,
            store_id=item.store_id,
            revert_to_price=item.constraints.current_price,
        )
        for item in plan.items
    )
    conditions = dict(DEFAULT_STOP_CONDITIONS)
    if stop_conditions:
        conditions.update(stop_conditions)
    conditions["negative_impact_threshold"] = negative_impact_threshold
    return RollbackPlan(
        rollback_plan_id=rollback_plan_id or f"pricing-rollback-plan-{uuid4()}",
        plan_id=plan.plan_id,
        reverts=reverts,
        trigger_conditions=conditions,
        created_at=created_at or datetime.now(UTC),
    )


def simulate_item(item: PricingPlanItem) -> SimulationResult:
    """Simulate an item at its *current* price (the plan's baseline view)."""
    return simulate_price(
        price=item.constraints.current_price,
        baseline_demand=item.baseline_demand,
        baseline_price=item.constraints.current_price,
        unit_cost=item.constraints.unit_cost,
        elasticity=item.elasticity.elasticity_value,
        confidence=item.elasticity.confidence,
    )


def optimize_item(item: PricingPlanItem) -> OptimizationResult:
    return optimize_price(
        constraints=item.constraints,
        baseline_demand=item.baseline_demand,
        elasticity=item.elasticity.elasticity_value,
        confidence=item.elasticity.confidence,
    )


def recommended_price_violations(
    item: PricingPlanItem, result: OptimizationResult
) -> list[ConstraintViolation]:
    """Re-validate a recommended price against the item's hard constraints.

    A recommendation that holds the current price (no change) introduces no new
    action, so it is never counted as a violation. A recommended *change* is
    re-checked from scratch — the AC-06-01 invariant is asserted on real data
    rather than merely trusted from the solver.
    """
    if not result.price_changed:
        return []
    return item.constraints.violations(result.recommended_price)


def count_hard_violations(
    pairs: tuple[tuple[PricingPlanItem, OptimizationResult], ...],
) -> int:
    """Total hard-constraint breaches across recommended price changes (AC-06-01)."""
    return sum(len(recommended_price_violations(item, result)) for item, result in pairs)


def evaluate_effect(
    *,
    plan_id: str,
    baseline_gross_margin: float,
    expected_incremental_gross_margin: float,
    actual_gross_margin: float,
    outcome_window: tuple[datetime, datetime],
    label_maturity_time: datetime,
    measurement_method: str = "before_after",
    evidence_level: str = "medium",
    negative_impact_threshold: float = DEFAULT_NEGATIVE_IMPACT_THRESHOLD,
    generated_at: datetime | None = None,
    evaluation_id: str | None = None,
) -> PricingEffectEvaluation:
    """Attribute realised effect and decide continue/adjust/stop/rollback."""
    actual_incremental = round(actual_gross_margin - baseline_gross_margin, 4)
    if baseline_gross_margin > 0:
        impact_ratio = round(actual_incremental / baseline_gross_margin, 6)
    else:
        impact_ratio = 0.0

    if impact_ratio <= -negative_impact_threshold:
        rollback = RollbackRecommendation(
            recommended=True,
            reason_code="negative_margin_impact",
            impact_ratio=impact_ratio,
            threshold=negative_impact_threshold,
            detail="realised gross margin fell below the rollback threshold",
        )
        next_status = PlanStatus.ROLLBACK
    else:
        rollback = RollbackRecommendation(
            recommended=False,
            reason_code="within_tolerance",
            impact_ratio=impact_ratio,
            threshold=negative_impact_threshold,
            detail="realised impact within acceptable tolerance",
        )
        if (
            expected_incremental_gross_margin > 0
            and actual_incremental >= 0.5 * expected_incremental_gross_margin
        ):
            next_status = PlanStatus.CONTINUE
        elif actual_incremental > 0:
            next_status = PlanStatus.ADJUST
        else:
            next_status = PlanStatus.STOP

    return PricingEffectEvaluation(
        evaluation_id=evaluation_id or f"pricing-effect-{uuid4()}",
        plan_id=plan_id,
        outcome_window=outcome_window,
        label_maturity_time=label_maturity_time,
        measurement_method=measurement_method,
        evidence_level=evidence_level,
        baseline_gross_margin=round(baseline_gross_margin, 4),
        expected_incremental_gross_margin=round(expected_incremental_gross_margin, 4),
        actual_incremental_gross_margin=actual_incremental,
        impact_ratio=impact_ratio,
        rollback=rollback,
        recommended_next_status=next_status,
        generated_at=generated_at or datetime.now(UTC),
    )


__all__ = [
    "DEFAULT_NEGATIVE_IMPACT_THRESHOLD",
    "DEFAULT_STOP_CONDITIONS",
    "PRICEOPS_FEATURE_VERSION",
    "PRICEOPS_MODEL_VERSION",
    "PRICEOPS_POLICY_VERSION",
    "PRICEOPS_SOLVER_VERSION",
    "VALID_TRANSITIONS",
    "ApprovalRecord",
    "ConstraintViolation",
    "InterventionTreatmentHandoff",
    "InvalidTransitionError",
    "ItemOptimization",
    "ItemPlanComparison",
    "ItemRevert",
    "ItemSimulation",
    "LabelRegistryEntry",
    "ObservationWindow",
    "PlanOptimization",
    "PricingPlanComparison",
    "PlanSimulation",
    "PlanStatus",
    "PriceConstraints",
    "PriceElasticityEstimate",
    "PriceTreatment",
    "PricingEffectEvaluation",
    "PricingExecution",
    "PricingPlan",
    "PricingPlanItem",
    "RollbackPlan",
    "RollbackRecommendation",
    "StatusTransition",
    "build_observation_window",
    "build_rollback_plan",
    "count_hard_violations",
    "evaluate_effect",
    "optimize_item",
    "recommended_price_violations",
    "simulate_item",
]
