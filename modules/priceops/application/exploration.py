"""PriceOps Exploration application service and authorization gates (ODP-FR-PRICE-006).

Enforces gate validation, budget tracking, and candidate generation via the Bandit framework.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from modules.priceops.domain.exploration import (
    ExplorationDecision,
    ExplorationGate,
    ExplorationGrant,
    ExplorationNotAuthorizedError,
    PriceScope,
    validate_gate_scope,
)
from modules.priceops.domain.pricing import PricingPlanItem
from modules.priceops.infrastructure.repositories import InMemoryPriceOpsRepository
from solver.pricing.bandit import (
    BanditAlgorithm,
    BanditCandidate,
    explore_price_candidate,
)


def authorize_exploration(
    scope: PriceScope,
    *,
    at: datetime | None = None,
    repository: InMemoryPriceOpsRepository,
) -> ExplorationGrant:
    """Resolve and authorize exploration for a given scope.

    Fails closed: Any failure to resolve an active, valid, funded Gate
    raises ExplorationNotAuthorizedError.
    """
    now = at or datetime.now(UTC)
    gate = repository.find_active_gate(scope, at=now)
    if gate is None:
        raise ExplorationNotAuthorizedError(
            f"No active exploration gate found for scope {scope.to_dict()} at {now.isoformat()}"
        )
    if gate.revoked_at is not None:
        raise ExplorationNotAuthorizedError(
            f"Exploration gate {gate.gate_id} was revoked at {gate.revoked_at.isoformat()}"
        )
    if not gate.is_valid_at(now):
        raise ExplorationNotAuthorizedError(
            f"Exploration gate {gate.gate_id} is outside validity window [{gate.effective_from.isoformat()}, {gate.effective_to.isoformat()}) at {now.isoformat()}"
        )
    if gate.remaining_budget <= 0:
        raise ExplorationNotAuthorizedError(
            f"Exploration gate {gate.gate_id} budget is exhausted: consumed {gate.budget_consumed} of limit {gate.budget_limit}"
        )

    return gate.to_grant()


class StandardBanditPriceExplorer:
    """Default implementation of BanditPriceExplorer."""

    def generate_candidates(
        self,
        scope: PriceScope,
        grant: ExplorationGrant,
        items: Sequence[PricingPlanItem],
        *,
        algorithm: BanditAlgorithm | str = BanditAlgorithm.THOMPSON_SAMPLING,
        history: Sequence[tuple[float, float]] | None = None,
        seed: int | None = None,
    ) -> list[BanditCandidate]:
        """Generate bandit exploration candidates within grant scope and budget."""
        if not scope.matches(grant):
            raise ExplorationNotAuthorizedError(
                f"requested scope {scope.to_dict()} does not match exploration gate {grant.gate_id}"
            )
        validate_gate_scope(grant, tenant_id=scope.tenant_id, items=items)
        # A gate decision must be replayable even when a caller omits the
        # optional seed.  The interactive/offline API remains convenient, but
        # its decision contract is never backed by ambient process entropy.
        effective_seed = seed if seed is not None else 0
        candidates: list[BanditCandidate] = []
        for item in items:
            candidate = explore_price_candidate(
                constraints=item.constraints,
                baseline_demand=item.baseline_demand,
                elasticity=item.elasticity.elasticity_value,
                confidence=item.elasticity.confidence,
                gate_id=grant.gate_id,
                sku_id=item.item_id,
                store_id=item.store_id,
                algorithm=algorithm,
                history=history,
                seed=effective_seed,
            )
            candidates.append(candidate)
        return candidates


class ExplorationService:
    """Service for managing pricing exploration gates, decisions, and candidates."""

    def __init__(self, repository: InMemoryPriceOpsRepository) -> None:
        self.repository = repository
        self.explorer = StandardBanditPriceExplorer()

    def register_gate(
        self,
        *,
        tenant_id: str,
        budget_limit: float,
        effective_from: datetime,
        effective_to: datetime,
        approved_by: str,
        approval_decision_id: str,
        approval_id: str,
        rollback_condition: str,
        decision_policy_version_id: str,
        scope_brand_id: str | None = None,
        scope_store_group: str | None = None,
        scope_sku_group: str | None = None,
        gate_id: str | None = None,
    ) -> ExplorationGate:
        """Register a new exploration gate after workflow approval."""
        if effective_to <= effective_from:
            raise ValueError("effective_to must be after effective_from")
        if budget_limit <= 0:
            raise ValueError("budget_limit must be greater than 0")

        gate = ExplorationGate(
            gate_id=gate_id or f"gate-{uuid4()}",
            tenant_id=tenant_id,
            budget_limit=budget_limit,
            effective_from=effective_from,
            effective_to=effective_to,
            approved_by=approved_by,
            approval_decision_id=approval_decision_id,
            approval_id=approval_id,
            rollback_condition=rollback_condition,
            decision_policy_version_id=decision_policy_version_id,
            scope_brand_id=scope_brand_id,
            scope_store_group=scope_store_group,
            scope_sku_group=scope_sku_group,
            budget_consumed=0.0,
            revoked_at=None,
        )
        return self.repository.save_gate(gate)

    def get_gate(self, gate_id: str, tenant_id: str | None = None) -> ExplorationGate | None:
        return self.repository.get_gate(gate_id, tenant_id=tenant_id)

    def list_gates(self, tenant_id: str | None = None) -> list[ExplorationGate]:
        return self.repository.list_gates(tenant_id=tenant_id)

    def get_active_grant(
        self, scope: PriceScope, at: datetime | None = None
    ) -> ExplorationGrant:
        return authorize_exploration(scope, at=at, repository=self.repository)

    def revoke_gate(
        self, gate_id: str, tenant_id: str, revoked_at: datetime | None = None
    ) -> ExplorationGate:
        return self.repository.revoke_gate(gate_id, tenant_id=tenant_id, revoked_at=revoked_at)

    def generate_candidates(
        self,
        scope: PriceScope,
        items: Sequence[PricingPlanItem],
        *,
        algorithm: BanditAlgorithm | str = BanditAlgorithm.THOMPSON_SAMPLING,
        history: Sequence[tuple[float, float]] | None = None,
        seed: int | None = None,
        at: datetime | None = None,
    ) -> list[BanditCandidate]:
        """Generate exploration candidates. Fails closed if Gate is not authorized."""
        grant = self.get_active_grant(scope, at=at)
        return self.explorer.generate_candidates(
            scope=scope,
            grant=grant,
            items=items,
            algorithm=algorithm,
            history=history,
            seed=seed,
        )

    def record_decision(
        self,
        *,
        decision_id: str,
        gate_id: str,
        tenant_id: str,
        sku_id: str,
        store_id: str | None,
        baseline_price: float,
        explored_price: float,
        budget_consumed: float,
        algorithm: str,
        created_at: datetime | None = None,
    ) -> ExplorationDecision:
        decision = ExplorationDecision(
            decision_id=decision_id,
            gate_id=gate_id,
            tenant_id=tenant_id,
            sku_id=sku_id,
            store_id=store_id,
            baseline_price=baseline_price,
            explored_price=explored_price,
            budget_consumed=budget_consumed,
            algorithm=algorithm,
            created_at=created_at or datetime.now(UTC),
        )
        return self.repository.record_exploration_decision(decision)


__all__ = [
    "ExplorationService",
    "StandardBanditPriceExplorer",
    "authorize_exploration",
]
