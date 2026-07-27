from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from shared.domain import AddressLocation, CandidateSite, Listing


class ListingPipelineStatus(StrEnum):
    RAW = "RAW"
    PARSED = "PARSED"
    GEOCODED = "GEOCODED"
    DUPLICATE = "DUPLICATE"
    FAILED_HARD_RULE = "FAILED_HARD_RULE"
    CANDIDATE = "CANDIDATE"


@dataclass(frozen=True)
class ListingHardRulePolicy:
    """Deterministic ODay G2 feasibility gate for listing-to-candidate conversion."""

    target_format_code: str = "ODAY_G2"
    min_area_ping: float = 18.0
    max_area_ping: float = 80.0
    max_monthly_rent: float = 250_000.0
    max_rent_per_ping: float = 3_500.0
    min_geocode_confidence: float = 0.7
    allowed_listing_statuses: tuple[str, ...] = ("active",)
    disallowed_floor_tokens: tuple[str, ...] = ("B", "地下", "2F", "3F", "4F", "5F")

    def evaluate(self, listing: Listing, address: AddressLocation) -> tuple[str, ...]:
        failures: list[str] = []
        if listing.listing_status not in self.allowed_listing_statuses:
            failures.append("listing_not_active")
        if listing.rent_amount <= 0:
            failures.append("missing_or_invalid_rent")
        if listing.area_ping <= 0:
            failures.append("missing_or_invalid_area")
        if listing.area_ping and listing.area_ping < self.min_area_ping:
            failures.append("area_below_format_minimum")
        if listing.area_ping > self.max_area_ping:
            failures.append("area_above_format_maximum")
        if listing.rent_amount > self.max_monthly_rent:
            failures.append("rent_above_format_maximum")
        if listing.area_ping > 0 and listing.rent_amount / listing.area_ping > self.max_rent_per_ping:
            failures.append("rent_per_ping_above_format_maximum")
        if address.geocode_confidence < self.min_geocode_confidence:
            failures.append("low_geocode_confidence")
        if not address.h3_res_9:
            failures.append("missing_h3_index")
        floor = listing.floor.upper()
        if any(token.upper() in floor for token in self.disallowed_floor_tokens):
            failures.append("floor_not_ground_level")
        return tuple(failures)


@dataclass
class CandidateSiteDraft:
    listing: Listing
    address: AddressLocation
    candidate_site: CandidateSite
    feasibility_flags: tuple[str, ...] = ()
    heat_zone_id: str = ""
    listing_source: str = ""
    status: ListingPipelineStatus = ListingPipelineStatus.CANDIDATE
    score: int = 68
    recommendation: str = "WAIT"
    model_version: str = "SiteScore v2.3"
    dataset_snapshot_id: str = "FS-20260704-0600"
    review_id: str | None = None

    def to_card_dict(self) -> dict[str, object]:
        return {
            "candidateSiteId": self.candidate_site.candidate_site_id,
            "address": self.address.normalized_address or self.address.raw_address,
            "geocodeConfidence": self.address.geocode_confidence,
            "rent": self.listing.rent_amount,
            "area": self.listing.area_ping,
            "frontage": self.listing.frontage_m,
            "floor": self.listing.floor,
            "parkingOrTemporaryStop": self.listing.parking_flag,
            "feasibilityFlags": list(self.feasibility_flags),
            "heatZone": self.heat_zone_id,
            "listingSource": self.listing_source,
            "status": getattr(self.status, "value", self.status),
        }


@dataclass(frozen=True)
class ListingDedupKey:
    source_id: str
    source_listing_id: str
    normalized_address: str
    rent_amount: float
    area_ping: float

    @property
    def source_key(self) -> str:
        return f"{self.source_id.strip().lower()}:{self.source_listing_id.strip().lower()}"

    @property
    def property_key(self) -> str:
        rent_bucket = round(self.rent_amount, 2)
        area_bucket = round(self.area_ping, 2)
        return f"{self.normalized_address.strip().lower()}|{rent_bucket}|{area_bucket}"


@dataclass(frozen=True)
class ListingIssue:
    code: str
    message: str
    field: str = ""


@dataclass(frozen=True)
class ListingDuplicateGroup:
    duplicate_group_id: str
    match_strategy: str
    confidence: float
    duplicate_key: str
    manual_actions: tuple[str, ...] = ("merge", "split")


@dataclass(frozen=True)
class ListingProcessingState:
    status: ListingPipelineStatus = ListingPipelineStatus.RAW
    issues: tuple[ListingIssue, ...] = field(default_factory=tuple)
