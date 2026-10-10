"""Foreground dev-smoke provisioning preflight; deliberately no executor/CLI yet.

This pure validator checks a custodian's NON-SECRET proposed execution binding.
It does not authenticate that custodian, verify release admission, consume a
one-time authorization, prove mailbox ownership, or authorize a cloud mutation.
Those controls must be implemented before any caller can issue/accept an invite
or bind credentials. Default workflows do not import or invoke this module.

Account input must come from the existing authenticated identity readback, not
caller headers or an offline receipt. Credentials/capabilities are not accepted
in a plan and must remain exclusively in the eventual executor's memory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

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
    durably consume execution_id against this exact tuple before any mutation.
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
