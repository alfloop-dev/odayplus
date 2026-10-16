"""Persisted identity-admin authority AFTER tenant lock, held to transaction end.

Caller owns the same-engine transaction and tenant advisory lock. One validator
serves role/status, invitation and recovery mutations: no pre-lock role, scope or
federation snapshot is write authority. Missing scope retains the identity store's
established unrestricted/CONFIDENTIAL default, NOT a malformed-value waiver.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from shared.auth import DataClassification, Principal, Role


class AdminAuthorityRefused(Exception):
    """Static refusal, never principal/SQL/provider details."""


def decode_identity_grant(value: Any, actual_type: Any, expected_type: str) -> Any:
    """Decode normalized JSON only with authoritative SQL type provenance."""
    if actual_type != expected_type or expected_type not in {"array", "object"}:
        raise ValueError("invalid persisted identity grant type")
    decoded = json.loads(value) if isinstance(value, str) else value
    if expected_type == "array":
        if not isinstance(decoded, list) or any(not isinstance(v, str) or not v.strip() for v in decoded):
            raise ValueError("invalid persisted identity grant array")
    elif not isinstance(decoded, dict):
        raise ValueError("invalid persisted identity grant object")
    return decoded


def checked_admin_authority(
    engine: Any, principal: Principal | None, *, tenant: str
) -> tuple[str, str]:
    """Return authenticated actor/deadline, holding current account/role/SID/link/scope."""
    if (
        not engine.in_transaction
        or not isinstance(principal, Principal)
        or not principal.authenticated
        or Role.PLATFORM_ADMIN not in principal.roles
        or principal.tenant_id != tenant
    ):
        raise AdminAuthorityRefused()
    try:
        actor = str(UUID(str(principal.subject_id)))
        sid = str(UUID(str(principal.attributes.get("sid"))))
    except (TypeError, ValueError, AttributeError):
        raise AdminAuthorityRefused() from None
    provider = principal.attributes.get("provider")
    if provider not in {"local_password", "oidc"}:
        raise AdminAuthorityRefused()
    row = engine.query_one(
        "SELECT LEAST(s.idle_expires_at, s.absolute_expires_at) AS deadline FROM identity.accounts a "
        "JOIN identity.account_roles r ON r.account_id=a.account_id "
        "JOIN identity.sessions s ON s.account_id=a.account_id "
        "WHERE a.account_id=? AND a.tenant_id=? AND a.status='active' AND r.role='platform_admin' "
        "AND s.session_id=? AND s.provider=? AND s.revoked_at IS NULL "
        "AND s.idle_expires_at>clock_timestamp() AND s.absolute_expires_at>clock_timestamp() "
        "FOR SHARE OF a,r,s",
        (actor, tenant, sid, provider),
    )
    if row is None:
        raise AdminAuthorityRefused()
    password = engine.query_one(
        "SELECT must_change FROM identity.password_credentials WHERE account_id=? FOR SHARE",
        (actor,),
    )
    if (password is None and provider == "local_password") or (
        password is not None and password["must_change"]
    ):
        raise AdminAuthorityRefused()
    if provider == "oidc":
        issuer, subject = principal.attributes.get("iss"), principal.attributes.get("sub")
        if (
            not isinstance(issuer, str)
            or not issuer
            or not isinstance(subject, str)
            or not subject
            or engine.query_one(
                "SELECT account_id FROM identity.federated_identities WHERE account_id=? AND issuer=? AND subject=? FOR SHARE",
                (actor, issuer, subject),
            )
            is None
        ):
            raise AdminAuthorityRefused()
    scope = engine.query_one(
        "SELECT brand_ids,region_ids,store_ids,assigned_area_ids,heat_zone_ids,modules,clearance, "
        "jsonb_typeof(brand_ids) AS brand_ids_json_type, "
        "jsonb_typeof(region_ids) AS region_ids_json_type, "
        "jsonb_typeof(store_ids) AS store_ids_json_type, "
        "jsonb_typeof(assigned_area_ids) AS assigned_area_ids_json_type, "
        "jsonb_typeof(heat_zone_ids) AS heat_zone_ids_json_type, "
        "jsonb_typeof(modules) AS modules_json_type "
        "FROM identity.account_scopes WHERE account_id=? FOR SHARE",
        (actor,),
    )
    if scope is not None:
        try:
            axes = {axis: decode_identity_grant(scope[axis], scope[f"{axis}_json_type"], "array")
                    for axis in ("brand_ids", "region_ids", "store_ids", "assigned_area_ids", "heat_zone_ids", "modules")}
            modules = axes["modules"]
            if (
                (modules and "operator_console" not in modules)
                or DataClassification[scope["clearance"]] < DataClassification.CONFIDENTIAL
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise AdminAuthorityRefused() from None
    # Federation/scope row waits may cross natural expiry even though held SID
    # cannot be concurrently revoked. Callers still fence their final write/audit.
    if (
        engine.query_one(
            "SELECT 1 AS active WHERE clock_timestamp()<CAST(? AS timestamptz)", (row["deadline"],)
        )
        is None
    ):
        raise AdminAuthorityRefused()
    return actor, row["deadline"]
