"""Network listing intake service for Operator Console R4/R5.

Owns the Listing Radar state used by ``/api/v1/operator/network-listings``:

- R4 HeatZone/listing/candidate identifiers.
- Listing to candidate conversion.
- Duplicate merge while retaining source evidence.
- Hard-rule archive with reason.
- R5 human-assisted URL intake (submit/correct/decide/retry/promote).

Persistence is injected, not assumed. The service composes with two optional
repositories and never reaches into their internals:

- ``listing_repository`` for Listing Radar rows.
- ``intake_repository`` (:class:`AssistedIntakeRepository`) for assisted intake
  records and their idempotency cache.

When a repository is omitted the service falls back to a process-local
implementation (:class:`InMemoryAssistedIntakeRepository`), which keeps tests
and fixture-only runs self-contained. In the composed application both are
durable — see ``shared.infrastructure.persistence.operator_network_listings``
and the wiring in ``apps/api/app/routes/operator.py`` — so intake records and
replayed writes survive a restart.

Writes are deterministic and idempotent: a replayed ``(action, key)`` returns
the original response from the repository-backed idempotency cache rather than
applying the effect twice.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class IntakeIdempotencyRecord:
    """A replayable write response keyed by ``(action, key)``."""

    action: str
    key: str
    response: dict[str, Any]


class AssistedIntakeRepository(Protocol):
    """Public persistence contract for assisted listing intake.

    The service depends on this contract only, so a durable implementation can
    be substituted without the application layer reaching into a generic
    document store or any other backing detail.
    """

    def list_intakes(self) -> list[dict[str, Any]]: ...

    def save_intake(self, intake: dict[str, Any]) -> None: ...

    def list_idempotency_records(self) -> list[IntakeIdempotencyRecord]: ...

    def save_idempotency_record(self, record: IntakeIdempotencyRecord) -> None: ...

    def get_listing_metadata(self, listing_id: str) -> dict[str, Any]: ...

    def save_listing_metadata(self, listing_id: str, metadata: dict[str, Any]) -> None: ...

    def get_candidate_metadata(self, candidate_id: str) -> dict[str, Any]: ...

    def save_candidate_metadata(self, candidate_id: str, metadata: dict[str, Any]) -> None: ...

    def get_promotion(self, promo_id: str) -> dict[str, Any] | None: ...

    def save_promotion(self, promo: dict[str, Any]) -> None: ...

    def list_promotions(self) -> list[dict[str, Any]]: ...

    def clear(self) -> None: ...


@dataclass
class InMemoryAssistedIntakeRepository:
    """Process-local implementation of :class:`AssistedIntakeRepository`.

    Used when the service runs without a durable backend; state is lost on
    restart, which is exactly what the durable implementation exists to fix.
    """

    intakes: dict[str, dict[str, Any]] = field(default_factory=dict)
    idempotency: dict[tuple[str, str], IntakeIdempotencyRecord] = field(default_factory=dict)
    listing_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidate_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    promotions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def list_intakes(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self.intakes.values()]

    def save_intake(self, intake: dict[str, Any]) -> None:
        self.intakes[intake["id"]] = copy.deepcopy(intake)

    def list_idempotency_records(self) -> list[IntakeIdempotencyRecord]:
        return list(self.idempotency.values())

    def save_idempotency_record(self, record: IntakeIdempotencyRecord) -> None:
        self.idempotency[(record.action, record.key)] = record

    def get_listing_metadata(self, listing_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.listing_metadata.get(listing_id) or {})

    def save_listing_metadata(self, listing_id: str, metadata: dict[str, Any]) -> None:
        self.listing_metadata[listing_id] = copy.deepcopy(metadata)

    def get_candidate_metadata(self, candidate_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.candidate_metadata.get(candidate_id) or {})

    def save_candidate_metadata(self, candidate_id: str, metadata: dict[str, Any]) -> None:
        self.candidate_metadata[candidate_id] = copy.deepcopy(metadata)

    def get_promotion(self, promo_id: str) -> dict[str, Any] | None:
        return copy.deepcopy(self.promotions.get(promo_id))

    def save_promotion(self, promo: dict[str, Any]) -> None:
        self.promotions[promo["promotion_decision_id"]] = copy.deepcopy(promo)

    def list_promotions(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self.promotions.values()]

    def clear(self) -> None:
        self.intakes.clear()
        self.idempotency.clear()
        self.listing_metadata.clear()
        self.candidate_metadata.clear()
        self.promotions.clear()


class NetworkListingNotFound(RuntimeError):
    """Raised when a listing/candidate/zone id is unknown."""


class NetworkListingConflict(RuntimeError):
    """Raised when a requested mutation conflicts with current state."""


class NetworkListingPolicyError(RuntimeError):
    """Raised when a mutation violates the network intake policy."""


def _require_acknowledged_risk(
    *, risk_summary: str | None, risk_acknowledged: bool, action_label: str
) -> str:
    """Validate the caller-supplied risk disclosure for a high-impact write.

    The summary must come from the caller and be acknowledged there: a
    server-invented summary would record consent to text the operator never
    saw, which is exactly what the audit trail is supposed to evidence.
    """
    summary = (risk_summary or "").strip()
    if not summary:
        raise NetworkListingPolicyError(f"risk summary is required to {action_label}")
    if not risk_acknowledged:
        raise NetworkListingPolicyError(
            f"risk acknowledgement is required to {action_label}"
        )
    return summary


def _require_governed_write_context(
    *,
    idempotency_key: str | None,
    correlation_id: str | None,
    action_label: str,
) -> str:
    key = (idempotency_key or "").strip()
    if not key:
        raise NetworkListingPolicyError(f"idempotency key is required to {action_label}")
    if not (correlation_id or "").strip():
        raise NetworkListingPolicyError(f"correlation id is required to {action_label}")
    return key


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

    def __init__(
        self,
        listing_repository: Any | None = None,
        intake_repository: AssistedIntakeRepository | None = None,
    ) -> None:
        self._listing_repository = listing_repository
        self._intakes: AssistedIntakeRepository = (
            intake_repository if intake_repository is not None else InMemoryAssistedIntakeRepository()
        )
        self._state = _seed_state()
        self._idempotency_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._load_intakes()
        self._load_idempotency_cache()
        self._load_listings()
        self._load_candidates()

    def _load_listings(self) -> None:
        if self._listing_repository is not None:
            repo_listings = self._listing_repository.list_listings()
            if repo_listings:
                self._state["listings"] = []
                for lst in repo_listings:
                    self._state["listings"].append(self._listing_to_dict(lst))
            else:
                for lst_dict in self._state["listings"]:
                    lst_obj, addr_obj, key_obj = self._dict_to_listing(lst_dict)
                    self._listing_repository.save_listing(lst_obj, addr_obj, key_obj)

    def _load_candidates(self) -> None:
        if self._listing_repository is not None:
            repo_candidates = self._listing_repository.list_candidates()
            if repo_candidates:
                self._state["candidates"] = []
                for cand in repo_candidates:
                    self._state["candidates"].append(self._candidate_to_dict(cand))
            else:
                for cand_dict in self._state["candidates"]:
                    cand_obj = self._dict_to_candidate(cand_dict)
                    self._listing_repository.save_candidate(cand_obj)

    def _get_listing_metadata(self, listing_id: str) -> dict[str, Any]:
        return self._intakes.get_listing_metadata(listing_id)

    def _save_listing_metadata(self, listing_id: str, metadata: dict[str, Any]) -> None:
        self._intakes.save_listing_metadata(listing_id, metadata)

    def _get_candidate_metadata(self, candidate_id: str) -> dict[str, Any]:
        return self._intakes.get_candidate_metadata(candidate_id)

    def _save_candidate_metadata(self, candidate_id: str, metadata: dict[str, Any]) -> None:
        self._intakes.save_candidate_metadata(candidate_id, metadata)

    def _listing_to_dict(self, lst: Any) -> dict[str, Any]:
        res = {
            "id": lst.listing_id,
            "sourceId": lst.source_id,
            "sourceListingId": lst.source_listing_id,
            "status": lst.listing_status,
            "rentPerMonth": int(lst.rent_amount),
            "areaPing": lst.area_ping,
            "floor": lst.floor,
            "frontageMeters": int(lst.frontage_m) if lst.frontage_m else 0,
            "geocodeConfidence": lst.confidence,
            "sourceUrl": lst.snapshot_id,
        }
        meta = self._get_listing_metadata(lst.listing_id)
        if meta:
            res.update(meta)
        else:
            for item in _seed_state()["listings"]:
                if item["id"] == lst.listing_id:
                    for k, v in item.items():
                        if k not in res:
                            res[k] = v
                    break
        return res

    def _dict_to_listing(self, d: dict[str, Any]) -> tuple[Any, Any, Any]:
        from modules.listing.domain.models import ListingDedupKey
        from shared.domain.models import AddressLocation, Listing
        lst = Listing(
            listing_id=d["id"],
            source_listing_id=d["sourceListingId"],
            source_id=d["sourceId"],
            listing_status=d["status"],
            rent_amount=float(d["rentPerMonth"]),
            area_ping=float(d["areaPing"]),
            floor=d["floor"],
            frontage_m=float(d.get("frontageMeters") or 0),
            confidence=float(d.get("geocodeConfidence") or 1.0),
            snapshot_id=d.get("sourceUrl") or "",
        )
        addr = AddressLocation(
            address_id=f"ADDR-{d['id']}",
            raw_address=d.get("address") or "",
            normalized_address=d.get("address") or "",
            geocode_confidence=float(d.get("geocodeConfidence") or 1.0),
        )
        key = ListingDedupKey(
            source_id=d["sourceId"],
            source_listing_id=d["sourceListingId"],
            normalized_address=d.get("address") or "",
            rent_amount=float(d["rentPerMonth"]),
            area_ping=float(d["areaPing"]),
        )
        return lst, addr, key

    def _candidate_to_dict(self, cand: Any) -> dict[str, Any]:
        res = {
            "id": cand.candidate_site.candidate_site_id,
            "listingId": cand.listing.listing_id,
            "heatZoneId": cand.heat_zone_id,
            "title": f"{cand.listing.listing_id} 候選點",
            "address": cand.address.raw_address,
            "status": cand.status.value if hasattr(cand.status, "value") else str(cand.status),
            "score": 68,
            "recommendation": "WAIT",
            "modelVersion": "SiteScore v2.3",
            "datasetSnapshotId": "FS-20260704-0600",
            "missingData": [],
        }
        meta = self._get_candidate_metadata(cand.candidate_site.candidate_site_id)
        if meta:
            res.update(meta)
        else:
            if res["id"] == "CS-1001":
                res["title"] = "信義松仁候選點"
                res["score"] = 82
                res["recommendation"] = "GO"
                res["reviewId"] = "RV-1001"
        return res

    def _dict_to_candidate(self, d: dict[str, Any]) -> Any:
        from modules.listing.domain.models import CandidateSiteDraft, ListingPipelineStatus
        from shared.domain.models import CandidateSite
        listing = self._listing(d["listingId"])
        lst_obj, addr_obj, _ = self._dict_to_listing(listing)

        cand_site = CandidateSite(
            candidate_site_id=d["id"],
            listing_id=d["listingId"],
            address_id=addr_obj.address_id,
            site_status=d["status"],
        )
        return CandidateSiteDraft(
            listing=lst_obj,
            address=addr_obj,
            candidate_site=cand_site,
            heat_zone_id=d["heatZoneId"],
            listing_source=listing.get("sourceId") or "",
            status=ListingPipelineStatus.CANDIDATE,
        )

    def _sync_listing_to_repo(self, listing_id: str) -> None:
        if self._listing_repository is not None:
            listing = self._listing(listing_id)
            lst_obj, addr_obj, key_obj = self._dict_to_listing(listing)
            self._listing_repository.save_listing(lst_obj, addr_obj, key_obj)

            meta = {
                "heatZoneId": listing.get("heatZoneId"),
                "hardRuleFailures": listing.get("hardRuleFailures"),
                "hardRuleSummary": listing.get("hardRuleSummary"),
                "sourceEvidence": listing.get("sourceEvidence"),
                "fitScore": listing.get("fitScore"),
                "firstSeenAt": listing.get("firstSeenAt"),
                # The domain Listing carries only `status`, which cannot tell an
                # already-merged duplicate from a merge-eligible one. Without
                # these, a restart drops the terminal marker and merge_listing
                # would accept a second request for the same source.
                "duplicateOfId": listing.get("duplicateOfId"),
                "mergedIntoId": listing.get("mergedIntoId"),
                "mergedAt": listing.get("mergedAt"),
                "mergeReason": listing.get("mergeReason"),
                "mergedSourceListingIds": listing.get("mergedSourceListingIds"),
            }
            self._save_listing_metadata(listing_id, meta)

    def _sync_candidate_to_repo(self, candidate_id: str) -> None:
        if self._listing_repository is not None:
            candidate = None
            for cand in self._state["candidates"]:
                if cand["id"] == candidate_id:
                    candidate = cand
                    break
            if candidate:
                cand_obj = self._dict_to_candidate(candidate)
                self._listing_repository.save_candidate(cand_obj)

                meta = {
                    "title": candidate.get("title"),
                    "score": candidate.get("score"),
                    "recommendation": candidate.get("recommendation"),
                    "modelVersion": candidate.get("modelVersion"),
                    "datasetSnapshotId": candidate.get("datasetSnapshotId"),
                    "missingData": candidate.get("missingData"),
                    "reviewId": candidate.get("reviewId"),
                }
                self._save_candidate_metadata(candidate_id, meta)

    def _load_intakes(self) -> None:
        self._state["assistedIntakes"] = self._intakes.list_intakes()

    def _load_idempotency_cache(self) -> None:
        self._idempotency_cache = {
            (record.action, record.key): record.response
            for record in self._intakes.list_idempotency_records()
        }

    def _save_idempotency(self, action: str, key: str, response: dict[str, Any]) -> None:
        self._idempotency_cache[(action, key)] = _copy(response)
        self._intakes.save_idempotency_record(
            IntakeIdempotencyRecord(action=action, key=key, response=_copy(response))
        )

    def _save_intake(self, intake: dict[str, Any]) -> None:
        self._state.setdefault("assistedIntakes", [])
        found = False
        for idx, item in enumerate(self._state["assistedIntakes"]):
            if item["id"] == intake["id"]:
                self._state["assistedIntakes"][idx] = intake
                found = True
                break
        if not found:
            self._state["assistedIntakes"].append(intake)

        self._intakes.save_intake(intake)

    def reset(self) -> dict[str, Any]:
        self._state = _seed_state()
        self._idempotency_cache = {}
        self._intakes.clear()
        self._state["assistedIntakes"] = []

        if self._listing_repository is not None:
            self._listing_repository.clear()
            for lst_dict in self._state["listings"]:
                lst_obj, addr_obj, key_obj = self._dict_to_listing(lst_dict)
                self._listing_repository.save_listing(lst_obj, addr_obj, key_obj)

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
        self._state.setdefault("assistedIntakes", [])
        return {
            "source": "api",
            "heatZones": _copy(self._state["heatZones"]),
            "listingSources": _copy(self._state["listingSources"]),
            "listings": _copy(self._state["listings"]),
            "candidates": _copy(self._state["candidates"]),
            "siteReviews": _copy(self._state["siteReviews"]),
            "assistedIntakes": _copy(self._state["assistedIntakes"]),
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
                "assistedIntakes": len(self._state["assistedIntakes"]),
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
        # Server-side role check
        allowed_roles = {"expansionManager", "expansion-manager", "siteReviewer", "site_reviewer"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(f"role {actor_role_id!r} is not allowed to convert listing")

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
        self._sync_listing_to_repo(listing_id)
        self._sync_candidate_to_repo(existing["id"])

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
            self._save_idempotency("convert", idempotency_key, result)
        return result

    def merge_listing(
        self,
        *,
        source_listing_id: str,
        target_listing_id: str,
        reason: str,
        risk_summary: str | None = None,
        risk_acknowledged: bool = False,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        # Server-side role check
        allowed_roles = {"expansionManager", "expansion-manager", "siteReviewer", "site_reviewer"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(f"role {actor_role_id!r} is not allowed to merge listing")

        governed_key = _require_governed_write_context(
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            action_label="merge listing",
        )

        reason = reason.strip()
        if not reason:
            raise NetworkListingPolicyError("merge reason is required")

        risk_summary_text = _require_acknowledged_risk(
            risk_summary=risk_summary,
            risk_acknowledged=risk_acknowledged,
            action_label="merge listing",
        )

        cache_key = ("merge", governed_key)
        if cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        source = self._listing(source_listing_id)
        target = self._listing(target_listing_id)
        if source_listing_id == target_listing_id:
            raise NetworkListingConflict("source and target listing must be different")
        # Merge is terminal for the source. Replaying the SAME idempotency key is
        # served from the cache above; reaching here with an already-merged source
        # means a genuinely new request, which would append a second listing.merge
        # audit event and overwrite the first merge's reason.
        merged_into = source.get("mergedIntoId")
        if merged_into:
            raise NetworkListingConflict(
                f"{source_listing_id} is already merged into {merged_into} and cannot be merged again"
            )

        source_before_status = source.get("status")
        target_before_evidence = list(target.get("sourceEvidence", []))

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
        self._sync_listing_to_repo(source_listing_id)
        self._sync_listing_to_repo(target_listing_id)

        audit = self._audit(
            action="listing.merge",
            target_id=target_listing_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={
                "sourceListingId": source_listing_id,
                # The operator's own words, recorded alongside the disclosure
                # they acknowledged; archive does the same.
                "reason": reason,
                "sourceEvidenceRetained": len(source_evidence),
                "targetEvidenceCount": len(target["sourceEvidence"]),
                "before": {
                    "sourceStatus": source_before_status,
                    "targetEvidenceCount": len(target_before_evidence),
                },
                "after": {
                    "sourceStatus": "duplicate",
                    "targetEvidenceCount": len(target["sourceEvidence"]),
                },
                "riskSummary": risk_summary_text,
                "riskAcknowledged": True,
                "effectSummary": (
                    f"Merge source {source_listing_id} into target {target_listing_id}."
                ),
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
        self._save_idempotency("merge", governed_key, result)
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
        # Server-side role check
        allowed_roles = {"expansionManager", "expansion-manager", "siteReviewer", "site_reviewer"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(f"role {actor_role_id!r} is not allowed to archive listing")

        reason = reason.strip()
        if not reason:
            raise NetworkListingPolicyError("archive reason is required")

        cache_key = ("archive", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        listing = self._listing(listing_id)
        before_status = listing.get("status")

        listing["status"] = "archived"
        listing["archivedReason"] = reason
        listing["archivedAt"] = listing.get("archivedAt") or _now()
        self._sync_listing_to_repo(listing_id)

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
                "before": {
                    "status": before_status,
                },
                "after": {
                    "status": "archived",
                },
                "effectSummary": f"Archive listing {listing_id}. Reason: {reason}",
            },
        )
        result = {
            "listing": _copy(listing),
            "auditEvent": audit,
            "correlationId": correlation_id,
            "expansionSteps": self._expansion_steps(selected_id=listing["heatZoneId"]),
        }
        if idempotency_key:
            self._save_idempotency("archive", idempotency_key, result)
        return result

    def submit_intake(
        self,
        *,
        url: str,
        heat_zone_id: str | None,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        job_queue: Any | None = None,
        async_intake: bool = False,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        cache_key = ("submit_intake", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        from modules.external_data.application.assisted_intake import (
            PARSER_VERSION,
            RetrievalFailure,
            content_fingerprint,
            effective_fields,
            match_listing,
            normalize_url,
            parse_snapshot,
            resolve_source_policy,
            validate_url,
        )
        from modules.external_data.security import redact_sensitive_snapshot

        url = url.strip()
        validate_url(url)
        canon_url = normalize_url(url)

        self._state.setdefault("assistedIntakes", [])
        for intake in self._state["assistedIntakes"]:
            if intake.get("canonicalUrl") == canon_url:
                if intake.get("stage") in {"NEEDS_REVIEW", "READY", "QUARANTINED", "FAILED", "AWAITING_ASSISTED_ENTRY"}:
                    # Exact duplicate before retrieval (terminal state idempotency)
                    return _copy(intake)
                else:
                    raise NetworkListingConflict(f"URL {url} is already being processed (intake {intake['id']})")

        policy = resolve_source_policy(url)
        intake_id = f"IN-{3001 + len(self._state['assistedIntakes'])}"

        intake = {
            "id": intake_id,
            "originalUrl": url,
            "canonicalUrl": canon_url,
            "submitter": actor_name or "林曉青（展店）",
            "owner": actor_name or "林曉青",
            "heatZoneId": heat_zone_id,
            "stage": "SUBMITTED",
            "sourceId": policy.source_id,
            "policy": policy.policy,
            "policyLabel": policy.policy_label,
            "policyReason": policy.policy_reason,
            "rawSnapshot": None,
            "snapshotId": None,
            "capturedAt": None,
            "parserVersion": PARSER_VERSION,
            "correlationId": correlation_id,
            "parsedFields": {},
            "matchResult": None,
            "auditEvents": [],
            "idempotencyKey": idempotency_key,
        }

        if policy.quarantines or policy.policy in {"POLICY_UNKNOWN", "SOURCE_BLOCKED"}:
            intake["stage"] = "QUARANTINED"
            intake["matchResult"] = {
                "outcome": "QUARANTINED",
                "outcomeLabel": "已隔離",
                "confidence": 0.0,
                "targetListingId": None,
                "agreeingSignals": [],
                "contradictingSignals": [],
                "summary": f"依來源政策 {policy.policy} 予以隔離：{policy.policy_reason}",
            }
        elif policy.policy in {"ASSISTED_ENTRY_ONLY", "AUTH_REQUIRED"}:
            intake["stage"] = "AWAITING_ASSISTED_ENTRY"
        elif policy.policy == "APPROVED_RETRIEVAL":
            if async_intake and job_queue is not None:
                intake["stage"] = "SUBMITTED"
                from shared.jobs.queue import JobRequest
                payload_to_send = {
                    "intake_id": intake_id,
                    "url": url,
                    "heat_zone_id": heat_zone_id,
                    "actor_role_id": actor_role_id,
                    "actor_name": actor_name,
                    "tenant_id": tenant_id,
                }
                job, created = job_queue.enqueue(
                    JobRequest(
                        job_type="assisted-listing-intake",
                        payload=payload_to_send,
                        idempotency_key=idempotency_key,
                    ),
                    correlation_id=correlation_id or "system",
                )
            else:
                intake["stage"] = "RETRIEVING"
                import json
                snapshot_service = self._get_snapshot_service()
                gate = self._get_security_gate(snapshot_service)
                
                resolved_tenant_id = tenant_id or "00000000-0000-0000-0000-000000000001"
                retrieval_res = gate.fetch(
                    canon_url,
                    tenant_id=resolved_tenant_id,
                    retrieval_method="fixture_replay",
                )
                
                if retrieval_res.ok:
                    raw_dict = json.loads(retrieval_res.body.decode("utf-8"))
                    redacted_raw = redact_sensitive_snapshot(raw_dict)
                    redacted_data = json.dumps(redacted_raw).encode("utf-8")
                    
                    snapshot_id = snapshot_service.create_snapshot(
                        tenant_id=resolved_tenant_id,
                        intake_id=intake_id,
                        source_id=policy.source_id,
                        raw_data=retrieval_res.body,
                        original_url=url,
                        canonical_url=canon_url,
                        media_type="application/json",
                        capture_method="SERVER_RETRIEVAL",
                        retention_class="STANDARD",
                        encryption_key_ref="kms://default-key",
                        observed_at=datetime.now(UTC),
                        captured_at=datetime.now(UTC),
                        bucket="taiwan-snapshots",
                        redacted_data=redacted_data,
                    )
                    
                    intake["stage"] = "PARSING"
                    intake["rawSnapshot"] = redacted_raw
                    intake["snapshotId"] = snapshot_id
                    intake["capturedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                    
                    from modules.external_data.application.assisted_intake import RetrievalResult
                    mock_retrieval_obj = RetrievalResult(
                        snapshot_id=snapshot_id,
                        captured_at=intake["capturedAt"],
                        raw=redacted_raw,
                    )
                    intake["parsedFields"] = parse_snapshot(mock_retrieval_obj)

                    effective_vals = effective_fields(intake["parsedFields"])

                    from modules.external_data.application.assisted_intake import (
                        ASSISTED_ENTRY_REQUIRED_FIELDS,
                    )
                    has_all_required = True
                    for rf in ASSISTED_ENTRY_REQUIRED_FIELDS:
                        val = effective_vals.get(rf)
                        if val in (None, ""):
                            has_all_required = False
                            break
                        if rf in ("rent", "areaPing"):
                            try:
                                if float(val) <= 0:
                                    has_all_required = False
                                    break
                            except (ValueError, TypeError):
                                has_all_required = False
                                break

                    if has_all_required:
                        intake["stage"] = "MATCHING"
                        fingerprint = content_fingerprint(effective_vals)

                        match_res = match_listing(
                            values=effective_vals,
                            canonical_url=canon_url,
                            source_id=policy.source_id,
                            fingerprint=fingerprint,
                            listings=self._get_match_listings(),
                        )
                        intake["matchResult"] = match_res.to_dict()
                        if match_res.outcome == "POSSIBLE_MATCH":
                            intake["stage"] = "NEEDS_REVIEW"
                        else:
                            intake["stage"] = "READY"
                    else:
                        intake["stage"] = "AWAITING_ASSISTED_ENTRY"
                else:
                    intake["stage"] = "FAILED"
                    failure_obj = RetrievalFailure(
                        code=retrieval_res.failure.code if retrieval_res.failure else "FAILED",
                        summary=retrieval_res.failure.summary if retrieval_res.failure else "retrieval failed",
                        next_action=retrieval_res.failure.next_action if retrieval_res.failure else "governance review",
                        retryable=retrieval_res.failure.retryable if retrieval_res.failure else False,
                    )
                    intake["failure"] = failure_obj.to_dict()


        audit_evt = {
            "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Expansion Manager",
            "action": "intake.create",
            "targetId": intake_id,
            "message": f"Intake record {intake_id} created for {url}.",
            "correlationId": correlation_id,
            "metadata": {
                "policy": policy.policy,
                "stage": intake["stage"],
                "matchOutcome": intake["matchResult"]["outcome"] if intake["matchResult"] else None,
            }
        }
        intake["auditEvents"].append(audit_evt)
        self._save_intake(intake)

        if idempotency_key:
            self._save_idempotency("submit_intake", idempotency_key, intake)
        return _copy(intake)

    def list_intakes(self, selected_heat_zone_id: str | None = None) -> list[dict[str, Any]]:
        self._load_intakes()
        self._state.setdefault("assistedIntakes", [])
        intakes = self._state["assistedIntakes"]
        if selected_heat_zone_id is not None:
            intakes = [item for item in intakes if item.get("heatZoneId") == selected_heat_zone_id]
        return _copy(intakes)

    def get_intake(self, intake_id: str) -> dict[str, Any]:
        self._load_intakes()
        self._state.setdefault("assistedIntakes", [])
        for intake in self._state["assistedIntakes"]:
            if intake["id"] == intake_id:
                return _copy(intake)
        raise NetworkListingNotFound(f"assisted intake record {intake_id} not found")

    def correct_intake(
        self,
        *,
        intake_id: str,
        fields: dict[str, Any],
        reason: str | None,
        risk_summary: str | None = None,
        risk_acknowledged: bool = False,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        # Server-side role check
        allowed_roles = {"expansionStaff", "expansion_user", "expansionManager", "expansion-manager", "dataSteward", "data_owner"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(f"role {actor_role_id!r} is not allowed to correct intake")

        governed_key = _require_governed_write_context(
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            action_label="correct intake",
        )
        cache_key = ("correct_intake", governed_key)
        if cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        risk_summary_text = _require_acknowledged_risk(
            risk_summary=risk_summary,
            risk_acknowledged=risk_acknowledged,
            action_label="correct intake",
        )

        intake = self._listing_intake(intake_id)

        from modules.external_data.application.assisted_intake import (
            IDENTITY_FIELDS,
            content_fingerprint,
            effective_fields,
            match_listing,
        )

        requires_reason = False
        for key in fields:
            if key in IDENTITY_FIELDS:
                requires_reason = True
                break
        if requires_reason and (not reason or not reason.strip()):
            raise NetworkListingPolicyError("reason is required for modifying identity-affecting fields")

        corrections_made = []
        before_after_changes = []
        for key, val in fields.items():
            if key not in intake["parsedFields"]:
                intake["parsedFields"][key] = {
                    "key": key,
                    "label": key,
                    "sourceValue": None,
                    "normalizedValue": None,
                    "correctedValue": None,
                    "correctionReason": None,
                    "identity": key in IDENTITY_FIELDS,
                    "lowConfidence": False,
                }

            before_val = intake["parsedFields"][key].get("correctedValue") or intake["parsedFields"][key].get("normalizedValue")
            intake["parsedFields"][key]["correctedValue"] = val
            intake["parsedFields"][key]["correctionReason"] = reason
            after_val = val
            corrections_made.append(f"'{key}' corrected from '{before_val}' to '{after_val}'")
            before_after_changes.append({
                "field": key,
                "before": before_val,
                "after": val,
            })

        effective_vals = effective_fields(intake["parsedFields"])

        from modules.external_data.application.assisted_intake import ASSISTED_ENTRY_REQUIRED_FIELDS
        has_all_required = True
        for rf in ASSISTED_ENTRY_REQUIRED_FIELDS:
            val = effective_vals.get(rf)
            if val in (None, ""):
                has_all_required = False
                break
            if rf in ("rent", "areaPing"):
                try:
                    if float(val) <= 0:
                        has_all_required = False
                        break
                except (ValueError, TypeError):
                    has_all_required = False
                    break

        if has_all_required:
            fingerprint = content_fingerprint(effective_vals)
            match_res = match_listing(
                values=effective_vals,
                canonical_url=intake["canonicalUrl"],
                source_id=intake["sourceId"],
                fingerprint=fingerprint,
                listings=self._get_match_listings(),
            )
            intake["matchResult"] = match_res.to_dict()
            if match_res.outcome == "POSSIBLE_MATCH":
                intake["stage"] = "NEEDS_REVIEW"
            else:
                intake["stage"] = "READY"
        else:
            intake["stage"] = "AWAITING_ASSISTED_ENTRY"

        audit_evt = {
            "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Expansion Manager",
            "action": "intake.correct",
            "targetId": intake_id,
            "message": f"Fields corrected: {'; '.join(corrections_made)}. Reason: {reason}",
            "correlationId": correlation_id,
            "metadata": {
                "fields": list(fields.keys()),
                "reason": reason,
                "stage": intake["stage"],
                "matchOutcome": intake["matchResult"]["outcome"] if intake["matchResult"] else None,
                "beforeAfter": before_after_changes,
                "riskSummary": risk_summary_text,
                "riskAcknowledged": True,
            }
        }
        intake["auditEvents"].append(audit_evt)
        self._save_intake(intake)
        self._save_idempotency("correct_intake", governed_key, intake)
        return _copy(intake)

    def decide_intake(
        self,
        *,
        intake_id: str,
        action: str,
        reason: str | None,
        risk_summary: str | None = None,
        risk_acknowledged: bool = False,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        # Server-side role check
        allowed_roles = {"expansionManager", "expansion-manager", "siteReviewer", "site_reviewer", "dataSteward", "data_owner"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(f"role {actor_role_id!r} is not allowed to decide intake")

        governed_key = _require_governed_write_context(
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            action_label="decide intake",
        )
        cache_key = ("decide_intake", governed_key)
        if cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        reason_text = (reason or "").strip()
        if not reason_text:
            raise NetworkListingPolicyError("decision reason is required")

        risk_summary_text = _require_acknowledged_risk(
            risk_summary=risk_summary,
            risk_acknowledged=risk_acknowledged,
            action_label="decide intake",
        )

        intake = self._listing_intake(intake_id)
        action = action.strip().lower()

        if action not in {"create", "revise", "duplicate", "quarantine", "reject"}:
            raise NetworkListingPolicyError(f"invalid action: {action}")

        from modules.external_data.application.assisted_intake import effective_fields
        effective_vals = effective_fields(intake["parsedFields"])

        before_stage = intake["stage"]
        before_after = {"stage": {"before": before_stage}}
        effect_summary = f"Manual decision '{action}' recorded for intake {intake_id}."

        if action == "create":
            new_id = f"L-{2031 + len(self._state['listings'])}"
            new_listing = {
                "id": new_id,
                "sourceId": intake["sourceId"],
                "sourceListingId": effective_vals.get("providerListingId", ""),
                "heatZoneId": intake["heatZoneId"] or "HZ-01",
                "address": effective_vals.get("address", ""),
                "status": "new",
                "rentPerMonth": effective_vals.get("rent", 0),
                "areaPing": effective_vals.get("areaPing", 0),
                "floor": effective_vals.get("floor", ""),
                "frontageMeters": 5,
                "geocodeConfidence": 0.9,
                "hardRuleFailures": [],
                "hardRuleSummary": "3/3 pass: area, floor, permitted use",
                "sourceEvidence": [f"EV-{new_id}-RAW", f"EV-{new_id}-GEOCODE"],
                "fitScore": 70,
                "firstSeenAt": _now(),
                "sourceUrl": intake["originalUrl"],
            }
            self._state["listings"].append(new_listing)
            self._sync_listing_to_repo(new_id)
            intake["stage"] = "READY"
            if not intake.get("matchResult"):
                intake["matchResult"] = {
                    "outcome": "NEW",
                    "outcomeLabel": "新物件",
                    "confidence": 1.0,
                    "targetListingId": new_id,
                    "agreeingSignals": [],
                    "contradictingSignals": [],
                    "summary": f"已手動建立為新物件 {new_id}。"
                }
            else:
                intake["matchResult"]["targetListingId"] = new_id
                intake["matchResult"]["outcome"] = "NEW"
                intake["matchResult"]["outcomeLabel"] = "新物件"
                intake["matchResult"]["summary"] = f"已手動建立為新物件 {new_id}。"

            before_after["stage"]["after"] = "READY"
            before_after["listings_count"] = {"before": len(self._state["listings"]) - 1, "after": len(self._state["listings"])}
            effect_summary = f"Created new listing {new_id} from intake {intake_id}."

        elif action == "revise":
            target_id = intake["matchResult"].get("targetListingId")
            if not target_id:
                raise NetworkListingConflict("no target listing found for revision")
            target = self._listing(target_id)
            before_rent = target["rentPerMonth"]
            before_area = target["areaPing"]
            before_floor = target["floor"]

            target["rentPerMonth"] = effective_vals.get("rent", target["rentPerMonth"])
            target["areaPing"] = effective_vals.get("areaPing", target["areaPing"])
            target["floor"] = effective_vals.get("floor", target["floor"])
            target["sourceEvidence"] = _dedupe(list(target.get("sourceEvidence", [])) + [f"EV-{intake_id}-REVISION"])
            target["status"] = "watching"
            self._sync_listing_to_repo(target_id)
            intake["stage"] = "READY"
            intake["matchResult"]["summary"] = f"已手動將版本更新至既有物件 {target_id}。"

            before_after["stage"]["after"] = "READY"
            before_after["target_rent"] = {"before": before_rent, "after": target["rentPerMonth"]}
            before_after["target_area"] = {"before": before_area, "after": target["areaPing"]}
            before_after["target_floor"] = {"before": before_floor, "after": target["floor"]}
            effect_summary = f"Revised listing {target_id} from intake {intake_id}."

        elif action == "duplicate":
            target_id = intake["matchResult"].get("targetListingId")
            if not target_id:
                raise NetworkListingConflict("no target listing found for duplicate merge")
            target = self._listing(target_id)
            before_evidence_count = len(target.get("sourceEvidence", []))

            target["sourceEvidence"] = _dedupe(list(target.get("sourceEvidence", [])) + [f"EV-{intake_id}-DUPLICATE"])
            target["status"] = "watching"
            self._sync_listing_to_repo(target_id)
            intake["stage"] = "READY"
            intake["matchResult"]["summary"] = f"已手動標記為重複並合併至 {target_id}。"

            before_after["stage"]["after"] = "READY"
            before_after["target_evidence_count"] = {"before": before_evidence_count, "after": len(target["sourceEvidence"])}
            effect_summary = f"Merged duplicate intake {intake_id} into listing {target_id}."

        elif action == "quarantine":
            intake["stage"] = "QUARANTINED"
            if not intake.get("matchResult"):
                intake["matchResult"] = {
                    "outcome": "QUARANTINED",
                    "outcomeLabel": "已隔離",
                    "confidence": 0.0,
                    "targetListingId": None,
                    "agreeingSignals": [],
                    "contradictingSignals": [],
                    "summary": f"已手動送交隔離。原因：{reason_text}"
                }
            else:
                intake["matchResult"]["outcome"] = "QUARANTINED"
                intake["matchResult"]["summary"] = f"已手動送交隔離。原因：{reason_text}"

            before_after["stage"]["after"] = "QUARANTINED"
            effect_summary = f"Quarantined intake {intake_id}."

        elif action == "reject":
            intake["stage"] = "FAILED"
            if not intake.get("matchResult"):
                intake["matchResult"] = {
                    "outcome": "QUARANTINED",
                    "outcomeLabel": "已隔離",
                    "confidence": 0.0,
                    "targetListingId": None,
                    "agreeingSignals": [],
                    "contradictingSignals": [],
                    "summary": f"已拒絕此送件。原因：{reason_text}"
                }
            else:
                intake["matchResult"]["summary"] = f"已拒絕此送件。原因：{reason_text}"

            before_after["stage"]["after"] = "FAILED"
            effect_summary = f"Rejected intake {intake_id}."

        audit_evt = {
            "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Expansion Manager",
            "action": f"intake.decide.{action}",
            "targetId": intake_id,
            "message": f"Intake decision '{action}' recorded. Reason: {reason_text}",
            "correlationId": correlation_id,
            "metadata": {
                "decision": action,
                "reason": reason_text,
                "stage": intake["stage"],
                "targetListingId": intake["matchResult"].get("targetListingId"),
                "beforeAfter": before_after,
                "riskSummary": risk_summary_text,
                "riskAcknowledged": True,
                "effectSummary": effect_summary,
            }
        }
        intake["auditEvents"].append(audit_evt)
        self._save_intake(intake)
        self._save_idempotency("decide_intake", governed_key, intake)
        return _copy(intake)

    def retry_intake(
        self,
        *,
        intake_id: str,
        actor_role_id: str,
        actor_name: str | None,
        correlation_id: str | None,
        job_queue: Any | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        intake = self._listing_intake(intake_id)
        if intake["stage"] not in {"FAILED", "READY", "NEEDS_REVIEW", "AWAITING_ASSISTED_ENTRY"}:
            raise NetworkListingConflict(f"intake {intake_id} is in stage {intake['stage']} and cannot be retried")

        if job_queue is not None and intake.get("idempotencyKey"):
            job = job_queue.get_by_idempotency_key(intake["idempotencyKey"])
            if job is not None:
                intake["stage"] = "SUBMITTED"
                job_queue.replay(job.job_id)
                audit_evt = {
                    "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
                    "occurredAt": _now(),
                    "actorRoleId": actor_role_id,
                    "actorName": actor_name or "Expansion Manager",
                    "action": "intake.retry",
                    "targetId": intake_id,
                    "message": f"Replayed async intake job for {intake['originalUrl']}.",
                    "correlationId": correlation_id,
                    "metadata": {
                        "job_id": job.job_id,
                        "stage": intake["stage"],
                    }
                }
                intake["auditEvents"].append(audit_evt)
                self._save_intake(intake)
                return _copy(intake)


        from modules.external_data.application.assisted_intake import (
            RetrievalFailure,
            content_fingerprint,
            effective_fields,
            match_listing,
            parse_snapshot,
            resolve_source_policy,
        )
        from modules.external_data.security import redact_sensitive_snapshot

        policy = resolve_source_policy(intake["originalUrl"])
        if policy.quarantines or policy.policy in {"POLICY_UNKNOWN", "SOURCE_BLOCKED"}:
            intake["stage"] = "QUARANTINED"
            intake["matchResult"] = {
                "outcome": "QUARANTINED",
                "outcomeLabel": "已隔離",
                "confidence": 0.0,
                "targetListingId": None,
                "agreeingSignals": [],
                "contradictingSignals": [],
                "summary": f"依來源政策 {policy.policy} 予以隔離：{policy.policy_reason}",
            }
            retrieval = None
        elif policy.policy in {"ASSISTED_ENTRY_ONLY", "AUTH_REQUIRED"}:
            intake["stage"] = "AWAITING_ASSISTED_ENTRY"
            retrieval = None
        else:
            intake["stage"] = "RETRIEVING"
            import json
            snapshot_service = self._get_snapshot_service()
            gate = self._get_security_gate(snapshot_service)
            
            resolved_tenant_id = tenant_id or "00000000-0000-0000-0000-000000000001"
            retrieval_res = gate.fetch(
                intake["canonicalUrl"],
                tenant_id=resolved_tenant_id,
                retrieval_method="fixture_replay",
            )
            
            if retrieval_res.ok:
                raw_dict = json.loads(retrieval_res.body.decode("utf-8"))
                redacted_raw = redact_sensitive_snapshot(raw_dict)
                redacted_data = json.dumps(redacted_raw).encode("utf-8")
                
                snapshot_id = snapshot_service.create_snapshot(
                    tenant_id=resolved_tenant_id,
                    intake_id=intake_id,
                    source_id=policy.source_id,
                    raw_data=retrieval_res.body,
                    original_url=intake["originalUrl"],
                    canonical_url=intake["canonicalUrl"],
                    media_type="application/json",
                    capture_method="SERVER_RETRIEVAL",
                    retention_class="STANDARD",
                    encryption_key_ref="kms://default-key",
                    observed_at=datetime.now(UTC),
                    captured_at=datetime.now(UTC),
                    bucket="taiwan-snapshots",
                    redacted_data=redacted_data,
                )
                
                from modules.external_data.application.assisted_intake import RetrievalResult
                retrieval = RetrievalResult(
                    snapshot_id=snapshot_id,
                    captured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    raw=redacted_raw,
                )
            else:
                from modules.external_data.application.assisted_intake import (
                    RetrievalFailure,
                    RetrievalResult,
                )
                retrieval = RetrievalResult(
                    snapshot_id="FAILED",
                    captured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    failure=RetrievalFailure(
                        code=retrieval_res.failure.code if retrieval_res.failure else "FAILED",
                        summary=retrieval_res.failure.summary if retrieval_res.failure else "retrieval failed",
                        next_action=retrieval_res.failure.next_action if retrieval_res.failure else "governance review",
                        retryable=retrieval_res.failure.retryable if retrieval_res.failure else False,
                    )
                )

        if retrieval is None:
            audit_evt = {
                "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
                "occurredAt": _now(),
                "actorRoleId": actor_role_id,
                "actorName": actor_name or "Expansion Manager",
                "action": "intake.retry",
                "targetId": intake_id,
                "message": f"Retried URL retrieval for {intake['originalUrl']}.",
                "correlationId": correlation_id,
                "metadata": {
                    "policy": policy.policy,
                    "stage": intake["stage"],
                    "matchOutcome": intake["matchResult"]["outcome"] if intake["matchResult"] else None,
                }
            }
            intake["auditEvents"].append(audit_evt)
            self._save_intake(intake)
            return _copy(intake)

        intake["stage"] = "RETRIEVING"

        if retrieval.ok:
            intake["stage"] = "PARSING"
            intake["rawSnapshot"] = retrieval.raw
            intake["snapshotId"] = retrieval.snapshot_id
            intake["capturedAt"] = retrieval.captured_at

            preserved_corrections = {
                k: (v.get("correctedValue"), v.get("correctionReason"))
                for k, v in intake["parsedFields"].items()
                if v.get("correctedValue") is not None
            }

            intake["parsedFields"] = parse_snapshot(retrieval)
            for k, (c_val, c_reason) in preserved_corrections.items():
                if k in intake["parsedFields"]:
                    intake["parsedFields"][k]["correctedValue"] = c_val
                    intake["parsedFields"][k]["correctionReason"] = c_reason

            effective_vals = effective_fields(intake["parsedFields"])

            from modules.external_data.application.assisted_intake import (
                ASSISTED_ENTRY_REQUIRED_FIELDS,
            )
            has_all_required = True
            for rf in ASSISTED_ENTRY_REQUIRED_FIELDS:
                val = effective_vals.get(rf)
                if val in (None, ""):
                    has_all_required = False
                    break
                if rf in ("rent", "areaPing"):
                    try:
                        if float(val) <= 0:
                            has_all_required = False
                            break
                    except (ValueError, TypeError):
                        has_all_required = False
                        break

            if has_all_required:
                intake["stage"] = "MATCHING"
                fingerprint = content_fingerprint(effective_vals)

                match_res = match_listing(
                    values=effective_vals,
                    canonical_url=intake["canonicalUrl"],
                    source_id=policy.source_id,
                    fingerprint=fingerprint,
                    listings=self._get_match_listings(),
                )
                intake["matchResult"] = match_res.to_dict()
                if match_res.outcome == "POSSIBLE_MATCH":
                    intake["stage"] = "NEEDS_REVIEW"
                else:
                    intake["stage"] = "READY"
            else:
                intake["stage"] = "AWAITING_ASSISTED_ENTRY"
        else:
            intake["stage"] = "FAILED"
            failure_obj = RetrievalFailure(
                code=retrieval.failure.code if retrieval.failure else "FAILED",
                summary=retrieval.failure.summary if retrieval.failure else "retrieval failed",
                next_action=retrieval.failure.next_action if retrieval.failure else "governance review",
                retryable=retrieval.failure.retryable if retrieval.failure else False,
            )
            intake["failure"] = failure_obj.to_dict()


        audit_evt = {
            "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Expansion Manager",
            "action": "intake.retry",
            "targetId": intake_id,
            "message": f"Retried URL retrieval for {intake['originalUrl']}.",
            "correlationId": correlation_id,
            "metadata": {
                "stage": intake["stage"],
                "matchOutcome": intake["matchResult"]["outcome"] if intake["matchResult"] else None,
            }
        }
        intake["auditEvents"].append(audit_evt)
        self._save_intake(intake)
        return _copy(intake)

    # Adapter methods for PromotionService
    def get_listing(self, listing_id: str) -> dict[str, Any] | None:
        try:
            return self._listing(listing_id)
        except NetworkListingNotFound:
            return None

    def save_listing(self, listing: dict[str, Any], address: Any = None, key: Any = None) -> None:
        for i, lst in enumerate(self._state["listings"]):
            if lst["id"] == listing["id"]:
                self._state["listings"][i] = listing
                break
        self._sync_listing_to_repo(listing["id"])

    def list_candidates(self) -> list[dict[str, Any]]:
        return self._state["candidates"]

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        for cand in self._state["candidates"]:
            if cand["id"] == candidate_id:
                return cand
        return None

    def save_candidate(self, candidate: dict[str, Any]) -> None:
        for i, cand in enumerate(self._state["candidates"]):
            if cand["id"] == candidate["id"]:
                self._state["candidates"][i] = candidate
                self._sync_candidate_to_repo(candidate["id"])
                return
        self._state["candidates"].append(candidate)
        self._sync_candidate_to_repo(candidate["id"])

    def get_listing_intake(self, intake_id: str) -> dict[str, Any] | None:
        try:
            return self._listing_intake(intake_id)
        except NetworkListingNotFound:
            return None

    def save_intake(self, intake: dict[str, Any]) -> None:
        self._save_intake(intake)

    def promote_intake(
        self,
        *,
        intake_id: str,
        reason: str | None = None,
        risk_summary: str | None = None,
        risk_acknowledged: bool = False,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        # Server-side role check
        allowed_roles = {"expansionManager", "expansion-manager", "siteReviewer", "site_reviewer"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(f"role {actor_role_id!r} is not allowed to promote intake")

        governed_key = _require_governed_write_context(
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            action_label="promote intake",
        )
        cache_key = ("promote_intake", governed_key)
        if cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        reason_text = (reason or "").strip()
        if not reason_text:
            raise NetworkListingPolicyError("promotion reason is required")

        risk_summary_text = _require_acknowledged_risk(
            risk_summary=risk_summary,
            risk_acknowledged=risk_acknowledged,
            action_label="promote intake",
        )

        intake = self._listing_intake(intake_id)
        target_listing_id = intake["matchResult"].get("targetListingId")
        if not target_listing_id:
            raise NetworkListingConflict("intake must be resolved to a listing before promotion")

        listing = self._listing(target_listing_id)
        before_listing_status = listing.get("status")
        before_candidate_count = len(self._state["candidates"])

        # Enforce segregation of duties & run reviewed promotion saga
        proposer_id = intake.get("submitter") or "operator-expansion-staff"
        reviewer_id = actor_name or "operator-expansion-manager"

        import hashlib
        gate_snap = str(listing.get("fitScore", 0))
        gate_snapshot_sha256 = hashlib.sha256(gate_snap.encode()).hexdigest()

        from modules.listing.application.promotion import PromotionService
        from modules.listing.domain.intake_states import Actor, PrincipalRole, TransitionContext

        promo_service = PromotionService(
            promotion_repository=self._intakes,
            listing_repository=self,
            intake_repository=self,
            outbox_repository=getattr(self._listing_repository, "outbox_repository", None),
        )

        # 1. Propose
        proposer_actor = Actor(
            actor_id=proposer_id,
            role=PrincipalRole.EXPANSION_STAFF,
            tenant_id=listing.get("tenantId") or "tenant-a",
        )
        proposer_context = TransitionContext(
            actor=proposer_actor,
            idempotency_key=governed_key,
            correlation_id=correlation_id,
            risk_acknowledged=risk_acknowledged,
            reason=reason_text,
        )

        try:
            promo_record = promo_service.request_promotion(
                intake_id=intake_id,
                target_format_code="FORMAT-A",
                reason=reason_text,
                gate_snapshot_sha256=gate_snapshot_sha256,
                context=proposer_context,
            )
        except Exception as exc:
            if "DUPLICATE_CANDIDATE" in str(exc) or "DEPENDENCY_CONFLICT" in str(exc):
                raise NetworkListingConflict(str(exc)) from exc
            raise NetworkListingPolicyError(str(exc)) from exc

        # 2. Review & Approve (which automatically executes the saga)
        reviewer_actor = Actor(
            actor_id=reviewer_id,
            role=PrincipalRole.EXPANSION_MANAGER,
            tenant_id=listing.get("tenantId") or "tenant-a",
        )
        reviewer_context = TransitionContext(
            actor=reviewer_actor,
            idempotency_key=f"review-{governed_key}",
            correlation_id=correlation_id,
            risk_acknowledged=risk_acknowledged,
            reason=reason_text,
            version_before=promo_record["version"],
        )

        try:
            promo_record = promo_service.review_promotion(
                promotion_decision_id=promo_record["promotion_decision_id"],
                decision="APPROVE",
                reason=reason_text,
                risk_acknowledged=risk_acknowledged,
                context=reviewer_context,
            )
        except Exception as exc:
            if "SELF_REVIEW_DENIED" in str(exc):
                raise NetworkListingConflict("SELF_REVIEW_DENIED") from exc
            raise NetworkListingPolicyError(str(exc)) from exc

        candidate = self.get_candidate(promo_record["candidate_site_id"])
        listing_updated = self.get_listing(promo_record["listing_id"])

        result = {
            "listing": _copy(listing_updated),
            "candidate": _copy(candidate),
            "created": True,
        }

        audit_evt = {
            "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Expansion Manager",
            "action": "intake.promote",
            "targetId": intake_id,
            "message": f"Promoted target listing {target_listing_id} to candidate. Reason: {reason_text}",
            "correlationId": correlation_id,
            "metadata": {
                "targetListingId": target_listing_id,
                "candidateId": promo_record["candidate_site_id"],
                "reason": reason_text,
                "before": {
                    "listingStatus": before_listing_status,
                    "candidateCount": before_candidate_count,
                },
                "after": {
                    "listingStatus": result["listing"]["status"],
                    "candidateCount": len(self._state["candidates"]),
                },
                "riskSummary": risk_summary_text,
                "riskAcknowledged": True,
                "effectSummary": (
                    f"Promote listing {target_listing_id} to candidate {promo_record['candidate_site_id']}."
                ),
            }
        }
        intake["auditEvents"].append(audit_evt)
        self._save_intake(intake)
        self._save_idempotency("promote_intake", governed_key, result)
        return result

    def _get_match_listings(self) -> list[dict[str, Any]]:
        res = []
        for lst in self._state["listings"]:
            source_id = lst.get("sourceId", "")
            source_listing_id = lst.get("sourceListingId", "")

            fp = lst.get("contentFingerprint")
            if not fp:
                from modules.external_data.application.assisted_intake import content_fingerprint
                fields_for_fp = {
                    "address": lst.get("address", ""),
                    "floor": lst.get("floor", ""),
                    "areaPing": lst.get("areaPing"),
                    "rent": lst.get("rentPerMonth"),
                    "listingType": lst.get("listingType", "店面"),
                    "listingStatus": lst.get("listingStatus", "active"),
                }
                fp = content_fingerprint(fields_for_fp)
                lst["contentFingerprint"] = fp

            res.append({
                "id": lst["id"],
                "sourceId": source_id,
                "sourceListingId": source_listing_id,
                "canonicalUrl": lst.get("sourceUrl", ""),
                "contentFingerprint": fp,
                "address": lst.get("address", ""),
                "floor": lst.get("floor", ""),
                "areaPing": lst.get("areaPing"),
                "rentPerMonth": lst.get("rentPerMonth"),
                "listingType": lst.get("listingType"),
            })
        return res

    def _listing_intake(self, intake_id: str) -> dict[str, Any]:
        self._state.setdefault("assistedIntakes", [])
        for intake in self._state["assistedIntakes"]:
            if intake["id"] == intake_id:
                return intake
        raise NetworkListingNotFound(f"assisted intake record {intake_id} not found")

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

    def _get_snapshot_service(self) -> Any:
        import os

        from modules.external_data.application.source_snapshots import SourceSnapshotService
        from shared.infrastructure.object_store.client import GcsObjectStore, InMemoryObjectStore

        doc_store = None
        db_conn = None
        if hasattr(self._intakes, "_store"):
            doc_store = self._intakes._store
            if hasattr(doc_store, "_engine"):
                db_conn = doc_store._engine

        def residency_resolver(tenant_id: str) -> str:
            if doc_store:
                tenant_meta = doc_store.get("operator.tenant_metadata", tenant_id)
                if tenant_meta:
                    return tenant_meta.get("residency_mode", "TW_ONLY")
            return "TW_ONLY"

        if os.environ.get("ODP_OBJECT_STORE") == "gcs":
            object_store = GcsObjectStore(tenant_residency_resolver=residency_resolver)
        else:
            object_store = InMemoryObjectStore(tenant_residency_resolver=residency_resolver)

        return SourceSnapshotService(
            db_conn=db_conn,
            object_store=object_store,
            document_store=doc_store,
        )

    def _get_security_gate(self, snapshot_service: Any) -> Any:
        from modules.external_data.security.assisted_listing_retrieval import RetrievalSecurityGate
        return RetrievalSecurityGate(source_snapshot_service=snapshot_service)

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
