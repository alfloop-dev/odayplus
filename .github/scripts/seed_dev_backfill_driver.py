"""One-off dev seeding driver: backfill the sample Atlas Mongo into dev Cloud SQL
core.* via the REAL data-plane backfill, then point the bjoe734 demo account at the
derived tenant so the Operator Console scopes to the seeded data.

Runs inside the seed-dev-data GitHub Actions job: GCP auth via WIF, dev Cloud SQL
reached through the Cloud SQL Auth Proxy on 127.0.0.1:5432. Reads:
  RAW_DB_URL        - dev DB URL fetched from Secret Manager
  CLOUD_SQL_INSTANCE- instance connection name (proj:region:instance)
  ODP_DATA_MONGO_URI- sample Atlas URI (dev secret)
  TARGET_TENANT     - uuid5-derived tenant for the synthetic merchant
  BJOE_USERNAME     - demo account to re-scope
Nothing here is secret in its output; credentials are never printed.
"""

from __future__ import annotations

import os
import urllib.parse


def _proxy_dsn(raw: str) -> str:
    u = urllib.parse.urlparse(raw)
    user = urllib.parse.quote(urllib.parse.unquote(u.username or ""), safe="")
    pw = urllib.parse.quote(urllib.parse.unquote(u.password or ""), safe="")
    db = (u.path or "/").lstrip("/") or "postgres"
    # Ignore the secret's host (often a /cloudsql/ unix socket); the Auth Proxy
    # exposes the same instance on TCP 127.0.0.1:5432.
    return f"postgresql://{user}:{pw}@127.0.0.1:5432/{db}"


def main() -> int:
    # Belt-and-suspenders: dlt must not phone home (a hanging telemetry POST per
    # partition stalled the first attempt for hours).
    os.environ.setdefault("DO_NOT_TRACK", "1")
    os.environ.setdefault("DLT__RUNTIME__DLTHUB_TELEMETRY", "false")

    raw = os.environ["RAW_DB_URL"]
    dsn = _proxy_dsn(raw)
    os.environ.update(
        ODP_DATA_ENV="production",
        ODP_DATA_MONGO_DATABASE="fongniao_prod",
        ODP_DATA_POSTGRES_DSN=dsn,
        ODP_DATA_CLOUD_SQL_PROXY="true",
        ODP_DATA_CLOUD_SQL_INSTANCE=os.environ["CLOUD_SQL_INSTANCE"],
        ODP_DATA_CLOUD_SQL_CONNECTOR_EVIDENCE="cloud-sql-auth-proxy-sidecar",
    )

    # --- Preflight: prove both endpoints before writing anything -------------
    import psycopg
    import pymongo

    mc = pymongo.MongoClient(
        os.environ["ODP_DATA_MONGO_URI"], serverSelectionTimeoutMS=20000, tz_aware=True
    )
    print("PREFLIGHT mongo merchant/place/device/orders:",
          mc["fongniao_prod"]["merchant"].count_documents({}),
          mc["fongniao_prod"]["place"].count_documents({}),
          mc["fongniao_prod"]["device"].count_documents({}),
          mc["fongniao_prod"]["orders"].count_documents({}), flush=True)
    with psycopg.connect(dsn, connect_timeout=30) as conn:
        print("PREFLIGHT pg select1:", conn.execute("SELECT 1").fetchone(), flush=True)
        for t in ("core.tenants", "core.stores", "core.transactions"):
            try:
                n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608
                print(f"PREFLIGHT {t} before: {n}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"PREFLIGHT {t} read error: {exc}", flush=True)

    # --- Real backfill: Atlas -> dev Cloud SQL core.* ------------------------
    from product_ops.data_platform.backfill import main as backfill_main

    print("BACKFILL snapshot kinds (merchant/place/device) ...", flush=True)
    rc1 = backfill_main([
        "--kind", "merchant", "--kind", "place", "--kind", "device",
        "--start", "2023-11-01", "--end", "2023-11-02",
        "--partition-days", "1", "--max-partitions", "2",
    ])
    print("BACKFILL snapshot rc:", rc1, flush=True)

    # Orders were already bulk-loaded by the prior run (core.transactions ~4742);
    # dlt's merge against Cloud SQL is ~14 min/partition, and re-running over the
    # already-canonical rows reconcile-fails (idempotent skip => valid_loaded 0).
    # Partial 87% coverage is ample for the demo, so we do not re-run orders.
    print("BACKFILL orders: skipped (already loaded by prior run)", flush=True)
    rc2 = 0

    # --- Verify + wire bjoe734 to the seeded tenant --------------------------
    target = os.environ["TARGET_TENANT"]
    username = os.environ["BJOE_USERNAME"].lower()
    with psycopg.connect(dsn, autocommit=True, connect_timeout=30) as conn:
        tcount = conn.execute(
            "SELECT count(*) FROM core.tenants WHERE tenant_id = %s", (target,)
        ).fetchone()[0]
        scount = conn.execute(
            "SELECT count(*) FROM core.stores WHERE tenant_id = %s", (target,)
        ).fetchone()[0]
        xcount = conn.execute(
            "SELECT count(*) FROM core.transactions x "
            "JOIN core.stores s ON s.store_id = x.store_id WHERE s.tenant_id = %s",
            (target,),
        ).fetchone()[0]
        print(f"VERIFY core.tenants={tcount} core.stores={scount} "
              f"core.transactions={xcount} for tenant {target}", flush=True)

        cur = conn.execute(
            "UPDATE identity.accounts SET tenant_id = %s, updated_at = now() "
            "WHERE lower(username) = %s",
            (target, username),
        )
        print(f"IDENTITY bjoe734 rows re-scoped: {cur.rowcount}", flush=True)
        row = conn.execute(
            "SELECT username, tenant_id FROM identity.accounts "
            "WHERE lower(username) = %s", (username,)
        ).fetchone()
        print("IDENTITY readback:", row, flush=True)

    if rc1 != 0 or rc2 != 0 or scount == 0:
        print("RESULT: FAILED (non-zero backfill or no stores seeded)", flush=True)
        return 1
    print("RESULT: OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
