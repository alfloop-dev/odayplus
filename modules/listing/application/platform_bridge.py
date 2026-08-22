"""Assisted Listing Platform Bridge.

Contract: `odayplus.assisted-listing-platform-bridge.v2`.
Part of Task: `ODP-LISTING-001`.

This module bridges external platform property observations (from `alfloop-dev/oday-data-platform`
via `emgi.property-observation.v1` and `MarketDataFacade`) with `odayplus`'s human-assisted listing
intake, spreadsheet import, identity resolution, and candidate site promotion.

Architectural Invariants (ODP-LISTING-001):
1. Single Listing Authority: `odayplus` is the sole authoritative master for listing records,
   human review decisions, manual corrections, and candidate promotions. Platform property
   observations are evidentiary inputs, not a competing listing authority.
   Never create a second listing master.
2. Zero Direct Ingestion Leakage: Platform observations are consumed via `MarketDataFacade`
   (`odayplus.market-data-facade.v2`) or versioned client models. No raw sockets or unapproved crawling.
3. Preserve Human Control: Manual corrections to identity fields always override normalized intake
   and raw platform observation values. Field precedence:
   `Manual Correction > Normalized Intake > Platform Observation Raw`.
4. Tenant Isolation & Product Authorization: Forward tenant scope and caller Principal on all facade queries.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from modules.external_data.application.assisted_intake import (
    normalize_address,
)
from modules.external_data.application.market_data_facade import (
    MarketDataFacade,
    MarketDataNotFoundError,
)
from modules.listing.domain.identity_graph import (
    IdentityGraph,
    SourceIdentity,
    SourceIdentityEdge,
)
from modules.listing.domain.models import CandidateSiteDraft
from packages.oday_data_product_contracts_client.models.property_observation import (
    ListingStatus,
    ListingStatusHistory,
    PropertyEntity,
    PropertyListingObservation,
    PropertyObservationDocument,
    RentBenchmark,
)
from shared.auth import Principal

BRIDGE_CONTRACT = "odayplus.assisted-listing-platform-bridge.v2"
BRIDGE_VERSION = "2.0.0"


@dataclass(frozen=True)
class ObservationEnrichment:
    """Evidentiary context from EMGI platform property observations attached to a listing or intake."""

    property_entity: PropertyEntity | None = None
    listing_observation: PropertyListingObservation | None = None
    rent_benchmark: RentBenchmark | None = None
    status_history: ListingStatusHistory | None = None
    observation_document: PropertyObservationDocument | None = None
    source_url: str | None = None
    is_evidentiary: bool = True
    reconciled_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def property_id(self) -> str | None:
        if self.property_entity:
            return self.property_entity.property_id
        if self.listing_observation:
            return self.listing_observation.property_id
        return None

    @property
    def listing_obs_id(self) -> str | None:
        if self.listing_observation:
            return self.listing_observation.listing_obs_id
        return None

    @property
    def median_rent_per_ping(self) -> float | None:
        if self.rent_benchmark and self.rent_benchmark.median_rent_per_ping is not None:
            return float(self.rent_benchmark.median_rent_per_ping)
        return None

    @property
    def p25_rent_per_ping(self) -> float | None:
        if self.rent_benchmark and self.rent_benchmark.p25_rent_per_ping is not None:
            return float(self.rent_benchmark.p25_rent_per_ping)
        return None

    @property
    def p75_rent_per_ping(self) -> float | None:
        if self.rent_benchmark and self.rent_benchmark.p75_rent_per_ping is not None:
            return float(self.rent_benchmark.p75_rent_per_ping)
        return None

    @property
    def sample_count(self) -> int | None:
        if self.rent_benchmark:
            return self.rent_benchmark.sample_count
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": BRIDGE_CONTRACT,
            "version": BRIDGE_VERSION,
            "property_id": self.property_id,
            "listing_obs_id": self.listing_obs_id,
            "property_entity": self.property_entity.to_dict() if self.property_entity else None,
            "listing_observation": self.listing_observation.to_dict() if self.listing_observation else None,
            "rent_benchmark": self.rent_benchmark.to_dict() if self.rent_benchmark else None,
            "status_history": self.status_history.to_dict() if self.status_history else None,
            "is_evidentiary": self.is_evidentiary,
            "reconciled_at": self.reconciled_at.isoformat(),
        }


class AssistedListingPlatformBridge:
    """Bridge service connecting EMGI Property Observations to odayplus Listing Domain.

    Contract: `odayplus.assisted-listing-platform-bridge.v2`.
    """

    def __init__(
        self,
        facade: MarketDataFacade | None = None,
        identity_graph: IdentityGraph | None = None,
    ) -> None:
        self._facade = facade
        self._identity_graph = identity_graph or IdentityGraph()

    @property
    def contract(self) -> str:
        return BRIDGE_CONTRACT

    @property
    def version(self) -> str:
        return BRIDGE_VERSION

    @property
    def facade(self) -> MarketDataFacade | None:
        return self._facade

    @property
    def identity_graph(self) -> IdentityGraph:
        return self._identity_graph

    def reconcile_observation_for_listing(
        self,
        *,
        listing_id: str | None = None,
        property_id: str | None = None,
        canonical_url: str | None = None,
        source_listing_id: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> ObservationEnrichment | None:
        """Fetch and reconcile platform property observations for a listing or URL.

        If `MarketDataFacade` is not configured, returns None gracefully.
        """
        if self._facade is None:
            return None

        prop_entity: PropertyEntity | None = None
        listing_obs: PropertyListingObservation | None = None
        status_hist: ListingStatusHistory | None = None
        obs_doc: PropertyObservationDocument | None = None

        # 1. Try querying document by document_id or property_id or listing_id
        target_listing_query = listing_id or source_listing_id
        try:
            obs_doc = self._facade.get_property_observation_document(
                property_id=property_id,
                listing_id=target_listing_query,
                tenant_id=tenant_id,
                principal=principal,
            )
            if obs_doc:
                if obs_doc.properties:
                    prop_entity = obs_doc.properties[0]
                if obs_doc.listing_observations:
                    listing_obs = obs_doc.listing_observations[0]
                if obs_doc.status_histories:
                    status_hist = obs_doc.status_histories[0]
        except MarketDataNotFoundError:
            pass

        # 2. If listing observation not found from doc, try explicit lookup
        if listing_obs is None and target_listing_query:
            try:
                listing_obs = self._facade.get_listing_observation(
                    target_listing_query,
                    tenant_id=tenant_id,
                    principal=principal,
                )
            except MarketDataNotFoundError:
                pass

        # 3. If property entity not found from doc, try explicit lookup
        target_prop_id = property_id or (listing_obs.property_id if listing_obs else None)
        if prop_entity is None and target_prop_id:
            try:
                prop_entity = self._facade.get_property_entity(
                    target_prop_id,
                    tenant_id=tenant_id,
                    principal=principal,
                )
            except MarketDataNotFoundError:
                pass

        if prop_entity is None and listing_obs is None and obs_doc is None:
            return None

        return ObservationEnrichment(
            property_entity=prop_entity,
            listing_observation=listing_obs,
            status_history=status_hist,
            observation_document=obs_doc,
            source_url=canonical_url,
            is_evidentiary=True,
        )

    def reconcile_and_bind_identity(
        self,
        tenant_id: str,
        listing_id: str,
        property_id: str,
        *,
        source_id: str = "platform.property_observation",
        source_entity_id: str | None = None,
        match_strategy: str = "platform_observation",
        confidence: float = 0.95,
        expected_version: int | None = None,
    ) -> SourceIdentityEdge:
        """Bind an evidentiary platform property observation to an odayplus property identity in IdentityGraph."""
        self._identity_graph.add_property(tenant_id, property_id)
        source = SourceIdentity(
            tenant_id=tenant_id,
            source_id=source_id,
            source_entity_id=source_entity_id or listing_id,
        )
        return self._identity_graph.bind_source(
            source=source,
            property_id=property_id,
            listing_id=listing_id,
            match_strategy=match_strategy,
            confidence=confidence,
            expected_version=expected_version,
        )

    def enrich_candidate_site(
        self,
        draft: CandidateSiteDraft,
        enrichment: ObservationEnrichment,
    ) -> CandidateSiteDraft:
        """Enrich a candidate site draft with platform observation metadata and rent benchmarks."""
        if enrichment.property_id:
            draft.property_entity_id = enrichment.property_id
        if enrichment.listing_obs_id:
            draft.listing_observation_id = enrichment.listing_obs_id
        if enrichment.median_rent_per_ping is not None:
            draft.rent_benchmark_median = enrichment.median_rent_per_ping
        if enrichment.p25_rent_per_ping is not None:
            draft.rent_benchmark_p25 = enrichment.p25_rent_per_ping
        if enrichment.p75_rent_per_ping is not None:
            draft.rent_benchmark_p75 = enrichment.p75_rent_per_ping
        if enrichment.sample_count is not None:
            draft.rent_benchmark_sample_count = enrichment.sample_count
        if enrichment.rent_benchmark and enrichment.rent_benchmark.benchmark_id:
            draft.rent_benchmark_id = enrichment.rent_benchmark.benchmark_id

        draft.observation_metadata = {
            "bridge_contract": BRIDGE_CONTRACT,
            "bridge_version": BRIDGE_VERSION,
            "is_evidentiary": enrichment.is_evidentiary,
            "reconciled_at": enrichment.reconciled_at.isoformat(),
        }
        return draft

    def create_evidentiary_intake_payload(
        self,
        observation: PropertyListingObservation,
        *,
        property_entity: PropertyEntity | None = None,
        benchmark: RentBenchmark | None = None,
        source_id: str = "platform.property_observation",
        tenant_id: str = "tenant-default",
    ) -> dict[str, Any]:
        """Convert a platform observation into a standard odayplus intake payload.

        Maintains Single Listing Authority: this produces an evidentiary intake record
        subject to normal odayplus intake validation, review, and promotion.
        """
        raw_address = (
            property_entity.normalized_address
            if property_entity
            else f"{observation.property_id}"
        )
        rent_amount = (
            float(observation.monthly_rent)
            if observation.monthly_rent is not None
            else 0.0
        )
        area_ping = (
            float(observation.floor_area_ping)
            if observation.floor_area_ping is not None
            else 0.0
        )
        floor = observation.target_floor or "1F"

        payload: dict[str, Any] = {
            "source_listing_id": observation.source_listing_id or observation.listing_obs_id,
            "source_id": source_id,
            "address_raw": raw_address,
            "rent_amount": rent_amount,
            "area_ping": area_ping,
            "floor": floor,
            "listing_status": "active" if observation.listing_status == ListingStatus.ACTIVE else str(observation.listing_status.value).lower(),
            "currency": "TWD",
            "is_evidentiary": True,
            "platform_property_id": observation.property_id,
            "platform_observation_id": observation.listing_obs_id,
            "metadata": {
                "channel": observation.channel,
                "first_seen_at": observation.first_seen_at,
                "last_seen_at": observation.last_seen_at,
                "observed_at": observation.observed_at,
                "tenant_id": tenant_id,
            },
        }

        if benchmark is not None:
            payload["rent_benchmark"] = {
                "benchmark_id": benchmark.benchmark_id,
                "median_rent_per_ping": float(benchmark.median_rent_per_ping) if benchmark.median_rent_per_ping is not None else None,
                "p25_rent_per_ping": float(benchmark.p25_rent_per_ping) if benchmark.p25_rent_per_ping is not None else None,
                "p75_rent_per_ping": float(benchmark.p75_rent_per_ping) if benchmark.p75_rent_per_ping is not None else None,
                "sample_count": benchmark.sample_count,
            }

        return payload

    def match_xlsx_row_with_property_entity(
        self,
        row: Mapping[str, Any],
        property_entity: PropertyEntity,
        *,
        min_confidence: float = 0.85,
    ) -> tuple[bool, float, str]:
        """Check if an XLSX row matches a platform PropertyEntity.

        Requires normalized address similarity or exact match >= min_confidence (0.85).
        """
        row_address = str(row.get("address_raw") or row.get("address") or "").strip()
        if not row_address:
            return False, 0.0, "Empty address"

        norm_row = normalize_address(row_address)
        norm_entity = normalize_address(property_entity.normalized_address)

        if norm_row == norm_entity:
            return True, 1.0, f"Exact normalized address match with {property_entity.property_id}"

        # Substring / partial match
        if norm_row in norm_entity or norm_entity in norm_row:
            similarity = min(len(norm_row), len(norm_entity)) / max(len(norm_row), len(norm_entity))
            if similarity >= min_confidence:
                return True, similarity, f"High confidence ({similarity:.2f}) address match with {property_entity.property_id}"

        return False, 0.0, f"Address mismatch ({norm_row} vs {norm_entity})"


__all__ = [
    "AssistedListingPlatformBridge",
    "BRIDGE_CONTRACT",
    "BRIDGE_VERSION",
    "ObservationEnrichment",
]
