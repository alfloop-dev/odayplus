"""Receipt matching follows the original area/size/floor/rent search intent."""

from copy import deepcopy

import pytest

from modules.listing.application.saved_search import (
    LISTING_CRITERIA_KIND,
    ListingCriteriaInvalid,
    SavedListingCriteria,
    receipt_matching_facts,
)
from shared.infrastructure.persistence.listing_search_matches import evidence_digest


def query(**updates):
    return {
        "kind": LISTING_CRITERIA_KIND,
        "heatZoneId": "zone-a",
        "minAreaPing": 14,
        "maxAreaPing": 26,
        "maxRentPerMonth": 60000,
        "groundFloorOnly": True,
        **updates,
    }


def receipt(**updates):
    return {
        "scope": {"tenant_id": "tenant-a", "heat_zone_id": "zone-a"},
        "state": "READY",
        "policy_state": "ASSISTED_ENTRY_ONLY",
        "intake_method": "MANUAL",
        "fields": [
            {"field_path": path, "effective": value, "masked": False}
            for path, value in [
                ("area_ping", 18), ("rent_amount", 50000), ("currency", "TWD"), ("floor", "1F 臨路")
            ]
        ],
        **updates,
    }


@pytest.mark.parametrize("method", ["URL", "MANUAL", "CSV", "APPROVED_FEED"])
def test_admitted_receipt_matches_without_fetching_or_changing_its_identity(method):
    value = receipt(intake_method=method)
    before = deepcopy(value)
    assert (
        SavedListingCriteria.from_query(query()).evaluate(value, tenant_id="tenant-a").outcome
        == "MATCH"
    )
    assert value == before


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("area_ping", 13, "AREA_OUTSIDE_RANGE"),
        ("area_ping", 27, "AREA_OUTSIDE_RANGE"),
        ("rent_amount", 60001, "RENT_ABOVE_MAXIMUM"),
        ("floor", "B1", "NOT_GROUND_FLOOR_ONLY"),
        ("floor", "1-2F", "NOT_GROUND_FLOOR_ONLY"),
        ("floor", "1F 2F", "NOT_GROUND_FLOOR_ONLY"),
    ],
)
def test_conditions_reject_actual_mismatch(field, value, reason):
    record = receipt()
    next(f for f in record["fields"] if f["field_path"] == field)["effective"] = value
    result = SavedListingCriteria.from_query(query()).evaluate(record, tenant_id="tenant-a")
    assert result.outcome == "NO_MATCH" and reason in result.reasons


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), -1, 0, "50000"])
def test_unknown_or_ambiguous_rent_is_not_a_favorable_search_match(value):
    record = receipt()
    record["fields"][1]["effective"] = value
    result = SavedListingCriteria.from_query(query()).evaluate(record, tenant_id="tenant-a")
    assert result.outcome == "INSUFFICIENT_EVIDENCE" and result.reasons == ("RENT_UNAVAILABLE",)


@pytest.mark.parametrize(
    "change", ["mask", "top_mask", "missing_effective", "conflicting_alias", "missing_floor"]
)
def test_masked_missing_or_conflicting_lineage_is_not_reconstructed(change):
    record = receipt()
    if change == "mask":
        record["fields"][0].update(masked=True, parsed=18)
    elif change == "top_mask":
        record["masked_fields"] = ["area_ping"]
    elif change == "missing_effective":
        record["fields"][0].pop("effective")
        record["fields"][0]["parsed"] = 18
    elif change == "conflicting_alias":
        record["fields"].append({"field_path": "areaPing", "effective": 20, "masked": False})
    else:
        record["fields"] = [f for f in record["fields"] if f["field_path"] != "floor"]
    assert (
        SavedListingCriteria.from_query(query()).evaluate(record, tenant_id="tenant-a").outcome
        == "INSUFFICIENT_EVIDENCE"
    )


@pytest.mark.parametrize(
    "state", ["QUARANTINED", "FAILED", "SUBMITTED", "RETRIEVING", "AWAITING_ASSISTED_ENTRY"]
)
def test_no_search_result_promotes_pending_or_refused_source_work(state):
    result = SavedListingCriteria.from_query(query()).evaluate(
        receipt(state=state), tenant_id="tenant-a"
    )
    assert result.outcome == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize("policy", ["POLICY_UNKNOWN", "AUTH_REQUIRED", "SOURCE_BLOCKED", None])
def test_a_ready_state_cannot_replace_missing_or_refused_source_admission(policy):
    result = SavedListingCriteria.from_query(query()).evaluate(
        receipt(policy_state=policy), tenant_id="tenant-a"
    )
    assert result.outcome == "INSUFFICIENT_EVIDENCE" and result.reasons == (
        "SOURCE_POLICY_NOT_ADMITTED",
    )


def test_private_tenant_zone_and_unknown_zone_have_distinct_safe_outcomes():
    criteria = SavedListingCriteria.from_query(query())
    assert criteria.evaluate(receipt(), tenant_id="tenant-b").outcome == "NO_MATCH"
    assert (
        criteria.evaluate(
            receipt(scope={"tenant_id": "tenant-a", "heat_zone_id": "other"}), tenant_id="tenant-a"
        ).outcome
        == "NO_MATCH"
    )
    assert criteria.evaluate(
        receipt(scope={"tenant_id": "tenant-a"}), tenant_id="tenant-a"
    ).reasons == ("HEAT_ZONE_UNAVAILABLE",)


@pytest.mark.parametrize(
    "updates",
    [
        {"maxRentPerMonth": True},
        {"maxRentPerMonth": float("nan")},
        {"minAreaPing": 27},
        {"minAreaPing": -1},
        {"groundFloorOnly": "true"},
        {"heatZoneId": " "},
        {"extra": "ignored?"},
    ],
)
def test_invalid_or_ambiguous_condition_is_refused(updates):
    with pytest.raises(ListingCriteriaInvalid):
        SavedListingCriteria.from_query(query(**updates))


@pytest.mark.parametrize("method", ["MANUAL", "CSV"])
@pytest.mark.parametrize(
    "change",
    ["USD", "missing", "masked", "top_mask", "conflict", "no_effective", "null", "typed"],
)
def test_rent_ceiling_requires_explicit_readable_unambiguous_twd(method, change):
    record = receipt(state="NEEDS_REVIEW", intake_method=method)
    currency = next(f for f in record["fields"] if f["field_path"] == "currency")
    if change == "missing":
        record["fields"].remove(currency)
    elif change == "masked":
        currency.update(masked=True, parsed="TWD")
    elif change == "top_mask":
        record["masked_fields"] = ["currency"]
    elif change == "conflict":
        record["fields"].append({**currency, "effective": "USD"})
    elif change == "no_effective":
        currency.pop("effective")
        currency.update(parsed="TWD", normalized="TWD", corrected="TWD")
    else:
        currency["effective"] = {"USD": "USD", "null": None, "typed": True}[change]
    before = deepcopy(record)
    criteria = SavedListingCriteria.from_query(query())
    result = criteria.evaluate(record, tenant_id="tenant-a")
    assert result.outcome == "INSUFFICIENT_EVIDENCE"
    assert result.reasons == ("RENT_CURRENCY_UNAVAILABLE",)
    # No rent constraint means currency is irrelevant, not a new admission gate.
    assert SavedListingCriteria.from_query(query(maxRentPerMonth=None)).evaluate(
        record, tenant_id="tenant-a"
    ).outcome == "MATCH"
    assert record == before


def test_uncomparable_currency_does_not_turn_a_large_amount_into_a_rent_mismatch():
    record = receipt()
    next(f for f in record["fields"] if f["field_path"] == "currency")["effective"] = "USD"
    next(f for f in record["fields"] if f["field_path"] == "rent_amount")["effective"] = 70000
    assert SavedListingCriteria.from_query(query()).evaluate(
        record, tenant_id="tenant-a"
    ).outcome == "INSUFFICIENT_EVIDENCE"


def test_currency_lineage_is_part_of_original_facts_and_typed_digest():
    record = receipt()
    before = deepcopy(record)
    facts = receipt_matching_facts(record)
    currency = next(f for f in facts["fields"] if f["field_path"] == "currency")
    assert currency == {"field_path": "currency", "effective": "TWD", "masked": False}
    original_hash = evidence_digest(facts)
    currency["effective"] = "USD"
    assert evidence_digest(facts) != original_hash
    assert record == before  # Facts are a detached snapshot, not a writer.
    record["fields"].append({"field_path": "raw_text", "effective": "USD", "masked": False})
    assert evidence_digest(receipt_matching_facts(record)) == original_hash


def test_existing_inbox_saved_views_are_not_reinterpreted_as_listing_searches():
    assert SavedListingCriteria.from_query({"stage": "READY"}) is None
    criteria = SavedListingCriteria.from_query(query())
    assert SavedListingCriteria.from_query(criteria.to_query()) == criteria
