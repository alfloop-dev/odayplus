"""RBAC + ABAC + authorization-engine tests.

Covers ODP-SA-04 §9 acceptance ids and ODP-SD-09 §12:
- ODP-AC-AUTH-001 unauthenticated denied
- ODP-AC-AUTH-002 cross-region denied
- ODP-AC-AUTH-003 franchisee store scope
- ODP-AC-AUTH-004 high-risk action needs approval role + policy hook
- ODP-AC-AUTH-005 permission failures write a security audit event
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

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
from shared.auth.abac import Decision, ResourceTarget, evaluate_abac
from shared.auth.identity import RiskLevel
from shared.auth.tenant import TenantAccessWaiver, default_waiver_registry

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


def test_cross_region_supervisor_denied() -> None:
    # ODP-AC-AUTH-002
    engine, _ = make_engine()
    # Explicit synthetic server-owned tenant inputs, not principal fallback.
    p = principal(
        Role.REGIONAL_SUPERVISOR, tenant_id="rbac-fixture-tenant", region_ids=frozenset({"north"})
    )
    res = ResourceDescriptor(type="forecastops", tenant_id="rbac-fixture-tenant", region_id="south")
    decision = engine.authorize(AccessRequest(p, Action.VIEW, res), on=ON)
    assert not decision.allowed
    assert decision.policy_id == "scope.region"


def test_cross_brand_principal_denied() -> None:
    engine, log = make_engine()
    p = principal(
        Role.OPERATIONS_MANAGER, tenant_id="rbac-fixture-tenant", brand_ids=frozenset({"brand-a"})
    )
    own = ResourceDescriptor(
        type="forecastops", tenant_id="rbac-fixture-tenant", brand_id="brand-a"
    )
    other = ResourceDescriptor(
        type="forecastops", tenant_id="rbac-fixture-tenant", brand_id="brand-b"
    )
    missing = ResourceDescriptor(type="forecastops", tenant_id="rbac-fixture-tenant", brand_id=None)

    assert engine.authorize(AccessRequest(p, Action.VIEW, own), on=ON).allowed

    denied = engine.authorize(AccessRequest(p, Action.VIEW, other), on=ON)
    assert not denied.allowed
    assert denied.policy_id == "scope.brand"
    assert denied.reason == "brand outside principal scope"

    denied_missing = engine.authorize(AccessRequest(p, Action.VIEW, missing), on=ON)
    assert not denied_missing.allowed
    assert denied_missing.policy_id == "scope.brand"
    assert denied_missing.reason == "brand outside principal scope"

    denials = events_with_outcome(log, AuditOutcome.DENY)
    assert any(e.metadata.get("policy_id") == "scope.brand" for e in denials)


def test_module_outside_scope_denied() -> None:
    engine, log = make_engine()
    p = principal(
        Role.OPERATIONS_MANAGER, tenant_id="rbac-fixture-tenant", modules=frozenset({"forecastops"})
    )
    own = ResourceDescriptor(
        type="forecastops", tenant_id="rbac-fixture-tenant", module="forecastops"
    )
    other = ResourceDescriptor(
        type="forecastops", tenant_id="rbac-fixture-tenant", module="netplan"
    )
    missing = ResourceDescriptor(type="forecastops", tenant_id="rbac-fixture-tenant", module=None)

    assert engine.authorize(AccessRequest(p, Action.VIEW, own), on=ON).allowed

    denied = engine.authorize(AccessRequest(p, Action.VIEW, other), on=ON)
    assert not denied.allowed
    assert denied.policy_id == "scope.module"
    assert denied.reason == "module outside principal scope"

    denied_missing = engine.authorize(AccessRequest(p, Action.VIEW, missing), on=ON)
    assert not denied_missing.allowed
    assert denied_missing.policy_id == "scope.module"
    assert denied_missing.reason == "module outside principal scope"

    denials = events_with_outcome(log, AuditOutcome.DENY)
    assert any(e.metadata.get("policy_id") == "scope.module" for e in denials)


def test_franchisee_limited_to_own_store() -> None:
    # ODP-AC-AUTH-003
    engine, _ = make_engine()
    p = principal(
        Role.FRANCHISEE, tenant_id="rbac-fixture-tenant", store_ids=frozenset({"store-1"})
    )
    own = ResourceDescriptor(
        type="forecastops", tenant_id="rbac-fixture-tenant", store_id="store-1"
    )
    other = ResourceDescriptor(
        type="forecastops", tenant_id="rbac-fixture-tenant", store_id="store-2"
    )
    assert engine.authorize(AccessRequest(p, Action.VIEW, own), on=ON).allowed
    denied = engine.authorize(AccessRequest(p, Action.VIEW, other), on=ON)
    assert not denied.allowed
    assert denied.policy_id in {"scope.store", "franchisee_isolation"}


def test_data_classification_visibility() -> None:
    engine, _ = make_engine()
    p = Principal(
        subject_id="auditor-1",
        roles=frozenset({Role.AUDITOR}),
        scope=Scope(tenant_id="rbac-fixture-tenant", clearance=DataClassification.CONFIDENTIAL),
    )
    res = ResourceDescriptor(
        type="audit",
        tenant_id="rbac-fixture-tenant",
        data_classification=DataClassification.RESTRICTED,
    )
    decision = engine.authorize(AccessRequest(p, Action.VIEW, res), on=ON)
    assert not decision.allowed
    assert decision.policy_id == "data_classification"


# --- engine: unauthenticated + audit ---------------------------------------


def test_unauthenticated_denied_and_audited() -> None:
    # ODP-AC-AUTH-001 + ODP-AC-AUTH-005
    engine, log = make_engine()
    res = ResourceDescriptor(type="priceops", tenant_id="rbac-fixture-tenant")
    req = AccessRequest(ANONYMOUS, Action.VIEW, res, Environment(source_ip="10.0.0.9"))
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
    p = principal(Role.MARKETING_MANAGER, tenant_id="rbac-fixture-tenant")
    res = ResourceDescriptor(type="netplan", tenant_id="rbac-fixture-tenant")
    engine.authorize(AccessRequest(p, Action.APPROVE, res), on=ON)
    denials = events_with_outcome(log, AuditOutcome.DENY)
    assert len(denials) == 1
    assert denials[0].metadata.get("policy_id") == "rbac"


# --- high-risk policy hooks -------------------------------------------------


def test_high_risk_denied_when_flag_disabled() -> None:
    # ODP-AC-AUTH-004: high-risk action requires the policy hook (flag)
    engine, _ = make_engine()  # default flags are all disabled
    p = principal(Role.PRICING_MANAGER, tenant_id="rbac-fixture-tenant")
    res = ResourceDescriptor(type="priceops", tenant_id="rbac-fixture-tenant")
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
    p = principal(Role.PRICING_MANAGER, tenant_id="rbac-fixture-tenant")
    res = ResourceDescriptor(type="priceops", tenant_id="rbac-fixture-tenant")
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
    reviewer = Principal(
        subject_id="rev-1",
        roles=frozenset({Role.SITE_REVIEWER}),
        scope=Scope(tenant_id="rbac-fixture-tenant"),
    )
    res = ResourceDescriptor(
        type="sitescore", tenant_id="rbac-fixture-tenant", attributes={"proposed_by": "rev-1"}
    )
    decision = engine.authorize(AccessRequest(reviewer, Action.APPROVE, res), on=ON)
    assert not decision.allowed
    assert decision.policy_id == "high_risk.separation_of_duties"


# Separate batch007 owning controls. Original assertions above are unchanged;
# their fifteen targets now have independently declared synthetic ownership.


@pytest.mark.parametrize(
    "role,kind",
    [
        (Role.AUDITOR, "audit"),
        (Role.PLATFORM_ADMIN, "audit"),
        (Role.OPERATOR_VIEWER, "forecastops"),
        (Role.OPERATIONS_MANAGER, "forecastops"),
    ],
)
@pytest.mark.parametrize("target_tenant,allowed", [("tenant-a", True), ("tenant-b", False)])
def test_default_engine_enforces_owning_tenant(role, kind, target_tenant, allowed) -> None:
    engine, log = make_engine()
    p = principal(role, tenant_id="tenant-a")
    res = ResourceDescriptor(type=kind, tenant_id=target_tenant, resource_id="owned-record")
    result = engine.authorize(AccessRequest(p, Action.VIEW, res))
    assert result.allowed is allowed
    assert len(log.list_events()) == 1
    if allowed:
        assert "audit" in result.obligations
    else:
        assert result.policy_id in {"tenant_isolation", "operator.tenant_isolation"}


@pytest.mark.parametrize(
    "principal_tenant,resource_tenant",
    [
        (None, None),
        (None, "tenant-a"),
        ("tenant-a", None),
        ("", "tenant-a"),
        ("tenant-a", ""),
        ("  ", "tenant-a"),
        ("tenant-a", "  "),
        (17, "tenant-a"),
        ("tenant-a", 17),
        (True, True),
        (["tenant-a"], ["tenant-a"]),
        (" tenant-a", "tenant-a"),
        ("tenant-a", "tenant-a "),
    ],
)
@pytest.mark.parametrize("resource_id", [None, "owned-record"])
def test_missing_or_invalid_bound_evidence_denies(
    principal_tenant, resource_tenant, resource_id
) -> None:
    engine, log = make_engine()
    p = principal(Role.AUDITOR, tenant_id=principal_tenant)
    res = ResourceDescriptor(type="audit", tenant_id=resource_tenant, resource_id=resource_id)
    result = engine.authorize(AccessRequest(p, Action.VIEW, res))
    assert not result.allowed
    assert result.policy_id == "tenant_isolation"
    assert len(events_with_outcome(log, AuditOutcome.DENY)) == 1


@pytest.mark.parametrize(
    "attributes",
    [
        {},
        {"global": True},
        {"abstract_capability": True},
        {"target": ResourceTarget.ABSTRACT_CAPABILITY},
        {"tenant_id": "tenant-a"},
    ],
)
def test_original_undecorated_input_and_attribute_spoofs_deny(attributes) -> None:
    engine, _ = make_engine()
    # Exact original missing-both input, including absent resource id, now DENIES.
    p = principal(Role.AUDITOR)
    res = ResourceDescriptor(type="audit", attributes=attributes)
    assert not engine.authorize(AccessRequest(p, Action.VIEW, res)).allowed
    p = principal(Role.AUDITOR, tenant_id="tenant-a")
    assert not engine.authorize(AccessRequest(p, Action.VIEW, res)).allowed


@pytest.mark.parametrize("policies", [(), (lambda req: Decision.allow("early allow"),)])
def test_custom_policy_chain_cannot_erase_tenant_boundary(policies) -> None:
    p = principal(Role.AUDITOR, tenant_id="tenant-a")
    request = AccessRequest(p, Action.VIEW, ResourceDescriptor(type="audit", tenant_id="tenant-b"))
    assert not evaluate_abac(request, policies).allowed
    assert not AuthorizationEngine(policies=policies).authorize(request).allowed


@pytest.mark.parametrize(
    "extra",
    [
        {"tenant_id": "tenant-b"},
        {"tenant_id": ""},
        {"resource_id": "tenant-record"},
        {"resource_id": ""},
        {"owner_subject_id": "owner"},
    ],
)
def test_abstract_declaration_cannot_override_owning_evidence(extra) -> None:
    p = principal(Role.AUDITOR, tenant_id="tenant-a")
    res = ResourceDescriptor(type="audit", target=ResourceTarget.ABSTRACT_CAPABILITY, **extra)
    assert not AuthorizationEngine().authorize(AccessRequest(p, Action.VIEW, res)).allowed


@pytest.mark.parametrize("target", ["abstract_capability", "tenant_bound", True, None])
def test_untyped_target_declaration_denies(target) -> None:
    p = principal(Role.AUDITOR, tenant_id="tenant-a")
    res = ResourceDescriptor(type="audit", tenant_id="tenant-a", target=target)
    assert not AuthorizationEngine().authorize(AccessRequest(p, Action.VIEW, res)).allowed


@pytest.mark.parametrize(
    "overrides,policy",
    [
        ({}, None),
        ({"brand_id": "other"}, "scope.brand"),
        ({"region_id": "other"}, "scope.region"),
        ({"store_id": "other"}, "scope.store"),
        ({"module": "other"}, "scope.module"),
        ({"data_classification": DataClassification.RESTRICTED}, "data_classification"),
    ],
)
def test_explicit_server_abstract_capability_keeps_other_constraints(overrides, policy) -> None:
    p = principal(
        Role.OPERATIONS_MANAGER,
        brand_ids=frozenset({"b"}),
        region_ids=frozenset({"r"}),
        store_ids=frozenset({"s"}),
        modules=frozenset({"forecastops"}),
    )
    inputs = dict(
        type="forecastops",
        target=ResourceTarget.ABSTRACT_CAPABILITY,
        brand_id="b",
        region_id="r",
        store_id="s",
        module="forecastops",
    )
    inputs.update(overrides)
    result = AuthorizationEngine().authorize(
        AccessRequest(p, Action.VIEW, ResourceDescriptor(**inputs))
    )
    assert result.allowed is (policy is None)
    assert result.policy_id == policy


@pytest.mark.parametrize("policies", [None, (), (lambda req: Decision.allow(),)])
def test_operator_viewer_abstract_declaration_never_bypasses_strict_rule(policies) -> None:
    p = principal(Role.OPERATOR_VIEWER, tenant_id="tenant-a")
    res = ResourceDescriptor(type="forecastops", target=ResourceTarget.ABSTRACT_CAPABILITY)
    result = AuthorizationEngine(policies=policies).authorize(AccessRequest(p, Action.VIEW, res))
    assert not result.allowed
    assert result.policy_id == "operator.tenant_isolation"


@pytest.mark.parametrize(
    "enabled,proposer,policy",
    [
        (False, "other", "high_risk.feature_flag"),
        (True, "user-site_reviewer", "high_risk.separation_of_duties"),
        (True, "other", None),
    ],
)
def test_abstract_capability_keeps_flag_sod_and_dual_approval(enabled, proposer, policy) -> None:
    flags = default_registry()
    if enabled:
        flags.enable(
            high_risk_flag_key("sitescore", Action.APPROVE), approvals=frozenset({"a", "b"})
        )
    p = principal(Role.SITE_REVIEWER)
    res = ResourceDescriptor(
        type="sitescore",
        target=ResourceTarget.ABSTRACT_CAPABILITY,
        risk_level=RiskLevel.HIGH,
        attributes={"proposed_by": proposer},
    )
    engine = AuthorizationEngine(flags=flags)
    result = engine.authorize(AccessRequest(p, Action.APPROVE, res))
    assert result.allowed is (policy is None)
    assert result.policy_id == policy
    if result.allowed:
        assert result.obligations == frozenset({"audit", "two_person_approval"})
    assert len(engine.audit_log.list_events()) == 1


@pytest.fixture
def registered_tenant_waiver():
    registry = default_waiver_registry()
    waiver = TenantAccessWaiver(
        waiver_id="batch007-synthetic-only",
        principal_id="user-auditor",
        target_tenant_id="tenant-b",
        scope=frozenset({"audit", "priceops"}),
        approved_by="security_officer",
        reason="synthetic source-only regression",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    registry.register(waiver)
    try:
        yield waiver
    finally:
        registry.unregister(waiver.waiver_id)


@pytest.mark.parametrize("reference_kind", ["object", "id"])
@pytest.mark.parametrize("high_risk", [False, True])
def test_registered_waiver_obligations_survive_engine_and_audit(
    registered_tenant_waiver, reference_kind, high_risk
) -> None:
    from dataclasses import replace

    waiver = registered_tenant_waiver
    role, kind, action = (
        (Role.PRICING_MANAGER, "priceops", Action.EXECUTE)
        if high_risk
        else (Role.AUDITOR, "audit", Action.VIEW)
    )
    if high_risk:
        waiver = replace(waiver, principal_id="user-pricing_manager")
        default_waiver_registry().register(waiver)
    flags = default_registry()
    if high_risk:
        flags.enable(high_risk_flag_key(kind, action), approvals=frozenset({"a", "b"}))
    engine = AuthorizationEngine(flags=flags)
    p = principal(role, tenant_id="tenant-a", clearance=DataClassification.PUBLIC)
    res = ResourceDescriptor(
        type=kind,
        tenant_id="tenant-b",
        risk_level=RiskLevel.HIGH,
        data_classification=DataClassification.PUBLIC,
    )
    reference = waiver if reference_kind == "object" else waiver.waiver_id
    request = AccessRequest(p, action, res, Environment(attributes={"tenant_waiver": reference}))
    result = engine.authorize(request)
    assert result.allowed
    assert {"audit", "cross_tenant_waiver"} <= result.obligations
    if high_risk:
        assert "two_person_approval" in result.obligations
    # PUBLIC VIEW normally needs no audit; explicit waiver obligation still does.
    assert len(events_with_outcome(engine.audit_log, AuditOutcome.ALLOW)) == 1
    assert "cross_tenant_waiver" in evaluate_abac(request).obligations


@pytest.mark.parametrize(
    "change",
    [
        "unregistered",
        "clone",
        "expired",
        "unsigned",
        "principal",
        "target",
        "scope",
        "unbounded",
    ],
)
def test_invalid_waivers_cannot_authorize_foreign_tenant(registered_tenant_waiver, change) -> None:
    from dataclasses import replace

    waiver = registered_tenant_waiver
    changes = {
        "expired": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
        "unsigned": {"approved_by": "untrusted"},
        "principal": {"principal_id": "other"},
        "target": {"target_tenant_id": "other"},
        "scope": {"scope": frozenset({"other"})},
        "unbounded": {"expires_at": None},
    }
    if change in changes:
        waiver = replace(waiver, **changes[change])
        default_waiver_registry().register(waiver)
    elif change == "unregistered":
        default_waiver_registry().unregister(waiver.waiver_id)
    else:
        waiver = replace(waiver)
    p = principal(Role.AUDITOR, tenant_id="tenant-a")
    res = ResourceDescriptor(
        type="audit", tenant_id="tenant-b", attributes={"tenant_waiver": waiver}
    )
    engine = AuthorizationEngine()
    result = engine.authorize(AccessRequest(p, Action.VIEW, res))
    assert not result.allowed
    assert result.policy_id == "tenant_isolation"
    assert len(events_with_outcome(engine.audit_log, AuditOutcome.DENY)) == 1


def test_waiver_and_early_allow_cannot_erase_later_policy_denial(registered_tenant_waiver) -> None:
    p = principal(Role.AUDITOR, tenant_id="tenant-a")
    res = ResourceDescriptor(
        type="audit", tenant_id="tenant-b", attributes={"tenant_waiver": registered_tenant_waiver}
    )
    request = AccessRequest(p, Action.VIEW, res)
    policies = (
        lambda req: Decision.allow("first"),
        lambda req: Decision.deny("later veto", policy_id="veto"),
    )
    result = AuthorizationEngine(policies=policies).authorize(request)
    assert not result.allowed
    assert result.policy_id == "veto"


def test_waiver_cannot_supply_missing_principal_ownership(registered_tenant_waiver) -> None:
    p = principal(Role.AUDITOR)
    res = ResourceDescriptor(
        type="audit", tenant_id="tenant-b", attributes={"tenant_waiver": registered_tenant_waiver}
    )
    assert not AuthorizationEngine().authorize(AccessRequest(p, Action.VIEW, res)).allowed
