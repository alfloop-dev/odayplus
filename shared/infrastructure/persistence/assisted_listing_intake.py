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
import pickle
import threading
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# repo_root/shared/infrastructure/persistence/assisted_listing_intake.py
_REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_DIR = _REPO_ROOT / "infra" / "db" / "migrations" / "assisted_listing_intake"
_CONTRACT_DIR = _REPO_ROOT / "docs" / "data"
CANONICAL_COMPATIBILITY_PATH = MIGRATION_DIR / "000_canonical_compatibility.sql"
_CANONICAL_SHARED_TABLES: tuple[str, ...] = (
    "expansion.listings",
    "expansion.candidate_sites",
    "audit.audit_events",
)

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

_STATE_COLLECTIONS: dict[str, str] = {
    "intakes": "operator.assisted_intakes",
    "corrections": "assisted_intake.corrections",
    "assignments": "assisted_intake.assignments",
    "jobs": "assisted_intake.jobs",
    "decisions": "assisted_intake.decisions",
    "promotions": "operator.promotions",
    "slas": "assisted_intake.slas",
    "saved_views": "assisted_intake.saved_views",
    "replays": "assisted_intake.idempotency_replays",
}

_STATE_KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "intakes": ("intake_id", "id"),
    "corrections": ("correction_id",),
    "assignments": ("assignment_id",),
    "jobs": ("job_id",),
    "decisions": ("decision_id",),
    "promotions": ("promotion_decision_id",),
    "slas": ("sla_instance_id",),
    "saved_views": ("saved_view_id",),
}


class AssistedIntakeSchemaError(RuntimeError):
    """Raised when the approved Assisted Intake schema is unavailable."""


class AssistedIntakePersistenceConflict(RuntimeError):
    """Raised when another API replica commits a state change first."""


@dataclass(frozen=True)
class MigrationStep:
    """One ordered upgrade file with its checksum and contract provenance."""

    order: int
    name: str
    path: str
    sha256: str
    contract_artifact: str
    matches_contract: bool


@dataclass(frozen=True)
class MigrationApplyResult:
    """Receipt for applying and validating the complete 001-004 stack."""

    manifest_sha256: str
    steps: tuple[str, ...]
    required_tables: tuple[str, ...]


class DurableAssistedIntakeStore:
    """PostgreSQL-backed state surface used by the Assisted Intake HTTP API.

    The route contract was originally written against dictionaries. This store
    preserves that interface while making a request operate on a refreshed
    database snapshot and committing every changed collection atomically.
    Per-document PostgreSQL advisory locks plus baseline comparison turn stale
    replica writes into a conflict instead of silently overwriting newer state.
    """

    _IDEMPOTENCY = "operator.idempotency_cache"
    _LISTING_META = "operator.listing_metadata"
    _CANDIDATE_META = "operator.candidate_metadata"

    def __init__(self, store: Any) -> None:
        if getattr(getattr(store, "engine", None), "dialect", None) != "postgresql":
            raise ValueError(
                "DurableAssistedIntakeStore requires PostgresDocumentStore"
            )
        self._store = store
        self._engine = store.engine
        self._local = threading.local()

    def __getattr__(self, name: str) -> Any:
        if name not in _STATE_COLLECTIONS:
            raise AttributeError(name)
        collections = getattr(self._local, "collections", None)
        if collections is None:
            raise AssistedIntakeSchemaError(
                "Assisted Intake store must be bound to a verified tenant"
            )
        return collections[name]

    def _key_for(self, name: str, value: Any) -> str | None:
        if isinstance(value, dict):
            if "_state_key" in value and "_tenant_id" in value and "value" in value:
                return str(value["_state_key"])
            for field in _STATE_KEY_FIELDS.get(name, ()):
                if value.get(field):
                    return str(value[field])
        return None

    @staticmethod
    def _unwrap(value: Any) -> Any:
        if (
            isinstance(value, dict)
            and set(value) == {"_state_key", "_tenant_id", "value"}
        ):
            return value["value"]
        return value

    @staticmethod
    def _wrap(tenant_id: str, key: str, value: Any) -> dict[str, Any]:
        return {
            "_tenant_id": tenant_id,
            "_state_key": key,
            "value": value,
        }

    def _load_collection(self, name: str, tenant_id: str) -> dict[str, Any]:
        loaded: dict[str, Any] = {}
        for persisted in self._store.list_all(_STATE_COLLECTIONS[name]):
            if (
                not isinstance(persisted, dict)
                or persisted.get("_tenant_id") != tenant_id
            ):
                continue
            key = self._key_for(name, persisted)
            if key is None:
                continue
            loaded[key] = deepcopy(self._unwrap(persisted))
        return loaded

    def refresh(self, tenant_id: str) -> None:
        """Bind one request tenant and load only its committed state."""

        normalized_tenant = str(tenant_id or "").strip()
        if not normalized_tenant:
            raise AssistedIntakeSchemaError(
                "Assisted Intake store requires a verified tenant_id"
            )
        loaded = {
            name: self._load_collection(name, normalized_tenant)
            for name in _STATE_COLLECTIONS
        }
        self._local.tenant_id = normalized_tenant
        self._local.collections = {
            name: list(values.values()) if name == "saved_views" else values
            for name, values in loaded.items()
        }
        self._local.baseline = deepcopy(loaded)

    def _current_collections(self) -> dict[str, dict[str, Any]]:
        if not getattr(self._local, "tenant_id", None):
            raise AssistedIntakeSchemaError(
                "Assisted Intake store must be bound before persistence"
            )
        current: dict[str, dict[str, Any]] = {}
        for name in _STATE_COLLECTIONS:
            value = getattr(self, name)
            if name == "saved_views":
                current[name] = {
                    str(item["saved_view_id"]): item
                    for item in value
                }
            else:
                current[name] = dict(value)
        return current

    def _read_locked(
        self,
        collection: str,
        storage_key: str,
    ) -> Any | None:
        self._engine.query_one(
            "SELECT pg_advisory_xact_lock(hashtextextended(?, 0)) AS locked",
            (f"{collection}:{storage_key}",),
        )
        row = self._engine.query_one(
            "SELECT data FROM durable_documents "
            "WHERE collection = ? AND doc_id = ? FOR UPDATE",
            (collection, storage_key),
        )
        if row is None:
            return None
        return self._unwrap(pickle.loads(row["data"]))

    def flush(self) -> None:
        """Atomically persist changed state or reject a stale replica write."""

        current = self._current_collections()
        tenant_id = self._local.tenant_id
        baseline_by_collection = getattr(self._local, "baseline", {})
        changes: list[tuple[str, str, str, Any, Any]] = []
        for name, collection in _STATE_COLLECTIONS.items():
            baseline_values = baseline_by_collection.get(name, {})
            current_values = current[name]
            deleted = set(baseline_values) - set(current_values)
            if deleted:
                raise AssistedIntakePersistenceConflict(
                    "Assisted Intake state deletion requires an explicit "
                    "repository operation"
                )
            for key, value in current_values.items():
                baseline = baseline_values.get(key)
                if key not in baseline_values or value != baseline:
                    changes.append((name, collection, key, baseline, value))

        if not changes:
            return

        with self._engine.lock:
            for _name, collection, key, baseline, value in sorted(changes):
                storage_key = f"{tenant_id}:{key}"
                persisted = self._read_locked(collection, storage_key)
                if persisted != baseline:
                    raise AssistedIntakePersistenceConflict(
                        f"Concurrent Assisted Intake update for {collection}/{key}"
                    )
                self._store.put(
                    collection,
                    storage_key,
                    self._wrap(tenant_id, key, deepcopy(value)),
                )
        self._local.baseline = deepcopy(current)

    # Operator network-listings repository compatibility. Keeping one state
    # owner avoids dual writes between the operator and approved v1 routers.
    def list_intakes(self) -> list[dict[str, Any]]:
        return list(self.intakes.values())

    def save_intake(self, intake: dict[str, Any]) -> None:
        key = str(intake.get("intake_id") or intake["id"])
        self.intakes[key] = intake
        self.flush()

    def list_idempotency_records(self) -> list[Any]:
        return self._store.list_all(self._IDEMPOTENCY)

    def save_idempotency_record(self, record: Any) -> None:
        self._store.put(
            self._IDEMPOTENCY,
            f"{record.action}:{record.key}",
            record,
        )

    def get_listing_metadata(self, listing_id: str) -> dict[str, Any]:
        return self._store.get(self._LISTING_META, listing_id) or {}

    def save_listing_metadata(
        self,
        listing_id: str,
        metadata: dict[str, Any],
    ) -> None:
        self._store.put(self._LISTING_META, listing_id, metadata)

    def get_candidate_metadata(self, candidate_id: str) -> dict[str, Any]:
        return self._store.get(self._CANDIDATE_META, candidate_id) or {}

    def save_candidate_metadata(
        self,
        candidate_id: str,
        metadata: dict[str, Any],
    ) -> None:
        self._store.put(self._CANDIDATE_META, candidate_id, metadata)

    def get_promotion(self, promotion_decision_id: str) -> dict[str, Any] | None:
        return self.promotions.get(promotion_decision_id)

    def save_promotion(self, promotion: dict[str, Any]) -> None:
        key = str(promotion["promotion_decision_id"])
        self.promotions[key] = promotion
        self.flush()

    def list_promotions(self) -> list[dict[str, Any]]:
        return list(self.promotions.values())


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


def validate_required_tables(engine: Any) -> None:
    """Fail closed unless every approved 001-004 relation is installed."""

    missing: list[str] = []
    for relation in ALL_TABLES:
        row = engine.query_one(
            "SELECT to_regclass(?) AS relation",
            (relation,),
        )
        if row is None or row.get("relation") is None:
            missing.append(relation)
    if missing:
        raise AssistedIntakeSchemaError(
            "Assisted Listing Intake schema is incomplete; missing relations: "
            + ", ".join(missing)
        )


def _psycopg_url(database_url: str) -> str:
    value = database_url.strip()
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    if value.startswith("postgresql://") or value.startswith("postgres://"):
        return value
    raise AssistedIntakeSchemaError(
        "ODAY_DATABASE_URL must use postgres:// or postgresql://"
    )


def _without_transaction_control(sql: str) -> str:
    return "\n".join(
        line
        for line in sql.splitlines()
        if line.strip().upper() not in {"BEGIN;", "COMMIT;"}
    )


def apply_upgrade_to_database(database_url: str) -> MigrationApplyResult:
    """Apply and validate the complete reviewed Assisted Intake schema stack."""

    drift = contract_drift()
    if drift:
        raise AssistedIntakeSchemaError(
            "Assisted Intake migration differs from reviewed contract: "
            + ", ".join(drift)
        )
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - runtime dependency contract
        raise AssistedIntakeSchemaError(
            "Assisted Intake migration requires psycopg"
        ) from exc

    expected_manifest = manifest_checksum()
    try:
        with psycopg.connect(
            _psycopg_url(database_url),
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended('oday.assisted-intake-schema', 0))"
                )
                connection.execute("CREATE SCHEMA IF NOT EXISTS odp_runtime")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS
                      odp_runtime.assisted_intake_schema_migrations (
                        migration_name text PRIMARY KEY,
                        manifest_sha256 char(64) NOT NULL,
                        applied_at timestamptz NOT NULL DEFAULT now()
                      )
                    """
                )
                applied = connection.execute(
                    """
                    SELECT manifest_sha256
                    FROM odp_runtime.assisted_intake_schema_migrations
                    WHERE migration_name = '001-004'
                    FOR UPDATE
                    """
                ).fetchone()
                if applied is not None:
                    if applied["manifest_sha256"] != expected_manifest:
                        raise AssistedIntakeSchemaError(
                            "Installed Assisted Intake schema manifest does not "
                            "match the reviewed 001-004 stack"
                        )
                else:
                    shared_tables = {
                        relation
                        for relation in _CANONICAL_SHARED_TABLES
                        if connection.execute(
                            "SELECT to_regclass(%s) AS relation",
                            (relation,),
                        ).fetchone()["relation"]
                        is not None
                    }
                    if shared_tables and shared_tables != set(
                        _CANONICAL_SHARED_TABLES
                    ):
                        raise AssistedIntakeSchemaError(
                            "Canonical shared schema is incomplete before "
                            "Assisted Intake migration: "
                            + ", ".join(sorted(shared_tables))
                        )
                    if shared_tables:
                        connection.execute(
                            _read(CANONICAL_COMPATIBILITY_PATH)
                        )
                    for _name, sql in upgrade_statements():
                        connection.execute(_without_transaction_control(sql))
                    connection.execute(
                        """
                        INSERT INTO
                          odp_runtime.assisted_intake_schema_migrations (
                            migration_name,
                            manifest_sha256
                          )
                        VALUES ('001-004', %s)
                        """,
                        (expected_manifest,),
                    )

            missing: list[str] = []
            for relation in ALL_TABLES:
                row = connection.execute(
                    "SELECT to_regclass(%s) AS relation",
                    (relation,),
                ).fetchone()
                if row is None or row["relation"] is None:
                    missing.append(relation)
            if missing:
                raise AssistedIntakeSchemaError(
                    "Assisted Listing Intake migration completed with missing "
                    "relations: "
                    + ", ".join(missing)
                )
    except AssistedIntakeSchemaError:
        raise
    except Exception as exc:
        raise AssistedIntakeSchemaError(
            "Unable to apply the Assisted Listing Intake 001-004 schema stack"
        ) from exc

    return MigrationApplyResult(
        manifest_sha256=expected_manifest,
        steps=tuple(name for name, _sql in upgrade_statements()),
        required_tables=ALL_TABLES,
    )


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
