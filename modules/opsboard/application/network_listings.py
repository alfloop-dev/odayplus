"""Network listing intake service for Operator Console R4.

Owns the task-scoped Listing Radar state used by
``/api/v1/operator/network-listings``:

- R4 HeatZone/listing/candidate identifiers.
- Listing to candidate conversion.
- Duplicate merge while retaining source evidence.
- Hard-rule archive with reason.

The service is deliberately in-memory for the Operator Console product slice.
It is deterministic, idempotent for write replays, and narrow enough to compose
with later SiteScore/Review tasks without owning those layers.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from typing import Any


class NetworkListingNotFound(RuntimeError):
    """Raised when a listing/candidate/zone id is unknown."""


class NetworkListingConflict(RuntimeError):
    """Raised when a requested mutation conflicts with current state."""


class NetworkListingPolicyError(RuntimeError):
    """Raised when a mutation violates the network intake policy."""


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            output.append(value)
            seen.add(value)
    return output


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _seed_state() -> dict[str, Any]:
    return {
        "heatZones": [
            {
                "id": "HZ-01",
                "label": "信義松仁生活圈",
                "rank": 1,
                "centroid": [121.5668, 25.0330],
                "demandGap": 0.86,
                "competitionIndex": 0.34,
                "cannibalizationRisk": "medium",
                "rentBand": "NT$52k-88k/month",
                "confidence": 0.93,
                "recommendedLens": "demand",
                "reasons": [
                    "Office-lunch and late-night demand gap remains high.",
                    "Transit and residential POI mix supports ODAY_G2 format.",
                    "L-2024 has clean geocode, floor, area, and source evidence.",
                ],
                "risks": ["Rent negotiation pressure", "Nearby competitor 220m away"],
                "nextStep": "Convert L-2024 into CS-1001 and continue SiteScore evidence review.",
            },
            {
                "id": "HZ-02",
                "label": "板橋府中生活圈",
                "rank": 2,
                "centroid": [121.4575, 25.0100],
                "demandGap": 0.78,
                "competitionIndex": 0.43,
                "cannibalizationRisk": "medium",
                "rentBand": "NT$48k-72k/month",
                "confidence": 0.88,
                "recommendedLens": "fit",
                "reasons": [
                    "Weekend service demand is under-realized.",
                    "L-2025 has duplicate-source coverage after merge.",
                ],
                "risks": ["Duplicate broker postings require evidence merge before review"],
                "nextStep": "Keep L-2025 active after merging L-2029 duplicate source evidence.",
            },
        ],
        "listingSources": [
            {
                "id": "SRC-591",
                "name": "591 licensed broker intake",
                "status": "connected",
                "complianceNote": "R4 proof uses licensed/manual intake fields and preserves source evidence refs.",
                "lastSyncedAt": "2026-07-14T06:10:00Z",
            },
            {
                "id": "SRC-BROKER",
                "name": "Broker confirmation",
                "status": "manualOnly",
                "complianceNote": "Broker notes are retained as source evidence before any merge/archive action.",
                "lastSyncedAt": "2026-07-14T06:12:00Z",
            },
        ],
        "listings": [
            {
                "id": "L-2024",
                "sourceId": "SRC-591",
                "sourceListingId": "s591-2024",
                "heatZoneId": "HZ-01",
                "address": "台北市信義區松仁路 96 號 1F",
                "status": "new",
                "rentPerMonth": 58000,
                "areaPing": 18,
                "floor": "1F 臨路",
                "frontageMeters": 6,
                "geocodeConfidence": 0.94,
                "hardRuleFailures": [],
                "hardRuleSummary": "3/3 pass: area, floor, permitted use",
                "sourceEvidence": [
                    "EV-L-2024-RAW-591",
                    "EV-L-2024-GEOCODE",
                    "EV-L-2024-BROKER-CALL",
                ],
                "fitScore": 72,
                "firstSeenAt": "2026-07-14T06:10:00Z",
                "sourceUrl": "https://example.invalid/listings/L-2024",
            },
            {
                "id": "L-2025",
                "sourceId": "SRC-BROKER",
                "sourceListingId": "broker-2025",
                "heatZoneId": "HZ-02",
                "address": "新北市板橋區府中路 52 號 1F",
                "status": "watching",
                "rentPerMonth": 53000,
                "areaPing": 22,
                "floor": "1F",
                "frontageMeters": 5,
                "geocodeConfidence": 0.93,
                "hardRuleFailures": [],
                "hardRuleSummary": "3/3 pass: area, floor, permitted use",
                "sourceEvidence": ["EV-L-2025-BROKER", "EV-L-2025-GEOCODE"],
                "fitScore": 70,
                "firstSeenAt": "2026-07-13T09:25:00Z",
                "sourceUrl": "https://example.invalid/listings/L-2025",
            },
            {
                "id": "L-2029",
                "sourceId": "SRC-591",
                "sourceListingId": "s591-2029",
                "heatZoneId": "HZ-02",
                "address": "新北市板橋區府中路 52 號 1F",
                "status": "duplicate",
                "rentPerMonth": 53000,
                "areaPing": 22,
                "floor": "1F",
                "frontageMeters": 5,
                "geocodeConfidence": 0.93,
                "duplicateOfId": "L-2025",
                "hardRuleFailures": [],
                "hardRuleSummary": "3/3 pass, duplicate same-address source",
                "sourceEvidence": [
                    "EV-L-2029-RAW-591",
                    "EV-L-2029-ADDRESS-MATCH",
                    "EV-L-2029-RENT-MATCH",
                ],
                "fitScore": 70,
                "firstSeenAt": "2026-07-14T06:10:00Z",
                "sourceUrl": "https://example.invalid/listings/L-2029",
            },
            {
                "id": "L-2030",
                "sourceId": "SRC-591",
                "sourceListingId": "s591-2030",
                "heatZoneId": "HZ-01",
                "address": "台北市信義區松仁路 110 號 1-2F",
                "status": "hardfail",
                "rentPerMonth": 128000,
                "areaPing": 40,
                "floor": "1-2F",
                "frontageMeters": 8,
                "geocodeConfidence": 0.95,
                "hardRuleFailures": ["area_above_format_maximum", "floor_not_ground_level"],
                "hardRuleSummary": "2/3 blocked: area above 30 ping and second-floor operations required",
                "sourceEvidence": ["EV-L-2030-RAW-591", "EV-L-2030-HARD-RULES"],
                "fitScore": 44,
                "firstSeenAt": "2026-07-14T06:10:00Z",
                "sourceUrl": "https://example.invalid/listings/L-2030",
            },
        ],
        "candidates": [],
        "siteReviews": [],
        "auditEvents": [],
    }


class NetworkListingService:
    """Application service for R4 Listing Radar intake actions."""

    def __init__(self) -> None:
        self._state = _seed_state()
        self._idempotency_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def reset(self) -> dict[str, Any]:
        self._state = _seed_state()
        self._idempotency_cache = {}
        return self.snapshot()

    def snapshot(
        self,
        *,
        selected_heat_zone_id: str | None = None,
        lens: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        selected_id = selected_heat_zone_id or "HZ-01"
        active_lens = lens or "demand"
        return {
            "source": "api",
            "heatZones": _copy(self._state["heatZones"]),
            "listingSources": _copy(self._state["listingSources"]),
            "listings": _copy(self._state["listings"]),
            "candidates": _copy(self._state["candidates"]),
            "siteReviews": _copy(self._state["siteReviews"]),
            "expansionSteps": self._expansion_steps(selected_id=selected_id),
            "selectedHeatZoneId": selected_id,
            "selectedLens": active_lens,
            "auditEvents": _copy(self._state["auditEvents"]),
            "correlationId": correlation_id,
            "counts": {
                "heatZones": len(self._state["heatZones"]),
                "listings": len(self._state["listings"]),
                "candidates": len(self._state["candidates"]),
                "siteReviews": len(self._state["siteReviews"]),
            },
        }

    def convert_listing(
        self,
        *,
        listing_id: str,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        cache_key = ("convert", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        listing = self._listing(listing_id)
        if listing.get("hardRuleFailures"):
            raise NetworkListingPolicyError(
                f"{listing_id} cannot convert until hard-rule failures are resolved"
            )
        if listing.get("status") in {"duplicate", "archived", "expired"}:
            raise NetworkListingConflict(f"{listing_id} is {listing.get('status')} and cannot convert")

        existing = self._candidate_for_listing(listing_id)
        created = existing is None
        if existing is None:
            candidate_id = "CS-1001" if listing_id == "L-2024" else f"CS-{1000 + len(self._state['candidates']) + 1}"
            existing = {
                "id": candidate_id,
                "listingId": listing_id,
                "heatZoneId": listing["heatZoneId"],
                "title": "信義松仁候選點" if candidate_id == "CS-1001" else f"{listing_id} 候選點",
                "address": listing["address"],
                "status": "ready",
                "score": 82 if candidate_id == "CS-1001" else 68,
                "recommendation": "GO" if candidate_id == "CS-1001" else "WAIT",
                "modelVersion": "SiteScore v2.3",
                "datasetSnapshotId": "FS-20260704-0600",
                "missingData": [],
                "reviewId": "RV-1001" if candidate_id == "CS-1001" else None,
            }
            self._state["candidates"].append(existing)
            if existing.get("reviewId"):
                self._state["siteReviews"].append(
                    {
                        "id": existing["reviewId"],
                        "candidateId": existing["id"],
                        "status": "pending",
                        "requestedByRoleId": "expansionManager",
                        "reviewerRoleIds": ["opsLead", "auditPm"],
                        "requestedAt": "2026-07-14T06:30:00Z",
                        "reasonRequired": True,
                    }
                )

        listing["status"] = "candidate"
        listing["candidateId"] = existing["id"]
        listing["convertedAt"] = listing.get("convertedAt") or _now()
        audit = self._audit(
            action="listing.convert",
            target_id=listing_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={"candidateId": existing["id"], "created": created},
        )
        result = {
            "listing": _copy(listing),
            "candidate": _copy(existing),
            "created": created,
            "auditEvent": audit,
            "candidateCount": len(
                [item for item in self._state["candidates"] if item.get("listingId") == listing_id]
            ),
            "correlationId": correlation_id,
            "expansionSteps": self._expansion_steps(selected_id=listing["heatZoneId"]),
        }
        if idempotency_key:
            self._idempotency_cache[cache_key] = _copy(result)
        return result

    def merge_listing(
        self,
        *,
        source_listing_id: str,
        target_listing_id: str,
        reason: str,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        reason = reason.strip()
        if not reason:
            raise NetworkListingPolicyError("merge reason is required")

        cache_key = ("merge", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        source = self._listing(source_listing_id)
        target = self._listing(target_listing_id)
        if source_listing_id == target_listing_id:
            raise NetworkListingConflict("source and target listing must be different")

        source_evidence = list(source.get("sourceEvidence", []))
        target["sourceEvidence"] = _dedupe(list(target.get("sourceEvidence", [])) + source_evidence)
        target["mergedSourceListingIds"] = _dedupe(
            list(target.get("mergedSourceListingIds", [])) + [source_listing_id]
        )
        target["mergeReason"] = reason
        target["mergedAt"] = target.get("mergedAt") or _now()
        source["status"] = "duplicate"
        source["duplicateOfId"] = target_listing_id
        source["mergedIntoId"] = target_listing_id
        source["mergedAt"] = source.get("mergedAt") or _now()
        source["mergeReason"] = reason

        audit = self._audit(
            action="listing.merge",
            target_id=target_listing_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={
                "sourceListingId": source_listing_id,
                "sourceEvidenceRetained": len(source_evidence),
                "targetEvidenceCount": len(target["sourceEvidence"]),
            },
        )
        result = {
            "source": _copy(source),
            "target": _copy(target),
            "sourceEvidenceRetained": _copy(source_evidence),
            "auditEvent": audit,
            "correlationId": correlation_id,
            "expansionSteps": self._expansion_steps(selected_id=target["heatZoneId"]),
        }
        if idempotency_key:
            self._idempotency_cache[cache_key] = _copy(result)
        return result

    def archive_listing(
        self,
        *,
        listing_id: str,
        reason: str,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        reason = reason.strip()
        if not reason:
            raise NetworkListingPolicyError("archive reason is required")

        cache_key = ("archive", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        listing = self._listing(listing_id)
        listing["status"] = "archived"
        listing["archivedReason"] = reason
        listing["archivedAt"] = listing.get("archivedAt") or _now()
        audit = self._audit(
            action="listing.archive",
            target_id=listing_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={
                "reason": reason,
                "hardRuleFailures": list(listing.get("hardRuleFailures", [])),
                "sourceEvidenceCount": len(listing.get("sourceEvidence", [])),
            },
        )
        result = {
            "listing": _copy(listing),
            "auditEvent": audit,
            "correlationId": correlation_id,
            "expansionSteps": self._expansion_steps(selected_id=listing["heatZoneId"]),
        }
        if idempotency_key:
            self._idempotency_cache[cache_key] = _copy(result)
        return result

    def _listing(self, listing_id: str) -> dict[str, Any]:
        for listing in self._state["listings"]:
            if listing.get("id") == listing_id:
                return listing
        raise NetworkListingNotFound(f"listing {listing_id} not found")

    def _candidate_for_listing(self, listing_id: str) -> dict[str, Any] | None:
        return next(
            (candidate for candidate in self._state["candidates"] if candidate.get("listingId") == listing_id),
            None,
        )

    def _audit(
        self,
        *,
        action: str,
        target_id: str,
        actor_role_id: str,
        actor_name: str | None,
        correlation_id: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "id": f"AUD-NET-{uuid.uuid4().hex[:10]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Expansion Manager",
            "category": "workflow",
            "action": action,
            "targetType": "listing",
            "targetId": target_id,
            "message": f"{action} recorded for {target_id}",
            "correlationId": correlation_id,
            "metadata": metadata,
        }
        self._state["auditEvents"].insert(0, event)
        return _copy(event)

    def _expansion_steps(self, *, selected_id: str) -> list[dict[str, Any]]:
        has_selected_zone = any(zone["id"] == selected_id for zone in self._state["heatZones"])
        candidate = next(
            (item for item in self._state["candidates"] if item.get("id") == "CS-1001"),
            None,
        )
        listing = self._listing("L-2024")
        candidate_created = candidate is not None
        return [
            {
                "id": "find",
                "label": "Find Area",
                "tabIndex": 0,
                "state": "completed" if has_selected_zone else "current",
                "entityId": selected_id,
                "summary": f"{selected_id} selected and synchronized to the map.",
            },
            {
                "id": "radar",
                "label": "Listing Radar",
                "tabIndex": 1,
                "state": "completed" if candidate_created else "current",
                "entityId": "L-2024",
                "summary": f"{listing['id']} clean; duplicate and hard-rule rows visible.",
            },
            {
                "id": "candidate",
                "label": "Candidate",
                "tabIndex": 2,
                "state": "current" if candidate_created else "next",
                "entityId": "CS-1001" if candidate_created else "L-2024",
                "summary": "CS-1001 created once from L-2024." if candidate_created else "Convert L-2024 to create CS-1001.",
            },
            {
                "id": "sitescore",
                "label": "SiteScore",
                "tabIndex": 3,
                "state": "next" if candidate_created else "blocked",
                "entityId": "CS-1001",
                "summary": "Ready for GO 82 evidence review." if candidate_created else "Blocked until candidate exists.",
            },
            {
                "id": "compare",
                "label": "Compare",
                "tabIndex": 4,
                "state": "next" if candidate_created else "blocked",
                "entityId": "CS-1001",
                "summary": "Compare HZ-01 candidate against HZ-02 pipeline." if candidate_created else "Blocked by missing candidate.",
            },
            {
                "id": "review",
                "label": "Review",
                "tabIndex": 5,
                "state": "blocked" if not candidate_created else "next",
                "entityId": "RV-1001" if candidate_created else None,
                "summary": "Reasoned review is available after scoring gate." if candidate_created else "No candidate review packet yet.",
            },
        ]


__all__ = [
    "NetworkListingConflict",
    "NetworkListingNotFound",
    "NetworkListingPolicyError",
    "NetworkListingService",
]
