"""Dev-only daily incremental order feed for the synthetic merchant.

Why this exists: the governed forecastops trainer only accepts horizon labels
that became known to the platform AFTER the prediction origin
(product_ops/modeling/forecast_training.py). History loaded by a one-off
backfill matures in batches, so it can never train. This job appends each new
day as it closes and loads it with its own real backfill run, so label
maturity advances day by day exactly like a live feed.

Per run (idempotent, catch-up capped):
  1. find the newest order day already canonical in dev for the tenant;
  2. for each following closed UTC day (up to yesterday, max MAX_CATCHUP_DAYS),
     regenerate the deterministic synthetic dataset with as_of = day + 1 and
     upsert only that day's orders into the sample Atlas ``fongniao_prod``;
  3. run the REAL data-plane backfill over exactly those new days in one
     partition (no already-canonical rows in the window -> reconcile passes);
  4. report how many forecast origins now have an incremental 28-day horizon.

Env: RAW_DB_URL, CLOUD_SQL_INSTANCE, ODP_DATA_MONGO_URI, TARGET_TENANT,
INCREMENTAL_START (first day of the incremental feed). No secrets printed.
"""

from __future__ import annotations

import os
import urllib.parse
from datetime import UTC, date, datetime, timedelta

MAX_CATCHUP_DAYS = 7


def _proxy_dsn(raw: str) -> str:
    u = urllib.parse.urlparse(raw)
    user = urllib.parse.quote(urllib.parse.unquote(u.username or ""), safe="")
    pw = urllib.parse.quote(urllib.parse.unquote(u.password or ""), safe="")
    db = (u.path or "/").lstrip("/") or "postgres"
    return f"postgresql://{user}:{pw}@127.0.0.1:5432/{db}"


def _midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _iso(d: date) -> str:
    return _midnight(d).strftime("%Y-%m-%dT%H:%M:%SZ")


def _orders_for_day(day: date) -> list[dict]:
    from apps.data_platform.contracts import SourceKind
    from product_ops.data_platform.seed_sample_mongo import coerce_dates
    from product_ops.data_platform.synthetic_source import (
        SyntheticConfig,
        SyntheticDataset,
    )

    dataset = SyntheticDataset(SyntheticConfig(as_of=_midnight(day + timedelta(days=1))))
    tag = f"-ORD-{day.strftime('%Y%m%d')}-"
    return [
        coerce_dates(SourceKind.ORDERS, doc)
        for doc in dataset.documents(SourceKind.ORDERS)
        if tag in doc["_id"]
    ]


def main() -> int:
    os.environ.setdefault("DO_NOT_TRACK", "1")
    os.environ.setdefault("DLT__RUNTIME__DLTHUB_TELEMETRY", "false")
    target = os.environ["TARGET_TENANT"].strip()
    incremental_start = date.fromisoformat(os.environ["INCREMENTAL_START"].strip())
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

    tenant_sql = (
        "FROM core.transactions x JOIN core.stores s ON s.store_id = x.store_id "
        "WHERE s.tenant_id = %s"
    )
    with psycopg.connect(dsn, connect_timeout=30) as conn:
        last_day = conn.execute(
            f"SELECT max((x.event_time AT TIME ZONE 'UTC')::date) {tenant_sql}", (target,)
        ).fetchone()[0]
        before = conn.execute(f"SELECT count(*) {tenant_sql}", (target,)).fetchone()[0]

    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    print(f"PREFLIGHT dev_last_order_day={last_day} yesterday={yesterday} txns={before}", flush=True)
    if last_day is None:
        print("RESULT: FAILED (no canonical orders for tenant)", flush=True)
        return 1
    first = last_day + timedelta(days=1)
    last = min(yesterday, last_day + timedelta(days=MAX_CATCHUP_DAYS))
    if first > last:
        print("RESULT: OK (already current; nothing to append)", flush=True)
        return 0

    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    mc = pymongo.MongoClient(
        os.environ["ODP_DATA_MONGO_URI"], serverSelectionTimeoutMS=20000, tz_aware=True
    )
    orders = mc["fongniao_prod"]["orders"]
    expected = 0
    for day in days:
        docs = _orders_for_day(day)
        if docs:
            orders.bulk_write(
                [UpdateOne({"_id": d["_id"]}, {"$set": d}, upsert=True) for d in docs],
                ordered=False,
            )
        expected += len(docs)
        print(f"MONGO day={day} orders={len(docs)}", flush=True)

    from product_ops.data_platform.backfill import main as backfill_main

    span = len(days)
    rc = backfill_main(
        [
            "--kind", "orders",
            "--start", _iso(first),
            "--end", _iso(last + timedelta(days=1)),
            "--partition-days", str(span),
            "--max-partitions", "1",
        ]
    )
    print(f"BACKFILL orders {first}..{last} rc={rc} expected_source={expected}", flush=True)

    with psycopg.connect(dsn, connect_timeout=30) as conn:
        after = conn.execute(f"SELECT count(*) {tenant_sql}", (target,)).fetchone()[0]
        # Origins whose whole 4-week horizon lies in the incremental feed: the
        # rows the trainer can use once it is bounded to INCREMENTAL_START.
        ready = conn.execute(
            """
            WITH d AS (
              SELECT DISTINCT x.store_id, (x.event_time AT TIME ZONE 'UTC')::date AS day
              FROM core.transactions x JOIN core.stores s ON s.store_id = x.store_id
              WHERE s.tenant_id = %s AND x.transaction_status = 'succeeded'
                AND (x.event_time AT TIME ZONE 'UTC')::date >= %s
            )
            SELECT count(*) FROM d
            WHERE (SELECT count(*) FROM d n WHERE n.store_id = d.store_id
                   AND n.day >= d.day AND n.day < d.day + 28) = 28
            """,
            (target, incremental_start),
        ).fetchone()[0]
    print(
        f"VERIFY txns {before} -> {after} (+{after - before}); "
        f"incremental_w4_origins={ready} (trainer minimum_rows=90)",
        flush=True,
    )
    if rc != 0:
        print("RESULT: FAILED (non-zero backfill)", flush=True)
        return 1
    print(f"RESULT: OK (appended {first}..{last})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
