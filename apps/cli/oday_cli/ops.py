from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_MIGRATIONS_DIR = Path("infra/db/migrations/versions")
DEFAULT_DBT_MODEL_DIR = Path("pipelines/dbt/models/model_ready")


class OpsPlanError(ValueError):
    pass


@dataclass(frozen=True)
class MigrationStep:
    revision: str
    path: str
    sha256: str


@dataclass(frozen=True)
class MigrationPlan:
    environment: str
    database_url_env: str
    target_revision: str
    dry_run: bool
    steps: tuple[MigrationStep, ...]
    rollback: dict[str, str]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "steps": [asdict(step) for step in self.steps],
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_migration_steps(migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> tuple[MigrationStep, ...]:
    if not migrations_dir.exists():
        raise OpsPlanError(f"migrations directory not found: {migrations_dir}")

    steps: list[MigrationStep] = []
    for path in sorted(migrations_dir.glob("*.py")):
        if path.name.startswith("__"):
            continue
        revision = path.stem.split("_", 1)[0]
        steps.append(
            MigrationStep(
                revision=revision,
                path=path.as_posix(),
                sha256=_sha256(path),
            )
        )
    if not steps:
        raise OpsPlanError(f"no migration files found in {migrations_dir}")
    return tuple(steps)


def build_migration_plan(
    *,
    environment: str,
    target_revision: str = "head",
    database_url_env: str = "ODAY_DATABASE_URL",
    dry_run: bool = True,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
) -> MigrationPlan:
    steps = discover_migration_steps(migrations_dir)
    return MigrationPlan(
        environment=environment,
        database_url_env=database_url_env,
        target_revision=target_revision,
        dry_run=dry_run,
        steps=steps,
        rollback={
            "command": "alembic downgrade -1",
            "requires": "approved rollback window, fresh backup, and migration owner",
        },
        generated_at=_utc_now(),
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
