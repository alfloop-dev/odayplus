from __future__ import annotations

import pytest
from dagster import build_schedule_context

from apps.data_platform.contracts import SourceKind
from apps.data_platform.definitions import (
    bounded_device_log,
    defs,
    device_log_job,
    dimension_schedule,
    trade_job,
)
from apps.data_platform.selection import SOURCE_SELECTION, read_limit_for
from scripts.data_platform.backfill import _windows, build_parser


def test_dagster_repository_loads_all_typed_assets() -> None:
    keys = {
        key.to_user_string()
        for key in defs.resolve_asset_graph().get_all_asset_keys()
    }
    assert bounded_device_log.key.to_user_string() in keys
    assert len(keys) == len(SourceKind)


def test_large_sources_are_manual_and_bounded() -> None:
    device_log = SOURCE_SELECTION[SourceKind.DEVICE_LOG]
    trade = SOURCE_SELECTION[SourceKind.TRADE]
    assert device_log.default_scheduled is False
    assert device_log.max_records_per_run == 100_000
    assert trade.default_scheduled is False
    assert trade.max_records_per_run == 100_000
    assert device_log_job.name.endswith("_manual")
    assert trade_job.name.endswith("_manual")
    assert read_limit_for(SourceKind.DEVICE_LOG, 250_000) == 100_000


def test_daily_schedule_carries_previous_day_partition_key() -> None:
    from datetime import UTC, datetime

    context = build_schedule_context(
        scheduled_execution_time=datetime(2026, 7, 24, 1, tzinfo=UTC),
        repository_def=defs.get_repository_def(),
    )
    tick = dimension_schedule.evaluate_tick(context)
    assert [request.partition_key for request in tick.run_requests] == [
        "2026-07-23"
    ]


def test_backfill_cli_requires_explicit_large_source_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--kind",
            "device_log",
            "--start",
            "2026-07-23T00:00:00Z",
            "--end",
            "2026-07-24T00:00:00Z",
            "--allow-device-log",
        ]
    )
    assert args.allow_device_log is True
    assert args.allow_trade is False


def test_backfill_range_is_partition_bounded() -> None:
    from datetime import UTC, datetime

    with pytest.raises(ValueError, match="max-partitions"):
        _windows(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 3, 1, tzinfo=UTC),
            partition_days=1,
            max_partitions=31,
        )
