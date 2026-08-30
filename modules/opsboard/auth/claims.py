"""Map verified OIDC claims onto a canonical :class:`shared.auth.Principal`.

The boundary calls this only *after* signature + issuer/audience/expiry
validation, so the claims are trusted here. Unknown role strings are dropped
(never trusted), mirroring ``principal_from_headers``' conservative parsing.

Recognised claims (namespaced under a configurable prefix, default
``odp``, plus bare fallbacks):

- ``sub``            -> subject id (required by the caller)
- ``roles``          -> list[str] of canonical role ids
- ``tenant_id``      -> home tenant for isolation
- ``brand_ids`` / ``region_ids`` / ``store_ids`` / ``assigned_area_ids`` /
  ``heat_zone_ids`` / ``modules`` -> scope axes
- ``clearance``      -> data-classification name (default CONFIDENTIAL)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shared.auth import DataClassification, Principal, Role, Scope


def _as_str_set(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(item).strip() for item in value if str(item).strip())
    return frozenset()


def _parse_roles(value: Any) -> frozenset[Role]:
    roles: set[Role] = set()
    for raw in _as_str_set(value):
        try:
            roles.add(Role(raw))
        except ValueError:
            continue  # unknown role id is ignored, not trusted
    return frozenset(roles)


def _parse_clearance(value: Any) -> DataClassification:
    if value is None:
        return DataClassification.CONFIDENTIAL
    try:
        return DataClassification[str(value).strip().upper()]
    except KeyError:
        return DataClassification.CONFIDENTIAL


def _lookup(claims: Mapping[str, Any], key: str, prefix: str) -> Any:
    """Prefer a namespaced claim (``{prefix}/{key}``) then a bare ``key``."""

    namespaced = f"{prefix}/{key}"
    if namespaced in claims:
        return claims[namespaced]
    return claims.get(key)


def principal_from_claims(
    claims: Mapping[str, Any],
    *,
    subject: str,
    claim_prefix: str = "odp",
    principal_mapping: Mapping[str, Any] | None = None,
) -> Principal:
    """Build an authenticated :class:`Principal` from verified ``claims``."""

    mapping = principal_mapping or {}
    mapping_is_authoritative = principal_mapping is not None

    def value(key: str) -> Any:
        if mapping_is_authoritative:
            if key in mapping:
                return mapping[key]
            scope_dict = mapping.get("scope")
            if isinstance(scope_dict, dict) and key in scope_dict:
                return scope_dict[key]
            return None
        return _lookup(claims, key, claim_prefix)


    scope = Scope(
        tenant_id=(value("tenant_id") or None),
        brand_ids=_as_str_set(value("brand_ids")),
        region_ids=_as_str_set(value("region_ids")),
        store_ids=_as_str_set(value("store_ids")),
        assigned_area_ids=_as_str_set(value("assigned_area_ids")),
        heat_zone_ids=_as_str_set(value("heat_zone_ids")),
        modules=_as_str_set(value("modules")),
        clearance=_parse_clearance(value("clearance")),
    )
    return Principal(
        subject_id=subject,
        roles=_parse_roles(value("roles")),
        scope=scope,
        attributes={
            "iss": claims.get("iss"),
            "email": claims.get("email"),
            "token_type": "oidc",
        },
        authenticated=True,
    )
