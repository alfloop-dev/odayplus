"""Market Intelligence BFF and Product Authorization Module.

Contract: `odayplus.market-intelligence-api.v2`.
Task ID: `ODP-API-001`.

Provides unified, authorized BFF endpoints for:
- Market Cells (emgi.market-cell-profile.v1)
- Site Market Context (emgi.site-market-context.v1)
- Candidate Compare
- Evidence & Lineage
- Coverage Surface & Data Gaps (emgi.coverage-surface.v1)
- Data Acquisition Plans (emgi.data-acquisition-plan.v1)
"""

from modules.market_intelligence_api.application import (
    MarketIntelligenceAuthorizationError,
    MarketIntelligenceError,
    MarketIntelligenceNotFoundError,
    MarketIntelligenceService,
    MarketIntelligenceValidationError,
    authorize_market_intelligence,
)
from modules.market_intelligence_api.domain import (
    ALLOWED_MARKET_INTELLIGENCE_ROLES,
    CONTRACT_CATEGORY,
    CONTRACT_ID,
    CONTRACT_VERSION,
    PROVIDED_CONTRACTS,
    REQUIRED_CONTRACTS,
    SCHEMA_VERSION,
    AcquisitionPlanFilter,
    CandidateCellSummary,
    CandidateCompareRequest,
    CandidateCompareResult,
    CandidateSiteSummary,
    CellEvidenceChain,
    CompareScope,
    CoverageFilter,
    DataGapFilter,
    DomainComparisonDelta,
    DomainEvidence,
    DomainReadiness,
    SiteEvidenceChain,
)
from modules.market_intelligence_api.infrastructure import (
    DataPlatformMarketIntelligenceRepository,
    MarketIntelligenceRepository,
)

__all__ = [
    "ALLOWED_MARKET_INTELLIGENCE_ROLES",
    "AcquisitionPlanFilter",
    "CONTRACT_CATEGORY",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "CandidateCellSummary",
    "CandidateCompareRequest",
    "CandidateCompareResult",
    "CandidateSiteSummary",
    "CellEvidenceChain",
    "CompareScope",
    "CoverageFilter",
    "DataGapFilter",
    "DataPlatformMarketIntelligenceRepository",
    "DomainComparisonDelta",
    "DomainEvidence",
    "DomainReadiness",
    "MarketIntelligenceAuthorizationError",
    "MarketIntelligenceError",
    "MarketIntelligenceNotFoundError",
    "MarketIntelligenceRepository",
    "MarketIntelligenceService",
    "MarketIntelligenceValidationError",
    "PROVIDED_CONTRACTS",
    "REQUIRED_CONTRACTS",
    "SCHEMA_VERSION",
    "SiteEvidenceChain",
    "authorize_market_intelligence",
]
