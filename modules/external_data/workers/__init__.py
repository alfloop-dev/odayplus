"""Legacy external fetch schedulers were decommissioned by XR-CUTOVER-001.

``scheduled_fetch`` — the provider scheduler, its fetch state stores and its
watermark/circuit-breaker state — is gone. ``SourceFreshnessEvidence`` is
re-exported from its retained home so operator freshness views over
pre-cutover runs keep resolving from this package path.
"""

from modules.external_data.application.ingestion_records import SourceFreshnessEvidence

__all__ = ["SourceFreshnessEvidence"]
