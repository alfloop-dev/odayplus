from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from apps.data_platform.config import DataPlaneConfig
from apps.data_platform.contracts import BackfillWindow, SourceKind
from apps.data_platform.pipeline import DataPlaneRunner
from apps.data_platform.selection import read_limit_for

_LOAD_ORDER = {
    SourceKind.MERCHANT: 10,
    SourceKind.PLACE: 20,
    SourceKind.DEVICE: 30,
    SourceKind.DEVICE_LOG: 35,
    SourceKind.ORDERS: 40,
    SourceKind.TRANSACTION: 41,
    SourceKind.TRADE: 42,
    SourceKind.DEVICE_DAILY_STATISTICS: 50,
    SourceKind.AI_REVENUE_STATS: 60,
    SourceKind.CAMPAIGN: 70,
    SourceKind.PRODUCT: 71,
    SourceKind.PRODUCTS: 72,
    SourceKind.PROMOTIONS: 73,
    SourceKind.AI_CONSUMER_KMEANS_V1: 80,
    SourceKind.MEMBER: 90,
}
BACKFILL_RECEIPT_PREFIX = "ODP_BACKFILL_RECEIPT="


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _windows(
    start: datetime,
    end: datetime,
    *,
    partition_days: int,
    max_partitions: int,
) -> list[BackfillWindow]:
    if not 1 <= partition_days <= 31:
        raise ValueError("--partition-days must be between 1 and 31")
    windows: list[BackfillWindow] = []
    cursor = start
    while cursor < end:
        boundary = min(cursor + timedelta(days=partition_days), end)
        key = f"{cursor.date().isoformat()}__{boundary.date().isoformat()}"
        windows.append(BackfillWindow(cursor, boundary, key))
        if len(windows) > max_partitions:
            raise ValueError("Requested range exceeds --max-partitions")
        cursor = boundary
    return windows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bounded Mongo fongniao_prod to PostgreSQL backfill"
    )
    parser.add_argument(
        "--kind",
        action="append",
        required=True,
        choices=[value.value for value in SourceKind],
    )
    parser.add_argument(
        "--allow-device-log",
        action="store_true",
        help="Explicitly permit bounded device_log reads and minimized raw evidence",
    )
    parser.add_argument("--start", required=True, help="Inclusive ISO-8601 timestamp")
    parser.add_argument("--end", required=True, help="Exclusive ISO-8601 timestamp")
    parser.add_argument("--partition-days", type=int, default=1)
    parser.add_argument("--max-partitions", type=int, default=31)
    parser.add_argument("--max-records", type=int)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument(
        "--allow-trade",
        action="store_true",
        help="Explicitly permit bounded trade reads; never removes date/read limits",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    start, end = _instant(args.start), _instant(args.end)
    windows = _windows(
        start,
        end,
        partition_days=args.partition_days,
        max_partitions=args.max_partitions,
    )
    kinds = sorted(
        {SourceKind(value) for value in args.kind},
        key=_LOAD_ORDER.__getitem__,
    )
    if SourceKind.TRADE in kinds and not args.allow_trade:
        raise SystemExit("trade backfill requires --allow-trade")
    if SourceKind.TRADE in kinds and args.partition_days != 1:
        raise SystemExit("trade backfill requires one-day partitions")
    if SourceKind.DEVICE_LOG in kinds and not args.allow_device_log:
        raise SystemExit("device_log backfill requires --allow-device-log")
    if SourceKind.DEVICE_LOG in kinds and args.partition_days != 1:
        raise SystemExit("device_log backfill requires one-day partitions")
    config = DataPlaneConfig.from_env()
    runner = DataPlaneRunner.from_env()
    results: list[dict[str, object]] = []
    for kind in kinds:
        policy_limit = read_limit_for(kind, config.max_records_per_run)
        limit = min(args.max_records or policy_limit, policy_limit)
        for window in windows:
            summary = runner.run_partition(
                kind,
                window,
                resume=not args.no_resume,
                limit=limit,
            )
            results.append(summary.as_dict())
    payload = {
        "status": "SUCCEEDED",
        "source_database": "fongniao_prod",
        "load_order": [value.value for value in kinds],
        "runs": results,
    }
    print(
        BACKFILL_RECEIPT_PREFIX
        + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
