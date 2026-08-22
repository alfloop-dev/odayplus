"""SiteScore v3 Module Public API.

Provides contract: odayplus.sitescore-v3.v1
Requires contract: emgi.site-market-context.v1
Requires contract: odayplus.physical-feasibility.v1
Requires contract: odayplus.site-economics.v1
"""

from modules.sitescore.v3.application.service import SiteScoreV3Service
from modules.sitescore.v3.domain.contracts import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    SiteScoreV3Document,
    validate_sitescore_v3_document,
)
from modules.sitescore.v3.domain.models import (
    DecisionReadiness,
    ScoreAvailability,
    SiteScoreAssessment,
    SiteScoreComponents,
    SiteScoreDecision,
)

__all__ = [
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "DecisionReadiness",
    "ScoreAvailability",
    "SiteScoreAssessment",
    "SiteScoreComponents",
    "SiteScoreDecision",
    "SiteScoreV3Document",
    "SiteScoreV3Service",
    "validate_sitescore_v3_document",
]
