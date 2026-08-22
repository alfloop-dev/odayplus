"""Site Feasibility domain models."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FeasibilityDecision(StrEnum):
    FEASIBLE = "FEASIBLE"
    CONDITIONAL = "CONDITIONAL"
    UNKNOWN_REQUIRES_SURVEY = "UNKNOWN_REQUIRES_SURVEY"
    INFEASIBLE = "INFEASIBLE"

@dataclass(frozen=True, slots=True)
class FeasibilityAssessment:
    recommendation: FeasibilityDecision
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation": self.recommendation.value,
            "reasons": self.reasons,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FeasibilityAssessment":
        return cls(
            recommendation=FeasibilityDecision(data["recommendation"]),
            reasons=list(data.get("reasons", [])),
        )
