"""Internal append-only root for the separately authorized dev-smoke recovery.

Incremental domain layer ONLY: not mounted, not an executor, and not permission
for credential/secret effects. Capability issuance/rotation and transport/gate
integration must compose with this root in a subsequent reviewed increment.
No reset/resume/re-reservation exists. The old creation journal stays immutable.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

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
                event = self._audit.record(AuditEvent(
                    event_type=self._TYPE, actor=PRESERVED_ACCOUNT_ID, action=self._ACTION,
                    resource=self._CORRELATION, correlation_id=self._CORRELATION,
                    outcome="success", occurred_at=now, metadata={
                        **plan, "plan_digest": digest, "issuer_session_id": sid,
                        "original_identity_digest": _digest(original), "target_identity_digest": _digest(target),
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
