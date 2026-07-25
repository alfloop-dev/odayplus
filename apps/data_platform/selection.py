from __future__ import annotations

from dataclasses import dataclass

from apps.data_platform.contracts import SourceKind


@dataclass(frozen=True)
class SourceSelectionPolicy:
    source_kind: SourceKind
    approximate_rows: int
    canonical_target: str
    default_scheduled: bool
    requires_date_partition: bool
    max_records_per_run: int
    authority_rank: int | None = None


SOURCE_SELECTION: dict[SourceKind, SourceSelectionPolicy] = {
    SourceKind.MERCHANT: SourceSelectionPolicy(
        SourceKind.MERCHANT, 1_436, "core.tenants + core.brands", True, False, 20_000
    ),
    SourceKind.PLACE: SourceSelectionPolicy(
        SourceKind.PLACE, 3_511, "core.address_locations + core.stores", True, False, 20_000
    ),
    SourceKind.DEVICE: SourceSelectionPolicy(
        SourceKind.DEVICE, 17_180, "core.machines", True, False, 50_000
    ),
    SourceKind.DEVICE_DAILY_STATISTICS: SourceSelectionPolicy(
        SourceKind.DEVICE_DAILY_STATISTICS,
        8_252_111,
        "data_plane.store_daily_facts",
        True,
        True,
        250_000,
    ),
    SourceKind.ORDERS: SourceSelectionPolicy(
        SourceKind.ORDERS,
        2_136_497,
        "core.transactions",
        True,
        True,
        250_000,
        authority_rank=1,
    ),
    SourceKind.TRANSACTION: SourceSelectionPolicy(
        SourceKind.TRANSACTION,
        13_106_594,
        "core.transactions (approved numeric status mapping required)",
        False,
        True,
        250_000,
        authority_rank=2,
    ),
    SourceKind.TRADE: SourceSelectionPolicy(
        SourceKind.TRADE,
        158_750_080,
        "core.transactions (manual bounded backfill only)",
        False,
        True,
        100_000,
        authority_rank=3,
    ),
    SourceKind.AI_REVENUE_STATS: SourceSelectionPolicy(
        SourceKind.AI_REVENUE_STATS,
        2_609_685,
        "data_plane.forecast_inputs",
        True,
        True,
        250_000,
    ),
    SourceKind.CAMPAIGN: SourceSelectionPolicy(
        SourceKind.CAMPAIGN, 659, "data_plane.domain_inputs", True, False, 20_000
    ),
    SourceKind.PRODUCT: SourceSelectionPolicy(
        SourceKind.PRODUCT, 5_349, "data_plane.domain_inputs", True, False, 20_000
    ),
    SourceKind.PRODUCTS: SourceSelectionPolicy(
        SourceKind.PRODUCTS, 1_933, "data_plane.domain_inputs", True, False, 20_000
    ),
    SourceKind.PROMOTIONS: SourceSelectionPolicy(
        SourceKind.PROMOTIONS, 1_024, "data_plane.domain_inputs", True, False, 20_000
    ),
    SourceKind.AI_CONSUMER_KMEANS_V1: SourceSelectionPolicy(
        SourceKind.AI_CONSUMER_KMEANS_V1,
        35_307,
        "data_plane.learning_import_lineage",
        True,
        True,
        100_000,
    ),
    SourceKind.MEMBER: SourceSelectionPolicy(
        SourceKind.MEMBER,
        12_383,
        "raw-minimized + quarantine only",
        False,
        False,
        20_000,
    ),
    SourceKind.DEVICE_LOG: SourceSelectionPolicy(
        SourceKind.DEVICE_LOG,
        14_864_112,
        "core.machine_status_events + minimized raw evidence",
        False,
        True,
        100_000,
    ),
}


def read_limit_for(source_kind: SourceKind, configured_limit: int) -> int:
    return min(SOURCE_SELECTION[source_kind].max_records_per_run, configured_limit)
