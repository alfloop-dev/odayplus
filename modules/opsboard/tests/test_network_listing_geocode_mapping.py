"""Geocode-confidence mapping for the operator network-listing dict layer.

`NetworkListingService` is the second adapter that feeds `PromotionService`
(`promote_intake` wires `listing_repository=self`), so its dict layer sits in
front of the same candidate gate as `V1ListingRepositoryAdapter`. It carried
the same fail-open: `_listing_to_dict` seeded `geocodeConfidence` from
`lst.confidence`, the listing's *extraction* confidence, so a listing whose
address record could not be resolved reported a fully-confident geocode.

ODP-BR-LST-001 is a Hard Constraint -- no address or failed geocode may enter
SiteScore -- and a gate is only as good as the field it is handed. See
`modules/listing/tests/test_promotion_candidate_gate.py` for the gate itself.
"""

from __future__ import annotations

from modules.listing.domain.models import ListingDedupKey
from modules.listing.infrastructure.repositories import InMemoryListingRepository
from modules.opsboard.application.network_listings import NetworkListingService
from shared.domain import AddressLocation, Listing

LISTING_ID = "L-GEOCODE-MAP"
ADDRESS_ID = "ADDR-GEOCODE-MAP"


def _repository(geocode_confidence: float) -> InMemoryListingRepository:
    repo = InMemoryListingRepository()
    listing = Listing(
        listing_id=LISTING_ID,
        source_listing_id="s591-geocode-map",
        source_id="SRC-591",
        listing_status="active",
        address_id=ADDRESS_ID,
        rent_amount=58000.0,
        area_ping=18.0,
        floor="1F",
        # Full extraction confidence: the value that must not be reused.
        confidence=1.0,
    )
    address = AddressLocation(
        address_id=ADDRESS_ID,
        raw_address="台北市信義區松仁路 96 號 1F",
        normalized_address="台北市信義區松仁路96號1F",
        geocode_confidence=geocode_confidence,
        h3_res_9="892a100d2d7ffff",
    )
    repo.save_listing(
        listing,
        address,
        ListingDedupKey(
            source_id=listing.source_id,
            source_listing_id=listing.source_listing_id,
            normalized_address=address.normalized_address,
            rent_amount=listing.rent_amount,
            area_ping=listing.area_ping,
        ),
    )
    return repo


def _service(repo: InMemoryListingRepository) -> NetworkListingService:
    return NetworkListingService(listing_repository=repo, seed_fixtures=False)


class TestGeocodeConfidenceComesFromTheAddress:
    def test_persisted_geocode_confidence_is_carried_through(self) -> None:
        repo = _repository(geocode_confidence=0.94)
        listing = repo.get_listing(LISTING_ID)

        assert _service(repo)._listing_to_dict(listing)["geocodeConfidence"] == 0.94

    def test_unresolvable_address_reports_no_geocode_confidence(self) -> None:
        """The fail-open: this used to report 1.0 from the listing record.

        A listing whose address row is gone has no geocode evidence at all, so
        the dict layer reports 0.0 -- what `AddressLocation` itself defaults to
        and what the promotion gate reads as absence.
        """
        repo = _repository(geocode_confidence=0.94)
        listing = repo.get_listing(LISTING_ID)
        repo.addresses.clear()

        assert _service(repo)._listing_to_dict(listing)["geocodeConfidence"] == 0.0

    def test_listing_confidence_is_still_reported_under_its_own_key(self) -> None:
        """Both values survive; they just stop being interchangeable."""
        repo = _repository(geocode_confidence=0.94)
        listing = repo.get_listing(LISTING_ID)
        repo.addresses.clear()

        as_dict = _service(repo)._listing_to_dict(listing)

        assert as_dict["listingConfidence"] == 1.0
        assert as_dict["geocodeConfidence"] == 0.0
