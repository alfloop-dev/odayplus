"""Durable storage contracts for direct external listing-feed ingestion.

The scheduled ingestion path already persists run summaries.  The direct
backfill adapter additionally needs the exact raw, canonical, and quarantine
snapshots plus an idempotent completion receipt.  This module keeps that data
behind a small repository boundary so production can use the canonical
PostgreSQL document store while local fixture replay can use files.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ListingFeedSnapshot:
    tenant_id: str
    provider_id: str
    snapshot_id: str
    kind: str
    payload: Any
    checksum_sha256: str
    correlation_id: str
    captured_at: datetime
    source_endpoint: str
    media_type: str = "application/json"


@dataclass(frozen=True)
class ListingFeedIngestionReceipt:
    tenant_id: str
    provider_id: str
    idempotency_key: str
    payload_checksum_sha256: str
    snapshot_id: str
    contract_id: str
    status: str
    correlation_id: str
    source_endpoint: str
    observed_at: datetime
    fetched_at: datetime
    completed_at: datetime
    accepted_count: int
    duplicate_count: int
    rejected_count: int
    quarantined_count: int
    raw_snapshot_uri: str
    canonical_snapshot_uri: str
    quarantine_snapshot_uri: str | None

    def to_result(self, *, duplicate: bool = False) -> dict[str, Any]:
        return {
            "status": "duplicate" if duplicate else self.status,
            "idempotency_key": self.idempotency_key,
            "payload_checksum_sha256": self.payload_checksum_sha256,
            "snapshot_id": self.snapshot_id,
            "contract_id": self.contract_id,
            "provider_id": self.provider_id,
            "tenant_id": self.tenant_id,
            "correlation_id": self.correlation_id,
            "source_endpoint": self.source_endpoint,
            "observed_at": self.observed_at.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "accepted_count": self.accepted_count,
            "duplicate_count": self.duplicate_count,
            "rejected_count": self.rejected_count,
            "quarantined_count": self.quarantined_count,
            "raw_snapshot_uri": self.raw_snapshot_uri,
            "canonical_snapshot_uri": self.canonical_snapshot_uri,
            "quarantine_snapshot_uri": self.quarantine_snapshot_uri,
            # Compatibility aliases for the old file-only CLI report.
            "raw_snapshot_path": self.raw_snapshot_uri,
            "canonical_snapshot_path": self.canonical_snapshot_uri,
            "quarantine_path": self.quarantine_snapshot_uri,
            "message": (
                "Listing payload has already been processed."
                if duplicate
                else "Listing payload was durably ingested."
            ),
        }


class ListingFeedIngestionStore(Protocol):
    is_durable: bool

    def get_receipt(
        self,
        *,
        tenant_id: str,
        provider_id: str,
        idempotency_key: str,
    ) -> ListingFeedIngestionReceipt | None: ...

    def save_snapshot(self, snapshot: ListingFeedSnapshot) -> str: ...

    def get_snapshot(
        self,
        *,
        tenant_id: str,
        provider_id: str,
        snapshot_id: str,
        kind: str,
    ) -> ListingFeedSnapshot | None: ...

    def save_receipt(
        self,
        receipt: ListingFeedIngestionReceipt,
    ) -> ListingFeedIngestionReceipt: ...


def _scope_key(tenant_id: str, provider_id: str, value: str) -> str:
    return hashlib.sha256(
        f"{tenant_id}\0{provider_id}\0{value}".encode()
    ).hexdigest()


class DocumentListingFeedIngestionStore:
    """Persist listing snapshots and receipts in the canonical document store."""

    is_durable = True
    _SNAPSHOTS = "external_data.listing_feed_snapshots"
    _RECEIPTS = "external_data.listing_feed_receipts"

    def __init__(self, document_store: Any) -> None:
        self._store = document_store

    def get_receipt(
        self,
        *,
        tenant_id: str,
        provider_id: str,
        idempotency_key: str,
    ) -> ListingFeedIngestionReceipt | None:
        return self._store.get(
            self._RECEIPTS,
            _scope_key(tenant_id, provider_id, idempotency_key),
        )

    def save_snapshot(self, snapshot: ListingFeedSnapshot) -> str:
        document_id = _scope_key(
            snapshot.tenant_id,
            snapshot.provider_id,
            f"{snapshot.snapshot_id}:{snapshot.kind}",
        )
        self._store.put(
            self._SNAPSHOTS,
            document_id,
            snapshot,
            group_key=f"{snapshot.tenant_id}:{snapshot.provider_id}",
            correlation_id=snapshot.correlation_id,
        )
        return f"document://{self._SNAPSHOTS}/{document_id}"

    def get_snapshot(
        self,
        *,
        tenant_id: str,
        provider_id: str,
        snapshot_id: str,
        kind: str,
    ) -> ListingFeedSnapshot | None:
        return self._store.get(
            self._SNAPSHOTS,
            _scope_key(tenant_id, provider_id, f"{snapshot_id}:{kind}"),
        )

    def save_receipt(
        self,
        receipt: ListingFeedIngestionReceipt,
    ) -> ListingFeedIngestionReceipt:
        self._store.put(
            self._RECEIPTS,
            _scope_key(
                receipt.tenant_id,
                receipt.provider_id,
                receipt.idempotency_key,
            ),
            receipt,
            group_key=f"{receipt.tenant_id}:{receipt.provider_id}",
            correlation_id=receipt.correlation_id,
        )
        return receipt


class FileListingFeedIngestionStore:
    """JSON-backed fixture store. It is intentionally not valid for live mode."""

    is_durable = False

    def __init__(self, snapshot_dir: str | Path, quarantine_dir: str | Path) -> None:
        self.snapshot_dir = Path(snapshot_dir)
        self.quarantine_dir = Path(quarantine_dir)
        self.receipt_dir = self.snapshot_dir / "receipts"
        for directory in (self.snapshot_dir, self.quarantine_dir, self.receipt_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def get_receipt(
        self,
        *,
        tenant_id: str,
        provider_id: str,
        idempotency_key: str,
    ) -> ListingFeedIngestionReceipt | None:
        path = self.receipt_dir / (
            _scope_key(tenant_id, provider_id, idempotency_key) + ".json"
        )
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ListingFeedIngestionReceipt(
            **{
                **payload,
                "observed_at": datetime.fromisoformat(payload["observed_at"]),
                "fetched_at": datetime.fromisoformat(payload["fetched_at"]),
                "completed_at": datetime.fromisoformat(payload["completed_at"]),
            }
        )

    def save_snapshot(self, snapshot: ListingFeedSnapshot) -> str:
        directory = (
            self.quarantine_dir
            if snapshot.kind == "quarantine"
            else self.snapshot_dir
        )
        path = directory / f"{snapshot.kind}_{snapshot.snapshot_id}.json"
        path.write_text(
            json.dumps(snapshot.payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return str(path)

    def get_snapshot(
        self,
        *,
        tenant_id: str,
        provider_id: str,
        snapshot_id: str,
        kind: str,
    ) -> ListingFeedSnapshot | None:
        del tenant_id, provider_id
        directory = self.quarantine_dir if kind == "quarantine" else self.snapshot_dir
        path = directory / f"{kind}_{snapshot_id}.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ListingFeedSnapshot(
            tenant_id="local",
            provider_id="fixture",
            snapshot_id=snapshot_id,
            kind=kind,
            payload=payload,
            checksum_sha256=hashlib.sha256(
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    default=str,
                ).encode()
            ).hexdigest(),
            correlation_id="fixture-reload",
            captured_at=datetime.fromtimestamp(path.stat().st_mtime).astimezone(),
            source_endpoint="fixture://local-replay",
        )

    def save_receipt(
        self,
        receipt: ListingFeedIngestionReceipt,
    ) -> ListingFeedIngestionReceipt:
        path = self.receipt_dir / (
            _scope_key(
                receipt.tenant_id,
                receipt.provider_id,
                receipt.idempotency_key,
            )
            + ".json"
        )
        path.write_text(
            json.dumps(asdict(receipt), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return receipt


__all__ = [
    "DocumentListingFeedIngestionStore",
    "FileListingFeedIngestionStore",
    "ListingFeedIngestionReceipt",
    "ListingFeedIngestionStore",
    "ListingFeedSnapshot",
]
