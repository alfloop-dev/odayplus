"""NetPlan domain model: scenario building, lifecycle, and outcome tracking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from solver.netplan import (
    SOLVER_VERSION,
    ActionOption,
    NetPlanConstraints,
    NetworkAction,
    NetworkPlanSolveResult,
)

NETPLAN_MODEL_VERSION = "netplan-network-baseline-v1"
NETPLAN_FEATURE_VERSION = "network-plan-view-v1"
NETPLAN_SOLVER_VERSION = SOLVER_VERSION


class NetPlanScenarioStatus(StrEnum):
    DRAFT = "draft"
    SOLVED = "solved"
    INFEASIBLE = "infeasible"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    OUTCOME_OBSERVED = "outcome_observed"
    CLOSED = "closed"


VALID_TRANSITIONS: dict[NetPlanScenarioStatus, frozenset[NetPlanScenarioStatus]] = {
    NetPlanScenarioStatus.DRAFT: frozenset(
        {NetPlanScenarioStatus.SOLVED, NetPlanScenarioStatus.INFEASIBLE}
    ),
    NetPlanScenarioStatus.SOLVED: frozenset(
        {NetPlanScenarioStatus.PENDING_APPROVAL, NetPlanScenarioStatus.REJECTED}
    ),
    NetPlanScenarioStatus.INFEASIBLE: frozenset(),
    NetPlanScenarioStatus.PENDING_APPROVAL: frozenset(
        {NetPlanScenarioStatus.APPROVED, NetPlanScenarioStatus.REJECTED}
    ),
    NetPlanScenarioStatus.APPROVED: frozenset({NetPlanScenarioStatus.EXECUTED}),
    NetPlanScenarioStatus.REJECTED: frozenset(),
    NetPlanScenarioStatus.EXECUTED: frozenset({NetPlanScenarioStatus.OUTCOME_OBSERVED}),
    NetPlanScenarioStatus.OUTCOME_OBSERVED: frozenset({NetPlanScenarioStatus.CLOSED}),
    NetPlanScenarioStatus.CLOSED: frozenset(),
}


class InvalidNetPlanTransitionError(ValueError):
    """Raised when a scenario moves through an invalid lifecycle edge."""


@dataclass(frozen=True)
class StatusTransition:
    from_status: NetPlanScenarioStatus
    to_status: NetPlanScenarioStatus
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
class ExistingStoreInput:
    store_id: str
    baseline_gross_margin: float
    improve_gross_margin_uplift: float = 0.0
    improve_cost: float = 0.0
    move_gross_margin_uplift: float = 0.0
    move_cost: float = 0.0
    exit_cost: float = 0.0
    keep_risk: float = 0.1
    improve_risk: float = 0.25
    move_risk: float = 0.35
    exit_risk: float = 0.2
    current_capacity: int = 1
    source_snapshot_ids: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ExistingStoreInput:
        return cls(
            store_id=str(data["store_id"]),
            baseline_gross_margin=float(data.get("baseline_gross_margin", data.get("baseline_gm", 0.0))),
            improve_gross_margin_uplift=float(data.get("improve_gross_margin_uplift", data.get("improve_gm_uplift", 0.0))),
            improve_cost=float(data.get("improve_cost", 0.0)),
            move_gross_margin_uplift=float(data.get("move_gross_margin_uplift", data.get("move_gm_uplift", 0.0))),
            move_cost=float(data.get("move_cost", 0.0)),
            exit_cost=float(data.get("exit_cost", 0.0)),
            keep_risk=_bounded(data.get("keep_risk", 0.1)),
            improve_risk=_bounded(data.get("improve_risk", 0.25)),
            move_risk=_bounded(data.get("move_risk", 0.35)),
            exit_risk=_bounded(data.get("exit_risk", 0.2)),
            current_capacity=int(data.get("current_capacity", 1)),
            source_snapshot_ids=tuple(str(v) for v in data.get("source_snapshot_ids", ())),
        )


@dataclass(frozen=True)
class CandidateSiteInput:
    candidate_site_id: str
    expected_gross_margin: float
    open_cost: float
    risk_score: float
    capacity_delta: int = 1
    source_snapshot_ids: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> CandidateSiteInput:
        return cls(
            candidate_site_id=str(data["candidate_site_id"]),
            expected_gross_margin=float(data.get("expected_gross_margin", data.get("expected_gm", 0.0))),
            open_cost=float(data.get("open_cost", data.get("budget_cost", 0.0))),
            risk_score=_bounded(data.get("risk_score", data.get("risk", 0.3))),
            capacity_delta=int(data.get("capacity_delta", 1)),
            source_snapshot_ids=tuple(str(v) for v in data.get("source_snapshot_ids", ())),
        )


@dataclass(frozen=True)
class NetPlanScenario:
    scenario_id: str
    tenant_id: str
    scenario_name: str
    planning_horizon: str
    options_by_entity: dict[str, tuple[ActionOption, ...]]
    constraints: NetPlanConstraints
    status: NetPlanScenarioStatus
    created_at: datetime
    correlation_id: str
    model_version: str = NETPLAN_MODEL_VERSION
    feature_version: str = NETPLAN_FEATURE_VERSION
    solver_version: str = NETPLAN_SOLVER_VERSION
    status_history: tuple[StatusTransition, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        scenario_name: str,
        planning_horizon: str,
        options_by_entity: dict[str, tuple[ActionOption, ...]],
        constraints: NetPlanConstraints,
        correlation_id: str,
        scenario_id: str | None = None,
        created_at: datetime | None = None,
    ) -> NetPlanScenario:
        return cls(
            scenario_id=scenario_id or f"netplan-scenario-{uuid4()}",
            tenant_id=tenant_id,
            scenario_name=scenario_name,
            planning_horizon=planning_horizon,
            options_by_entity=options_by_entity,
            constraints=constraints,
            status=NetPlanScenarioStatus.DRAFT,
            created_at=created_at or datetime.now(UTC),
            correlation_id=correlation_id,
        )

    def transition(
        self,
        to_status: NetPlanScenarioStatus,
        *,
        actor: str,
        reason: str,
        occurred_at: datetime | None = None,
        correlation_id: str | None = None,
    ) -> NetPlanScenario:
        if to_status not in VALID_TRANSITIONS.get(self.status, frozenset()):
            raise InvalidNetPlanTransitionError(
                f"cannot move scenario {self.scenario_id} from {self.status.value} to {to_status.value}"
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
            "scenario_id": self.scenario_id,
            "tenant_id": self.tenant_id,
            "scenario_name": self.scenario_name,
            "planning_horizon": self.planning_horizon,
            "status": self.status.value,
            "constraints": self.constraints.to_dict(),
            "options_by_entity": {
                entity: [option.to_dict() for option in options]
                for entity, options in self.options_by_entity.items()
            },
            "created_at": self.created_at.isoformat(),
            "correlation_id": self.correlation_id,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "solver_version": self.solver_version,
            "status_history": [transition.to_dict() for transition in self.status_history],
        }


@dataclass(frozen=True)
class ScenarioSolveRecord:
    scenario_id: str
    result: NetworkPlanSolveResult
    solved_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "result": self.result.to_dict(),
            "solved_at": self.solved_at.isoformat(),
        }


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    scenario_id: str
    actor_id: str
    decision: str
    reason: str
    decided_at: datetime
    policy_version: str

    @property
    def is_approved(self) -> bool:
        return self.decision == "approved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "scenario_id": self.scenario_id,
            "actor_id": self.actor_id,
            "decision": self.decision,
            "reason": self.reason,
            "decided_at": self.decided_at.isoformat(),
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class ExecutionRecord:
    execution_id: str
    scenario_id: str
    actions: tuple[ActionOption, ...]
    executed_by: str
    executed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "scenario_id": self.scenario_id,
            "actions": [action.to_dict() for action in self.actions],
            "executed_by": self.executed_by,
            "executed_at": self.executed_at.isoformat(),
        }


@dataclass(frozen=True)
class OutcomeRecord:
    outcome_id: str
    scenario_id: str
    expected_gross_margin: float
    actual_gross_margin: float
    variance: float
    variance_pct: float
    observed_at: datetime
    source_snapshot_ids: tuple[str, ...]
    label_registry_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_id": self.outcome_id,
            "scenario_id": self.scenario_id,
            "expected_gross_margin": self.expected_gross_margin,
            "actual_gross_margin": self.actual_gross_margin,
            "variance": self.variance,
            "variance_pct": self.variance_pct,
            "observed_at": self.observed_at.isoformat(),
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "label_registry_payload": self.label_registry_payload,
        }


def build_scenario_options(
    *,
    existing_stores: Sequence[ExistingStoreInput | Mapping[str, Any]] = (),
    candidate_sites: Sequence[CandidateSiteInput | Mapping[str, Any]] = (),
) -> dict[str, tuple[ActionOption, ...]]:
    options: dict[str, tuple[ActionOption, ...]] = {}
    for raw in existing_stores:
        store = raw if isinstance(raw, ExistingStoreInput) else ExistingStoreInput.from_mapping(raw)
        options[store.store_id] = (
            ActionOption(
                entity_id=store.store_id,
                action=NetworkAction.KEEP,
                expected_gross_margin=store.baseline_gross_margin,
                budget_cost=0.0,
                risk_score=store.keep_risk,
                capacity_delta=0,
                source_snapshot_ids=store.source_snapshot_ids,
            ),
            ActionOption(
                entity_id=store.store_id,
                action=NetworkAction.IMPROVE,
                expected_gross_margin=store.baseline_gross_margin + store.improve_gross_margin_uplift,
                budget_cost=store.improve_cost,
                risk_score=store.improve_risk,
                capacity_delta=0,
                source_snapshot_ids=store.source_snapshot_ids,
            ),
            ActionOption(
                entity_id=store.store_id,
                action=NetworkAction.MOVE,
                expected_gross_margin=store.baseline_gross_margin + store.move_gross_margin_uplift,
                budget_cost=store.move_cost,
                risk_score=store.move_risk,
                capacity_delta=0,
                source_snapshot_ids=store.source_snapshot_ids,
            ),
            ActionOption(
                entity_id=store.store_id,
                action=NetworkAction.EXIT,
                expected_gross_margin=0.0,
                budget_cost=store.exit_cost,
                risk_score=store.exit_risk,
                capacity_delta=-store.current_capacity,
                source_snapshot_ids=store.source_snapshot_ids,
            ),
        )

    for raw in candidate_sites:
        site = raw if isinstance(raw, CandidateSiteInput) else CandidateSiteInput.from_mapping(raw)
        options[site.candidate_site_id] = (
            ActionOption(
                entity_id=site.candidate_site_id,
                action=NetworkAction.OPEN,
                expected_gross_margin=site.expected_gross_margin,
                budget_cost=site.open_cost,
                risk_score=site.risk_score,
                capacity_delta=site.capacity_delta,
                source_snapshot_ids=site.source_snapshot_ids,
            ),
            ActionOption(
                entity_id=site.candidate_site_id,
                action=NetworkAction.KEEP,
                expected_gross_margin=0.0,
                budget_cost=0.0,
                risk_score=0.0,
                capacity_delta=0,
                source_snapshot_ids=site.source_snapshot_ids,
                notes=("defer_candidate_site",),
            ),
        )
    return options


def build_outcome_record(
    *,
    scenario_id: str,
    solve_result: NetworkPlanSolveResult,
    actual_gross_margin: float,
    observed_at: datetime | None = None,
    source_snapshot_ids: Sequence[str] = (),
) -> OutcomeRecord:
    expected = solve_result.expected_gross_margin
    variance = round(actual_gross_margin - expected, 4)
    variance_pct = round(variance / expected, 6) if expected else 0.0
    payload = {
        "entity_id": scenario_id,
        "label_type": "netplan_realized_gross_margin",
        "expected_gross_margin": expected,
        "actual_gross_margin": actual_gross_margin,
        "variance": variance,
        "solver_version": solve_result.solver_version,
    }
    return OutcomeRecord(
        outcome_id=f"netplan-outcome-{uuid4()}",
        scenario_id=scenario_id,
        expected_gross_margin=expected,
        actual_gross_margin=actual_gross_margin,
        variance=variance,
        variance_pct=variance_pct,
        observed_at=observed_at or datetime.now(UTC),
        source_snapshot_ids=tuple(str(v) for v in source_snapshot_ids),
        label_registry_payload=payload,
    )


def _bounded(value: Any, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, float(value)))


__all__ = [
    "NETPLAN_FEATURE_VERSION",
    "NETPLAN_MODEL_VERSION",
    "NETPLAN_SOLVER_VERSION",
    "VALID_TRANSITIONS",
    "ApprovalRecord",
    "CandidateSiteInput",
    "ExecutionRecord",
    "ExistingStoreInput",
    "InvalidNetPlanTransitionError",
    "NetPlanScenario",
    "NetPlanScenarioStatus",
    "OutcomeRecord",
    "ScenarioSolveRecord",
    "StatusTransition",
    "build_outcome_record",
    "build_scenario_options",
]
