"""Role-based access control (RBAC).

Source baseline: ODP-SA-04 §6 (function permission matrix), ODP-SD-09 §4-5.

RBAC answers only "does the role permit this action on this resource type".
Data-scope, object state, risk, and policy conditions are layered on top by
:mod:`shared.auth.abac` and :mod:`shared.auth.engine`. This separation mirrors
the SD-09 §5 authorization model.
"""

from __future__ import annotations

from enum import StrEnum

from .identity import Principal, Role


class Action(StrEnum):
    """Verbs a role may be granted on a resource type."""

    VIEW = "view"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    APPROVE = "approve"
    EXECUTE = "execute"
    EXPORT = "export"
    PUBLISH = "publish"
    OVERRIDE = "override"
    ROLLBACK = "rollback"


# Resource-type wildcard used in admin-style grants.
WILDCARD = "*"


class Permission(tuple):
    """A ``(resource, action)`` grant.

    Implemented as a 2-tuple subclass so permissions are hashable, frozen, and
    cheap to put in sets. ``WILDCARD`` as the resource matches any resource.
    """

    __slots__ = ()

    def __new__(cls, resource: str, action: Action) -> Permission:
        return super().__new__(cls, (resource, action))

    @property
    def resource(self) -> str:
        return self[0]

    @property
    def action(self) -> Action:
        return self[1]

    def matches(self, resource: str, action: Action) -> bool:
        if self.action != action:
            return False
        return self.resource == WILDCARD or self.resource == resource

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Permission({self.resource!r}, {self.action.value})"


def _grant(resource: str, *actions: Action) -> set[Permission]:
    return {Permission(resource, action) for action in actions}


# Role -> granted permissions. Derived from the ODP-SA-04 §6 matrix; this is a
# foundation baseline, not the exhaustive production policy. Read access is kept
# broad for internal roles, while create/approve/execute/publish follow the
# "最終負責 (A)" / "可執行 (R)" columns of the matrix.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.PLATFORM_ADMIN: frozenset(
        _grant("feature_flag", Action.VIEW, Action.CREATE, Action.UPDATE, Action.DELETE)
        | _grant("user", Action.VIEW, Action.CREATE, Action.UPDATE, Action.DELETE)
        | _grant("role", Action.VIEW, Action.CREATE, Action.UPDATE, Action.DELETE)
        # Admin manages configuration but does not get blanket business-data
        # visibility (ODP-SA-04 §3 ROLE-ADMIN limitation).
    ),
    Role.ARCHITECTURE_OWNER: frozenset(_grant("audit", Action.VIEW)),
    Role.DATA_OWNER: frozenset(
        _grant("integration", Action.VIEW, Action.CREATE, Action.UPDATE)
        | _grant("data_quality", Action.VIEW, Action.OVERRIDE)
        | _grant("audit", Action.VIEW)
    ),
    Role.MODEL_OWNER: frozenset(
        _grant("model", Action.VIEW, Action.CREATE, Action.PUBLISH)
        | _grant("audit", Action.VIEW)
    ),
    Role.RELEASE_OWNER: frozenset(
        _grant("model", Action.VIEW, Action.PUBLISH, Action.ROLLBACK)
        | _grant("audit", Action.VIEW)
    ),
    Role.EXPANSION_USER: frozenset(
        _grant("heatzone", Action.VIEW, Action.CREATE)
        | _grant("listing", Action.VIEW, Action.CREATE, Action.UPDATE)
        | _grant("sitescore", Action.VIEW, Action.EXECUTE)
    ),
    Role.SITE_REVIEWER: frozenset(
        _grant("heatzone", Action.VIEW)
        | _grant("listing", Action.VIEW, Action.CREATE)
        | _grant("sitescore", Action.VIEW, Action.EXECUTE, Action.APPROVE, Action.OVERRIDE)
    ),
    Role.OPERATIONS_MANAGER: frozenset(
        _grant("forecastops", Action.VIEW)
        | _grant("intervention", Action.VIEW, Action.CREATE, Action.APPROVE)
        | _grant("audit", Action.VIEW)
    ),
    Role.REGIONAL_SUPERVISOR: frozenset(
        _grant("forecastops", Action.VIEW)
        | _grant("intervention", Action.VIEW, Action.EXECUTE)
        | _grant("audit", Action.VIEW)
    ),
    Role.PRICING_MANAGER: frozenset(
        _grant("priceops", Action.VIEW, Action.CREATE, Action.APPROVE, Action.EXECUTE)
    ),
    Role.MARKETING_MANAGER: frozenset(
        _grant("adlift", Action.VIEW, Action.CREATE, Action.APPROVE)
    ),
    Role.FINANCE_LEGAL: frozenset(
        _grant("avm", Action.VIEW, Action.APPROVE)
        | _grant("dealroom", Action.VIEW)
        | _grant("avm", Action.EXPORT)
    ),
    Role.EXECUTIVE: frozenset(
        _grant("netplan", Action.VIEW, Action.APPROVE)
        | _grant("heatzone", Action.VIEW, Action.APPROVE)
        | _grant("sitescore", Action.VIEW, Action.APPROVE)
        | _grant("audit", Action.VIEW)
    ),
    Role.FRANCHISEE: frozenset(
        _grant("forecastops", Action.VIEW)
        | _grant("intervention", Action.VIEW)
        | _grant("audit", Action.VIEW)
    ),
    Role.AUDITOR: frozenset(
        _grant("audit", Action.VIEW, Action.EXPORT)
        | _grant("model", Action.VIEW)
        | _grant("decision", Action.VIEW)
    ),
}


def permissions_for(roles: frozenset[Role]) -> frozenset[Permission]:
    """Union of permissions granted to the supplied roles."""

    granted: set[Permission] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(granted)


def rbac_allows(principal: Principal, resource: str, action: Action) -> bool:
    """True if any of the principal's roles grant ``action`` on ``resource``."""

    if not principal.authenticated:
        return False
    for permission in permissions_for(principal.roles):
        if permission.matches(resource, action):
            return True
    return False
