"""Build a canonical :class:`shared.auth.Principal` for a verified token.

The boundary calls this only *after* signature + issuer/audience/expiry
validation. Even then, the token's claims are **not** authorization facts:
per contract §4.4 the caller must supply an authoritative
``principal_mapping`` (from ``ODP_AUTH_PRINCIPAL_MAP``), and roles/scope are
read from that mapping alone. The claims contribute only identity and
descriptive attributes (``sub``, ``iss``, ``email``).

This is deliberately not optional. An earlier version fell back to reading
``roles``/``tenant_id``/scope axes straight off the token whenever no mapping
was supplied, which let a validly signed token from the service issuer
self-assign ``platform_admin`` and an arbitrary tenant
(ODP-WEB-LOCAL-AUTH-API-TRUST-001).

Mapping keys (also accepted nested under a ``scope`` dict):

- ``roles``          -> list[str] of canonical role ids
- ``tenant_id``      -> home tenant for isolation
- ``brand_ids`` / ``region_ids`` / ``store_ids`` / ``assigned_area_ids`` /
  ``heat_zone_ids`` / ``modules`` -> scope axes
- ``clearance``      -> data-classification name (default CONFIDENTIAL)

Unknown role strings are dropped (never trusted), mirroring
``principal_from_headers``' conservative parsing.
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


def principal_from_claims(
    claims: Mapping[str, Any],
    *,
    subject: str,
    principal_mapping: Mapping[str, Any],
) -> Principal:
    """Build an authenticated :class:`Principal` for a verified token.

    ``principal_mapping`` is required and is the sole source of roles and
    scope. Pass an empty mapping for a declared identity that is granted
    nothing; callers must reject undeclared subjects before getting here.
    """

    mapping = principal_mapping

    def value(key: str) -> Any:
        if key in mapping:
            return mapping[key]
        scope_dict = mapping.get("scope")
        if isinstance(scope_dict, dict) and key in scope_dict:
            return scope_dict[key]
        return None

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
    attributes = {
        "iss": claims.get("iss"),
        "email": claims.get("email"),
        "token_type": "oidc",
    }
    # These values are accepted only from the deployment-owned principal
    # mapping, never from token claims.  A confidential export still requires
    # the proof to verify against the external AVM authority key.
    for key in (
        "identity_proof_sha256",
        "verified_identity",
        "data_room_access",
        "event_id",
    ):
        if key in mapping:
            attributes[key] = mapping[key]
    return Principal(
        subject_id=subject,
        roles=_parse_roles(value("roles")),
        scope=scope,
        attributes=attributes,
        authenticated=True,
    )
