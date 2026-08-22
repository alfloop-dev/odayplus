"""Data Platform Client Infrastructure Adapter.

Contract: `odayplus.data-platform-foundation-client.v1`, `odayplus.data-platform-product-client.v1`.
Part of Task: `ODP-LEGACY-FACADE-001`.

This infrastructure adapter encapsulates communication with the generated foundation and product
contract clients published by `alfloop-dev/oday-data-platform`. It provides typed access to versioned
product models (e.g. SiteMarketContext, MarketCellProfile, CatchmentProfile, PropertyObservation)
and foundation datasets (e.g. StoreReference, StoreDayCoverage, PlatformFoundation) without exposing
raw producer tables or direct database credentials.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Foundation contracts client
from packages.oday_data_contracts_client import (
    ContractClientError as FoundationClientError,
    FoundationRelease,
    FoundationVersion,
    diagnostics as foundation_diagnostics,
    foundation_contracts,
    foundation_version,
    verify_release as verify_foundation_release,
)
from packages.oday_data_contracts_client.models import (
    Coverage,
    EMGIPlatformFoundationConfig,
    MachineCapacityRecord,
    ManifestDocument,
    MeasurementEnvelope,
    OperationalStartObservation,
    ScopePrincipalDocument,
    SourceObservationDocument,
    SourceRegistryDocument,
    StoreDailyPerformance,
    StoreDayCoverage,
    StoreReference,
    TimeContract,
)

# Product contracts client
from packages.oday_data_product_contracts_client import (
    ContractClientError as ProductClientError,
    ProductRelease,
    ProductVersion,
    diagnostics as product_diagnostics,
    product_contracts,
    product_version,
    verify_release as verify_product_release,
)
from packages.oday_data_product_contracts_client.models import (
    CatchmentProfileDocument,
    CoverageSurface,
    DataAcquisitionPlan,
    FieldSurveyDocument,
    MarketCellProfileDocument,
    PropertyObservationDocument,
    SiteMarketContextDocument,
)
from packages.oday_data_product_contracts_client.models.catchment_profile import (
    CatchmentProfile,
)
from packages.oday_data_product_contracts_client.models.market_cell_profile import (
    MarketCellProfile,
)
from packages.oday_data_product_contracts_client.models.property_observation import (
    PropertyEntity,
    PropertyListingObservation,
)
from packages.oday_data_product_contracts_client.models.site_market_context import (
    PeriodGrain,
    SiteMarketContext,
)


class DataPlatformClientError(Exception):
    """Base exception for all DataPlatformClient infrastructure errors."""

    def __init__(self, message: str, *, code: str = "data_platform_error", details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details) if details else {}


class DataPlatformConnectionError(DataPlatformClientError):
    """Raised when communication with the data platform fails."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="data_platform_connection_error", details=details)


class DataPlatformDocumentNotFoundError(DataPlatformClientError):
    """Raised when a requested contract document or entity is not found."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="document_not_found", details=details)


class DataPlatformValidationError(DataPlatformClientError):
    """Raised when data returned by the data platform fails schema validation."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="contract_validation_error", details=details)


class DataPlatformIntegrityError(DataPlatformClientError):
    """Raised when release digests or contract lock verification fails."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="release_integrity_error", details=details)


@runtime_checkable
class DataPlatformTransport(Protocol):
    """Transport protocol for querying documents from the data platform."""

    def fetch_document(
        self,
        contract_id: str,
        *,
        document_id: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        """Fetch a single document payload by contract ID and optional document ID or query parameters."""
        ...

    def query_records(
        self,
        contract_id: str,
        *,
        filter_params: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Query multiple record payloads matching filter parameters."""
        ...


@dataclass
class InMemoryDataPlatformTransport:
    """In-memory transport implementation for testing and local replay."""

    _documents: dict[str, dict[str, Mapping[str, Any]]] = field(default_factory=dict)
    _records: dict[str, list[Mapping[str, Any]]] = field(default_factory=dict)
    _site_contexts: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    _cell_profiles: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    _catchment_profiles: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    _properties: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    _listings: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    _stores: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    _store_coverage: dict[tuple[str, str], Mapping[str, Any]] = field(default_factory=dict)
    _store_performance: dict[tuple[str, str], Mapping[str, Any]] = field(default_factory=dict)

    def store_document(
        self,
        contract_id: str,
        document_id: str,
        data: Mapping[str, Any],
    ) -> None:
        """Store a document by contract ID and document ID."""
        if contract_id not in self._documents:
            self._documents[contract_id] = {}
        self._documents[contract_id][document_id] = data

    def store_record(self, contract_id: str, data: Mapping[str, Any]) -> None:
        """Store a single record under a contract ID."""
        if contract_id not in self._records:
            self._records[contract_id] = []
        self._records[contract_id].append(data)

    def store_site_context(self, site_id: str, data: Mapping[str, Any]) -> None:
        """Index a SiteMarketContext record by site_id."""
        self._site_contexts[site_id] = data

    def store_cell_profile(self, cell_id: str, data: Mapping[str, Any]) -> None:
        """Index a MarketCellProfile record by cell_id."""
        self._cell_profiles[cell_id] = data

    def store_catchment_profile(self, catchment_id: str, data: Mapping[str, Any]) -> None:
        """Index a CatchmentProfile record by catchment_id."""
        self._catchment_profiles[catchment_id] = data

    def store_property_entity(self, property_id: str, data: Mapping[str, Any]) -> None:
        """Index a property entity by property_id."""
        self._properties[property_id] = data

    def store_listing_observation(self, listing_id: str, data: Mapping[str, Any]) -> None:
        """Index a listing observation by listing_obs_id or source_listing_id."""
        self._listings[listing_id] = data

    def store_store_reference(self, store_id: str, data: Mapping[str, Any]) -> None:
        """Index a store reference by store_id."""
        self._stores[store_id] = data

    def store_store_coverage_record(self, store_id: str, date_key: str, data: Mapping[str, Any]) -> None:
        """Index store day coverage by (store_id, date_key)."""
        self._store_coverage[(store_id, date_key)] = data

    def store_store_daily_performance_record(self, store_id: str, date_key: str, data: Mapping[str, Any]) -> None:
        """Index store daily performance by (store_id, date_key)."""
        self._store_performance[(store_id, date_key)] = data

    def fetch_document(
        self,
        contract_id: str,
        *,
        document_id: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        query_params = dict(params) if params else {}

        if document_id and contract_id in self._documents and document_id in self._documents[contract_id]:
            return self._documents[contract_id][document_id]

        if contract_id in self._documents and self._documents[contract_id]:
            if not document_id and not query_params:
                return next(iter(self._documents[contract_id].values()))
            for doc in self._documents[contract_id].values():
                matches = True
                for k, v in query_params.items():
                    if doc.get(k) != v:
                        matches = False
                        break
                if matches:
                    return doc

        return None

    def query_records(
        self,
        contract_id: str,
        *,
        filter_params: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        records = self._records.get(contract_id, [])
        if not filter_params:
            return list(records)
        matched = []
        for rec in records:
            if all(rec.get(k) == v for k, v in filter_params.items()):
                matched.append(rec)
        return matched

    def get_indexed_site_context(self, site_id: str) -> Mapping[str, Any] | None:
        return self._site_contexts.get(site_id)

    def get_indexed_cell_profile(self, cell_id: str) -> Mapping[str, Any] | None:
        return self._cell_profiles.get(cell_id)

    def get_indexed_catchment_profile(self, catchment_id: str) -> Mapping[str, Any] | None:
        return self._catchment_profiles.get(catchment_id)

    def get_indexed_property(self, property_id: str) -> Mapping[str, Any] | None:
        return self._properties.get(property_id)

    def get_indexed_listing(self, listing_id: str) -> Mapping[str, Any] | None:
        return self._listings.get(listing_id)

    def get_indexed_store_reference(self, store_id: str) -> Mapping[str, Any] | None:
        return self._stores.get(store_id)

    def get_indexed_store_coverage(self, store_id: str, date_key: str) -> Mapping[str, Any] | None:
        return self._store_coverage.get((store_id, date_key))

    def get_indexed_store_performance(self, store_id: str, date_key: str) -> Mapping[str, Any] | None:
        return self._store_performance.get((store_id, date_key))


class DataPlatformClient:
    """Client adapter for reading released foundation and product data contracts.

    Guarantees:
    1. Read models are generated only from pinned contract releases (`oday_data_contracts_client`
       and `oday_data_product_contracts_client`).
    2. Strict read-only semantics: no provider credentials, no raw fetch, and no direct source-snapshot writes.
    3. Fail-closed contract validation and comprehensive release diagnostics.
    """

    def __init__(self, transport: DataPlatformTransport | None = None) -> None:
        self._transport = transport if transport is not None else InMemoryDataPlatformTransport()

    @property
    def transport(self) -> DataPlatformTransport:
        return self._transport

    # -----------------------------------------------------------------------
    # Version & Diagnostics APIs
    # -----------------------------------------------------------------------

    def get_foundation_version(self) -> FoundationVersion:
        """Return the exact verified foundation release version."""
        try:
            return foundation_version()
        except FoundationClientError as err:
            raise DataPlatformIntegrityError(f"Foundation contract release verification failed: {err}") from err

    def get_product_version(self) -> ProductVersion:
        """Return the exact verified product release version."""
        try:
            return product_version()
        except ProductClientError as err:
            raise DataPlatformIntegrityError(f"Product contract release verification failed: {err}") from err

    def verify_integrity(self) -> dict[str, Any]:
        """Verify release integrity across both foundation and product clients."""
        try:
            foundation_report = verify_foundation_release()
            product_report = verify_product_release()
            return {
                "status": "healthy",
                "foundation": {
                    "compatible": foundation_report.compatible,
                    "release_id": foundation_report.release_id,
                    "semantic_version": foundation_report.semantic_version,
                    "contracts_checked": len(foundation_report.checked_contracts),
                },
                "product": {
                    "compatible": product_report.compatible,
                    "release_id": product_report.release_id,
                    "semantic_version": product_report.semantic_version,
                    "contracts_checked": len(product_report.checked_contracts),
                },
            }
        except Exception as err:
            raise DataPlatformIntegrityError(f"Integrity check failed: {err}") from err

    def get_diagnostics(self) -> dict[str, Any]:
        """Return runtime diagnostics for all pinned data contracts."""
        return {
            "foundation": foundation_diagnostics(),
            "product": product_diagnostics(),
        }

    # -----------------------------------------------------------------------
    # Product Contracts: Site Market Context (emgi.site-market-context.v1)
    # -----------------------------------------------------------------------

    def get_site_market_context_document(
        self,
        document_id: str | None = None,
        *,
        site_id: str | None = None,
        tenant_id: str | None = None,
    ) -> SiteMarketContextDocument:
        """Fetch and parse a complete SiteMarketContextDocument."""
        params: dict[str, Any] = {}
        if site_id:
            params["site_id"] = site_id
        if tenant_id:
            params["tenant_id"] = tenant_id

        raw = self._transport.fetch_document(
            "emgi.site-market-context.v1",
            document_id=document_id,
            params=params,
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"SiteMarketContextDocument not found (document_id={document_id}, site_id={site_id})",
                details={"document_id": document_id, "site_id": site_id, "tenant_id": tenant_id},
            )
        try:
            return SiteMarketContextDocument.from_dict(raw)
        except Exception as err:
            raise DataPlatformValidationError(
                f"Failed to parse SiteMarketContextDocument: {err}",
                details={"contract_id": "emgi.site-market-context.v1", "raw": raw},
            ) from err

    def get_site_market_context(
        self,
        site_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
    ) -> SiteMarketContext:
        """Retrieve a specific SiteMarketContext object by site_id."""
        if isinstance(self._transport, InMemoryDataPlatformTransport):
            raw_indexed = self._transport.get_indexed_site_context(site_id)
            if raw_indexed is not None:
                try:
                    return SiteMarketContext.from_dict(raw_indexed)
                except Exception as err:
                    raise DataPlatformValidationError(f"Invalid indexed SiteMarketContext for site_id={site_id}: {err}") from err

        try:
            doc = self.get_site_market_context_document(site_id=site_id, tenant_id=tenant_id)
            for ctx in doc.contexts:
                if ctx.identity.site_id == site_id:
                    if period_key and ctx.period_key != period_key:
                        continue
                    return ctx
        except DataPlatformDocumentNotFoundError:
            pass

        raise DataPlatformDocumentNotFoundError(
            f"SiteMarketContext not found for site_id={site_id}",
            details={"site_id": site_id, "period_grain": str(period_grain), "period_key": period_key},
        )

    # -----------------------------------------------------------------------
    # Product Contracts: Market Cell Profile (emgi.market-cell-profile.v1)
    # -----------------------------------------------------------------------

    def get_market_cell_profile_document(
        self,
        document_id: str | None = None,
        *,
        cell_id: str | None = None,
        tenant_id: str | None = None,
    ) -> MarketCellProfileDocument:
        """Fetch and parse a complete MarketCellProfileDocument."""
        params: dict[str, Any] = {}
        if cell_id:
            params["cell_id"] = cell_id
        if tenant_id:
            params["tenant_id"] = tenant_id

        raw = self._transport.fetch_document(
            "emgi.market-cell-profile.v1",
            document_id=document_id,
            params=params,
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"MarketCellProfileDocument not found (document_id={document_id}, cell_id={cell_id})",
                details={"document_id": document_id, "cell_id": cell_id, "tenant_id": tenant_id},
            )
        try:
            return MarketCellProfileDocument.from_dict(raw)
        except Exception as err:
            raise DataPlatformValidationError(
                f"Failed to parse MarketCellProfileDocument: {err}",
                details={"contract_id": "emgi.market-cell-profile.v1", "raw": raw},
            ) from err

    def get_market_cell_profile(
        self,
        cell_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
    ) -> MarketCellProfile:
        """Retrieve a specific MarketCellProfile object by cell_id."""
        if isinstance(self._transport, InMemoryDataPlatformTransport):
            raw_indexed = self._transport.get_indexed_cell_profile(cell_id)
            if raw_indexed is not None:
                try:
                    return MarketCellProfile.from_dict(raw_indexed)
                except Exception as err:
                    raise DataPlatformValidationError(f"Invalid indexed MarketCellProfile for cell_id={cell_id}: {err}") from err

        try:
            doc = self.get_market_cell_profile_document(cell_id=cell_id, tenant_id=tenant_id)
            for cell in doc.cells:
                if cell.cell_id == cell_id:
                    if period_key and cell.period_key != period_key:
                        continue
                    return cell
        except DataPlatformDocumentNotFoundError:
            pass

        raise DataPlatformDocumentNotFoundError(
            f"MarketCellProfile not found for cell_id={cell_id}",
            details={"cell_id": cell_id, "period_grain": str(period_grain), "period_key": period_key},
        )

    # -----------------------------------------------------------------------
    # Product Contracts: Catchment Profile (emgi.catchment-profile.v1)
    # -----------------------------------------------------------------------

    def get_catchment_profile_document(
        self,
        document_id: str | None = None,
        *,
        catchment_id: str | None = None,
        tenant_id: str | None = None,
    ) -> CatchmentProfileDocument:
        """Fetch and parse a complete CatchmentProfileDocument."""
        params: dict[str, Any] = {}
        if catchment_id:
            params["catchment_id"] = catchment_id
        if tenant_id:
            params["tenant_id"] = tenant_id

        raw = self._transport.fetch_document(
            "emgi.catchment-profile.v1",
            document_id=document_id,
            params=params,
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"CatchmentProfileDocument not found (document_id={document_id}, catchment_id={catchment_id})",
                details={"document_id": document_id, "catchment_id": catchment_id, "tenant_id": tenant_id},
            )
        try:
            return CatchmentProfileDocument.from_dict(raw)
        except Exception as err:
            raise DataPlatformValidationError(
                f"Failed to parse CatchmentProfileDocument: {err}",
                details={"contract_id": "emgi.catchment-profile.v1", "raw": raw},
            ) from err

    def get_catchment_profile(
        self,
        catchment_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
    ) -> CatchmentProfile:
        """Retrieve a specific CatchmentProfile object by catchment_id."""
        if isinstance(self._transport, InMemoryDataPlatformTransport):
            raw_indexed = self._transport.get_indexed_catchment_profile(catchment_id)
            if raw_indexed is not None:
                try:
                    return CatchmentProfile.from_dict(raw_indexed)
                except Exception as err:
                    raise DataPlatformValidationError(f"Invalid indexed CatchmentProfile for catchment_id={catchment_id}: {err}") from err

        try:
            doc = self.get_catchment_profile_document(catchment_id=catchment_id, tenant_id=tenant_id)
            for prof in doc.profiles:
                if prof.profile_id == catchment_id or (hasattr(prof, "boundary") and prof.boundary.catchment_id == catchment_id):
                    if period_key and prof.period_key != period_key:
                        continue
                    return prof
        except DataPlatformDocumentNotFoundError:
            pass

        raise DataPlatformDocumentNotFoundError(
            f"CatchmentProfile not found for catchment_id={catchment_id}",
            details={"catchment_id": catchment_id, "period_grain": str(period_grain), "period_key": period_key},
        )

    # -----------------------------------------------------------------------
    # Product Contracts: Property Observation (emgi.property-observation.v1)
    # -----------------------------------------------------------------------

    def get_property_observation_document(
        self,
        document_id: str | None = None,
        *,
        property_id: str | None = None,
        listing_id: str | None = None,
    ) -> PropertyObservationDocument:
        """Fetch and parse a complete PropertyObservationDocument."""
        params: dict[str, Any] = {}
        if property_id:
            params["property_id"] = property_id
        if listing_id:
            params["listing_id"] = listing_id

        raw = self._transport.fetch_document(
            "emgi.property-observation.v1",
            document_id=document_id,
            params=params,
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"PropertyObservationDocument not found (document_id={document_id}, property_id={property_id})",
                details={"document_id": document_id, "property_id": property_id, "listing_id": listing_id},
            )
        try:
            return PropertyObservationDocument.from_dict(raw)
        except Exception as err:
            raise DataPlatformValidationError(
                f"Failed to parse PropertyObservationDocument: {err}",
                details={"contract_id": "emgi.property-observation.v1", "raw": raw},
            ) from err

    def get_property_entity(self, property_id: str) -> PropertyEntity:
        """Retrieve a specific PropertyEntity by property_id."""
        if isinstance(self._transport, InMemoryDataPlatformTransport):
            raw_indexed = self._transport.get_indexed_property(property_id)
            if raw_indexed is not None:
                try:
                    return PropertyEntity.from_dict(raw_indexed)
                except Exception as err:
                    raise DataPlatformValidationError(f"Invalid indexed PropertyEntity for property_id={property_id}: {err}") from err

        try:
            doc = self.get_property_observation_document(property_id=property_id)
            for prop in doc.properties:
                if prop.property_id == property_id:
                    return prop
        except DataPlatformDocumentNotFoundError:
            pass

        raise DataPlatformDocumentNotFoundError(
            f"PropertyEntity not found for property_id={property_id}",
            details={"property_id": property_id},
        )

    def get_listing_observation(self, listing_id: str) -> PropertyListingObservation:
        """Retrieve a specific PropertyListingObservation by listing_obs_id or source_listing_id."""
        if isinstance(self._transport, InMemoryDataPlatformTransport):
            raw_indexed = self._transport.get_indexed_listing(listing_id)
            if raw_indexed is not None:
                try:
                    return PropertyListingObservation.from_dict(raw_indexed)
                except Exception as err:
                    raise DataPlatformValidationError(f"Invalid indexed PropertyListingObservation for listing_id={listing_id}: {err}") from err

        try:
            doc = self.get_property_observation_document(listing_id=listing_id)
            for obs in doc.listing_observations:
                if obs.listing_obs_id == listing_id or obs.source_listing_id == listing_id:
                    return obs
        except DataPlatformDocumentNotFoundError:
            pass

        raise DataPlatformDocumentNotFoundError(
            f"PropertyListingObservation not found for listing_id={listing_id}",
            details={"listing_id": listing_id},
        )

    # -----------------------------------------------------------------------
    # Foundation Contracts: Platform Foundation & Store Reference
    # -----------------------------------------------------------------------

    def get_platform_foundation_config(self) -> EMGIPlatformFoundationConfig:
        """Fetch and parse EMGI Platform Foundation Config."""
        raw = self._transport.fetch_document("emgi.platform-foundation.v1")
        if raw is None:
            raise DataPlatformDocumentNotFoundError("EMGIPlatformFoundationConfig document not found")
        try:
            return EMGIPlatformFoundationConfig.from_dict(raw)
        except Exception as err:
            raise DataPlatformValidationError(f"Failed to parse EMGIPlatformFoundationConfig: {err}") from err

    def get_store_reference(self, store_id: str) -> StoreReference:
        """Retrieve StoreReference for a store_id."""
        if isinstance(self._transport, InMemoryDataPlatformTransport):
            raw_indexed = self._transport.get_indexed_store_reference(store_id)
            if raw_indexed is not None:
                try:
                    return StoreReference.from_dict(raw_indexed)
                except Exception as err:
                    raise DataPlatformValidationError(f"Invalid indexed StoreReference for store_id={store_id}: {err}") from err

        raw = self._transport.fetch_document("oday.store-reference.v1", params={"store_id": store_id})
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"StoreReference not found for store_id={store_id}",
                details={"store_id": store_id},
            )
        try:
            return StoreReference.from_dict(raw)
        except Exception as err:
            raise DataPlatformValidationError(f"Failed to parse StoreReference: {err}") from err

    def get_store_day_coverage(self, store_id: str, date_key: str) -> StoreDayCoverage:
        """Retrieve StoreDayCoverage for (store_id, date_key)."""
        if isinstance(self._transport, InMemoryDataPlatformTransport):
            raw_indexed = self._transport.get_indexed_store_coverage(store_id, date_key)
            if raw_indexed is not None:
                try:
                    return StoreDayCoverage.from_dict(raw_indexed)
                except Exception as err:
                    raise DataPlatformValidationError(f"Invalid indexed StoreDayCoverage for store_id={store_id}, date={date_key}: {err}") from err

        raw = self._transport.fetch_document(
            "oday.store-coverage.v1",
            params={"store_id": store_id, "date_key": date_key},
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"StoreDayCoverage not found for store_id={store_id}, date={date_key}",
                details={"store_id": store_id, "date_key": date_key},
            )
        try:
            return StoreDayCoverage.from_dict(raw)
        except Exception as err:
            raise DataPlatformValidationError(f"Failed to parse StoreDayCoverage: {err}") from err

    def get_store_daily_performance(self, store_id: str, date_key: str) -> StoreDailyPerformance:
        """Retrieve StoreDailyPerformance for (store_id, date_key)."""
        if isinstance(self._transport, InMemoryDataPlatformTransport):
            raw_indexed = self._transport.get_indexed_store_performance(store_id, date_key)
            if raw_indexed is not None:
                try:
                    return StoreDailyPerformance.from_dict(raw_indexed)
                except Exception as err:
                    raise DataPlatformValidationError(f"Invalid indexed StoreDailyPerformance for store_id={store_id}, date={date_key}: {err}") from err

        raw = self._transport.fetch_document(
            "oday.store-daily-performance.v1",
            params={"store_id": store_id, "date_key": date_key},
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"StoreDailyPerformance not found for store_id={store_id}, date={date_key}",
                details={"store_id": store_id, "date_key": date_key},
            )
        try:
            return StoreDailyPerformance.from_dict(raw)
        except Exception as err:
            raise DataPlatformValidationError(f"Failed to parse StoreDailyPerformance: {err}") from err


__all__ = [
    "DataPlatformClient",
    "DataPlatformClientError",
    "DataPlatformConnectionError",
    "DataPlatformDocumentNotFoundError",
    "DataPlatformIntegrityError",
    "DataPlatformTransport",
    "DataPlatformValidationError",
    "InMemoryDataPlatformTransport",
]
