"""User & role administration bound to the authoritative identity schema.

Task: ODP-DEV-ADMIN-RELEASE-READINESS-001
Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §2.2, §7.3, §8.1

:class:`~modules.opsboard.application.user_role_management.UserRoleManagementService`
edits a ``users-roles`` document aggregate. Nothing reads that document when a
request is authenticated: the API boundary resolves every password principal
from ``identity.accounts`` / ``identity.account_roles`` /
``identity.account_scopes``. A role change or a disable written only to the
document therefore *looked* successful while the account kept its old
privileges. Live Operator deployments use this service instead, so every
supported user administration operation changes exactly the rows the boundary
reads.

Rules
-----
* Same public surface as the document service so the existing router and UI
  keep working; records are keyed by ``account_id``.
* Tenant isolation: the caller's verified tenant is mandatory and every read and
  write is constrained to it. An account in another tenant is indistinguishable
  from a missing one.
* No account manufacturing: accounts (and their passwords) are created only by
  the deployment bootstrap (and, once implemented, the contract's invitation
  flow). Saving an unknown subject fails.
* One transaction per mutation: the identity rows, session revocation and the
  audit event share ``engine.lock``; if the audit write fails nothing commits.
* Disabling an account revokes all of its sessions immediately (Contract §7.3);
  re-enabling requires ``platform_admin``.
* The last active ``platform_admin`` of a tenant cannot be disabled or lose that
  role, so administration cannot lock itself out.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from shared.audit import AuditEvent
from shared.auth import DataClassification, Role

from .user_role_management import (
    ROLE_LABELS,
    UserNotFound,
    UserRolePolicyError,
)

IDENTITY_EVENT_PREFIX = "identity.account."
_PLACEHOLDER_TENANTS = frozenset({"", "tenant-default"})
_SCOPE_AXES = (
    "brand_ids",
    "region_ids",
    "store_ids",
    "assigned_area_ids",
    "heat_zone_ids",
    "modules",
)


class AccountCreationNotSupported(UserRolePolicyError):
    """Accounts are created by bootstrap/invitation, never by a role save."""


def _uuid_or_none(value: str | None) -> str | None:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _json_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [str(v) for v in value] if isinstance(value, list) else []


class IdentityUserRoleManagementService:
    """Identity-backed implementation of the Operator user administration API."""

    requires_tenant = True

    def __init__(self, *, engine: Any, audit_log: Any) -> None:
        if str(getattr(engine, "dialect", "")).lower() != "postgresql":
            raise ValueError("identity user administration requires the PostgreSQL engine")
        self._engine = engine
        self._audit_log = audit_log

    # ── reads ─────────────────────────────────────────────────────────────

    def _require_tenant(self, tenant_id: str | None) -> str:
        tenant = _uuid_or_none(tenant_id)
        if tenant is None:
            raise UserRolePolicyError("a verified caller tenant is required")
        return tenant

    def _load(self, tenant: str, account_ids: list[str] | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT a.account_id::text AS account_id, a.tenant_id::text AS tenant_id, "
            "a.username, a.email, a.display_name, a.status, a.updated_at, a.created_by, "
            "a.disabled_reason, s.brand_ids, s.region_ids, s.store_ids, s.assigned_area_ids, "
            "s.heat_zone_ids, s.modules, s.clearance "
            "FROM identity.accounts a "
            "LEFT JOIN identity.account_scopes s ON s.account_id = a.account_id "
            "WHERE a.tenant_id = ?"
        )
        params: list[Any] = [tenant]
        if account_ids is not None:
            sql += " AND a.account_id = ANY(CAST(? AS uuid[]))"
            params.append(account_ids)
        sql += " ORDER BY lower(a.username)"
        rows = self._engine.query(sql, tuple(params))
        if not rows:
            return []
        role_rows = self._engine.query(
            "SELECT account_id::text AS account_id, role FROM identity.account_roles "
            "WHERE account_id = ANY(CAST(? AS uuid[])) ORDER BY role",
            ([r["account_id"] for r in rows],),
        )
        roles: dict[str, list[str]] = {}
        for row in role_rows:
            roles.setdefault(row["account_id"], []).append(str(row["role"]))
        return [self._to_record(row, roles.get(row["account_id"], [])) for row in rows]

    @staticmethod
    def _to_record(row: dict[str, Any], roles: list[str]) -> dict[str, Any]:
        updated = row.get("updated_at")
        scope: dict[str, Any] = {"tenant_id": row["tenant_id"]}
        for axis in _SCOPE_AXES:
            scope[axis] = _json_list(row.get(axis))
        scope["clearance"] = row.get("clearance") or DataClassification.CONFIDENTIAL.name
        return {
            "subject_id": row["account_id"],
            "username": row["username"],
            "email": row["email"],
            "name": row.get("display_name") or row["username"],
            "roles": roles,
            "scope": scope,
            "attributes": {"identity_source": "identity.accounts", "username": row["username"]},
            "status": row["status"],
            "updated_at": updated.isoformat() if isinstance(updated, datetime) else updated,
            "updated_by": row.get("created_by"),
            "notes": row.get("disabled_reason") or "",
        }

    def _find(self, tenant: str, subject_id: str) -> dict[str, Any]:
        subject = (subject_id or "").strip()
        account_id = _uuid_or_none(subject)
        if account_id is None:
            row = self._engine.query_one(
                "SELECT account_id::text AS account_id FROM identity.accounts "
                "WHERE tenant_id = ? AND lower(username) = lower(?)",
                (tenant, subject),
            )
            account_id = row["account_id"] if row else None
        records = self._load(tenant, [account_id]) if account_id else []
        if not records:
            raise UserNotFound(f"User with subject_id '{subject}' not found")
        return records[0]

    def list_users(
        self, *, status_filter: str | None = None, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        users = self._load(self._require_tenant(tenant_id))
        if status_filter:
            users = [u for u in users if u["status"] == status_filter]
        return users

    def get_user(self, subject_id: str, *, tenant_id: str | None = None) -> dict[str, Any]:
        return self._find(self._require_tenant(tenant_id), subject_id)

    def list_roles(self) -> list[dict[str, Any]]:
        return [
            {
                "role_id": role.value,
                "label": ROLE_LABELS.get(role.value, role.value),
                "description": f"Canonical role: {role.value}",
            }
            for role in Role
        ]

    def get_audit_trail(
        self, *, subject_id: str | None = None, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        tenant = self._require_tenant(tenant_id)
        events: list[dict[str, Any]] = []
        for event in self._audit_log.list_events(tenant_id=tenant):
            if not event.event_type.startswith(IDENTITY_EVENT_PREFIX):
                continue
            meta = dict(event.metadata or {})
            if subject_id and subject_id not in {meta.get("subject_id"), meta.get("account_id")}:
                continue
            events.append(
                {
                    "event_id": event.event_id,
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

    # ── writes ────────────────────────────────────────────────────────────

    def _lock_tenant(self, tenant: str) -> None:
        # Serialises concurrent administration of one tenant across instances so
        # the last-admin check cannot be raced.
        self._engine.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"identity-admin:{tenant}",))

    def _other_active_admins(self, tenant: str, account_id: str) -> int:
        row = self._engine.query_one(
            "SELECT count(*) AS n FROM identity.accounts a "
            "JOIN identity.account_roles r ON r.account_id = a.account_id "
            "WHERE a.tenant_id = ? AND a.status = 'active' AND r.role = ? AND a.account_id <> ?",
            (tenant, Role.PLATFORM_ADMIN.value, account_id),
        )
        return int(row["n"]) if row else 0

    def _revoke_sessions(self, account_id: str, reason: str, now: datetime) -> int:
        result = self._engine.execute(
            "UPDATE identity.sessions SET revoked_at = ?, revoked_reason = ? "
            "WHERE account_id = ? AND revoked_at IS NULL",
            (now, reason, account_id),
        )
        return max(int(result.rowcount), 0)

    def _apply_status(
        self,
        before: dict[str, Any],
        status: str,
        *,
        tenant: str,
        reason: str,
        actor_roles: frozenset[str],
        now: datetime,
    ) -> int:
        account_id = before["subject_id"]
        if status == before["status"]:
            return 0
        if status == "disabled":
            if Role.PLATFORM_ADMIN.value in before["roles"] and before["status"] == "active":
                if self._other_active_admins(tenant, account_id) == 0:
                    raise UserRolePolicyError(
                        "Cannot disable the last active platform_admin of this tenant."
                    )
            self._engine.execute(
                "UPDATE identity.accounts SET status = 'disabled', disabled_at = ?, "
                "disabled_reason = ? WHERE account_id = ?",
                (now, reason or "disabled by administrator", account_id),
            )
            return self._revoke_sessions(account_id, "admin_disable_account", now)
        if status == "active":
            if Role.PLATFORM_ADMIN.value not in actor_roles:
                raise UserRolePolicyError("Re-enabling an account requires platform_admin.")
            if before["status"] != "disabled":
                # invited/locked accounts are activated by their own flows
                # (invitation acceptance / lockout expiry), not by an admin toggle.
                raise UserRolePolicyError(
                    f"Cannot activate an account in status '{before['status']}'."
                )
            self._engine.execute(
                "UPDATE identity.accounts SET status = 'active', disabled_at = NULL, "
                "disabled_reason = NULL WHERE account_id = ?",
                (account_id,),
            )
            return 0
        raise UserRolePolicyError("Status must be 'active' or 'disabled'")

    def _record(
        self,
        *,
        event_type: str,
        action: str,
        actor: str,
        account_id: str,
        correlation_id: str | None,
        metadata: dict[str, Any],
        now: datetime,
    ) -> None:
        self._audit_log.record(
            AuditEvent(
                event_type=event_type,
                actor=actor,
                action=action,
                resource=f"identity.account:{account_id}",
                outcome="success",
                correlation_id=correlation_id or f"identity-admin-{account_id}",
                occurred_at=now,
                metadata={"subject_id": account_id, "account_id": account_id, **metadata},
            )
        )

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
        actor_roles: frozenset[str] | None = None,
        reason: str = "",
        correlation_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Replace an existing account's roles/scope (and optionally status)."""

        del attributes  # identity has no free-form attribute store
        tenant = self._require_tenant(tenant_id)
        valid = {r.value for r in Role}
        new_roles: list[str] = []
        for raw in roles:
            role = str(raw).strip()
            if role not in valid:
                raise UserRolePolicyError(f"Invalid role '{role}'. Must be one of canonical roles.")
            if role not in new_roles:
                new_roles.append(role)
        if not new_roles:
            raise UserRolePolicyError("At least one valid role must be assigned to user")
        if status not in {"active", "disabled"}:
            raise UserRolePolicyError("Status must be 'active' or 'disabled'")

        requested = dict(scope) if scope is not None else None
        if requested is not None and "tenant_id" in requested:
            scope_tenant = str(requested.get("tenant_id") or "").strip()
            if scope_tenant not in _PLACEHOLDER_TENANTS and _uuid_or_none(scope_tenant) != tenant:
                raise UserRolePolicyError(
                    f"Cannot save user scope for tenant '{scope_tenant}'; caller is restricted to its own tenant."
                )
        if requested is not None and "clearance" in requested and requested.get("clearance") is not None:
            clearance_input = str(requested.get("clearance")).upper()
            if clearance_input not in DataClassification.__members__:
                raise UserRolePolicyError(f"Invalid clearance '{clearance_input}'.")

        actor = actor_name or "operator"
        now = datetime.now(UTC)
        with self._engine.lock:
            self._lock_tenant(tenant)
            try:
                before = self._find(tenant, subject_id)
            except UserNotFound as exc:
                raise AccountCreationNotSupported(
                    "Accounts are created only through the identity bootstrap or invitation flow; "
                    f"no account '{subject_id}' exists in this tenant."
                ) from exc
            account_id = before["subject_id"]
            if (
                Role.PLATFORM_ADMIN.value in before["roles"]
                and Role.PLATFORM_ADMIN.value not in new_roles
                and before["status"] == "active"
                and self._other_active_admins(tenant, account_id) == 0
            ):
                raise UserRolePolicyError(
                    "Cannot remove platform_admin from the last active platform_admin of this tenant."
                )

            # Resolve clearance: explicit requested clearance wins; otherwise preserve before-state
            if requested is not None and "clearance" in requested and requested.get("clearance") is not None:
                clearance = str(requested.get("clearance")).upper()
            else:
                clearance = str(before.get("scope", {}).get("clearance") or DataClassification.CONFIDENTIAL.name).upper()

            # Merge omitted axes from authoritative before-state transactionally
            before_scope = before.get("scope") or {}
            axes: dict[str, list[str]] = {}
            for axis in _SCOPE_AXES:
                if requested is not None and axis in requested and requested[axis] is not None:
                    axes[axis] = sorted({str(v) for v in requested[axis]})
                else:
                    axes[axis] = sorted({str(v) for v in (before_scope.get(axis) or [])})

            self._engine.execute("DELETE FROM identity.account_roles WHERE account_id = ?", (account_id,))
            for role in new_roles:
                self._engine.execute(
                    "INSERT INTO identity.account_roles (account_id, role, granted_at, granted_by) "
                    "VALUES (?, ?, ?, ?)",
                    (account_id, role, now, actor),
                )
            self._engine.execute(
                "INSERT INTO identity.account_scopes (account_id, brand_ids, region_ids, store_ids, "
                "assigned_area_ids, heat_zone_ids, modules, clearance) VALUES (?, "
                "CAST(? AS jsonb), CAST(? AS jsonb), CAST(? AS jsonb), CAST(? AS jsonb), "
                "CAST(? AS jsonb), CAST(? AS jsonb), ?) "
                "ON CONFLICT (account_id) DO UPDATE SET brand_ids = EXCLUDED.brand_ids, "
                "region_ids = EXCLUDED.region_ids, store_ids = EXCLUDED.store_ids, "
                "assigned_area_ids = EXCLUDED.assigned_area_ids, heat_zone_ids = EXCLUDED.heat_zone_ids, "
                "modules = EXCLUDED.modules, clearance = EXCLUDED.clearance",
                (account_id, *(json.dumps(axes[a]) for a in _SCOPE_AXES), clearance),
            )
            if name is not None and name.strip() and name.strip() != before["name"]:
                self._engine.execute(
                    "UPDATE identity.accounts SET display_name = ? WHERE account_id = ?",
                    (name.strip()[:255], account_id),
                )
            revoked = self._apply_status(
                {**before, "roles": new_roles},
                status,
                tenant=tenant,
                reason=reason,
                actor_roles=actor_roles or frozenset(),
                now=now,
            )
            after = self._find(tenant, account_id)
            self._record(
                event_type=f"{IDENTITY_EVENT_PREFIX}roles_updated",
                action="IDENTITY_ACCOUNT_ROLES_UPDATED",
                actor=actor,
                account_id=account_id,
                correlation_id=correlation_id,
                now=now,
                metadata={
                    "tenant_id": tenant,
                    "roles_before": before["roles"],
                    "roles_after": after["roles"],
                    "scope_before": before["scope"],
                    "scope_after": after["scope"],
                    "status_before": before["status"],
                    "status": after["status"],
                    "sessions_revoked": revoked,
                    "reason": reason,
                    "actor_role": actor_role,
                },
            )
        return after

    def set_user_status(
        self,
        *,
        subject_id: str,
        status: str,
        actor_name: str | None = None,
        actor_roles: frozenset[str] | None = None,
        reason: str = "",
        correlation_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Disable (revoking every session) or re-enable an account."""

        tenant = self._require_tenant(tenant_id)
        if status not in {"active", "disabled"}:
            raise UserRolePolicyError("Status must be 'active' or 'disabled'")
        actor = actor_name or "operator"
        now = datetime.now(UTC)
        with self._engine.lock:
            self._lock_tenant(tenant)
            before = self._find(tenant, subject_id)
            revoked = self._apply_status(
                before,
                status,
                tenant=tenant,
                reason=reason,
                actor_roles=actor_roles or frozenset(),
                now=now,
            )
            after = self._find(tenant, before["subject_id"])
            self._record(
                event_type=f"{IDENTITY_EVENT_PREFIX}{'disabled' if status == 'disabled' else 'enabled'}",
                action="IDENTITY_ACCOUNT_STATUS_UPDATED",
                actor=actor,
                account_id=before["subject_id"],
                correlation_id=correlation_id,
                now=now,
                metadata={
                    "tenant_id": tenant,
                    "status_before": before["status"],
                    "status": after["status"],
                    "sessions_revoked": revoked,
                    "reason": reason,
                },
            )
        return after


class UnavailableUserRoleManagementService:
    """Live fallback when identity persistence is not wired: refuse, never fake.

    A live Operator deployment without the PostgreSQL identity schema has no
    authoritative account store, so a document-only edit would report success
    for a privilege change that never takes effect.
    """

    requires_tenant = True

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        from fastapi import HTTPException, status

        def unavailable(*_args: Any, **_kwargs: Any) -> Any:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "IDENTITY_PERSISTENCE_UNAVAILABLE",
                    "message": (
                        "live user administration requires the PostgreSQL identity schema; "
                        "document-only role changes are refused"
                    ),
                },
            )

        return unavailable


__all__ = [
    "AccountCreationNotSupported",
    "IdentityUserRoleManagementService",
    "UnavailableUserRoleManagementService",
]
