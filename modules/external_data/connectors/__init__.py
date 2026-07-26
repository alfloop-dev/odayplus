"""External Data Platform connectors.

Land + validate + canonicalize external datasets (POI, competitor stores,
listings, administrative boundaries, geocode results) with geocode / H3
enrichment and a preserved lineage envelope. See ``external.py`` for the
connector implementations and ``build_external_connectors`` for the registry.
"""

from __future__ import annotations

from modules.external_data.connectors.external import (
    AdminBoundaryConnector,
    CompetitorStoreConnector,
    GeocodeConnector,
    ListingConnector,
    PoiConnector,
    build_external_connectors,
)
from modules.external_data.connectors.provider_connectivity import (
    ProviderConnectivityResult,
    ProviderProbeEvidence,
    probe_external_provider_connectivity,
)
from modules.external_data.connectors.provider_registry import (
    ExternalProviderConfigError,
    ExternalProviderDefinition,
    ExternalProviderMode,
    ProviderAuthMode,
    ProviderCategory,
    ProviderCredential,
    ProviderLicense,
    ProviderValidationResult,
    provider_downstream_use_flags,
    provider_export_allowed,
    provider_registry,
    provider_secret_inventory,
    validate_external_providers,
    validate_external_providers_or_raise,
)

from modules.external_data.connectors.store_opening import (
    ApprovedStoreOpeningAuthority,
    MissingStoreOpeningAuthorityError,
    StoreOpeningAuthorityConnector,
    StoreOpeningBackfillEngine,
    StoreOpeningBackfillResult,
    StoreOpeningError,
    TenantIsolationError,
    UnauthoritativeStoreOpeningError,
    validate_store_opening_record,
)

__all__ = [
    "AdminBoundaryConnector",
    "CompetitorStoreConnector",
    "GeocodeConnector",
    "ListingConnector",
    "PoiConnector",
    "StoreOpeningAuthorityConnector",
    "ApprovedStoreOpeningAuthority",
    "StoreOpeningBackfillEngine",
    "StoreOpeningBackfillResult",
    "StoreOpeningError",
    "UnauthoritativeStoreOpeningError",
    "MissingStoreOpeningAuthorityError",
    "TenantIsolationError",
    "validate_store_opening_record",
    "build_external_connectors",
    "ProviderConnectivityResult",
    "ProviderProbeEvidence",
    "probe_external_provider_connectivity",
    "ExternalProviderConfigError",
    "ExternalProviderDefinition",
    "ExternalProviderMode",
    "ProviderAuthMode",
    "ProviderCategory",
    "ProviderCredential",
    "ProviderLicense",
    "ProviderValidationResult",
    "provider_downstream_use_flags",
    "provider_export_allowed",
    "provider_registry",
    "provider_secret_inventory",
    "validate_external_providers",
    "validate_external_providers_or_raise",
]

