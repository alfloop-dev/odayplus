"""Site Feasibility Module Public API.

Provides contract: odayplus.physical-feasibility.v1
Requires contract: emgi.site-market-context.v1
Requires contract: odayplus.survey-workflow.v2
"""

from modules.site_feasibility.application.service import SiteFeasibilityService
from modules.site_feasibility.domain.contracts import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    SiteFeasibilityDocument,
    validate_site_feasibility_document,
)
from modules.site_feasibility.domain.models import (
    FeasibilityAssessment,
    FeasibilityDecision,
)

__all__ = [
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "FeasibilityAssessment",
    "FeasibilityDecision",
    "SiteFeasibilityDocument",
    "SiteFeasibilityService",
    "validate_site_feasibility_document",
]
