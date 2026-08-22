"""Domain contracts and constants for Market Intelligence BFF API (v2).

Contract: `odayplus.market-intelligence-api.v2`.
Task ID: `ODP-API-001`.
"""

from __future__ import annotations

from shared.auth import Role

CONTRACT_ID = "odayplus.market-intelligence-api.v2"
CONTRACT_VERSION = "2.0.0"
CONTRACT_CATEGORY = "bff_api"
SCHEMA_VERSION = "0.4.1"

REQUIRED_CONTRACTS = frozenset({
    "odayplus.market-data-facade.v2",
    "emgi.site-market-context.v1",
    "emgi.coverage-surface.v1",
    "emgi.data-acquisition-plan.v1",
})

PROVIDED_CONTRACTS = frozenset({
    CONTRACT_ID,
})

# Roles authorized to access the Market Intelligence BFF and its endpoints
ALLOWED_MARKET_INTELLIGENCE_ROLES = frozenset({
    Role.PLATFORM_ADMIN,
    Role.ARCHITECTURE_OWNER,
    Role.DATA_OWNER,
    Role.MODEL_OWNER,
    Role.RELEASE_OWNER,
    Role.EXPANSION_USER,
    Role.SITE_REVIEWER,
    Role.OPERATIONS_MANAGER,
    Role.REGIONAL_SUPERVISOR,
    Role.PRICING_MANAGER,
    Role.MARKETING_MANAGER,
    Role.FINANCE_LEGAL,
    Role.COMPLIANCE_OFFICER,
    Role.RECORDS_MANAGER,
    Role.RETENTION_MANAGER,
    Role.EXECUTIVE,
    Role.FRANCHISEE,
    Role.AUDITOR,
})

__all__ = [
    "ALLOWED_MARKET_INTELLIGENCE_ROLES",
    "CONTRACT_CATEGORY",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "PROVIDED_CONTRACTS",
    "REQUIRED_CONTRACTS",
    "SCHEMA_VERSION",
]
