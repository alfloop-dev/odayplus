"""Internal append-only root for the separately authorized dev-smoke recovery.

Mounted through the authenticated operator users router, not a foreground
executor or permission for credential/secret effects. Hash-only capability and
atomic rotation belong here; foreground/gate integration remains incomplete.
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
    CREDENTIAL_BUNDLE_SECRET_NAME,
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
    on every call. A plan is NOT authenticated authorization. The trusted
    foreground must separately pin the real user receipt,
    exact reviewed execution plan, source/CI/admission and custodian. Runtime
    tuple arguments come from server configuration, never request body/headers.
    """

    _CORRELATION = f"dev-smoke-recovery:{AUTHORIZATION_ID}"
    _TYPE = "identity.dev_smoke.recovery"
    _ACTION = "DEV_SMOKE_RECOVERY"
    _KEYS = _PLAN_KEYS | {
        "plan_digest", "issuer_session_id", "original_identity_digest", "target_identity_digest",
        "original_credential_updated_at", "target_credential_updated_at",
        "original_credential_revision", "target_credential_revision",
        "stage", "execution_authorized", "credential_binding_verified", "secret_values_redacted",
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

    def _credential_revisions(self) -> tuple[str, str]:
        revisions = [self._engine.query_one(
            "SELECT credential_revision::text AS revision FROM identity.password_credentials "
            "WHERE account_id = ? FOR SHARE", (account,),
        )["revision"] for account in (PRESERVED_ACCOUNT_ID, TARGET_ACCOUNT_ID)]
        return revisions[0], revisions[1]

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
                    or any(type(m[k]) is not str or not re.fullmatch(r"[1-9][0-9]{0,18}", m[k])
                           for k in ("original_credential_revision", "target_credential_revision"))
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
                original_revision, target_revision = self._credential_revisions()
                event = self._audit.record(AuditEvent(
                    event_type=self._TYPE, actor=PRESERVED_ACCOUNT_ID, action=self._ACTION,
                    resource=self._CORRELATION, correlation_id=self._CORRELATION,
                    outcome="success", occurred_at=now, metadata={
                        **plan, "plan_digest": digest, "issuer_session_id": sid,
                        "original_identity_digest": _digest(original), "target_identity_digest": _digest(target),
                        "original_credential_updated_at": original_time,
                        "target_credential_updated_at": target_time,
                        "original_credential_revision": original_revision,
                        "target_credential_revision": target_revision,
                        "stage": "reserved", "execution_authorized": False,
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
    """Authenticated single-account rotation; never a generic reset API.

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
    _CAP_KEYS = _PLAN_KEYS | {
        "plan_digest", "root_event_id", "issuer_session_id", "capability_id",
        "capability_expires_at", "stage", "issue_event_id", "revoked_target_sessions",
        "execution_authorized", "credential_binding_verified", "secret_values_redacted",
    }

    def _top_level(self) -> None:
        # PostgresEngine nests by joining the existing transaction. A caller
        # transaction would roll back the abuse budget or expose an uncommitted
        # successful receipt; prohibit it rather than pretend it is independent.
        if getattr(self._engine._local, "connection", None) is not None:
            raise RecoveryRefused("RECOVERY_OUTERMOST_TRANSACTION_REQUIRED")

    def _root(self, principal: Principal, reservation: RecoveryReservation) -> AuditEvent:
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
        return root

    def _active(self, principal: Principal, reservation: RecoveryReservation) -> AuditEvent:
        root = self._root(principal, reservation)
        m = root.metadata
        # Lock credential updates before the baseline read; self-password-change
        # writers need not share the tenant advisory lock. No PHC enters audit.
        self._engine.query_one(
            "SELECT account_id FROM identity.password_credentials WHERE account_id = ? FOR UPDATE",
            (TARGET_ACCOUNT_ID,),
        )
        original, target = self._account(PRESERVED_ACCOUNT_ID), self._account(TARGET_ACCOUNT_ID)
        if (_digest(original) != m["original_identity_digest"]
                or _digest(target) != m["target_identity_digest"]
                or self._credential_times() != (m["original_credential_updated_at"], m["target_credential_updated_at"])
                or self._credential_revisions() != (m["original_credential_revision"], m["target_credential_revision"])):
            raise RecoveryRefused("RECOVERY_IDENTITY_DRIFT")
        self._old_lineage(target)
        return root

    def _cap_events(self) -> list[AuditEvent]:
        return self._audit.list_events(correlation_id=self._CAP_CORRELATION)

    def _cap_metadata(self, root: AuditEvent, *, capability_id: str, expires: datetime,
                      stage: str, issue_event_id: str = "", revoked_count: int = 0) -> dict[str, Any]:
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
        return metadata

    def _record_cap(self, root: AuditEvent, *, capability_id: str, expires: datetime,
                    stage: str, now: datetime, issue_event_id: str = "",
                    revoked_count: int = 0) -> AuditEvent:
        return self._audit.record(AuditEvent(
            event_type=self._CAP_TYPE, actor=PRESERVED_ACCOUNT_ID,
            action="DEV_SMOKE_RECOVERY_CAPABILITY", resource=self._CAP_CORRELATION,
            correlation_id=self._CAP_CORRELATION, outcome="success", occurred_at=now,
            metadata=self._cap_metadata(root, capability_id=capability_id, expires=expires,
                stage=stage, issue_event_id=issue_event_id, revoked_count=revoked_count),
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
                original_revision, target_revision = self._credential_revisions()
                token, cap_id = secrets.token_urlsafe(32), str(uuid4())
                event = self._record_cap(root, capability_id=cap_id, expires=expires,
                                         stage="issued", now=now)
                self._engine.execute(
                    f"INSERT INTO {self._TABLE} (authorization_id, capability_id, execution_id, "
                    "plan_digest, root_event_id, issue_event_id, tenant_id, target_account_id, "
                    "issuer_account_id, issuer_session_id, token_hash, issued_at, expires_at, "
                    "original_credential_updated_at, target_credential_updated_at, "
                    "original_credential_revision, target_credential_revision) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (AUTHORIZATION_ID, cap_id, reservation.execution_id, reservation.plan_digest,
                     root.event_id, event.event_id, TENANT_ID, TARGET_ACCOUNT_ID,
                     PRESERVED_ACCOUNT_ID, root.metadata["issuer_session_id"],
                     hashlib.sha256(token.encode("ascii")).hexdigest(), now, expires,
                     original_time, target_time, original_revision, target_revision),
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
                or self._credential_times() != (row["original_credential_updated_at"], row["target_credential_updated_at"])
                or self._credential_revisions() != (str(row["original_credential_revision"]), str(row["target_credential_revision"]))):
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
                    "WHERE account_id = ? AND revoked_at IS NULL",
                    (now, TARGET_ACCOUNT_ID),
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


# Safe producer/consumer contract. The ledger records an authenticated original
# admin's ACK declaration, not independently authenticated GitHub response proof.
# A reviewed trusted foreground single-attempt PUT boundary is still required.
RECOVERY_BINDING_CORRELATION = f"dev-smoke-recovery-binding:{AUTHORIZATION_ID}"
RECOVERY_BINDING_TYPE = "release.dev_smoke.recovery_binding"
RECOVERY_BINDING_ACTION = "DEV_SMOKE_RECOVERY_BINDING"
RECOVERY_BINDING_KEYS = _PLAN_KEYS | {
    "plan_digest", "root_event_id", "issuer_session_id", "capability_id",
    "capability_issue_event_id", "rotation_event_id", "secret_name", "stage",
    "put_status", "execution_authorized", "credential_binding_verified", "secret_values_redacted",
}


class RecoveryBindingJournal(RecoveryCapabilityService):
    """Append-only same-engine post-rotation intent/ACK, never a secret writer.

    ACK is an authenticated original-admin declaration of an actual 201. The
    server cannot authenticate a GitHub response from an integer in a request;
    only a reviewed trusted foreground transport may submit that declaration.
    These internal methods are intentionally NOT mounted yet. They supply no
    custodian/source/admission/external-writer-exclusion or live-binding proof.
    Root quarantine is the only failure transition, including after ACK.
    """

    def _binding_events(self) -> list[AuditEvent]:
        events = self._audit.list_events(correlation_id=RECOVERY_BINDING_CORRELATION)
        if len(events) > 2:
            raise RecoveryRefused("RECOVERY_BINDING_INVALID")
        for index, event in enumerate(events):
            m = event.metadata
            if (event.event_type != RECOVERY_BINDING_TYPE or event.actor != PRESERVED_ACCOUNT_ID
                    or event.action != RECOVERY_BINDING_ACTION or event.resource != RECOVERY_BINDING_CORRELATION
                    or event.outcome != "success" or set(m) != RECOVERY_BINDING_KEYS
                    or any(m[k] != v for k, v in _FIXED_PLAN.items())
                    or m["stage"] != ("binding-intent" if index == 0 else "binding-acknowledged")
                    or type(m["put_status"]) is not int or m["put_status"] != (0 if index == 0 else 201)
                    or m["execution_authorized"] is not False or m["credential_binding_verified"] is not False
                    or m["secret_values_redacted"] is not True
                    or m["secret_name"] != CREDENTIAL_BUNDLE_SECRET_NAME
                    or (index and {k: v for k, v in m.items() if k not in {"stage", "put_status"}} != {
                        k: v for k, v in events[0].metadata.items() if k not in {"stage", "put_status"}})):
                raise RecoveryRefused("RECOVERY_BINDING_INVALID")
            plan = {k: m[k] for k in _PLAN_KEYS}
            if (validate_recovery_plan(plan, release_sha=m["release_sha"], manifest_digest=m["manifest_digest"],
                                       now=events[0].occurred_at) != m["plan_digest"]
                    or event.occurred_at < events[0].occurred_at
                    or event.occurred_at >= datetime.fromisoformat(m["expires_at"])
                    or any(type(m[k]) is not str or str(UUID(m[k])) != m[k] or not UUID(m[k]).int for k in (
                        "root_event_id", "issuer_session_id", "capability_id", "capability_issue_event_id", "rotation_event_id"))):
                raise RecoveryRefused("RECOVERY_BINDING_INVALID")
        return events

    def _post_rotation(self, principal: Principal, reservation: RecoveryReservation) -> dict[str, Any]:
        root = self._root(principal, reservation)
        m = root.metadata
        # No bearer/hash/PHC is read for binding. Hold account/credential/session
        # SHARE locks until append commits; self-change/logout writers must wait.
        original, target = self._account(PRESERVED_ACCOUNT_ID), self._account(TARGET_ACCOUNT_ID)
        row = self._engine.query_one(
            f"SELECT capability_id, execution_id, plan_digest, root_event_id, issue_event_id, "
            "tenant_id, target_account_id, issuer_account_id, issuer_session_id, issued_at, expires_at, "
            "consumed_at, rotation_event_id, original_credential_updated_at, target_credential_updated_at, "
            f"original_credential_revision, target_credential_revision FROM {self._TABLE} "
            "WHERE authorization_id = ? FOR SHARE", (AUTHORIZATION_ID,),
        )
        if (row is None or row["consumed_at"] is None or row["rotation_event_id"] is None
                or row["execution_id"] != reservation.execution_id or row["plan_digest"] != reservation.plan_digest
                or row["root_event_id"] != root.event_id or row["issuer_session_id"] != m["issuer_session_id"]
                or row["tenant_id"] != TENANT_ID or row["target_account_id"] != TARGET_ACCOUNT_ID
                or row["issuer_account_id"] != PRESERVED_ACCOUNT_ID
                or row["original_credential_updated_at"] != m["original_credential_updated_at"]
                or row["target_credential_updated_at"] != m["target_credential_updated_at"]
                or str(row["original_credential_revision"]) != m["original_credential_revision"]
                or str(row["target_credential_revision"]) != m["target_credential_revision"]):
            raise RecoveryRefused("RECOVERY_ROTATION_UNVERIFIED")
        original_time, _ = self._credential_times()
        original_revision, target_revision = self._credential_revisions()
        rotated = self._engine.query_one(
            "SELECT last_rotated_at FROM identity.password_credentials WHERE account_id = ? FOR SHARE",
            (TARGET_ACCOUNT_ID,),
        )
        if (_digest(original) != m["original_identity_digest"] or _digest(target) != m["target_identity_digest"]
                or original_time != m["original_credential_updated_at"]
                or original_revision != m["original_credential_revision"]
                or int(target_revision) != int(m["target_credential_revision"]) + 1
                or rotated["last_rotated_at"] != row["consumed_at"]):
            raise RecoveryRefused("RECOVERY_IDENTITY_DRIFT")
        self._old_lineage(target)
        events = self._cap_events()
        if len(events) != 2:
            raise RecoveryRefused("RECOVERY_ROTATION_UNVERIFIED")
        issued, rotation = events
        expires = datetime.fromisoformat(row["expires_at"])
        count = rotation.metadata.get("revoked_target_sessions")
        if (type(count) is not int or count < 0
                or type(issued.metadata.get("revoked_target_sessions")) is not int
                or not root.occurred_at <= issued.occurred_at <= rotation.occurred_at < expires
                or expires > min(issued.occurred_at + timedelta(seconds=600), datetime.fromisoformat(m["expires_at"]))):
            raise RecoveryRefused("RECOVERY_ROTATION_UNVERIFIED")
        for event, stage, event_id, at, issue_id, revoked in (
            (issued, "issued", row["issue_event_id"], row["issued_at"], "", 0),
            (rotation, "rotated", row["rotation_event_id"], row["consumed_at"], row["issue_event_id"], count),
        ):
            if (event.event_id != event_id or event.occurred_at.isoformat() != at
                    or event.event_type != self._CAP_TYPE or event.actor != PRESERVED_ACCOUNT_ID
                    or event.action != "DEV_SMOKE_RECOVERY_CAPABILITY" or event.resource != self._CAP_CORRELATION
                    or event.outcome != "success" or event.metadata != self._cap_metadata(
                        root, capability_id=row["capability_id"], expires=expires, stage=stage,
                        issue_event_id=issue_id, revoked_count=revoked)):
                raise RecoveryRefused("RECOVERY_ROTATION_UNVERIFIED")
        # A live local-password session at the rotated revision proves a fresh
        # credential-authenticated login occurred, not caller-supplied pair proof.
        # No target session is revoked/updated/created by this ledger.
        if self._engine.query_one(
            "SELECT session_id FROM identity.sessions WHERE account_id = ? "
            "AND provider = 'local_password' AND verified_credential_revision = ? "
            "AND revoked_at IS NULL AND created_at >= ? "
            "AND idle_expires_at > clock_timestamp() AND absolute_expires_at > clock_timestamp() "
            "ORDER BY session_id LIMIT 1 FOR SHARE", (TARGET_ACCOUNT_ID, target_revision, row["consumed_at"]),
        ) is None:
            raise RecoveryRefused("RECOVERY_FRESH_TARGET_SESSION_REQUIRED")
        return {**{k: m[k] for k in _PLAN_KEYS}, "plan_digest": m["plan_digest"],
                "root_event_id": root.event_id, "issuer_session_id": m["issuer_session_id"],
                "capability_id": row["capability_id"], "capability_issue_event_id": row["issue_event_id"],
                "rotation_event_id": row["rotation_event_id"], "secret_name": CREDENTIAL_BUNDLE_SECRET_NAME,
                "execution_authorized": False, "credential_binding_verified": False, "secret_values_redacted": True}

    @staticmethod
    def _binding_receipt(event: AuditEvent) -> dict[str, Any]:
        return {**event.metadata, "audit_event_id": event.event_id}

    def binding_inspect(self, principal: Principal) -> dict[str, Any] | None:
        """Readback never supplies authority to repeat an intent or unknown PUT."""
        self._top_level()
        try:
            with self._engine.lock:
                self._actor(principal)
                self._lock()
                events = self._binding_events()
                return self._binding_receipt(events[-1]) if events else None
        except RecoveryRefused:
            raise
        except Exception:
            raise RecoveryRefused("RECOVERY_BINDING_UNAVAILABLE") from None

    def _binding_append(self, principal: Principal, reservation: RecoveryReservation, *,
                        stage: str, intent_event_id: str = "", put_status: int = 0) -> dict[str, Any]:
        self._top_level()
        try:
            with self._engine.lock:
                metadata = self._post_rotation(principal, reservation)
                events = self._binding_events()
                if stage == "binding-intent":
                    if events:
                        raise RecoveryRefused("RECOVERY_BINDING_ALREADY_ATTEMPTED")
                elif (len(events) != 1 or events[0].event_id != intent_event_id
                        or events[0].metadata != {**metadata, "stage": "binding-intent", "put_status": 0}
                        or type(put_status) is not int or put_status != 201):
                    raise RecoveryRefused("RECOVERY_BINDING_ACK_REFUSED")
                now = self._identity._now()
                if now >= datetime.fromisoformat(metadata["expires_at"]):
                    raise RecoveryRefused("RECOVERY_EXECUTION_DRIFT")
                event = self._audit.record(AuditEvent(
                    event_type=RECOVERY_BINDING_TYPE, actor=PRESERVED_ACCOUNT_ID, action=RECOVERY_BINDING_ACTION,
                    resource=RECOVERY_BINDING_CORRELATION, correlation_id=RECOVERY_BINDING_CORRELATION,
                    outcome="success", occurred_at=now, metadata={**metadata, "stage": stage, "put_status": put_status},
                ))
            return self._binding_receipt(event)  # only after actual PG commit
        except RecoveryRefused:
            raise
        except Exception:
            raise RecoveryRefused("RECOVERY_BINDING_UNAVAILABLE") from None

    def binding_intent(self, principal: Principal, reservation: RecoveryReservation) -> dict[str, Any]:
        return self._binding_append(principal, reservation, stage="binding-intent")

    def binding_acknowledge(self, principal: Principal, reservation: RecoveryReservation, *,
                            intent_event_id: str, put_status: int) -> dict[str, Any]:
        """Trusted foreground declaration only, NOT remote receipt authentication."""
        return self._binding_append(principal, reservation, stage="binding-acknowledged",
                                    intent_event_id=intent_event_id, put_status=put_status)


@dataclass(frozen=True)
class RecoveryBundleLineage:
    """Non-secret schema-2 expectations, never execution/ACK authority."""

    plan_digest: str
    root_event_id: str
    capability_id: str
    capability_issue_event_id: str
    rotation_event_id: str
    binding_intent_event_id: str
    old_execution_id: str
    old_plan_digest: str
    old_quarantine_event_id: str
    invitation_id: str
    issue_event_id: str
    accept_event_id: str


def recovery_bundle_lineage(value: Any) -> RecoveryBundleLineage:
    try:
        keys = set(RecoveryBundleLineage.__dataclass_fields__)
        if (type(value) is not dict or set(value) != keys
                or any(type(v) is not str for v in value.values())):
            raise ValueError
        for k in ("old_execution_id", "old_plan_digest", "old_quarantine_event_id",
                  "invitation_id", "issue_event_id", "accept_event_id"):
            if value[k] != _FIXED_PLAN[k]:
                raise ValueError
        for k, v in value.items():
            if k.endswith("digest"):
                if not _DIGEST.fullmatch(v):
                    raise ValueError
            elif str(UUID(v)) != v or not UUID(v).int:
                raise ValueError
        return RecoveryBundleLineage(**value)
    except Exception:
        raise RecoveryRefused("RECOVERY_BUNDLE_LINEAGE_INVALID") from None


def recovery_bundle_acknowledged(
    *, execution_id: str, lineage: RecoveryBundleLineage, events: Any, target_account: Any,
) -> bool:
    """Verify complete authenticated tenant-audit projection, NOT a PUT receipt.

    Old creation must STILL be quarantined with no binding, not repaired. New
    root/issuance/rotation/intent/201-ACK must be exact, unique and unquarantined.
    Expiry constrains execution timestamps, not later standing-bundle use. A
    caller's passing boolean or ACK-shaped object cannot replace signed server
    audit readback; the canonical gate supplies the authenticated projection.
    """
    from .dev_smoke_journal import CREDENTIAL_BUNDLE_SECRET_NAME

    try:
        if (type(lineage) is not RecoveryBundleLineage
                or recovery_bundle_lineage(vars(lineage)) != lineage
                or type(events) is not list or any(type(e) is not dict for e in events)
                or str(UUID(execution_id)) != execution_id or not UUID(execution_id).int
                or execution_id == OLD_EXECUTION_ID):
            return False
        expected_provenance = {"invitation_id": INVITATION_ID, "issue_event_id": ISSUE_EVENT_ID,
                               "accept_event_id": ACCEPT_EVENT_ID, "issuer_account_id": PRESERVED_ACCOUNT_ID}
        if (type(target_account) is not dict or target_account.get("subject_id") != TARGET_ACCOUNT_ID
                or target_account.get("username") != "odp-dev-smoke"
                or invitation_provenance(target_account, events) != expected_provenance):
            return False

        def group(kind: str, correlation: str, actor: str, action: str, keys: Any,
                  stages: list[str]) -> list[dict[str, Any]]:
            found = [e for e in events if e.get("event_type") == kind or e.get("correlation_id") == correlation]
            found.sort(key=lambda e: datetime.fromisoformat(e["timestamp"]))
            if len(found) != len(stages):
                raise ValueError
            for e, stage in zip(found, stages, strict=True):
                m = e["metadata"]
                if (e["event_type"] != kind or e["correlation_id"] != correlation
                        or e["resource"] != correlation or e["actor"] != actor or e["action"] != action
                        or e["outcome"] != "success" or type(m) is not dict or set(m) != keys
                        or m["stage"] != stage or m["tenant_id"] != TENANT_ID
                        or m["execution_authorized"] is not False or m["secret_values_redacted"] is not True
                        or not _aware(datetime.fromisoformat(e["timestamp"]))
                        or str(UUID(e["event_id"])) != e["event_id"] or not UUID(e["event_id"]).int):
                    raise ValueError
            return found

        old = group(ProvisioningJournal._TYPE, ProvisioningJournal._CORRELATION,
                    ProvisioningJournal._ACTOR, "DEV_SMOKE_RESERVATION", ProvisioningJournal._KEYS,
                    ["reserved", "recovery-required"])
        old_base = old[0]["metadata"]
        if (old[1]["event_id"] != OLD_QUARANTINE_EVENT_ID
                or old_base["authorization_id"] != ProvisioningJournal._CORRELATION.split(":", 1)[1]
                or old_base["execution_id"] != OLD_EXECUTION_ID or old_base["plan_digest"] != OLD_PLAN_DIGEST
                or not _SHA.fullmatch(old_base["release_sha"]) or not _DIGEST.fullmatch(old_base["manifest_digest"])
                or {k: v for k, v in old[1]["metadata"].items() if k != "stage"} != {
                    k: v for k, v in old_base.items() if k != "stage"}
                or any(e.get("event_type") == DevSmokeBindingJournal._TYPE
                       or e.get("correlation_id") == DevSmokeBindingJournal._CORRELATION for e in events)):
            return False
        roots = group(RecoveryJournal._TYPE, RecoveryJournal._CORRELATION, PRESERVED_ACCOUNT_ID,
                      RecoveryJournal._ACTION, RecoveryJournal._KEYS, ["reserved"])
        root, m = roots[0], roots[0]["metadata"]
        plan = {k: m[k] for k in _PLAN_KEYS}
        digest = validate_recovery_plan(plan, release_sha=m["release_sha"],
                                       manifest_digest=m["manifest_digest"], now=datetime.fromisoformat(root["timestamp"]))
        if (m["execution_id"] != execution_id or m["plan_digest"] != lineage.plan_digest
                or digest != lineage.plan_digest or root["event_id"] != lineage.root_event_id
                or m["credential_binding_verified"] is not False
                or any(type(m[k]) is not str or not _DIGEST.fullmatch(m[k])
                       for k in ("original_identity_digest", "target_identity_digest"))
                or str(UUID(m["issuer_session_id"])) != m["issuer_session_id"] or not UUID(m["issuer_session_id"]).int
                or any(type(m[k]) is not str or not re.fullmatch(r"[1-9][0-9]{0,18}", m[k])
                       for k in ("original_credential_revision", "target_credential_revision"))
                or any(not _aware(datetime.fromisoformat(m[k])) for k in (
                    "original_credential_updated_at", "target_credential_updated_at"))):
            return False
        caps = group(RecoveryCapabilityService._CAP_TYPE, RecoveryCapabilityService._CAP_CORRELATION,
                     PRESERVED_ACCOUNT_ID, "DEV_SMOKE_RECOVERY_CAPABILITY", RecoveryCapabilityService._CAP_KEYS,
                     ["issued", "rotated"])
        issue, rotation = caps
        cm = issue["metadata"]
        base = {**plan, "plan_digest": digest, "root_event_id": root["event_id"],
                "issuer_session_id": m["issuer_session_id"], "capability_id": lineage.capability_id,
                "execution_authorized": False, "credential_binding_verified": False, "secret_values_redacted": True}
        expires = datetime.fromisoformat(cm["capability_expires_at"])
        issued_at = datetime.fromisoformat(issue["timestamp"])
        rotated_at = datetime.fromisoformat(rotation["timestamp"])
        if (not _aware(expires) or not issued_at <= rotated_at < expires <= issued_at + timedelta(seconds=600)
                or expires > datetime.fromisoformat(plan["expires_at"])
                or issue["event_id"] != lineage.capability_issue_event_id
                or rotation["event_id"] != lineage.rotation_event_id
                or cm != {**base, "capability_expires_at": cm["capability_expires_at"], "stage": "issued",
                          "issue_event_id": "", "revoked_target_sessions": 0}
                or type(cm["revoked_target_sessions"]) is not int
                or type(rotation["metadata"]["revoked_target_sessions"]) is not int
                or rotation["metadata"]["revoked_target_sessions"] < 0
                or rotation["metadata"] != {**cm, "stage": "rotated", "issue_event_id": issue["event_id"],
                    "revoked_target_sessions": rotation["metadata"]["revoked_target_sessions"]}):
            return False
        bindings = group(RECOVERY_BINDING_TYPE, RECOVERY_BINDING_CORRELATION, PRESERVED_ACCOUNT_ID,
                         RECOVERY_BINDING_ACTION, RECOVERY_BINDING_KEYS, ["binding-intent", "binding-acknowledged"])
        binding_base = {**plan, "plan_digest": digest, "root_event_id": root["event_id"],
                        "issuer_session_id": m["issuer_session_id"], "capability_id": lineage.capability_id,
                        "capability_issue_event_id": issue["event_id"], "rotation_event_id": rotation["event_id"],
                        "secret_name": CREDENTIAL_BUNDLE_SECRET_NAME, "execution_authorized": False,
                        "credential_binding_verified": False, "secret_values_redacted": True}
        if (bindings[0]["event_id"] != lineage.binding_intent_event_id
                or any(e["metadata"] != {**binding_base, "stage": stage, "put_status": status}
                       or type(e["metadata"]["put_status"]) is not int
                       for e, stage, status in zip(bindings, ["binding-intent", "binding-acknowledged"], [0, 201], strict=True))):
            return False
        provenance = [next(e for e in events if e["event_id"] == identifier)
                      for identifier in (ISSUE_EVENT_ID, ACCEPT_EVENT_ID)]
        ordered = [old[0], *provenance, old[1], root, *caps, *bindings]
        if (len({e["event_id"] for e in ordered}) != len(ordered)
                or any(sum(e.get("event_id") == item["event_id"] for e in events) != 1 for item in ordered)):
            return False
        times = [datetime.fromisoformat(e["timestamp"]) for e in ordered]
        return (all(_aware(t) for t in times) and all(a <= b for a, b in zip(times, times[1:]))
                and times[-1] < datetime.fromisoformat(plan["expires_at"]))
    except Exception:
        return False
