"""Foreground preflight, reservation journal and memory-only Web lifecycle.

The pure validator checks a custodian's NON-SECRET proposed execution binding.
The optional PostgreSQL journal reserves that exact plan once and can quarantine
it after an uncertain result. Neither authenticates the custodian, verifies
release admission/human approval, proves mailbox ownership, or authorizes a cloud
mutation. The Web lifecycle below is callable only by the trusted foreground
coordinator after source approval and exact release admission; it does not
implement those control-plane checks or credential binding. Default workflows
do not invoke this module, and there is intentionally no anonymous CLI.

Account input must come from the existing authenticated identity readback, not
caller headers or an offline receipt. Credentials/capabilities are not accepted
in a plan and remain exclusively in the Web executor's memory.
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


class WebInvitationExecutor:
    """Execute the supported lifecycle through Web, never directly mutate SQL.

    This is a foreground library entrypoint, NOT an admission/approval service.
    The coordinator must verify independent source approval, exact dev release
    admission/promotion, and recipient custody before calling it. A journal or
    matching string is not that proof. No workflow or CLI calls this entrypoint.

    All HTTP side effects are single-attempt. Once reserved, ANY uncertainty
    quarantines the root; never reset a password, replace a recipient, revoke
    another session, delete an account, or retry invite/accept automatically.
    The returned receipt proves lifecycle readback only, not binding or a gate.
    """

    _COOKIE = "__Host-oday_web_session"

    def __init__(self, *, web: Any, web_origin: str, journal: ProvisioningJournal) -> None:
        from urllib.parse import urlsplit

        origin = urlsplit(web_origin)
        if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment
                or not isinstance(journal, ProvisioningJournal)):
            raise ProvisioningRefused("PROVISIONING_TRANSPORT_INVALID")
        self._web = web
        self._origin = web_origin
        self._journal = journal

    def _request(
        self, method: str, path: str, *, cookie: str = "", body: Any = None, status: int = 200,
    ) -> Any:
        # No user bearer, actor, tenant or role headers, and no redirects.
        headers = {"accept": "application/json", "origin": self._origin}
        if cookie:
            headers["cookie"] = f"{self._COOKIE}={cookie}"
        try:
            response = self._web.request(method, path, authenticated=False, body=body,
                                         headers=headers, follow_redirects=False)
            if response.failed or response.status != status or not isinstance(response.payload, dict):
                raise ValueError("unusable response")
            return response
        except Exception:
            # Never expose upstream payload, exceptions, cookie or credentials.
            raise ProvisioningRefused("PROVISIONING_WEB_REQUEST_REFUSED") from None

    def _login(self, username: str, password: str) -> str:
        response = self._request("POST", "/login", body={
            "username": username, "password": password, "returnTo": "/operator?view=admin",
        })
        cookie = response.cookies.get(self._COOKIE)
        if (response.payload.get("ok") is not True or response.payload.get("subject") != username
                or not isinstance(cookie, str) or not cookie or len(cookie) > 8192
                or any(c in cookie for c in ";\r\n")):
            raise ProvisioningRefused("PROVISIONING_SESSION_INVALID")
        return cookie

    def _session(self, cookie: str, username: str) -> None:
        current = self._request("GET", "/auth/session", cookie=cookie).payload
        if current.get("subject") != username:
            raise ProvisioningRefused("PROVISIONING_SESSION_INVALID")

    def _users(self, cookie: str) -> list[dict[str, Any]]:
        records = self._request("GET", "/api/v1/operator/users", cookie=cookie).payload.get("users")
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            raise ProvisioningRefused("PROVISIONING_INVENTORY_INVALID")
        return records

    @staticmethod
    def _original(records: list[dict[str, Any]]) -> dict[str, Any]:
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

    def _principal(self, cookie: str, account: str, roles: list[str]) -> None:
        principal = self._request("GET", "/api/v1/auth/principal", cookie=cookie).payload
        observed = principal.get("roles")
        if (principal.get("account_id") != account or principal.get("tenant_id") != TENANT_ID
                or not isinstance(observed, list) or not all(isinstance(r, str) for r in observed)
                or sorted(observed) != sorted(roles)):
            raise ProvisioningRefused("PROVISIONING_PRINCIPAL_MISMATCH")

    def execute(
        self, plan: Any, *, admin_password: str, new_password: str,
        release_sha: str, manifest_digest: str,
    ) -> dict[str, Any]:
        """After foreground approval/admission: invite, accept, read back, logout.

        Secrets are separate memory-only arguments, never part of plan/receipt.
        Binding must use this same new pair only after this function succeeds.
        A reserved root cannot be replayed even after successful lifecycle proof.
        """
        from delivery_toolchain.e2e.check_live_e2e_gate import _invitation_provenance

        reservation = None
        admin_cookie = new_cookie = ""
        result = None
        failed = False
        try:
            if (not isinstance(admin_password, str) or not admin_password
                    or not isinstance(new_password, str) or not 12 <= len(new_password) <= 1024
                    or admin_password == new_password):
                raise ProvisioningRefused("PROVISIONING_CREDENTIAL_INPUT_INVALID")
            admin_cookie = self._login("ajoe734", admin_password)
            self._session(admin_cookie, "ajoe734")
            self._principal(admin_cookie, PRESERVED_ACCOUNT_ID, sorted(PRESERVED_ROLES))
            records = self._users(admin_cookie)
            original = self._original(records)
            checked = validate_foreground_plan(
                plan, original_account=original, release_sha=release_sha,
                manifest_digest=manifest_digest, now=self._journal._now(),
            )
            if any(str(r.get("username", "")).casefold() == checked.username.casefold()
                   or str(r.get("email", "")).casefold() == checked.email.casefold() for r in records):
                raise ProvisioningRefused("PROVISIONING_ACCOUNT_EXISTS")
            reservation = self._journal.reserve(plan, original_account=original,
                                                release_sha=release_sha, manifest_digest=manifest_digest)
            issued = self._request("POST", "/api/v1/operator/users/invitations", cookie=admin_cookie,
                                   body={"email": checked.email, "lifetime_seconds": 3600}, status=201).payload
            invitation = issued.get("invitation_id")
            token = issued.get("token")
            if (issued.get("status") != "invited" or issued.get("tenant_id") != TENANT_ID
                    or not isinstance(invitation, str) or str(UUID(invitation)) != invitation
                    or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)):
                raise ProvisioningRefused("PROVISIONING_INVITATION_INVALID")
            accepted = self._request("POST", "/auth/invitations", body={
                "invitation_id": invitation, "token": token, "username": checked.username,
                "password": new_password,
            }, status=201).payload
            account = accepted.get("account_id")
            if (accepted.get("status") != "accepted" or accepted.get("invitation_id") != invitation
                    or accepted.get("tenant_id") != TENANT_ID or not isinstance(account, str)
                    or str(UUID(account)) != account or account == PRESERVED_ACCOUNT_ID):
                raise ProvisioningRefused("PROVISIONING_ACCEPTANCE_INVALID")
            new_cookie = self._login(checked.username, new_password)
            self._session(new_cookie, checked.username)
            self._principal(new_cookie, account, ["platform_admin"])
            after = self._users(new_cookie)
            if self._original(after) != original:
                raise ProvisioningRefused("PROVISIONING_ORIGINAL_ACCOUNT_CHANGED")
            own = [r for r in after if r.get("subject_id") == account and r.get("username") == checked.username
                   and r.get("email") == checked.email
                   and r.get("attributes", {}).get("identity_source") == "identity.accounts"]
            events = self._request("GET", "/api/v1/operator/users/audit-trail", cookie=new_cookie).payload.get("events")
            provenance = (_invitation_provenance(own[0], events) if len(own) == 1
                          and isinstance(events, list) and all(isinstance(e, dict) for e in events) else None)
            if (provenance is None or provenance["invitation_id"] != invitation
                    or provenance["issuer_account_id"] != PRESERVED_ACCOUNT_ID
                    or provenance["issue_event_id"] != issued.get("audit_event_id")
                    or provenance["accept_event_id"] != accepted.get("audit_event_id")):
                raise ProvisioningRefused("PROVISIONING_PROVENANCE_INVALID")
            result = {**checked.to_receipt(), "stage": "web-lifecycle-verified",
                      "account_id": account, **provenance, "credential_binding_verified": False,
                      "deployment_success": False, "live_gate_passed": False}
        except Exception:
            failed = True
        finally:
            # Logout only the two sessions created here; never revoke other sessions.
            for cookie in (new_cookie, admin_cookie):
                if cookie:
                    try:
                        logout = self._request("POST", "/auth/logout", cookie=cookie).payload
                        if logout.get("ok") is not True:
                            failed = True
                        self._request("GET", "/auth/session", cookie=cookie, status=401)
                    except Exception:
                        failed = True
            if failed and reservation is not None:
                try:
                    self._journal.require_recovery(reservation)
                except Exception:
                    # Reservation itself remains the durable no-retry boundary.
                    pass
        if failed or result is None:
            raise ProvisioningRefused("PROVISIONING_LIFECYCLE_RECOVERY_REQUIRED" if reservation
                                     else "PROVISIONING_LIFECYCLE_REFUSED") from None
        return result
