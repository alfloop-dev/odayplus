"""Package10 listing criteria over admitted intake facts, without discovery I/O.

The criteria are a private saved-view query. A match is a search result, not a
property identity decision, geocode, candidate promotion or source admission.
The caller must supply committed, admitted receipt rows and project current
record/field permissions before exposing results to an operator.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

LISTING_CRITERIA_KIND = "listing-search.v1"
LISTING_SEARCH_RECEIPT_SERVICE = "listing-search"
_KEYS = frozenset(
    {"kind", "heatZoneId", "minAreaPing", "maxAreaPing", "maxRentPerMonth", "groundFloorOnly"}
)
_GROUND_FLOOR = re.compile(r"^(?:1[fF]|[fF]1|1樓|一樓)(?:\s*(?:臨路|店面|臨街|地面))*$")
_MATCHING_FIELDS = frozenset(
    {"area_ping", "areaPing", "rent_amount", "rent", "rentPerMonth", "floor"}
)


def receipt_matching_facts(receipt: dict[str, Any]) -> dict[str, Any]:
    """Snapshot only the facts the matcher uses, retaining missing/typed values.

    Workflow history, correlation IDs and parser raw/source text are outside
    this envelope. Source admission and scope remain explicit evidence.
    """
    names = (
        "intake_id",
        "version",
        "scope",
        "state",
        "policy_state",
        "intake_method",
        "source_id",
        "source_policy_version",
        "source_snapshot_id",
        "source_snapshot_version",
        "masked_fields",
    )
    facts = {name: deepcopy(receipt[name]) for name in names if name in receipt}
    fields = receipt.get("fields")
    if isinstance(fields, list):
        facts["fields"] = [
            {
                name: deepcopy(field[name])
                for name in ("field_path", "masked", "effective", "classification")
                if name in field
            }
            for field in fields
            if isinstance(field, dict) and field.get("field_path") in _MATCHING_FIELDS
        ]
    elif "fields" in receipt:
        facts["fields"] = deepcopy(fields)
    return facts


class ListingCriteriaInvalid(ValueError):
    """A recognized listing query has an invalid or ambiguous condition."""


@dataclass(frozen=True)
class ListingCriteriaEvaluation:
    outcome: str
    reasons: tuple[str, ...] = ()


def _finite_number(value: Any) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def _effective_field(receipt: dict[str, Any], aliases: frozenset[str]) -> Any:
    """Use effective lineage facts; never replace a missing/masked value."""
    if any(name in (receipt.get("masked_fields") or []) for name in aliases):
        return None
    fields = receipt.get("fields")
    if not isinstance(fields, list):
        return None
    values = []
    for field in fields:
        if not isinstance(field, dict) or field.get("field_path") not in aliases:
            continue
        if field.get("masked") is not False or "effective" not in field:
            return None
        values.append(field["effective"])
    if not values or any(type(v) is not type(values[0]) or v != values[0] for v in values):
        return None
    return values[0]


@dataclass(frozen=True)
class SavedListingCriteria:
    heat_zone_id: str
    min_area_ping: float | None = None
    max_area_ping: float | None = None
    max_rent_per_month: float | None = None
    ground_floor_only: bool = False

    @classmethod
    def from_query(cls, query: Any) -> SavedListingCriteria | None:
        # Existing intake saved views retain their separate, original queries.
        if not isinstance(query, dict) or query.get("kind") != LISTING_CRITERIA_KIND:
            return None
        if set(query) - _KEYS:
            raise ListingCriteriaInvalid("LISTING_CRITERIA_UNKNOWN_FIELD")
        zone = query.get("heatZoneId")
        if type(zone) is not str or not zone.strip() or zone != zone.strip() or len(zone) > 200:
            raise ListingCriteriaInvalid("LISTING_CRITERIA_ZONE_REQUIRED")
        numbers = {}
        for name in ("minAreaPing", "maxAreaPing", "maxRentPerMonth"):
            value = query.get(name)
            number = None if value is None else _finite_number(value)
            if value is not None and (number is None or number < 0):
                raise ListingCriteriaInvalid("LISTING_CRITERIA_FINITE_NONNEGATIVE_REQUIRED")
            numbers[name] = number
        low, high = numbers["minAreaPing"], numbers["maxAreaPing"]
        if low is not None and high is not None and low > high:
            raise ListingCriteriaInvalid("LISTING_CRITERIA_AREA_RANGE_REVERSED")
        floor = query.get("groundFloorOnly", False)
        if type(floor) is not bool:
            raise ListingCriteriaInvalid("LISTING_CRITERIA_FLOOR_BOOLEAN_REQUIRED")
        return cls(zone, low, high, numbers["maxRentPerMonth"], floor)

    def to_query(self) -> dict[str, Any]:
        return {
            "kind": LISTING_CRITERIA_KIND,
            "heatZoneId": self.heat_zone_id,
            "minAreaPing": self.min_area_ping,
            "maxAreaPing": self.max_area_ping,
            "maxRentPerMonth": self.max_rent_per_month,
            "groundFloorOnly": self.ground_floor_only,
        }

    def evaluate(self, receipt: dict[str, Any], *, tenant_id: str) -> ListingCriteriaEvaluation:
        scope = receipt.get("scope")
        if not isinstance(scope, dict) or scope.get("tenant_id") != tenant_id:
            return ListingCriteriaEvaluation("NO_MATCH", ("TENANT_SCOPE_DENIED",))
        zone = scope.get("heat_zone_id")
        if zone is None:
            return ListingCriteriaEvaluation("INSUFFICIENT_EVIDENCE", ("HEAT_ZONE_UNAVAILABLE",))
        if zone != self.heat_zone_id:
            return ListingCriteriaEvaluation("NO_MATCH", ("OTHER_HEAT_ZONE",))
        # These are completed source/admitted-entry stages, not permission to
        # fetch a page. A failure, quarantine or pending parser is never a hit.
        if receipt.get("state") not in {"READY", "NEEDS_REVIEW"}:
            return ListingCriteriaEvaluation("INSUFFICIENT_EVIDENCE", ("INTAKE_FACTS_NOT_READY",))
        if receipt.get("policy_state") not in {"ASSISTED_ENTRY_ONLY", "APPROVED_RETRIEVAL"}:
            return ListingCriteriaEvaluation(
                "INSUFFICIENT_EVIDENCE", ("SOURCE_POLICY_NOT_ADMITTED",)
            )
        if receipt.get("intake_method") not in {"URL", "MANUAL", "CSV", "APPROVED_FEED"}:
            return ListingCriteriaEvaluation(
                "INSUFFICIENT_EVIDENCE", ("INTAKE_METHOD_UNAVAILABLE",)
            )
        missing, mismatch = [], []
        if self.min_area_ping is not None or self.max_area_ping is not None:
            area = _finite_number(_effective_field(receipt, frozenset({"area_ping", "areaPing"})))
            if area is None or area <= 0:
                missing.append("AREA_UNAVAILABLE")
            elif (self.min_area_ping is not None and area < self.min_area_ping) or (
                self.max_area_ping is not None and area > self.max_area_ping
            ):
                mismatch.append("AREA_OUTSIDE_RANGE")
        if self.max_rent_per_month is not None:
            rent = _finite_number(
                _effective_field(receipt, frozenset({"rent_amount", "rent", "rentPerMonth"}))
            )
            if rent is None or rent <= 0:
                missing.append("RENT_UNAVAILABLE")
            elif rent > self.max_rent_per_month:
                mismatch.append("RENT_ABOVE_MAXIMUM")
        if self.ground_floor_only:
            floor = _effective_field(receipt, frozenset({"floor"}))
            if floor is None or floor == "":
                missing.append("FLOOR_UNAVAILABLE")
            elif not (
                (type(floor) is int and floor == 1)
                or (type(floor) is str and _GROUND_FLOOR.fullmatch(floor.strip()))
            ):
                mismatch.append("NOT_GROUND_FLOOR_ONLY")
        if mismatch:
            return ListingCriteriaEvaluation("NO_MATCH", tuple(mismatch))
        if missing:
            return ListingCriteriaEvaluation("INSUFFICIENT_EVIDENCE", tuple(missing))
        return ListingCriteriaEvaluation("MATCH")
