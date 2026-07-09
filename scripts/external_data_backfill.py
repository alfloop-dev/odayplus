#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.external_data.workers import run_external_fetch_backfill


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an idempotent external data fetch backfill.")
    parser.add_argument("--provider-id", default="listing.partner_feed")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--interval-hours", type=float, default=1.0)
    args = parser.parse_args()

    runs = run_external_fetch_backfill(
        provider_id=args.provider_id,
        start=_parse_datetime(args.start),
        end=_parse_datetime(args.end),
        interval=timedelta(hours=args.interval_hours),
    )
    print(json.dumps([run.to_dict() for run in runs], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
