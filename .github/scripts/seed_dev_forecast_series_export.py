"""Dev-only, read-only: export per-store daily net revenue for the ForecastOps
timeseries ingest (POST /api/v1/forecastops/timeseries).

The day aggregation mirrors model_ready.forecast_training_view's
transaction_daily CTE (succeeded TWD transactions, UTC business date,
sum(net_amount), source snapshot ids from data_plane.canonical_lineage), so the
runtime sees the same revenue definition the model was trained on. Days a store
had no transaction are NOT filled in: no lineage exists for them, and the
runtime requires lineage for every history day. machine_cycles is the day's
transaction count. data_quality_score follows the view (1.0 when every
transaction has lineage from a completed ingestion run, else 0.0).

Env: RAW_DB_URL, CLOUD_SQL_INSTANCE, TARGET_TENANT, LOOKBACK_DAYS, OUT_PATH.
Writes JSON; prints counts only.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import psycopg

SQL = """
WITH txn AS (
    SELECT
        txn.store_id,
        (txn.event_time AT TIME ZONE 'UTC')::date AS day,
        txn.net_amount,
        txn.observation_time >= txn.event_time
            AND txn.ingested_at >= txn.observation_time AS times_ok,
        src.snapshot_ids,
        src.run_complete
    FROM core.transactions AS txn
    JOIN core.stores AS store ON store.store_id = txn.store_id
    LEFT JOIN LATERAL (
        SELECT
            array_agg(DISTINCT lineage.source_snapshot_id::text) AS snapshot_ids,
            bool_and(ingestion.status = 'SUCCEEDED' AND ingestion.finished_at IS NOT NULL)
                AS run_complete
        FROM data_plane.canonical_lineage AS lineage
        JOIN data_plane.ingestion_runs AS ingestion ON ingestion.run_id = lineage.run_id
        WHERE lineage.tenant_id = store.tenant_id
          AND lineage.canonical_table = 'core.transactions'
          AND lineage.canonical_id = txn.transaction_id
    ) AS src ON TRUE
    WHERE store.tenant_id = %(tenant)s
      AND txn.transaction_status = 'succeeded'
      AND txn.currency = 'TWD'
      AND (txn.event_time AT TIME ZONE 'UTC')::date BETWEEN %(start)s AND %(end)s
),
day_ids AS (
    SELECT store_id, day, array_agg(DISTINCT snapshot_id ORDER BY snapshot_id) AS snapshot_ids
    FROM txn CROSS JOIN LATERAL unnest(coalesce(txn.snapshot_ids, ARRAY[]::text[])) AS snapshot_id
    GROUP BY store_id, day
)
SELECT
    txn.store_id::text,
    txn.day,
    sum(txn.net_amount)::double precision AS revenue,
    count(*)::bigint AS txn_count,
    coalesce(bool_and(cardinality(txn.snapshot_ids) > 0), FALSE)
        AND coalesce(bool_and(txn.times_ok AND txn.run_complete), FALSE) AS quality_ok,
    day_ids.snapshot_ids
FROM txn
LEFT JOIN day_ids ON day_ids.store_id = txn.store_id AND day_ids.day = txn.day
GROUP BY txn.store_id, txn.day, day_ids.snapshot_ids
ORDER BY txn.store_id, txn.day
"""


def _socket_dsn(raw: str, instance: str) -> str:
    u = urllib.parse.urlparse(raw)
    user = urllib.parse.quote(urllib.parse.unquote(u.username or ""), safe="")
    pw = urllib.parse.quote(urllib.parse.unquote(u.password or ""), safe="")
    db = (u.path or "/").lstrip("/") or "postgres"
    host = urllib.parse.quote(f"/cloudsql/{instance}", safe="/:")
    return f"postgresql://{user}:{pw}@/{db}?host={host}"


def main() -> int:
    dsn = _socket_dsn(os.environ["RAW_DB_URL"], os.environ["CLOUD_SQL_INSTANCE"])
    tenant = os.environ["TARGET_TENANT"].strip()
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    start = yesterday - timedelta(days=int(os.environ.get("LOOKBACK_DAYS", "60")) - 1)
    with psycopg.connect(dsn, connect_timeout=30) as conn:
        rows = conn.execute(SQL, {"tenant": tenant, "start": start, "end": yesterday}).fetchall()

    by_store: dict[str, list[dict]] = defaultdict(list)
    for store_id, day, revenue, txn_count, quality_ok, snapshot_ids in rows:
        by_store[store_id].append(
            {
                "store_id": store_id,
                "business_date": day.isoformat(),
                "actual_revenue": round(float(revenue), 2),
                "machine_cycles": int(txn_count),
                "data_quality_score": 1.0 if quality_ok else 0.0,
                "source_snapshot_ids": list(snapshot_ids or []),
            }
        )

    summary = []
    for store_id, days in sorted(by_store.items()):
        dates = {d["business_date"] for d in days}
        run = 0
        cursor = yesterday
        while cursor.isoformat() in dates:
            run += 1
            cursor -= timedelta(days=1)
        no_lineage = sum(1 for d in days if not d["source_snapshot_ids"])
        summary.append(
            {
                "store_id": store_id,
                "days": len(days),
                "contiguous_days_to_yesterday": run,
                "days_without_lineage": no_lineage,
                "last_day": max(dates),
            }
        )
        print(
            f"STORE {store_id} days={len(days)} contiguous_to_{yesterday}={run} "
            f"no_lineage={no_lineage} last={max(dates)}",
            flush=True,
        )

    out = {
        "tenant_id": tenant,
        "window": [start.isoformat(), yesterday.isoformat()],
        "prediction_origin_time": f"{(yesterday + timedelta(days=1)).isoformat()}T00:00:00Z",
        "stores": summary,
        "observations": [d for days in by_store.values() for d in days],
    }
    with open(os.environ["OUT_PATH"], "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    print(f"RESULT: OK stores={len(by_store)} observations={len(out['observations'])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
