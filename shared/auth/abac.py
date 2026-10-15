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
from enum import Enum
from typing import Any

from .identity import DataClassification, Principal, RiskLevel, Role
from .rbac import Action


class ResourceTarget(Enum):
    """Trusted server descriptor intent, never a request/attributes flag.

    ABSTRACT_CAPABILITY checks only type-level permissions and constraints. It
    grants no object/list/business execution authority or global-record access.
    Callers must not deserialize this declaration from untrusted input.
    """

    TENANT_BOUND = "tenant_bound"
    ABSTRACT_CAPABILITY = "abstract_capability"


@dataclass(frozen=True)
class ResourceDescriptor:
    """Server-owned target evidence; ordinary targets are always tenant-bound.

    Missing tenant/id never implies an abstract capability. Explicit abstract
    declarations cannot carry object identity or tenant/owner evidence.
    """

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
    target: ResourceTarget = ResourceTarget.TENANT_BOUND


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


# A policy may veto, abstain, or add obligations. An allow never ends the chain.
AbacPolicy = Callable[[AccessRequest], Decision | None]


# --- default policies (SD-09 §5 clauses) -----------------------------------


def require_authenticated(request: AccessRequest) -> Decision | None:
    if not request.principal.authenticated:
        return Decision.deny("principal not authenticated", policy_id="authenticated")
    return None


def operator_viewer_tenant_isolation(request: AccessRequest) -> Decision | None:
    """The opt-in read role never authorizes tenant-less or foreign objects."""

    principal = request.principal
    if principal.has_role(Role.OPERATOR_VIEWER) and (
        not principal.tenant_id or request.resource.tenant_id != principal.tenant_id
    ):
        return Decision.deny(
            "operator viewer requires matching tenant evidence",
            policy_id="operator.tenant_isolation",
        )
    return None


def target_tenant_isolation(request: AccessRequest) -> Decision:
    """Mandatory owning boundary, including when callers supply custom policies.

    Reuse the registered-waiver guard without its deny-only ABAC adapter, which
    discards successful waiver obligations. Validate typed evidence first: the
    guard's string coercion is not authority for malformed/missing ownership.
    """
    from .tenant import TenantAccessWaiver, check_tenant_isolation, default_waiver_registry

    resource = request.resource
    if resource.target is ResourceTarget.ABSTRACT_CAPABILITY:
        if any(
            value is not None
            for value in (resource.tenant_id, resource.resource_id, resource.owner_subject_id)
        ):
            return Decision.deny(
                "abstract capability cannot carry tenant or object ownership",
                policy_id="tenant_isolation",
            )
        return Decision.allow("explicit abstract capability")
    if resource.target is not ResourceTarget.TENANT_BOUND:
        return Decision.deny("invalid resource target declaration", policy_id="tenant_isolation")
    for value in (request.principal.tenant_id, resource.tenant_id):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            return Decision.deny(
                "tenant-bound target requires valid principal and owning resource tenant",
                policy_id="tenant_isolation",
            )
    waiver = request.environment.attributes.get("tenant_waiver") or resource.attributes.get(
        "tenant_waiver"
    )
    if isinstance(waiver, TenantAccessWaiver) and (
        default_waiver_registry().get(waiver.waiver_id) is not waiver
    ):
        return Decision.deny(
            "waiver object is not the registered record", policy_id="tenant_isolation"
        )
    decision = check_tenant_isolation(
        request.principal,
        resource.tenant_id,
        resource_type=resource.type,
        resource_id=resource.resource_id,
        waiver=waiver,
    )
    if not decision.allowed:
        return decision
    return Decision.allow(decision.reason, obligations=decision.obligations | frozenset({"audit"}))


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
        return Decision.deny("franchisee restricted to own store", policy_id="franchisee_isolation")
    return None


# Default deny-first policy chain. Order is not significant for correctness
# (any single deny wins) but is arranged cheap-checks-first for readability.
DEFAULT_POLICIES: tuple[AbacPolicy, ...] = (
    require_authenticated,
    operator_viewer_tenant_isolation,
    scope_containment,
    data_classification_visibility,
    franchisee_isolation,
)


def evaluate_abac(
    request: AccessRequest, policies: Sequence[AbacPolicy] = DEFAULT_POLICIES
) -> Decision:
    """Mandatory tenant boundary, then deny-first policies; accumulate obligations.

    Empty/custom chains and early policy allows cannot waive tenant enforcement
    or OPERATOR_VIEWER's stricter evidence requirement.
    """
    for mandatory in (require_authenticated, operator_viewer_tenant_isolation):
        decision = mandatory(request)
        if decision is not None and not decision.allowed:
            return decision
    tenant = target_tenant_isolation(request)
    if not tenant.allowed:
        return tenant
    obligations = set(tenant.obligations)
    for policy in policies:
        decision = policy(request)
        if decision is not None:
            if not decision.allowed:
                return decision
            obligations.update(decision.obligations)
    return Decision.allow("abac policies passed", obligations=frozenset(obligations))
