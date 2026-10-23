"""Service for SiteScore v3."""

import json
import math
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from modules.site_economics.application.context_inputs import (
    SiteEconomicsInputError,
    content_sha256,
    select_market_context,
)
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


def _selected_context(value: Any, site_id: str, tenant_id: str | None,
                      reasons: list[str]) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Select one released child; retain the legacy flat component interface.

    A flat manifest-only component input remains a pure application input. This
    compatibility path is not a released context or production admission.
    """
    try:
        root = dict(_as_mapping(value))
        content_sha256(root)
        root = json.loads(json.dumps(root, allow_nan=False))
        if "contexts" in root or "identity" in root:
            snapshot = select_market_context(root, site_id=site_id, tenant_id=tenant_id)
            context = snapshot.context
        else:
            context = root
            if ("site_id" in context and context["site_id"] != site_id) or (
                "tenant_id" in context and context["tenant_id"] != tenant_id
            ):
                raise SiteEconomicsInputError("SITESCORE_CONTEXT_SCOPE_MISMATCH", "market_context")
        # The root may share manifest refs; a sibling child never does.
        scope = {"component_manifest_refs": [
            {key: value for key, value in root.items() if key != "contexts"}, context,
        ]}
        return context, scope, True
    except (SiteEconomicsInputError, TypeError, ValueError, OverflowError, RecursionError):
        reasons.append("Market context selection or finite JSON content is invalid.")
        return {}, {}, False


def _market_context_identity(value: Any, site_id: str) -> str | None:
    mapping = _as_mapping(value)
    return (_non_empty_string(mapping.get("context_id"))
            or _non_empty_string(mapping.get("document_id")))


def _market_context_hash(value: Any) -> str | None:
    try:
        return content_sha256(_as_mapping(value))
    except (SiteEconomicsInputError, TypeError, ValueError, OverflowError, RecursionError):
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
    tenant_id: str | None = None,
) -> bool:
    """Check every declared reference; legacy metadata cannot waive a conflict."""
    mapping = _as_mapping(value)
    metadata = _as_mapping(_field(value, "metadata"))
    legacy = "manifest_id" in metadata
    if legacy and not _check_manifest(
        label, value, expected_manifest_id, reasons, from_metadata=True,
    ):
        return False
    if "site_id" in mapping and mapping["site_id"] != site_id:
        reasons.append(f"{label} belongs to another site.")
        return False
    if tenant_id is not None and "tenant_id" in mapping and mapping["tenant_id"] != tenant_id:
        reasons.append(f"{label} belongs to another tenant.")
        return False
    expected = {
        "source_market_context_id": _market_context_identity(market_context, site_id),
        "source_market_context_sha256": _market_context_hash(market_context),
    }
    for field, wanted in expected.items():
        declared = [source[field] for source in (mapping, metadata) if field in source]
        if not declared:
            if legacy and expected["source_market_context_id"] is None:
                continue  # Original flat component inputs cannot enter NetPlan.
            reasons.append(f"{label} manifest is missing." if field.endswith("_id")
                           else f"{label} source market context digest is missing.")
            return False
        if wanted is None or any(type(item) is not str or item != wanted for item in declared):
            suffix = "digest mismatch" if field.endswith("sha256") else "mismatch"
            reasons.append(f"{label} source market context {suffix}.")
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
        *,
        tenant_id: str | None = None,
    ) -> SiteScoreV3Document:
        reasons: list[str] = []
        readiness = DecisionReadiness.READY
        expected_manifest_id = (
            manifest_id if isinstance(manifest_id, str) and manifest_id.strip() else None
        )
        if expected_manifest_id is None:
            reasons.append("Evaluation manifest is missing.")

        market_context_values, market_scope, selected = _selected_context(
            market_context, site_id, tenant_id, reasons,
        )
        market_context_ready = _check_manifest(
            "Market context", market_scope, expected_manifest_id, reasons,
        ) and selected
        feasibility_provenance_ready = _check_document_provenance(
            "Feasibility",
            feasibility_doc,
            expected_manifest_id,
            market_context_values,
            site_id,
            reasons,
            tenant_id,
        )
        economics_provenance_ready = _check_document_provenance(
            "Economics",
            economics_doc,
            expected_manifest_id,
            market_context_values,
            site_id,
            reasons,
            tenant_id,
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

        # Model values stay observable while physical/economic readiness is
        # incomplete. Invalid market provenance still withholds those values.
        fields = (
            "demand_score", "format_score", "ramp_score", "cannibalization_score",
            "economics_score", "policy_score",
        )
        scores: dict[str, float] = {}
        missing_fields = []
        for field in fields:
            value = market_context_values.get(field)
            try:
                valid = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                valid = False
            if valid:
                scores[field] = float(value)
            else:
                missing_fields.append(field)
        if not market_context_ready or missing_fields:
            availability = ScoreAvailability.UNAVAILABLE_MISSING_INPUT
            components = None
            if missing_fields:
                reasons.append(f"Missing or invalid component scores: {', '.join(missing_fields)}.")
        else:
            availability = ScoreAvailability.AVAILABLE
            components = SiteScoreComponents(**scores)

        if readiness != DecisionReadiness.READY:
            decision = SiteScoreDecision.INCOMPLETE
        elif (feasibility_decision == FeasibilityDecision.INFEASIBLE.value
              or economics_decision == EconomicsDecision.REJECT.value):
            decision = SiteScoreDecision.NO_GO
        elif availability != ScoreAvailability.AVAILABLE:
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
            source_market_context_sha256=_market_context_hash(market_context_values) if selected else None,
        )
