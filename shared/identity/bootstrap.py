"""Deployment-only first administrator bootstrap.

Task: ODP-DEV-ADMIN-RELEASE-READINESS-001
Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §7.1, §7.2, §8.1

The invite-only account model has exactly one way to obtain the first
``platform_admin``: a one-time, deployment-time bootstrap that reads
``ODP_IDENTITY_BOOTSTRAP_SECRET`` and creates a single ``active`` account whose
password credential carries ``must_change=true``. There is no HTTP route for
this: the entrypoint is ``python -m shared.identity.bootstrap``, which can only
be run by an operator who already holds the database credentials of the target
environment (that database authority *is* the authentication of this path).

Guarantees
----------
* **Idempotent no-op** — if ``identity.accounts`` holds any ``active`` account
  the bootstrap writes nothing and reports ``noop_active_account_exists``.
* **Never overwrites** — an existing row with the same tenant username/email
  (whatever its status) is refused; no password is ever reset here.
* **Concurrency safe** — the existence check and every insert run inside one
  PostgreSQL transaction holding a transaction-scoped advisory lock, so two
  concurrent runs cannot both create an administrator.
* **Audited atomically** — the ``identity.account.bootstrap`` audit event is
  written on the same connection and transaction as the account, credential and
  role rows. If the audit write fails, nothing is committed.
* **Fail closed on input** — a missing, malformed, weak or expired secret, or a
  malformed tenant/username/email, refuses before any database access.
* **No credential output** — neither the secret nor its hash appears in the
  result, exceptions, logs or the audit event.

The one-time secret is the administrator's *initial* password. Because the
credential is created with ``must_change=true``, the API refuses every
protected operation for that session until the password is rotated through the
web ``/auth/password`` flow (see ``apps/api/oday_api/security/dependencies.py``).
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from shared.audit import AuditEvent
from shared.auth import Role

from .credential_service import CredentialService
from .password_policy import PasswordPolicy

BOOTSTRAP_SECRET_ENV = "ODP_IDENTITY_BOOTSTRAP_SECRET"
BOOTSTRAP_EXPIRES_AT_ENV = "ODP_IDENTITY_BOOTSTRAP_EXPIRES_AT"
BOOTSTRAP_TENANT_ENV = "ODP_IDENTITY_BOOTSTRAP_TENANT_ID"
BOOTSTRAP_USERNAME_ENV = "ODP_IDENTITY_BOOTSTRAP_USERNAME"
BOOTSTRAP_EMAIL_ENV = "ODP_IDENTITY_BOOTSTRAP_EMAIL"
BOOTSTRAP_DISPLAY_NAME_ENV = "ODP_IDENTITY_BOOTSTRAP_DISPLAY_NAME"

BOOTSTRAP_AUDIT_EVENT = "identity.account.bootstrap"
BOOTSTRAP_ACTOR = "identity-bootstrap"
# Same upper bound as an invitation token (Contract §7.3): a bootstrap secret
# is a one-shot credential and must not be valid indefinitely.
MAX_SECRET_LIFETIME = timedelta(hours=72)
# pg_advisory_xact_lock key: stable, arbitrary, and unique to this operation.
_ADVISORY_LOCK_KEY = 0x0D9_1D_B007

_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")

STATUS_CREATED = "created"
STATUS_NOOP = "noop_active_account_exists"


class BootstrapRefused(Exception):
    """The bootstrap request is invalid or unsafe; nothing was written.

    Messages name the failing *field* only, never a secret value.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BootstrapRequest:
    tenant_id: UUID
    username: str
    email: str
    display_name: str
    secret: str = field(default="", repr=False)
    expires_at: datetime | None = None

    def __repr__(self) -> str:  # never render the secret
        return (
            f"BootstrapRequest(tenant_id={self.tenant_id!s}, username={self.username!r}, "
            f"email={self.email!r}, expires_at={self.expires_at!s}, secret=<redacted>)"
        )


@dataclass(frozen=True)
class BootstrapResult:
    status: str
    account_id: str | None
    tenant_id: str
    username: str
    audit_event_id: str | None = None

    def to_receipt(self) -> dict[str, Any]:
        """Receipt safe for logs/workflow output: identifiers only."""

        return {
            "status": self.status,
            "account_id": self.account_id,
            "tenant_id": self.tenant_id,
            "username": self.username,
            "role": Role.PLATFORM_ADMIN.value,
            "must_change": self.status == STATUS_CREATED,
            "audit_event": BOOTSTRAP_AUDIT_EVENT if self.audit_event_id else None,
            "audit_event_id": self.audit_event_id,
        }


def _parse_expiry(raw: str, now: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise BootstrapRefused(
            "BOOTSTRAP_EXPIRY_MALFORMED", f"{BOOTSTRAP_EXPIRES_AT_ENV} is not an ISO-8601 time"
        ) from exc
    if parsed.tzinfo is None:
        raise BootstrapRefused(
            "BOOTSTRAP_EXPIRY_MALFORMED", f"{BOOTSTRAP_EXPIRES_AT_ENV} must carry a UTC offset"
        )
    if parsed <= now:
        raise BootstrapRefused("BOOTSTRAP_SECRET_EXPIRED", "bootstrap secret has expired")
    if parsed - now > MAX_SECRET_LIFETIME:
        raise BootstrapRefused(
            "BOOTSTRAP_EXPIRY_TOO_LONG",
            f"{BOOTSTRAP_EXPIRES_AT_ENV} may be at most 72 hours in the future",
        )
    return parsed


def request_from_env(
    env: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
    policy: PasswordPolicy | None = None,
) -> BootstrapRequest:
    """Validate the deployment inputs. Refuses before any database access."""

    source = os.environ if env is None else env
    current = now or datetime.now(UTC)

    def required(name: str) -> str:
        value = (source.get(name) or "").strip()
        if not value:
            raise BootstrapRefused("BOOTSTRAP_INPUT_MISSING", f"{name} is required")
        return value

    secret = source.get(BOOTSTRAP_SECRET_ENV) or ""
    if not secret.strip():
        raise BootstrapRefused("BOOTSTRAP_INPUT_MISSING", f"{BOOTSTRAP_SECRET_ENV} is required")
    expires_at = _parse_expiry(required(BOOTSTRAP_EXPIRES_AT_ENV), current)

    try:
        tenant_id = UUID(required(BOOTSTRAP_TENANT_ENV))
    except ValueError as exc:
        raise BootstrapRefused(
            "BOOTSTRAP_INPUT_MALFORMED", f"{BOOTSTRAP_TENANT_ENV} must be a UUID"
        ) from exc
    username = required(BOOTSTRAP_USERNAME_ENV)
    if not _USERNAME_RE.match(username):
        raise BootstrapRefused(
            "BOOTSTRAP_INPUT_MALFORMED",
            f"{BOOTSTRAP_USERNAME_ENV} must be 3-64 chars of [A-Za-z0-9._-]",
        )
    email = required(BOOTSTRAP_EMAIL_ENV)
    if not _EMAIL_RE.match(email):
        raise BootstrapRefused("BOOTSTRAP_INPUT_MALFORMED", f"{BOOTSTRAP_EMAIL_ENV} is not an email")
    display_name = (source.get(BOOTSTRAP_DISPLAY_NAME_ENV) or "").strip()[:255]

    active_policy = policy or PasswordPolicy()
    normalized = active_policy.normalize(secret)
    result = active_policy.validate(normalized, username=username, email=email)
    if not result.valid:
        codes = ",".join(v.code for v in result.violations)
        raise BootstrapRefused(
            "BOOTSTRAP_SECRET_REJECTED",
            f"{BOOTSTRAP_SECRET_ENV} violates the password policy ({codes})",
        )
    return BootstrapRequest(
        tenant_id=tenant_id,
        username=username,
        email=email,
        display_name=display_name,
        secret=normalized,
        expires_at=expires_at,
    )


def bootstrap_first_admin(
    engine: Any,
    audit_log: Any,
    request: BootstrapRequest,
    *,
    credential_service: CredentialService | None = None,
    hash_password: Callable[[str], str] | None = None,
    now: datetime | None = None,
) -> BootstrapResult:
    """Create the first ``platform_admin`` or no-op; one transaction, audited.

    ``engine`` is a :class:`~shared.infrastructure.persistence.postgresql.PostgresEngine`
    and ``audit_log`` the durable audit log bound to that same engine, so that
    ``engine.lock`` makes the identity rows and the audit row one transaction.
    """

    current = now or datetime.now(UTC)
    if request.expires_at is None or request.expires_at <= current:
        raise BootstrapRefused("BOOTSTRAP_SECRET_EXPIRED", "bootstrap secret has expired")
    if str(getattr(engine, "dialect", "")).lower() != "postgresql":
        raise BootstrapRefused(
            "BOOTSTRAP_REQUIRES_POSTGRESQL",
            "identity bootstrap only runs against the durable PostgreSQL identity schema",
        )
    # Hash before taking the lock: Argon2id is deliberately slow and must not
    # extend the critical section.
    hasher = hash_password or (credential_service or CredentialService()).hash_password
    phc_hash = hasher(request.secret)
    params = json.dumps(
        CredentialService.extract_params_from_phc(phc_hash) if phc_hash.startswith("$argon2id$") else {}
    )

    tenant = str(request.tenant_id)
    with engine.lock:
        engine.execute("SELECT pg_advisory_xact_lock(?)", (_ADVISORY_LOCK_KEY,))
        active = engine.query_one(
            "SELECT account_id FROM identity.accounts WHERE status = 'active' LIMIT 1"
        )
        if active is not None:
            return BootstrapResult(
                status=STATUS_NOOP, account_id=None, tenant_id=tenant, username=request.username
            )
        clash = engine.query_one(
            "SELECT account_id FROM identity.accounts "
            "WHERE tenant_id = ? AND (lower(username) = lower(?) OR lower(email) = lower(?)) "
            "LIMIT 1",
            (tenant, request.username, request.email),
        )
        if clash is not None:
            raise BootstrapRefused(
                "BOOTSTRAP_ACCOUNT_EXISTS",
                "an account with this username or email already exists; bootstrap never "
                "overwrites or reactivates accounts",
            )

        account_id = str(uuid4())
        engine.execute(
            "INSERT INTO identity.accounts (account_id, tenant_id, username, email, "
            "display_name, status, created_at, created_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)",
            (
                account_id,
                tenant,
                request.username,
                request.email,
                request.display_name,
                current,
                BOOTSTRAP_ACTOR,
                current,
            ),
        )
        engine.execute(
            "INSERT INTO identity.password_credentials (account_id, algorithm, phc_hash, "
            "params, must_change, last_rotated_at, updated_at) "
            "VALUES (?, 'argon2id', ?, CAST(? AS jsonb), true, ?, ?)",
            (account_id, phc_hash, params, current, current),
        )
        engine.execute(
            "INSERT INTO identity.account_roles (account_id, role, granted_at, granted_by) "
            "VALUES (?, ?, ?, ?)",
            (account_id, Role.PLATFORM_ADMIN.value, current, BOOTSTRAP_ACTOR),
        )
        event = audit_log.record(
            AuditEvent(
                event_type=BOOTSTRAP_AUDIT_EVENT,
                actor=BOOTSTRAP_ACTOR,
                action="IDENTITY_ACCOUNT_BOOTSTRAP",
                resource=f"identity.account:{account_id}",
                outcome="success",
                correlation_id=f"identity-bootstrap-{account_id}",
                occurred_at=current,
                metadata={
                    "tenant_id": tenant,
                    "account_id": account_id,
                    "username": request.username,
                    "roles": [Role.PLATFORM_ADMIN.value],
                    "must_change": True,
                    "secret_expires_at": request.expires_at.isoformat(),
                },
            )
        )
    return BootstrapResult(
        status=STATUS_CREATED,
        account_id=account_id,
        tenant_id=tenant,
        username=request.username,
        audit_event_id=event.event_id,
    )


def _database_url(env: Mapping[str, str]) -> str:
    for name in ("ODP_IDENTITY_DATABASE_URL", "ODAY_DATABASE_URL", "DATABASE_URL"):
        value = (env.get(name) or "").strip()
        if value:
            return value
    raise BootstrapRefused(
        "BOOTSTRAP_DATABASE_MISSING",
        "ODP_IDENTITY_DATABASE_URL, ODAY_DATABASE_URL or DATABASE_URL is required",
    )


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """Deployment entrypoint. Prints only the identifier receipt as JSON."""

    del argv
    source = os.environ if env is None else env
    try:
        request = request_from_env(source)
        from shared.audit.worm import build_audit_worm_sink_from_env
        from shared.infrastructure.persistence.audit_log import DurableAuditLog
        from shared.infrastructure.persistence.postgresql import PostgresEngine

        engine = PostgresEngine(_database_url(source), bootstrap=False, validate_schema=False)
        try:
            for relation in (
                "identity.accounts",
                "identity.password_credentials",
                "identity.account_roles",
                "odp_runtime.durable_audit_events",
            ):
                row = engine.query_one("SELECT to_regclass(?) AS relation", (relation,))
                if row is None or row["relation"] is None:
                    raise BootstrapRefused(
                        "BOOTSTRAP_SCHEMA_MISSING", f"required relation {relation} is missing"
                    )
            audit_log = DurableAuditLog(engine, worm_sink=build_audit_worm_sink_from_env())
            result = bootstrap_first_admin(engine, audit_log, request)
        finally:
            engine.close()
    except BootstrapRefused as exc:
        print(json.dumps({"status": "refused", "code": exc.code, "reason": str(exc)}))
        return 2
    except Exception as exc:  # noqa: BLE001 - only the class name; messages may echo DSNs
        print(json.dumps({"status": "failed", "code": "BOOTSTRAP_FAILED", "error": type(exc).__name__}))
        return 1
    print(json.dumps(result.to_receipt(), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main() in tests
    sys.exit(main())
