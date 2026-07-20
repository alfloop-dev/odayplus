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
    claims: Mapping[str, Any], *, subject: str, claim_prefix: str = "odp"
) -> Principal:
    """Build an authenticated :class:`Principal` from verified ``claims``."""

    scope = Scope(
        tenant_id=(_lookup(claims, "tenant_id", claim_prefix) or None),
        brand_ids=_as_str_set(_lookup(claims, "brand_ids", claim_prefix)),
        region_ids=_as_str_set(_lookup(claims, "region_ids", claim_prefix)),
        store_ids=_as_str_set(_lookup(claims, "store_ids", claim_prefix)),
        assigned_area_ids=_as_str_set(
            _lookup(claims, "assigned_area_ids", claim_prefix)
        ),
        heat_zone_ids=_as_str_set(_lookup(claims, "heat_zone_ids", claim_prefix)),
        modules=_as_str_set(_lookup(claims, "modules", claim_prefix)),
        clearance=_parse_clearance(_lookup(claims, "clearance", claim_prefix)),
    )
    return Principal(
        subject_id=subject,
        roles=_parse_roles(_lookup(claims, "roles", claim_prefix)),
        scope=scope,
        attributes={"iss": claims.get("iss"), "token_type": "oidc"},
        authenticated=True,
    )
