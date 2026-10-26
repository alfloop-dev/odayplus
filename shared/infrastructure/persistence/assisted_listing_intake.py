"""Assisted Listing Intake migrations and native snapshot metadata access.

This module is the programmatic surface over the ordered PostgreSQL 16 migration
under ``infra/db/migrations/assisted_listing_intake/``. That directory holds the
versioned contract DDL artifacts, copied byte-for-byte so the production
migration never drifts from its declared contract, plus a structural downgrade
boundary. The original four-artifact receipt is preserved on forward upgrade.

Migration metadata helpers read SQL and compute traceability checksums. The
explicit database upgrade opens its own migration connection. Native snapshot
metadata access borrows the caller's current engine and transaction; it never
opens, commits or closes a connection.

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
from contextlib import contextmanager
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
    (
        "003_promotion_state.sql",
        "ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0003_PROMOTION_STATE_PATCH.sql",
    ),
    (
        "004_tenant_rls_lineage.sql",
        "ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0004_TENANT_RLS_LINEAGE_PATCH.sql",
    ),
    (
        "005_decision_service_actor.sql",
        "ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0005_DECISION_SERVICE_ACTOR_PATCH.sql",
    ),
    (
        "006_workflow_schedule.sql",
        "ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0006_WORKFLOW_SCHEDULE_PATCH.sql",
    ),
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
# assertion set in delivery_toolchain/governance/validate_assisted_listing_intake_schema.sql.
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
    "workflow_audits": "assisted_intake.workflow_audits",
    "workflow_outbox": "assisted_intake.workflow_outbox",
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
    "workflow_audits": ("audit_event_id",),
    "workflow_outbox": ("event_id",),
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
    """Receipt for applying and validating the complete ordered stack."""

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
            raise ValueError("DurableAssistedIntakeStore requires PostgresDocumentStore")
        self._store = store
        self._engine = store.engine
        self._local = threading.local()
        self._canonical_audit_sink = None

    def configure_canonical_audit(self, sink: Any) -> None:
        if sink.writer.engine is not self._engine:
            raise ValueError("Canonical audit must share the business transaction engine")
        self._canonical_audit_sink = sink

    def configure_audit_retention(self, provider: Any) -> None:
        if self._canonical_audit_sink is None:
            raise RuntimeError("Canonical Intake audit writer is not wired")
        self._canonical_audit_sink.set_retention_provider(provider)

    def canonical_audit_is_committed(self, audit: Any) -> bool:
        return bool(self._canonical_audit_sink
                    and audit.get("tenant_id") == self._local.tenant_id
                    and self._canonical_audit_sink.is_committed(audit))

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
        if isinstance(value, dict) and set(value) == {"_state_key", "_tenant_id", "value"}:
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
            if not isinstance(persisted, dict) or persisted.get("_tenant_id") != tenant_id:
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
            raise AssistedIntakeSchemaError("Assisted Intake store requires a verified tenant_id")
        loaded = {
            name: self._load_collection(name, normalized_tenant) for name in _STATE_COLLECTIONS
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
                current[name] = {str(item["saved_view_id"]): item for item in value}
            else:
                current[name] = dict(value)
        return current

    @contextmanager
    def transaction(self):
        """Roll back request state as well as SQL on command/commit failure."""
        self._current_collections()  # Require a verified tenant binding.
        collections = deepcopy(self._local.collections)
        baseline = deepcopy(self._local.baseline)
        try:
            with self._engine.lock:
                yield
        except BaseException:
            self._local.collections = collections
            self._local.baseline = baseline
            raise

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
            "SELECT data FROM durable_documents WHERE collection = ? AND doc_id = ? FOR UPDATE",
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
                    "Assisted Intake state deletion requires an explicit repository operation"
                )
            for key, value in current_values.items():
                baseline = baseline_values.get(key)
                if key not in baseline_values or value != baseline:
                    if name == "workflow_audits" and key in baseline_values:
                        raise AssistedIntakePersistenceConflict("Workflow audit records are append-only")
                    changes.append((name, collection, key, baseline, value))

        if not changes:
            return

        with self._engine.lock:
            canonical_audits = {}
            for _name, collection, key, baseline, value in sorted(changes):
                storage_key = f"{tenant_id}:{key}"
                persisted = self._read_locked(collection, storage_key)
                if persisted != baseline:
                    raise AssistedIntakePersistenceConflict(
                        f"Concurrent Assisted Intake update for {collection}/{key}"
                    )
                if _name == "workflow_audits":
                    from shared.audit.errors import CanonicalAuditUnavailable
                    parent = current["intakes"].get(value.get("intake_id"))
                    if self._canonical_audit_sink is None or parent is None:
                        raise CanonicalAuditUnavailable("Canonical audit writer/parent is unavailable")
                    value = self._canonical_audit_sink.persist(value, parent["scope"])
                    canonical_audits[key] = value
                self._store.put(
                    collection,
                    storage_key,
                    self._wrap(tenant_id, key, deepcopy(value)),
                )
            self._stage_listing_search_changes(tenant_id, changes)
        self.workflow_audits.update(canonical_audits)
        self._local.baseline = deepcopy(self._current_collections())

    def _stage_listing_search_changes(self, tenant_id: str, changes: list) -> None:
        from shared.infrastructure.persistence.job_queue import DurableJobQueue
        from shared.infrastructure.persistence.listing_search_delivery import ListingSearchDelivery
        from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore

        delivery = ListingSearchDelivery(
            TenantScopedDocumentStore(self._store, tenant_id), DurableJobQueue(self._engine),
        )
        for name, _, _, _, value in changes:
            if name == "saved_views":
                delivery.stage(kind="search", value=value)
                from modules.listing.application.saved_search import SavedListingCriteria
                if SavedListingCriteria.from_query(value.get("query")) is not None:
                    # Bootstrap pre-feature canonical receipts from committed
                    # rows. These reads take no parent locks. Later receipt
                    # commits carry their own events, so a concurrent receipt
                    # cannot be lost between this snapshot and search commit.
                    for receipt in self._load_collection("intakes", tenant_id).values():
                        delivery.stage(kind="receipt", value=receipt)
            elif name == "intakes":
                delivery.stage(kind="receipt", value=value)


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
        with self._engine.lock:
            collection = (self._store._collection(self._LISTING_META)
                          if hasattr(self._store, "_collection") else self._LISTING_META)
            # This store is PostgreSQL-only. Use the reader's exact partitioned
            # advisory key before an upsert can insert a previously absent row.
            self._engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?, 0)) AS locked",
                                   (f"{collection}:{listing_id}",))
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
    """Fail closed unless every declared Intake relation is installed."""

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
            "Assisted Listing Intake schema is incomplete; missing relations: " + ", ".join(missing)
        )


def _psycopg_url(database_url: str) -> str:
    value = database_url.strip()
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    if value.startswith("postgresql://") or value.startswith("postgres://"):
        return value
    raise AssistedIntakeSchemaError("ODAY_DATABASE_URL must use postgres:// or postgresql://")


def _without_transaction_control(sql: str) -> str:
    return "\n".join(
        line for line in sql.splitlines() if line.strip().upper() not in {"BEGIN;", "COMMIT;"}
    )


def apply_upgrade_to_database(database_url: str) -> MigrationApplyResult:
    """Apply and validate the complete reviewed Assisted Intake schema stack."""

    drift = contract_drift()
    if drift:
        raise AssistedIntakeSchemaError(
            "Assisted Intake migration differs from reviewed contract: " + ", ".join(drift)
        )
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - runtime dependency contract
        raise AssistedIntakeSchemaError("Assisted Intake migration requires psycopg") from exc

    steps = migration_steps()
    statements = upgrade_statements()
    prefixes = ((4, "001-004"), (5, "001-005"), (6, "001-006"))
    expected_manifests = {name: manifest_checksum(steps[:count]) for count, name in prefixes}
    expected_manifest = expected_manifests["001-006"]
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
                rows = connection.execute(
                    """
                    SELECT migration_name, manifest_sha256
                    FROM odp_runtime.assisted_intake_schema_migrations
                    ORDER BY migration_name
                    FOR UPDATE
                    """
                ).fetchall()
                applied = {row["migration_name"]: row["manifest_sha256"] for row in rows}
                if (any(expected_manifests.get(name) != digest for name, digest in applied.items())
                        or any(marker in applied and any(prior not in applied for _, prior in prefixes[:index])
                               for index, (_, marker) in enumerate(prefixes))):
                    raise AssistedIntakeSchemaError(
                        "Installed Assisted Intake schema manifest does not match the declared ordered stack"
                    )
                if not applied:
                    shared_tables = {
                        relation
                        for relation in _CANONICAL_SHARED_TABLES
                        if connection.execute(
                            "SELECT to_regclass(%s) AS relation",
                            (relation,),
                        ).fetchone()["relation"]
                        is not None
                    }
                    if shared_tables:
                        connection.execute(_read(CANONICAL_COMPATIBILITY_PATH))
                installed_count = 0
                for count, marker in prefixes:
                    if marker in applied:
                        if marker == "001-005":
                            present = connection.execute(
                                "SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid="
                                "to_regclass('identity.match_decisions') AND attname="
                                "'proposer_service_principal' AND NOT attisdropped) AS present"
                            ).fetchone()["present"]
                            if not present:
                                raise AssistedIntakeSchemaError(
                                    "Recorded decision service actor schema is missing; repair is not an upgrade"
                                )
                            # The additive migration's own replay validates
                            # its exact actor constraint/function/trigger.
                            connection.execute(_without_transaction_control(statements[count - 1][1]))
                        elif marker == "001-006":
                            present = connection.execute(
                                "SELECT count(*) AS present FROM pg_attribute WHERE attrelid="
                                "to_regclass('workflow.sla_instances') AND NOT attisdropped AND attname "
                                "IN ('assignment_id','calendar_version','due_soon_at','breach_at')"
                            ).fetchone()["present"]
                            if present != 4:
                                raise AssistedIntakeSchemaError(
                                    "Recorded workflow schedule is missing; repair is not an upgrade"
                                )
                            connection.execute(_without_transaction_control(statements[count - 1][1]))
                        installed_count = count
                        continue
                    for _name, sql in statements[installed_count:count]:
                        connection.execute(_without_transaction_control(sql))
                    connection.execute(
                        """
                        INSERT INTO
                          odp_runtime.assisted_intake_schema_migrations (
                            migration_name,
                            manifest_sha256
                          )
                        VALUES (%s, %s)
                        """,
                        (marker, expected_manifests[marker]),
                    )
                    installed_count = count

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
                    "relations: " + ", ".join(missing)
                )
    except AssistedIntakeSchemaError:
        raise
    except Exception as exc:
        raise AssistedIntakeSchemaError(
            "Unable to apply the Assisted Listing Intake 001-006 schema stack"
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


class NativeIntakeSnapshotMetadataRepository:
    """Existing snapshot metadata within the caller's authorized SQL root.

    This facade neither stores raw content nor owns a transaction. The native
    owner retains its current authority, budget and fence checks around each
    call, including the exact-generation/legacy-NULL reference guard.
    """

    def __init__(self, graph: Any) -> None:
        self.graph = graph
        self.engine = graph.engine

    def referenced_object(self, tenant: str, uri: str, generation: int) -> dict | None:
        return self.engine.query_one("SELECT source_snapshot_id FROM intake.source_snapshots "
            "WHERE tenant_id=? AND raw_object_uri=? AND (object_generation=? OR object_generation IS NULL) LIMIT 1",
            (tenant, uri, generation))

    def find_content(self, tenant: str, intake: str, source: str, content_sha256: str) -> dict | None:
        return self.engine.query_one("SELECT * FROM intake.source_snapshots WHERE tenant_id=? AND intake_id=? "
            "AND source_id=? AND content_sha256=? FOR UPDATE", (tenant, intake, source, content_sha256))

    def insert(self, values: tuple[object, ...]) -> None:
        self.engine.execute("INSERT INTO intake.source_snapshots (source_snapshot_id,tenant_id,intake_id,source_id,"
            "original_url,canonical_url,raw_object_uri,content_sha256,media_type,byte_length,captured_at,observed_at,"
            "capture_method,retention_class,encryption_key_ref,object_generation,residency_mode,version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)

    def required_for_intake(self, tenant: str, snapshot: str, intake: str) -> dict:
        return self.graph._one("SELECT * FROM intake.source_snapshots WHERE tenant_id=? AND source_snapshot_id=? "
            "AND intake_id=?", (tenant, snapshot, intake))

    @staticmethod
    def _selected_parameters(tenant: str, snapshots: list[str], row_limit: int) -> tuple:
        if (type(snapshots) is not list or type(row_limit) is not int or row_limit < 1
                or len(snapshots) >= row_limit):
            raise ValueError("NATIVE_SNAPSHOT_METADATA_SELECTION_BOUND_REQUIRED")
        return tenant, snapshots, row_limit

    def selected_sizes(self, tenant: str, snapshots: list[str], row_limit: int) -> list[dict]:
        """Size only exact referenced metadata before the caller fetches cells.

        The caller owns the current tenant authority, whole-read byte budget
        and original SQL root/clock. A guarded graph keeps those checks around
        this fixed parameterized read. No provider object or raw body is read.
        """
        params = self._selected_parameters(tenant, snapshots, row_limit)
        return self.engine.query("SELECT source_snapshot_id AS id,octet_length(to_jsonb(t)::text) AS bytes "
            "FROM intake.source_snapshots t WHERE tenant_id=? AND source_snapshot_id=ANY(?::uuid[]) "
            "ORDER BY source_snapshot_id LIMIT ?", params)

    def selected_metadata(self, tenant: str, snapshots: list[str], row_limit: int) -> list[dict]:
        """Read selected existing metadata after the caller's size preflight."""
        params = self._selected_parameters(tenant, snapshots, row_limit)
        return self.engine.query("SELECT * FROM intake.source_snapshots WHERE tenant_id=? "
            "AND source_snapshot_id=ANY(?::uuid[]) ORDER BY source_snapshot_id LIMIT ? FOR SHARE", params)
