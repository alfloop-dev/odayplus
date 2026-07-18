"""Listing application services, exposed without eager pipeline imports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "ListingImportResult",
    "ListingPipeline",
    "ListingPipelineRecord",
    "run_listing_csv_import",
    "run_listing_import",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    value = getattr(import_module("modules.listing.application.pipeline"), name)
    globals()[name] = value
    return value
