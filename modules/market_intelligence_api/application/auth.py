"""Product authorization and security enforcement for Market Intelligence BFF.

Contract: `odayplus.market-intelligence-api.v2`.
Task ID: `ODP-API-001`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from modules.market_intelligence_api.domain.contracts import (
    ALLOWED_MARKET_INTELLIGENCE_ROLES,
    CONTRACT_ID,
)
from shared.audit.policy import build_security_event, requires_audit
from shared.auth import (
    AccessRequest,
    Action,
    DataClassification,
    Decision,
    Principal,
    ResourceDescriptor,
    Role,
)
from shared.auth.engine import AuthorizationEngine


class MarketIntelligenceError(Exception):
    """Base error for Market Intelligence operations."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "market_intelligence_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details) if details else {}


class MarketIntelligenceAuthorizationError(MarketIntelligenceError, PermissionError):
    """Raised when access to market intelligence is denied by authorization policy."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "market_intelligence_unauthorized",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)


class MarketIntelligenceNotFoundError(MarketIntelligenceError, LookupError):
    """Raised when the requested market intelligence entity or document does not exist."""

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="market_intelligence_not_found", details=details)


class MarketIntelligenceValidationError(MarketIntelligenceError, ValueError):
    """Raised when input parameters or contract schema validation fails."""

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="market_intelligence_validation_error", details=details)


def authorize_market_intelligence(
    resource_type: str,
    resource_id: str | None = None,
    *,
    action: Action = Action.VIEW,
    tenant_id: str | None = None,
    principal: Principal | None = None,
    auth_engine: AuthorizationEngine | None = None,
    classification: DataClassification = DataClassification.CONFIDENTIAL,
    enforce_auth: bool = True,
) -> str | None:
    """Enforce ODayPlus product authorization, tenant isolation, and RBAC rules.

    Returns:
        The verified effective tenant_id for downstream data access.
    """
    effective_tenant_id = tenant_id or (principal.tenant_id if principal is not None else None)

    if not enforce_auth and principal is None:
        return effective_tenant_id

    if principal is None:
        raise MarketIntelligenceAuthorizationError(
            "Authentication required: no principal provided for market intelligence access",
            code="authentication_required",
            details={"resource_type": resource_type, "resource_id": resource_id},
        )

    access = AccessRequest(
        principal=principal,
        action=action,
        resource=ResourceDescriptor(
            type=resource_type,
            resource_id=resource_id,
            tenant_id=effective_tenant_id,
            data_classification=classification,
        ),
    )

    engine = auth_engine or AuthorizationEngine()

    if not principal.authenticated:
        decision = Decision.deny("Principal is not authenticated", policy_id="authenticated")
        if hasattr(engine, "audit_log") and engine.audit_log:
            engine.audit_log.record(build_security_event(access, decision))
        raise MarketIntelligenceAuthorizationError(
            "Principal is not authenticated",
            code="unauthenticated_principal",
            details={"subject_id": principal.subject_id, "resource_type": resource_type},
        )

    # 1. Tenant Isolation
    if effective_tenant_id and principal.tenant_id and principal.tenant_id != effective_tenant_id:
        if not principal.has_role(Role.PLATFORM_ADMIN):
            decision = Decision.deny(
                f"Cross-tenant access denied: principal tenant {principal.tenant_id!r} cannot access resource tenant {effective_tenant_id!r}",
                policy_id="tenant_isolation",
            )
            if hasattr(engine, "audit_log") and engine.audit_log:
                engine.audit_log.record(build_security_event(access, decision))
            raise MarketIntelligenceAuthorizationError(
                f"Cross-tenant access denied: principal tenant {principal.tenant_id!r} cannot access resource tenant {effective_tenant_id!r}",
                code="cross_tenant_access_denied",
                details={
                    "principal_tenant_id": principal.tenant_id,
                    "resource_tenant_id": effective_tenant_id,
                    "resource_id": resource_id,
                },
            )

    # 2. RBAC: Caller must hold at least one role permitted for Market Intelligence
    has_allowed_role = any(role in ALLOWED_MARKET_INTELLIGENCE_ROLES for role in principal.roles)
    if not has_allowed_role:
        role_names = [getattr(r, "value", str(r)) for r in principal.roles]
        decision = Decision.deny(
            f"Principal {principal.subject_id!r} with roles {role_names} is not authorized for market intelligence",
            policy_id="rbac",
        )
        if hasattr(engine, "audit_log") and engine.audit_log:
            engine.audit_log.record(build_security_event(access, decision))
        raise MarketIntelligenceAuthorizationError(
            f"Principal {principal.subject_id!r} with roles {role_names} is not authorized for market intelligence",
            code="role_unauthorized",
            details={"subject_id": principal.subject_id, "roles": role_names},
        )

    # 3. Data classification clearance check
    if not principal.scope.permits_classification(classification):
        decision = Decision.deny(
            f"Principal clearance {principal.scope.clearance.name} insufficient for {classification.name} data",
            policy_id="data_classification",
        )
        if hasattr(engine, "audit_log") and engine.audit_log:
            engine.audit_log.record(build_security_event(access, decision))
        raise MarketIntelligenceAuthorizationError(
            f"Principal clearance {principal.scope.clearance.name} insufficient for {classification.name} data",
            code="insufficient_clearance",
            details={"clearance": principal.scope.clearance.value, "required": classification.value},
        )

    # 4. Audit recording for authorized access
    decision = Decision.allow("authorized")
    if requires_audit(action, classification) and hasattr(engine, "audit_log") and engine.audit_log:
        engine.audit_log.record(build_security_event(access, decision))

    return effective_tenant_id


__all__ = [
    "MarketIntelligenceAuthorizationError",
    "MarketIntelligenceError",
    "MarketIntelligenceNotFoundError",
    "MarketIntelligenceValidationError",
    "authorize_market_intelligence",
]
