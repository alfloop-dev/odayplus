from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row

REPO_ROOT = Path("/app")
RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
MIGRATION_RECEIPT_TABLE = "odp_runtime.deployment_migration_receipts"
REQUIRED_RELATIONS = (
    "core.tenants",
    "core.brands",
    "core.address_locations",
    "core.stores",
    "core.machines",
    "intake.intakes",
    "identity.properties",
    "expansion.listings",
    "workflow.jobs",
    "audit.audit_events",
    "odp_runtime.schema_migrations",
    "data_plane.ingestion_runs",
    "data_plane.quarantined_records",
    "data_plane.checkpoints",
)
SCHEDULED_KINDS = (
    "merchant",
    "place",
    "device",
    "device_daily_statistics",
    "orders",
    "ai_revenue_stats",
    "campaign",
    "product",
    "products",
    "promotions",
    "ai_consumer_kmeans_v1",
)
MANUAL_HARD_LIMIT = 100_000
ORDERS_HISTORY_MAX_DAYS = 62


class DeploymentContractError(RuntimeError):
    pass


def _apply_assisted_intake_upgrade(database_url: str) -> Any:
    """Load the migration module without importing the broad persistence package."""

    path = (
        REPO_ROOT
        / "shared/infrastructure/persistence/assisted_listing_intake.py"
    )
    spec = importlib.util.spec_from_file_location(
        "oday_assisted_listing_intake_migration",
        path,
    )
    if spec is None or spec.loader is None:
        raise DeploymentContractError("Assisted Intake migration loader is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.apply_upgrade_to_database(database_url)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise DeploymentContractError(f"{name} is required")
    return value


def _release_identity() -> tuple[str, str]:
    release_sha = _required("ODP_RELEASE_SHA")
    image_ref = _required("ODP_IMAGE_REFERENCE")
    if not RELEASE_SHA_RE.fullmatch(release_sha):
        raise DeploymentContractError("ODP_RELEASE_SHA must be a full lowercase Git SHA")
    if not IMAGE_DIGEST_RE.fullmatch(image_ref):
        raise DeploymentContractError(
            "ODP_IMAGE_REFERENCE must be an immutable sha256 image reference"
        )
    return release_sha, image_ref


def _database_url() -> str:
    existing = os.environ.get("ODP_DATA_POSTGRES_DSN", "").strip()
    if existing:
        return existing
    user = quote(_required("ODP_POSTGRES_USER"), safe="")
    password = quote(_required("ODP_POSTGRES_PASSWORD"), safe="")
    database = quote(_required("ODP_POSTGRES_DATABASE"), safe="")
    host = os.environ.get("ODP_POSTGRES_HOST", "127.0.0.1").strip()
    port = int(os.environ.get("ODP_POSTGRES_PORT", "5432"))
    dsn = f"postgresql://{user}:{password}@{host}:{port}/{database}?sslmode=disable"
    os.environ["ODP_DATA_POSTGRES_DSN"] = dsn
    os.environ["ODAY_POSTGRES_DSN"] = dsn
    os.environ["ODAY_DATABASE_URL"] = dsn
    return dsn


def _wait_for_postgres() -> None:
    host = os.environ.get("ODP_POSTGRES_HOST", "127.0.0.1")
    port = int(os.environ.get("ODP_POSTGRES_PORT", "5432"))
    deadline = time.monotonic() + int(os.environ.get("ODP_PROXY_WAIT_SECONDS", "90"))
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError:
            time.sleep(2)
    raise DeploymentContractError("Cloud SQL Auth Proxy did not become ready")


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _receipt_checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _write_termination_receipt(payload: dict[str, Any]) -> None:
    path = Path(os.environ.get("ODP_TERMINATION_RECEIPT_PATH", "/var/run/oday/termination.log"))
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _canonical_json(payload)
    path.write_text(encoded[:4000] + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def _validator_sql() -> str:
    path = REPO_ROOT / "scripts/validate_assisted_listing_intake_schema.sql"
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("\\")
    )


def _assert_relations(connection: psycopg.Connection[Any]) -> None:
    missing = []
    for relation in REQUIRED_RELATIONS:
        row = connection.execute(
            "SELECT to_regclass(%s)::text AS relation",
            (relation,),
        ).fetchone()
        if row is None or row["relation"] is None:
            missing.append(relation)
    if missing:
        raise DeploymentContractError(
            "Migration schema verification failed; missing: " + ", ".join(missing)
        )


def _ensure_receipt_table(connection: psycopg.Connection[Any]) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {MIGRATION_RECEIPT_TABLE} (
          release_sha char(40) PRIMARY KEY,
          image_reference text NOT NULL,
          alembic_revision text NOT NULL,
          assisted_manifest_sha256 char(64) NOT NULL,
          runtime_migration_id text NOT NULL,
          schema_verification_status text NOT NULL
            CHECK (schema_verification_status = 'PASSED'),
          required_relations jsonb NOT NULL,
          receipt_sha256 char(64) NOT NULL UNIQUE,
          completed_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def migrate() -> int:
    release_sha, image_ref = _release_identity()
    _wait_for_postgres()
    dsn = _database_url()

    environment = dict(os.environ)
    environment["ODAY_DATABASE_URL"] = dsn
    subprocess.run(
        [
            "alembic",
            "-c",
            "infra/db/migrations/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
    )
    assisted = _apply_assisted_intake_upgrade(dsn)

    runtime_sql = (
        REPO_ROOT / "infra/db/migrations/000008_postgresql_runtime_persistence.sql"
    ).read_text(encoding="utf-8")
    control_sql = (
        REPO_ROOT / "apps/data_platform/sql/control_schema.sql"
    ).read_text(encoding="utf-8").replace("{{control_schema}}", "data_plane")

    with psycopg.connect(dsn, autocommit=False, row_factory=dict_row) as connection:
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended('oday.data-platform-deployment-migration', 0))"
            )
            connection.execute(runtime_sql)
            connection.execute(control_sql)
            connection.execute(_validator_sql())
            _assert_relations(connection)

            alembic = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            if alembic is None or alembic["version_num"] != "0002":
                raise DeploymentContractError("Alembic head must be exactly 0002")
            runtime = connection.execute(
                """
                SELECT migration_id
                FROM odp_runtime.schema_migrations
                WHERE migration_id = '000008_postgresql_runtime_persistence'
                """
            ).fetchone()
            if runtime is None:
                raise DeploymentContractError("PostgreSQL runtime migration 000008 is absent")

            _ensure_receipt_table(connection)
            existing_receipt = connection.execute(
                f"""
                SELECT image_reference
                FROM {MIGRATION_RECEIPT_TABLE}
                WHERE release_sha = %s
                FOR UPDATE
                """,
                (release_sha,),
            ).fetchone()
            if (
                existing_receipt is not None
                and existing_receipt["image_reference"] != image_ref
            ):
                raise DeploymentContractError(
                    "Release SHA already has a migration receipt for a different image digest"
                )
            receipt_body = {
                "release_sha": release_sha,
                "image_reference": image_ref,
                "alembic_revision": "0002",
                "assisted_manifest_sha256": assisted.manifest_sha256,
                "runtime_migration_id": runtime["migration_id"],
                "schema_verification_status": "PASSED",
                "required_relations": list(REQUIRED_RELATIONS),
            }
            checksum = _receipt_checksum(receipt_body)
            connection.execute(
                f"""
                INSERT INTO {MIGRATION_RECEIPT_TABLE} (
                  release_sha,
                  image_reference,
                  alembic_revision,
                  assisted_manifest_sha256,
                  runtime_migration_id,
                  schema_verification_status,
                  required_relations,
                  receipt_sha256
                ) VALUES (%s, %s, %s, %s, %s, 'PASSED', %s::jsonb, %s)
                ON CONFLICT (release_sha) DO UPDATE SET
                  alembic_revision = EXCLUDED.alembic_revision,
                  assisted_manifest_sha256 = EXCLUDED.assisted_manifest_sha256,
                  runtime_migration_id = EXCLUDED.runtime_migration_id,
                  schema_verification_status = EXCLUDED.schema_verification_status,
                  required_relations = EXCLUDED.required_relations,
                  receipt_sha256 = EXCLUDED.receipt_sha256,
                  completed_at = now()
                """,
                (
                    release_sha,
                    image_ref,
                    "0002",
                    assisted.manifest_sha256,
                    runtime["migration_id"],
                    json.dumps(REQUIRED_RELATIONS),
                    checksum,
                ),
            )

    receipt = {
        "status": "MIGRATION_SUCCEEDED",
        "migration_receipt_sha256": checksum,
        **receipt_body,
    }
    _write_termination_receipt(receipt)
    return 0


def _migration_receipt(dsn: str) -> dict[str, Any]:
    release_sha, image_ref = _release_identity()
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as connection:
        row = connection.execute(
            f"""
            SELECT release_sha, image_reference, alembic_revision,
                   assisted_manifest_sha256, runtime_migration_id,
                   schema_verification_status, required_relations,
                   receipt_sha256, completed_at
            FROM {MIGRATION_RECEIPT_TABLE}
            WHERE release_sha = %s AND image_reference = %s
            """,
            (release_sha, image_ref),
        ).fetchone()
        if row is None:
            raise DeploymentContractError(
                "No PASSED migration receipt exists for this release SHA and image digest"
            )
        if (
            row["alembic_revision"] != "0002"
            or row["runtime_migration_id"] != "000008_postgresql_runtime_persistence"
            or row["schema_verification_status"] != "PASSED"
        ):
            raise DeploymentContractError("Migration receipt is incomplete")
        _assert_relations(connection)
        return {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in row.items()
        }


def _manual_window() -> tuple[datetime, datetime]:
    def parse(name: str) -> datetime:
        value = _required(name).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise DeploymentContractError(f"{name} must include a timezone")
        return parsed.astimezone(UTC)

    start = parse("ODP_MANUAL_START")
    end = parse("ODP_MANUAL_END")
    if end - start <= timedelta(0) or end - start > timedelta(days=1):
        raise DeploymentContractError("Manual trade/device_log window must be <= one day")
    return start, end


def _orders_history_window() -> tuple[datetime, datetime]:
    def parse(name: str) -> datetime:
        value = _required(name).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise DeploymentContractError(f"{name} must include a timezone")
        return parsed.astimezone(UTC)

    start = parse("ODP_ORDERS_HISTORY_START")
    end = parse("ODP_ORDERS_HISTORY_END")
    duration = end - start
    if duration <= timedelta(0) or duration > timedelta(days=ORDERS_HISTORY_MAX_DAYS):
        raise DeploymentContractError(
            f"Orders history window must be <= {ORDERS_HISTORY_MAX_DAYS} days"
        )
    if any(
        value != 0
        for instant in (start, end)
        for value in (instant.hour, instant.minute, instant.second, instant.microsecond)
    ):
        raise DeploymentContractError("Orders history bounds must be UTC day boundaries")
    return start, end


def _backfill_command(mode: str) -> list[str]:
    if mode == "scheduled":
        end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=1)
        kinds = SCHEDULED_KINDS
        limit = 250_000
        extra: list[str] = []
        max_partitions = 1
    elif mode == "orders-history":
        start, end = _orders_history_window()
        kinds = ("orders",)
        limit = 250_000
        extra = []
        max_partitions = (end - start).days
    elif mode in {"trade", "device-log"}:
        start, end = _manual_window()
        kinds = ("trade",) if mode == "trade" else ("device_log",)
        limit = MANUAL_HARD_LIMIT
        extra = ["--allow-trade"] if mode == "trade" else ["--allow-device-log"]
        max_partitions = 1
    else:
        raise DeploymentContractError(f"Unsupported backfill mode: {mode}")

    command = [
        sys.executable,
        "-m",
        "scripts.data_platform.backfill",
    ]
    for kind in kinds:
        command.extend(["--kind", kind])
    command.extend(
        [
            "--start",
            start.isoformat().replace("+00:00", "Z"),
            "--end",
            end.isoformat().replace("+00:00", "Z"),
            "--partition-days",
            "1",
            "--max-partitions",
            str(max_partitions),
            "--max-records",
            str(limit),
            *extra,
        ]
    )
    return command


def backfill(mode: str) -> int:
    _wait_for_postgres()
    dsn = _database_url()
    migration = _migration_receipt(dsn)
    result = subprocess.run(
        _backfill_command(mode),
        cwd=REPO_ROOT,
        env=os.environ,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode != 0:
        raise DeploymentContractError(
            f"Bounded {mode} backfill failed with exit {result.returncode}"
        )
    payload = json.loads(result.stdout)
    runs = payload.get("runs")
    if payload.get("status") != "SUCCEEDED" or not isinstance(runs, list):
        raise DeploymentContractError("Backfill did not return a successful run envelope")
    if any(
        run.get("status") != "SUCCEEDED"
        or run.get("reconciliation", {}).get("reconciled") is not True
        for run in runs
    ):
        raise DeploymentContractError("Backfill reconciliation did not pass")
    receipt = {
        "status": "BACKFILL_SUCCEEDED",
        "mode": mode,
        "release_sha": _required("ODP_RELEASE_SHA"),
        "image_reference": _required("ODP_IMAGE_REFERENCE"),
        "migration_receipt_sha256": migration["receipt_sha256"],
        "source_database": payload.get("source_database"),
        "load_order": payload.get("load_order"),
        "runs": runs,
    }
    _write_termination_receipt(receipt)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("migrate", "scheduled", "orders-history", "trade", "device-log"),
    )
    args = parser.parse_args()
    try:
        if args.command == "migrate":
            return migrate()
        return backfill(args.command)
    except Exception as exc:
        failure = {
            "status": "FAILED",
            "command": args.command,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "occurred_at": datetime.now(UTC).isoformat(),
        }
        _write_termination_receipt(failure)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
