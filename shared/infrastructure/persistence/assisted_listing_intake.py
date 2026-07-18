"""Ordered production migration loader for the Assisted Listing Intake schema.

This module is the programmatic surface over the ordered PostgreSQL 16 migration
under ``infra/db/migrations/assisted_listing_intake/``. That directory holds the
four approved contract DDL artifacts (baseline + three consistency/state/RLS
patches) from ODP-SD-INTAKE-001, copied byte-for-byte so the production migration
never drifts from the reviewed contract, plus a structural downgrade boundary.

The module is intentionally driver-agnostic: it only reads SQL text and computes
traceability checksums. Applying the SQL is the caller's responsibility (pass an
``execute`` callable such as ``psycopg`` cursor ``.execute``). Keeping it free of
any database driver lets it import cleanly in the minimal CI environment while the
schema is proven at runtime against real PostgreSQL 16 by the contract and RLS
security tests.

Contract references:
- docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql
- docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0002_CONSISTENCY_PATCH.sql
- docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0003_PROMOTION_STATE_PATCH.sql
- docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0004_TENANT_RLS_LINEAGE_PATCH.sql
- docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_MIGRATION_ROLLOUT_RUNBOOK.md
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

# repo_root/shared/infrastructure/persistence/assisted_listing_intake.py
_REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_DIR = _REPO_ROOT / "infra" / "db" / "migrations" / "assisted_listing_intake"
_CONTRACT_DIR = _REPO_ROOT / "docs" / "data"

# Ordered upgrade steps. Each entry maps the production migration file to the
# reviewed contract artifact it must reproduce byte-for-byte.
_UPGRADE_STEPS: tuple[tuple[str, str], ...] = (
    ("001_baseline.sql", "ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql"),
    ("002_consistency.sql", "ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0002_CONSISTENCY_PATCH.sql"),
    ("003_promotion_state.sql", "ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0003_PROMOTION_STATE_PATCH.sql"),
    ("004_tenant_rls_lineage.sql", "ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0004_TENANT_RLS_LINEAGE_PATCH.sql"),
)
DOWNGRADE_FILE = "downgrade.sql"

# The five schema namespaces the intake bounded context installs into. intake and
# identity are exclusive to this context; expansion, workflow and audit are shared
# with the canonical platform baseline (the downgrade never drops those three).
SCHEMAS: tuple[str, ...] = ("intake", "identity", "expansion", "workflow", "audit")
INTAKE_EXCLUSIVE_SCHEMAS: tuple[str, ...] = ("intake", "identity")

# Every tenant-bearing contract table. Patch 0004 ENABLEs and FORCEs row level
# security plus a fail-closed ``tenant_isolation`` policy on exactly this set.
TENANT_TABLES: tuple[str, ...] = (
    "intake.intakes",
    "intake.intake_stage_transitions",
    "intake.source_snapshots",
    "intake.parser_runs",
    "identity.properties",
    "expansion.listings",
    "expansion.listing_revisions",
    "expansion.listing_observations",
    "identity.source_identity_edges",
    "identity.property_redirects",
    "identity.match_cases",
    "identity.match_candidates",
    "identity.match_decisions",
    "intake.human_corrections",
    "workflow.assignments",
    "workflow.assignment_transitions",
    "workflow.sla_instances",
    "workflow.sla_transitions",
    "workflow.sla_pause_intervals",
    "expansion.promotion_decisions",
    "expansion.candidate_sites",
    "workflow.idempotency_records",
    "workflow.jobs",
    "workflow.outbox_events",
    "workflow.reconciliation_findings",
    "audit.legal_holds",
    "audit.audit_events",
    "audit.export_manifests",
)

# Reference-data tables that are deliberately global (no tenant_id): the source
# policy registry and parser release registry. They are NOT under RLS.
NON_TENANT_TABLES: tuple[str, ...] = (
    "intake.source_registry",
    "intake.parser_releases",
)

ALL_TABLES: tuple[str, ...] = NON_TENANT_TABLES + TENANT_TABLES

# Tables carrying an optimistic-concurrency ``version bigint`` column. Append-only
# history/event tables (transitions, observations, outbox, audit_events, ...) track
# ordering with sequence/revision numbers instead and are intentionally excluded.
VERSIONED_TABLES: tuple[str, ...] = (
    "intake.source_registry",
    "intake.parser_releases",
    "intake.intakes",
    "intake.source_snapshots",
    "intake.parser_runs",
    "intake.human_corrections",
    "identity.properties",
    "identity.property_redirects",
    "identity.match_cases",
    "identity.match_decisions",
    "expansion.listings",
    "expansion.promotion_decisions",
    "expansion.candidate_sites",
    "workflow.assignments",
    "workflow.sla_instances",
    "workflow.sla_pause_intervals",
    "workflow.jobs",
    "workflow.reconciliation_findings",
    "audit.legal_holds",
)

# Tables carrying a data-retention class column.
RETENTION_CLASS_TABLES: tuple[str, ...] = (
    "intake.intakes",
    "intake.source_snapshots",
    "expansion.listings",
)

# Tables carrying a ``legal_hold`` boolean field.
LEGAL_HOLD_TABLES: tuple[str, ...] = (
    "intake.intakes",
    "intake.source_snapshots",
    "expansion.listings",
    "audit.audit_events",
)

# Tenant-qualified unique constraints ``UNIQUE (tenant_id, <pk>)`` that back the
# composite tenant-equal foreign keys. Twelve are added by the 0002 consistency
# patch and six more by the 0004 tenant isolation patch.
TENANT_QUALIFIED_UNIQUE_CONSTRAINTS: tuple[str, ...] = (
    "uq_intakes_tenant_id",
    "uq_source_snapshots_tenant_id",
    "uq_parser_runs_tenant_id",
    "uq_properties_tenant_id",
    "uq_listings_tenant_id",
    "uq_listing_revisions_tenant_id",
    "uq_match_cases_tenant_id",
    "uq_match_decisions_tenant_id",
    "uq_assignments_tenant_id",
    "uq_sla_instances_tenant_id",
    "uq_promotion_decisions_tenant_id",
    "uq_candidate_sites_tenant_id",
    "uq_listing_observations_tenant_id",
    "uq_source_identity_edges_tenant_id",
    "uq_property_redirects_tenant_id",
    "uq_match_candidates_tenant_id",
    "uq_human_corrections_tenant_id",
    "uq_jobs_tenant_id",
)

# Deferrable current-pointer / promotion foreign keys. These must stay DEFERRABLE
# INITIALLY DEFERRED so a row and the row it points at can be written in one
# transaction (listing<->current revision/observation, promotion<->candidate).
DEFERRABLE_FOREIGN_KEYS: tuple[str, ...] = (
    "fk_listing_current_revision",
    "fk_listing_current_revision_tenant",
    "fk_listing_current_observation_tenant",
    "fk_promotion_candidate",
    "fk_promotion_candidate_tenant",
)

# Cross-table tenant-equal lineage foreign keys required by patch 0004. Mirrors the
# assertion set in scripts/validate_assisted_listing_intake_schema.sql.
LINEAGE_FOREIGN_KEYS: tuple[str, ...] = (
    "fk_intake_resolved_listing_tenant",
    "fk_transition_snapshot_tenant",
    "fk_transition_match_case_tenant",
    "fk_transition_job_tenant",
    "fk_property_redirect_pointer_tenant",
    "fk_listing_current_revision_tenant",
    "fk_listing_current_observation_tenant",
    "fk_revision_supersedes_tenant",
    "fk_edge_supersedes_tenant",
    "fk_edge_decision_tenant",
    "fk_redirect_decision_tenant",
    "fk_match_decision_snapshot_tenant",
    "fk_match_decision_parser_tenant",
    "fk_match_decision_supersedes_tenant",
    "fk_match_decision_reversal_tenant",
    "fk_correction_snapshot_tenant",
    "fk_correction_parser_tenant",
    "fk_correction_supersedes_tenant",
    "fk_correction_reversal_tenant",
    "fk_promotion_candidate_tenant",
    "fk_audit_snapshot_tenant",
    "fk_audit_parser_tenant",
)

# Request-scoped setting the RLS policy reads. Application connections must
# ``SET LOCAL app.tenant_id`` inside every request transaction; a missing or empty
# value fails closed (the policy resolves to NULL and matches no rows).
TENANT_SETTING = "app.tenant_id"


@dataclass(frozen=True)
class MigrationStep:
    """One ordered upgrade file with its checksum and contract provenance."""

    order: int
    name: str
    path: str
    sha256: str
    contract_artifact: str
    matches_contract: bool


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def ordered_upgrade_paths() -> tuple[Path, ...]:
    """Return the upgrade SQL files in the order they must be applied."""
    return tuple(MIGRATION_DIR / name for name, _ in _UPGRADE_STEPS)


def downgrade_path() -> Path:
    return MIGRATION_DIR / DOWNGRADE_FILE


def upgrade_statements() -> tuple[tuple[str, str], ...]:
    """Return ``(filename, sql)`` pairs in apply order."""
    return tuple((name, _read(MIGRATION_DIR / name)) for name, _ in _UPGRADE_STEPS)


def combined_upgrade_sql() -> str:
    """Return the full ordered upgrade as a single script.

    Each step is separated by a marker comment so failures are traceable to the
    originating contract artifact.
    """
    chunks: list[str] = []
    for name, sql in upgrade_statements():
        chunks.append(f"-- >>> assisted_listing_intake upgrade step: {name}\n{sql}")
    return "\n\n".join(chunks) + "\n"


def downgrade_sql() -> str:
    return _read(downgrade_path())


def apply_upgrade(execute: Callable[[str], object]) -> None:
    """Apply every ordered upgrade step through ``execute``.

    ``execute`` should run against an autocommit connection (the baseline step has
    no explicit transaction; the patch steps wrap themselves in BEGIN/COMMIT).
    """
    for _name, sql in upgrade_statements():
        execute(sql)


def apply_downgrade(execute: Callable[[str], object]) -> None:
    execute(downgrade_sql())


def migration_steps() -> tuple[MigrationStep, ...]:
    """Return ordered steps with checksums and contract-equality provenance."""
    steps: list[MigrationStep] = []
    for index, (name, artifact) in enumerate(_UPGRADE_STEPS, start=1):
        migration_path = MIGRATION_DIR / name
        migration_text = _read(migration_path)
        contract_text = _read(_CONTRACT_DIR / artifact)
        steps.append(
            MigrationStep(
                order=index,
                name=name,
                path=migration_path.relative_to(_REPO_ROOT).as_posix(),
                sha256=_sha256_text(migration_text),
                contract_artifact=(_CONTRACT_DIR / artifact).relative_to(_REPO_ROOT).as_posix(),
                matches_contract=migration_text == contract_text,
            )
        )
    return tuple(steps)


def contract_drift() -> tuple[str, ...]:
    """Return migration files whose bytes diverge from their contract artifact."""
    return tuple(step.name for step in migration_steps() if not step.matches_contract)


def manifest() -> dict[str, object]:
    """Return a deterministic manifest for the ordered migration."""
    steps = migration_steps()
    return {
        "migration": "assisted_listing_intake",
        "schemas": list(SCHEMAS),
        "tenant_tables": list(TENANT_TABLES),
        "non_tenant_tables": list(NON_TENANT_TABLES),
        "tenant_setting": TENANT_SETTING,
        "steps": [asdict(step) for step in steps],
        "downgrade": {
            "path": downgrade_path().relative_to(_REPO_ROOT).as_posix(),
            "sha256": _sha256_text(downgrade_sql()),
        },
    }


def manifest_checksum(steps: Iterable[MigrationStep] | None = None) -> str:
    payload = [asdict(step) for step in (steps if steps is not None else migration_steps())]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
