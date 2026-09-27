"""Read only target identity/schema/version metadata, never secret payloads."""
from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

PROJECT = "odayplus-runtime-20260825"
SECRET = "oday-plus-dev-api-database-url-pg16"
OUTPUT = Path(__file__).with_name("live-readback.json")


def main():
    report = {
        "at": datetime.datetime.now(datetime.UTC).isoformat(),
        "project": PROJECT,
        "secret": SECRET,
        "complete": False,
        "database_mutations": False,
    }
    phase = "secret_access"
    engine = None
    try:
        result = subprocess.run(
            ["gcloud", "secrets", "versions", "access", "latest", "--secret", SECRET,
             "--project", PROJECT, "--quiet"],
            capture_output=True, text=True, timeout=40, check=False,
        )
        if result.returncode:
            report["error"] = "secret access denied or credentials require reauthentication"
            return report
        phase = "parse_connection_target"
        url = make_url(result.stdout.strip())
        if url.get_backend_name() not in {"postgres", "postgresql"}:
            report["error"] = "unexpected database backend"
            return report
        report["connection_target"] = {
            "host": url.host,
            "port": url.port,
            "database": url.database,
            "cloud_sql_socket": url.query.get("host"),
        }
        phase = "verify_cloud_sql_target"
        expected_socket = "/cloudsql/odayplus-runtime-20260825:asia-east1:oday-dev-sql"
        if url.query.get("host") != expected_socket or url.database != "oday_plus":
            raise RuntimeError("Secret target differs from intended diagnostic proxy")
        url = url.difference_update_query(["host", "port"]).set(host="127.0.0.1", port=15438)
        report["transport"] = "loopback Cloud SQL Auth Proxy to the exact secret socket instance"
        phase = "read_only_database_metadata"
        engine = create_engine(
            url.set(drivername="postgresql+psycopg"),
            connect_args={
                "connect_timeout": 10,
                "options": "-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=1000",
            },
            hide_parameters=True,
            echo=False,
        )
        with engine.connect() as conn:
            identity = conn.execute(text(
                "SELECT current_database() AS database, current_schema() AS schema, "
                "inet_server_addr()::text AS server_address, "
                "current_setting('transaction_read_only') AS transaction_read_only, "
                "current_setting('search_path') AS search_path"
            )).mappings().one()
            report["database_identity"] = dict(identity)
            if identity["transaction_read_only"] != "on":
                raise RuntimeError("read-only transaction was not established")
            rows = conn.execute(text(
                "SELECT schemaname, tablename FROM pg_catalog.pg_tables "
                "WHERE tablename IN ('alembic_version','oday_plus_alembic_version',"
                "'tenants','runs','event_logs','schedules') "
                "ORDER BY schemaname, tablename"
            )).mappings().all()
            report["relevant_tables"] = [dict(row) for row in rows]
            histories = []
            quote = conn.dialect.identifier_preparer.quote
            for row in rows:
                if row["tablename"] not in {"alembic_version", "oday_plus_alembic_version"}:
                    continue
                qualified = quote(row["schemaname"]) + "." + quote(row["tablename"])
                revisions = conn.execute(text(
                    "SELECT version_num FROM " + qualified + " ORDER BY version_num LIMIT 50"
                )).scalars().all()
                histories.append({"schema": row["schemaname"], "table": row["tablename"], "revisions": revisions})
            report["version_tables"] = histories
            report["application_tenants_table"] = conn.execute(text(
                "SELECT to_regclass('core.tenants')::text"
            )).scalar()
            report["non_system_schemas"] = conn.execute(text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT LIKE 'pg_%' AND schema_name <> 'information_schema' ORDER BY schema_name"
            )).scalars().all()
            report["extensions"] = [dict(row) for row in conn.execute(text(
                "SELECT extname, extversion FROM pg_extension ORDER BY extname"
            )).mappings()]
            report["migration_privileges"] = dict(conn.execute(text(
                "SELECT has_database_privilege(current_user, current_database(), 'CREATE') AS can_create_schema, "
                "has_schema_privilege(current_user, 'public', 'CREATE') AS can_create_public_table"
            )).mappings().one())
            conn.rollback()
        report["complete"] = True
    except Exception as exc:
        # Driver exceptions may contain a connection string. Persist only type
        # and phase; never interpolate str(exc), engine/url repr or subprocess output.
        report["error_type"] = type(exc).__name__
        report["failed_phase"] = phase
    finally:
        if engine is not None:
            engine.dispose()
    return report


if __name__ == "__main__":
    result = main()
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["complete"] else 1)
