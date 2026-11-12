"""Deterministic seeding of Operator Console *document-store* panels.

The data-plane backfill fills the canonical ``core.*`` tables (stores, revenue,
machines) that the 門市/營收 panels read.  The expansion panels — 展店 network
listings and candidate sites — live in a separate, tenant-scoped document store
and are never touched by the backfill.  In production they are written by the
listing intake / convert workflow (external listing providers), which is
disabled in dev.

This module is a standalone *seeding tool* (like
``product_ops/data_platform/seed_sample_mongo.py``): it builds real
``shared.domain`` listing/address/candidate aggregates and writes them through
the **production** ``DurableListingRepository`` + ``DurableAssistedIntakeRepository``
— exactly the write path the integration test ``_seed_convertible_listing``
exercises — so the seeded rows are indistinguishable from intake-written ones.

It does **not** fabricate SiteScore decisions, forecast alerts, interventions,
or heat-zone scores: those require the scoring runtime / forecast / intervention
services to actually run, and writing hand-built domain objects for them would
be inventing a second, drift-prone path. Those sections stay available-but-empty.
"""

from __future__ import annotations

import argparse
import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# Real Taiwan store geography, reused from the Mongo synthetic generator so the
# candidate sites sit in the same districts as the seeded stores.
from product_ops.data_platform.synthetic_source import _TW_LOCATIONS

_DEFAULT_MERCHANT = "ODP-DEMO-FONGNIAO-001"

# listing_status distribution: most live for the 有效物件 KPI, a couple parked in
# review so the 今日工作 queue shows network review tasks.
_ACTIVE = "active"
_REVIEW = "manual_review"

# candidate site_status values the work-queue projection surfaces as review tasks
# (operator_live_repository._candidate_tasks): {"new","screened","scored","visited"}.
_CANDIDATE_STATUSES = ("new", "screened", "scored")


@dataclass(frozen=True)
class OperatorDocsConfig:
    """How many listings/candidates to seed, and for which tenant."""

    tenant_id: str
    merchant_source_id: str = _DEFAULT_MERCHANT
    listing_count: int = 12
    candidate_count: int = 6
    review_listing_count: int = 2
    seed: int = 20261112


@dataclass(frozen=True)
class SeededListing:
    """A fully-built listing bundle ready for ``save_listing`` + metadata."""

    listing: Any
    address: Any
    dedup_key: Any
    metadata: dict[str, Any]


@dataclass(frozen=True)
class OperatorDocsDataset:
    """Deterministically-built listing + candidate aggregates for one tenant."""

    config: OperatorDocsConfig
    listings: list[SeededListing] = field(default_factory=list)
    candidates: list[Any] = field(default_factory=list)


def _h3_res9(lat: float, lon: float) -> str:
    import h3

    return h3.latlng_to_cell(lat, lon, 9)


def build_dataset(config: OperatorDocsConfig) -> OperatorDocsDataset:
    """Build listing/address/candidate aggregates without touching any store."""

    # Imported lazily so the module imports cleanly in environments that only
    # need the config dataclasses.
    from modules.listing.domain.models import CandidateSiteDraft, ListingDedupKey
    from shared.domain import AddressLocation, CandidateSite, Listing

    rng = random.Random(config.seed)
    now = datetime.now(UTC)
    listings: list[SeededListing] = []
    candidates: list[Any] = []

    for index in range(config.listing_count):
        city, district, base_lon, base_lat = _TW_LOCATIONS[index % len(_TW_LOCATIONS)]
        # Nudge off the store coordinate so a candidate is a *prospective* site,
        # not the existing store; keep it inside the same district.
        lat = round(base_lat + rng.uniform(-0.004, 0.004), 6)
        lon = round(base_lon + rng.uniform(-0.004, 0.004), 6)
        h3_res_9 = _h3_res9(lat, lon)
        suffix = f"{index + 1:03d}"
        address = AddressLocation(
            address_id=f"ADDR-{config.merchant_source_id}-CAND-{suffix}",
            raw_address=f"{city}{district}展店候選路 {index + 1} 號",
            normalized_address=f"{city}{district}展店候選路{index + 1}號",
            city=city,
            district=district,
            latitude=lat,
            longitude=lon,
            geocode_confidence=round(rng.uniform(0.9, 0.98), 3),
            h3_res_9=h3_res_9,
        )
        status = _REVIEW if index < config.review_listing_count else _ACTIVE
        listing = Listing(
            listing_id=f"LST-{config.merchant_source_id}-CAND-{suffix}",
            source_listing_id=f"src-cand-{suffix}",
            source_id="approved-feed",
            listing_status=status,
            address_id=address.address_id,
            rent_amount=float(rng.randrange(45_000, 130_000, 1_000)),
            area_ping=float(rng.randrange(15, 45)),
            floor="1F",
            frontage_m=round(rng.uniform(4.0, 8.0), 1),
            snapshot_id=f"snapshot-cand-{suffix}",
            confidence=round(rng.uniform(0.9, 0.97), 3),
            tenant_id=config.tenant_id,
        )
        dedup_key = ListingDedupKey(
            source_id=listing.source_id,
            source_listing_id=listing.source_listing_id,
            normalized_address=address.normalized_address,
            rent_amount=listing.rent_amount,
            area_ping=listing.area_ping,
        )
        prior_revenue = float(rng.randrange(90_000, 320_000, 10_000))
        snapshot_time = now - timedelta(days=rng.randint(1, 20))
        metadata = {
            "heatZoneId": h3_res_9,
            "hardRuleFailures": [],
            "hardRuleSummary": "3/3 pass",
            "address": address.normalized_address,
            "prior90dCellNetRevenue": prior_revenue,
            "prior90dCellTransactionCount": rng.randint(12, 60),
            "prior90dCellStoreCount": rng.randint(1, 3),
            "featureSnapshotTime": snapshot_time.isoformat(),
        }
        listings.append(SeededListing(listing, address, dedup_key, metadata))

        if index < config.candidate_count:
            candidate_site = CandidateSite(
                candidate_site_id=f"CAND-{config.merchant_source_id}-{suffix}",
                listing_id=listing.listing_id,
                address_id=address.address_id,
                target_format_code="ODAY_G2",
                site_status=_CANDIDATE_STATUSES[index % len(_CANDIDATE_STATUSES)],
                created_by="dev-seed",
            )
            candidates.append(
                CandidateSiteDraft(
                    listing=listing,
                    address=address,
                    candidate_site=candidate_site,
                    heat_zone_id=h3_res_9,
                    listing_source=listing.source_id,
                    recommendation=rng.choice(("GO", "WAIT", "WAIT")),
                    prior_90d_cell_net_revenue=metadata["prior90dCellNetRevenue"],
                    prior_90d_cell_transaction_count=metadata[
                        "prior90dCellTransactionCount"
                    ],
                    prior_90d_cell_store_count=metadata["prior90dCellStoreCount"],
                    feature_snapshot_time=snapshot_time,
                )
            )

    return OperatorDocsDataset(config=config, listings=listings, candidates=candidates)


def seed_documents(
    scoped_store: Any,
    config: OperatorDocsConfig,
) -> dict[str, int]:
    """Write the dataset through the production durable repositories.

    ``scoped_store`` must be a ``TenantScopedDocumentStore`` wrapping the raw
    document store for ``config.tenant_id`` — the identical wrapping the live
    read path (``OperatorLiveRepository._tenant_scoped_repository``) applies, so
    the writes land in the partition the Operator Console reads.
    """

    from shared.infrastructure.persistence.operator_network_listings import (
        DurableAssistedIntakeRepository,
    )
    from shared.infrastructure.persistence.repositories import DurableListingRepository

    listing_repo = DurableListingRepository(scoped_store)
    intake_repo = DurableAssistedIntakeRepository(scoped_store)

    dataset = build_dataset(config)
    for item in dataset.listings:
        listing_repo.save_listing(item.listing, item.address, item.dedup_key)
        intake_repo.save_listing_metadata(item.listing.listing_id, item.metadata)
    for candidate in dataset.candidates:
        listing_repo.save_candidate(candidate)

    return {
        "listings": len(listing_repo.list_listings()),
        "candidates": len(listing_repo.list_candidates()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--merchant-source-id", default=_DEFAULT_MERCHANT)
    parser.add_argument("--listings", type=int, default=12)
    parser.add_argument("--candidates", type=int, default=6)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the dataset and print counts without writing.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    config = OperatorDocsConfig(
        tenant_id=args.tenant_id,
        merchant_source_id=args.merchant_source_id,
        listing_count=args.listings,
        candidate_count=args.candidates,
    )
    dataset = build_dataset(config)
    print(
        f"dry-run operator-docs tenant={config.tenant_id} "
        f"listings={len(dataset.listings)} candidates={len(dataset.candidates)}"
    )
    if not args.dry_run:
        print("Refusing to write without a configured document store; "
              "use the dev seeding driver.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
