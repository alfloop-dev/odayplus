from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class SourceKind(StrEnum):
    MERCHANT = "merchant"
    PLACE = "place"
    DEVICE = "device"
    DEVICE_DAILY_STATISTICS = "device_daily_statistics"
    TRANSACTION = "transaction"
    TRADE = "trade"
    ORDERS = "orders"
    AI_REVENUE_STATS = "ai_revenue_stats"
    CAMPAIGN = "campaign"
    PRODUCT = "product"
    PRODUCTS = "products"
    PROMOTIONS = "promotions"
    AI_CONSUMER_KMEANS_V1 = "ai_consumer_kmeans_v1"
    MEMBER = "member"
    DEVICE_LOG = "device_log"


class QuarantineReason(StrEnum):
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_DATETIME = "INVALID_DATETIME"
    INVALID_COORDINATES = "INVALID_COORDINATES"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    UNSUPPORTED_STATUS = "UNSUPPORTED_STATUS"
    MISSING_MERCHANT_MAPPING = "MISSING_MERCHANT_MAPPING"
    MISSING_PLACE_MAPPING = "MISSING_PLACE_MAPPING"
    TENANT_OWNERSHIP_MISMATCH = "TENANT_OWNERSHIP_MISMATCH"
    MISSING_DEVICE_MAPPING = "MISSING_DEVICE_MAPPING"
    SENSITIVE_MEMBER_EXCLUDED = "SENSITIVE_MEMBER_EXCLUDED"
    EVENT_TIME_EPOCH_OUTLIER = "EVENT_TIME_EPOCH_OUTLIER"
    EVENT_TIME_FUTURE_OUTLIER = "EVENT_TIME_FUTURE_OUTLIER"
    STATUS_MAPPING_UNAPPROVED = "STATUS_MAPPING_UNAPPROVED"
    MISSING_AUTHORITATIVE_PAYMENT = "MISSING_AUTHORITATIVE_PAYMENT"
    SOURCE_SUPERSEDED = "SOURCE_SUPERSEDED"
    OPERATION_MAPPING_UNAPPROVED = "OPERATION_MAPPING_UNAPPROVED"
    TYPE_MAPPING_UNAPPROVED = "TYPE_MAPPING_UNAPPROVED"
    CONNECTION_MAPPING_UNAPPROVED = "CONNECTION_MAPPING_UNAPPROVED"
    NON_CANONICAL_LOG_TYPE = "NON_CANONICAL_LOG_TYPE"
    INVALID_LOG_EVIDENCE = "INVALID_LOG_EVIDENCE"


@dataclass(frozen=True)
class BackfillWindow:
    start: datetime
    end: datetime
    partition_key: str

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Backfill windows must use timezone-aware datetimes")
        if self.end <= self.start:
            raise ValueError("Backfill window end must be after start")
        if not self.partition_key.strip():
            raise ValueError("partition_key is required")


@dataclass(frozen=True)
class SourceEnvelope:
    source_kind: SourceKind
    source_id: str
    source_document: dict[str, Any]
    source_updated_at: datetime | None
    observed_at: datetime
    source_snapshot_id: str
    content_sha256: str
    run_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "source_id",
            "source_snapshot_id",
            "content_sha256",
            "run_id",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        if (
            self.source_updated_at is not None
            and self.source_updated_at.tzinfo is None
        ) or self.observed_at.tzinfo is None:
            raise ValueError("Envelope timestamps must be timezone aware")

    def as_raw_record(self) -> dict[str, Any]:
        return {
            "source_snapshot_id": self.source_snapshot_id,
            "source_kind": self.source_kind.value,
            "source_id": self.source_id,
            "source_document": self.source_document,
            "source_updated_at": self.source_updated_at,
            "observed_at": self.observed_at,
            "content_sha256": self.content_sha256,
            "run_id": self.run_id,
        }


@dataclass(frozen=True)
class ProjectionBatchResult:
    valid_snapshot_checksums: tuple[str, ...]
    quarantine_reason_counts: dict[str, int]

    @property
    def valid_loaded(self) -> int:
        return len(self.valid_snapshot_checksums)

    @property
    def quarantined_count(self) -> int:
        return sum(self.quarantine_reason_counts.values())


@dataclass(frozen=True)
class ReconciliationResult:
    source_total: int
    valid_loaded: int
    quarantined_count: int
    raw_count: int
    canonical_count: int
    source_checksum: str
    raw_checksum: str
    valid_checksum: str
    canonical_checksum: str
    quarantine_reason_counts: dict[str, int] = field(default_factory=dict)

    @property
    def reconciled(self) -> bool:
        return (
            self.source_total == self.valid_loaded + self.quarantined_count
            and self.source_total == self.raw_count
            and self.valid_loaded == self.canonical_count
            and self.source_checksum == self.raw_checksum
            and self.valid_checksum == self.canonical_checksum
            and sum(self.quarantine_reason_counts.values()) == self.quarantined_count
        )


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    source_kind: SourceKind
    partition_key: str
    processed_count: int
    resumed_from: str | None
    final_cursor: str | None
    reconciliation: ReconciliationResult
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def status(self) -> str:
        return "SUCCEEDED" if self.reconciliation.reconciled else "RECONCILIATION_FAILED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "source_kind": self.source_kind.value,
            "partition_key": self.partition_key,
            "processed_count": self.processed_count,
            "resumed_from": self.resumed_from,
            "final_cursor": self.final_cursor,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "reconciliation": {
                "source_total": self.reconciliation.source_total,
                "valid_loaded": self.reconciliation.valid_loaded,
                "quarantined_count": self.reconciliation.quarantined_count,
                "raw_count": self.reconciliation.raw_count,
                "canonical_count": self.reconciliation.canonical_count,
                "source_checksum": self.reconciliation.source_checksum,
                "raw_checksum": self.reconciliation.raw_checksum,
                "valid_checksum": self.reconciliation.valid_checksum,
                "canonical_checksum": self.reconciliation.canonical_checksum,
                "quarantine_reason_counts": dict(
                    self.reconciliation.quarantine_reason_counts
                ),
                "reconciled": self.reconciliation.reconciled,
            },
        }
