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
    "AssignmentSlaService",
    "SlaCalendar",
    "InMemoryAssignmentRepository",
    "InMemorySlaRepository",
]

_EXPORT_MODULES = {
    "ListingImportResult": "modules.listing.application.pipeline",
    "ListingPipeline": "modules.listing.application.pipeline",
    "ListingPipelineRecord": "modules.listing.application.pipeline",
    "run_listing_csv_import": "modules.listing.application.pipeline",
    "run_listing_import": "modules.listing.application.pipeline",
    "AssignmentSlaService": "modules.listing.application.assignment_sla",
    "SlaCalendar": "modules.listing.application.assignment_sla",
    "InMemoryAssignmentRepository": "modules.listing.application.assignment_sla",
    "InMemorySlaRepository": "modules.listing.application.assignment_sla",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
