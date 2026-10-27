"""Feature flag governance tests (ODP-SA-08 §"Feature Flag ... Owner/到期", SA-04 §3)."""

from __future__ import annotations

from datetime import date

import pytest

from shared.auth.feature_flags import (
    ASSISTED_INTAKE_FLAG_SCOPES,
    FeatureFlag,
    FeatureFlagConfigurationConflict,
    FeatureFlagRegistry,
    FlagScope,
    FlagScopeBinding,
    Readiness,
    default_registry,
)

ON = date(2026, 6, 27)


def test_flag_requires_owner() -> None:
    with pytest.raises(ValueError):
        FeatureFlag(key="x", owner="")


def test_high_risk_flag_cannot_start_enabled() -> None:
    with pytest.raises(ValueError):
        FeatureFlag(key="x", owner="sec", high_risk=True, enabled=True)


def test_flag_metadata_present() -> None:
    flag = FeatureFlag(
        key="x",
        owner="sec",
        readiness=Readiness.BETA,
        expires_on=date(2026, 12, 31),
        description="demo",
    )
    assert flag.owner == "sec"
    assert flag.readiness is Readiness.BETA
    assert flag.expires_on == date(2026, 12, 31)


def test_expired_flag_is_inactive() -> None:
    flag = FeatureFlag(key="x", owner="sec", enabled=True, expires_on=date(2026, 1, 1))
    assert flag.is_expired(ON)
    assert not flag.is_active(ON)


def test_default_high_risk_flags_disabled() -> None:
    reg = default_registry()
    for flag in reg.all():
        if flag.high_risk:
            assert not flag.is_active(ON)


def test_high_risk_enable_requires_dual_approval() -> None:
    reg = default_registry()
    key = "high_risk.priceops.execute"
    with pytest.raises(PermissionError):
        reg.enable(key, approvals=frozenset({"only-one"}))
    reg.enable(key, approvals=frozenset({"a", "b"}))
    assert reg.is_enabled(key, on=ON)


def test_unknown_flag_disabled_by_default() -> None:
    reg = FeatureFlagRegistry()
    assert not reg.is_enabled("nope", on=ON)


def test_registry_lists_expired() -> None:
    reg = FeatureFlagRegistry()
    reg.register(FeatureFlag(key="old", owner="sec", expires_on=date(2025, 1, 1)))
    reg.register(FeatureFlag(key="new", owner="sec", expires_on=date(2099, 1, 1)))
    expired = {f.key for f in reg.expired(on=ON)}
    assert expired == {"old"}


TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000002"


def test_scoped_flag_requires_real_query_context_and_exact_tenant_source_pair():
    flag = FeatureFlag(key="assisted_intake_v1_write", owner="synthetic-scope-owner", high_risk=True,
        scope=FlagScope.TENANT_SOURCE, bindings=(FlagScopeBinding(TENANT_A, "source-a"),
        FlagScopeBinding(TENANT_B, "source-b")), enabled=True, approved_by=frozenset({"synthetic-a", "synthetic-b"}))
    registry = FeatureFlagRegistry({flag.key: flag})
    assert not registry.is_enabled(flag.key, on=ON)
    assert not registry.is_enabled(flag.key, on=ON, tenant_id=TENANT_A)
    assert registry.is_enabled(flag.key, on=ON, tenant_id=TENANT_A, source_id="source-a")
    assert registry.is_enabled(flag.key, on=ON, tenant_id=TENANT_B, source_id="source-b")
    assert not registry.is_enabled(flag.key, on=ON, tenant_id=TENANT_A, source_id="source-b")
    assert not registry.is_enabled(flag.key, on=ON, tenant_id=TENANT_B, source_id="source-a")
    assert not registry.is_enabled(flag.key, on=ON, tenant_id="*", source_id="source-a")
    assert not registry.is_enabled(flag.key, on=ON, tenant_id=TENANT_A, source_id="*")


@pytest.mark.parametrize("key", ["assisted_intake_v1_read", "assisted_intake_v1_events", "assisted_intake_v1_promotion"])
def test_tenant_scoped_flags_do_not_become_global_or_require_invented_source(key):
    flag = FeatureFlag(key=key, owner="synthetic-owner", high_risk=True,
        scope=ASSISTED_INTAKE_FLAG_SCOPES[key], bindings=(FlagScopeBinding(TENANT_A),),
        enabled=True, approved_by=frozenset({"synthetic-a", "synthetic-b"}))
    assert flag.is_active(ON, tenant_id=TENANT_A)
    assert flag.is_active(ON, tenant_id=TENANT_A, source_id="an-exact-row-source")
    assert not flag.is_active(ON, tenant_id=TENANT_B)
    assert not flag.is_active(ON)
    assert FeatureFlag.from_dict(flag.to_record()) == flag


@pytest.mark.parametrize("key,scope", ASSISTED_INTAKE_FLAG_SCOPES.items())
def test_intake_keys_cannot_be_registered_as_global_or_low_risk(key, scope):
    with pytest.raises(ValueError, match="cannot be downgraded"):
        FeatureFlag(key=key, owner="synthetic-owner", high_risk=True)
    with pytest.raises(ValueError, match="cannot be downgraded"):
        FeatureFlag(key=key, owner="synthetic-owner", scope=scope)


def test_scope_change_and_disable_invalidate_old_confirmation_and_approvals():
    key = "assisted_intake_v1_read"
    registry = FeatureFlagRegistry({key: FeatureFlag(key=key, owner="synthetic-owner", high_risk=True,
        scope=FlagScope.TENANT, bindings=(FlagScopeBinding(TENANT_A),))})
    with pytest.raises(FeatureFlagConfigurationConflict):
        registry.add_approval(key, "synthetic-a")
    for actor in ("synthetic-a", "synthetic-b"):
        registry.add_approval(key, actor, configuration_version=1)
    assert registry.enable(key, configuration_version=1).enabled
    changed = registry.configure_scope(key, (FlagScopeBinding(TENANT_B),), configuration_version=1)
    assert changed.configuration_version == 2 and not changed.enabled and not changed.approved_by
    with pytest.raises(FeatureFlagConfigurationConflict):
        registry.add_approval(key, "synthetic-a", configuration_version=1)
    with pytest.raises(FeatureFlagConfigurationConflict):
        registry.enable(key, configuration_version=1)
    for actor in ("synthetic-a", "synthetic-b"):
        registry.add_approval(key, actor, configuration_version=2)
    registry.enable(key, configuration_version=2)
    assert registry.configure_scope(key, changed.bindings, configuration_version=2).enabled  # actual no-op
    stopped = registry.disable(key)
    assert stopped.configuration_version == 3 and not stopped.enabled and not stopped.approved_by
    with pytest.raises(FeatureFlagConfigurationConflict):
        registry.add_approval(key, "synthetic-a", configuration_version=2)
    assert not registry.is_enabled(key, tenant_id=TENANT_B)


@pytest.mark.parametrize("scope,bindings", [
    (FlagScope.GLOBAL, (FlagScopeBinding(TENANT_A),)),
    (FlagScope.TENANT, (FlagScopeBinding(TENANT_A, "source-a"),)),
    (FlagScope.TENANT_SOURCE, (FlagScopeBinding(TENANT_A),)),
    (FlagScope.TENANT, (FlagScopeBinding(TENANT_A), FlagScopeBinding(TENANT_A))),
])
def test_malformed_scope_units_never_form_an_eligibility_grant(scope, bindings):
    with pytest.raises(ValueError):
        FeatureFlag(key="synthetic.scope", owner="synthetic-owner", scope=scope, bindings=bindings)


@pytest.mark.parametrize("row", [
    {"enabled": "false"}, {"high_risk": "true"}, {"approved_by": "ab"},
    {"configuration_version": True}, {"bindings": [{"tenant_id": "*"}]},
])
def test_serialized_flag_metadata_does_not_coerce_invalid_grants(row):
    with pytest.raises((ValueError, TypeError)):
        FeatureFlag.from_dict({"key": "synthetic-invalid", "owner": "synthetic-owner", **row})


def test_expiry_still_disables_an_otherwise_admitted_scoped_flag():
    flag = FeatureFlag(key="synthetic.expired.scope", owner="synthetic-owner", scope=FlagScope.TENANT,
        bindings=(FlagScopeBinding(TENANT_A),), enabled=True, expires_on=date(2025, 1, 1))
    assert not flag.is_active(ON, tenant_id=TENANT_A)
