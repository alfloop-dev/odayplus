"""Shared Tenant Isolation and Cross-Tenant Authorization Guard.

Source baseline:
- ODP-SA-04 §3 (ROLE-ADMIN scope limitations), §4 (data scope)
- ODP-SD-09 §4.2 (tenant scope), §5 (authorization model), §11 (audit logging)
- ODP Remediation Plan 2026-09-03 (Item 5a / Item 6):
  Unified PLATFORM_ADMIN cross-tenant policy to shared fail-closed guard.

Policy Invariants:
1. Default Fail-Closed: PLATFORM_ADMIN and all other roles cannot cross tenant
   boundaries by default.
2. Resource-derived Tenant: Target tenant must be derived from the target resource,
   never defaulted from the principal's own tenant.
3. Explicit Tenant Requirement: Missing principal tenant or missing resource tenant
   fails closed (denied).
4. Formal Risk Acceptance / Waiver: Cross-tenant access is permitted ONLY when governed
   by an active, time-bounded risk acceptance / waiver signed by an authorized decider.
5. Immutable Audit Obligation: All denials and all waiver-governed access paths must
   record canonical security audit events with actor, resource, reason, and tenant metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from shared.auth.abac import AccessRequest, Decision, Environment, ResourceDescriptor
from shared.auth.identity import DataClassification, Principal, Role
from shared.auth.rbac import Action


POLICY_ID_TENANT_ISOLATION = "tenant_isolation"


class TenantAuthorizationError(PermissionError):
    """Raised when tenant isolation or cross-tenant policy rejects access."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "cross_tenant_access_denied",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details) if details else {}


@dataclass(frozen=True)
class TenantAccessWaiver:
    """Formal, time-bounded risk acceptance / waiver for cross-tenant access.

    Requirements (ODP Remediation Plan 2026-09-03, Item 5a / 6):
    - Must be time-bounded (expires_at is mandatory and must not be expired).
    - Must be approved by an authorized signer / decider (approved_by is mandatory).
    - Must specify non-empty justification (reason is mandatory).
    - Must be scoped by principal_id (or role), target_tenant_id, and resource scope.
    """

    waiver_id: str
    principal_id: str
    target_tenant_id: str
    scope: frozenset[str] = frozenset({"*"})
    approved_by: str = ""
    reason: str = ""
    expires_at: datetime | date | None = None
    created_at: datetime | date | None = None

    def is_valid_for(
        self,
        *,
        principal_id: str,
        target_tenant_id: str,
        resource_type: str,
        on: datetime | date | None = None,
    ) -> bool:
        """Validate whether this waiver covers the access request at the given point in time."""
        if not self.approved_by or not str(self.approved_by).strip():
            return False
        if not self.reason or not str(self.reason).strip():
            return False
        if self.principal_id != "*" and self.principal_id != principal_id:
            return False
        if self.target_tenant_id != "*" and self.target_tenant_id != target_tenant_id:
            return False
        if "*" not in self.scope and resource_type not in self.scope:
            return False

        if self.expires_at is None:
            # Unbounded waivers without expiration date fail closed
            return False

        now = on or datetime.now(UTC)
        if isinstance(self.expires_at, datetime):
            exp = (
                self.expires_at
                if self.expires_at.tzinfo is not None
                else self.expires_at.replace(tzinfo=UTC)
            )
            cur = (
                now
                if isinstance(now, datetime)
                else datetime.combine(now, datetime.min.time(), tzinfo=UTC)
            )
            if cur.tzinfo is None:
                cur = cur.replace(tzinfo=UTC)
            if cur > exp:
                return False
        elif isinstance(self.expires_at, date):
            cur_date = now.date() if isinstance(now, datetime) else now
            if cur_date > self.expires_at:
                return False

        return True


def check_tenant_isolation(
    principal: Principal | None,
    resource_tenant_id: str | None,
    *,
    resource_type: str = "resource",
    resource_id: str | None = None,
    waiver: TenantAccessWaiver | None = None,
    on: datetime | date | None = None,
) -> Decision:
    """Evaluate tenant isolation policy with fail-closed semantics.

    Returns:
        Decision(allowed=True, ...) if same-tenant or covered by valid waiver.
        Decision(allowed=False, policy_id="tenant_isolation", ...) otherwise.
    """
    if principal is None or not principal.authenticated:
        return Decision.deny(
            "Principal is not authenticated",
            policy_id=POLICY_ID_TENANT_ISOLATION,
        )

    clean_resource_tenant = str(resource_tenant_id).strip() if resource_tenant_id else ""
    if not clean_resource_tenant:
        return Decision.deny(
            "Resource tenant missing or undefined: tenant isolation requires verified resource tenant",
            policy_id=POLICY_ID_TENANT_ISOLATION,
        )

    principal_tenant = principal.tenant_id
    clean_principal_tenant = str(principal_tenant).strip() if principal_tenant else ""

    if clean_principal_tenant and clean_principal_tenant == clean_resource_tenant:
        return Decision.allow("Tenant isolation verified: matching tenant")

    # Cross-tenant access attempt (or principal without tenant scope):
    # Check for formal risk acceptance / waiver
    if waiver is not None:
        if waiver.is_valid_for(
            principal_id=principal.subject_id,
            target_tenant_id=clean_resource_tenant,
            resource_type=resource_type,
            on=on,
        ):
            return Decision.allow(
                f"Cross-tenant access authorized under formal waiver {waiver.waiver_id}: {waiver.reason}",
                obligations=frozenset({"audit", "cross_tenant_waiver"}),
            )
        else:
            return Decision.deny(
                f"Cross-tenant access denied: formal waiver {waiver.waiver_id} is invalid, expired, or out of scope",
                policy_id=POLICY_ID_TENANT_ISOLATION,
            )

    if not clean_principal_tenant:
        return Decision.deny(
            "Principal missing tenant scope: tenant isolation requires verified principal tenant",
            policy_id=POLICY_ID_TENANT_ISOLATION,
        )

    # PLATFORM_ADMIN has no automatic cross-tenant bypass without a formal waiver
    return Decision.deny(
        f"Cross-tenant access denied: principal tenant {clean_principal_tenant!r} cannot access "
        f"resource tenant {clean_resource_tenant!r} without an approved time-bounded risk acceptance",
        policy_id=POLICY_ID_TENANT_ISOLATION,
    )



def tenant_isolation_policy(request: AccessRequest) -> Decision | None:
    """ABAC policy hook evaluating tenant isolation.

    Returns a denying Decision on tenant violation, or None to abstain.
    """
    principal = request.principal
    resource = request.resource
    waiver = (
        request.environment.attributes.get("tenant_waiver")
        or resource.attributes.get("tenant_waiver")
    )
    decision = check_tenant_isolation(
        principal=principal,
        resource_tenant_id=resource.tenant_id,
        resource_type=resource.type,
        resource_id=resource.resource_id,
        waiver=waiver,
    )
    if not decision.allowed:
        return decision
    return None
