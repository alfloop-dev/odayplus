from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shared.infrastructure.persistence import (
    MigrationStep,
    build_migration_manifest_checksum,
    discover_migration_steps,
)
from shared.infrastructure.persistence.assisted_listing_intake import (
    AssistedIntakeSchemaError,
    apply_upgrade_to_database,
)
from shared.infrastructure.persistence.assisted_listing_intake import (
    manifest_checksum as assisted_intake_manifest_checksum,
)
from shared.infrastructure.persistence.assisted_listing_intake import (
    migration_steps as assisted_intake_migration_steps,
)

DEFAULT_MIGRATIONS_DIR = Path("infra/db/migrations/versions")
DEFAULT_ALEMBIC_CONFIG = Path("infra/db/migrations/alembic.ini")
DEFAULT_DBT_MODEL_DIR = Path("pipelines/dbt/models/model_ready")


class OpsPlanError(ValueError):
    pass


@dataclass(frozen=True)
class MigrationPlan:
    environment: str
    database_url_env: str
    target_revision: str
    dry_run: bool
    steps: tuple[MigrationStep, ...]
    manifest_sha256: str
    rollback: dict[str, str]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "steps": [asdict(step) for step in self.steps],
        }


@dataclass(frozen=True)
class MigrationRun:
    environment: str
    database_url_env: str
    target_revision: str
    dry_run: bool
    manifest_sha256: str
    checksum_status: str
    command: tuple[str, ...]
    returncode: int | None
    generated_at: str
    plan: MigrationPlan
    assisted_intake_manifest_sha256: str | None = None
    assisted_intake_steps: tuple[str, ...] = ()
    assisted_intake_schema_status: str = "not-requested"

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "database_url_env": self.database_url_env,
            "target_revision": self.target_revision,
            "dry_run": self.dry_run,
            "manifest_sha256": self.manifest_sha256,
            "checksum_status": self.checksum_status,
            "command": list(self.command),
            "returncode": self.returncode,
            "generated_at": self.generated_at,
            "plan": self.plan.to_dict(),
            "assisted_intake_manifest_sha256": self.assisted_intake_manifest_sha256,
            "assisted_intake_steps": list(self.assisted_intake_steps),
            "assisted_intake_schema_status": self.assisted_intake_schema_status,
        }


@dataclass(frozen=True)
class BackfillPlan:
    environment: str
    job_type: str
    source_snapshot_id: str
    target_view: str
    window_start: str
    window_end: str
    idempotency_key: str
    checks: tuple[str, ...]
    quarantine_table: str
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_migration_plan(
    *,
    environment: str,
    target_revision: str = "head",
    database_url_env: str = "ODAY_DATABASE_URL",
    dry_run: bool = True,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
) -> MigrationPlan:
    try:
        steps = discover_migration_steps(migrations_dir)
    except FileNotFoundError as exc:
        raise OpsPlanError(str(exc)) from exc
    if not steps:
        raise OpsPlanError(f"no migration files found in {migrations_dir}")
    return MigrationPlan(
        environment=environment,
        database_url_env=database_url_env,
        target_revision=target_revision,
        dry_run=dry_run,
        steps=steps,
        manifest_sha256=build_migration_manifest_checksum(steps),
        rollback={
            "command": "alembic downgrade -1",
            "requires": "approved rollback window, fresh backup, and migration owner",
        },
        generated_at=_utc_now(),
    )


def build_migration_run(
    *,
    environment: str,
    target_revision: str = "head",
    database_url_env: str = "ODAY_DATABASE_URL",
    dry_run: bool = True,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
    alembic_config: Path = DEFAULT_ALEMBIC_CONFIG,
    expected_manifest_sha256: str | None = None,
    include_assisted_intake: bool = False,
) -> MigrationRun:
    plan = build_migration_plan(
        environment=environment,
        target_revision=target_revision,
        database_url_env=database_url_env,
        dry_run=dry_run,
        migrations_dir=migrations_dir,
    )
    if expected_manifest_sha256 and expected_manifest_sha256 != plan.manifest_sha256:
        raise OpsPlanError(
            "migration checksum mismatch: "
            f"expected {expected_manifest_sha256}, found {plan.manifest_sha256}"
        )

    command = ("alembic", "-c", alembic_config.as_posix(), "upgrade", target_revision)
    returncode: int | None = None
    intake_manifest_sha256: str | None = None
    intake_steps: tuple[str, ...] = ()
    intake_schema_status = "not-requested"
    if include_assisted_intake:
        intake_manifest_sha256 = assisted_intake_manifest_checksum()
        intake_steps = tuple(step.name for step in assisted_intake_migration_steps())
        intake_schema_status = "planned" if dry_run else "pending"
    if not dry_run:
        if not os.environ.get(database_url_env):
            raise OpsPlanError(f"{database_url_env} must be set before executing migrations")
        result = subprocess.run(command, check=False)
        returncode = result.returncode
        if result.returncode != 0:
            raise OpsPlanError(f"migration runner failed with exit code {result.returncode}")
        if include_assisted_intake:
            try:
                intake_result = apply_upgrade_to_database(
                    os.environ[database_url_env]
                )
            except AssistedIntakeSchemaError as exc:
                raise OpsPlanError(str(exc)) from exc
            intake_manifest_sha256 = intake_result.manifest_sha256
            intake_steps = intake_result.steps
            intake_schema_status = "verified"

    return MigrationRun(
        environment=environment,
        database_url_env=database_url_env,
        target_revision=target_revision,
        dry_run=dry_run,
        manifest_sha256=plan.manifest_sha256,
        checksum_status="verified",
        command=command,
        returncode=returncode,
        generated_at=_utc_now(),
        plan=plan,
        assisted_intake_manifest_sha256=intake_manifest_sha256,
        assisted_intake_steps=intake_steps,
        assisted_intake_schema_status=intake_schema_status,
    )


def build_backfill_plan(
    *,
    environment: str,
    job_type: str,
    source_snapshot_id: str,
    target_view: str,
    window_start: str,
    window_end: str,
) -> BackfillPlan:
    if window_start >= window_end:
        raise OpsPlanError("window_start must be before window_end")
    if not source_snapshot_id:
        raise OpsPlanError("source_snapshot_id is required")
    if not target_view:
        raise OpsPlanError("target_view is required")

    idempotency_source = "|".join(
        [environment, job_type, source_snapshot_id, target_view, window_start, window_end]
    )
    idempotency_key = "backfill:" + hashlib.sha256(idempotency_source.encode()).hexdigest()[:24]
    return BackfillPlan(
        environment=environment,
        job_type=job_type,
        source_snapshot_id=source_snapshot_id,
        target_view=target_view,
        window_start=window_start,
        window_end=window_end,
        idempotency_key=idempotency_key,
        checks=(
            "source_snapshot_exists",
            "point_in_time_boundaries",
            "data_quality_threshold",
            "target_view_row_count",
            "quarantine_empty_or_explained",
        ),
        quarantine_table=f"operations.quarantine_{target_view}",
        generated_at=_utc_now(),
    )


def write_json(payload: dict[str, Any], output: Path | None) -> None:
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(content, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")


def _add_common_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, help="Write the JSON plan to this path.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oday", description="ODay Plus operational CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    migration = subparsers.add_parser("migration-plan", help="Render an auditable DB migration plan.")
    migration.add_argument("--environment", required=True, choices=("local", "dev", "staging", "prod"))
    migration.add_argument("--target-revision", default="head")
    migration.add_argument("--database-url-env", default="ODAY_DATABASE_URL")
    migration.add_argument("--apply", action="store_true", help="Mark the plan as an apply run.")
    migration.add_argument("--migrations-dir", type=Path, default=DEFAULT_MIGRATIONS_DIR)
    _add_common_output(migration)

    runner = subparsers.add_parser(
        "migration-runner",
        help="Validate migration checksums and render or execute the Alembic upgrade command.",
    )
    runner.add_argument("--environment", required=True, choices=("local", "dev", "staging", "prod"))
    runner.add_argument("--target-revision", default="head")
    runner.add_argument("--database-url-env", default="ODAY_DATABASE_URL")
    runner.add_argument("--migrations-dir", type=Path, default=DEFAULT_MIGRATIONS_DIR)
    runner.add_argument("--alembic-config", type=Path, default=DEFAULT_ALEMBIC_CONFIG)
    runner.add_argument("--expected-manifest-sha256")
    runner.add_argument("--execute", action="store_true", help="Run Alembic after checksum validation.")
    _add_common_output(runner)

    backfill = subparsers.add_parser("backfill-plan", help="Render an idempotent backfill plan.")
    backfill.add_argument("--environment", required=True, choices=("local", "dev", "staging", "prod"))
    backfill.add_argument("--job-type", required=True)
    backfill.add_argument("--source-snapshot-id", required=True)
    backfill.add_argument("--target-view", required=True)
    backfill.add_argument("--window-start", required=True)
    backfill.add_argument("--window-end", required=True)
    _add_common_output(backfill)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "migration-plan":
            plan = build_migration_plan(
                environment=args.environment,
                target_revision=args.target_revision,
                database_url_env=args.database_url_env,
                dry_run=not args.apply,
                migrations_dir=args.migrations_dir,
            )
            write_json(plan.to_dict(), args.output)
            return 0
        if args.command == "migration-runner":
            run = build_migration_run(
                environment=args.environment,
                target_revision=args.target_revision,
                database_url_env=args.database_url_env,
                dry_run=not args.execute,
                migrations_dir=args.migrations_dir,
                alembic_config=args.alembic_config,
                expected_manifest_sha256=args.expected_manifest_sha256,
            )
            write_json(run.to_dict(), args.output)
            return 0
        if args.command == "backfill-plan":
            plan = build_backfill_plan(
                environment=args.environment,
                job_type=args.job_type,
                source_snapshot_id=args.source_snapshot_id,
                target_view=args.target_view,
                window_start=args.window_start,
                window_end=args.window_end,
            )
            write_json(plan.to_dict(), args.output)
            return 0
    except OpsPlanError as exc:
        parser.error(str(exc))
    return 2
