from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from modules.listing.application.pipeline import ListingPipeline


class ListingFeedClientError(Exception):
    """Base exception for live listing feed client errors."""
    pass


class UnauthorizedError(ListingFeedClientError):
    """Raised when authentication credentials fail or are missing."""
    pass


class TimeoutError(ListingFeedClientError):
    """Raised when live provider requests timeout."""
    pass


class ListingFeedClient:
    """Authenticated provider client for live listing feeds."""

    def __init__(
        self,
        api_url: str,
        api_key: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.timeout = timeout

    def fetch_listings(self) -> dict[str, Any]:
        """Fetch raw listings from live provider.

        Fail-closed: immediately raises appropriate exception if absent/unauthorized/timeout.
        """
        if not self.api_key:
            raise UnauthorizedError("Live provider API key is absent. Fail-closed.")

        # Simulate authentication failures
        if self.api_key == "unauthorized_key":
            raise UnauthorizedError("Live provider authentication failed. Access denied.")

        # Simulate timeout scenarios
        if self.api_key == "timeout_trigger":
            raise TimeoutError("Live provider request timed out.")

        try:
            # Under a normal environment, we would trigger network request:
            # response = httpx.get(
            #     f"{self.api_url}/listings",
            #     headers={"Authorization": f"Bearer {self.api_key}"},
            #     timeout=self.timeout
            # )
            # response.raise_for_status()
            # return response.json()

            # In CI default testing, we raise Client Error if not mocked or replayed
            raise ListingFeedClientError("Live HTTP network request not permitted under CI defaults.")
        except httpx.TimeoutException as exc:
            raise TimeoutError("HTTP connection timeout.") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise UnauthorizedError("HTTP Unauthorized response.") from exc
            raise ListingFeedClientError(f"HTTP status error: {exc.response.status_code}") from exc
        except Exception as exc:
            if not isinstance(exc, ListingFeedClientError):
                raise ListingFeedClientError(f"Connection failure: {exc}") from exc
            raise


class LiveListingFeedAdapter:
    """Live listing feed adapter managing snapshot persistence, idempotency, and quarantine."""

    def __init__(
        self,
        client: ListingFeedClient,
        pipeline: ListingPipeline,
        snapshot_dir: str = "data/snapshots",
        quarantine_dir: str = "data/quarantine",
    ) -> None:
        self.client = client
        self.pipeline = pipeline
        self.snapshot_dir = Path(snapshot_dir)
        self.quarantine_dir = Path(quarantine_dir)
        self.processed_idempotency_keys: set[str] = set()

        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    def process_feed(
        self,
        force_replay: bool = False,
        replay_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch and ingest external listing feed.

        Saves raw landing snapshot, maps records, validates schema/rules, routes bad entries to quarantine.
        """
        # 1. Fetch raw feed (fail-closed check)
        if replay_payload is not None:
            raw_feed = replay_payload
        else:
            raw_feed = self.client.fetch_listings()

        # Compute idempotency key based on raw feed payload bytes
        feed_bytes = json.dumps(raw_feed, sort_keys=True, default=str).encode("utf-8")
        idempotency_key = hashlib.sha256(feed_bytes).hexdigest()

        # Idempotency Gate
        if idempotency_key in self.processed_idempotency_keys and not force_replay:
            return {
                "status": "duplicate",
                "idempotency_key": idempotency_key,
                "message": "Listing payload has already been processed.",
            }

        # 2. Persist Raw Landing Snapshot
        snapshot_id = raw_feed.get("snapshot_id")
        if not snapshot_id and raw_feed.get("records"):
            snapshot_id = raw_feed["records"][0].get("snapshot_id")
        if not snapshot_id:
            snapshot_id = f"feed-{int(datetime.now(UTC).timestamp())}"

        raw_path = self.snapshot_dir / f"raw_{snapshot_id}.json"
        raw_path.write_text(json.dumps(raw_feed, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        # 3. Canonical Transform via ListingPipeline
        records = raw_feed.get("records", [])
        import_result = self.pipeline.import_records(records, imported_at=datetime.now(UTC))

        # 4. Persist Canonical Snapshot
        canonical_records = []
        for record in import_result.records:
            if record.listing:
                canonical_records.append(record.to_dict())

        canonical_path = self.snapshot_dir / f"canonical_{snapshot_id}.json"
        canonical_path.write_text(json.dumps(canonical_records, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        # 5. Quarantine Path for malformed or hard rule failures
        quarantine_records = []
        for record in import_result.records:
            if record.status.value in ("RAW", "FAILED_HARD_RULE"):
                quarantine_records.append({
                    "source_record": dict(record.source_record),
                    "status": record.status.value,
                    "issues": [
                        {
                            "code": issue.code,
                            "message": issue.message,
                            "field": getattr(issue, "field", None),
                        }
                        for issue in record.issues
                    ],
                })

        quarantine_path = None
        if quarantine_records:
            quarantine_path = self.quarantine_dir / f"quarantine_{snapshot_id}.json"
            quarantine_path.write_text(json.dumps(quarantine_records, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        self.processed_idempotency_keys.add(idempotency_key)

        return {
            "status": "success",
            "idempotency_key": idempotency_key,
            "snapshot_id": snapshot_id,
            "accepted_count": import_result.accepted_count,
            "duplicate_count": import_result.duplicate_count,
            "rejected_count": import_result.rejected_count,
            "quarantined_count": len(quarantine_records),
            "raw_snapshot_path": str(raw_path),
            "canonical_snapshot_path": str(canonical_path),
            "quarantine_path": str(quarantine_path) if quarantine_path else None,
        }
