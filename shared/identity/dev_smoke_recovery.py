"""Internal append-only root for the separately authorized dev-smoke recovery.

Incremental domain layer ONLY: not mounted, not a foreground executor, and not
permission for credential/secret effects. Hash-only capability/atomic rotation
are internal domain operations; transport/gate integration remains incomplete.
No reset/resume/re-reservation exists. The old creation journal stays immutable.
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
from shared.auth import Principal
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.postgresql import PostgresEngine

from .dev_smoke_journal import (
    PRESERVED_ACCOUNT_ID,
    REPOSITORY,
    TENANT_ID,
    DevSmokeBindingJournal,
    ProvisioningJournal,
    _CUSTODIAN,
    _DIGEST,
    _SHA,
    _aware,
)
from .credential_service import CredentialService
from .password_policy import PasswordPolicy
from .invitation_provenance import invitation_provenance
from .invitation_service import InvitationService, _scope

AUTHORIZATION_ID = "HUMAN-ODP-DEV-SMOKE-RECOVERY-20261011-001"
TARGET_ACCOUNT_ID = "13faae19-21c6-4663-8e89-b93ea7f1107d"
OLD_EXECUTION_ID = "89dad153-138f-4541-8bd2-e972d59cce08"
OLD_PLAN_DIGEST = "sha256:d466580ea9c4b941b92e302669db5777ebf746c6b06e8efb9c812fbc69d581c9"
OLD_QUARANTINE_EVENT_ID = "019e39ed-27f9-4a33-bc99-9bd0e561e576"
INVITATION_ID = "1a012edb-842b-4846-8699-6ebd4e350218"
ISSUE_EVENT_ID = "0b85d791-129a-4dab-8c51-0978c4111a12"
ACCEPT_EVENT_ID = "a9c756a7-cdf6-48f3-9bd9-7c9437958939"
_PLAN_KEYS = frozenset({
    "authorization_id", "execution_id", "repository", "environment", "release_profile",
    "release_sha", "manifest_digest", "tenant_id", "actor_account_id", "target_account_id",
    "old_execution_id", "old_plan_digest", "old_quarantine_event_id", "invitation_id",
    "issue_event_id", "accept_event_id", "custodian", "expires_at",
})
_FIXED_PLAN = {
    "authorization_id": AUTHORIZATION_ID, "repository": REPOSITORY,
    "environment": "dev", "release_profile": "dev-admin", "tenant_id": TENANT_ID,
    "actor_account_id": PRESERVED_ACCOUNT_ID, "target_account_id": TARGET_ACCOUNT_ID,
    "old_execution_id": OLD_EXECUTION_ID, "old_plan_digest": OLD_PLAN_DIGEST,
    "old_quarantine_event_id": OLD_QUARANTINE_EVENT_ID, "invitation_id": INVITATION_ID,
    "issue_event_id": ISSUE_EVENT_ID, "accept_event_id": ACCEPT_EVENT_ID,
}


class RecoveryRefused(Exception):
    """Static codes only; never include arbitrary request or database values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()


def validate_recovery_plan(
    plan: Any, *, release_sha: str, manifest_digest: str, now: datetime,
) -> str:
    """Validate NON-SECRET shape/scope only, not human/source/admission authority."""
    try:
        if (type(plan) is not dict or set(plan) != _PLAN_KEYS
                or any(type(v) is not str or not 0 < len(v) <= 320 for v in plan.values())
                or any(plan[k] != v for k, v in _FIXED_PLAN.items())
                or type(release_sha) is not str or not _SHA.fullmatch(release_sha)
                or type(manifest_digest) is not str or not _DIGEST.fullmatch(manifest_digest)
                or plan["release_sha"] != release_sha or plan["manifest_digest"] != manifest_digest
                or not _CUSTODIAN.fullmatch(plan["custodian"]) or not _aware(now)):
            raise ValueError
        execution = UUID(plan["execution_id"])
        expires = datetime.fromisoformat(plan["expires_at"])
        if (not execution.int or str(execution) != plan["execution_id"]
                or plan["execution_id"] == OLD_EXECUTION_ID or not _aware(expires)
                or not now < expires <= now + timedelta(hours=1)):
            raise ValueError
        return _digest(plan)
    except Exception:
        raise RecoveryRefused("RECOVERY_PLAN_INVALID") from None


@dataclass(frozen=True)
class RecoveryReservation:
    execution_id: str
    plan_digest: str
    event_id: str
    stage: str

    def to_receipt(self) -> dict[str, Any]:
        return {**vars(self), "authorization_id": AUTHORIZATION_ID,
                "execution_authorized": False, "credential_binding_verified": False,
                "secret_values_redacted": True}


class RecoveryJournal:
    """Server-side root reservation, inspection and one-way quarantine.

    The caller supplies an already authenticated principal from the canonical
    boundary; this component revalidates persisted ORIGINAL account/tenant/session
    on every call. A plan is NOT authenticated authorization. Before eventual
    mounting, the trusted foreground must separately pin the real user receipt,
    exact reviewed execution plan, source/CI/admission and custodian. Runtime
    tuple arguments come from server configuration, never request body/headers.
    """

    _CORRELATION = f"dev-smoke-recovery:{AUTHORIZATION_ID}"
    _TYPE = "identity.dev_smoke.recovery"
    _ACTION = "DEV_SMOKE_RECOVERY"
    _KEYS = _PLAN_KEYS | {
        "plan_digest", "issuer_session_id", "original_identity_digest", "target_identity_digest",
        "original_credential_updated_at", "target_credential_updated_at", "stage", "execution_authorized", "credential_binding_verified", "secret_values_redacted",
    }

    def __init__(
        self, *, engine: PostgresEngine, audit_log: DurableAuditLog,
        environment: str, release_profile: str, release_sha: str, manifest_digest: str,
    ) -> None:
        if (not isinstance(engine, PostgresEngine) or not isinstance(audit_log, DurableAuditLog)
                or audit_log._engine is not engine):
            raise RecoveryRefused("RECOVERY_SAME_ENGINE_REQUIRED")
        if (environment != "dev" or release_profile != "dev-admin"
                or type(release_sha) is not str or not _SHA.fullmatch(release_sha)
                or type(manifest_digest) is not str or not _DIGEST.fullmatch(manifest_digest)):
            raise RecoveryRefused("RECOVERY_RUNTIME_REFUSED")
        self._engine, self._audit = engine, audit_log
        self._release_sha, self._manifest_digest = release_sha, manifest_digest
        self._identity = InvitationService(engine=engine, audit_log=audit_log)

    def _actor(self, principal: Principal) -> str:
        # Reject foreign claims BEFORE locking a caller-selected tenant.
        if (not isinstance(principal, Principal) or not principal.authenticated
                or principal.subject_id != PRESERVED_ACCOUNT_ID or principal.tenant_id != TENANT_ID):
            raise RecoveryRefused("RECOVERY_ORIGINAL_ADMIN_REQUIRED")
        actor, tenant = self._identity._actor(principal)
        original = self._account(actor)
        if (actor != PRESERVED_ACCOUNT_ID or tenant != TENANT_ID
                or original["username"] != "ajoe734"
                or original["roles"] != ["auditor", "platform_admin"]):
            raise RecoveryRefused("RECOVERY_ORIGINAL_ADMIN_REQUIRED")
        return str(UUID(principal.attributes["sid"]))

    def _account(self, account_id: str) -> dict[str, Any]:
        row = self._engine.query_one(
            "SELECT a.account_id::text AS subject_id, a.tenant_id::text AS tenant_id, "
            "a.username, a.email, a.display_name, a.status, a.created_by AS updated_by, "
            "s.brand_ids, s.region_ids, s.store_ids, s.assigned_area_ids, s.heat_zone_ids, "
            "s.modules, s.clearance FROM identity.accounts a "
            "JOIN identity.account_scopes s ON s.account_id = a.account_id "
            "WHERE a.account_id = ? FOR SHARE OF a, s", (account_id,),
        )
        roles = self._engine.query(
            "SELECT role FROM identity.account_roles WHERE account_id = ? ORDER BY role FOR SHARE",
            (account_id,),
        )
        credential = self._engine.query_one(
            "SELECT account_id FROM identity.password_credentials WHERE account_id = ? "
            "AND algorithm = 'argon2id' AND must_change = false FOR SHARE", (account_id,),
        )
        if row is None or credential is None:
            raise RecoveryRefused("RECOVERY_IDENTITY_DRIFT")
        scope = {"tenant_id": row["tenant_id"], "clearance": row["clearance"]}
        for axis in ("brand_ids", "region_ids", "store_ids", "assigned_area_ids", "heat_zone_ids", "modules"):
            scope[axis] = json.loads(row[axis])
        if row["tenant_id"] != TENANT_ID or row["status"] != "active" or scope != _scope(TENANT_ID):
            raise RecoveryRefused("RECOVERY_IDENTITY_DRIFT")
        return {k: row[k] for k in ("subject_id", "username", "email", "display_name", "status", "updated_by")} | {
            "roles": [r["role"] for r in roles], "scope": scope,
        }

    def _credential_times(self) -> tuple[str, str]:
        times = [self._engine.query_one(
            "SELECT updated_at FROM identity.password_credentials WHERE account_id = ? FOR SHARE",
            (account,),
        )["updated_at"] for account in (PRESERVED_ACCOUNT_ID, TARGET_ACCOUNT_ID)]
        return times[0], times[1]

    def _lock(self) -> None:
        # Always after the shared tenant administration lock acquired by _actor.
        self._engine.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (self._CORRELATION,))

    def _events(self) -> list[AuditEvent]:
        events = self._audit.list_events(correlation_id=self._CORRELATION)
        if len(events) > 2:
            raise RecoveryRefused("RECOVERY_JOURNAL_INVALID")
        for index, event in enumerate(events):
            m = event.metadata
            if (event.event_type != self._TYPE or event.actor != PRESERVED_ACCOUNT_ID
                    or event.action != self._ACTION or event.resource != self._CORRELATION
                    or event.outcome != "success" or set(m) != self._KEYS
                    or any(m[k] != v for k, v in _FIXED_PLAN.items())
                    or m["stage"] != ("reserved" if index == 0 else "recovery-required")
                    or m["execution_authorized"] is not False or m["credential_binding_verified"] is not False
                    or m["secret_values_redacted"] is not True):
                raise RecoveryRefused("RECOVERY_JOURNAL_INVALID")
            plan = {k: m[k] for k in _PLAN_KEYS}
            validate_recovery_plan(plan, release_sha=m["release_sha"], manifest_digest=m["manifest_digest"],
                                   now=events[0].occurred_at)
            if (m["plan_digest"] != _digest(plan)
                    or any(not _DIGEST.fullmatch(m[k]) for k in ("original_identity_digest", "target_identity_digest"))
                    or str(UUID(m["issuer_session_id"])) != m["issuer_session_id"]
                    or not UUID(m["issuer_session_id"]).int
                    or any(not _aware(datetime.fromisoformat(m[k])) for k in (
                        "original_credential_updated_at", "target_credential_updated_at"))
                    or (index and {k: v for k, v in m.items() if k != "stage"} != {
                        k: v for k, v in events[0].metadata.items() if k != "stage"})):
                raise RecoveryRefused("RECOVERY_JOURNAL_INVALID")
        return events

    def _old_lineage(self, target: dict[str, Any]) -> None:
        old = ProvisioningJournal(engine=self._engine, audit_log=self._audit)
        self._engine.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (old._CORRELATION,))
        events = old._events()
        if (len(events) != 2 or events[-1].event_id != OLD_QUARANTINE_EVENT_ID
                or events[-1].metadata["execution_id"] != OLD_EXECUTION_ID
                or events[-1].metadata["plan_digest"] != OLD_PLAN_DIGEST
                or DevSmokeBindingJournal(journal=old).inspect() is not None):
            raise RecoveryRefused("RECOVERY_OLD_LINEAGE_INVALID")
        invitation = self._engine.query_one(
            "SELECT email, created_by, accepted_at, revoked_at, preset_roles, preset_scope "
            "FROM identity.invitations WHERE invitation_id = ? AND tenant_id = ? FOR SHARE",
            (INVITATION_ID, TENANT_ID),
        )
        if (invitation is None or invitation["email"] != target["email"]
                or invitation["created_by"] != PRESERVED_ACCOUNT_ID
                or invitation["accepted_at"] is None or invitation["revoked_at"] is not None
                or json.loads(invitation["preset_roles"]) != ["platform_admin"]
                or json.loads(invitation["preset_scope"]) != _scope(TENANT_ID)):
            raise RecoveryRefused("RECOVERY_INVITATION_PROVENANCE_INVALID")
        projected = [{"event_id": e.event_id, "event_type": e.event_type, "actor": e.actor,
                      "resource": e.resource, "outcome": e.outcome, "correlation_id": e.correlation_id,
                      "timestamp": e.occurred_at.isoformat(), "metadata": e.metadata}
                     for e in self._audit.list_events(tenant_id=TENANT_ID)]
        if invitation_provenance(target, projected) != {
            "invitation_id": INVITATION_ID, "issue_event_id": ISSUE_EVENT_ID,
            "accept_event_id": ACCEPT_EVENT_ID, "issuer_account_id": PRESERVED_ACCOUNT_ID,
        }:
            raise RecoveryRefused("RECOVERY_INVITATION_PROVENANCE_INVALID")

    @staticmethod
    def _receipt(event: AuditEvent) -> RecoveryReservation:
        m = event.metadata
        return RecoveryReservation(m["execution_id"], m["plan_digest"], event.event_id, m["stage"])

    def reserve(self, principal: Principal, plan: Any) -> RecoveryReservation:
        try:
            with self._engine.lock:
                sid = self._actor(principal)
                self._lock()
                if self._events():
                    raise RecoveryRefused("RECOVERY_ALREADY_RESERVED")
                now = self._identity._now()
                digest = validate_recovery_plan(plan, release_sha=self._release_sha,
                                                manifest_digest=self._manifest_digest, now=now)
                original = self._account(PRESERVED_ACCOUNT_ID)
                target = self._account(TARGET_ACCOUNT_ID)
                if target["username"] != "odp-dev-smoke" or target["roles"] != ["platform_admin"]:
                    raise RecoveryRefused("RECOVERY_IDENTITY_DRIFT")
                self._old_lineage(target)
                original_time, target_time = self._credential_times()
                event = self._audit.record(AuditEvent(
                    event_type=self._TYPE, actor=PRESERVED_ACCOUNT_ID, action=self._ACTION,
                    resource=self._CORRELATION, correlation_id=self._CORRELATION,
                    outcome="success", occurred_at=now, metadata={
                        **plan, "plan_digest": digest, "issuer_session_id": sid,
                        "original_identity_digest": _digest(original), "target_identity_digest": _digest(target),
                        "original_credential_updated_at": original_time,
                        "target_credential_updated_at": target_time, "stage": "reserved", "execution_authorized": False,
                        "credential_binding_verified": False, "secret_values_redacted": True,
                    },
                ))
            return self._receipt(event)  # only after transaction commit
        except RecoveryRefused:
            raise
        except Exception:
            raise RecoveryRefused("RECOVERY_JOURNAL_UNAVAILABLE") from None

    def inspect(self, principal: Principal) -> RecoveryReservation | None:
        try:
            with self._engine.lock:
                self._actor(principal)
                self._lock()
                events = self._events()
                return self._receipt(events[-1]) if events else None
        except RecoveryRefused:
            raise
        except Exception:
            raise RecoveryRefused("RECOVERY_JOURNAL_UNAVAILABLE") from None

    def quarantine(self, principal: Principal, reservation: RecoveryReservation) -> RecoveryReservation:
        """One-way append, even after expiry; no target reset or old journal edit.

        A fresh valid original-admin session may record quarantine after the
        execution-owned session is lost. It may NOT resume/issue/rotate anything.
        Target/source drift must not prevent recording uncertainty.
        """
        try:
            with self._engine.lock:
                self._actor(principal)
                self._lock()
                events = self._events()
                if (len(events) != 1 or not isinstance(reservation, RecoveryReservation)
                        or reservation != self._receipt(events[0])):
                    raise RecoveryRefused("RECOVERY_QUARANTINE_REFUSED")
                event = self._audit.record(AuditEvent(
                    event_type=self._TYPE, actor=PRESERVED_ACCOUNT_ID, action=self._ACTION,
                    resource=self._CORRELATION, correlation_id=self._CORRELATION,
                    outcome="success", occurred_at=self._identity._now(),
                    metadata={**events[0].metadata, "stage": "recovery-required"},
                ))
            return self._receipt(event)
        except RecoveryRefused:
            raise
        except Exception:
            raise RecoveryRefused("RECOVERY_JOURNAL_UNAVAILABLE") from None


@dataclass(frozen=True)
class RecoveryCapability:
    capability_id: str
    execution_id: str
    expires_at: str
    audit_event_id: str
    token: str = field(repr=False)

    def to_receipt(self) -> dict[str, str]:
        return {k: getattr(self, k) for k in (
            "capability_id", "execution_id", "expires_at", "audit_event_id",
        )}


class RecoveryCapabilityService(RecoveryJournal):
    """Unmounted internal single-account rotation; never a generic reset API.

    One issuance and at most five consume submissions for this authorization.
    Persist only independent capability SHA-256 in dedicated credential storage.
    The mounted adapter must return the token once with no-store, auth/CSRF and
    bounded body, without logging it. Root/session/tuple/identities revalidated
    before hashing and under the final transaction. No retry/resume method.
    """

    _CAP_CORRELATION = f"dev-smoke-recovery-capability:{AUTHORIZATION_ID}"
    _CAP_TYPE = "identity.dev_smoke.recovery_capability"
    _TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
    _TABLE = "identity.dev_smoke_recovery_capabilities"

    def _top_level(self) -> None:
        # PostgresEngine nests by joining the existing transaction. A caller
        # transaction would roll back the abuse budget or expose an uncommitted
        # successful receipt; prohibit it rather than pretend it is independent.
        if getattr(self._engine._local, "connection", None) is not None:
            raise RecoveryRefused("RECOVERY_OUTERMOST_TRANSACTION_REQUIRED")

    def _active(self, principal: Principal, reservation: RecoveryReservation) -> AuditEvent:
        sid = self._actor(principal)
        self._lock()
        events = self._events()
        if (len(events) != 1 or not isinstance(reservation, RecoveryReservation)
                or reservation != self._receipt(events[0])):
            raise RecoveryRefused("RECOVERY_ROOT_UNAVAILABLE")
        root = events[0]
        m = root.metadata
        if (sid != m["issuer_session_id"] or m["release_sha"] != self._release_sha
                or m["manifest_digest"] != self._manifest_digest
                or self._identity._now() >= datetime.fromisoformat(m["expires_at"])):
            raise RecoveryRefused("RECOVERY_EXECUTION_DRIFT")
        # Lock credential updates before the baseline read; self-password-change
        # writers need not share the tenant advisory lock. No PHC enters audit.
        self._engine.query_one(
            "SELECT account_id FROM identity.password_credentials WHERE account_id = ? FOR UPDATE",
            (TARGET_ACCOUNT_ID,),
        )
        original, target = self._account(PRESERVED_ACCOUNT_ID), self._account(TARGET_ACCOUNT_ID)
        if (_digest(original) != m["original_identity_digest"]
                or _digest(target) != m["target_identity_digest"]
                or self._credential_times() != (m["original_credential_updated_at"], m["target_credential_updated_at"])):
            raise RecoveryRefused("RECOVERY_IDENTITY_DRIFT")
        self._old_lineage(target)
        return root

    def _cap_events(self) -> list[AuditEvent]:
        return self._audit.list_events(correlation_id=self._CAP_CORRELATION)

    def _record_cap(self, root: AuditEvent, *, capability_id: str, expires: datetime,
                    stage: str, now: datetime, issue_event_id: str = "",
                    revoked_count: int = 0) -> AuditEvent:
        # Only fixed safe lineage and counts; no token/hash/PHC/password.
        metadata = {k: root.metadata[k] for k in _PLAN_KEYS}
        metadata.update({
            "plan_digest": root.metadata["plan_digest"], "root_event_id": root.event_id,
            "issuer_session_id": root.metadata["issuer_session_id"],
            "capability_id": capability_id, "capability_expires_at": expires.isoformat(),
            "stage": stage, "issue_event_id": issue_event_id,
            "revoked_target_sessions": revoked_count, "execution_authorized": False,
            "credential_binding_verified": False, "secret_values_redacted": True,
        })
        return self._audit.record(AuditEvent(
            event_type=self._CAP_TYPE, actor=PRESERVED_ACCOUNT_ID,
            action="DEV_SMOKE_RECOVERY_CAPABILITY", resource=self._CAP_CORRELATION,
            correlation_id=self._CAP_CORRELATION, outcome="success", occurred_at=now,
            metadata=metadata,
        ))

    def issue(self, principal: Principal, reservation: RecoveryReservation, *,
              lifetime_seconds: int = 600) -> RecoveryCapability:
        self._top_level()
        try:
            with self._engine.lock:
                root = self._active(principal, reservation)
                if type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 600:
                    raise RecoveryRefused("RECOVERY_CAPABILITY_LIFETIME_INVALID")
                if self._cap_events() or self._engine.query_one(
                        f"SELECT capability_id FROM {self._TABLE} WHERE authorization_id = ?",
                        (AUTHORIZATION_ID,)) is not None:
                    raise RecoveryRefused("RECOVERY_CAPABILITY_ALREADY_ISSUED")
                now = self._identity._now()
                expires = min(now + timedelta(seconds=lifetime_seconds),
                              datetime.fromisoformat(root.metadata["expires_at"]))
                if expires <= now:
                    raise RecoveryRefused("RECOVERY_EXECUTION_DRIFT")
                original_time, target_time = self._credential_times()
                token, cap_id = secrets.token_urlsafe(32), str(uuid4())
                event = self._record_cap(root, capability_id=cap_id, expires=expires,
                                         stage="issued", now=now)
                self._engine.execute(
                    f"INSERT INTO {self._TABLE} (authorization_id, capability_id, execution_id, "
                    "plan_digest, root_event_id, issue_event_id, tenant_id, target_account_id, "
                    "issuer_account_id, issuer_session_id, token_hash, issued_at, expires_at, "
                    "original_credential_updated_at, target_credential_updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (AUTHORIZATION_ID, cap_id, reservation.execution_id, reservation.plan_digest,
                     root.event_id, event.event_id, TENANT_ID, TARGET_ACCOUNT_ID,
                     PRESERVED_ACCOUNT_ID, root.metadata["issuer_session_id"],
                     hashlib.sha256(token.encode("ascii")).hexdigest(), now, expires,
                     original_time, target_time),
                )
            return RecoveryCapability(cap_id, reservation.execution_id, expires.isoformat(), event.event_id, token)
        except RecoveryRefused:
            raise
        except Exception:
            raise RecoveryRefused("RECOVERY_CAPABILITY_UNAVAILABLE") from None

    def _budget(self, principal: Principal) -> None:
        denied = False
        with self._engine.lock:
            self._actor(principal)
            self._lock()
            self._engine.execute(
                "INSERT INTO identity.dev_smoke_recovery_budget (authorization_id, attempt_count) "
                "VALUES (?, 0) ON CONFLICT (authorization_id) DO NOTHING", (AUTHORIZATION_ID,),
            )
            row = self._engine.query_one(
                "SELECT attempt_count FROM identity.dev_smoke_recovery_budget "
                "WHERE authorization_id = ? FOR UPDATE", (AUTHORIZATION_ID,),
            )
            count = int(row["attempt_count"])
            denied = count >= 5
            self._engine.execute(
                "UPDATE identity.dev_smoke_recovery_budget SET attempt_count = ? "
                "WHERE authorization_id = ?", (min(count + 1, 5), AUTHORIZATION_ID),
            )
        if denied:  # outside transaction: refusal must not undo the budget
            raise RecoveryRefused("RECOVERY_RATE_LIMITED")

    def _valid_cap(self, principal: Principal, reservation: RecoveryReservation,
                   capability_id: str, token_hash: str) -> tuple[AuditEvent, dict[str, Any]]:
        root = self._active(principal, reservation)
        row = self._engine.query_one(
            f"SELECT * FROM {self._TABLE} WHERE authorization_id = ? "
            "AND capability_id = ? AND token_hash = ? FOR UPDATE",
            (AUTHORIZATION_ID, capability_id, token_hash),
        )
        if (row is None or row["consumed_at"] is not None
                or datetime.fromisoformat(row["expires_at"]) <= self._identity._now()
                or row["execution_id"] != reservation.execution_id
                or row["plan_digest"] != reservation.plan_digest or row["root_event_id"] != root.event_id
                or row["issuer_session_id"] != root.metadata["issuer_session_id"]
                or row["tenant_id"] != TENANT_ID or row["target_account_id"] != TARGET_ACCOUNT_ID
                or row["issuer_account_id"] != PRESERVED_ACCOUNT_ID
                or self._credential_times() != (row["original_credential_updated_at"], row["target_credential_updated_at"])):
            raise RecoveryRefused("RECOVERY_CAPABILITY_UNAVAILABLE")
        events = self._cap_events()
        if len(events) != 1:
            raise RecoveryRefused("RECOVERY_CAPABILITY_UNAVAILABLE")
        event = events[0]
        if (event.event_id != row["issue_event_id"] or event.event_type != self._CAP_TYPE
                or event.actor != PRESERVED_ACCOUNT_ID or event.outcome != "success"
                or event.action != "DEV_SMOKE_RECOVERY_CAPABILITY" or event.resource != self._CAP_CORRELATION
                or event.metadata != {
                    **{k: root.metadata[k] for k in _PLAN_KEYS},
                    "plan_digest": reservation.plan_digest, "root_event_id": root.event_id,
                    "issuer_session_id": root.metadata["issuer_session_id"],
                    "capability_id": capability_id, "capability_expires_at": row["expires_at"],
                    "stage": "issued", "issue_event_id": "", "revoked_target_sessions": 0,
                    "execution_authorized": False, "credential_binding_verified": False,
                    "secret_values_redacted": True,
                }):
            raise RecoveryRefused("RECOVERY_CAPABILITY_UNAVAILABLE")
        return root, row

    def consume(self, principal: Principal, reservation: RecoveryReservation, *,
                capability_id: str, token: str, password: str) -> dict[str, Any]:
        self._top_level()
        try:
            self._budget(principal)  # independently committed before any refusal/Argon2
            if (type(capability_id) is not str or str(UUID(capability_id)) != capability_id
                    or not UUID(capability_id).int or type(token) is not str or not self._TOKEN.fullmatch(token)):
                raise RecoveryRefused("RECOVERY_CAPABILITY_UNAVAILABLE")
            token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
            with self._engine.lock:
                self._valid_cap(principal, reservation, capability_id, token_hash)
                target = self._account(TARGET_ACCOUNT_ID)
            if type(password) is not str or not 12 <= len(password) <= 1024:
                raise RecoveryRefused("RECOVERY_PASSWORD_REJECTED")
            policy = PasswordPolicy()
            normalized = policy.normalize(password)
            if not policy.validate(normalized, username=target["username"], email=target["email"]).valid:
                raise RecoveryRefused("RECOVERY_PASSWORD_REJECTED")
            service = CredentialService()  # instantiated only after cheap authenticated validity
            phc = service.hash_password(normalized)
            params = json.dumps(service.extract_params_from_phc(phc))
            with self._engine.lock:
                root, row = self._valid_cap(principal, reservation, capability_id, token_hash)
                now = self._identity._now()
                self._engine.execute(
                    "UPDATE identity.password_credentials SET phc_hash = ?, params = CAST(? AS jsonb), "
                    "last_rotated_at = ?, must_change = false WHERE account_id = ?",
                    (phc, params, now, TARGET_ACCOUNT_ID),
                )
                revoked = self._engine.execute(
                    "UPDATE identity.sessions SET revoked_at = ?, revoked_reason = 'dev-smoke-recovery' "
                    "WHERE account_id = ? AND revoked_at IS NULL AND created_at <= ?",
                    (now, TARGET_ACCOUNT_ID, now),
                ).rowcount
                event = self._record_cap(root, capability_id=capability_id,
                    expires=datetime.fromisoformat(row["expires_at"]), stage="rotated", now=now,
                    issue_event_id=row["issue_event_id"], revoked_count=revoked)
                self._engine.execute(
                    f"UPDATE {self._TABLE} SET consumed_at = ?, rotation_event_id = ? "
                    "WHERE authorization_id = ?", (now, event.event_id, AUTHORIZATION_ID),
                )
            return {"status": "rotated", "account_id": TARGET_ACCOUNT_ID, "tenant_id": TENANT_ID,
                    "execution_id": reservation.execution_id, "capability_id": capability_id,
                    "issue_event_id": row["issue_event_id"], "rotation_event_id": event.event_id,
                    "revoked_target_sessions": revoked, "credential_binding_verified": False,
                    "execution_authorized": False, "secret_values_redacted": True}
        except RecoveryRefused:
            raise
        except Exception:
            raise RecoveryRefused("RECOVERY_CAPABILITY_UNAVAILABLE") from None
