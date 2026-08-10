"""User and Role Management Application Service (UX-SCR-ADMIN-001 / FR-OPS-003).

Provides self-service role & data-scope assignment, principal mapping, role definition
queries, and audit logging for all user permission changes (ODP-CAP-USER-ROLE-UI-001).

Scope Dimensions:
- Tenant / Brand / Region / Store / Role / Attribute ABAC & RBAC controls.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from shared.audit import AuditEvent, InMemoryAuditLog
from shared.audit.integrity import (
    DEFAULT_AUDIT_INTEGRITY_KEY_ID,
    DEFAULT_AUDIT_SIGNATURE_ALG,
    DEFAULT_AUDIT_SIGNATURE_VERSION,
    DEFAULT_AUDIT_WORM_SINK_ID,
)
from shared.auth import DataClassification, Role


class UserRoleManagementError(Exception):
    """Base exception for User Role Management service."""


class UserNotFound(UserRoleManagementError):
    """Raised when a subject_id is not found."""


class UserRolePolicyError(UserRoleManagementError):
    """Raised when a role management operation violates policy."""


# Default seed principals used in development and fallback
DEFAULT_SEED_USERS: list[dict[str, Any]] = [
    {
        "subject_id": "ops-lead",
        "email": "ops-lead@odayplus.com",
        "name": "營運主管",
        "roles": [Role.OPERATIONS_MANAGER.value, Role.REGIONAL_SUPERVISOR.value],
        "scope": {
            "tenant_id": "tenant-default",
            "brand_ids": ["brand-a", "brand-b"],
            "region_ids": ["region-north", "region-south"],
            "store_ids": [],
            "clearance": DataClassification.CONFIDENTIAL.name,
        },
        "attributes": {"department": "Operations", "level": "Senior"},
        "status": "active",
        "updated_at": "2026-08-01T00:00:00Z",
        "updated_by": "system-bootstrap",
        "notes": "Default operations lead principal",
    },
    {
        "subject_id": "pm-auditor",
        "email": "pm-auditor@odayplus.com",
        "name": "PM / 稽核",
        "roles": [Role.AUDITOR.value, Role.COMPLIANCE_OFFICER.value],
        "scope": {
            "tenant_id": "tenant-default",
            "brand_ids": [],
            "region_ids": [],
            "store_ids": [],
            "clearance": DataClassification.HIGHLY_RESTRICTED.name,
        },
        "attributes": {"department": "Audit", "level": "Lead"},
        "status": "active",
        "updated_at": "2026-08-01T00:00:00Z",
        "updated_by": "system-bootstrap",
        "notes": "PM & Audit principal",
    },
    {
        "subject_id": "marketing-lead",
        "email": "marketing-lead@odayplus.com",
        "name": "行銷經理",
        "roles": [Role.MARKETING_MANAGER.value, Role.PRICING_MANAGER.value],
        "scope": {
            "tenant_id": "tenant-default",
            "brand_ids": ["brand-a"],
            "region_ids": [],
            "store_ids": [],
            "clearance": DataClassification.CONFIDENTIAL.name,
        },
        "attributes": {"department": "Marketing"},
        "status": "active",
        "updated_at": "2026-08-01T00:00:00Z",
        "updated_by": "system-bootstrap",
        "notes": "Marketing lead principal",
    },
    {
        "subject_id": "expansion-lead",
        "email": "expansion-lead@odayplus.com",
        "name": "展店經理",
        "roles": [Role.EXPANSION_USER.value, Role.SITE_REVIEWER.value],
        "scope": {
            "tenant_id": "tenant-default",
            "brand_ids": ["brand-a", "brand-b"],
            "region_ids": ["region-north"],
            "store_ids": [],
            "clearance": DataClassification.CONFIDENTIAL.name,
        },
        "attributes": {"department": "Expansion"},
        "status": "active",
        "updated_at": "2026-08-01T00:00:00Z",
        "updated_by": "system-bootstrap",
        "notes": "Expansion & Site reviewer principal",
    },
    {
        "subject_id": "platform-admin",
        "email": "admin@odayplus.com",
        "name": "平台維運管理員",
        "roles": [Role.PLATFORM_ADMIN.value],
        "scope": {
            "tenant_id": "tenant-default",
            "brand_ids": [],
            "region_ids": [],
            "store_ids": [],
            "clearance": DataClassification.HIGHLY_RESTRICTED.name,
        },
        "attributes": {"department": "Platform Ops", "super_admin": True},
        "status": "active",
        "updated_at": "2026-08-01T00:00:00Z",
        "updated_by": "system-bootstrap",
        "notes": "Platform super admin",
    },
    {
        "subject_id": "110296401444439097904",
        "email": "oday-dev-smoke-operator@odayplus.iam.gserviceaccount.com",
        "name": "Cloud Run Live Smoke Operator",
        "roles": [Role.OPERATIONS_MANAGER.value],
        "scope": {
            "tenant_id": "tenant-default",
            "brand_ids": [],
            "region_ids": [],
            "store_ids": [],
            "clearance": DataClassification.CONFIDENTIAL.name,
        },
        "attributes": {"service_account": True},
        "status": "active",
        "updated_at": "2026-08-01T00:00:00Z",
        "updated_by": "system-bootstrap",
        "notes": "WIF/Service Account smoke test operator",
    },
]

# Chinese labels for canonical roles
ROLE_LABELS: dict[str, str] = {
    Role.PLATFORM_ADMIN.value: "平台維運管理員",
    Role.ARCHITECTURE_OWNER.value: "架構負責人",
    Role.DATA_OWNER.value: "資料負責人",
    Role.MODEL_OWNER.value: "模型負責人",
    Role.RELEASE_OWNER.value: "發布負責人",
    Role.EXPANSION_USER.value: "展店經理",
    Role.SITE_REVIEWER.value: "選址審核員",
    Role.OPERATIONS_MANAGER.value: "營運主管",
    Role.REGIONAL_SUPERVISOR.value: "區域督導",
    Role.PRICING_MANAGER.value: "定價經理",
    Role.MARKETING_MANAGER.value: "行銷經理",
    Role.FINANCE_LEGAL.value: "法務與財務",
    Role.COMPLIANCE_OFFICER.value: "合規官",
    Role.RECORDS_MANAGER.value: "紀錄管理員",
    Role.RETENTION_MANAGER.value: "保留管理員",
    Role.EXECUTIVE.value: "高階主管",
    Role.FRANCHISEE.value: "加盟商",
    Role.AUDITOR.value: "PM / 稽核員",
}


class UserRoleManagementService:
    """Domain service for managing users, roles, scope constraints, and audit trail."""

    def __init__(
        self,
        *,
        audit_log: InMemoryAuditLog | None = None,
        initial_state: dict[str, Any] | None = None,
        initial_users: list[dict[str, Any]] | None = None,
        seed_fixtures: bool = True,
    ) -> None:
        self.audit_log = InMemoryAuditLog()
        self._shared_audit_log = audit_log
        self._users: dict[str, dict[str, Any]] = {}

        if seed_fixtures:
            for user in DEFAULT_SEED_USERS:
                self._users[user["subject_id"]] = dict(user)

        raw_users = (initial_state or {}).get("users") or initial_users
        if raw_users:
            for user in raw_users:
                sub = user.get("subject_id")
                if sub:
                    self._users[sub] = dict(user)

        raw_events = (initial_state or {}).get("events")
        if raw_events:
            for raw_ev in raw_events:
                if isinstance(raw_ev, dict):
                    meta = dict(raw_ev.get("metadata") or raw_ev.get("detail") or {})
                    ev_id = raw_ev.get("event_id") or str(uuid4())
                    ev_type = raw_ev.get("event_type") or "user_role_management.event"
                    actor = raw_ev.get("actor") or "system"
                    action = raw_ev.get("action") or "EVENT"
                    res = raw_ev.get("resource") or "user"
                    outcome = raw_ev.get("outcome") or raw_ev.get("result") or "success"
                    cid = raw_ev.get("correlation_id") or str(uuid4())
                    job_id = raw_ev.get("job_id")

                    occ_raw = raw_ev.get("occurred_at") or raw_ev.get("timestamp")
                    if occ_raw:
                        try:
                            occurred_at = datetime.fromisoformat(str(occ_raw))
                        except Exception:
                            occurred_at = datetime.now(UTC)
                    else:
                        occurred_at = datetime.now(UTC)

                    integrity = raw_ev.get("integrity") or {}
                    seq = (
                        integrity.get("sequence")
                        if "sequence" in integrity
                        else raw_ev.get("sequence")
                    )
                    prev_hash = (
                        integrity.get("previous_hash")
                        if "previous_hash" in integrity
                        else raw_ev.get("previous_hash")
                    )
                    ev_hash = (
                        integrity.get("event_hash")
                        if "event_hash" in integrity
                        else raw_ev.get("event_hash")
                    )
                    sig_key_id = (
                        integrity.get("signature_key_id")
                        or raw_ev.get("signature_key_id")
                        or DEFAULT_AUDIT_INTEGRITY_KEY_ID
                    )
                    sig_ver = (
                        integrity.get("signature_version")
                        or raw_ev.get("signature_version")
                        or DEFAULT_AUDIT_SIGNATURE_VERSION
                    )
                    sig_alg = (
                        integrity.get("signature_alg")
                        or raw_ev.get("signature_alg")
                        or DEFAULT_AUDIT_SIGNATURE_ALG
                    )
                    worm_sink_id = (
                        integrity.get("worm_sink_id")
                        or raw_ev.get("worm_sink_id")
                        or DEFAULT_AUDIT_WORM_SINK_ID
                    )

                    ev = AuditEvent(
                        event_id=ev_id,
                        event_type=ev_type,
                        actor=actor,
                        action=action,
                        resource=res,
                        outcome=outcome,
                        correlation_id=cid,
                        job_id=job_id,
                        metadata=meta,
                        occurred_at=occurred_at,
                        sequence=seq,
                        previous_hash=prev_hash,
                        event_hash=ev_hash,
                        signature_key_id=sig_key_id,
                        signature_version=sig_ver,
                        signature_alg=sig_alg,
                        worm_sink_id=worm_sink_id,
                    )
                    if seq is not None and ev_hash is not None:
                        self.audit_log._events.append(ev)
                    else:
                        self.audit_log.record(ev)

    def _record_audit_event(self, event: AuditEvent) -> AuditEvent:
        if self._shared_audit_log is not None and self._shared_audit_log is not self.audit_log:
            shared_event = AuditEvent(
                event_id=event.event_id,
                event_type=event.event_type,
                actor=event.actor,
                action=event.action,
                resource=event.resource,
                outcome=event.outcome,
                correlation_id=event.correlation_id,
                job_id=event.job_id,
                metadata=dict(event.metadata),
                occurred_at=event.occurred_at,
            )
            self._shared_audit_log.record(shared_event)
        recorded = self.audit_log.record(event)
        return recorded

    def export_state(self) -> dict[str, Any]:
        """Export state dictionary for durable persistence."""
        return {
            "users": [dict(u) for u in self._users.values()],
            "events": [e.to_dict() for e in self.audit_log.list_events()],
        }

    def list_users(self, *, status_filter: str | None = None) -> list[dict[str, Any]]:
        """Return all user role and scope records, optionally filtered by status."""
        users = list(self._users.values())
        if status_filter:
            users = [u for u in users if u.get("status") == status_filter]
        return users

    def get_user(self, subject_id: str) -> dict[str, Any]:
        """Return a single user record by subject_id."""
        if subject_id not in self._users:
            raise UserNotFound(f"User with subject_id '{subject_id}' not found")
        return dict(self._users[subject_id])

    def list_roles(self) -> list[dict[str, Any]]:
        """List all canonical system roles and their human-readable labels."""
        result: list[dict[str, Any]] = []
        for role_enum in Role:
            role_id = role_enum.value
            result.append(
                {
                    "role_id": role_id,
                    "label": ROLE_LABELS.get(role_id, role_id),
                    "description": f"Canonical role: {role_id}",
                }
            )
        return result

    def save_user(
        self,
        *,
        subject_id: str,
        roles: list[str],
        scope: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
        email: str | None = None,
        name: str | None = None,
        status: str = "active",
        actor_name: str | None = None,
        actor_role: str | None = None,
        reason: str = "",
        correlation_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a user's roles, scope axes, and attributes with audit trail."""
        subject_id = subject_id.strip()
        if not subject_id:
            raise UserRolePolicyError("subject_id must not be empty")

        # Validate canonical roles
        valid_role_values = {r.value for r in Role}
        validated_roles: list[str] = []
        for r in roles:
            r_str = str(r).strip()
            if r_str not in valid_role_values:
                raise UserRolePolicyError(
                    f"Invalid role '{r_str}'. Must be one of canonical roles."
                )
            if r_str not in validated_roles:
                validated_roles.append(r_str)

        if not validated_roles:
            raise UserRolePolicyError("At least one valid role must be assigned to user")

        now_str = datetime.now(UTC).isoformat()
        actor = actor_name or "platform_admin"

        existing = self._users.get(subject_id)
        before_state = dict(existing) if existing else None
        existing_tenant = (existing.get("scope") or {}).get("tenant_id") if existing else None

        client_tenant = (scope or {}).get("tenant_id")
        if tenant_id:
            if client_tenant and client_tenant != tenant_id:
                raise UserRolePolicyError(
                    f"Cannot save user scope for tenant '{client_tenant}'; caller is restricted to tenant '{tenant_id}'."
                )
            if existing_tenant and existing_tenant != "tenant-default" and existing_tenant != tenant_id:
                raise UserRolePolicyError(
                    f"Cannot modify user belonging to tenant '{existing_tenant}'; caller is restricted to tenant '{tenant_id}'."
                )

        resolved_tenant = client_tenant or tenant_id or existing_tenant or "tenant-default"

        updated_scope = {
            "tenant_id": resolved_tenant,
            "brand_ids": list((scope or {}).get("brand_ids", [])),
            "region_ids": list((scope or {}).get("region_ids", [])),
            "store_ids": list((scope or {}).get("store_ids", [])),
            "assigned_area_ids": list((scope or {}).get("assigned_area_ids", [])),
            "heat_zone_ids": list((scope or {}).get("heat_zone_ids", [])),
            "modules": list((scope or {}).get("modules", [])),
            "clearance": (scope or {}).get(
                "clearance", DataClassification.CONFIDENTIAL.name
            ),
        }

        updated_record: dict[str, Any] = {
            "subject_id": subject_id,
            "email": email or (existing.get("email") if existing else f"{subject_id}@odayplus.com"),
            "name": name or (existing.get("name") if existing else subject_id),
            "roles": validated_roles,
            "scope": updated_scope,
            "attributes": attributes if attributes is not None else (existing.get("attributes", {}) if existing else {}),
            "status": status,
            "updated_at": now_str,
            "updated_by": actor,
            "notes": reason or ("Role assignment update" if existing else "User created"),
        }

        self._users[subject_id] = updated_record

        # Audit Event logging
        action_name = "USER_UPDATED" if existing else "USER_CREATED"
        cid = correlation_id or str(uuid4())
        self._record_audit_event(
            AuditEvent(
                event_type=f"user_role_management.{action_name.lower()}",
                actor=actor,
                action=action_name,
                resource=f"user:{subject_id}",
                outcome="success",
                correlation_id=cid,
                metadata={
                    "subject_id": subject_id,
                    "tenant_id": resolved_tenant,
                    "roles_before": before_state.get("roles") if before_state else None,
                    "roles_after": validated_roles,
                    "scope_before": before_state.get("scope") if before_state else None,
                    "scope_after": updated_scope,
                    "status": status,
                    "reason": reason,
                    "actor_role": actor_role,
                },
            )
        )

        return dict(updated_record)

    def set_user_status(
        self,
        *,
        subject_id: str,
        status: str,
        actor_name: str | None = None,
        reason: str = "",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Activate or deactivate a user account."""
        user = self.get_user(subject_id)
        if status not in {"active", "disabled"}:
            raise UserRolePolicyError("Status must be 'active' or 'disabled'")

        user["status"] = status
        user["updated_at"] = datetime.now(UTC).isoformat()
        user["updated_by"] = actor_name or "system"
        self._users[subject_id] = user

        user_tenant = (user.get("scope") or {}).get("tenant_id", "tenant-default")

        cid = correlation_id or str(uuid4())
        self._record_audit_event(
            AuditEvent(
                event_type="user_role_management.status_updated",
                actor=actor_name or "system",
                action="USER_STATUS_UPDATED",
                resource=f"user:{subject_id}",
                outcome="success",
                correlation_id=cid,
                metadata={
                    "subject_id": subject_id,
                    "tenant_id": user_tenant,
                    "status": status,
                    "reason": reason,
                },
            )
        )
        return dict(user)

    def get_audit_trail(
        self,
        *,
        subject_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return audit trail events recorded by user role management."""
        events: list[dict[str, Any]] = []
        for event in self.audit_log.list_events(tenant_id=tenant_id):
            if event.event_type.startswith("user_role_management."):
                meta = dict(event.metadata or {})
                if subject_id and meta.get("subject_id") != subject_id:
                    continue
                events.append(
                    {
                        "event_type": event.event_type,
                        "action": event.action,
                        "actor": event.actor,
                        "resource": event.resource,
                        "timestamp": event.occurred_at.isoformat(),
                        "metadata": meta,
                        "detail": meta,
                        "correlation_id": event.correlation_id,
                    }
                )
        return events
