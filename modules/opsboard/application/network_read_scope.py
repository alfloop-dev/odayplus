"""Scope projection for the opt-in Network reader, before derived summaries.

The listing snapshot is the authoritative relationship index. A child cannot
escape an excluded parent by declaring the right tenant or duplicating scope.
Missing scope evidence on a restricted axis is a denial, not an empty grant.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from shared.auth import Principal


def record_in_scope(principal: Principal, row: dict[str, Any]) -> bool:
    nested = row.get("scope") or {}
    if not isinstance(nested, dict):
        return False
    tenants = [source[key] for source in (row, nested)
               for key in ("tenantId", "tenant_id") if source.get(key) is not None]
    checks = (
        (principal.scope.permits_brand, ("brandId", "brand_id")),
        (principal.scope.permits_region, ("regionId", "region_id")),
        (principal.scope.permits_store, ("storeId", "store_id")),
        (principal.scope.permits_assigned_area, ("assignedAreaId", "assigned_area_id")),
        (principal.scope.permits_heat_zone, ("heatZoneId", "heat_zone_id")),
    )
    return bool(tenants) and all(t == principal.tenant_id for t in tenants) and all(
        all(check(value) for value in (
            [source[key] for source in (row, nested) for key in keys if key in source] or [None]
        )) for check, keys in checks
    )


def linked_record_in_scope(
    principal: Principal, row: dict[str, Any], parent: dict[str, Any]
) -> bool:
    # Only absent fields may inherit authoritative parent metadata. Conflicting
    # child metadata must fail its own check, never override the parent denial.
    checks = (
        (principal.scope.permits_brand, ("brandId", "brand_id")),
        (principal.scope.permits_region, ("regionId", "region_id")),
        (principal.scope.permits_store, ("storeId", "store_id")),
        (principal.scope.permits_assigned_area, ("assignedAreaId", "assigned_area_id")),
        (principal.scope.permits_heat_zone, ("heatZoneId", "heat_zone_id")),
    )
    if not isinstance(row.get("scope") or {}, dict):
        return False
    for source in (row, row.get("scope") or {}):
        if any(source[k] != principal.tenant_id for k in ("tenantId", "tenant_id") if source.get(k) is not None):
            return False
        if any(not check(source[k]) for check, keys in checks for k in keys if k in source):
            return False
    evidence = {**parent, **row}
    evidence["scope"] = {**(parent.get("scope") or {}), **(row.get("scope") or {})}
    return record_in_scope(principal, evidence)


def candidate_scope_evidence(principal: Principal, snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    listings = {r["id"]: r for r in snapshot.get("listings", []) if record_in_scope(principal, r)}
    return {
        row["id"]: {**listings[row["listingId"]], **row,
                    "scope": {**(listings[row["listingId"]].get("scope") or {}), **(row.get("scope") or {})}}
        for row in snapshot.get("candidates", [])
        if row.get("listingId") in listings
        and linked_record_in_scope(principal, row, listings[row["listingId"]])
    }


def visible_candidate_ids(principal: Principal, snapshot: dict[str, Any]) -> set[str]:
    return set(candidate_scope_evidence(principal, snapshot))


def project_intake_record(row: dict[str, Any], listing_ids: set[str] | None = None) -> dict[str, Any]:
    result = deepcopy(row)
    match = result.get("matchResult")
    if match and match.get("targetListingId") not in (listing_ids or set()):
        result["matchResult"] = None
    result["auditEvents"] = scoped_audit_events(result.get("auditEvents", []), {
        "intake": {row["id"]}, "assistedIntake": {row["id"]},
    })
    return result


def scoped_audit_events(events: list[dict[str, Any]], targets: dict[str, set[str]]) -> list[dict[str, Any]]:
    # Free-form messages/metadata may reference other objects. Without a
    # per-field relationship contract they are not safe scoped read evidence.
    return [
        {key: row.get(key) for key in (
            "id", "occurredAt", "actorRoleId", "actorName", "category",
            "action", "targetType", "targetId", "correlationId",
        )}
        for row in events
        if row.get("targetId") in targets.get(row.get("targetType"), set())
    ]


# Listing field contract for the scoped reader, inventoried from every
# NetworkListingService producer (seed, repository reload, convert, merge,
# archive, intake create/revise/duplicate). A field is either the listing's
# own state or a reference that is re-derived from the visible id set.
# Unclassified fields are withheld: a new producer field is not scoped read
# evidence until someone decides what it references.
LISTING_OWN_FIELDS = frozenset({
    "id", "tenantId", "tenant_id", "scope", "brandId", "brand_id", "regionId",
    "region_id", "storeId", "store_id", "assignedAreaId", "assigned_area_id",
    "heatZoneId", "heat_zone_id", "sourceId", "sourceListingId", "sourceUrl",
    "canonicalUrl", "contentFingerprint", "address", "coordinates", "latitude",
    "longitude", "h3Index", "status", "rentPerMonth", "areaPing", "floor",
    "frontageMeters", "listingType", "geocodeConfidence", "listingConfidence",
    "listingConfidenceProvenance", "hardRuleFailures", "hardRuleSummary",
    "fitScore", "firstSeenAt", "convertedAt", "archivedReason", "archivedAt",
    "prior90dCellNetRevenue", "prior90dCellTransactionCount",
    "prior90dCellStoreCount", "featureSnapshotTime", "hasLegalHold",
    "legalHold", "proposer", "submitter", "owner", "brokerCompany",
    "contactEmail", "rawSnapshot",
})
# merge_listing writes both directions: the source names its target, the
# target accumulates its sources. mergeReason/mergedAt describe the merge as a
# whole (the target keeps only the latest reason), so they are shown only when
# every counterpart is visible.
LISTING_MERGE_REFERENCES = ("duplicateOfId", "mergedIntoId")
LISTING_MERGE_CONTEXT = ("mergeReason", "mergedAt")


def _merge_counterparts(row: dict[str, Any]) -> list[Any]:
    sources = row.get("mergedSourceListingIds") or []
    refs = [row.get(key) for key in LISTING_MERGE_REFERENCES]
    # A malformed source list cannot be checked, so it counts as unseen.
    refs.extend(sources if isinstance(sources, list) else [object()])
    return [ref for ref in refs if ref is not None]


def _is_visible(ref: Any, ids: set[str]) -> bool:
    return isinstance(ref, str) and ref in ids


def evidence_owner(ref: Any, known_ids: set[str]) -> str | None:
    # Every producer writes "EV-<listing or intake id>-<kind>"; merge and the
    # intake decisions append other objects' refs onto the target listing.
    # Longest match so an excluded "L-1-X" never passes as visible "L-1".
    if not isinstance(ref, str):
        return None
    owners = [oid for oid in known_ids if ref.startswith(f"EV-{oid}-")]
    return max(owners, key=len, default=None)


def project_listing_record(
    row: dict[str, Any], *, listing_ids: set[str], evidence_ids: set[str],
    known_ids: set[str], candidate_ids: set[str],
) -> dict[str, Any]:
    result = {key: deepcopy(value) for key, value in row.items() if key in LISTING_OWN_FIELDS}
    for key in LISTING_MERGE_REFERENCES:
        if key in row:
            result[key] = row[key] if _is_visible(row[key], listing_ids) else None
    if "mergedSourceListingIds" in row:
        sources = row["mergedSourceListingIds"]
        result["mergedSourceListingIds"] = (
            [ref for ref in sources if _is_visible(ref, listing_ids)] if isinstance(sources, list) else []
        )
    whole_merge_visible = all(_is_visible(ref, listing_ids) for ref in _merge_counterparts(row))
    for key in LISTING_MERGE_CONTEXT:
        if key in row:
            result[key] = row[key] if whole_merge_visible else None
    if "candidateId" in row:
        result["candidateId"] = (
            row["candidateId"] if _is_visible(row["candidateId"], candidate_ids) else None
        )
    if "sourceEvidence" in row:
        refs = row["sourceEvidence"]
        result["sourceEvidence"] = [
            ref for ref in refs if evidence_owner(ref, known_ids) in evidence_ids
        ] if isinstance(refs, list) else []
    return result


def project_listing_snapshot(principal: Principal, snapshot: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(snapshot)
    all_listings = result.get("listings", [])
    all_intakes = result.get("assistedIntakes", [])
    listings = {row["id"]: row for row in all_listings if record_in_scope(principal, row)}
    result["listings"] = list(listings.values())
    result["assistedIntakes"] = [
        project_intake_record(row, set(listings))
        for row in all_intakes if record_in_scope(principal, row)
    ]
    evidence = candidate_scope_evidence(principal, result)
    ids = set(evidence)
    known_ids = {row.get("id") for row in all_listings + all_intakes} | {
        ref for row in all_listings for ref in _merge_counterparts(row) if isinstance(ref, str)
    }
    evidence_ids = set(listings) | {row["id"] for row in result["assistedIntakes"]}
    result["listings"] = [
        project_listing_record(row, listing_ids=set(listings), evidence_ids=evidence_ids,
                               known_ids=known_ids - {None}, candidate_ids=ids)
        for row in result["listings"]
    ]
    result["candidates"] = [row for row in result.get("candidates", []) if row["id"] in ids]
    result["siteReviews"] = [
        row for row in result.get("siteReviews", [])
        if row.get("candidateId") in ids
        and linked_record_in_scope(principal, row, evidence[row["candidateId"]])
    ]
    review_ids = {row["id"] for row in result["siteReviews"]}
    for row in result["candidates"]:
        if row.get("reviewId") not in review_ids:
            row["reviewId"] = None
    # HeatZone summaries contain whole-zone demand/rent/reasons, not just the
    # visible listings. A brand/region/store restriction cannot authorize that
    # aggregate by having one visible listing in the zone. Require zone-owned
    # evidence (the tenant partition is supplied by the service resolver).
    result["heatZones"] = [
        row for row in result.get("heatZones", [])
        if record_in_scope(principal, {
            "tenantId": principal.tenant_id, **row, "heatZoneId": row.get("id"),
        })
    ]
    for rank, row in enumerate(result["heatZones"], 1):
        row["rank"] = rank
    zone_ids = {row["id"] for row in result["heatZones"]}
    if result.get("selectedHeatZoneId") not in zone_ids:
        result["selectedHeatZoneId"] = next(iter(sorted(zone_ids)), None)
    source_ids = {row.get("sourceId") for row in result["listings"] + result["assistedIntakes"]}
    result["listingSources"] = [row for row in result.get("listingSources", []) if row.get("id") in source_ids]
    # These steps were built from the unfiltered pipeline and contain opaque
    # cross-zone prose. Do not present them as scoped progress observations.
    result["expansionSteps"] = []
    result["auditEvents"] = scoped_audit_events(result.get("auditEvents", []), {
        "listing": set(listings), "candidate": ids,
        "intake": {row["id"] for row in result["assistedIntakes"]},
        "review": {row["id"] for row in result["siteReviews"]},
    })
    result["counts"] = {key: len(result[key]) for key in (
        "heatZones", "listings", "candidates", "siteReviews", "assistedIntakes",
    )}
    return result


def project_review_snapshot(
    principal: Principal, snapshot: dict[str, Any], scope_snapshot: dict[str, Any]
) -> dict[str, Any]:
    result = deepcopy(snapshot)
    evidence = candidate_scope_evidence(principal, scope_snapshot)
    ids = set(evidence)
    result["candidates"] = [row for row in result.get("candidates", [])
                            if row.get("id") in ids and linked_record_in_scope(principal, row, evidence[row["id"]])]
    ids = {row["id"] for row in result["candidates"]}
    result["reviews"] = [row for row in result.get("reviews", [])
                         if row.get("candidateId") in ids and linked_record_in_scope(principal, row, evidence[row["candidateId"]])]
    # compareText is produced against the whole queue, not this visible subset.
    for row in result["reviews"]:
        row["compareText"] = ""
    review_ids = {row["id"] for row in result["reviews"]}
    for row in result["candidates"]:
        if row.get("reviewId") not in review_ids:
            row["reviewId"] = None
    result["approvals"] = [
        row for row in result.get("approvals", [])
        if row.get("candidateId") in ids and row.get("reviewId") in review_ids
        and linked_record_in_scope(principal, row, evidence[row["candidateId"]])
    ]
    result["decisions"] = [
        row for row in result.get("decisions", [])
        if row.get("candidateId", row.get("candidate_site_id")) in ids
        and row.get("reviewId", row.get("decision_id")) in review_ids
        and linked_record_in_scope(principal, row, evidence[row.get("candidateId", row.get("candidate_site_id"))])
    ]
    result["auditEvents"] = scoped_audit_events(result.get("auditEvents", []), {
        "candidate": ids, "review": review_ids,
    })
    pending = sum(row.get("status") == "pending" for row in result["reviews"])
    result["counts"] = {"reviews": len(result["reviews"]), "pending": pending,
                        "decided": len(result["reviews"]) - pending}
    return result
