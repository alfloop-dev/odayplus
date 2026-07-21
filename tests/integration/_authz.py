"""Shared auth-header helpers for domain API integration tests.

The FastAPI domain routers enforce server-side RBAC (ODP-GAP-API-001): a route
is guarded at the type level by ``require_permission`` and denies anonymous or
under-privileged callers with HTTP 403. Integration tests therefore drive the
endpoints as an authenticated principal carrying the role(s) that the RBAC
matrix (``shared.auth.rbac``) grants for that domain.

``principal_from_headers`` (see ``apps/api/oday_api/security/dependencies.py``)
reads ``x-subject-id`` and a comma-separated ``x-roles`` header, so a test only
needs to supply those to exercise the authorized path.
"""

from __future__ import annotations

from shared.auth import Role


def auth_headers(*roles: Role, subject: str = "test-operator") -> dict[str, str]:
    """Headers for an authenticated principal holding ``roles``."""

    return {
        "x-subject-id": subject,
        "x-roles": ",".join(role.value for role in roles),
    }


# Least-privilege role bundles per domain, derived from ROLE_PERMISSIONS.
ADLIFT_HEADERS = auth_headers(Role.MARKETING_MANAGER)
AUDIT_HEADERS = auth_headers(Role.AUDITOR)
AUDIT_LEGAL_HEADERS = auth_headers(Role.FINANCE_LEGAL, subject="legal-a")
AUDIT_RECORDS_HEADERS = auth_headers(Role.RECORDS_MANAGER, subject="records-a")
AVM_HEADERS = auth_headers(Role.FINANCE_LEGAL)
FORECASTOPS_HEADERS = auth_headers(Role.OPERATIONS_MANAGER)
# Expansion domain: EXPANSION_USER holds heatzone view/create and listing
# view/create/update (ROLE_PERMISSIONS in shared.auth.rbac).
HEATZONE_HEADERS = auth_headers(Role.EXPANSION_USER)
LISTING_HEADERS = auth_headers(Role.EXPANSION_USER)
# External-data freshness is an integration-domain read; DATA_OWNER holds the
# integration view grant.
EXTERNAL_DATA_HEADERS = auth_headers(Role.DATA_OWNER)
# Intervention lifecycle spans create/approve (operations manager) and execute
# (regional supervisor); a principal may hold both roles.
INTERVENTION_HEADERS = auth_headers(Role.OPERATIONS_MANAGER, Role.REGIONAL_SUPERVISOR)
# PriceOps view/create/approve/execute is held by the pricing manager.
PRICEOPS_HEADERS = auth_headers(Role.PRICING_MANAGER)
SITESCORE_HEADERS = auth_headers(Role.SITE_REVIEWER)
