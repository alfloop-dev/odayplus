#!/usr/bin/env python3
"""CLI utility for running external data backfill jobs and ingestion.

Supports both:
1. Scheduled fetch backfill for registered external providers (dev baseline).
2. Direct listing-feed backfill with fixture-only files or canonical durable
   live persistence (ODP-EXT-002).
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
    FeedSchemaError,
    ListingFeedClient,
    ListingFeedClientError,
    ListingFeedConfigurationError,
    LiveListingFeedAdapter,
    RateLimitError,
    TimeoutError,
    TransportError,
    UnauthorizedError,
    UpstreamError,
)
from modules.external_data.application.listing_feed_store import (
    DocumentListingFeedIngestionStore,
)
from modules.external_data.connectors.provider_registry import (
    ExternalProviderConfigError,
    validate_external_providers_or_raise,
)
from modules.external_data.geo import GeocodeCandidate, GeoPipeline, StaticGeocodeProvider
from modules.external_data.workers import run_external_fetch_backfill
from modules.listing.application.pipeline import ListingPipeline
from modules.listing.infrastructure.repositories import InMemoryListingRepository
from shared.infrastructure.persistence.factory import build_persistence


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
        default=os.environ.get("ODP_LISTING_PROVIDER_API_KEY", ""),
        help="API key for live provider access; prefer the environment variable.",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=os.environ.get("ODP_LISTING_PROVIDER_FEED_URL", ""),
        help="Exact approved listing feed URL; prefer the environment variable.",
    )
    parser.add_argument(
        "--tenant-id",
        type=str,
        default=os.environ.get("ODP_TENANT_ID", ""),
        help="Tenant scope for live canonical persistence.",
    )
    parser.add_argument(
        "--persistence",
        type=str,
        default=os.environ.get("ODP_PERSISTENCE", "memory"),
        choices=["memory", "durable", "sqlite", "postgres", "postgresql"],
        help="Persistence backend. Live mode rejects memory.",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=os.environ.get("ODP_DB_PATH", ""),
        help="SQLite path for local durable contract runs; prohibited for PostgreSQL.",
    )
    parser.add_argument(
        "--idempotency-key",
        type=str,
        default="",
        help="Optional caller idempotency key; payload checksum is used when omitted.",
    )
    parser.add_argument(
        "--correlation-id",
        type=str,
        default="",
        help="Optional caller correlation ID.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.environ.get("ODP_LISTING_PROVIDER_TIMEOUT_SECONDS", "10")),
    )
    parser.add_argument(
        "--max-response-bytes",
        type=int,
        default=int(os.environ.get("ODP_LISTING_PROVIDER_MAX_RESPONSE_BYTES", str(10 * 1024 * 1024))),
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=int(os.environ.get("ODP_LISTING_PROVIDER_MAX_RECORDS", "10000")),
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
        return 0 if all(run.status == "SUCCEEDED" for run in runs) else 10

    # Otherwise, run local ListingFeedAdapter backfill
    print(f"Starting {args.source} backfill in '{args.mode}' mode...")

    persistence = None
    if args.mode == "fixture":
        repository = InMemoryListingRepository()
        pipeline = ListingPipeline(
            repository=repository,
            geo_pipeline=get_default_geo_pipeline(),
        )
        client = ListingFeedClient(api_url="", api_key="")
        adapter = LiveListingFeedAdapter(
            client=client,
            pipeline=pipeline,
            snapshot_dir=args.snapshot_dir,
            quarantine_dir=args.quarantine_dir,
            mode="fixture",
        )
    else:
        try:
            persistence, adapter = _build_live_adapter(args)
        except (
            ExternalProviderConfigError,
            ListingFeedConfigurationError,
            RuntimeError,
            ValueError,
        ) as exc:
            print(f"FAIL-CLOSED: Live ingestion configuration is invalid: {exc}", file=sys.stderr)
            return 6

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
            result = adapter.process_feed(
                correlation_id=args.correlation_id or None,
                idempotency_key=args.idempotency_key or None,
            )
        except UnauthorizedError as exc:
            print(f"FAIL-CLOSED: Unauthorized access to live provider: {exc}", file=sys.stderr)
            return 2
        except TimeoutError as exc:
            print(f"FAIL-CLOSED: Live provider connection timed out: {exc}", file=sys.stderr)
            return 3
        except RateLimitError as exc:
            print(f"FAIL-CLOSED: Live provider rate limit reached: {exc}", file=sys.stderr)
            return 7
        except (UpstreamError, TransportError) as exc:
            print(f"FAIL-CLOSED: Live provider is unavailable: {exc}", file=sys.stderr)
            return 8
        except FeedSchemaError as exc:
            print(f"FAIL-CLOSED: Live provider schema is invalid: {exc}", file=sys.stderr)
            return 9
        except ListingFeedClientError as exc:
            print(f"FAIL-CLOSED: Live provider client encountered error: {exc}", file=sys.stderr)
            return 4
        except Exception as exc:
            print(f"FAIL-CLOSED: Unexpected pipeline failure: {exc}", file=sys.stderr)
            return 5
        finally:
            if persistence is not None and persistence.engine is not None:
                persistence.engine.close()

    # Print run summary
    print("\n=== Backfill Execution Report ===")
    print(f"Ingestion Status:   {result['status']}")
    print(f"Idempotency Key:    {result['idempotency_key']}")
    print(f"Snapshot ID:        {result['snapshot_id']}")
    print(f"Accepted Records:   {result['accepted_count']}")
    print(f"Duplicate Records:  {result['duplicate_count']}")
    print(f"Rejected Records:   {result['rejected_count']}")
    print(f"Quarantined Items:  {result['quarantined_count']}")
    print(f"Correlation ID:     {result['correlation_id']}")
    print(f"Payload Checksum:   {result['payload_checksum_sha256']}")
    print(f"Raw Snapshot URI:   {result['raw_snapshot_uri']}")
    print(f"Canonical URI:      {result['canonical_snapshot_uri']}")
    if result["quarantine_snapshot_uri"]:
        print(f"Quarantine URI:     {result['quarantine_snapshot_uri']}")
    print("=================================\n")

    # Save evidence report
    evidence_dir = Path(args.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    evidence_file = evidence_dir / "ODP-EXT-002_BACKFILL_EVIDENCE.json"
    evidence_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Successfully saved execution evidence to {evidence_file}")

    return 0


def _build_live_adapter(args: argparse.Namespace) -> tuple[object, LiveListingFeedAdapter]:
    deploy_env = (
        os.environ.get("ODP_DEPLOY_ENV")
        or os.environ.get("ODP_ENV")
        or os.environ.get("ODAY_ENV")
        or os.environ.get("APP_ENV")
        or "development"
    ).strip().lower()
    production = deploy_env in {"prod", "production"}
    persistence_mode = args.persistence.strip().lower()
    if persistence_mode == "memory":
        raise ListingFeedConfigurationError(
            "Live ingestion prohibits in-memory persistence."
        )
    if production and persistence_mode not in {"postgres", "postgresql"}:
        raise ListingFeedConfigurationError(
            "Production live ingestion requires PostgreSQL persistence."
        )
    if not args.tenant_id.strip():
        raise ListingFeedConfigurationError(
            "ODP_TENANT_ID or --tenant-id is required for live ingestion."
        )
    if args.provider_id != "listing.partner_feed":
        raise ListingFeedConfigurationError(
            "Direct listing backfill only accepts provider listing.partner_feed."
        )

    selected_providers = {
        provider_id.strip()
        for provider_id in os.environ.get(
            "ODP_PRODUCTION_PROVIDER_IDS",
            "",
        ).split(",")
        if provider_id.strip()
    }
    if production and not selected_providers:
        raise ListingFeedConfigurationError(
            "Production live ingestion requires ODP_PRODUCTION_PROVIDER_IDS."
        )
    if selected_providers and args.provider_id not in selected_providers:
        raise ListingFeedConfigurationError(
            f"Provider {args.provider_id!r} is not selected for live ingestion."
        )

    approved_endpoint = os.environ.get("ODP_LISTING_PROVIDER_FEED_URL", "").strip()
    if not approved_endpoint:
        raise ListingFeedConfigurationError(
            "ODP_LISTING_PROVIDER_FEED_URL is required for live ingestion."
        )
    if args.api_url.strip() != approved_endpoint:
        raise ListingFeedConfigurationError(
            "The requested endpoint does not match ODP_LISTING_PROVIDER_FEED_URL."
        )

    validation_env = dict(os.environ)
    validation_env["ODP_EXTERNAL_PROVIDER_MODE"] = "live"
    validation_env["ODP_PRODUCTION_PROVIDER_IDS"] = args.provider_id
    validation = validate_external_providers_or_raise(
        env=validation_env,
        mode="live",
        correlation_id=args.correlation_id or None,
    )
    if not any(provider.provider_id == args.provider_id for provider in validation.providers):
        raise ListingFeedConfigurationError(
            f"Provider {args.provider_id!r} is not approved for live ingestion."
        )

    db_path = args.db_path or None
    if persistence_mode in {"postgres", "postgresql"} and db_path is not None:
        raise ListingFeedConfigurationError(
            "--db-path is not valid for PostgreSQL persistence."
        )
    persistence = build_persistence(
        mode=persistence_mode,
        db_path=db_path,
    )
    if persistence.mode == "postgresql":
        from shared.infrastructure.persistence.postgresql import PostgresDocumentStore

        document_store = PostgresDocumentStore(persistence.engine)
    else:
        from shared.infrastructure.persistence.document_store import SqliteDocumentStore

        document_store = SqliteDocumentStore(persistence.engine)

    allow_insecure_localhost = (
        not production
        and os.environ.get("ODP_ALLOW_INSECURE_LOCAL_PROVIDER", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    client = ListingFeedClient(
        api_url=args.api_url,
        api_key=args.api_key,
        timeout=args.timeout_seconds,
        approved_endpoint_url=approved_endpoint,
        max_response_bytes=args.max_response_bytes,
        max_records=args.max_records,
        allow_insecure_localhost=allow_insecure_localhost,
    )
    pipeline = ListingPipeline(
        repository=persistence.listing_repository,
        # Live records use approved provider coordinates or stay unresolved;
        # static fixture geocoding is intentionally not composed here.
        geo_pipeline=GeoPipeline(),
    )
    return persistence, LiveListingFeedAdapter(
        client=client,
        pipeline=pipeline,
        store=DocumentListingFeedIngestionStore(document_store),
        mode="live",
        tenant_id=args.tenant_id,
        provider_id=args.provider_id,
    )


if __name__ == "__main__":
    sys.exit(main())
