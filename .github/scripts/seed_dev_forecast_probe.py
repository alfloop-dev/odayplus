"""Read-only probe: can the governed forecastops trainer run against dev?

Answers, without writing anything:
  * is the model_ready schema / forecast_training_view installed in dev Cloud SQL;
  * how many forecast rows are training-eligible (installed view) or would be
    (same 28-consecutive-day rule computed directly from core.transactions);
  * whether the dev source snapshot ids carry a marker the trainer rejects
    (mock/fixture/synthetic/seed) -- the trainer must not be steered around it;
  * the read-only installer preflight (``install_views inventory``).

Runs inside the seed-dev-forecast-probe job. Reads:
  RAW_DB_URL          - dev DB URL from Secret Manager (never printed)
  CLOUD_SQL_INSTANCE  - proj:region:instance; the proxy serves /cloudsql/<it>
  TARGET_TENANT       - derived tenant of the synthetic merchant
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.parse

MARKER_RE = "(mock|fixture|synthetic|seed)"


def _socket_dsn(raw: str, instance: str) -> str:
    u = urllib.parse.urlparse(raw)
    user = urllib.parse.quote(urllib.parse.unquote(u.username or ""), safe="")
    pw = urllib.parse.quote(urllib.parse.unquote(u.password or ""), safe="")
    db = (u.path or "/").lstrip("/") or "postgres"
    host = urllib.parse.quote(f"/cloudsql/{instance}", safe="/:")
    return f"postgresql://{user}:{pw}@/{db}?host={host}"


def main() -> int:
    import psycopg

    target = os.environ["TARGET_TENANT"].strip()
    dsn = _socket_dsn(os.environ["RAW_DB_URL"], os.environ["CLOUD_SQL_INSTANCE"])

    with psycopg.connect(dsn, connect_timeout=30) as conn:
        conn.read_only = True
        view = conn.execute(
            "SELECT to_regclass('model_ready.forecast_training_view')::text"
        ).fetchone()[0]
        print("VIEW forecast_training_view:", view or "NOT INSTALLED", flush=True)

        if view:
            for reason, eligible, n in conn.execute(
                "SELECT coalesce(exclusion_reason,'-'), is_training_eligible, count(*) "
                "FROM model_ready.forecast_training_view WHERE tenant_id = %s "
                "GROUP BY 1,2 ORDER BY 3 DESC",
                (target,),
            ).fetchall():
                print(f"VIEWROWS eligible={eligible} reason={reason} n={n}", flush=True)

        # Same consecutive-history rule the view applies, computed directly.
        row = conn.execute(
            """
            WITH d AS (
              SELECT x.store_id, (x.event_time AT TIME ZONE 'UTC')::date AS day
              FROM core.transactions x JOIN core.stores s ON s.store_id = x.store_id
              WHERE s.tenant_id = %s AND x.transaction_status = 'succeeded'
                AND x.currency = 'TWD'
              GROUP BY 1, 2
            )
            SELECT count(*) AS store_days,
                   count(DISTINCT store_id) AS stores,
                   min(day), max(day),
                   count(*) FILTER (WHERE (
                     SELECT count(*) FROM d p
                     WHERE p.store_id = d.store_id
                       AND p.day >= d.day - 28 AND p.day < d.day) = 28
                   ) AS with_28_prior
            FROM d
            """,
            (target,),
        ).fetchone()
        print(
            f"ESTIMATE store_days={row[0]} stores={row[1]} window={row[2]}..{row[3]} "
            f"store_days_with_full_28d_history={row[4]} (forecast minimum_rows=90)",
            flush=True,
        )

        for status, n in conn.execute(
            "SELECT txn.transaction_status || '/' || txn.currency, count(*) "
            "FROM core.transactions txn JOIN core.stores s ON s.store_id = txn.store_id "
            "WHERE s.tenant_id = %s GROUP BY 1 ORDER BY 2 DESC",
            (target,),
        ).fetchall():
            print(f"TXN status/currency={status} n={n}", flush=True)

        snap = conn.execute(
            "SELECT count(DISTINCT source_snapshot_id), "
            "count(DISTINCT source_snapshot_id) FILTER "
            f"(WHERE lower(source_snapshot_id::text) ~ '{MARKER_RE}'), "
            "count(*) "
            "FROM data_plane.canonical_lineage "
            "WHERE tenant_id = %s AND canonical_table = 'core.transactions'",
            (target,),
        ).fetchone()
        print(
            f"LINEAGE snapshot_ids={snap[0]} snapshot_ids_with_blocked_marker={snap[1]} "
            f"lineage_rows={snap[2]}",
            flush=True,
        )
        for status, n in conn.execute(
            "SELECT status, count(*) FROM data_plane.ingestion_runs "
            "WHERE run_id IN (SELECT DISTINCT run_id FROM data_plane.canonical_lineage "
            "WHERE tenant_id = %s AND canonical_table = 'core.transactions') "
            "GROUP BY 1",
            (target,),
        ).fetchall():
            print(f"INGESTION_RUNS status={status} n={n}", flush=True)

        # Trainer resolves the platform-wide drift policy for this tenant.
        platform = "00000000-0000-0000-0000-000000000001"
        print(
            "TENANTS total=",
            conn.execute("SELECT count(*) FROM core.tenants").fetchone()[0],
            "platform_tenant_exists=",
            conn.execute(
                "SELECT count(*) FROM core.tenants WHERE tenant_id = %s", (platform,)
            ).fetchone()[0],
            "target_tenant_exists=",
            conn.execute(
                "SELECT count(*) FROM core.tenants WHERE tenant_id = %s", (target,)
            ).fetchone()[0],
            flush=True,
        )
        for kind, tenant, in_force, n in conn.execute(
            "SELECT policy_kind, tenant_id::text, (effective_to IS NULL), count(*) "
            "FROM workflow.decision_policies "
            "WHERE policy_kind IN ('model_performance_drift','forecast_alert') "
            "GROUP BY 1,2,3 ORDER BY 1,2"
        ).fetchall():
            label = "PLATFORM" if tenant == platform else ("TARGET" if tenant == target else "other")
            print(f"POLICY kind={kind} tenant={label} in_force={in_force} n={n}", flush=True)
        for tg in conn.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'core.tenants'::regclass "
            "AND NOT tgisinternal"
        ).fetchall():
            print("TENANT_TRIGGER", tg[0], flush=True)
        print(
            "ALEMBIC",
            conn.execute("SELECT version_num FROM alembic_version").fetchall(),
            flush=True,
        )

    env = dict(os.environ, ODAY_DATABASE_URL=dsn)
    proc = subprocess.run(
        [sys.executable, "-m", "product_ops.modeling.install_views", "inventory"],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    print("INSTALL_VIEWS inventory rc:", proc.returncode, flush=True)
    print((proc.stdout or "")[:4000], flush=True)
    if proc.stderr:
        print("stderr tail:", proc.stderr[-1500:], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
