"""Contracts for SiteScore v3."""

import math
from collections.abc import Mapping
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
    # Optional on retained documents; new NetPlan admission requires an exact
    # selected-context hash from the producer, not a caller-declared digest.
    source_market_context_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "document_id": self.document_id,
            "site_id": self.site_id,
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "manifest_id": self.manifest_id,
            "evaluated_at": self.evaluated_at,
            "assessment": self.assessment.to_dict(),
        }
        context_hash = getattr(self, "source_market_context_sha256", None)
        if context_hash is not None:
            payload["source_market_context_sha256"] = context_hash
        return payload


def validate_sitescore_v3_payload(data: Any) -> bool:
    """Validate state consistency, without granting model or source authority."""
    if not isinstance(data, Mapping):
        return False
    if data.get("contract_id") != CONTRACT_ID or data.get("contract_version") != CONTRACT_VERSION:
        return False
    for name in ("document_id", "site_id", "manifest_id", "evaluated_at"):
        value = data.get(name)
        if type(value) is not str or not value or value != value.strip():
            return False
    try:
        instant = datetime.fromisoformat(data["evaluated_at"].replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() is None:
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    if "source_market_context_sha256" in data:
        digest = data["source_market_context_sha256"]
        if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            return False
    assessment = data.get("assessment")
    if not isinstance(assessment, Mapping):
        return False
    availability, readiness, decision = (assessment.get(key) for key in (
        "availability", "readiness", "decision",
    ))
    if any(type(value) is not str for value in (availability, readiness, decision)):
        return False
    if (availability not in {"AVAILABLE", "UNAVAILABLE_MISSING_INPUT"}
            or readiness not in {"READY", "INCOMPLETE_FEASIBILITY", "INCOMPLETE_ECONOMICS"}
            or decision not in {"GO", "NO_GO", "INCOMPLETE"}):
        return False
    reasons = assessment.get("reasons")
    if not isinstance(reasons, (list, tuple)) or any(type(reason) is not str for reason in reasons):
        return False
    components = assessment.get("components")
    if availability == "AVAILABLE":
        if not isinstance(components, Mapping):
            return False
        for name in ("demand_score", "format_score", "ramp_score", "cannibalization_score",
                     "economics_score", "policy_score"):
            number = components.get(name)
            try:
                if type(number) not in (int, float) or not math.isfinite(number):
                    return False
            except OverflowError:
                return False
    elif components is not None:
        return False
    if decision in {"GO", "NO_GO"} and readiness != "READY":
        return False
    if decision == "GO" and availability != "AVAILABLE":
        return False
    if decision == "INCOMPLETE" and readiness == "READY" and availability == "AVAILABLE":
        return False
    return True


def validate_sitescore_v3_document(doc: Any) -> bool:
    if type(doc) is not SiteScoreV3Document:
        return False
    try:
        return validate_sitescore_v3_payload(doc.to_dict())
    except (AttributeError, TypeError, ValueError, OverflowError, RecursionError):
        return False
