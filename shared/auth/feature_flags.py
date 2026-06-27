"""Feature flag primitives with governance metadata.

Source baseline: ODP-SA-08 §"Feature Flag 必須有 Owner、啟用條件、停用條件與
到期檢查" and ODP-SA-04 §3 (high-risk feature flags require dual approval);
ADR-0001 high-risk controls.

Foundation rules captured here:
- every flag carries owner / readiness / expiry metadata;
- high-risk flags default to disabled and require dual approval to enable;
- expired flags evaluate as disabled regardless of the stored value.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum


class Readiness(StrEnum):
    """Lifecycle / activation readiness of a flag (the SA-08 enable/disable
    condition axis)."""

    EXPERIMENTAL = "experimental"
    BETA = "beta"
    GA = "ga"
    DEPRECATED = "deprecated"


# Minimum approvals required to enable a high-risk flag (SA-04 §3: 雙人核准).
DUAL_APPROVAL_MINIMUM = 2


@dataclass(frozen=True)
class FeatureFlag:
    """A single feature flag and its governance metadata."""

    key: str
    owner: str
    enabled: bool = False
    readiness: Readiness = Readiness.EXPERIMENTAL
    high_risk: bool = False
    expires_on: date | None = None
    description: str = ""
    approved_by: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.owner:
            raise ValueError(f"feature flag {self.key!r} must declare an owner")
        # High-risk surfaces must fail safe: they can only be enabled together
        # with a recorded dual approval (SA-04 §3). This blocks both
        # enabled-by-default config and un-approved runtime enables.
        if self.high_risk and self.enabled and len(self.approved_by) < DUAL_APPROVAL_MINIMUM:
            raise ValueError(
                f"high-risk flag {self.key!r} cannot be enabled without "
                f">= {DUAL_APPROVAL_MINIMUM} approvals"
            )

    def is_expired(self, on: date) -> bool:
        return self.expires_on is not None and on >= self.expires_on

    def is_active(self, on: date) -> bool:
        """Effective state: enabled AND not expired."""

        return self.enabled and not self.is_expired(on)


class FeatureFlagRegistry:
    """In-memory flag store. The persistence-backed implementation arrives with
    the admin/config task; this provides the stable contract and safe defaults.
    """

    def __init__(self, flags: dict[str, FeatureFlag] | None = None) -> None:
        self._flags: dict[str, FeatureFlag] = dict(flags or {})

    def register(self, flag: FeatureFlag) -> None:
        self._flags[flag.key] = flag

    def get(self, key: str) -> FeatureFlag | None:
        return self._flags.get(key)

    def all(self) -> tuple[FeatureFlag, ...]:
        return tuple(self._flags.values())

    def is_enabled(self, key: str, *, on: date) -> bool:
        """True only when the flag exists, is enabled, and is not expired.

        Unknown flags are treated as disabled (deny by default).
        """

        flag = self._flags.get(key)
        return flag is not None and flag.is_active(on)

    def enable(self, key: str, *, approvals: frozenset[str] = frozenset()) -> FeatureFlag:
        """Enable a flag, enforcing dual approval for high-risk flags."""

        flag = self._require(key)
        if flag.high_risk and len(approvals) < DUAL_APPROVAL_MINIMUM:
            raise PermissionError(
                f"high-risk flag {key!r} needs >= {DUAL_APPROVAL_MINIMUM} "
                f"distinct approvals, got {len(approvals)}"
            )
        updated = replace(flag, enabled=True, approved_by=approvals)
        self._flags[key] = updated
        return updated

    def disable(self, key: str) -> FeatureFlag:
        flag = self._require(key)
        updated = replace(flag, enabled=False, approved_by=frozenset())
        self._flags[key] = updated
        return updated

    def expired(self, *, on: date) -> tuple[FeatureFlag, ...]:
        """Flags past their expiry date (input for the expiry-check job)."""

        return tuple(f for f in self._flags.values() if f.is_expired(on))

    def _require(self, key: str) -> FeatureFlag:
        flag = self._flags.get(key)
        if flag is None:
            raise KeyError(f"unknown feature flag {key!r}")
        return flag


def _flag(*args, **kwargs) -> tuple[str, FeatureFlag]:
    flag = FeatureFlag(*args, **kwargs)
    return flag.key, flag


# Seed flags for high-risk surfaces called out in ODP-SA-04 §7. They start
# disabled; enabling them is a dual-approval admin action.
DEFAULT_FLAGS: dict[str, FeatureFlag] = dict(
    [
        _flag("high_risk.sitescore.approve", owner="site_reviewer", high_risk=True,
              description="Enable SiteScore GO approvals"),
        _flag("high_risk.priceops.execute", owner="pricing_manager", high_risk=True,
              description="Enable price change go-live"),
        _flag("high_risk.adlift.approve", owner="marketing_manager", high_risk=True,
              description="Enable ad budget increase approvals"),
        _flag("high_risk.netplan.approve", owner="executive", high_risk=True,
              description="Enable NetPlan MOVE/EXIT approvals"),
        _flag("high_risk.model.publish", owner="release_owner", high_risk=True,
              description="Enable model promotion to production"),
    ]
)


def default_registry() -> FeatureFlagRegistry:
    """A fresh registry seeded with the default high-risk flags."""

    return FeatureFlagRegistry({k: v for k, v in DEFAULT_FLAGS.items()})
