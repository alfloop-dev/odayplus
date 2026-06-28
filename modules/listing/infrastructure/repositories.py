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
        self.listings.append(listing)
        self.addresses.append(address)
        self.source_keys.add(key.source_key)
        self.property_keys.add(key.property_key)

    def save_candidate(self, candidate: CandidateSiteDraft) -> None:
        self.candidates.append(candidate)

    def list_candidates(self) -> list[CandidateSiteDraft]:
        return list(self.candidates)
