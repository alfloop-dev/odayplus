"""Attribute-based access control (ABAC).

Source baseline: ODP-SD-09 §5 authorization model and §5.1 ABAC conditions,
ODP-SA-04 §4 (scope) and §7 (high-risk actions).

The SD-09 §5 rule is:

    Allow if:
      authenticated
      AND role permits action          (handled by shared.auth.rbac)
      AND scope permits resource
      AND workflow state permits action
      AND policy constraints pass
      AND data classification permits visibility

This module implements the non-RBAC clauses as a deny-by-default chain of
policies. Each policy inspects an :class:`AccessRequest` and either returns a
denying :class:`Decision` (veto) or ``None`` to abstain. The orchestration in
:mod:`shared.auth.engine` combines RBAC and ABAC and wires the audit hook.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .identity import DataClassification, Principal, RiskLevel, Role
from .rbac import Action


@dataclass(frozen=True)
class ResourceDescriptor:
    """The object an action targets, with its ABAC-relevant attributes."""

    type: str
    resource_id: str | None = None
    tenant_id: str | None = None
    brand_id: str | None = None
    region_id: str | None = None
    store_id: str | None = None
    module: str | None = None
    owner_subject_id: str | None = None
    data_classification: DataClassification = DataClassification.CONFIDENTIAL
    workflow_state: str | None = None
    risk_level: RiskLevel = RiskLevel.LOW
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Environment:
    """Request-time context not owned by the principal or resource."""

    source_ip: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AccessRequest:
    principal: Principal
    action: Action
    resource: ResourceDescriptor
    environment: Environment = field(default_factory=Environment)


@dataclass(frozen=True)
class Decision:
    """Outcome of an authorization evaluation."""

    allowed: bool
    reason: str
    policy_id: str | None = None
    obligations: frozenset[str] = frozenset()

    @classmethod
    def allow(
        cls, reason: str = "allowed", *, obligations: frozenset[str] = frozenset()
    ) -> Decision:
        return cls(allowed=True, reason=reason, obligations=obligations)

    @classmethod
    def deny(cls, reason: str, *, policy_id: str | None = None) -> Decision:
        return cls(allowed=False, reason=reason, policy_id=policy_id)


# A policy returns a denying Decision to veto, or None to abstain.
AbacPolicy = Callable[[AccessRequest], Decision | None]


# --- default policies (SD-09 §5 clauses) -----------------------------------

def require_authenticated(request: AccessRequest) -> Decision | None:
    if not request.principal.authenticated:
        return Decision.deny("principal not authenticated", policy_id="authenticated")
    return None


def tenant_isolation(request: AccessRequest) -> Decision | None:
    """Principal home tenant must match the resource tenant (SD-09 §10 threat:
    cross-tenant access)."""

    resource_tenant = request.resource.tenant_id
    if resource_tenant is None:
        return None
    principal_tenant = request.principal.tenant_id
    if principal_tenant is not None and principal_tenant != resource_tenant:
        return Decision.deny(
            f"tenant {principal_tenant!r} may not access tenant {resource_tenant!r}",
            policy_id="tenant_isolation",
        )
    return None


def scope_containment(request: AccessRequest) -> Decision | None:
    """Brand/region/store scope must contain the resource (SD-09 §4.2).

    ODP-AC-AUTH-002 (cross-region) and ODP-AC-AUTH-003 (franchisee store scope)
    rely on this clause.
    """

    scope = request.principal.scope
    res = request.resource
    if not scope.permits_brand(res.brand_id):
        return Decision.deny("brand outside principal scope", policy_id="scope.brand")
    if not scope.permits_region(res.region_id):
        return Decision.deny("region outside principal scope", policy_id="scope.region")
    if not scope.permits_store(res.store_id):
        return Decision.deny("store outside principal scope", policy_id="scope.store")
    if not scope.permits_module(res.module):
        return Decision.deny("module outside principal scope", policy_id="scope.module")
    return None


def data_classification_visibility(request: AccessRequest) -> Decision | None:
    """Principal clearance must cover the resource classification (SD-09 §6)."""

    if not request.principal.scope.permits_classification(request.resource.data_classification):
        return Decision.deny(
            "data classification exceeds principal clearance",
            policy_id="data_classification",
        )
    return None


def franchisee_isolation(request: AccessRequest) -> Decision | None:
    """Franchisees may only ever touch their own store (SA-04 §8)."""

    principal = request.principal
    if not principal.has_role(Role.FRANCHISEE):
        return None
    store_id = request.resource.store_id
    if store_id is not None and store_id not in principal.scope.store_ids:
        return Decision.deny(
            "franchisee restricted to own store", policy_id="franchisee_isolation"
        )
    return None


# Default deny-first policy chain. Order is not significant for correctness
# (any single deny wins) but is arranged cheap-checks-first for readability.
DEFAULT_POLICIES: tuple[AbacPolicy, ...] = (
    require_authenticated,
    tenant_isolation,
    scope_containment,
    data_classification_visibility,
    franchisee_isolation,
)


def evaluate_abac(
    request: AccessRequest, policies: Sequence[AbacPolicy] = DEFAULT_POLICIES
) -> Decision:
    """Run the policy chain; first deny wins, otherwise allow."""

    for policy in policies:
        decision = policy(request)
        if decision is not None and not decision.allowed:
            return decision
    return Decision.allow("abac policies passed")
