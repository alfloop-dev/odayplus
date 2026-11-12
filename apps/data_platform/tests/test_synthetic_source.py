"""The synthetic dev-seeding generator must stay aligned with the real canonical
contracts: every projectable document it emits has to pass
``apps.data_platform.mapping`` without a quarantine reason, otherwise the real
backfill would silently drop rows and the operator console would still be empty.

These run at a small scale so CI stays fast; the CLI (`--stores 15 --years 3`)
exercises the full dataset.
"""

from __future__ import annotations

from apps.data_platform.contracts import SourceKind
from apps.data_platform.identifiers import tenant_id_for_merchant
from product_ops.data_platform.synthetic_source import (
    SyntheticConfig,
    SyntheticDataset,
    verify_bundle,
)


def _small() -> SyntheticConfig:
    return SyntheticConfig(
        store_count=3,
        years_of_history=1,
        order_history_days=14,
        members=12,
    )


def test_every_projectable_document_passes_canonical_contracts() -> None:
    report = verify_bundle(SyntheticDataset(_small()))
    assert report["quarantined"] == 0, report["quarantine_reasons"]
    assert report["projected_ok"] > 0


def test_derived_tenant_is_production_identifier() -> None:
    config = _small()
    report = verify_bundle(SyntheticDataset(config))
    assert report["derived_tenant_id"] == str(
        tenant_id_for_merchant(config.merchant_source_id)
    )


def test_core_console_collections_are_populated() -> None:
    dataset = SyntheticDataset(_small())
    for kind in (
        SourceKind.MERCHANT,
        SourceKind.PLACE,
        SourceKind.DEVICE,
        SourceKind.DEVICE_DAILY_STATISTICS,
        SourceKind.AI_REVENUE_STATS,
        SourceKind.ORDERS,
    ):
        assert dataset.documents(kind), kind.value


def test_store_count_matches_config() -> None:
    config = _small()
    dataset = SyntheticDataset(config)
    assert len(dataset.documents(SourceKind.PLACE)) == config.store_count
    assert (
        len(dataset.documents(SourceKind.DEVICE))
        == config.store_count * config.devices_per_store
    )


def test_generation_is_deterministic() -> None:
    first = SyntheticDataset(_small()).collections
    second = SyntheticDataset(_small()).collections
    assert first == second
