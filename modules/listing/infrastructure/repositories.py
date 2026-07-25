from __future__ import annotations

from dataclasses import dataclass, field

from modules.listing.domain.models import CandidateSiteDraft, ListingDedupKey
from shared.domain import AddressLocation, Listing


@dataclass
class InMemoryListingRepository:
    listings: list[Listing] = field(default_factory=list)
    addresses: list[AddressLocation] = field(default_factory=list)
    candidates: list[CandidateSiteDraft] = field(default_factory=list)
    source_keys: set[str] = field(default_factory=set)
    property_keys: set[str] = field(default_factory=set)

    def has_duplicate(self, key: ListingDedupKey) -> bool:
        return key.source_key in self.source_keys or key.property_key in self.property_keys

    def save_listing(self, listing: Listing, address: AddressLocation, key: ListingDedupKey) -> None:
        for idx, lst in enumerate(self.listings):
            if lst.listing_id == listing.listing_id:
                self.listings[idx] = listing
                for a_idx, addr in enumerate(self.addresses):
                    if addr.address_id == address.address_id:
                        self.addresses[a_idx] = address
                return
        self.listings.append(listing)
        self.addresses.append(address)
        self.source_keys.add(key.source_key)
        self.property_keys.add(key.property_key)

    def save_candidate(self, candidate: CandidateSiteDraft) -> None:
        for idx, cand in enumerate(self.candidates):
            if cand.candidate_site.candidate_site_id == candidate.candidate_site.candidate_site_id:
                self.candidates[idx] = candidate
                return
        self.candidates.append(candidate)

    def list_candidates(self) -> list[CandidateSiteDraft]:
        return list(self.candidates)

    def list_listings(self) -> list[Listing]:
        return list(self.listings)

    def get_listing(self, listing_id: str) -> Listing | None:
        for lst in self.listings:
            if lst.listing_id == listing_id:
                return lst
        return None

    def get_address(self, address_id: str) -> AddressLocation | None:
        for address in self.addresses:
            if address.address_id == address_id:
                return address
        return None

    def clear(self) -> None:
        self.listings.clear()
        self.addresses.clear()
        self.candidates.clear()
        self.source_keys.clear()
        self.property_keys.clear()
