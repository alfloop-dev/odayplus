"""PriceOps Exploration domain model: Gate authorization, Bandit candidates,
and exploration decision records (ODP-FR-PRICE-006 / ODP-SD-AMD-001 §7).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from solver.pricing.bandit import BanditCandidate


class ExplorationNotAuthorizedError(RuntimeError):
    """Raised when exploration is requested without an active, valid Gate authorization."""


class ExplorationBudgetExceededError(RuntimeError):
    """Raised when exploration budget is exhausted."""


class ExplorationGateExpiredError(RuntimeError):
    """Raised when exploration gate validity window has passed."""


class ExplorationGateRevokedError(RuntimeError):
    """Raised when exploration gate has been revoked."""


@dataclass(frozen=True)
class PriceScope:
    """Scope defining where a pricing decision or gate applies."""

    tenant_id: str
    brand_id: str | None = None
    store_group: str | None = None
    sku_group: str | None = None
    store_id: str | None = None
    sku_id: str | None = None

    def matches(self, gate: ExplorationGate | ExplorationGrant) -> bool:
        """Check if this scope matches the authorized gate scope."""
        if self.tenant_id != gate.tenant_id:
            return False
        if gate.scope_brand_id is not None and self.brand_id != gate.scope_brand_id:
            return False
        if gate.scope_store_group is not None and self.store_group != gate.scope_store_group:
            return False
        if gate.scope_sku_group is not None and self.sku_group != gate.scope_sku_group:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "brand_id": self.brand_id,
            "store_group": self.store_group,
            "sku_group": self.sku_group,
            "store_id": self.store_id,
            "sku_id": self.sku_id,
        }


@dataclass(frozen=True)
class ExplorationGrant:
    """An active exploration authorization granted by a valid Gate."""

    gate_id: str
    tenant_id: str
    policy_version_id: str
    effective_from: datetime
    effective_to: datetime
    budget_limit: float
    budget_consumed: float
    budget_remaining: float
    rollback_condition: str
    approved_by: str
    approval_decision_id: str
    approval_id: str
    scope_brand_id: str | None = None
    scope_store_group: str | None = None
    scope_sku_group: str | None = None
    revoked_at: datetime | None = None

    def is_active(self, at: datetime | None = None) -> bool:
        now = at or datetime.now(UTC)
        if self.revoked_at is not None:
            return False
        if not (self.effective_from <= now < self.effective_to):
            return False
        if self.budget_remaining <= 0:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "tenant_id": self.tenant_id,
            "policy_version_id": self.policy_version_id,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat(),
            "budget_limit": self.budget_limit,
            "budget_consumed": self.budget_consumed,
            "budget_remaining": self.budget_remaining,
            "rollback_condition": self.rollback_condition,
            "approved_by": self.approved_by,
            "approval_decision_id": self.approval_decision_id,
            "approval_id": self.approval_id,
            "scope_brand_id": self.scope_brand_id,
            "scope_store_group": self.scope_store_group,
            "scope_sku_group": self.scope_sku_group,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
        }


@dataclass(frozen=True)
class ExplorationGate:
    """Domain model representing a persistent pricing exploration Gate."""

    gate_id: str
    tenant_id: str
    budget_limit: float
    effective_from: datetime
    effective_to: datetime
    approved_by: str
    approval_decision_id: str
    approval_id: str
    rollback_condition: str
    decision_policy_version_id: str
    scope_brand_id: str | None = None
    scope_store_group: str | None = None
    scope_sku_group: str | None = None
    budget_consumed: float = 0.0
    revoked_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def remaining_budget(self) -> float:
        return max(0.0, round(self.budget_limit - self.budget_consumed, 4))

    def is_valid_at(self, at: datetime) -> bool:
        if self.revoked_at is not None and at >= self.revoked_at:
            return False
        return self.effective_from <= at < self.effective_to

    def has_budget(self, required: float = 0.0) -> bool:
        return (self.budget_consumed + required) <= self.budget_limit + 1e-6

    def to_grant(self) -> ExplorationGrant:
        return ExplorationGrant(
            gate_id=self.gate_id,
            tenant_id=self.tenant_id,
            policy_version_id=self.decision_policy_version_id,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
            budget_limit=self.budget_limit,
            budget_consumed=self.budget_consumed,
            budget_remaining=self.remaining_budget,
            rollback_condition=self.rollback_condition,
            approved_by=self.approved_by,
            approval_decision_id=self.approval_decision_id,
            approval_id=self.approval_id,
            scope_brand_id=self.scope_brand_id,
            scope_store_group=self.scope_store_group,
            scope_sku_group=self.scope_sku_group,
            revoked_at=self.revoked_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "tenant_id": self.tenant_id,
            "scope_brand_id": self.scope_brand_id,
            "scope_store_group": self.scope_store_group,
            "scope_sku_group": self.scope_sku_group,
            "budget_limit": self.budget_limit,
            "budget_consumed": self.budget_consumed,
            "remaining_budget": self.remaining_budget,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat(),
            "approved_by": self.approved_by,
            "approval_decision_id": self.approval_decision_id,
            "approval_id": self.approval_id,
            "rollback_condition": self.rollback_condition,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "decision_policy_version_id": self.decision_policy_version_id,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class ExplorationDecision:
    """A recorded pricing exploration decision linking to an authorized Gate."""

    decision_id: str
    gate_id: str
    tenant_id: str
    sku_id: str
    store_id: str | None
    baseline_price: float
    explored_price: float
    budget_consumed: float
    algorithm: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "gate_id": self.gate_id,
            "tenant_id": self.tenant_id,
            "sku_id": self.sku_id,
            "store_id": self.store_id,
            "baseline_price": self.baseline_price,
            "explored_price": self.explored_price,
            "budget_consumed": self.budget_consumed,
            "algorithm": self.algorithm,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class ActivationReceipt:
    """Auditable activation receipt binding policy version, actor, experiment, guardrails and rollback."""

    receipt_id: str
    plan_id: str
    policy_version: str
    actor: str
    exploration_enabled: bool
    experiment_id: str | None
    guardrails: dict[str, Any]
    rollback_target: str
    activated_at: datetime
    execution_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "plan_id": self.plan_id,
            "policy_version": self.policy_version,
            "actor": self.actor,
            "exploration_enabled": self.exploration_enabled,
            "experiment_id": self.experiment_id,
            "guardrails": dict(self.guardrails),
            "rollback_target": self.rollback_target,
            "activated_at": self.activated_at.isoformat(),
            "execution_id": self.execution_id,
        }


class BanditPriceExplorer(Protocol):
    """Protocol for generating bandit price exploration candidates."""

    def generate_candidates(
        self,
        scope: PriceScope,
        grant: ExplorationGrant,
        items: Sequence[Any],
        *,
        algorithm: str = "THOMPSON_SAMPLING",
        history: Sequence[tuple[float, float]] | None = None,
        seed: int | None = None,
    ) -> Sequence[BanditCandidate]:
        """Generate bandit exploration candidates within the grant budget and hard constraints."""
        ...


__all__ = [
    "ActivationReceipt",
    "BanditPriceExplorer",
    "ExplorationBudgetExceededError",
    "ExplorationDecision",
    "ExplorationGate",
    "ExplorationGateExpiredError",
    "ExplorationGateRevokedError",
    "ExplorationGrant",
    "ExplorationNotAuthorizedError",
    "PriceScope",
]
