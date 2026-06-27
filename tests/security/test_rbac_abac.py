"""RBAC + ABAC + authorization-engine tests.

Covers ODP-SA-04 §9 acceptance ids and ODP-SD-09 §12:
- ODP-AC-AUTH-001 unauthenticated denied
- ODP-AC-AUTH-002 cross-region denied
- ODP-AC-AUTH-003 franchisee store scope
- ODP-AC-AUTH-004 high-risk action needs approval role + policy hook
- ODP-AC-AUTH-005 permission failures write a security audit event
"""

from __future__ import annotations

from datetime import date

from shared.audit import AuditOutcome, InMemoryAuditLog
from shared.auth import (
    ANONYMOUS,
    AccessRequest,
    Action,
    AuthorizationEngine,
    DataClassification,
    Environment,
    Principal,
    ResourceDescriptor,
    Role,
    Scope,
    default_registry,
    high_risk_flag_key,
    permissions_for,
    rbac_allows,
)

ON = date(2026, 6, 27)


def make_engine() -> tuple[AuthorizationEngine, InMemoryAuditLog]:
    log = InMemoryAuditLog()
    return AuthorizationEngine(audit_log=log, flags=default_registry()), log


def events_with_outcome(log: InMemoryAuditLog, outcome: AuditOutcome) -> list:
    return [e for e in log.list_events() if e.outcome == outcome]


def principal(role: Role, **scope_kwargs) -> Principal:
    return Principal(
        subject_id=f"user-{role.value}",
        roles=frozenset({role}),
        scope=Scope(**scope_kwargs),
    )


# --- RBAC -------------------------------------------------------------------

def test_rbac_grants_role_permission() -> None:
    p = principal(Role.PRICING_MANAGER)
    assert rbac_allows(p, "priceops", Action.APPROVE)


def test_rbac_denies_action_outside_role() -> None:
    p = principal(Role.PRICING_MANAGER)
    assert not rbac_allows(p, "netplan", Action.APPROVE)


def test_rbac_denies_unauthenticated() -> None:
    assert not rbac_allows(ANONYMOUS, "priceops", Action.VIEW)


def test_permissions_for_unions_roles() -> None:
    perms = permissions_for(frozenset({Role.PRICING_MANAGER, Role.MARKETING_MANAGER}))
    resources = {p.resource for p in perms}
    assert {"priceops", "adlift"} <= resources


# --- ABAC -------------------------------------------------------------------

def test_tenant_isolation_blocks_other_tenant() -> None:
    engine, sink = make_engine()
    p = principal(Role.OPERATIONS_MANAGER, tenant_id="tenant-a")
    res = ResourceDescriptor(type="forecastops", tenant_id="tenant-b")
    decision = engine.authorize(
        AccessRequest(p, Action.VIEW, res), on=ON
    )
    assert not decision.allowed
    assert decision.policy_id == "tenant_isolation"


def test_cross_region_supervisor_denied() -> None:
    # ODP-AC-AUTH-002
    engine, _ = make_engine()
    p = principal(Role.REGIONAL_SUPERVISOR, region_ids=frozenset({"north"}))
    res = ResourceDescriptor(type="forecastops", region_id="south")
    decision = engine.authorize(AccessRequest(p, Action.VIEW, res), on=ON)
    assert not decision.allowed
    assert decision.policy_id == "scope.region"


def test_franchisee_limited_to_own_store() -> None:
    # ODP-AC-AUTH-003
    engine, _ = make_engine()
    p = principal(Role.FRANCHISEE, store_ids=frozenset({"store-1"}))
    own = ResourceDescriptor(type="forecastops", store_id="store-1")
    other = ResourceDescriptor(type="forecastops", store_id="store-2")
    assert engine.authorize(AccessRequest(p, Action.VIEW, own), on=ON).allowed
    denied = engine.authorize(AccessRequest(p, Action.VIEW, other), on=ON)
    assert not denied.allowed
    assert denied.policy_id in {"scope.store", "franchisee_isolation"}


def test_data_classification_visibility() -> None:
    engine, _ = make_engine()
    p = Principal(
        subject_id="auditor-1",
        roles=frozenset({Role.AUDITOR}),
        scope=Scope(clearance=DataClassification.CONFIDENTIAL),
    )
    res = ResourceDescriptor(
        type="audit", data_classification=DataClassification.RESTRICTED
    )
    decision = engine.authorize(AccessRequest(p, Action.VIEW, res), on=ON)
    assert not decision.allowed
    assert decision.policy_id == "data_classification"


# --- engine: unauthenticated + audit ---------------------------------------

def test_unauthenticated_denied_and_audited() -> None:
    # ODP-AC-AUTH-001 + ODP-AC-AUTH-005
    engine, log = make_engine()
    res = ResourceDescriptor(type="priceops")
    req = AccessRequest(
        ANONYMOUS, Action.VIEW, res, Environment(source_ip="10.0.0.9")
    )
    decision = engine.authorize(req, on=ON)
    assert not decision.allowed
    denials = events_with_outcome(log, AuditOutcome.DENY)
    assert len(denials) == 1
    event = denials[0]
    assert event.event_type == "security.authorization"
    assert event.actor == "anonymous"
    assert event.resource == "priceops"
    assert event.metadata["source_ip"] == "10.0.0.9"
    assert event.metadata["reason"]


def test_denied_action_writes_security_audit_event() -> None:
    # ODP-AC-AUTH-005: 403 paths write security audit events
    engine, log = make_engine()
    p = principal(Role.MARKETING_MANAGER)
    res = ResourceDescriptor(type="netplan")
    engine.authorize(AccessRequest(p, Action.APPROVE, res), on=ON)
    denials = events_with_outcome(log, AuditOutcome.DENY)
    assert len(denials) == 1
    assert denials[0].metadata.get("policy_id") == "rbac"


# --- high-risk policy hooks -------------------------------------------------

def test_high_risk_denied_when_flag_disabled() -> None:
    # ODP-AC-AUTH-004: high-risk action requires the policy hook (flag)
    engine, _ = make_engine()  # default flags are all disabled
    p = principal(Role.PRICING_MANAGER)
    res = ResourceDescriptor(type="priceops")
    decision = engine.authorize(AccessRequest(p, Action.EXECUTE, res), on=ON)
    assert not decision.allowed
    assert decision.policy_id == "high_risk.feature_flag"


def test_high_risk_allowed_when_flag_enabled_with_dual_approval() -> None:
    log = InMemoryAuditLog()
    flags = default_registry()
    flags.enable(
        high_risk_flag_key("priceops", Action.EXECUTE),
        approvals=frozenset({"approver-1", "approver-2"}),
    )
    engine = AuthorizationEngine(audit_log=log, flags=flags)
    p = principal(Role.PRICING_MANAGER)
    res = ResourceDescriptor(type="priceops")
    decision = engine.authorize(AccessRequest(p, Action.EXECUTE, res), on=ON)
    assert decision.allowed
    assert "audit" in decision.obligations
    # high-risk allow is still audited
    assert events_with_outcome(log, AuditOutcome.ALLOW)


def test_separation_of_duties_blocks_self_approval() -> None:
    log = InMemoryAuditLog()
    flags = default_registry()
    flags.enable(
        high_risk_flag_key("sitescore", Action.APPROVE),
        approvals=frozenset({"a", "b"}),
    )
    engine = AuthorizationEngine(audit_log=log, flags=flags)
    reviewer = Principal(subject_id="rev-1", roles=frozenset({Role.SITE_REVIEWER}))
    res = ResourceDescriptor(
        type="sitescore", attributes={"proposed_by": "rev-1"}
    )
    decision = engine.authorize(AccessRequest(reviewer, Action.APPROVE, res), on=ON)
    assert not decision.allowed
    assert decision.policy_id == "high_risk.separation_of_duties"
