"""Shared dev-only identity reservation and binding audit contracts.

Internal server-side storage only: authenticated routing owns actor/session checks.
Plans and journal receipts grant no source, consent, admission or release authority.
No credential writer, HTTP transport, deployment hook or anonymous CLI lives here.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from shared.audit import AuditEvent
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.postgresql import PostgresEngine

CREDENTIAL_BUNDLE_SECRET_NAME = "ODP_DEV_ADMIN_CREDENTIAL_BUNDLE"

AUTHORIZATION_ID = "HUMAN-ODP-DEV-SMOKE-20261010-001"
REPOSITORY = "alfloop-dev/odayplus"
TENANT_ID = "e34f2117-de4b-478c-82fd-13c4ef428d42"
PRESERVED_ACCOUNT_ID = "17e9cb99-db46-4a07-8e61-6bf9b22cf5d2"
# The consent receipt's recorded role set: an immutable historical upper bound,
# NOT a live baseline to force or restore. The actual roles are read back fresh
# before execution and must be byte-for-byte unchanged afterwards; they must
# keep platform_admin (the invitation issuer) and never exceed this set.
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
            or len(set(roles)) != len(roles) or "platform_admin" not in roles
            or not set(roles) <= PRESERVED_ROLES
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
    The trusted foreground owner must bind the recorded consent, approved plan
    and independent source/admission observations before any mutation.
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
    if (username != "odp-dev-smoke" or not _USERNAME.fullmatch(username)
            or username.casefold() == original_account["username"].casefold()
            or len(email) > 320 or not _EMAIL.fullmatch(email)
            or email.casefold() == original_account["email"].casefold()
            or plan["recipient_control"] != "owner-controlled"
            or not _CUSTODIAN.fullmatch(plan["recipient_custodian"])):
        raise ProvisioningRefused("PROVISIONING_RECIPIENT_INVALID")
    # A plus alias is not normalized into the existing address, nor treated as
    # verified delivery. Recipient selection belongs to the trusted owner.
    return ForegroundPlan(execution_id, release_sha, manifest_digest, username,
                          email, plan["recipient_custodian"], expires_at)


def original_account_readback(records: list[dict[str, Any]]) -> dict[str, Any]:
    own = [r for r in records if r.get("subject_id") == PRESERVED_ACCOUNT_ID]
    if len(own) != 1:
        raise ProvisioningRefused("PROVISIONING_ORIGINAL_ACCOUNT_INVALID")
    record = own[0]
    account = {key: record.get(key) for key in ("subject_id", "username", "email", "status", "roles", "scope")}
    scope = record.get("scope")
    attrs = record.get("attributes")
    account["tenant_id"] = scope.get("tenant_id") if isinstance(scope, dict) else None
    account["identity_source"] = attrs.get("identity_source") if isinstance(attrs, dict) else None
    _original_account(account)
    return account


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

    The trusted foreground executor must bind recorded consent, approved custody,
    independent source/admission and live account readback BEFORE reserving. A plan,
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

    @contextmanager
    def session(self, *, admin_password: str) -> Any:
        # Local server-side storage already runs inside authenticated API context.
        yield self

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
                if self._events():
                    raise ProvisioningRefused("PROVISIONING_ALREADY_RESERVED")
                checked = validate_foreground_plan(
                    plan, original_account=original_account, release_sha=release_sha,
                    manifest_digest=manifest_digest, now=now,
                )
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


class DevSmokeBindingJournal:
    """Server-side durable binding intent/acknowledgement ledger.

    Never grants authority or verifies a decrypted GitHub secret.
    """

    _CORRELATION = f"dev-smoke-binding:{AUTHORIZATION_ID}"
    _ACTOR = "system:dev-smoke-credential-binding"
    _TYPE = "release.dev_smoke.binding"
    _KEYS = frozenset({
        "authorization_id", "execution_id", "plan_digest", "release_sha", "manifest_digest",
        "tenant_id", "account_id", "secret_name", "stage", "execution_authorized",
        "credential_binding_verified", "secret_values_redacted",
    })

    def __init__(self, *, journal: ProvisioningJournal) -> None:
        self._journal = journal

    def _events(self) -> list[AuditEvent]:
        events = self._journal._audit.list_events(correlation_id=self._CORRELATION)
        if len(events) > 2:
            raise ProvisioningRefused("PROVISIONING_BINDING_JOURNAL_INVALID")
        for index, event in enumerate(events):
            m = event.metadata
            if (event.event_type != self._TYPE or event.actor != self._ACTOR
                    or event.resource != self._CORRELATION or event.action != "DEV_SMOKE_BINDING"
                    or event.outcome != "success" or set(m) != self._KEYS
                    or m["authorization_id"] != AUTHORIZATION_ID or m["tenant_id"] != TENANT_ID
                    or m["secret_name"] != CREDENTIAL_BUNDLE_SECRET_NAME
                    or m["execution_authorized"] is not False
                    or m["credential_binding_verified"] is not False
                    or m["secret_values_redacted"] is not True
                    or m["stage"] not in (("binding-intent",) if index == 0 else
                                          ("binding-acknowledged", "recovery-required"))):
                raise ProvisioningRefused("PROVISIONING_BINDING_JOURNAL_INVALID")
            if index and {k: v for k, v in m.items() if k != "stage"} != {
                k: v for k, v in events[0].metadata.items() if k != "stage"
            }:
                raise ProvisioningRefused("PROVISIONING_BINDING_JOURNAL_INVALID")
        return events

    def inspect(self) -> dict[str, Any] | None:
        try:
            with self._journal._engine.lock:
                self._journal._engine.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (self._CORRELATION,))
                events = self._events()
                return {**events[-1].metadata, "audit_event_id": events[-1].event_id} if events else None
        except Exception:
            raise ProvisioningRefused("PROVISIONING_BINDING_JOURNAL_UNAVAILABLE") from None

    def _append(self, metadata: dict[str, Any], stage: str) -> AuditEvent:
        with self._journal._engine.lock:
            self._journal._engine.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (self._CORRELATION,))
            events = self._events()
            if (stage not in {"binding-intent", "binding-acknowledged", "recovery-required"}
                    or (stage == "binding-intent" and events)
                    or (stage != "binding-intent" and (len(events) != 1
                        or metadata != events[0].metadata))):
                raise ProvisioningRefused("PROVISIONING_BINDING_ALREADY_ATTEMPTED")
            event = self._journal._audit.record(AuditEvent(
                event_type=self._TYPE, actor=self._ACTOR, action="DEV_SMOKE_BINDING",
                resource=self._CORRELATION, correlation_id=self._CORRELATION, outcome="success",
                occurred_at=self._journal._now(), metadata={**metadata, "stage": stage},
            ))
        return event


