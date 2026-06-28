"""External Data Platform connectors.

Each connector lands one external dataset, runs the data-quality gate, and emits
typed canonical entities with geocode / H3 enrichment and a preserved lineage
envelope (ODP-DATA-03 §6/§9, ODP-DATA-05 §6). They compose three existing
pieces: the contract engine (DQ + quarantine reasons), the geo pipeline
(geocode / H3), and deterministic identity resolution (stable canonical ids).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from modules.external_data.application.external_contracts import external_contract
from modules.external_data.geo import (
    GeocodeResult,
    GeoPipeline,
    StaticGeocodeProvider,
    build_geo_cell,
    coordinates_in_market,
    stable_h3_index,
)
from modules.integration.application.identity_resolution import (
    IdentityKey,
    deterministic_canonical_id,
)
from modules.integration.application.mapping import FieldLineage
from modules.integration.connectors.base import (
    SourceConnector,
    build_field_lineage,
    parse_datetime,
)
from shared.domain import (
    AddressLocation,
    CompetitorStore,
    GeoCell,
    Listing,
    Poi,
)


def _deterministic_id(entity_type: str, source_system: str, source_entity_id: str) -> str:
    return deterministic_canonical_id(
        IdentityKey(
            entity_type=entity_type,
            source_id=source_system,
            source_entity_id=source_entity_id,
        )
    )


class _GeoConnector(SourceConnector):
    """Base for external connectors that geocode an address-bearing record."""

    def _geocode(
        self, record: Mapping[str, Any], as_of: datetime | None, *, source_entity_id: str
    ) -> GeocodeResult:
        if self.geo_pipeline is None:
            raise ValueError(f"{self.connector_id} requires a geo pipeline")
        result = self.geo_pipeline.geocode_record(record, as_of=as_of)
        # Pin a deterministic address id so canonical output is reproducible.
        address_id = _deterministic_id(
            "address",
            self.contract.source_system,
            source_entity_id or result.address.normalized_address,
        )
        return replace(result, address=replace(result.address, address_id=address_id))

    @staticmethod
    def _geo_cell_id(geocode: GeocodeResult, *, resolution: int = 9) -> str:
        if geocode.h3_resolution_map.get(resolution):
            return build_geo_cell(geocode, resolution=resolution).geo_cell_id
        return ""


class PoiConnector(_GeoConnector):
    canonical_target = "poi"

    def canonicalize(
        self, record: Mapping[str, Any], *, as_of: datetime | None = None
    ) -> tuple[Any, tuple[FieldLineage, ...], GeocodeResult]:
        source_poi_id = str(record["source_poi_id"])
        geocode = self._geocode(record, as_of, source_entity_id=source_poi_id)
        poi = Poi(
            poi_id=_deterministic_id("poi", self.contract.source_system, source_poi_id),
            source_poi_id=source_poi_id,
            poi_name=str(record.get("poi_name", "")),
            poi_category=str(record.get("poi_category", "")),
            poi_subcategory=str(record.get("poi_subcategory", "")),
            address_id=geocode.address.address_id,
            geo_cell_id=self._geo_cell_id(geocode),
            status=str(record.get("status", "active")),
            confidence=float(record.get("confidence", 1.0)),
            snapshot_id=str(record.get("snapshot_id", "")),
        )
        lineage = build_field_lineage(
            record,
            (
                ("source_poi_id", "source_poi_id"),
                ("poi_name", "poi_name"),
                ("poi_category", "poi_category"),
                ("poi_subcategory", "poi_subcategory"),
                ("address_id", "address_raw"),
                ("status", "status"),
                ("confidence", "confidence"),
                ("snapshot_id", "snapshot_id"),
            ),
        )
        return poi, lineage, geocode


class CompetitorStoreConnector(_GeoConnector):
    canonical_target = "competitor_store"

    def canonicalize(
        self, record: Mapping[str, Any], *, as_of: datetime | None = None
    ) -> tuple[Any, tuple[FieldLineage, ...], GeocodeResult]:
        source_id = str(record["source_competitor_id"])
        geocode = self._geocode(record, as_of, source_entity_id=source_id)
        competitor = CompetitorStore(
            competitor_store_id=_deterministic_id(
                "competitor_store", self.contract.source_system, source_id
            ),
            brand_name=str(record.get("brand_name", "")),
            store_name=str(record.get("store_name", "")),
            address_id=geocode.address.address_id,
            geo_cell_id=self._geo_cell_id(geocode),
            estimated_capacity=float(record.get("estimated_capacity", 0.0) or 0.0),
            status=str(record.get("status", "active")),
            confidence=float(record.get("confidence", 1.0)),
            last_verified_at=parse_datetime(record.get("last_verified_at")),
        )
        lineage = build_field_lineage(
            record,
            (
                ("brand_name", "brand_name"),
                ("store_name", "store_name"),
                ("address_id", "address_raw"),
                ("estimated_capacity", "estimated_capacity"),
                ("status", "status"),
                ("confidence", "confidence"),
                ("last_verified_at", "last_verified_at"),
            ),
        )
        return competitor, lineage, geocode


class ListingConnector(_GeoConnector):
    canonical_target = "listing"

    def canonicalize(
        self, record: Mapping[str, Any], *, as_of: datetime | None = None
    ) -> tuple[Any, tuple[FieldLineage, ...], GeocodeResult]:
        source_id = str(record.get("source_listing_id", ""))
        mapping = self.mapper.map_record(
            "listing", record, source_id=self.contract.source_system, tenant_id=self.tenant_id
        )
        geocode = self._geocode(record, as_of, source_entity_id=source_id)
        listing: Listing = replace(
            mapping.canonical,
            source_id=self.contract.source_system,
            address_id=geocode.address.address_id,
            snapshot_id=str(record.get("snapshot_id", "")),
        )
        lineage = mapping.field_lineage + build_field_lineage(
            record, (("address_id", "address_raw"),)
        )
        return listing, lineage, geocode


class AdminBoundaryConnector(SourceConnector):
    """Administrative boundary reference; canonicalized as an admin geo cell."""

    canonical_target = "geo_cell"

    def canonicalize(
        self, record: Mapping[str, Any], *, as_of: datetime | None = None
    ) -> tuple[Any, tuple[FieldLineage, ...], None]:
        source_id = str(record["source_boundary_id"])
        admin_level = str(record.get("admin_level", ""))
        admin_name = str(record.get("admin_name", ""))
        latitude = _as_float(record.get("centroid_latitude"))
        longitude = _as_float(record.get("centroid_longitude"))
        h3_index = ""
        if (
            latitude is not None
            and longitude is not None
            and coordinates_in_market(latitude, longitude)
        ):
            h3_index = stable_h3_index(latitude, longitude, 9)
        geo_cell = GeoCell(
            geo_cell_id=_deterministic_id("geo_cell", self.contract.source_system, source_id),
            h3_index=h3_index,
            h3_resolution=9,
            centroid_latitude=latitude or 0.0,
            centroid_longitude=longitude or 0.0,
            admin_city=admin_name if admin_level == "city" else "",
            admin_district=admin_name if admin_level == "district" else "",
            service_area_id=str(record.get("admin_code", "")) or None,
        )
        lineage = build_field_lineage(
            record,
            (
                ("admin_city", "admin_name"),
                ("centroid_latitude", "centroid_latitude"),
                ("centroid_longitude", "centroid_longitude"),
                ("service_area_id", "admin_code"),
            ),
        )
        return geo_cell, lineage, None


class GeocodeConnector(_GeoConnector):
    """Third-party geocode results landed as canonical address locations."""

    canonical_target = "address_location"

    def canonicalize(
        self, record: Mapping[str, Any], *, as_of: datetime | None = None
    ) -> tuple[Any, tuple[FieldLineage, ...], GeocodeResult]:
        source_id = str(record["source_geocode_id"])
        geocode = self._geocode(record, as_of, source_entity_id=source_id)
        address: AddressLocation = geocode.address
        lineage = build_field_lineage(
            record,
            (
                ("raw_address", "address_raw"),
                ("latitude", "latitude"),
                ("longitude", "longitude"),
                ("geocode_precision", "geocode_precision"),
                ("geocode_confidence", "confidence"),
                ("city", "admin_city"),
                ("district", "admin_district"),
            ),
        )
        return address, lineage, geocode


_EXTERNAL_CONNECTOR_TYPES: dict[str, type[SourceConnector]] = {
    "poi_snapshot": PoiConnector,
    "competitor_store_snapshot": CompetitorStoreConnector,
    "listing_raw_snapshot": ListingConnector,
    "admin_boundary_snapshot": AdminBoundaryConnector,
    "geocode_result_snapshot": GeocodeConnector,
}


def build_external_connectors(
    *, geo_pipeline: GeoPipeline | None = None, tenant_id: str = ""
) -> dict[str, SourceConnector]:
    """One connector per external contract, keyed by contract id."""
    pipeline = geo_pipeline or GeoPipeline(StaticGeocodeProvider({}))
    connectors: dict[str, SourceConnector] = {}
    for contract_id, connector_cls in _EXTERNAL_CONNECTOR_TYPES.items():
        contract = external_contract(contract_id)
        connectors[contract_id] = connector_cls(
            contract, geo_pipeline=pipeline, tenant_id=tenant_id
        )
    return connectors


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "AdminBoundaryConnector",
    "CompetitorStoreConnector",
    "GeocodeConnector",
    "ListingConnector",
    "PoiConnector",
    "build_external_connectors",
]
