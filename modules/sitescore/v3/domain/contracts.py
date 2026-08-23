"""Contracts for SiteScore v3."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from modules.sitescore.v3.domain.models import SiteScoreAssessment

CONTRACT_ID = "odayplus.sitescore-v3.v1"
CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class SiteScoreV3Document:
    document_id: str
    site_id: str
    assessment: SiteScoreAssessment
    manifest_id: str
    contract_id: str = CONTRACT_ID
    contract_version: str = CONTRACT_VERSION
    evaluated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "site_id": self.site_id,
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "manifest_id": self.manifest_id,
            "evaluated_at": self.evaluated_at,
            "assessment": self.assessment.to_dict(),
        }


def validate_sitescore_v3_document(doc: Any) -> bool:
    return isinstance(doc, SiteScoreV3Document) and doc.contract_id == CONTRACT_ID
