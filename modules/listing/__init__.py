"""Listing public API, loaded lazily to keep domain imports side-effect free."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "CandidateSiteDraft",
    "InMemoryListingRepository",
    "ListingDuplicateGroup",
    "ListingHardRulePolicy",
    "ListingImportResult",
    "ListingPipeline",
    "ListingPipelineRecord",
    "run_listing_csv_import",
    "ListingPipelineStatus",
    "run_listing_import",
]

_EXPORT_MODULES = {
    "CandidateSiteDraft": "modules.listing.domain.models",
    "InMemoryListingRepository": "modules.listing.infrastructure.repositories",
    "ListingDuplicateGroup": "modules.listing.domain.models",
    "ListingHardRulePolicy": "modules.listing.domain.models",
    "ListingImportResult": "modules.listing.application.pipeline",
    "ListingPipeline": "modules.listing.application.pipeline",
    "ListingPipelineRecord": "modules.listing.application.pipeline",
    "ListingPipelineStatus": "modules.listing.domain.models",
    "run_listing_csv_import": "modules.listing.application.pipeline",
    "run_listing_import": "modules.listing.application.pipeline",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
