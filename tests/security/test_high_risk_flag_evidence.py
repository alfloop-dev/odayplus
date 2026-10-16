"""Critical-action classification and canonical server approval evidence."""
from datetime import date

import pytest

from shared.auth import (
    AccessRequest, Action, AuthorizationEngine, Principal, ResourceDescriptor, Role, Scope,
)
from shared.auth.feature_flags import FeatureFlag, FeatureFlagRegistry

KEY = "high_risk.priceops.execute"


def request():
    return AccessRequest(
        Principal(subject_id="officer", roles=frozenset({Role.PRICING_MANAGER}),
                  scope=Scope(tenant_id="tenant-a")),
        Action.EXECUTE, ResourceDescriptor(type="priceops", tenant_id="tenant-a"))


@pytest.mark.parametrize("approvals", [
    ["", " "], ["a", " a "], ["a", "a"], ["a", None], ["a", 1], "ab", None,
])
@pytest.mark.parametrize("entry", ["constructor", "deserialize", "enable"])
def test_invalid_approval_evidence_is_rejected_at_each_entry(approvals, entry):
    with pytest.raises(ValueError):
        if entry == "constructor":
            FeatureFlag(KEY, owner="test", high_risk=True, enabled=True, approved_by=approvals)
        elif entry == "deserialize":
            FeatureFlag.from_dict(dict(key=KEY, owner="test", high_risk=True,
                                       enabled=True, approved_by=approvals))
        else:
            registry = FeatureFlagRegistry({KEY: FeatureFlag(KEY, owner="test", high_risk=True)})
            registry.enable(KEY, approvals=approvals)


@pytest.mark.parametrize("approver", [None, 1, "", "   "])
def test_add_approval_does_not_coerce_an_identity(approver):
    registry = FeatureFlagRegistry({KEY: FeatureFlag(KEY, owner="test", high_risk=True)})
    with pytest.raises(ValueError):
        registry.add_approval(KEY, approver)
    assert registry.get(KEY).approved_by == frozenset()


@pytest.mark.parametrize("approvals", [frozenset(), frozenset({"a", "b"})])
def test_low_risk_definition_under_critical_key_never_authorizes(approvals):
    flag = FeatureFlag(KEY, owner="test", enabled=True, approved_by=approvals)
    engine = AuthorizationEngine(flags=FeatureFlagRegistry({KEY: flag}))
    result = engine.authorize(request())
    assert not result.allowed
    assert result.policy_id == "high_risk.feature_flag"
    assert engine.audit_log.list_events()[-1].outcome == "deny"


def test_canonical_distinct_approvals_enable_and_remain_audited():
    registry = FeatureFlagRegistry({KEY: FeatureFlag(KEY, owner="test", high_risk=True)})
    registry.add_approval(KEY, " a ")
    registry.add_approval(KEY, "a")  # idempotent, not a second approval
    with pytest.raises(PermissionError):
        registry.enable(KEY)
    flag = registry.enable(KEY, approvals=frozenset({" b "}))
    assert flag.approved_by == frozenset({"a", "b"})
    assert FeatureFlag.from_dict(flag.to_dict()).approved_by == flag.approved_by
    engine = AuthorizationEngine(flags=registry)
    result = engine.authorize(request())
    assert result.allowed
    assert "audit" in result.obligations
    assert engine.audit_log.list_events()[-1].outcome == "allow"
    registry.disable(KEY)
    assert not engine.authorize(request()).allowed


def test_expired_genuine_approvals_do_not_enable_critical_action():
    flag = FeatureFlag(KEY, owner="test", high_risk=True, enabled=True,
                       approved_by=frozenset({"a", "b"}), expires_on=date(2026, 1, 1))
    result = AuthorizationEngine(flags=FeatureFlagRegistry({KEY: flag})).authorize(
        request(), on=date(2026, 1, 1))
    assert not result.allowed


@pytest.mark.parametrize("field", ["enabled", "high_risk"])
def test_deserialization_never_turns_string_boolean_into_authority(field):
    with pytest.raises(ValueError):
        FeatureFlag.from_dict({"key": KEY, "owner": "test", field: "false"})
