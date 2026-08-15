"""Shared auth and authorization primitives.

Public surface for identity, RBAC, ABAC, feature flags, and the authorization
engine. See ODP-SA-04 and ODP-SD-09 for the source design.
"""

from .abac import (
    AbacPolicy,
    AccessRequest,
    Decision,
    Environment,
    ResourceDescriptor,
    evaluate_abac,
)
from .engine import AuthorizationEngine, high_risk_flag_key
from .feature_flags import (
    DEFAULT_FLAGS,
    DUAL_APPROVAL_MINIMUM,
    FeatureFlag,
    FeatureFlagRegistry,
    Readiness,
    default_registry,
)
from .identity import (
    ANONYMOUS,
    DataClassification,
    Principal,
    RiskLevel,
    Role,
    Scope,
)
from .rbac import (
    ROLE_PERMISSIONS,
    Action,
    Permission,
    permissions_for,
    rbac_allows,
)

__all__ = [
    "ANONYMOUS",
    "AbacPolicy",
    "AccessRequest",
    "Action",
    "AuthorizationEngine",
    "DEFAULT_FLAGS",
    "DUAL_APPROVAL_MINIMUM",
    "DataClassification",
    "Decision",
    "Environment",
    "FeatureFlag",
    "FeatureFlagRegistry",
    "Permission",
    "Principal",
    "ROLE_PERMISSIONS",
    "Readiness",
    "ResourceDescriptor",
    "RiskLevel",
    "Role",
    "Scope",
    "default_registry",
    "evaluate_abac",
    "high_risk_flag_key",
    "permissions_for",
    "rbac_allows",
]
