"""Read-only relationships for PriceOps-derived Growth and Govern projections.

The HTTP composition supplies actual plans checked against its verified
principal and canonical masters. No scope claim is inferred from UI text,
persona, synthetic metadata or a client-provided source ID. These projections
are fresh read copies; they must never replace persisted aggregate state.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


class ScopedPriceOpsReadRepository:
    """Keep one native owner while restricting its canonical read parents."""

    def __init__(self, owner: Any, plans: list[Any]) -> None:
        self._owner = owner
        self._plans = {plan.plan_id: plan for plan in plans}

    def list_plans(self) -> list[Any]:
        return list(self._plans.values())

    def get_optimization(self, plan_id: str) -> Any:
        return self._owner.get_optimization(plan_id) if plan_id in self._plans else None

    def get_evaluation(self, plan_id: str) -> Any:
        return self._owner.get_evaluation(plan_id) if plan_id in self._plans else None

    def list_approvals(self, plan_id: str) -> list[Any]:
        return self._owner.list_approvals(plan_id) if plan_id in self._plans else []


def project_growth_read_state(state: dict[str, Any] | None, plans: list[Any]) -> dict[str, Any] | None:
    if state is None:
        return None
    result = deepcopy(state)
    segment_ids = {f"priceops:{plan.plan_id}:{item.item_id}" for plan in plans for item in plan.items}
    recommendation_ids = {f"priceops-rec:{plan.plan_id}:{item.item_id}" for plan in plans for item in plan.items}
    result["actions"] = [row for row in result.get("actions", []) if row.get("segmentId") in segment_ids
                         and (not row.get("sourceRecommendationId") or row["sourceRecommendationId"] in recommendation_ids)]
    action_ids = {row["id"] for row in result["actions"]}
    result["approvals"] = [row for row in result.get("approvals", []) if row.get("ref") in action_ids]
    result["decisions"] = [row for row in result.get("decisions", []) if row.get("ref") in action_ids]
    result["auditEvents"] = [row for row in result.get("auditEvents", [])
                              if (row.get("metadata") or {}).get("actionId") in action_ids]
    return result


def project_governance_read_state(state: dict[str, Any] | None, plans: list[Any],
                                  growth_state: dict[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    result = deepcopy(state)
    plan_ids = {plan.plan_id for plan in plans}
    growth = growth_state or {}
    action_ids = {row["id"] for row in growth.get("actions", [])}
    approval_ids = {row["id"] for row in growth.get("approvals", [])}

    def visible(row, kind):
        if row.get("module") == "PriceOps":
            identity = row.get("approvalId") if kind == "decisions" else row.get("entityRef", row.get("id"))
            return identity in plan_ids
        if row.get("module") == "Growth":
            identity = row.get("approvalId") if kind == "decisions" else row.get("entityRef")
            return identity in (approval_ids if kind == "decisions" else action_ids)
        return True

    for kind in ("approvals", "decisions", "auditRows", "statusBoard", "evidencePackages"):
        value = result.get(kind, [])
        if isinstance(value, list):
            result[kind] = [row for row in value if visible(row, kind)]
    return result
