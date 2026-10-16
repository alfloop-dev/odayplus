"""Direct public guard invariants; no actual waiver registration is changed."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from shared.auth import Principal, Role, Scope
from shared.auth.tenant import (
    TenantAccessWaiver,
    TenantAccessWaiverRegistry,
    check_tenant_isolation,
)

NOW = datetime(2026, 10, 16, tzinfo=UTC)


def setup_waiver():
    registry = TenantAccessWaiverRegistry()
    waiver = TenantAccessWaiver(
        waiver_id="test-only-evidence", principal_id="actor", target_tenant_id="tenant-b",
        scope=frozenset({"audit"}), approved_by="security_officer",
        reason="synthetic owning contract", expires_at=NOW + timedelta(hours=1))
    registry.register(waiver)
    return registry, waiver


def check(tenant, target="tenant-b", **kwargs):
    actor = Principal(subject_id="actor", roles=frozenset({Role.PLATFORM_ADMIN}),
                      scope=Scope(tenant_id=tenant))
    return check_tenant_isolation(actor, target, resource_type="audit", on=NOW, **kwargs)


@pytest.mark.parametrize("tenant", [None, "", "   "])
@pytest.mark.parametrize("by_id", [False, True])
def test_registered_current_waiver_cannot_supply_missing_principal_tenant(tenant, by_id):
    registry, waiver = setup_waiver()
    result = check(tenant, waiver=waiver.waiver_id if by_id else waiver, waiver_registry=registry)
    assert result.allowed is False
    assert result.policy_id == "tenant_isolation"
    assert "Principal missing tenant" in result.reason


@pytest.mark.parametrize("target", [None, "", "   "])
def test_waiver_cannot_supply_missing_resource_tenant(target):
    registry, waiver = setup_waiver()
    assert not check("tenant-a", target, waiver=waiver, waiver_registry=registry).allowed


def test_known_tenants_still_allow_matching_and_governed_waiver():
    registry, waiver = setup_waiver()
    assert check("tenant-b").allowed
    assert not check("tenant-a").allowed
    result = check("tenant-a", waiver=waiver, waiver_registry=registry)
    assert result.allowed
    assert result.obligations == frozenset({"audit", "cross_tenant_waiver"})
    assert not waiver.is_valid_for(principal_id="actor", target_tenant_id="tenant-b",
                                   resource_type="audit", on=NOW, registry=TenantAccessWaiverRegistry())


@pytest.mark.parametrize("change", [
    {"approved_by": "untrusted"}, {"expires_at": NOW}, {"principal_id": "someone-else"},
    {"target_tenant_id": "tenant-c"}, {"scope": frozenset({"model"})},
])
def test_known_tenants_do_not_waive_signer_expiry_subject_target_or_scope(change):
    registry, waiver = setup_waiver()
    altered = replace(waiver, **change)
    registry.register(altered)
    assert not check("tenant-a", waiver=altered, waiver_registry=registry).allowed
