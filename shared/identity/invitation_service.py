"""Tenant-bound, pure-admin invitation lifecycle (password-first §7.1/7.3).

Internal application service, NOT a deployment CLI or an authentication path.
The HTTP adapter must supply the principal from the existing auth boundary;
there are no actor/tenant/role inputs in the recipient payload. The service also
rechecks the actor's persisted account, role, credential and session under the
same transaction as invitation issuance/revocation.

No account exists until acceptance. A 256-bit capability is returned once, only
to the authenticated issuer, and persisted only as SHA-256. Callers must keep it
in memory, never logs, URLs, browser storage, receipts or local files. Acceptance
does not sign in, reset an account or create sessions. All inserts and the durable
audit append share the production PostgreSQL engine's transaction.

This deliberately implements only invitations with exactly platform_admin and
the fixed tenant scope; general role/scope invitation editing is out of scope.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from shared.audit import AuditEvent
from shared.auth import Principal, Role
from shared.infrastructure.persistence.audit_log import DurableAuditLog

from .credential_service import CredentialService
from .password_policy import PasswordPolicy

MAX_INVITATION_LIFETIME_SECONDS = 72 * 60 * 60
_SCOPE_AXES = (
    "brand_ids", "region_ids", "store_ids", "assigned_area_ids", "heat_zone_ids", "modules"
)
_USERNAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}\Z")
_EMAIL_RE = re.compile(r"[^@\s]{1,64}@[^@\s]+\.[^@\s]+\Z")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class InvitationRefused(Exception):
    """Safe, static error code; never includes request values or SQL details."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class InvitationIssued:
    invitation_id: str
    tenant_id: str
    expires_at: str
    audit_event_id: str
    token: str = field(repr=False)

    def to_receipt(self) -> dict[str, str]:
        """Identifiers only; deliberately not the transport capability payload."""
        return {
            "status": "invited",
            "invitation_id": self.invitation_id,
            "tenant_id": self.tenant_id,
            "expires_at": self.expires_at,
            "audit_event_id": self.audit_event_id,
        }


@dataclass(frozen=True)
class InvitationAccepted:
    invitation_id: str
    account_id: str
    tenant_id: str
    audit_event_id: str

    def to_receipt(self) -> dict[str, str]:
        return {"status": "accepted", **vars(self)}


def _uuid(value: Any, code: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise InvitationRefused(code) from None


def _scope(tenant: str) -> dict[str, Any]:
    return {"tenant_id": tenant, **{axis: [] for axis in _SCOPE_AXES}, "clearance": "CONFIDENTIAL"}


class InvitationService:
    def __init__(self, *, engine: Any, audit_log: DurableAuditLog) -> None:
        if str(getattr(engine, "dialect", "")).lower() != "postgresql":
            raise ValueError("invitations require PostgreSQL")
        # Otherwise an audit success can survive a rolled-back identity write,
        # or an in-memory sink can manufacture a successful receipt.
        if not isinstance(audit_log, DurableAuditLog) or audit_log._engine is not engine:
            raise ValueError("invitations require the same-engine durable audit log")
        self._engine = engine
        self._audit = audit_log

    def _lock_tenant(self, tenant: str) -> None:
        # Same key as identity user administration, including role/status edits.
        self._engine.execute(
            "SELECT pg_advisory_xact_lock(hashtext(?))", (f"identity-admin:{tenant}",)
        )

    def _now(self) -> Any:
        # Database time is authoritative across Cloud Run instances. Do not use
        # transaction_timestamp(): time may advance while waiting on the lock.
        return datetime.fromisoformat(self._engine.query_one("SELECT clock_timestamp() AS now")["now"])

    def _actor(self, principal: Principal) -> tuple[str, str]:
        if not principal.authenticated or Role.PLATFORM_ADMIN not in principal.roles:
            raise InvitationRefused("INVITATION_ADMIN_REQUIRED")
        actor = _uuid(principal.subject_id, "INVITATION_ADMIN_REQUIRED")
        tenant = _uuid(principal.tenant_id, "INVITATION_TENANT_REQUIRED")
        sid = _uuid(principal.attributes.get("sid"), "INVITATION_SESSION_REQUIRED")
        self._lock_tenant(tenant)
        row = self._engine.query_one(
            "SELECT a.account_id FROM identity.accounts a "
            "JOIN identity.account_roles r ON r.account_id = a.account_id "
            "JOIN identity.password_credentials p ON p.account_id = a.account_id "
            "JOIN identity.sessions s ON s.account_id = a.account_id "
            "WHERE a.account_id = ? AND a.tenant_id = ? AND a.status = 'active' "
            "AND r.role = ? AND p.must_change = false AND s.session_id = ? "
            "AND s.revoked_at IS NULL AND s.idle_expires_at > clock_timestamp() "
            "AND s.absolute_expires_at > clock_timestamp() FOR SHARE OF a, r, p, s",
            (actor, tenant, Role.PLATFORM_ADMIN.value, sid),
        )
        if row is None:
            raise InvitationRefused("INVITATION_ADMIN_SESSION_INVALID")
        return actor, tenant

    def _record(
        self, *, event_type: str, actor: str, invitation_id: str, tenant: str,
        now: Any, account_id: str | None = None, expires_at: datetime | None = None,
    ) -> Any:
        metadata: dict[str, Any] = {"tenant_id": tenant, "invitation_id": invitation_id}
        if expires_at is not None:
            metadata.update({
                "preset_roles": [Role.PLATFORM_ADMIN.value], "preset_scope": _scope(tenant),
                "expires_at": expires_at.isoformat(),
            })
        if account_id is not None:
            metadata.update({
                "account_id": account_id, "subject_id": account_id,
                "roles": [Role.PLATFORM_ADMIN.value], "scope": _scope(tenant),
                "status": "active", "must_change": False,
            })
        return self._audit.record(AuditEvent(
            event_type=event_type, actor=actor, action=event_type.upper().replace(".", "_"),
            resource=f"identity.invitation:{invitation_id}", outcome="success",
            correlation_id=f"identity-invitation-{invitation_id}", occurred_at=now,
            metadata=metadata,
        ))

    def issue(
        self, principal: Principal, *, email: str, lifetime_seconds: int = 3600,
    ) -> InvitationIssued:
        email = email.strip()
        if len(email) > 320 or not _EMAIL_RE.fullmatch(email):
            raise InvitationRefused("INVITATION_EMAIL_INVALID")
        if type(lifetime_seconds) is not int or not 0 < lifetime_seconds <= MAX_INVITATION_LIFETIME_SECONDS:
            raise InvitationRefused("INVITATION_LIFETIME_INVALID")
        token = secrets.token_urlsafe(32)
        invitation_id = str(uuid4())
        with self._engine.lock:
            actor, tenant = self._actor(principal)
            now = self._now()
            if self._engine.query_one(
                "SELECT account_id FROM identity.accounts WHERE tenant_id = ? "
                "AND lower(email) = lower(?) LIMIT 1", (tenant, email),
            ) is not None:
                raise InvitationRefused("INVITATION_ACCOUNT_EXISTS")
            if self._engine.query_one(
                "SELECT invitation_id FROM identity.invitations WHERE tenant_id = ? "
                "AND lower(email) = lower(?) AND accepted_at IS NULL AND revoked_at IS NULL "
                "AND expires_at > clock_timestamp() LIMIT 1", (tenant, email),
            ) is not None:
                # Never rotate an outstanding capability implicitly. A custodian
                # who loses it must explicitly revoke it before issuing another.
                raise InvitationRefused("INVITATION_PENDING_EXISTS")
            expires = now + timedelta(seconds=lifetime_seconds)
            self._engine.execute(
                "INSERT INTO identity.invitations (invitation_id, tenant_id, email, token_hash, "
                "preset_roles, preset_scope, created_by, expires_at) "
                "VALUES (?, ?, ?, ?, CAST(? AS jsonb), CAST(? AS jsonb), ?, ?)",
                (invitation_id, tenant, email, hashlib.sha256(token.encode("ascii")).hexdigest(),
                 json.dumps([Role.PLATFORM_ADMIN.value]), json.dumps(_scope(tenant)), actor, expires),
            )
            event = self._record(
                event_type="identity.account.invite", actor=actor, invitation_id=invitation_id,
                tenant=tenant, now=now, expires_at=expires,
            )
        return InvitationIssued(invitation_id, tenant, expires.isoformat(), event.event_id, token)

    def revoke(self, principal: Principal, *, invitation_id: str) -> dict[str, str]:
        invitation_id = _uuid(invitation_id, "INVITATION_NOT_FOUND")
        with self._engine.lock:
            actor, tenant = self._actor(principal)
            row = self._engine.query_one(
                "SELECT accepted_at, revoked_at FROM identity.invitations "
                "WHERE invitation_id = ? AND tenant_id = ? FOR UPDATE", (invitation_id, tenant),
            )
            if row is None:
                raise InvitationRefused("INVITATION_NOT_FOUND")
            if row["accepted_at"] is not None or row["revoked_at"] is not None:
                raise InvitationRefused("INVITATION_UNAVAILABLE")
            now = self._now()
            self._engine.execute(
                "UPDATE identity.invitations SET revoked_at = ? WHERE invitation_id = ?",
                (now, invitation_id),
            )
            event = self._record(
                event_type="identity.account.invitation_revoked", actor=actor,
                invitation_id=invitation_id, tenant=tenant, now=now,
            )
        return {"status": "revoked", "invitation_id": invitation_id, "audit_event_id": event.event_id}

    def _reserve_acceptance(self, invitation_id: str) -> None:
        """Durable abuse budget before policy/Argon2, independent of consumption.

        Use the dedicated identity.invitation_acceptance_budget, never writing
        identity.login_attempts or changing account sessions. All submissions cost budget,
        including valid capabilities, so possession cannot amplify hashing work.
        Only existing invitation UUIDs get per-invitation rows; random input
        cannot manufacture unbounded throttle rows. Reservations commit even
        when the subsequent acceptance transaction rolls back.
        """
        denied = False
        with self._engine.lock:
            self._engine.execute(
                "SELECT pg_advisory_xact_lock(hashtext(?))", ("identity-invitation-accept-budget",)
            )
            now = self._now()
            budgets = [("invitation-accept:global", 50)]
            try:
                ref = str(UUID(invitation_id))
            except (ValueError, TypeError, AttributeError):
                ref = None
            if ref and self._engine.query_one(
                "SELECT invitation_id FROM identity.invitations WHERE invitation_id = ?", (ref,)
            ) is not None:
                budgets.append(("invitation-accept:" + hashlib.sha256(ref.encode()).hexdigest(), 5))
            for key, limit in budgets:
                self._engine.execute(
                    "INSERT INTO identity.invitation_acceptance_budget (attempt_key, window_started_at, failure_count) "
                    "VALUES (?, ?, 0) ON CONFLICT (attempt_key) DO NOTHING", (key, now),
                )
                row = self._engine.query_one(
                    "SELECT window_started_at, failure_count FROM identity.invitation_acceptance_budget "
                    "WHERE attempt_key = ? FOR UPDATE", (key,),
                )
                start = datetime.fromisoformat(row["window_started_at"])
                count = int(row["failure_count"])
                if now >= start + timedelta(minutes=15):
                    start, count = now, 0
                denied = denied or count >= limit
                self._engine.execute(
                    "UPDATE identity.invitation_acceptance_budget SET window_started_at = ?, failure_count = ? "
                    "WHERE attempt_key = ?", (start, min(count + 1, limit), key),
                )
        # Raise OUTSIDE the reservation transaction, otherwise refusal rolls
        # back its own budget and attackers get unlimited retries.
        if denied:
            raise InvitationRefused("INVITATION_RATE_LIMITED")

    def accept(
        self, *, invitation_id: str, token: str, username: str, password: str,
        display_name: str = "",
    ) -> InvitationAccepted:
        self._reserve_acceptance(invitation_id)
        invitation_id = _uuid(invitation_id, "INVITATION_UNAVAILABLE")
        if not _TOKEN_RE.fullmatch(token):
            raise InvitationRefused("INVITATION_UNAVAILABLE")
        username = username.strip()
        if not _USERNAME_RE.fullmatch(username) or len(display_name) > 255:
            raise InvitationRefused("INVITATION_ACCOUNT_INPUT_INVALID")
        policy = PasswordPolicy()
        normalized = policy.normalize(password)
        if not policy.validate(normalized, username=username).valid:
            raise InvitationRefused("INVITATION_PASSWORD_REJECTED")
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        # Invalid/consumed capabilities must not force a 64 MiB Argon2 job.
        # This inexpensive preflight is NOT the consumption check: expiry,
        # revocation and single-use are rechecked under the write lock below.
        if self._engine.query_one(
            "SELECT invitation_id FROM identity.invitations WHERE invitation_id = ? "
            "AND token_hash = ? AND accepted_at IS NULL AND revoked_at IS NULL "
            "AND expires_at > clock_timestamp()", (invitation_id, token_hash),
        ) is None:
            raise InvitationRefused("INVITATION_UNAVAILABLE")
        # Argon2 outside the critical section; the capability is rechecked below.
        phc = CredentialService().hash_password(normalized)
        params = json.dumps(CredentialService.extract_params_from_phc(phc))
        with self._engine.lock:
            # Read the immutable tenant before taking the shared administration
            # lock; lock ordering matches issue/revoke and avoids deadlocks.
            ref = self._engine.query_one(
                "SELECT tenant_id::text AS tenant_id FROM identity.invitations "
                "WHERE invitation_id = ? AND token_hash = ?", (invitation_id, token_hash),
            )
            if ref is None:
                raise InvitationRefused("INVITATION_UNAVAILABLE")
            tenant = ref["tenant_id"]
            self._lock_tenant(tenant)
            row = self._engine.query_one(
                "SELECT * FROM identity.invitations WHERE invitation_id = ? "
                "AND token_hash = ? AND tenant_id = ? FOR UPDATE", (invitation_id, token_hash, tenant),
            )
            now = self._now()
            if (row is None or row["accepted_at"] is not None or row["revoked_at"] is not None
                    or datetime.fromisoformat(row["expires_at"]) <= now):
                raise InvitationRefused("INVITATION_UNAVAILABLE")
            # Do not reinterpret arbitrary legacy/foreign presets or silently
            # drop business grants. Only this exact bounded lifecycle is accepted.
            # PostgresEngine normalizes JSONB columns to JSON text (the same
            # repository contract used by durable identity administration).
            try:
                preset_roles = json.loads(row["preset_roles"])
                preset_scope = json.loads(row["preset_scope"])
            except (TypeError, ValueError):
                raise InvitationRefused("INVITATION_PRESET_INVALID") from None
            if preset_roles != [Role.PLATFORM_ADMIN.value] or preset_scope != _scope(tenant):
                raise InvitationRefused("INVITATION_PRESET_INVALID")
            if not policy.validate(normalized, username=username, email=row["email"]).valid:
                raise InvitationRefused("INVITATION_PASSWORD_REJECTED")
            if self._engine.query_one(
                "SELECT account_id FROM identity.accounts WHERE tenant_id = ? "
                "AND (lower(username) = lower(?) OR lower(email) = lower(?)) LIMIT 1",
                (tenant, username, row["email"]),
            ) is not None:
                raise InvitationRefused("INVITATION_ACCOUNT_EXISTS")
            account_id = str(uuid4())
            self._engine.execute(
                "INSERT INTO identity.accounts (account_id, tenant_id, username, email, "
                "display_name, status, created_by) VALUES (?, ?, ?, ?, ?, 'active', ?)",
                (account_id, tenant, username, row["email"], display_name.strip(), row["created_by"]),
            )
            self._engine.execute(
                "INSERT INTO identity.password_credentials (account_id, algorithm, phc_hash, "
                "params, must_change, last_rotated_at) VALUES (?, 'argon2id', ?, CAST(? AS jsonb), false, ?)",
                (account_id, phc, params, now),
            )
            self._engine.execute(
                "INSERT INTO identity.account_roles (account_id, role, granted_by) VALUES (?, ?, ?)",
                (account_id, Role.PLATFORM_ADMIN.value, row["created_by"]),
            )
            self._engine.execute(
                "INSERT INTO identity.account_scopes (account_id, clearance) VALUES (?, 'CONFIDENTIAL')",
                (account_id,),
            )
            self._engine.execute(
                "UPDATE identity.invitations SET accepted_at = ? WHERE invitation_id = ?", (now, invitation_id),
            )
            event = self._record(
                event_type="identity.account.accept", actor=account_id, invitation_id=invitation_id,
                tenant=tenant, now=now, account_id=account_id,
            )
        return InvitationAccepted(invitation_id, account_id, tenant, event.event_id)
