"""Dev-only: fill store-day gaps in the canonical order history.

The 2026-05-14..2026-11-10 batch load left 2026-10-14..10-22 missing for 10
stores (8 lost the whole week). The synthetic generator emits at least one
order per open store per day, so these are load gaps, not quiet days. They
break ForecastOps inference, which needs 28 contiguous lineage-backed days.

Per run:
  1. regenerate the deterministic dataset for each day in GAP_START..GAP_END
     (same generator and as_of as the daily feed) and INSERT ONLY orders whose
     _id is absent in the sample Atlas ``fongniao_prod`` ($setOnInsert): orders
     already in the source are never rewritten;
  2. run the REAL data-plane backfill over the window in one partition;
  3. report per-day store coverage before and after.

Env: RAW_DB_URL, CLOUD_SQL_INSTANCE, ODP_DATA_MONGO_URI, TARGET_TENANT,
GAP_START, GAP_END. No secrets printed.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from seed_dev_orders_daily_driver import _iso, _orders_for_day, _proxy_dsn  # noqa: E402

COVERAGE_SQL = """
SELECT (x.event_time AT TIME ZONE 'UTC')::date AS day, count(DISTINCT x.store_id)
FROM core.transactions x JOIN core.stores s ON s.store_id = x.store_id
WHERE s.tenant_id = %s AND (x.event_time AT TIME ZONE 'UTC')::date BETWEEN %s AND %s
GROUP BY 1 ORDER BY 1
"""


def main() -> int:
    target = os.environ["TARGET_TENANT"].strip()
    first = date.fromisoformat(os.environ["GAP_START"].strip())
    last = date.fromisoformat(os.environ["GAP_END"].strip())
    dsn = _proxy_dsn(os.environ["RAW_DB_URL"])
    os.environ.update(
        ODP_DATA_ENV="production",
        ODP_DATA_MONGO_DATABASE="fongniao_prod",
        ODP_DATA_POSTGRES_DSN=dsn,
        ODP_DATA_CLOUD_SQL_PROXY="true",
        ODP_DATA_CLOUD_SQL_INSTANCE=os.environ["CLOUD_SQL_INSTANCE"],
        ODP_DATA_CLOUD_SQL_CONNECTOR_EVIDENCE="cloud-sql-auth-proxy-sidecar",
    )
    import psycopg
    import pymongo
    from pymongo import UpdateOne

    with psycopg.connect(dsn, connect_timeout=30) as conn:
        before = dict(conn.execute(COVERAGE_SQL, (target, first, last)).fetchall())
    print("BEFORE", {d.isoformat(): n for d, n in before.items()}, flush=True)

    mc = pymongo.MongoClient(
        os.environ["ODP_DATA_MONGO_URI"], serverSelectionTimeoutMS=20000, tz_aware=True
    )
    orders = mc["fongniao_prod"]["orders"]
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    for day in days:
        docs = _orders_for_day(day)
        result = orders.bulk_write(
            [UpdateOne({"_id": d["_id"]}, {"$setOnInsert": d}, upsert=True) for d in docs],
            ordered=False,
        )
        print(
            f"MONGO day={day} generated={len(docs)} inserted={result.upserted_count} "
            f"already_present={len(docs) - result.upserted_count}",
            flush=True,
        )

    from product_ops.data_platform.backfill import main as backfill_main

    rc = backfill_main(
        [
            "--kind", "orders",
            "--start", _iso(first),
            "--end", _iso(last + timedelta(days=1)),
            "--partition-days", str(len(days)),
            "--max-partitions", "1",
        ]
    )
    print(f"BACKFILL orders {first}..{last} rc={rc}", flush=True)

    with psycopg.connect(dsn, connect_timeout=30) as conn:
        after = dict(conn.execute(COVERAGE_SQL, (target, first, last)).fetchall())
    print("AFTER", {d.isoformat(): n for d, n in after.items()}, flush=True)
    if rc != 0:
        print("RESULT: FAILED (non-zero backfill)", flush=True)
        return 1
    print("RESULT: OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
