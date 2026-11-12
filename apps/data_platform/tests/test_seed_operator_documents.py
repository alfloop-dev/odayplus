"""The Operator document seeder must write through the real durable listing
repository so the 展店 panel reads the seeded rows, and must be deterministic so
re-running it is idempotent rather than duplicating candidate sites.

These cover the offline build + a round-trip through a SQLite document store
wrapped exactly the way the live read path wraps it (tenant partition). It does
not need Postgres: the durable repositories share one document-store contract.
"""

from __future__ import annotations

from pathlib import Path

import h3

from product_ops.data_platform.seed_operator_documents import (
    OperatorDocsConfig,
    build_dataset,
    seed_documents,
)
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurableListingRepository

TENANT = "d4fe5181-da2c-5438-bd12-230036ea6f59"


def _config() -> OperatorDocsConfig:
    return OperatorDocsConfig(tenant_id=TENANT, listing_count=8, candidate_count=4)


def test_build_is_deterministic_and_produces_valid_h3() -> None:
    first = build_dataset(_config())
    second = build_dataset(_config())
    assert len(first.listings) == 8
    assert len(first.candidates) == 4
    # Deterministic: same listing ids and coordinates across runs.
    assert [s.listing.listing_id for s in first.listings] == [
        s.listing.listing_id for s in second.listings
    ]
    assert [s.address.latitude for s in first.listings] == [
        s.address.latitude for s in second.listings
    ]
    for seeded in first.listings:
        cell = seeded.address.h3_res_9
        assert h3.is_valid_cell(cell)
        assert h3.get_resolution(cell) == 9
        # The metadata heat zone must match the address cell so the candidate is
        # joinable to its H3 aggregates.
        assert seeded.metadata["heatZoneId"] == cell
        assert seeded.listing.tenant_id == TENANT


def test_status_mix_feeds_both_kpi_and_review_queue() -> None:
    dataset = build_dataset(_config())
    statuses = {s.listing.listing_status for s in dataset.listings}
    assert "active" in statuses  # 有效物件 KPI
    assert "manual_review" in statuses  # 今日工作 listing review tasks
    candidate_states = {c.candidate_site.site_status for c in dataset.candidates}
    assert candidate_states <= {"new", "screened", "scored", "visited"}


def test_seed_round_trips_through_tenant_scoped_listing_repository(
    tmp_path: Path,
) -> None:
    engine = SqliteEngine(tmp_path / "operator-docs.sqlite3")
    try:
        store = SqliteDocumentStore(engine)
        scoped = TenantScopedDocumentStore(store, TENANT)
        counts = seed_documents(scoped, _config())
        assert counts == {"listings": 8, "candidates": 4}

        # The live read path resolves the tenant repository by wrapping the raw
        # store in a TenantScopedDocumentStore; a fresh wrapper over the same
        # engine must see the rows (proves the partition, not the instance).
        reader = DurableListingRepository(TenantScopedDocumentStore(store, TENANT))
        assert len(reader.list_listings()) == 8
        assert len(reader.list_candidates()) == 4

        # A different tenant's partition is isolated: nothing leaks across.
        other = DurableListingRepository(
            TenantScopedDocumentStore(store, "00000000-0000-0000-0000-000000000000")
        )
        assert reader.tenant_id == TENANT
        assert other.list_listings() == []
    finally:
        engine.close()
