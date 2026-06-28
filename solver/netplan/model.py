"""Pure NetPlan optimization model primitives."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

NETPLAN_POLICY_VERSION = "netplan-network-policy-v1"


class NetworkAction(StrEnum):
    OPEN = "OPEN"
    KEEP = "KEEP"
    IMPROVE = "IMPROVE"
    MOVE = "MOVE"
    EXIT = "EXIT"


@dataclass(frozen=True)
class ActionOption:
    entity_id: str
    action: NetworkAction
    expected_gross_margin: float
    budget_cost: float
    risk_score: float
    capacity_delta: int = 0
    source_snapshot_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ActionOption:
        return cls(
            entity_id=str(data["entity_id"]),
            action=NetworkAction(str(data["action"]).upper()),
            expected_gross_margin=float(data.get("expected_gross_margin", data.get("expected_gm", 0.0))),
            budget_cost=float(data.get("budget_cost", data.get("cost", 0.0))),
            risk_score=_bounded(data.get("risk_score", data.get("risk", 0.0))),
            capacity_delta=int(data.get("capacity_delta", 0)),
            source_snapshot_ids=tuple(str(v) for v in data.get("source_snapshot_ids", ())),
            notes=tuple(str(v) for v in data.get("notes", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "action": self.action.value,
            "expected_gross_margin": self.expected_gross_margin,
            "budget_cost": self.budget_cost,
            "risk_score": self.risk_score,
            "capacity_delta": self.capacity_delta,
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class NetPlanConstraints:
    max_budget: float
    min_expected_gross_margin: float | None = None
    min_capacity_delta: int | None = None
    max_average_risk: float | None = None
    min_action_counts: Mapping[NetworkAction, int] = field(default_factory=dict)
    max_action_counts: Mapping[NetworkAction, int] = field(default_factory=dict)
    policy_version: str = NETPLAN_POLICY_VERSION

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> NetPlanConstraints:
        return cls(
            max_budget=float(data["max_budget"]),
            min_expected_gross_margin=_optional_float(data.get("min_expected_gross_margin", data.get("min_expected_gm"))),
            min_capacity_delta=_optional_int(data.get("min_capacity_delta")),
            max_average_risk=_optional_float(data.get("max_average_risk", data.get("max_risk"))),
            min_action_counts=_action_count_mapping(data.get("min_action_counts", {})),
            max_action_counts=_action_count_mapping(data.get("max_action_counts", {})),
            policy_version=str(data.get("policy_version", NETPLAN_POLICY_VERSION)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_budget": self.max_budget,
            "min_expected_gross_margin": self.min_expected_gross_margin,
            "min_capacity_delta": self.min_capacity_delta,
            "max_average_risk": self.max_average_risk,
            "min_action_counts": {k.value: v for k, v in self.min_action_counts.items()},
            "max_action_counts": {k.value: v for k, v in self.max_action_counts.items()},
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class InfeasibilityDiagnosis:
    violated_constraint: str
    affected_stores: tuple[str, ...]
    required_relaxation: str
    business_impact: str
    suggested_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "violated_constraint": self.violated_constraint,
            "affected_stores": list(self.affected_stores),
            "required_relaxation": self.required_relaxation,
            "business_impact": self.business_impact,
            "suggested_action": self.suggested_action,
        }


def _bounded(value: Any, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    numeric = float(value)
    return max(minimum, min(maximum, numeric))


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _action_count_mapping(data: Mapping[str, Any]) -> dict[NetworkAction, int]:
    return {NetworkAction(str(action).upper()): int(count) for action, count in data.items()}


__all__ = [
    "NETPLAN_POLICY_VERSION",
    "ActionOption",
    "InfeasibilityDiagnosis",
    "NetPlanConstraints",
    "NetworkAction",
]
