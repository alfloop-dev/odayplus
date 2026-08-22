"""External data application services.

XR-CUTOVER-001 decommissioned the legacy ingestion loop: ``ingestion_service``
and ``ingestion_store`` are gone, and the datasets they used to fetch are now
read through :mod:`modules.external_data.application.market_data_facade`. What
remains here is the retained provenance record surface
(:mod:`modules.external_data.application.ingestion_records`) plus the operator
XLSX import path.

The symbols are re-exported lazily (PEP 562) rather than at package import
time. Eagerly importing them would pull ``shared.infrastructure.persistence``
in at package-init, and that persistence package eagerly imports
``modules.heatzone.workers`` (via ``repositories``). Because
``modules.heatzone.workers`` imports ``modules.external_data.geo`` (which runs
*this* package's ``__init__``), the eager version closed an import cycle.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from modules.external_data.application.ingestion_records import (
        IngestionRunRecord,
        InMemoryIngestionRunStore,
        LineageRecord,
        QuarantineRecord,
        SourceFreshnessEvidence,
    )
    from modules.external_data.application.xlsx_import import (
        XlsxCommitReceipt,
        XlsxImportError,
        XlsxPreviewResult,
        XlsxRowError,
        commit_xlsx_import,
        export_xlsx_import_errors,
        preview_xlsx_import,
    )

__all__ = [
    "IngestionRunRecord",
    "InMemoryIngestionRunStore",
    "LineageRecord",
    "QuarantineRecord",
    "SourceFreshnessEvidence",
    "XlsxCommitReceipt",
    "XlsxImportError",
    "XlsxPreviewResult",
    "XlsxRowError",
    "commit_xlsx_import",
    "export_xlsx_import_errors",
    "preview_xlsx_import",
]

_RECORD_EXPORTS = {
    "IngestionRunRecord",
    "InMemoryIngestionRunStore",
    "LineageRecord",
    "QuarantineRecord",
    "SourceFreshnessEvidence",
}
_XLSX_EXPORTS = {
    "XlsxCommitReceipt",
    "XlsxImportError",
    "XlsxPreviewResult",
    "XlsxRowError",
    "commit_xlsx_import",
    "export_xlsx_import_errors",
    "preview_xlsx_import",
}


def __getattr__(name: str) -> Any:
    if name in _RECORD_EXPORTS:
        from modules.external_data.application import ingestion_records

        return getattr(ingestion_records, name)
    if name in _XLSX_EXPORTS:
        from modules.external_data.application import xlsx_import

        return getattr(xlsx_import, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
