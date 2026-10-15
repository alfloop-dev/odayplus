"""Canonical pricing/workflow exploration records (SD-AMD-001 section 7)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from modules.priceops.domain.exploration import (
    ExplorationBudgetExceededError,
    ExplorationDecision,
    ExplorationGate,
    ExplorationNotAuthorizedError,
    PriceScope,
)

_GATES = """
SELECT g.*, a.approver_id AS verified_approver
FROM pricing.exploration_gates g
JOIN workflow.approvals a ON a.approval_id = g.approval_id
 AND a.decision_id = g.approval_decision_id AND a.approval_status = 'approved'
 AND a.approved_at IS NOT NULL AND a.approved_at <= clock_timestamp()
JOIN workflow.decisions d ON d.decision_id = a.decision_id
 AND d.policy_version_id = g.decision_policy_version_id
 AND d.decision_status IN ('approved', 'executed')
JOIN workflow.decision_policies p ON p.policy_version_id = g.decision_policy_version_id
 AND p.tenant_id = g.tenant_id AND p.policy_kind = 'price_exploration'
WHERE g.tenant_id = ? AND p.effective_from <= clock_timestamp()
 AND p.approved_at <= clock_timestamp()
 AND (p.effective_to IS NULL OR clock_timestamp() < p.effective_to)
"""


def _time(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


class CanonicalPriceExplorationStore:
    def __init__(self, engine: Any) -> None:
        if getattr(engine, "dialect", "") != "postgresql":
            raise ValueError("Canonical pricing exploration requires PostgreSQL")
        self.engine = engine

    @staticmethod
    def budget_cost(value: float) -> Decimal:
        raw = Decimal(str(value))
        if not raw.is_finite() or raw < 0 or raw > Decimal("9999999999999999.99"):
            raise ExplorationBudgetExceededError("Canonical exploration cost must fit its nonnegative numeric budget")
        return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @contextmanager
    def transaction(self, gate_id: str, tenant_id: str):
        with self.engine.transaction():
            UUID(gate_id)
            UUID(tenant_id)
            self.engine.query_one(
                "SELECT gate_id FROM pricing.exploration_gates WHERE gate_id = ? AND tenant_id = ? FOR UPDATE",
                (gate_id, tenant_id),
            )
            yield

    def get_gate(self, gate_id: str, tenant_id: str | None = None) -> ExplorationGate | None:
        if not tenant_id:
            raise ExplorationNotAuthorizedError("Canonical gate lookup requires its tenant")
        try:
            UUID(gate_id)
            UUID(tenant_id)
        except ValueError:
            return None
        row = self.engine.query_one(
            _GATES + " AND g.gate_id = ? FOR SHARE OF a, d, p",
            (tenant_id, gate_id),
        )
        return self._gate(row) if row else None

    def list_gates(self, tenant_id: str | None = None) -> list[ExplorationGate]:
        if not tenant_id:
            raise ExplorationNotAuthorizedError("Canonical gate enumeration requires its tenant")
        UUID(tenant_id)
        return [self._gate(row) for row in self.engine.query(
            _GATES + " ORDER BY g.created_at, g.gate_id", (tenant_id,),
        )]

    def find_active_gate(self, scope: PriceScope, at: datetime | None = None) -> ExplorationGate | None:
        now = at or datetime.now(UTC)
        return next((gate for gate in self.list_gates(scope.tenant_id)
                     if scope.matches(gate) and gate.is_valid_at(now) and gate.remaining_budget > 0
                     and gate.scope_store_group is None and gate.scope_sku_group is None), None)

    def save_gate(self, gate: ExplorationGate) -> ExplorationGate:
        raise ExplorationNotAuthorizedError("Gate creation/update must use the workflow approval path")

    def revoke_gate(self, *args: Any, **kwargs: Any) -> ExplorationGate:
        raise ExplorationNotAuthorizedError("Gate revocation must use the workflow approval path")

    def validate_items(self, gate: ExplorationGate, items: Any) -> None:
        if gate.scope_store_group is not None or gate.scope_sku_group is not None:
            raise ExplorationNotAuthorizedError("Canonical store/SKU group membership is not bound")
        for item in sorted(items, key=lambda entry: entry.store_id):
            row = self.engine.query_one(
                "SELECT s.tenant_id, s.brand_id, s.is_current FROM core.stores s "
                "JOIN core.brands b ON b.brand_id = s.brand_id AND b.tenant_id = s.tenant_id "
                "WHERE s.store_id = ? FOR SHARE OF s, b", (item.store_id,),
            )
            if (row is None or not row["is_current"] or row["tenant_id"] != gate.tenant_id
                    or row["brand_id"] != item.brand_id
                    or (gate.scope_brand_id is not None and row["brand_id"] != gate.scope_brand_id)):
                raise ExplorationNotAuthorizedError("Exploration item does not match canonical store ownership")

    def record_exploration_decision(self, decision: ExplorationDecision) -> ExplorationDecision:
        UUID(decision.decision_id)
        cost = self.budget_cost(decision.budget_consumed)
        actor = getattr(decision, "created_by", None)
        if not actor or not actor.strip() or actor == "system":
            raise ExplorationNotAuthorizedError("Canonical exploration decision requires its verified actor")
        with self.transaction(decision.gate_id, decision.tenant_id):
            existing = self.engine.query_one(
                "SELECT * FROM pricing.exploration_decisions WHERE decision_id = ?", (decision.decision_id,),
            )
            if existing is not None:
                raise ExplorationNotAuthorizedError("Canonical exploration decisions are immutable; duplicate ID refused")
            gate = self.get_gate(decision.gate_id, decision.tenant_id)
            if (gate is None or gate.revoked_at is not None
                    or not gate.is_valid_at(datetime.now(UTC)) or not gate.is_valid_at(decision.created_at)):
                raise ExplorationNotAuthorizedError("Canonical exploration gate is unavailable, expired or revoked")
            if cost > Decimal(str(gate.remaining_budget)):
                raise ExplorationBudgetExceededError("Canonical exploration budget is exhausted")
            if gate.scope_store_group is not None or gate.scope_sku_group is not None:
                raise ExplorationNotAuthorizedError("Canonical store/SKU group membership is not bound")
            if decision.store_id is None:
                if gate.scope_brand_id is not None:
                    raise ExplorationNotAuthorizedError("Brand-scoped exploration requires its actual store")
            else:
                store = self.engine.query_one(
                    "SELECT s.tenant_id,s.brand_id,s.is_current FROM core.stores s "
                    "JOIN core.brands b ON b.brand_id=s.brand_id AND b.tenant_id=s.tenant_id "
                    "WHERE s.store_id=? FOR SHARE OF s,b", (decision.store_id,),
                )
                if (store is None or not store["is_current"] or store["tenant_id"] != gate.tenant_id
                        or (gate.scope_brand_id is not None and store["brand_id"] != gate.scope_brand_id)):
                    raise ExplorationNotAuthorizedError("Canonical exploration decision has different store ownership")
            self.engine.execute(
                "INSERT INTO workflow.decisions(decision_id,decision_type,entity_type,entity_id,"
                "recommendation,decision_status,policy_version_id,created_by,created_at) "
                "VALUES (?, 'price', 'pricing_exploration', ?, ?, 'proposed', ?, ?, ?)",
                (decision.decision_id, decision.gate_id, "Gate-authorized exploration candidate",
                 gate.decision_policy_version_id, actor, decision.created_at.isoformat()),
            )
            # The canonical INSERT trigger owns the budget increment. No
            # application UPDATE of budget_consumed or document mirror occurs.
            self.engine.execute(
                "INSERT INTO pricing.exploration_decisions(decision_id,gate_id,tenant_id,sku_id,store_id,"
                "baseline_price,explored_price,budget_consumed,algorithm,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (decision.decision_id, decision.gate_id, decision.tenant_id, decision.sku_id, decision.store_id,
                 decision.baseline_price, decision.explored_price, str(cost),
                 decision.algorithm, decision.created_at.isoformat()),
            )
            row = self.engine.query_one(
                "SELECT e.*, d.created_by FROM pricing.exploration_decisions e "
                "JOIN workflow.decisions d ON d.decision_id = e.decision_id WHERE e.decision_id = ?",
                (decision.decision_id,),
            )
            return self._decision(row)

    def list_exploration_decisions(self, gate_id: str | None = None) -> list[ExplorationDecision]:
        if gate_id is None:
            raise ExplorationNotAuthorizedError("Canonical exploration decisions require a gate scope")
        UUID(gate_id)
        return [self._decision(row) for row in self.engine.query(
            "SELECT e.*, d.created_by FROM pricing.exploration_decisions e "
            "JOIN workflow.decisions d ON d.decision_id = e.decision_id "
            "WHERE e.gate_id = ? ORDER BY e.created_at, e.decision_id", (gate_id,),
        )]

    @staticmethod
    def _gate(row: Any) -> ExplorationGate:
        return ExplorationGate(
            gate_id=row["gate_id"], tenant_id=row["tenant_id"],
            budget_limit=float(row["budget_limit"]), budget_consumed=float(row["budget_consumed"]),
            effective_from=_time(row["effective_from"]), effective_to=_time(row["effective_to"]),
            approved_by=row["verified_approver"], approval_decision_id=row["approval_decision_id"],
            approval_id=row["approval_id"], rollback_condition=row["rollback_condition"],
            decision_policy_version_id=row["decision_policy_version_id"],
            scope_brand_id=row["scope_brand_id"], scope_store_group=row["scope_store_group"],
            scope_sku_group=row["scope_sku_group"], revoked_at=_time(row["revoked_at"]) if row["revoked_at"] else None,
            created_at=_time(row["created_at"]),
        )

    @staticmethod
    def _decision(row: Any) -> ExplorationDecision:
        return ExplorationDecision(
            decision_id=row["decision_id"], gate_id=row["gate_id"], tenant_id=row["tenant_id"],
            sku_id=row["sku_id"], store_id=row["store_id"], baseline_price=float(row["baseline_price"]),
            explored_price=float(row["explored_price"]), budget_consumed=float(row["budget_consumed"]),
            algorithm=row["algorithm"], created_at=_time(row["created_at"]), created_by=row["created_by"],
        )
