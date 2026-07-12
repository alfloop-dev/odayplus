"""External data application services.

The ingestion-run symbols are re-exported lazily (PEP 562) rather than at
package import time. Eagerly importing ``ingestion_service``/``ingestion_store``
here would pull in ``workers.scheduled_fetch`` →
``shared.infrastructure.persistence`` at package-init, and that persistence
package eagerly imports ``modules.heatzone.workers`` (via ``repositories``).
Because ``modules.heatzone.workers`` imports ``modules.external_data.geo`` (which
runs *this* package's ``__init__``), the eager version closed an import cycle.
Deferring the heavy imports keeps ``modules.external_data`` importable from the
heatzone side while preserving ``from modules.external_data.application import
ExternalIngestionService`` for direct callers.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from modules.external_data.application.ingestion_service import (
        ExternalIngestionService,
        IngestionOutcome,
    )
    from modules.external_data.application.ingestion_store import (
        IngestionRunRecord,
        InMemoryIngestionRunStore,
        LineageRecord,
        QuarantineRecord,
        build_ingestion_run_record,
    )

__all__ = [
    "ExternalIngestionService",
    "IngestionOutcome",
    "IngestionRunRecord",
    "InMemoryIngestionRunStore",
    "LineageRecord",
    "QuarantineRecord",
    "build_ingestion_run_record",
]

_SERVICE_EXPORTS = {"ExternalIngestionService", "IngestionOutcome"}
_STORE_EXPORTS = {
    "IngestionRunRecord",
    "InMemoryIngestionRunStore",
    "LineageRecord",
    "QuarantineRecord",
    "build_ingestion_run_record",
}


def __getattr__(name: str) -> Any:
    if name in _SERVICE_EXPORTS:
        from modules.external_data.application import ingestion_service

        return getattr(ingestion_service, name)
    if name in _STORE_EXPORTS:
        from modules.external_data.application import ingestion_store

        return getattr(ingestion_store, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
