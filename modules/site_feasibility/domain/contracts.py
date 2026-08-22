"""Contracts for site feasibility."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.site_feasibility.domain.models import FeasibilityAssessment, FeasibilityDecision

CONTRACT_ID = "odayplus.physical-feasibility.v1"
CONTRACT_VERSION = "1.0.0"
CONTRACT_CATEGORY = "decision_product"

@dataclass(frozen=True, slots=True)
class SiteFeasibilityDocument:
    document_id: str
    site_id: str
    decision: FeasibilityAssessment
    contract_id: str = CONTRACT_ID
    contract_version: str = CONTRACT_VERSION
    evaluated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "site_id": self.site_id,
            "decision": self.decision.to_dict(),
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "evaluated_at": self.evaluated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SiteFeasibilityDocument":
        return cls(
            document_id=str(data.get("document_id", str(uuid4()))),
            site_id=str(data["site_id"]),
            decision=FeasibilityAssessment.from_dict(data["decision"]),
            contract_id=str(data.get("contract_id", CONTRACT_ID)),
            contract_version=str(data.get("contract_version", CONTRACT_VERSION)),
            evaluated_at=str(data.get("evaluated_at", datetime.now(UTC).isoformat())),
            metadata=dict(data.get("metadata", {})),
        )

def validate_site_feasibility_document(doc: SiteFeasibilityDocument | Mapping[str, Any]) -> None:
    """Validate the wire shape of the physical-feasibility product contract."""

    data = doc.to_dict() if isinstance(doc, SiteFeasibilityDocument) else doc
    if not isinstance(data, Mapping):
        raise ValueError("physical-feasibility document must be a mapping")
    if data.get("contract_id") != CONTRACT_ID:
        raise ValueError(f"Invalid contract_id: expected '{CONTRACT_ID}', got '{data.get('contract_id')}'")
    if data.get("contract_version") != CONTRACT_VERSION:
        raise ValueError(
            f"Invalid contract_version: expected '{CONTRACT_VERSION}', got '{data.get('contract_version')}'"
        )
    if not isinstance(data.get("document_id"), str) or not data["document_id"].strip():
        raise ValueError("document_id is required")
    if not isinstance(data.get("site_id"), str) or not data["site_id"].strip():
        raise ValueError("site_id is required")
    if not isinstance(data.get("evaluated_at"), str) or not data["evaluated_at"].strip():
        raise ValueError("evaluated_at is required")
    decision = data.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("decision is required")
    recommendation = decision.get("recommendation")
    allowed_decisions = {decision.value for decision in FeasibilityDecision}
    if recommendation not in allowed_decisions:
        raise ValueError(f"Invalid feasibility recommendation: {recommendation!r}")
    reasons = decision.get("reasons", [])
    if not isinstance(reasons, (list, tuple)) or not all(isinstance(reason, str) for reason in reasons):
        raise ValueError("decision.reasons must be an array of strings")
    if not isinstance(data.get("metadata", {}), Mapping):
        raise ValueError("metadata must be an object")
