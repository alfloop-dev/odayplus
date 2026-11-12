"""Load the deterministic synthetic dataset into a *sample* MongoDB.

Companion to ``synthetic_source.py``. It bulk-upserts the generated documents
into a Mongo database named ``fongniao_prod`` so the real, unmodified backfill
(``product_ops/data_platform/backfill.py``) can read them exactly as it reads the
production source. Intended target: a disposable sample cluster (e.g. MongoDB
Atlas M0, which ships a replica set so CDC change streams work later).

Why this is a separate seeding tool and not a data-plane source: the production
reader (``apps.data_platform.source.MongoSource``) stays the only source, and its
config guard rejects localhost — so the sample must be a real hosted cluster and
the backfill path is never modified. See
``apps/data_platform/tests/test_pipeline.py::test_production_constructor_has_no_fixture_or_synthetic_fallback``.

CRITICAL — date typing: the backfill's fact-window query filters with
``{field: {"$gte": start, "$lt": end}}`` where the bounds are ``datetime``
objects. Mongo does not compare a BSON date against a stored *string*, so fact
rows written with ISO-string timestamps would be invisible to the window query
and every revenue chart would stay empty. This loader therefore coerces the known
timestamp fields to timezone-aware ``datetime`` (BSON date) before insert.

This module performs outbound writes only inside ``load_to_mongo`` and only when
invoked with a URI. Importing it, generating data, and the date-coercion logic
are all offline and unit-tested.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from typing import Any

from apps.data_platform.contracts import SourceKind
from product_ops.data_platform.synthetic_source import (
    SyntheticConfig,
    SyntheticDataset,
)

# Fields per collection that must be stored as BSON dates, not strings, so the
# backfill window query matches them. Snapshot kinds are filtered by _id, but we
# still coerce their dates for faithful typing and canonical event-time parsing.
_DATETIME_FIELDS: dict[SourceKind, frozenset[str]] = {
    SourceKind.MERCHANT: frozenset({"createdAt", "updatedAt"}),
    SourceKind.PLACE: frozenset({"createdAt", "updatedAt"}),
    SourceKind.DEVICE: frozenset({"createdAt", "updatedAt"}),
    SourceKind.DEVICE_DAILY_STATISTICS: frozenset(
        {"startDatetime", "endDatetime", "createdAt", "updatedAt"}
    ),
    SourceKind.AI_REVENUE_STATS: frozenset({"date", "createdAt", "updatedAt"}),
    SourceKind.ORDERS: frozenset({"createdAt", "updatedAt"}),
    SourceKind.CAMPAIGN: frozenset(
        {"startDatetime", "endDatetime", "createdAt", "updatedAt"}
    ),
    SourceKind.PRODUCTS: frozenset({"createdAt", "updatedAt"}),
    SourceKind.PROMOTIONS: frozenset({"start", "end", "createdAt", "updatedAt"}),
    SourceKind.MEMBER: frozenset({"createdAt", "updatedAt"}),
    SourceKind.DEVICE_LOG: frozenset({"createdAt", "updatedAt"}),
}

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def coerce_dates(kind: SourceKind, document: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``document`` with its timestamp fields as BSON datetimes."""
    fields = _DATETIME_FIELDS.get(kind, frozenset())
    out = dict(document)
    for name in fields:
        value = out.get(name)
        if isinstance(value, str) and value:
            out[name] = datetime.strptime(value, _ISO).replace(tzinfo=UTC)
    return out


def bson_documents(dataset: SyntheticDataset) -> dict[SourceKind, list[dict[str, Any]]]:
    """Whole dataset with timestamps coerced to BSON dates, ready for insert."""
    return {
        kind: [coerce_dates(kind, doc) for doc in docs]
        for kind, docs in dataset.collections.items()
        if docs
    }


def load_to_mongo(
    uri: str,
    dataset: SyntheticDataset,
    *,
    database: str = "fongniao_prod",
    drop: bool = False,
) -> dict[str, int]:
    """Bulk-upsert the dataset into ``database`` on the Mongo cluster at ``uri``.

    Idempotent: upserts by ``_id`` so re-runs converge. Returns per-collection
    written counts. Requires ``pymongo`` (declared in apps/data_platform).
    """
    try:
        from pymongo import MongoClient, UpdateOne
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError(
            "pymongo is required to load the sample Mongo "
            "(installed with the data-plane dependencies)"
        ) from exc

    client = MongoClient(uri, appname="oday-plus-sample-seed", tz_aware=True)
    client.admin.command("ping")
    db = client[database]
    written: dict[str, int] = {}
    try:
        for kind, documents in bson_documents(dataset).items():
            collection = db[kind.value]
            if drop:
                collection.drop()
            operations = [
                UpdateOne({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
                for doc in documents
            ]
            # Chunk to keep each bulk write well under the 48MB command cap.
            total = 0
            for start in range(0, len(operations), 2000):
                result = collection.bulk_write(
                    operations[start : start + 2000], ordered=False
                )
                total += result.upserted_count + result.modified_count
            written[kind.value] = len(documents)
    finally:
        client.close()
    return written


def _target_host(uri: str) -> str:
    # Print only the host for a safe, non-secret progress line.
    from urllib.parse import urlparse

    return (urlparse(uri).hostname or "<unknown>")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed a sample MongoDB fongniao_prod with synthetic data"
    )
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("ODP_SEED_MONGO_URI"),
        help="Sample Mongo URI (or env ODP_SEED_MONGO_URI). Omit for --dry-run.",
    )
    parser.add_argument("--database", default="fongniao_prod")
    parser.add_argument("--merchant-id", default=SyntheticConfig.merchant_source_id)
    parser.add_argument("--stores", type=int, default=SyntheticConfig.store_count)
    parser.add_argument("--years", type=int, default=SyntheticConfig.years_of_history)
    parser.add_argument(
        "--drop", action="store_true", help="Drop each collection before loading"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and count only; no connection, no write",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    import json

    args = build_parser().parse_args(argv)
    config = SyntheticConfig(
        merchant_source_id=args.merchant_id,
        store_count=args.stores,
        years_of_history=args.years,
    )
    dataset = SyntheticDataset(config)
    if args.dry_run or not args.mongo_uri:
        report = {
            "mode": "dry-run",
            "database": args.database,
            "merchant_source_id": config.merchant_source_id,
            "counts": dataset.counts(),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(f"seeding {args.database} on {_target_host(args.mongo_uri)} ...", flush=True)
    written = load_to_mongo(
        args.mongo_uri, dataset, database=args.database, drop=args.drop
    )
    print(
        json.dumps(
            {"mode": "loaded", "database": args.database, "written": written},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
