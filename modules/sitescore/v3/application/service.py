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


def _non_empty_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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


def _manifest_ids(value: Any) -> set[str]:
    """Return manifest references from wire and released product shapes."""

    mapping = _as_mapping(value)
    manifest_ids: set[str] = set()

    def collect(source: Any) -> None:
        source_mapping = _as_mapping(source)
        for name in (
            "manifest_id",
            "product_manifest_id",
            "feature_manifest_id",
            "source_manifest_id",
            "release_id",
        ):
            candidate = _non_empty_string(_field(source_mapping, name))
            if candidate is not None:
                manifest_ids.add(candidate)

        refs = _field(source_mapping, "component_manifest_refs")
        if isinstance(refs, (list, tuple)):
            for ref in refs:
                collect(ref)

        contexts = _field(source_mapping, "contexts")
        if isinstance(contexts, (list, tuple)):
            for context in contexts:
                collect(context)

        metadata = _field(source_mapping, "metadata")
        if metadata is not None:
            metadata_mapping = _as_mapping(metadata)
            for name in ("manifest_id", "product_manifest_id"):
                candidate = _non_empty_string(_field(metadata_mapping, name))
                if candidate is not None:
                    manifest_ids.add(candidate)

    collect(mapping)
    return manifest_ids


def _manifest_id(value: Any, *, from_metadata: bool = False) -> str | None:
    """Return a single legacy metadata manifest reference, when present."""

    source = _field(value, "metadata") if from_metadata else value
    return _non_empty_string(_field(source, "manifest_id"))


def _market_context_identity(value: Any, site_id: str) -> str | None:
    """Find the context id used by feasibility/economics producer outputs."""

    mapping = _as_mapping(value)
    direct = _non_empty_string(_field(mapping, "context_id"))
    if direct is not None:
        return direct

    contexts = _field(mapping, "contexts")
    if isinstance(contexts, (list, tuple)):
        for context in contexts:
            context_mapping = _as_mapping(context)
            identity = _as_mapping(_field(context_mapping, "identity"))
            if _field(identity, "site_id") == site_id:
                context_id = _non_empty_string(_field(context_mapping, "context_id"))
                if context_id is not None:
                    return context_id

    return _non_empty_string(_field(mapping, "document_id"))


def _market_context_hash(value: Any) -> str | None:
    mapping = _as_mapping(value)
    for name in ("sha256", "digest"):
        candidate = _non_empty_string(_field(mapping, name))
        if candidate is not None:
            return candidate
    metadata = _as_mapping(_field(mapping, "metadata"))
    for name in ("sha256", "digest"):
        candidate = _non_empty_string(_field(metadata, name))
        if candidate is not None:
            return candidate
    return None


def _source_market_context_reference(value: Any) -> tuple[str | None, str | None]:
    """Read provenance emitted by physical-feasibility/site-economics docs."""

    mapping = _as_mapping(value)
    metadata = _as_mapping(_field(mapping, "metadata"))
    source_id = _non_empty_string(_field(mapping, "source_market_context_id"))
    source_hash = _non_empty_string(_field(mapping, "source_market_context_sha256"))
    if source_id is None:
        source_id = _non_empty_string(_field(metadata, "source_market_context_id"))
    if source_hash is None:
        source_hash = _non_empty_string(_field(metadata, "source_market_context_sha256"))
    return source_id, source_hash


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
    if from_metadata:
        actual_manifest_ids = set()
        actual_manifest_id = _manifest_id(value, from_metadata=True)
        if actual_manifest_id is not None:
            actual_manifest_ids.add(actual_manifest_id)
    else:
        actual_manifest_ids = _manifest_ids(value)
    if not actual_manifest_ids:
        reasons.append(f"{label} manifest is missing.")
        return False
    if expected_manifest_id not in actual_manifest_ids:
        reasons.append(f"{label} manifest mismatch.")
        return False
    return True


def _check_document_provenance(
    label: str,
    value: Any,
    expected_manifest_id: str | None,
    market_context: Any,
    site_id: str,
    reasons: list[str],
) -> bool:
    """Validate either legacy manifest metadata or producer source references."""

    legacy_manifest_id = _manifest_id(value, from_metadata=True)
    if legacy_manifest_id is not None:
        return _check_manifest(
            label,
            value,
            expected_manifest_id,
            reasons,
            from_metadata=True,
        )

    source_id, source_hash = _source_market_context_reference(value)
    expected_context_id = _market_context_identity(market_context, site_id)
    if source_id is None:
        reasons.append(f"{label} manifest is missing.")
        return False
    if expected_context_id is None:
        reasons.append("Market context identity is missing.")
        return False
    if source_id != expected_context_id:
        reasons.append(f"{label} source market context mismatch.")
        return False

    expected_context_hash = _market_context_hash(market_context)
    if (
        source_hash is not None
        and expected_context_hash is not None
        and source_hash != expected_context_hash
    ):
        reasons.append(f"{label} source market context digest mismatch.")
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
        feasibility_provenance_ready = _check_document_provenance(
            "Feasibility",
            feasibility_doc,
            expected_manifest_id,
            market_context,
            site_id,
            reasons,
        )
        economics_provenance_ready = _check_document_provenance(
            "Economics",
            economics_doc,
            expected_manifest_id,
            market_context,
            site_id,
            reasons,
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
        elif feasibility_decision != FeasibilityDecision.FEASIBLE.value:
            readiness = _set_incomplete(readiness, DecisionReadiness.INCOMPLETE_FEASIBILITY)
            reasons.append(f"Feasibility decision is unrecognized: {feasibility_decision}.")

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
