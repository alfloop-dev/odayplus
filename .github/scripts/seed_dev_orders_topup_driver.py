"""One-off dev seeding driver: top up the remaining orders into dev Cloud SQL
core.transactions via the REAL data-plane backfill, loading ONLY the contiguous
tail window that is not yet loaded.

Why a tail window: the data-plane reconciliation compares the source order count
in the backfill window against the number of rows it actually loads. Re-running
over a window that already contains canonical rows makes those rows idempotent-
skip, so valid_loaded < source_total and the run reconcile-fails. The prior
backfill loaded orders oldest-first and was cut off by the job timeout, so the
loaded rows are a contiguous *prefix* by date. This driver finds the first day
that is fully unloaded (dev count 0) and backfills from there to the newest
order day in one coarse partition — a window with no already-canonical rows, so
reconciliation passes.

Runs inside the seed-dev-orders GitHub Actions job: GCP auth via WIF, dev Cloud
SQL via the Cloud SQL Auth Proxy on 127.0.0.1:5432. Reads:
  RAW_DB_URL         - dev DB URL fetched from Secret Manager
  CLOUD_SQL_INSTANCE - instance connection name (proj:region:instance)
  ODP_DATA_MONGO_URI - sample Atlas URI (dev secret)
  TARGET_TENANT      - uuid5-derived tenant for the synthetic merchant
No secrets are printed.
"""

from __future__ import annotations

import os
import urllib.parse
from datetime import date, datetime, timedelta, timezone


def _proxy_dsn(raw: str) -> str:
    u = urllib.parse.urlparse(raw)
    user = urllib.parse.quote(urllib.parse.unquote(u.username or ""), safe="")
    pw = urllib.parse.quote(urllib.parse.unquote(u.password or ""), safe="")
    db = (u.path or "/").lstrip("/") or "postgres"
    return f"postgresql://{user}:{pw}@127.0.0.1:5432/{db}"


def _iso_day(d: date) -> str:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def main() -> int:
    os.environ.setdefault("DO_NOT_TRACK", "1")
    os.environ.setdefault("DLT__RUNTIME__DLTHUB_TELEMETRY", "false")

    target = os.environ["TARGET_TENANT"].strip()
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

    # --- Mongo per-day order counts -----------------------------------------
    mc = pymongo.MongoClient(
        os.environ["ODP_DATA_MONGO_URI"], serverSelectionTimeoutMS=20000, tz_aware=True
    )
    orders = mc["fongniao_prod"]["orders"]
    mongo_total = orders.count_documents({})
    mongo_counts: dict[date, int] = {}
    for row in orders.aggregate(
        [
            {
                "$group": {
                    "_id": {
                        "$dateToString": {"format": "%Y-%m-%d", "date": "$createdAt"}
                    },
                    "n": {"$sum": 1},
                }
            }
        ]
    ):
        mongo_counts[date.fromisoformat(row["_id"])] = int(row["n"])
    if not mongo_counts:
        print("RESULT: FAILED (no orders in Mongo source)", flush=True)
        return 1
    mongo_min, mongo_max = min(mongo_counts), max(mongo_counts)

    # --- dev per-day loaded counts for this tenant --------------------------
    dev_counts: dict[date, int] = {}
    with psycopg.connect(dsn, connect_timeout=30) as conn:
        pg_total = conn.execute(
            "SELECT count(*) FROM core.transactions x "
            "JOIN core.stores s ON s.store_id = x.store_id WHERE s.tenant_id = %s",
            (target,),
        ).fetchone()[0]
        for d, n in conn.execute(
            "SELECT (x.event_time AT TIME ZONE 'UTC')::date AS d, count(*) "
            "FROM core.transactions x "
            "JOIN core.stores s ON s.store_id = x.store_id "
            "WHERE s.tenant_id = %s GROUP BY 1",
            (target,),
        ).fetchall():
            dev_counts[d] = int(n)

    print(
        f"PREFLIGHT mongo_orders={mongo_total} dev_transactions={pg_total} "
        f"window={mongo_min}..{mongo_max}",
        flush=True,
    )

    # First fully-unloaded day (dev count 0). Orders loaded oldest-first, so the
    # loaded region is a date prefix and the first zero day begins the clean tail.
    start_day: date | None = None
    d = mongo_min
    while d <= mongo_max:
        if dev_counts.get(d, 0) == 0 and mongo_counts.get(d, 0) > 0:
            start_day = d
            break
        d += timedelta(days=1)

    if start_day is None:
        print("RESULT: OK (no unloaded tail day found; orders already complete)", flush=True)
        return 0

    end_exclusive = mongo_max + timedelta(days=1)
    span_days = (end_exclusive - start_day).days
    partition_days = min(span_days, 31)
    # One coarse partition keeps dlt's per-partition cost paid once; a >31-day
    # gap (should not happen for a ~weeks-long tail) spills into a few more.
    max_partitions = (span_days + partition_days - 1) // partition_days
    expected_source = sum(
        n for day, n in mongo_counts.items() if start_day <= day < end_exclusive
    )
    print(
        f"TAIL start={start_day} end<{end_exclusive} span_days={span_days} "
        f"partition_days={partition_days} max_partitions={max_partitions} "
        f"expected_source_orders={expected_source}",
        flush=True,
    )

    from product_ops.data_platform.backfill import main as backfill_main

    rc = backfill_main(
        [
            "--kind",
            "orders",
            "--start",
            _iso_day(start_day),
            "--end",
            _iso_day(end_exclusive),
            "--partition-days",
            str(partition_days),
            "--max-partitions",
            str(max_partitions),
        ]
    )
    print("BACKFILL orders rc:", rc, flush=True)

    with psycopg.connect(dsn, connect_timeout=30) as conn:
        final_total = conn.execute(
            "SELECT count(*) FROM core.transactions x "
            "JOIN core.stores s ON s.store_id = x.store_id WHERE s.tenant_id = %s",
            (target,),
        ).fetchone()[0]
    print(
        f"VERIFY dev_transactions: {pg_total} -> {final_total} "
        f"(mongo_total={mongo_total})",
        flush=True,
    )

    if rc != 0:
        print("RESULT: FAILED (non-zero backfill)", flush=True)
        return 1
    print("RESULT: OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
