"""Foreground preflight, reservation journal and memory-only Web lifecycle.

The pure validator checks a custodian's NON-SECRET proposed execution binding.
The optional PostgreSQL journal reserves that exact plan once and can quarantine
it after an uncertain result. Neither authenticates the custodian, verifies
release admission/human approval, proves mailbox ownership, or authorizes a cloud
mutation. The Web lifecycle below is callable only by the trusted foreground
coordinator after source approval and exact release admission; it does not
implement those control-plane checks. The optional encrypted bundle writer below
composes the same lifecycle/password in one call but cannot prove the stored
secret value. The release workflow consumes an already staged bundle through the
strict memory-only reader; it never invokes the lifecycle/writer. There is
intentionally no anonymous provisioning CLI.

Account input must come from the existing authenticated identity readback, not
caller headers or an offline receipt. Credentials/capabilities are not accepted
in a plan and remain exclusively in executor/encryption memory.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
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

    def _promoted_release(self, cookie: str, checked: ForegroundPlan) -> None:
        """Observe BOTH revisions through the same authenticated Web/BFF path.

        Caller tuple, independent API URL, plan/journal and offline receipts are
        not serving-revision evidence. This readback is not source approval or
        admission authority: the trusted coordinator must still verify those.
        Missing metadata and mixed/rolled-back revisions always refuse.
        """
        identity = self._request("GET", "/api/v1/platform/release-identity", cookie=cookie).payload
        expected = {
            "release_sha": checked.release_sha, "web_release_sha": checked.release_sha,
            "manifest_digest": checked.manifest_digest, "web_manifest_digest": checked.manifest_digest,
            "release_profile": "dev-admin", "web_release_profile": "dev-admin",
        }
        if (identity.get("release_profile_valid") is not True
                or any(type(identity.get(key)) is not str or identity[key] != value
                       for key, value in expected.items())):
            raise ProvisioningRefused("PROVISIONING_PROMOTED_RELEASE_MISMATCH")

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
            # Observe the serving pair before consuming the single-use root.
            self._promoted_release(admin_cookie, checked)
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
            # A rollback/mixed-revision transition after issue cannot authorize
            # acceptance. Leave the invitation reserved for explicit recovery.
            self._promoted_release(admin_cookie, checked)
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
            self._promoted_release(new_cookie, checked)
            result = {**checked.to_receipt(), "stage": "web-lifecycle-verified",
                      "serving_release_observed": True,
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


class GitHubDevSecretStore:
    """Pinned GitHub HTTPS API, no CLI, redirects, retries or plaintext uploads.

    Only the trusted foreground custodian supplies the token. The coordinator
    must exclude concurrent external secret writers: GitHub has no create-only
    conditional PUT. No secret value can be read back through this API.
    """

    NAME = "ODP_DEV_ADMIN_CREDENTIAL_BUNDLE"

    def __init__(self, *, token: str, transport: Any = None) -> None:
        import httpx

        if not isinstance(token, str) or not token or any(c in token for c in "\r\n"):
            raise ProvisioningRefused("PROVISIONING_GITHUB_CONFIG_INVALID")
        self._client = httpx.Client(
            base_url="https://api.github.com", transport=transport, timeout=20,
            follow_redirects=False, headers={
                "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            return self._client.request(method, path, **kwargs)
        except Exception:
            raise ProvisioningRefused("PROVISIONING_GITHUB_UNCERTAIN") from None

    def prepare(self) -> tuple[str, str, str]:
        """Read non-secret repository identity, secret absence and encryption key."""
        import base64

        try:
            response = self._request("GET", f"/repos/{REPOSITORY}")
            repo = response.json()
            if (response.status_code != 200 or repo.get("full_name") != REPOSITORY
                    or type(repo.get("id")) is not int or repo["id"] <= 0):
                raise ValueError("repository mismatch")
            path = f"/repositories/{repo['id']}/environments/dev/secrets"
            existing = self._request("GET", f"{path}/{self.NAME}")
            if existing.status_code != 404:
                raise ValueError("binding exists or absence unproven")
            response = self._request("GET", f"{path}/public-key")
            key = response.json()
            if (response.status_code != 200 or not isinstance(key.get("key_id"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key["key_id"])
                    or not isinstance(key.get("key"), str)
                    or len(base64.b64decode(key["key"], validate=True)) != 32):
                raise ValueError("invalid key")
            return path, key["key_id"], key["key"]
        except Exception:
            raise ProvisioningRefused("PROVISIONING_GITHUB_PREFLIGHT_REFUSED") from None

    def write(self, prepared: tuple[str, str, str], bundle: dict[str, Any]) -> None:
        import base64
        from nacl.public import PublicKey, SealedBox

        try:
            path, key_id, public_key = prepared
            clear = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
            encrypted = SealedBox(PublicKey(base64.b64decode(public_key, validate=True))).encrypt(clear)
            response = self._request("PUT", f"{path}/{self.NAME}", json={
                "key_id": key_id, "encrypted_value": base64.b64encode(encrypted).decode(),
            })
            # 204 would mean an existing secret was replaced (external writer race).
            # A timeout/204/error is never inferred to be an acknowledged creation.
            if response.status_code != 201:
                raise ValueError("creation not acknowledged")
        except Exception:
            raise ProvisioningRefused("PROVISIONING_GITHUB_UNCERTAIN") from None


@dataclass(frozen=True)
class DevCredentialBundle:
    """Memory-only matched pair. Never serialize, print or pass to a subprocess."""

    username: str
    password: str = field(repr=False)
    account_id: str
    tenant_id: str
    execution_id: str


def read_dev_credential_bundle(
    raw: str, *, environment: str, release_profile: str,
) -> DevCredentialBundle | None:
    """Empty means legacy binding; any nonempty invalid bundle refuses fallback.

    This is a secret/configuration reader, NOT human approval or lifecycle proof.
    The unchanged gate must still authenticate the account and prove invitation
    provenance. The bundle is standing dev configuration, not candidate-specific.
    Neither an acknowledged upload nor a successful parse is activation evidence.
    """
    if raw == "":
        return None
    try:
        if (not isinstance(raw, str) or len(raw) > 16384
                or environment != "dev" or release_profile != "dev-admin"):
            raise ValueError

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        bundle = json.loads(raw, object_pairs_hook=unique)
        keys = {"schema_version", "authorization_id", "execution_id", "repository",
                "environment", "tenant_id", "account_id", "username", "password"}
        if (not isinstance(bundle, dict) or set(bundle) != keys
                or type(bundle["schema_version"]) is not int or bundle["schema_version"] != 1
                or not all(type(bundle[k]) is str for k in keys - {"schema_version"})
                or bundle["authorization_id"] != AUTHORIZATION_ID
                or bundle["repository"] != REPOSITORY or bundle["environment"] != "dev"
                or bundle["tenant_id"] != TENANT_ID
                or not _USERNAME.fullmatch(bundle["username"])
                or bundle["username"].casefold() == "ajoe734"
                or not 12 <= len(bundle["password"]) <= 1024):
            raise ValueError
        for key in ("account_id", "execution_id"):
            value = UUID(bundle[key])
            if value.int == 0 or str(value) != bundle[key]:
                raise ValueError
        if bundle["account_id"] == PRESERVED_ACCOUNT_ID:
            raise ValueError
        return DevCredentialBundle(bundle["username"], bundle["password"], bundle["account_id"],
                                   bundle["tenant_id"], bundle["execution_id"])
    except Exception:
        # JSON decoding/UUID errors may embed secret input: static code only.
        raise ProvisioningRefused("PROVISIONING_CREDENTIAL_BUNDLE_INVALID") from None


def credential_bundle_acknowledged(bundle: DevCredentialBundle, events: list[Any]) -> bool:
    """Authenticated tenant-audit projection only; never a local upload receipt.

    Requires reserved root + intent + durable ACK, matching execution/account and
    original tuple. Recovery/extra/ambiguous events fail closed. Standing binding
    may be used for later admitted candidates without relabelling its creation.
    """
    try:
        grouped = []
        for cls, stages in ((ProvisioningJournal, ["reserved"]),
                            (DevCredentialBundleExecutor, ["binding-intent", "binding-acknowledged"])):
            found = [e for e in events if e.get("event_type") == cls._TYPE
                     or e.get("correlation_id") == cls._CORRELATION]
            found.sort(key=lambda e: datetime.fromisoformat(e["timestamp"]))
            if len(found) != len(stages):
                return False
            for event, stage in zip(found, stages):
                m = event["metadata"]
                if (event["event_type"] != cls._TYPE or event["actor"] != cls._ACTOR
                        or event["action"] != ("DEV_SMOKE_RESERVATION" if cls is ProvisioningJournal else "DEV_SMOKE_BINDING")
                        or event["resource"] != cls._CORRELATION or event["correlation_id"] != cls._CORRELATION
                        or event["outcome"] != "success" or not _aware(datetime.fromisoformat(event["timestamp"]))
                        or str(UUID(event["event_id"])) != event["event_id"] or UUID(event["event_id"]).int == 0
                        or set(m) != cls._KEYS or m["stage"] != stage
                        or m["authorization_id"] != AUTHORIZATION_ID or m["tenant_id"] != bundle.tenant_id
                        or m["execution_id"] != bundle.execution_id or m["execution_authorized"] is not False
                        or m["secret_values_redacted"] is not True or not _SHA.fullmatch(m["release_sha"])
                        or not _DIGEST.fullmatch(m["manifest_digest"])
                        or not _DIGEST.fullmatch(m["plan_digest"])):
                    return False
                if cls is DevCredentialBundleExecutor and (
                        m["account_id"] != bundle.account_id or m["secret_name"] != GitHubDevSecretStore.NAME
                        or m["credential_binding_verified"] is not False):
                    return False
            grouped.append(found)
        ordered = grouped[0] + grouped[1]
        if len({e["event_id"] for e in ordered}) != 3:
            return False
        base = grouped[0][0]["metadata"]
        for e in grouped[1]:
            if any(e["metadata"][k] != base[k] for k in ProvisioningJournal._KEYS - {"stage"}):
                return False
        return all(datetime.fromisoformat(a["timestamp"]) <= datetime.fromisoformat(b["timestamp"])
                   for a, b in zip(ordered, ordered[1:]))
    except Exception:
        return False


class DevCredentialBundleExecutor:
    """Foreground lifecycle + same-pair encrypted staging, NOT rollout authority.

    A SINGLE encrypted JSON secret contains the matched username/password plus
    tuple/account identifiers. It cannot mix the preserved account's password or
    optional bootstrap credential. Existing vars/secrets are untouched. The
    workflow consumes the bundle only as a matched pair; acknowledged PUT is NOT
    proof of secret value, deployment or gate success. The approved coordinator must own
    source/admission/custody checks before calling this library.

    Durable intent precedes PUT. Crash/lost reply/readback/journal failures leave
    intent or quarantine as a no-retry boundary. No password reset, secret delete,
    value retrieval, rollback, replacement or automatically repeated PUT exists.
    """

    _CORRELATION = f"dev-smoke-binding:{AUTHORIZATION_ID}"
    _ACTOR = "system:dev-smoke-credential-binding"
    _TYPE = "release.dev_smoke.binding"
    _KEYS = frozenset({
        "authorization_id", "execution_id", "plan_digest", "release_sha", "manifest_digest",
        "tenant_id", "account_id", "secret_name", "stage", "execution_authorized",
        "credential_binding_verified", "secret_values_redacted",
    })

    def __init__(self, *, lifecycle: WebInvitationExecutor, store: GitHubDevSecretStore) -> None:
        if not isinstance(lifecycle, WebInvitationExecutor) or not isinstance(store, GitHubDevSecretStore):
            raise ProvisioningRefused("PROVISIONING_BINDING_CONFIG_INVALID")
        self._lifecycle = lifecycle
        self._journal = lifecycle._journal
        self._store = store

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
                    or m["secret_name"] != self._store.NAME
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
        """Restart diagnosis only; a pending intent MUST NOT be retried."""
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
            if ((stage == "binding-intent" and events)
                    or (stage != "binding-intent" and (len(events) != 1
                        or metadata != events[0].metadata))):
                raise ProvisioningRefused("PROVISIONING_BINDING_ALREADY_ATTEMPTED")
            event = self._journal._audit.record(AuditEvent(
                event_type=self._TYPE, actor=self._ACTOR, action="DEV_SMOKE_BINDING",
                resource=self._CORRELATION, correlation_id=self._CORRELATION, outcome="success",
                occurred_at=self._journal._now(), metadata={**metadata, "stage": stage},
            ))
        return event

    def execute_and_check_gate(
        self, plan: Any, *, gate_config: Any, worker_job: str,
        gcp_region: str, gcp_project: str, **credentials: Any,
    ) -> dict[str, Any]:
        """Foreground-only same-process staging -> unchanged final live gate.

        The caller must already hold authenticated custody/source approval and
        exact dev admission, and own the promoted rollout's rollback boundary.
        This library does NOT promote traffic or commit deployment. It avoids
        GitHub's stale same-job secret context by passing the newly accepted pair
        directly to the canonical evaluator in memory, never to shell/env/argv.
        It does not verify GitHub's decrypted value: a later ordinary consumer
        must still do that. No caller gate callback or passing receipt is accepted.
        """
        from delivery_toolchain.e2e import check_live_e2e_gate as gate

        # Validate every known gate input before login, reservation or PUT. The
        # template's standing/initial credentials are never used or inherited.
        try:
            if (type(gate_config) is not gate.GateConfig or not isinstance(plan, dict)
                    or gate_config.expected_sha != credentials["release_sha"]
                    or gate_config.expected_manifest_digest != credentials["manifest_digest"]
                    or gate_config.release_profile != "dev-admin"
                    or gate_config.expected_deployment != "dev"
                    or gate_config.allow_http is not False
                    or gate_config.external_provider_mode != "disabled"
                    or gate_config.web_url != self._lifecycle._origin
                    or any(not isinstance(v, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", v)
                           for v in (worker_job, gcp_region, gcp_project))):
                raise ValueError
            config = replace(
                gate_config, dev_admin_username=plan["username"],
                dev_admin_password=credentials["new_password"],
                dev_admin_initial_password="", bootstrap_admin_username="",
                bootstrap_admin_password="", dev_admin_bundle_account_id="",
                dev_admin_bundle_tenant_id="", dev_admin_bundle_execution_id="",
            )
            if not all(c.ok for c in gate.validate_config(config)):
                raise ValueError
        except Exception:
            raise ProvisioningRefused("PROVISIONING_GATE_CONFIG_INVALID") from None

        receipt = self.execute(plan, **credentials)
        try:
            config = replace(
                config, dev_admin_bundle_account_id=receipt["account_id"],
                dev_admin_bundle_tenant_id=TENANT_ID,
                dev_admin_bundle_execution_id=receipt["execution_id"],
            )
            correlation = f"corr-dev-smoke-{receipt['execution_id']}"
            http = gate.UrllibHttpClient(
                gate._normalize_origin(config.api_url, allow_http=False), timeout=config.timeout,
                bearer_token=config.bearer_token, operator_role=config.operator_role,
                operator_subject=config.operator_subject, operator_tenant=config.operator_tenant,
                correlation_id=correlation, transport_token=config.api_transport_token,
            )
            worker = gate.CloudRunWorkerDriver(
                job=worker_job, region=gcp_region, project=gcp_project,
                max_jobs=len(config.snapshot_provider_ids) + 4,
                timeout=max(config.worker_deadline_seconds, 60.0),
            )
            checks, report = gate.evaluate_gate(
                config, http=http, worker_driver=worker, correlation_id=correlation,
                now=self._journal._now().isoformat(),
                web_http=gate._web_client(config, correlation),
            )
            # Only the canonical evaluator's complete result counts, not an
            # acknowledged upload, point-in-time readback or truthy status value.
            passed = bool(checks) and all(c.ok is True for c in checks)
            if (type(report.get("ok")) is not bool or report["ok"] != passed
                    or report.get("expected_release_sha") != config.expected_sha
                    or report.get("expected_deployment") != "dev"):
                raise ValueError
            if not passed:
                self._journal.require_recovery(self._journal.inspect())
            return {"provisioning": {**receipt, "live_gate_passed": passed,
                                     "deployment_success": False,
                                     "credential_binding_verified": False},
                    "gate": report}
        except Exception:
            try:
                self._journal.require_recovery(self._journal.inspect())
            except Exception:
                pass  # Reserved root/binding ACK still prevent lifecycle retry.
            raise ProvisioningRefused("PROVISIONING_GATE_RECOVERY_REQUIRED") from None

    def execute(self, plan: Any, **credentials: Any) -> dict[str, Any]:
        """Same password goes to acceptance/fresh-login AND encrypted bundle.

        No caller-provided lifecycle receipt is accepted as a creation proof.
        API acknowledgement cannot verify the decrypted GitHub secret: only a
        future normally admitted consumer's unchanged live gate can do that.
        """
        reservation = None
        metadata = None
        try:
            # Fail before account mutation if a staged binding already exists,
            # journal is unavailable, repository/key is wrong, or auth is refused.
            if self.inspect() is not None:
                raise ProvisioningRefused("PROVISIONING_BINDING_ALREADY_ATTEMPTED")
            prepared = self._store.prepare()
            lifecycle = self._lifecycle.execute(plan, **credentials)
            reservation = self._journal.inspect()
            if reservation is None or reservation.stage != "reserved":
                raise ProvisioningRefused("PROVISIONING_RESERVATION_MISMATCH")
            metadata = {
                "authorization_id": AUTHORIZATION_ID, "execution_id": reservation.execution_id,
                "plan_digest": reservation.plan_digest, "release_sha": lifecycle["release_sha"],
                "manifest_digest": lifecycle["manifest_digest"], "tenant_id": TENANT_ID,
                "account_id": lifecycle["account_id"], "secret_name": self._store.NAME,
                "stage": "binding-intent", "execution_authorized": False,
                "credential_binding_verified": False, "secret_values_redacted": True,
            }
            self._append(metadata, "binding-intent")
            # Contains no initial-password fallback, token, email or admin password.
            bundle = {"schema_version": 1, "authorization_id": AUTHORIZATION_ID,
                      "execution_id": reservation.execution_id, "repository": REPOSITORY,
                      "environment": "dev", "tenant_id": TENANT_ID,
                      "account_id": lifecycle["account_id"], "username": plan["username"],
                      "password": credentials["new_password"]}
            self._store.write(prepared, bundle)
            event = self._append(metadata, "binding-acknowledged")
            return {**lifecycle, "stage": "binding-acknowledged", "binding_audit_event_id": event.event_id,
                    "binding_write_acknowledged": True, "credential_binding_verified": False,
                    "live_gate_passed": False, "deployment_success": False}
        except Exception:
            if reservation is not None:
                if metadata is not None:
                    try:
                        self._append(metadata, "recovery-required")
                    except Exception:
                        pass  # Committed intent still forbids retry even if quarantine is unavailable.
                try:
                    self._journal.require_recovery(reservation)
                except Exception:
                    pass  # Root reservation still forbids account recreation.
            raise ProvisioningRefused("PROVISIONING_BINDING_RECOVERY_REQUIRED" if reservation
                                     else "PROVISIONING_BINDING_REFUSED") from None
