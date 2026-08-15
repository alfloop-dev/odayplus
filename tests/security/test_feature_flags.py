"""Feature flag governance tests (ODP-SA-08 §"Feature Flag ... Owner/到期", SA-04 §3)."""

from __future__ import annotations

from datetime import date

import pytest

from shared.auth.feature_flags import (
    FeatureFlag,
    FeatureFlagRegistry,
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
