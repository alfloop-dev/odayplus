"""Dev-only: load an earlier, still-empty stretch of synthetic order history in
date-ordered chunks, each through its own REAL data-plane backfill run.

Why: the forecastops trainer only accepts horizon labels that matured AFTER the
prediction origin (product_ops/modeling/forecast_training.py). Label maturity
is greatest(ingested_at, ingestion run finished_at, date+1), so rows loaded in
one batch share one maturity and a 4-week horizon window inside a single batch
is rejected. Loading [HIST_START, HIST_END) as consecutive CHUNK_DAYS (< 28)
runs in ascending date order makes every 4-week window reach into a later run,
using only true load timestamps (nothing is back-dated).

The window must hold no canonical rows yet (dev orders for the tenant start at
2026-05-14), so each chunk reconciles cleanly. Re-runs skip chunks that are
already fully loaded and stop on a partially loaded one.

Env: RAW_DB_URL, CLOUD_SQL_INSTANCE, ODP_DATA_MONGO_URI, TARGET_TENANT,
HIST_START, HIST_END (exclusive), CHUNK_DAYS. No secrets printed.
"""

from __future__ import annotations

import os
import urllib.parse
from datetime import UTC, date, datetime, timedelta


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


def main() -> int:
    os.environ.setdefault("DO_NOT_TRACK", "1")
    os.environ.setdefault("DLT__RUNTIME__DLTHUB_TELEMETRY", "false")
    target = os.environ["TARGET_TENANT"].strip()
    start = date.fromisoformat(os.environ["HIST_START"])
    end = date.fromisoformat(os.environ["HIST_END"])
    chunk_days = int(os.environ["CHUNK_DAYS"])
    if not 1 <= chunk_days < 28:
        print("RESULT: FAILED (CHUNK_DAYS must be 1..27)", flush=True)
        return 1
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

    from apps.data_platform.contracts import SourceKind
    from product_ops.data_platform.backfill import main as backfill_main
    from product_ops.data_platform.seed_sample_mongo import coerce_dates
    from product_ops.data_platform.synthetic_source import (
        SyntheticConfig,
        SyntheticDataset,
    )

    # Generate with the as_of the original seeding used (GEN_AS_OF) so store
    # opening dates match the canonical stores; orders are seeded per
    # store-day, so the window's values do not depend on as_of.
    gen_as_of = date.fromisoformat(os.environ.get("GEN_AS_OF", "") or end.isoformat())
    dataset = SyntheticDataset(
        SyntheticConfig(
            as_of=_midnight(gen_as_of), order_history_days=(gen_as_of - start).days
        )
    )
    docs = [
        coerce_dates(SourceKind.ORDERS, d)
        for d in dataset.documents(SourceKind.ORDERS)
    ]
    docs = [d for d in docs if start <= d["createdAt"].date() < end]
    per_day: dict[date, int] = {}
    for d in docs:
        per_day[d["createdAt"].date()] = per_day.get(d["createdAt"].date(), 0) + 1

    tenant_sql = (
        "FROM core.transactions x JOIN core.stores s ON s.store_id = x.store_id "
        "WHERE s.tenant_id = %s"
    )

    def dev_count(lo: date, hi: date) -> int:
        with psycopg.connect(dsn, connect_timeout=30) as conn:
            return conn.execute(
                f"SELECT count(*) {tenant_sql} AND x.event_time >= %s AND x.event_time < %s",
                (target, _midnight(lo), _midnight(hi)),
            ).fetchone()[0]

    with psycopg.connect(dsn, connect_timeout=30) as conn:
        before = conn.execute(f"SELECT count(*) {tenant_sql}", (target,)).fetchone()[0]
    print(
        f"PREFLIGHT window={start}..{end} generated_orders={len(docs)} "
        f"days_with_orders={len(per_day)} dev_txns={before}",
        flush=True,
    )

    mc = pymongo.MongoClient(
        os.environ["ODP_DATA_MONGO_URI"], serverSelectionTimeoutMS=20000, tz_aware=True
    )
    result = mc["fongniao_prod"]["orders"].bulk_write(
        [UpdateOne({"_id": d["_id"]}, {"$set": d}, upsert=True) for d in docs],
        ordered=False,
    )
    print(
        f"MONGO upserted={result.upserted_count} modified={result.modified_count} "
        f"matched={result.matched_count}",
        flush=True,
    )

    chunk = start
    while chunk < end:
        hi = min(end, chunk + timedelta(days=chunk_days))
        expected = sum(n for day, n in per_day.items() if chunk <= day < hi)
        have = dev_count(chunk, hi)
        if have == expected and expected > 0:
            print(f"CHUNK {chunk}..{hi} already loaded ({have}); skip", flush=True)
            chunk = hi
            continue
        if have != 0:
            print(
                f"RESULT: FAILED (chunk {chunk}..{hi} partially loaded: {have}/{expected})",
                flush=True,
            )
            return 1
        rc = backfill_main(
            [
                "--kind", "orders",
                "--start", _iso(chunk),
                "--end", _iso(hi),
                "--partition-days", str((hi - chunk).days),
                "--max-partitions", "1",
            ]
        )
        got = dev_count(chunk, hi)
        print(f"CHUNK {chunk}..{hi} rc={rc} expected={expected} loaded={got}", flush=True)
        if rc != 0:
            print("RESULT: FAILED (non-zero backfill)", flush=True)
            return 1
        chunk = hi

    with psycopg.connect(dsn, connect_timeout=30) as conn:
        after = conn.execute(f"SELECT count(*) {tenant_sql}", (target,)).fetchone()[0]
    print(f"VERIFY dev_txns {before} -> {after}", flush=True)
    print("RESULT: OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
