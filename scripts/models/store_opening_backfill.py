"""CLI runner for store opening date backfill (ODP-STORE-OPENING-001).

Usage:
    python -m scripts.models.store_opening_backfill \
        --tenant-id <UUID> \
        --snapshot-id <UUID> \
        --input-json <PATH> \
        [--eligible-stores-json <PATH>] \
        [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from apps.data_platform.store_opening import (
    MissingStoreOpeningAuthorityError,
    StoreOpeningBackfillEngine,
    StoreOpeningError,
    TenantIsolationError,
    UnauthoritativeStoreOpeningError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Idempotent, tenant-safe authoritative store opening date backfill."
    )
    parser.add_argument("--tenant-id", required=True, help="Target tenant UUID")
    parser.add_argument("--snapshot-id", required=True, help="Source snapshot lineage UUID")
    parser.add_argument(
        "--input-json",
        required=True,
        help="Path to JSON file containing raw store opening authority records",
    )
    parser.add_argument(
        "--eligible-stores-json",
        help="Path to JSON file containing list of eligible store UUIDs (triggers fail-closed validation)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate records and lineage without mutating core.stores database",
    )
    parser.add_argument(
        "--db-url",
        help="Optional PostgreSQL database URL (defaults to ODAY_DATABASE_URL or ODP_DATABASE_URL env var)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input_json)
    if not input_path.exists():
        print(f"Error: input file {input_path} does not exist", file=sys.stderr)
        return 2

    try:
        raw_data = json.loads(input_path.read_text(encoding="utf-8"))
        records = raw_data if isinstance(raw_data, list) else raw_data.get("records", [])
    except Exception as exc:
        print(f"Error parsing input JSON: {exc}", file=sys.stderr)
        return 2

    eligible_store_ids = None
    if args.eligible_stores_json:
        elig_path = Path(args.eligible_stores_json)
        if not elig_path.exists():
            print(f"Error: eligible stores file {elig_path} does not exist", file=sys.stderr)
            return 2
        try:
            elig_data = json.loads(elig_path.read_text(encoding="utf-8"))
            eligible_store_ids = elig_data if isinstance(elig_data, list) else elig_data.get("store_ids", [])
        except Exception as exc:
            print(f"Error parsing eligible stores JSON: {exc}", file=sys.stderr)
            return 2

    db_conn = None
    db_url = args.db_url or os.getenv("ODAY_DATABASE_URL") or os.getenv("ODP_DATABASE_URL")
    if db_url and "postgresql" in db_url.lower():
        try:
            import psycopg
            db_conn = psycopg.connect(db_url)
        except Exception as exc:
            print(f"Warning: Could not connect to PostgreSQL ({exc}). Operating in dry/in-memory mode.", file=sys.stderr)

    engine = StoreOpeningBackfillEngine(db_conn=db_conn)

    try:
        result = engine.run_backfill(
            snapshot_id=args.snapshot_id,
            tenant_id=args.tenant_id,
            records=records,
            eligible_store_ids=eligible_store_ids,
            dry_run=args.dry_run,
        )
        print(json.dumps(result.to_dict(), indent=2))
        return 0
    except (
        UnauthoritativeStoreOpeningError,
        MissingStoreOpeningAuthorityError,
        TenantIsolationError,
        StoreOpeningError,
    ) as exc:
        print(f"FAIL CLOSED: Store opening backfill rejected: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected backfill error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
