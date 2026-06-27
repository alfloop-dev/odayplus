"""Identity, role, scope, and data-classification primitives.

Source baseline:
- ODP-SA-04 §3 (role table), §4 (data scope), §5 (data sensitivity)
- ODP-SD-09 §4 (role + scope), §6 (data classification)

These are R0 foundation stubs: they fix the vocabulary (canonical role ids,
scope axes, classification ordering) that RBAC, ABAC, the API security layer,
and the audit policy compose against. Behaviour is intentionally minimal but
real enough to be tested.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any


class Role(StrEnum):
    """Canonical platform roles (ODP-SD-09 §4.1)."""

    PLATFORM_ADMIN = "platform_admin"
    ARCHITECTURE_OWNER = "architecture_owner"
    DATA_OWNER = "data_owner"
    MODEL_OWNER = "model_owner"
    RELEASE_OWNER = "release_owner"
    EXPANSION_USER = "expansion_user"
    SITE_REVIEWER = "site_reviewer"
    OPERATIONS_MANAGER = "operations_manager"
    REGIONAL_SUPERVISOR = "regional_supervisor"
    PRICING_MANAGER = "pricing_manager"
    MARKETING_MANAGER = "marketing_manager"
    FINANCE_LEGAL = "finance_legal"
    EXECUTIVE = "executive"
    FRANCHISEE = "franchisee"
    AUDITOR = "auditor"


class DataClassification(int, Enum):
    """Data sensitivity ladder (ODP-SD-09 §6), ordered low -> high.

    Integer values let visibility checks compare classifications directly:
    a principal may see a resource only when its clearance is >= the resource
    classification.
    """

    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    RESTRICTED = 3
    HIGHLY_RESTRICTED = 4


class RiskLevel(int, Enum):
    """Action risk ladder (ODP-SA-04 §7 / ODP-SD-09 §4.2 action_risk_level)."""

    LOW = 0
    MEDIUM = 1
    HIGH = 2


@dataclass(frozen=True)
class Scope:
    """Data-scope grant for a principal (ODP-SD-09 §4.2).

    An empty id set means "no explicit restriction on this axis"; a non-empty
    set restricts the principal to the listed ids. ``tenant_id`` is the single
    home tenant used for tenant isolation.
    """

    tenant_id: str | None = None
    brand_ids: frozenset[str] = frozenset()
    region_ids: frozenset[str] = frozenset()
    store_ids: frozenset[str] = frozenset()
    modules: frozenset[str] = frozenset()
    clearance: DataClassification = DataClassification.CONFIDENTIAL

    def _axis_allows(self, allowed: frozenset[str], value: str | None) -> bool:
        if not allowed:
            return True
        if value is None:
            return False
        return value in allowed

    def permits_brand(self, brand_id: str | None) -> bool:
        return self._axis_allows(self.brand_ids, brand_id)

    def permits_region(self, region_id: str | None) -> bool:
        return self._axis_allows(self.region_ids, region_id)

    def permits_store(self, store_id: str | None) -> bool:
        return self._axis_allows(self.store_ids, store_id)

    def permits_module(self, module: str | None) -> bool:
        return self._axis_allows(self.modules, module)

    def permits_classification(self, classification: DataClassification) -> bool:
        return self.clearance >= classification


@dataclass(frozen=True)
class Principal:
    """An authenticated subject and its authorization context.

    ``attributes`` carries ABAC inputs that do not fit a fixed scope axis
    (for example ``data_room_access`` or ``approved_modules``).
    """

    subject_id: str
    roles: frozenset[Role] = frozenset()
    scope: Scope = field(default_factory=Scope)
    attributes: Mapping[str, Any] = field(default_factory=dict)
    authenticated: bool = True

    def has_role(self, *roles: Role) -> bool:
        return any(role in self.roles for role in roles)

    @property
    def tenant_id(self) -> str | None:
        return self.scope.tenant_id


# Unauthenticated principal used as the default for missing/invalid credentials.
# ODP-AC-AUTH-001: unauthenticated users must not reach any protected surface.
ANONYMOUS = Principal(
    subject_id="anonymous",
    roles=frozenset(),
    scope=Scope(clearance=DataClassification.PUBLIC),
    authenticated=False,
)
