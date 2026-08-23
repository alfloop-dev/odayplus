"""Service for SiteScore v3."""

import math
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from modules.site_economics.domain.models import EconomicsDecision
from modules.site_feasibility.domain.models import FeasibilityDecision
from modules.sitescore.v3.domain.contracts import SiteScoreV3Document
from modules.sitescore.v3.domain.models import (
    DecisionReadiness,
    ScoreAvailability,
    SiteScoreAssessment,
    SiteScoreComponents,
    SiteScoreDecision,
)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Read either a wire mapping or one of the domain contract documents."""

    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        rendered = to_dict()
        if isinstance(rendered, Mapping):
            return rendered
    return {}


def _field(value: Any, name: str) -> Any:
    mapping = _as_mapping(value)
    if name in mapping:
        return mapping[name]
    if value is None:
        return None
    return getattr(value, name, None)


def _recommendation(value: Any) -> str | None:
    if isinstance(value, (FeasibilityDecision, EconomicsDecision)):
        return value.value
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _nested_recommendation(value: Any, *paths: tuple[str, ...]) -> str | None:
    for path in paths:
        current = value
        for name in path:
            current = _field(current, name)
            if current is None:
                break
        else:
            recommendation = _recommendation(current)
            if recommendation is not None:
                return recommendation
    return None


def _manifest_id(value: Any, *, from_metadata: bool = False) -> str | None:
    source = value
    if from_metadata:
        source = _field(value, "metadata")
    candidate = _field(source, "manifest_id")
    if isinstance(candidate, str) and candidate.strip():
        return candidate
    return None


def _check_manifest(
    label: str,
    value: Any,
    expected_manifest_id: str | None,
    reasons: list[str],
    *,
    from_metadata: bool = False,
) -> bool:
    """Require a present, exact point-in-time manifest reference."""

    if expected_manifest_id is None:
        return False
    actual_manifest_id = _manifest_id(value, from_metadata=from_metadata)
    if actual_manifest_id is None:
        reasons.append(f"{label} manifest is missing.")
        return False
    if actual_manifest_id != expected_manifest_id:
        reasons.append(f"{label} manifest mismatch.")
        return False
    return True


def _set_incomplete(
    current: DecisionReadiness,
    incomplete: DecisionReadiness,
) -> DecisionReadiness:
    if current == DecisionReadiness.READY:
        return incomplete
    return current


class SiteScoreV3Service:
    def evaluate(
        self,
        site_id: str,
        manifest_id: str,
        market_context: Mapping[str, Any] | Any,
        feasibility_doc: Any,
        economics_doc: Any,
    ) -> SiteScoreV3Document:
        reasons: list[str] = []
        readiness = DecisionReadiness.READY
        expected_manifest_id = (
            manifest_id if isinstance(manifest_id, str) and manifest_id.strip() else None
        )
        if expected_manifest_id is None:
            reasons.append("Evaluation manifest is missing.")

        market_context_values = _as_mapping(market_context)
        market_context_ready = _check_manifest(
            "Market context", market_context, expected_manifest_id, reasons
        )
        feasibility_provenance_ready = _check_manifest(
            "Feasibility", feasibility_doc, expected_manifest_id, reasons, from_metadata=True
        )
        economics_provenance_ready = _check_manifest(
            "Economics", economics_doc, expected_manifest_id, reasons, from_metadata=True
        )

        feasibility_decision = _nested_recommendation(
            feasibility_doc, ("decision", "recommendation")
        )
        economics_decision = _nested_recommendation(
            economics_doc,
            ("decision", "recommendation"),
            ("summary", "assessment", "recommendation"),
        )

        if not market_context_ready:
            readiness = _set_incomplete(readiness, DecisionReadiness.INCOMPLETE_FEASIBILITY)
        if not feasibility_provenance_ready:
            readiness = _set_incomplete(readiness, DecisionReadiness.INCOMPLETE_FEASIBILITY)
        if not economics_provenance_ready:
            readiness = _set_incomplete(readiness, DecisionReadiness.INCOMPLETE_ECONOMICS)

        if feasibility_decision is None:
            readiness = _set_incomplete(readiness, DecisionReadiness.INCOMPLETE_FEASIBILITY)
            reasons.append("Feasibility decision is missing.")
        elif feasibility_decision == FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY.value:
            readiness = _set_incomplete(readiness, DecisionReadiness.INCOMPLETE_FEASIBILITY)
            reasons.append("Feasibility is unknown, requires survey.")
        elif feasibility_decision == FeasibilityDecision.CONDITIONAL.value:
            readiness = _set_incomplete(readiness, DecisionReadiness.INCOMPLETE_FEASIBILITY)
            reasons.append("Feasibility is conditional and requires resolution.")
        elif feasibility_decision == FeasibilityDecision.INFEASIBLE.value:
            reasons.append("Site is physically infeasible.")

        if economics_decision is None or economics_decision in {"UNKNOWN", "MISSING"}:
            readiness = _set_incomplete(readiness, DecisionReadiness.INCOMPLETE_ECONOMICS)
            reasons.append("Economics decision is missing.")
        elif economics_decision == EconomicsDecision.CONDITIONAL_GO.value:
            readiness = _set_incomplete(readiness, DecisionReadiness.INCOMPLETE_ECONOMICS)
            reasons.append("Economics is conditional and requires resolution.")
        elif economics_decision == EconomicsDecision.INVESTIGATE.value:
            readiness = _set_incomplete(readiness, DecisionReadiness.INCOMPLETE_ECONOMICS)
            reasons.append("Economics requires investigation.")
        elif economics_decision == EconomicsDecision.REJECT.value:
            reasons.append("Economics rejected the site.")
        elif economics_decision != EconomicsDecision.GO.value:
            readiness = _set_incomplete(readiness, DecisionReadiness.INCOMPLETE_ECONOMICS)
            reasons.append(f"Economics decision is not a GO: {economics_decision}.")

        if readiness != DecisionReadiness.READY:
            decision = SiteScoreDecision.INCOMPLETE
            availability = ScoreAvailability.UNAVAILABLE_MISSING_INPUT
            components = None
        else:
            fields = [
                "demand_score",
                "format_score",
                "ramp_score",
                "cannibalization_score",
                "economics_score",
                "policy_score",
            ]
            scores = {}
            missing_fields = []
            for f in fields:
                val = market_context_values.get(f)
                if val is None:
                    missing_fields.append(f)
                else:
                    try:
                        f_val = float(val)
                        
                        if not math.isfinite(f_val):
                            missing_fields.append(f)
                        else:
                            scores[f] = f_val
                    except (ValueError, TypeError):
                        missing_fields.append(f)

            if missing_fields:
                availability = ScoreAvailability.UNAVAILABLE_MISSING_INPUT
                components = None
                reasons.append(f"Missing or invalid component scores: {', '.join(missing_fields)}.")
            else:
                availability = ScoreAvailability.AVAILABLE
                components = SiteScoreComponents(
                    demand_score=scores["demand_score"],
                    format_score=scores["format_score"],
                    ramp_score=scores["ramp_score"],
                    cannibalization_score=scores["cannibalization_score"],
                    economics_score=scores["economics_score"],
                    policy_score=scores["policy_score"],
                )

            if (
                feasibility_decision == FeasibilityDecision.INFEASIBLE.value
                or economics_decision == EconomicsDecision.REJECT.value
            ):
                decision = SiteScoreDecision.NO_GO
            elif missing_fields:
                decision = SiteScoreDecision.INCOMPLETE
            else:
                decision = SiteScoreDecision.GO

        assessment = SiteScoreAssessment(
            availability=availability,
            readiness=readiness,
            decision=decision,
            components=components,
            reasons=tuple(reasons),
        )

        return SiteScoreV3Document(
            document_id=str(uuid4()),
            site_id=site_id,
            manifest_id=manifest_id,
            assessment=assessment,
        )
