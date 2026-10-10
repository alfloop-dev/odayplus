"""Foreground preflight and internal reservation journal; no executor/CLI yet.

The pure validator checks a custodian's NON-SECRET proposed execution binding.
The optional PostgreSQL journal reserves that exact plan once and can quarantine
it after an uncertain result. Neither authenticates the custodian, verifies
release admission/human approval, proves mailbox ownership, or authorizes a cloud
mutation. Those controls must be implemented before any caller can issue/accept
an invite or bind credentials. Default workflows do not invoke this module.

Account input must come from the existing authenticated identity readback, not
caller headers or an offline receipt. Credentials/capabilities are not accepted
in a plan and must remain exclusively in the eventual executor's memory.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from shared.audit import AuditEvent
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.postgresql import PostgresEngine

AUTHORIZATION_ID = "HUMAN-ODP-DEV-SMOKE-20261010-001"
REPOSITORY = "alfloop-dev/odayplus"
TENANT_ID = "e34f2117-de4b-478c-82fd-13c4ef428d42"
PRESERVED_ACCOUNT_ID = "17e9cb99-db46-4a07-8e61-6bf9b22cf5d2"
PRESERVED_ROLES = frozenset({"auditor", "operations_manager", "platform_admin"})
PURPOSE = "dedicated-dev-release-smoke"
_AXES = ("brand_ids", "region_ids", "store_ids", "assigned_area_ids", "heat_zone_ids", "modules")
_PLAN_KEYS = frozenset({
    "authorization_id", "execution_id", "repository", "environment", "release_profile",
    "release_sha", "manifest_digest", "tenant_id", "actor_account_id", "purpose",
    "username", "email", "recipient_custodian", "recipient_control", "expires_at",
})
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}\Z")
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]+\.[^@\s]+\Z")
_CUSTODIAN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}\Z")


class ProvisioningRefused(Exception):
    """Static code only: never includes arbitrary plan/readback/exception values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ForegroundPlan:
    """Validated shape, NOT an authorization token or execution receipt."""

    execution_id: str
    release_sha: str
    manifest_digest: str
    username: str
    email: str = field(repr=False)
    recipient_custodian: str
    expires_at: datetime

    def to_receipt(self) -> dict[str, Any]:
        # Deliberately no recipient address, credentials or acceptance claim.
        return {
            "stage": "preflight-only", "execution_authorized": False,
            "authorization_id": AUTHORIZATION_ID, "execution_id": self.execution_id,
            "repository": REPOSITORY, "environment": "dev", "release_profile": "dev-admin",
            "release_sha": self.release_sha, "manifest_digest": self.manifest_digest,
            "tenant_id": TENANT_ID, "preserved_account_id": PRESERVED_ACCOUNT_ID,
            "secret_values_redacted": True,
        }


def _aware(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _original_account(account: Any) -> None:
    if not isinstance(account, dict):
        raise ProvisioningRefused("PROVISIONING_ORIGINAL_ACCOUNT_INVALID")
    roles = account.get("roles")
    scope = account.get("scope")
    if (account.get("subject_id") != PRESERVED_ACCOUNT_ID
            or account.get("tenant_id") != TENANT_ID
            or account.get("username") != "ajoe734" or account.get("status") != "active"
            or account.get("identity_source") != "identity.accounts"
            or not isinstance(roles, list) or not all(isinstance(role, str) for role in roles)
            or len(roles) != len(PRESERVED_ROLES) or set(roles) != PRESERVED_ROLES
            or not isinstance(scope, dict)
            or scope != {"tenant_id": TENANT_ID, "clearance": "CONFIDENTIAL", **{axis: [] for axis in _AXES}}
            or not isinstance(account.get("email"), str) or not _EMAIL.fullmatch(account["email"])):
        raise ProvisioningRefused("PROVISIONING_ORIGINAL_ACCOUNT_INVALID")


def validate_foreground_plan(
    plan: Any, *, original_account: Any, release_sha: str, manifest_digest: str,
    now: datetime,
) -> ForegroundPlan:
    """Fail closed before side effects; expected tuple must come from admission.

    A matching authorization_id is just a reference to the bounded human scope.
    Neither it nor recipient_control/recipient_custodian authenticates approval.
    The future foreground orchestrator must independently verify those facts and
    durably reserve this exact tuple before any account/configuration mutation.
    """
    if (not isinstance(plan, dict) or set(plan) != _PLAN_KEYS
            or not all(type(value) is str for value in plan.values())
            or any(not value or len(value) > 320 for value in plan.values())):
        raise ProvisioningRefused("PROVISIONING_PLAN_INVALID")
    if (plan["authorization_id"] != AUTHORIZATION_ID or plan["repository"] != REPOSITORY
            or plan["environment"] != "dev" or plan["release_profile"] != "dev-admin"
            or plan["tenant_id"] != TENANT_ID or plan["actor_account_id"] != PRESERVED_ACCOUNT_ID
            or plan["purpose"] != PURPOSE):
        raise ProvisioningRefused("PROVISIONING_SCOPE_MISMATCH")
    if (not isinstance(release_sha, str) or not _SHA.fullmatch(release_sha)
            or not isinstance(manifest_digest, str) or not _DIGEST.fullmatch(manifest_digest)
            or plan["release_sha"] != release_sha or plan["manifest_digest"] != manifest_digest):
        raise ProvisioningRefused("PROVISIONING_RELEASE_MISMATCH")
    try:
        execution_id = str(UUID(plan["execution_id"]))
    except ValueError:
        raise ProvisioningRefused("PROVISIONING_EXECUTION_INVALID") from None
    if execution_id != plan["execution_id"] or UUID(execution_id).int == 0:
        raise ProvisioningRefused("PROVISIONING_EXECUTION_INVALID")
    if not _aware(now):
        raise ProvisioningRefused("PROVISIONING_EXPIRY_INVALID")
    try:
        expires_at = datetime.fromisoformat(plan["expires_at"])
    except ValueError:
        raise ProvisioningRefused("PROVISIONING_EXPIRY_INVALID") from None
    # Short execution window, not an extension of the invitation's own TTL.
    if not _aware(expires_at) or not now < expires_at <= now + timedelta(hours=1):
        raise ProvisioningRefused("PROVISIONING_EXPIRY_INVALID")
    _original_account(original_account)
    username, email = plan["username"], plan["email"]
    if (not _USERNAME.fullmatch(username) or username.casefold() == original_account["username"].casefold()
            or len(email) > 320 or not _EMAIL.fullmatch(email)
            or email.casefold() == original_account["email"].casefold()
            or plan["recipient_control"] != "owner-controlled"
            or not _CUSTODIAN.fullmatch(plan["recipient_custodian"])):
        raise ProvisioningRefused("PROVISIONING_RECIPIENT_INVALID")
    # A plus alias is not normalized into the existing address, nor treated as
    # verified delivery. Explicit owner-controlled custody still needs proof.
    return ForegroundPlan(execution_id, release_sha, manifest_digest, username,
                          email, plan["recipient_custodian"], expires_at)


@dataclass(frozen=True)
class JournalReservation:
    """Internal bookkeeping only; never a bearer capability or approval proof."""

    execution_id: str
    plan_digest: str
    event_id: str
    stage: str

    def to_receipt(self) -> dict[str, Any]:
        return {**vars(self), "authorization_id": AUTHORIZATION_ID,
                "execution_authorized": False, "secret_values_redacted": True}


class ProvisioningJournal:
    """Append-only reservation/quarantine, NOT an executor or authorization check.

    A future trusted foreground executor must independently authenticate the
    human/custodian/admission and live account readback BEFORE reserving. A plan,
    digest or journal receipt alone grants nothing. This class changes only the
    existing durable audit journal, never identity rows or GitHub configuration.

    The root authorization (not just the chosen UUID) may be reserved once across
    processes. A crash, expiry or uncertain remote result never releases it for
    a fresh account/binding attempt. Recovery is explicit readback/quarantine;
    there is intentionally no reset, retry or resume API in this increment.
    """

    _CORRELATION = f"dev-smoke-provisioning:{AUTHORIZATION_ID}"
    _ACTOR = "system:dev-smoke-provisioning-journal"
    _TYPE = "release.dev_smoke.reservation"
    _KEYS = frozenset({
        "authorization_id", "execution_id", "plan_digest", "release_sha", "manifest_digest",
        "tenant_id", "stage", "execution_authorized", "secret_values_redacted",
    })

    def __init__(self, *, engine: PostgresEngine, audit_log: DurableAuditLog) -> None:
        if (not isinstance(engine, PostgresEngine) or not isinstance(audit_log, DurableAuditLog)
                or audit_log._engine is not engine):
            raise ProvisioningRefused("PROVISIONING_JOURNAL_PERSISTENCE_REQUIRED")
        self._engine = engine
        self._audit = audit_log

    def _lock(self) -> None:
        self._engine.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (self._CORRELATION,))

    def _now(self) -> datetime:
        return datetime.fromisoformat(self._engine.query_one("SELECT clock_timestamp() AS now")["now"])

    def _events(self) -> list[AuditEvent]:
        # Existing correlation-index read verifies each event's signed integrity.
        events = self._audit.list_events(correlation_id=self._CORRELATION)
        if len(events) > 2:
            raise ProvisioningRefused("PROVISIONING_JOURNAL_INVALID")
        for index, event in enumerate(events):
            metadata = event.metadata
            if (event.event_type != self._TYPE or event.actor != self._ACTOR
                    or event.resource != self._CORRELATION or event.outcome != "success"
                    or event.action != "DEV_SMOKE_RESERVATION"
                    or set(metadata) != self._KEYS
                    or metadata["authorization_id"] != AUTHORIZATION_ID
                    or metadata["tenant_id"] != TENANT_ID
                    or metadata["stage"] != ("reserved" if index == 0 else "recovery-required")
                    or metadata["execution_authorized"] is not False
                    or metadata["secret_values_redacted"] is not True):
                raise ProvisioningRefused("PROVISIONING_JOURNAL_INVALID")
            if index and {k: v for k, v in metadata.items() if k != "stage"} != {
                k: v for k, v in events[0].metadata.items() if k != "stage"
            }:
                raise ProvisioningRefused("PROVISIONING_JOURNAL_INVALID")
        return events

    @staticmethod
    def _receipt(event: AuditEvent) -> JournalReservation:
        return JournalReservation(event.metadata["execution_id"], event.metadata["plan_digest"],
                                  event.event_id, event.metadata["stage"])

    def reserve(
        self, plan: Any, *, original_account: Any, release_sha: str, manifest_digest: str,
    ) -> JournalReservation:
        """Atomic exact-plan reservation using DB time, not caller/workflow time.

        Even an identical second request is refused: no idempotent response may
        be mistaken for permission to repeat a remote side effect. All plan
        fields (including recipient, custodian, expiry and release tuple) are
        hashed together in memory; no email or arbitrary input reaches audit.
        """
        try:
            with self._engine.lock:
                self._lock()
                now = self._now()
                checked = validate_foreground_plan(
                    plan, original_account=original_account, release_sha=release_sha,
                    manifest_digest=manifest_digest, now=now,
                )
                if self._events():
                    raise ProvisioningRefused("PROVISIONING_ALREADY_RESERVED")
                digest = "sha256:" + hashlib.sha256(json.dumps(
                    plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                ).encode()).hexdigest()
                event = self._audit.record(AuditEvent(
                    event_type=self._TYPE, actor=self._ACTOR, action="DEV_SMOKE_RESERVATION",
                    resource=self._CORRELATION, correlation_id=self._CORRELATION,
                    outcome="success", occurred_at=now,
                    metadata={
                        "authorization_id": AUTHORIZATION_ID, "execution_id": checked.execution_id,
                        "plan_digest": digest, "release_sha": checked.release_sha,
                        "manifest_digest": checked.manifest_digest, "tenant_id": TENANT_ID,
                        "stage": "reserved", "execution_authorized": False,
                        "secret_values_redacted": True,
                    },
                ))
                receipt = self._receipt(event)
            # Return only after commit; an audit failure/commit failure is not success.
            return receipt
        except ProvisioningRefused:
            raise
        except Exception:
            raise ProvisioningRefused("PROVISIONING_JOURNAL_UNAVAILABLE") from None

    def inspect(self) -> JournalReservation | None:
        """Read-only restart evidence; never permission to retry a reservation."""
        try:
            with self._engine.lock:
                self._lock()
                events = self._events()
                return self._receipt(events[-1]) if events else None
        except ProvisioningRefused:
            raise
        except Exception:
            raise ProvisioningRefused("PROVISIONING_JOURNAL_UNAVAILABLE") from None

    def require_recovery(self, reservation: JournalReservation) -> JournalReservation:
        """Quarantine the exact reserved tuple after any uncertain external result.

        Can be recorded after expiry. Does not accept free-form error text,
        tokens, password, email or remote response bodies. There is no automatic
        compensating password reset, account deletion or configuration rollback.
        """
        try:
            with self._engine.lock:
                self._lock()
                events = self._events()
                if (not events or not isinstance(reservation, JournalReservation)
                        or reservation != self._receipt(events[0])):
                    raise ProvisioningRefused("PROVISIONING_RESERVATION_MISMATCH")
                if len(events) != 1:
                    raise ProvisioningRefused("PROVISIONING_RECOVERY_ALREADY_RECORDED")
                event = self._audit.record(AuditEvent(
                    event_type=self._TYPE, actor=self._ACTOR, action="DEV_SMOKE_RESERVATION",
                    resource=self._CORRELATION, correlation_id=self._CORRELATION,
                    outcome="success", occurred_at=self._now(),
                    metadata={**events[0].metadata, "stage": "recovery-required"},
                ))
                receipt = self._receipt(event)
            return receipt
        except ProvisioningRefused:
            raise
        except Exception:
            raise ProvisioningRefused("PROVISIONING_JOURNAL_UNAVAILABLE") from None
