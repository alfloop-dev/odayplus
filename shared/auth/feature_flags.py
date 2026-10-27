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
from typing import Any
from uuid import UUID


class Readiness(StrEnum):
    """Lifecycle / activation readiness of a flag (the SA-08 enable/disable
    condition axis)."""

    EXPERIMENTAL = "experimental"
    BETA = "beta"
    GA = "ga"
    DEPRECATED = "deprecated"


# Minimum approvals required to enable a high-risk flag (SA-04 §3: 雙人核准).
DUAL_APPROVAL_MINIMUM = 2


class FlagScope(StrEnum):
    GLOBAL = "global"
    TENANT = "per-tenant"
    TENANT_SOURCE = "per-tenant-per-source"
    TENANT_EVENTS = "global-per-tenant"


# The existing release manifest is checked against this same runtime contract.
# These are not defaults: adding a key must not invalidate an older global SQL
# record or activate an Intake surface during bootstrap.
ASSISTED_INTAKE_FLAG_SCOPES = {
    "assisted_intake_v1_read": FlagScope.TENANT,
    "assisted_intake_v1_shadow": FlagScope.TENANT_SOURCE,
    "assisted_intake_v1_write": FlagScope.TENANT_SOURCE,
    "assisted_intake_v1_events": FlagScope.TENANT_EVENTS,
    "assisted_intake_v1_promotion": FlagScope.TENANT,
}


class FeatureFlagConfigurationConflict(ValueError):
    """The actor did not confirm the current scope configuration."""


@dataclass(frozen=True, order=True)
class FlagScopeBinding:
    """An exact tenant or tenant/source unit, never two Cartesian lists."""

    tenant_id: str
    source_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str):
            raise ValueError("A scope binding requires an explicit tenant UUID")
        try:
            object.__setattr__(self, "tenant_id", str(UUID(self.tenant_id)))
        except (ValueError, AttributeError) as exc:
            raise ValueError("A scope binding requires an explicit tenant UUID") from exc
        if self.source_id is not None and (
            not isinstance(self.source_id, str) or not self.source_id.strip()
            or self.source_id != self.source_id.strip() or self.source_id == "*"
            or any(ord(char) < 32 for char in self.source_id)
        ):
            raise ValueError("A source binding requires an exact source identifier")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> FlagScopeBinding:
        if not isinstance(value, dict) or set(value) - {"tenant_id", "source_id"}:
            raise ValueError("Invalid feature flag scope binding")
        return cls(tenant_id=value["tenant_id"], source_id=value.get("source_id"))

    def to_dict(self) -> dict[str, Any]:
        return {"tenant_id": self.tenant_id, "source_id": self.source_id}


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
    scope: FlagScope = FlagScope.GLOBAL
    bindings: tuple[FlagScopeBinding, ...] = ()
    configuration_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("A feature flag requires an explicit key")
        if not isinstance(self.owner, str) or not self.owner.strip():
            raise ValueError(f"feature flag {self.key!r} must declare an owner")
        if type(self.enabled) is not bool or type(self.high_risk) is not bool:
            raise ValueError("Feature flag state and risk must be booleans")
        object.__setattr__(self, "readiness", Readiness(self.readiness))
        if not isinstance(self.description, str) or (self.expires_on is not None and type(self.expires_on) is not date):
            raise ValueError("Invalid feature flag metadata")
        if isinstance(self.approved_by, str) or any(
            not isinstance(actor, str) or not actor.strip() for actor in self.approved_by
        ):
            raise ValueError("Feature flag approvals require explicit actors")
        object.__setattr__(self, "approved_by", frozenset(self.approved_by))
        object.__setattr__(self, "scope", FlagScope(self.scope))
        bindings = tuple(self.bindings)
        if any(not isinstance(binding, FlagScopeBinding) for binding in bindings):
            raise ValueError("Feature flag bindings must be explicit scope units")
        if len(set(bindings)) != len(bindings):
            raise ValueError("Duplicate feature flag scope binding")
        object.__setattr__(self, "bindings", tuple(sorted(bindings, key=lambda b: (b.tenant_id, b.source_id or ""))))
        if type(self.configuration_version) is not int or self.configuration_version < 1:
            raise ValueError("Invalid feature flag configuration version")
        if self.scope == FlagScope.GLOBAL and bindings:
            raise ValueError("A global flag cannot contain tenant bindings")
        for binding in bindings:
            if (self.scope == FlagScope.TENANT_SOURCE) != (binding.source_id is not None):
                raise ValueError("Feature flag binding does not match its scope")
        expected_scope = ASSISTED_INTAKE_FLAG_SCOPES.get(self.key)
        if expected_scope is not None and (self.scope != expected_scope or self.high_risk is not True):
            raise ValueError("Intake flag scope and high-risk governance cannot be downgraded")
        # High-risk surfaces must fail safe: they can only be enabled together
        # with a recorded dual approval (SA-04 §3). This blocks both
        # enabled-by-default config and un-approved runtime enables.
        if self.high_risk and self.enabled and len(self.approved_by) < DUAL_APPROVAL_MINIMUM:
            raise ValueError(
                f"high-risk flag {self.key!r} cannot be enabled without "
                f">= {DUAL_APPROVAL_MINIMUM} approvals"
            )
        if self.enabled and self.scope != FlagScope.GLOBAL and not bindings:
            raise ValueError("A scoped flag cannot be enabled without explicit bindings")

    def is_expired(self, on: date) -> bool:
        return self.expires_on is not None and on >= self.expires_on

    def is_active(self, on: date, *, tenant_id: str | None = None, source_id: str | None = None) -> bool:
        """Effective state for an explicit resource unit; global stays compatible."""

        if not self.enabled or self.is_expired(on):
            return False
        if self.scope == FlagScope.GLOBAL:
            return True
        if not isinstance(tenant_id, str):
            return False
        try:
            tenant = str(UUID(tenant_id))
        except (ValueError, AttributeError):
            return False
        return any(binding.tenant_id == tenant and (
            self.scope != FlagScope.TENANT_SOURCE or binding.source_id == source_id
        ) for binding in self.bindings)

    def to_record(self) -> dict[str, Any]:
        """The configuration serialized by SQL, administration and command audit."""
        return {
            "key": self.key,
            "owner": self.owner,
            "enabled": self.enabled,
            "readiness": self.readiness.value if isinstance(self.readiness, Readiness) else str(self.readiness),
            "high_risk": self.high_risk,
            "expires_on": self.expires_on.isoformat() if self.expires_on else None,
            "description": self.description,
            "approved_by": list(sorted(self.approved_by)),
            "scope": self.scope.value,
            "bindings": [binding.to_dict() for binding in self.bindings],
            "configuration_version": self.configuration_version,
        }

    def to_dict(self, on: date | None = None, *, include_bindings: bool = True) -> dict[str, Any]:
        check_date = on or date.today()
        value = {
            **self.to_record(),
            "is_active": self.is_active(check_date),
            "is_expired": self.is_expired(check_date),
        }
        if not include_bindings:
            value.pop("bindings")
        return value

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FeatureFlag:
        raw_expiry = d.get("expires_on")
        expires_on = raw_expiry if type(raw_expiry) is date else date.fromisoformat(raw_expiry) if raw_expiry else None
        readiness_val = d.get("readiness", Readiness.EXPERIMENTAL)
        try:
            readiness = Readiness(readiness_val)
        except ValueError:
            readiness = Readiness.EXPERIMENTAL
        approvals = d.get("approved_by", [])
        if not isinstance(approvals, (list, tuple, set, frozenset)):
            raise ValueError("Feature flag approvals require an actor collection")
        approved_by = frozenset(approvals)
        return cls(
            key=d["key"],
            owner=d["owner"],
            enabled=d.get("enabled", False),
            readiness=readiness,
            high_risk=d.get("high_risk", False),
            expires_on=expires_on,
            description=d.get("description", ""),
            approved_by=approved_by,
            scope=FlagScope(d.get("scope", FlagScope.GLOBAL)),
            bindings=tuple(FlagScopeBinding.from_dict(value) for value in d.get("bindings", ())),
            configuration_version=d.get("configuration_version", 1),
        )


class FeatureFlagRegistry:
    """In-memory flag store with governance validation and persistence hooks."""

    def __init__(self, flags: dict[str, FeatureFlag] | None = None) -> None:
        self._flags: dict[str, FeatureFlag] = dict(flags or {})

    def register(self, flag: FeatureFlag) -> None:
        self._flags[flag.key] = flag

    def get(self, key: str) -> FeatureFlag | None:
        return self._flags.get(key)

    def all(self) -> tuple[FeatureFlag, ...]:
        return tuple(self._flags.values())

    def is_enabled(self, key: str, *, on: date | None = None,
                   tenant_id: str | None = None, source_id: str | None = None) -> bool:
        """True only when the flag exists, is enabled, and is not expired.

        Unknown flags are treated as disabled (deny by default).
        """

        check_date = on or date.today()
        flag = self._flags.get(key)
        return flag is not None and flag.is_active(check_date, tenant_id=tenant_id, source_id=source_id)

    def enable(self, key: str, *, approvals: frozenset[str] = frozenset(),
               configuration_version: int | None = None) -> FeatureFlag:
        """Enable a flag, enforcing dual approval for high-risk flags."""

        flag = self._require(key)
        self._require_configuration(flag, configuration_version)
        effective_approvals = flag.approved_by | approvals
        if flag.high_risk and len(effective_approvals) < DUAL_APPROVAL_MINIMUM:
            raise PermissionError(
                f"high-risk flag {key!r} needs >= {DUAL_APPROVAL_MINIMUM} "
                f"distinct approvals, got {len(effective_approvals)}"
            )
        updated = replace(flag, enabled=True, approved_by=effective_approvals)
        self._flags[key] = updated
        return updated

    def disable(self, key: str) -> FeatureFlag:
        flag = self._require(key)
        updated = replace(flag, enabled=False, approved_by=frozenset(),
                          configuration_version=flag.configuration_version + (flag.scope != FlagScope.GLOBAL))
        self._flags[key] = updated
        return updated

    def add_approval(self, key: str, approver: str, *, configuration_version: int | None = None) -> FeatureFlag:
        flag = self._require(key)
        self._require_configuration(flag, configuration_version)
        if not approver or not approver.strip():
            raise ValueError("approver cannot be empty")
        new_approvals = flag.approved_by | {approver.strip()}
        updated = replace(flag, approved_by=new_approvals)
        self._flags[key] = updated
        return updated

    def configure_scope(self, key: str, bindings: tuple[FlagScopeBinding, ...], *,
                        configuration_version: int | None) -> FeatureFlag:
        flag = self._require(key)
        if flag.scope == FlagScope.GLOBAL:
            raise ValueError("Global flags have no tenant scope to configure")
        self._require_configuration(flag, configuration_version)
        updated = replace(flag, bindings=bindings, enabled=False, approved_by=frozenset(),
                          configuration_version=flag.configuration_version + 1)
        if updated.bindings == flag.bindings:
            return flag
        self._flags[key] = updated
        return updated

    @staticmethod
    def _require_configuration(flag: FeatureFlag, version: int | None) -> None:
        if ((flag.scope != FlagScope.GLOBAL and version is None)
                or (version is not None and (type(version) is not int or version != flag.configuration_version))):
            raise FeatureFlagConfigurationConflict("Feature flag scope configuration changed or was not confirmed")

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

_GLOBAL_REGISTRY: FeatureFlagRegistry | None = None


def get_global_registry() -> FeatureFlagRegistry:
    """Return the process-wide singleton registry."""

    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = FeatureFlagRegistry({k: v for k, v in DEFAULT_FLAGS.items()})
    return _GLOBAL_REGISTRY


def reset_global_registry() -> FeatureFlagRegistry:
    """Reset the process-wide singleton registry to initial defaults (used in tests)."""

    global _GLOBAL_REGISTRY
    _GLOBAL_REGISTRY = FeatureFlagRegistry({k: v for k, v in DEFAULT_FLAGS.items()})
    return _GLOBAL_REGISTRY


def default_registry() -> FeatureFlagRegistry:
    """Return the shared global registry, ensuring single process-wide truth."""

    return get_global_registry()

