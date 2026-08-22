"""SiteScore v3 domain models."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ScoreAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE_MISSING_INPUT = "UNAVAILABLE_MISSING_INPUT"

class DecisionReadiness(StrEnum):
    READY = "READY"
    INCOMPLETE_FEASIBILITY = "INCOMPLETE_FEASIBILITY"
    INCOMPLETE_ECONOMICS = "INCOMPLETE_ECONOMICS"

class SiteScoreDecision(StrEnum):
    GO = "GO"
    NO_GO = "NO_GO"
    INCOMPLETE = "INCOMPLETE"

@dataclass(frozen=True, slots=True)
class SiteScoreComponents:
    demand_score: float
    format_score: float
    ramp_score: float
    cannibalization_score: float
    economics_score: float
    policy_score: float

@dataclass(frozen=True, slots=True)
class SiteScoreAssessment:
    availability: ScoreAvailability
    readiness: DecisionReadiness
    decision: SiteScoreDecision
    components: SiteScoreComponents | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability.value,
            "readiness": self.readiness.value,
            "decision": self.decision.value,
            "components": {
                "demand_score": self.components.demand_score,
                "format_score": self.components.format_score,
                "ramp_score": self.components.ramp_score,
                "cannibalization_score": self.components.cannibalization_score,
                "economics_score": self.components.economics_score,
                "policy_score": self.components.policy_score,
            } if self.components else None,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SiteScoreAssessment":
        components = None
        if data.get("components"):
            components = SiteScoreComponents(
                demand_score=data["components"]["demand_score"],
                format_score=data["components"]["format_score"],
                ramp_score=data["components"]["ramp_score"],
                cannibalization_score=data["components"]["cannibalization_score"],
                economics_score=data["components"]["economics_score"],
                policy_score=data["components"]["policy_score"],
            )
        return cls(
            availability=ScoreAvailability(data["availability"]),
            readiness=DecisionReadiness(data["readiness"]),
            decision=SiteScoreDecision(data["decision"]),
            components=components,
            reasons=tuple(data.get("reasons", [])),
        )
