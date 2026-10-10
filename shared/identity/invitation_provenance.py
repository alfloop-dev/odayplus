"""Pure invitation provenance predicates for authenticated identity/audit reads.

Not an authentication or approval mechanism. Callers must obtain records through
existing verified tenant/session boundaries; arbitrary receipts grant nothing.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def canonical_uuid(value: Any) -> str | None:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return None


def identity_snapshot(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The identity facts a refused tenant move must leave untouched.

    Returns ``None`` when any of them is absent, so a readback that drops a
    field is treated as changed rather than silently compared as missing.
    """
    subject_id = record.get("subject_id")
    username = record.get("username")
    roles = record.get("roles")
    status = record.get("status")
    scope = record.get("scope")
    if not (
        isinstance(subject_id, str)
        and subject_id
        and isinstance(username, str)
        and username
        and isinstance(roles, list)
        and roles
        and isinstance(status, str)
        and status
        and isinstance(scope, dict)
        and canonical_uuid(scope.get("tenant_id")) is not None
    ):
        return None
    return {
        "subject_id": subject_id,
        "username": username,
        "roles": sorted(str(r) for r in roles),
        "status": status,
        "scope": json.loads(json.dumps(scope, sort_keys=True)),
    }


def invitation_provenance(
    record: Mapping[str, Any], events: Sequence[Mapping[str, Any]],
) -> dict[str, str] | None:
    """Bind a pure admin to one real issue/accept pair, never a fake bootstrap.

    Inputs come only from the authenticated identity administration readback.
    No receipt/config switch can waive the existing role or release checks.
    """
    account = record.get("subject_id")
    snapshot = identity_snapshot(record)
    if snapshot is None or canonical_uuid(account) != account:
        return None
    tenant = snapshot["scope"]["tenant_id"]
    fixed_scope = {"tenant_id": tenant, "clearance": "CONFIDENTIAL", **{
        axis: [] for axis in ("brand_ids", "region_ids", "store_ids", "assigned_area_ids",
                             "heat_zone_ids", "modules")
    }}
    if snapshot["roles"] != ["platform_admin"] or snapshot["status"] != "active" or snapshot["scope"] != fixed_scope:
        return None
    accepts = [e for e in events if e.get("event_type") == "identity.account.accept"
               and (e.get("actor") == account or _as_dict(e.get("metadata")).get("account_id") == account)]
    if len(accepts) != 1 or any(
        e.get("event_type") == "identity.account.bootstrap"
        and _as_dict(e.get("metadata")).get("account_id") == account for e in events
    ):
        return None
    accept = accepts[0]
    meta = _as_dict(accept.get("metadata"))
    invitation = meta.get("invitation_id")
    if canonical_uuid(invitation) != invitation or invitation is None:
        return None
    issues = [e for e in events if e.get("event_type") == "identity.account.invite"
              and _as_dict(e.get("metadata")).get("invitation_id") == invitation]
    # A duplicate/revoked/replayed lifecycle is not provenance for this account.
    related_accepts = [e for e in events if e.get("event_type") == "identity.account.accept"
                       and _as_dict(e.get("metadata")).get("invitation_id") == invitation]
    if len(issues) != 1 or len(related_accepts) != 1 or any(
        e.get("event_type") == "identity.account.invitation_revoked"
        and _as_dict(e.get("metadata")).get("invitation_id") == invitation for e in events
    ):
        return None
    issue = issues[0]
    preset = _as_dict(issue.get("metadata"))
    actor = issue.get("actor")
    if actor is None or canonical_uuid(actor) != actor or actor == account or record.get("updated_by") != actor:
        return None
    if (accept.get("actor") != account or meta.get("account_id") != account
            or meta.get("subject_id") != account or meta.get("tenant_id") != tenant
            or meta.get("roles") != ["platform_admin"] or meta.get("scope") != fixed_scope
            or meta.get("status") != "active" or meta.get("must_change") is not False
            or preset.get("tenant_id") != tenant or preset.get("preset_roles") != ["platform_admin"]
            or preset.get("preset_scope") != fixed_scope):
        return None
    for event in (issue, accept):
        if (event.get("outcome") != "success"
                or event.get("resource") != f"identity.invitation:{invitation}"
                or event.get("correlation_id") != f"identity-invitation-{invitation}"
                or canonical_uuid(event.get("event_id")) != event.get("event_id")
                or event.get("event_id") is None):
            return None
    if issue["event_id"] == accept["event_id"]:
        return None
    try:
        issued = datetime.fromisoformat(issue["timestamp"])
        accepted = datetime.fromisoformat(accept["timestamp"])
        expires = datetime.fromisoformat(preset["expires_at"])
        if (any(t.tzinfo is None for t in (issued, accepted, expires))
                or not issued <= accepted < expires or not timedelta(0) < expires - issued <= timedelta(hours=72)):
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return {"invitation_id": invitation, "issue_event_id": issue["event_id"],
            "accept_event_id": accept["event_id"], "issuer_account_id": actor}


