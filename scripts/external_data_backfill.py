#!/usr/bin/env python3
"""CLI utility for running external data backfill jobs and ingestion.

Supports both:
1. Scheduled fetch backfill for registered external providers (dev baseline).
2. Direct local ListingFeedAdapter backfill with snapshots and quarantine path (ODP-EXT-002).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.external_data.application.listing_feed_adapter import (
    ListingFeedClient,
    ListingFeedClientError,
    LiveListingFeedAdapter,
    TimeoutError,
    UnauthorizedError,
)
from modules.external_data.geo import GeocodeCandidate, GeoPipeline, StaticGeocodeProvider
from modules.external_data.workers import run_external_fetch_backfill
from modules.listing.application.pipeline import ListingPipeline
from modules.listing.infrastructure.repositories import InMemoryListingRepository


def load_fixture_data(filename: str) -> dict:
    fixture_path = ROOT / "tests" / "fixtures" / "source_data" / "external" / filename
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture file not found: {fixture_path}")
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def get_default_geo_pipeline() -> GeoPipeline:
    # A basic in-memory geocoder mapping coordinates for standard fixtures
    return GeoPipeline(
        StaticGeocodeProvider(
            {
                "台北市大安區復興南路二段100號": GeocodeCandidate(
                    latitude=25.026,
                    longitude=121.543,
                    precision="rooftop",
                    confidence=0.92,
                    provider="fixture",
                    admin_city="台北市",
                    admin_district="大安區",
                ),
                "新北市板橋區中山路一段50號": GeocodeCandidate(
                    latitude=25.008,
                    longitude=121.462,
                    precision="rooftop",
                    confidence=0.95,
                    provider="fixture",
                    admin_city="新北市",
                    admin_district="板橋區",
                ),
            }
        )
    )


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def main() -> int:
    parser = argparse.ArgumentParser(description="External data backfill and ingestion utility.")

    # 1. Scheduled fetch backfill arguments (from dev)
    parser.add_argument("--provider-id", default="listing.partner_feed")
    parser.add_argument("--start", default=None, help="Start time for scheduled fetch backfill (ISO format).")
    parser.add_argument("--end", default=None, help="End time for scheduled fetch backfill (ISO format).")
    parser.add_argument("--interval-hours", type=float, default=1.0)

    # 2. Local ListingFeedAdapter backfill arguments (from ODP-EXT-002)
    parser.add_argument(
        "--source",
        type=str,
        default="listing",
        choices=["listing"],
        help="External source dataset to backfill.",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="fixture",
        choices=["fixture", "live"],
        help="Execution mode: 'fixture' replay or 'live' API fetching.",
    )
    parser.add_argument(
        "--snapshot-id",
        type=str,
        default="",
        help="Custom snapshot ID for the ingestion batch.",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.environ.get("LIVE_PROVIDER_API_KEY", ""),
        help="API Key for live provider access.",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default="https://api.external-provider.com/v1",
        help="API URL base for the live provider.",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=str,
        default=str(ROOT / "data" / "snapshots"),
        help="Directory to persist raw and canonical snapshots.",
    )
    parser.add_argument(
        "--quarantine-dir",
        type=str,
        default=str(ROOT / "data" / "quarantine"),
        help="Directory to write quarantined bad records.",
    )
    parser.add_argument(
        "--evidence-dir",
        type=str,
        default=str(ROOT / "docs" / "evidence" / "completion"),
        help="Directory to write final completion and verification evidence reports.",
    )

    args = parser.parse_args()

    # Route execution based on inputs
    if args.start is not None:
        if args.end is None:
            parser.error("--end is required when running scheduled fetch backfill (with --start)")
        runs = run_external_fetch_backfill(
            provider_id=args.provider_id,
            start=_parse_datetime(args.start),
            end=_parse_datetime(args.end),
            interval=timedelta(hours=args.interval_hours),
        )
        print(json.dumps([run.to_dict() for run in runs], ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    # Otherwise, run local ListingFeedAdapter backfill
    print(f"Starting {args.source} backfill in '{args.mode}' mode...")

    # Initialize dependencies
    repository = InMemoryListingRepository()
    geo_pipeline = get_default_geo_pipeline()
    pipeline = ListingPipeline(repository=repository, geo_pipeline=geo_pipeline)

    client = ListingFeedClient(
        api_url=args.api_url,
        api_key=args.api_key or "fixture_default" if args.mode == "fixture" else args.api_key,
    )

    adapter = LiveListingFeedAdapter(
        client=client,
        pipeline=pipeline,
        snapshot_dir=args.snapshot_dir,
        quarantine_dir=args.quarantine_dir,
    )

    # In fixture mode, we load standard valid + invalid fixtures and run them
    if args.mode == "fixture":
        try:
            valid_payload = load_fixture_data("listing_raw_snapshot.valid.json")
            invalid_payload = load_fixture_data("listing_raw_snapshot.invalid.json")

            # Combine records into one feed payload
            combined_records = list(valid_payload.get("records", []))
            for case in invalid_payload.get("cases", []):
                combined_records.append(case.get("record"))

            snapshot_id = args.snapshot_id or valid_payload.get("snapshot_id") or "listing-fixture-replay"
            feed_payload = {
                "contract_id": "listing_raw_snapshot",
                "snapshot_id": snapshot_id,
                "records": combined_records,
            }

            result = adapter.process_feed(replay_payload=feed_payload)
        except Exception as exc:
            print(f"Error executing fixture-compatible replay: {exc}", file=sys.stderr)
            return 1
    else:
        # Live mode
        try:
            result = adapter.process_feed()
        except UnauthorizedError as exc:
            print(f"FAIL-CLOSED: Unauthorized access to live provider: {exc}", file=sys.stderr)
            return 2
        except TimeoutError as exc:
            print(f"FAIL-CLOSED: Live provider connection timed out: {exc}", file=sys.stderr)
            return 3
        except ListingFeedClientError as exc:
            print(f"FAIL-CLOSED: Live provider client encountered error: {exc}", file=sys.stderr)
            return 4
        except Exception as exc:
            print(f"FAIL-CLOSED: Unexpected pipeline failure: {exc}", file=sys.stderr)
            return 5

    # Print run summary
    print("\n=== Backfill Execution Report ===")
    print(f"Ingestion Status:   {result['status']}")
    print(f"Idempotency Key:    {result['idempotency_key']}")
    print(f"Snapshot ID:        {result['snapshot_id']}")
    print(f"Accepted Records:   {result['accepted_count']}")
    print(f"Duplicate Records:  {result['duplicate_count']}")
    print(f"Rejected Records:   {result['rejected_count']}")
    print(f"Quarantined Items:  {result['quarantined_count']}")
    print(f"Raw Landing Path:   {result['raw_snapshot_path']}")
    print(f"Canonical Path:     {result['canonical_snapshot_path']}")
    if result["quarantine_path"]:
        print(f"Quarantine Path:    {result['quarantine_path']}")
    print("=================================\n")

    # Save evidence report
    evidence_dir = Path(args.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    evidence_file = evidence_dir / "ODP-EXT-002_BACKFILL_EVIDENCE.json"
    evidence_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Successfully saved execution evidence to {evidence_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
