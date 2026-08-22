"""Application service for the physical site-feasibility decision gate."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from modules.site_feasibility.domain.contracts import (
    SiteFeasibilityDocument,
    validate_site_feasibility_document,
)
from modules.site_feasibility.domain.models import FeasibilityAssessment, FeasibilityDecision

_MISSING = object()

# These are intentionally conservative.  A zoning label outside this set is
# not treated as a commercial zone by implication; it requires a survey or
# regulatory review before a recommendation can be bound.
_ALLOWED_ZONING = frozenset(
    {
        "business",
        "commercial",
        "commercial_mixed_use",
        "commercial_residential",
        "industrial",
        "mixed_use",
        "office",
        "retail",
    }
)
_FORBIDDEN_ZONING = frozenset(
    {
        "agricultural",
        "conservation",
        "residential",
        "residential_only",
    }
)
_ZONING_DENIAL_MARKERS = (
    "no_commercial",
    "not_commercial",
    "strictly_no_commercial",
    "commercial_prohibited",
    "commercial_forbidden",
    "prohibited",
    "forbidden",
)
_EMPTY_RESTRICTION_VALUES = frozenset(
    {
        "",
        "allowed",
        "none",
        "no_restrictions",
        "no_restriction",
        "not_applicable",
        "permitted",
        "unrestricted",
    }
)
_HARD_RESTRICTION_MARKERS = (
    "forbidden",
    "illegal",
    "not_allowed",
    "no_access",
    "no_commercial",
    "no_entry",
    "prohibited",
)
_CONDITIONAL_RESTRICTION_MARKERS = (
    "approval",
    "condition",
    "permit",
    "restricted",
    "restriction",
)
_MINIMUM_POWER_AMPS = 50.0
_MINIMUM_WATER_PRESSURE_PSI = 20.0
_MINIMUM_FRONTAGE_METERS = 3.0


@dataclass(slots=True)
class _Signals:
    """Decision signals collected without relying on input ordering."""

    hard_reasons: set[str] = field(default_factory=set)
    conditional_reasons: set[str] = field(default_factory=set)
    unknown_reasons: set[str] = field(default_factory=set)

    def merge(self, other: _Signals) -> None:
        self.hard_reasons.update(other.hard_reasons)
        self.conditional_reasons.update(other.conditional_reasons)
        self.unknown_reasons.update(other.unknown_reasons)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return a safe mapping for wire objects and generated contract models."""

    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        rendered = to_dict()
        if isinstance(rendered, Mapping):
            return rendered
    return {}


def _normalise_token(value: Any) -> str:
    return "_".join(str(value).strip().lower().replace("-", " ").split())


def _tokens(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_normalise_token(item) for item in value if item is not None]
    return [_normalise_token(value)]


def _first_present(mapping: Mapping[str, Any], names: Sequence[str]) -> tuple[Any, bool]:
    for name in names:
        if name in mapping:
            return mapping[name], True
    return _MISSING, False


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = _normalise_token(value)
        if token in {"true", "yes", "y", "1", "available", "allowed"}:
            return True
        if token in {"false", "no", "n", "0", "unavailable", "prohibited"}:
            return False
    return None


def _context_for_site(market_context: Any, site_id: str) -> Mapping[str, Any]:
    """Unwrap a context item from either a context or context-document payload."""

    context = _as_mapping(market_context)
    contexts = context.get("contexts")
    if isinstance(contexts, Sequence) and not isinstance(contexts, (str, bytes)):
        candidates = [_as_mapping(item) for item in contexts]
        matching = [
            item
            for item in candidates
            if _as_mapping(item.get("identity")).get("site_id") == site_id
        ]
        if matching:
            return matching[0]
        # A context document is not interchangeable with its sole child.
        # Falling back to an unmatched child would let another site's zoning
        # decide this site's binding recommendation.
        return {}
    return context


def _context_metadata(context: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    identity = _as_mapping(context.get("identity"))
    identity_metadata = _as_mapping(identity.get("metadata"))
    context_metadata = _as_mapping(context.get("metadata"))
    return identity_metadata, context_metadata


def _evaluate_zoning(context: Mapping[str, Any], signals: _Signals) -> None:
    identity_metadata, context_metadata = _context_metadata(context)
    value, found = _first_present(
        identity_metadata,
        ("zoning", "zoning_code", "zone", "land_use_zone"),
    )
    if not found:
        value, found = _first_present(
            context_metadata,
            ("zoning", "zoning_code", "zone", "land_use_zone"),
        )
    if not found:
        value, found = _first_present(
            context,
            ("zoning", "zoning_code", "zone", "land_use_zone"),
        )
    if not found or value is None:
        signals.unknown_reasons.add("Zoning is missing from the site-market context")
        return

    zoning_tokens = _tokens(value)
    if not zoning_tokens:
        signals.unknown_reasons.add("Zoning is empty or not a scalar value")
        return

    for token in zoning_tokens:
        if any(marker in token for marker in _ZONING_DENIAL_MARKERS) or token in _FORBIDDEN_ZONING:
            signals.hard_reasons.add(f"Zoning does not permit commercial use: {token}")
        elif token not in _ALLOWED_ZONING:
            signals.unknown_reasons.add(f"Unrecognised zoning requires regulatory review: {token}")


def _evaluate_legal_use(values: Mapping[str, Any], signals: _Signals) -> None:
    value, found = _first_present(
        values,
        (
            "legal_use_restrictions",
            "legal_use",
            "permitted_use",
            "commercial_use_allowed",
        ),
    )
    if not found or value is None:
        signals.unknown_reasons.add("Legal use evidence is missing from the physical survey")
        return

    if isinstance(value, bool):
        if not value:
            signals.hard_reasons.add("Commercial legal use is not allowed")
        return

    if isinstance(value, Mapping):
        allowed = _coerce_bool(value.get("commercial_use_allowed"))
        if allowed is None:
            signals.unknown_reasons.add("Legal use evidence has an unsupported shape")
        elif not allowed:
            signals.hard_reasons.add("Commercial legal use is not allowed")
        return

    legal_tokens = _tokens(value)
    if not legal_tokens:
        signals.unknown_reasons.add("Legal use evidence is empty")
        return

    for token in legal_tokens:
        if token in _EMPTY_RESTRICTION_VALUES or token in {"commercial", "commercial_use", "commercial_allowed"}:
            continue
        if any(marker in token for marker in _HARD_RESTRICTION_MARKERS):
            signals.hard_reasons.add(f"Legal use restriction blocks operation: {token}")
        elif any(marker in token for marker in _CONDITIONAL_RESTRICTION_MARKERS):
            signals.conditional_reasons.add(f"Legal use requires an approval or condition: {token}")
        else:
            # An explicit but unfamiliar restriction must not be silently
            # treated as permission.  Keep the gate fail-closed.
            signals.hard_reasons.add(f"Unresolved legal use restriction: {token}")


def _survey_values(survey: Mapping[str, Any]) -> dict[str, Any]:
    attributes = dict(_as_mapping(survey.get("attributes")))
    values = {key: value for key, value in survey.items() if key != "attributes"}
    values.update(attributes)
    utilities = _as_mapping(attributes.get("utilities"))
    for key, value in utilities.items():
        values.setdefault(f"utilities_{key}", value)
        values.setdefault(key, value)
    return values


def _evaluate_flood(values: Mapping[str, Any], signals: _Signals) -> None:
    value, found = _first_present(values, ("flood_risk_level", "flood_risk", "flood_zone"))
    if not found or value is None:
        signals.unknown_reasons.add("Flood risk evidence is missing from the physical survey")
        return

    number = _coerce_float(value)
    if number is not None:
        if number >= 3:
            signals.hard_reasons.add(f"Flood risk is high: {number:g}")
        elif number > 0:
            signals.conditional_reasons.add(f"Flood risk requires mitigation: {number:g}")
        return

    token = _normalise_token(value)
    if token in {"none", "low", "minimal", "no_risk", "no_flood_risk"}:
        return
    if token in {"medium", "moderate", "medium_risk", "marginal"}:
        signals.conditional_reasons.add(f"Flood risk requires mitigation: {token}")
        return
    if token in {"high", "critical", "severe", "very_high", "high_risk"}:
        signals.hard_reasons.add(f"Flood risk is high: {token}")
        return
    signals.unknown_reasons.add(f"Unrecognised flood risk value requires survey: {token}")


def _evaluate_utilities(values: Mapping[str, Any], signals: _Signals) -> None:
    power, power_found = _first_present(
        values,
        (
            "utilities_power_capacity_amp",
            "utilities_power_capacity_amps",
            "power_capacity_amp",
            "power_capacity_amps",
            "electricity_capacity_amp",
        ),
    )
    water, water_found = _first_present(
        values,
        (
            "utilities_water_pressure_psi",
            "water_pressure_psi",
            "water_pressure",
        ),
    )
    power_value = _coerce_float(power)
    water_value = _coerce_float(water)

    if not power_found or power_value is None or power_value < 0:
        signals.unknown_reasons.add("Electrical power capacity is missing or invalid")
    elif power_value < _MINIMUM_POWER_AMPS:
        signals.conditional_reasons.add(
            f"Electrical power capacity requires an upgrade: {power_value:g}A"
        )

    if not water_found or water_value is None or water_value < 0:
        signals.unknown_reasons.add("Water pressure is missing or invalid")
    elif water_value < _MINIMUM_WATER_PRESSURE_PSI:
        signals.conditional_reasons.add(
            f"Water pressure requires an upgrade: {water_value:g} psi"
        )

    for name in (
        "utilities_power_available",
        "utilities_water_available",
        "utilities_drainage_available",
        "utilities_sewer_available",
        "utilities_connected",
    ):
        if name not in values:
            continue
        available = _coerce_bool(values[name])
        if available is None:
            signals.unknown_reasons.add(f"Utility availability flag is invalid: {name}")
        elif not available:
            signals.conditional_reasons.add(f"Utility connection requires remediation: {name}")


def _evaluate_frontage(values: Mapping[str, Any], signals: _Signals) -> None:
    value, found = _first_present(
        values,
        ("frontage_meters", "frontage_m", "frontageMeters", "frontage"),
    )
    if isinstance(value, Mapping):
        value, nested_found = _first_present(value, ("meters", "metres", "frontage_meters", "frontage_m"))
        found = found and nested_found
    frontage = _coerce_float(value) if found else None
    if frontage is None or frontage < 0:
        signals.unknown_reasons.add("Frontage measurement is missing or invalid")
    elif frontage < _MINIMUM_FRONTAGE_METERS:
        signals.conditional_reasons.add(
            f"Frontage is below the operating threshold: {frontage:g}m"
        )


def _evaluate_access(values: Mapping[str, Any], signals: _Signals) -> None:
    loading, loading_found = _first_present(
        values,
        ("loading_zone_available", "loading_available", "loading_zone"),
    )
    temporary_stop, temporary_found = _first_present(
        values,
        ("temporary_stop_allowed", "temporary_stop", "parking_or_temporary_stop"),
    )

    loading_allowed = _coerce_bool(loading) if loading_found else None
    temporary_allowed = _coerce_bool(temporary_stop) if temporary_found else None
    if loading_allowed is None:
        signals.unknown_reasons.add("Loading-zone evidence is missing or invalid")
    if temporary_allowed is None:
        signals.unknown_reasons.add("Temporary-stop evidence is missing or invalid")

    if loading_allowed is False and temporary_allowed is True:
        signals.conditional_reasons.add("No loading zone; operation depends on temporary stopping")
    elif loading_allowed is False and temporary_allowed is False:
        signals.conditional_reasons.add("Neither a loading zone nor temporary stopping is available")


def _evaluate_other_restrictions(values: Mapping[str, Any], signals: _Signals) -> None:
    value, found = _first_present(
        values,
        ("site_restrictions", "operational_restrictions", "access_restrictions", "restrictions"),
    )
    if not found or value is None:
        return
    if isinstance(value, Mapping):
        signals.unknown_reasons.add("Site restrictions have an unsupported shape")
        return
    tokens = _tokens(value)
    for token in tokens:
        if token in _EMPTY_RESTRICTION_VALUES:
            continue
        if any(marker in token for marker in _HARD_RESTRICTION_MARKERS):
            signals.hard_reasons.add(f"Site restriction blocks operation: {token}")
        else:
            signals.conditional_reasons.add(f"Site restriction requires mitigation: {token}")


def _evaluate_survey(survey: Mapping[str, Any]) -> _Signals:
    values = _survey_values(survey)
    signals = _Signals()
    _evaluate_legal_use(values, signals)
    _evaluate_frontage(values, signals)
    _evaluate_utilities(values, signals)
    _evaluate_flood(values, signals)
    _evaluate_access(values, signals)
    _evaluate_other_restrictions(values, signals)
    return signals


def _survey_statuses(survey: Mapping[str, Any]) -> list[str]:
    statuses: list[str] = []
    top_level = survey.get("review_status")
    if top_level is not None:
        statuses.append(_normalise_token(top_level))
    review = _as_mapping(survey.get("review"))
    review_record = _as_mapping(survey.get("review_record"))
    for record in (review, review_record):
        status = record.get("review_status")
        if status is not None:
            statuses.append(_normalise_token(status))
    return statuses


def _usable_survey(survey: Mapping[str, Any], site_id: str) -> tuple[bool, str | None]:
    target_entity_id = survey.get("target_entity_id")
    if target_entity_id is not None and str(target_entity_id) != site_id:
        return False, f"Survey {survey.get('survey_id', '<unknown>')} targets a different site"

    survey_type = _normalise_token(survey.get("survey_type", ""))
    if survey_type and survey_type not in {"physical_feasibility", "site_feasibility"}:
        return False, f"Survey {survey.get('survey_id', '<unknown>')} is not a physical-feasibility survey"

    statuses = _survey_statuses(survey)
    if not statuses:
        return False, f"Survey {survey.get('survey_id', '<unknown>')} has no independent review status"
    if any(status != "approved" for status in statuses):
        return False, f"Survey {survey.get('survey_id', '<unknown>')} is not approved for decision use"
    for flag_name, description in (
        ("is_retracted", "retracted"),
        ("is_superseded", "superseded"),
    ):
        if flag_name not in survey:
            continue
        flag = _coerce_bool(survey[flag_name])
        if flag is None:
            return False, f"Survey {survey.get('survey_id', '<unknown>')} has invalid {flag_name} state"
        if flag:
            return False, f"Survey {survey.get('survey_id', '<unknown>')} is {description}"
    return True, None


class SiteFeasibilityService:
    """Evaluate a site with a conservative, non-binding physical gate."""

    def evaluate_feasibility(
        self,
        site_id: str,
        market_context: Mapping[str, Any],
        surveys: list[Mapping[str, Any]],
    ) -> SiteFeasibilityDocument:
        context = _context_for_site(market_context, site_id)
        signals = _Signals()
        _evaluate_zoning(context, signals)

        usable_surveys: list[Mapping[str, Any]] = []
        excluded_surveys: set[str] = set()
        for raw_survey in surveys or []:
            survey = _as_mapping(raw_survey)
            usable, exclusion_reason = _usable_survey(survey, site_id)
            if usable:
                usable_surveys.append(survey)
            elif exclusion_reason:
                excluded_surveys.add(exclusion_reason)

        # Evaluate every usable survey and union the signals.  The final
        # precedence below is deliberately independent of list order: a
        # missing value in any competing evidence cannot be hidden by a later
        # conditional result.
        if not usable_surveys:
            signals.unknown_reasons.add(
                "No approved, non-retracted, non-superseded physical survey is available"
            )
        else:
            for survey in sorted(
                usable_surveys,
                key=lambda item: str(item.get("survey_id", "")),
            ):
                signals.merge(_evaluate_survey(survey))

        if signals.hard_reasons:
            recommendation = FeasibilityDecision.INFEASIBLE
        elif signals.unknown_reasons:
            recommendation = FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
        elif signals.conditional_reasons:
            recommendation = FeasibilityDecision.CONDITIONAL
        else:
            recommendation = FeasibilityDecision.FEASIBLE

        reasons = sorted(
            signals.hard_reasons | signals.unknown_reasons | signals.conditional_reasons
        )
        metadata = {
            "source_market_context_id": context.get("context_id") or context.get("document_id"),
            "policy_version": "physical-feasibility-gate-v1",
            "approved_survey_ids": sorted(
                str(survey.get("survey_id"))
                for survey in usable_surveys
                if survey.get("survey_id") is not None
            ),
            "binding_recommendation_allowed": recommendation == FeasibilityDecision.FEASIBLE,
        }
        if excluded_surveys and not usable_surveys:
            reasons.extend(sorted(excluded_surveys))
            reasons = sorted(set(reasons))

        document = SiteFeasibilityDocument(
            document_id=str(uuid4()),
            site_id=site_id,
            decision=FeasibilityAssessment(
                recommendation=recommendation,
                reasons=tuple(reasons),
            ),
            metadata=metadata,
        )
        # Validate at the producer boundary so downstream consumers never see
        # an object that merely resembles the physical-feasibility contract.
        validate_site_feasibility_document(document)
        return document
