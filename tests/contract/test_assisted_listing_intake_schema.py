"""Contract tests for the Assisted Listing Intake production migration.

These exercise the ordered migration under
``infra/db/migrations/assisted_listing_intake/`` against a real PostgreSQL 16
server (see ``tests/conftest.py`` for provisioning). They prove clean install,
catalog constraints, the downgrade boundary, and that the production migration
reproduces the approved ODP-SD-INTAKE-001 four-artifact DDL stack without dropping
any approved constraint.

Run locally with the bundled server::

    uv pip install pgserver "psycopg[binary]"
    uv run pytest tests/contract/test_assisted_listing_intake_schema.py -q

or against an external server via ``INTAKE_TEST_DATABASE_URL``. The database tests
are marked ``requires_live_env`` and are excluded from the default CI marker
expression; they skip cleanly when no PostgreSQL 16 is reachable.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from shared.infrastructure.persistence import assisted_listing_intake as intake

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_SQL = REPO_ROOT / "scripts" / "validate_assisted_listing_intake_schema.sql"

live = pytest.mark.requires_live_env


def _relations(cur) -> set[str]:
    cur.execute(
        """
        SELECT n.nspname || '.' || c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
          AND n.nspname = ANY(%s)
        """,
        (list(intake.SCHEMAS),),
    )
    return {row[0] for row in cur.fetchall()}


def _columns(cur, table: str) -> dict[str, tuple[str, bool]]:
    schema, name = table.split(".", 1)
    cur.execute(
        """
        SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
        """,
        (schema, name),
    )
    return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def _constraint_names(cur, contype: str) -> set[str]:
    cur.execute(
        """
        SELECT co.conname
        FROM pg_constraint co
        JOIN pg_class c ON c.oid = co.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE co.contype = %s AND n.nspname = ANY(%s)
        """,
        (contype, list(intake.SCHEMAS)),
    )
    return {r[0] for r in cur.fetchall()}


# --------------------------------------------------------------------------- #
# Provenance: the production migration must equal the reviewed contract stack.
# This needs no database and always runs.
# --------------------------------------------------------------------------- #

def test_migration_reproduces_reviewed_contract_artifacts_byte_for_byte() -> None:
    steps = intake.migration_steps()
    assert [s.name for s in steps] == [
        "001_baseline.sql",
        "002_consistency.sql",
        "003_promotion_state.sql",
        "004_tenant_rls_lineage.sql",
    ]
    # Every ordered migration file must be a byte-for-byte copy of the artifact
    # it converts, so no approved constraint can be dropped or altered in transit.
    assert intake.contract_drift() == ()
    assert all(step.matches_contract for step in steps)
    assert len(intake.manifest_checksum()) == 64


def test_ordered_migration_files_exist_and_are_nonempty() -> None:
    for path in (*intake.ordered_upgrade_paths(), intake.downgrade_path()):
        assert path.is_file(), path
        assert path.stat().st_size > 0, path


# --------------------------------------------------------------------------- #
# Clean install on PostgreSQL 16.
# --------------------------------------------------------------------------- #

@live
def test_clean_install_creates_every_contract_table(intake_db) -> None:
    with intake_db.connect() as conn:
        relations = _relations(conn.cursor())
    assert relations == set(intake.ALL_TABLES)
    assert len(relations) == 30


@live
def test_all_schema_namespaces_created(intake_db) -> None:
    with intake_db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s)",
            (list(intake.SCHEMAS),),
        )
        present = {r[0] for r in cur.fetchall()}
    assert present == set(intake.SCHEMAS)


@live
def test_every_tenant_table_has_not_null_tenant_id(intake_db) -> None:
    with intake_db.connect() as conn:
        cur = conn.cursor()
        for table in intake.TENANT_TABLES:
            cols = _columns(cur, table)
            assert "tenant_id" in cols, f"{table} missing tenant_id"
            type_name, not_null = cols["tenant_id"]
            assert type_name == "uuid", f"{table}.tenant_id is {type_name}"
            assert not_null, f"{table}.tenant_id must be NOT NULL"


@live
def test_versioned_tables_carry_version_bigint(intake_db) -> None:
    with intake_db.connect() as conn:
        cur = conn.cursor()
        actual = set()
        for table in intake.ALL_TABLES:
            cols = _columns(cur, table)
            if "version" in cols and cols["version"][0] == "bigint":
                actual.add(table)
    assert actual == set(intake.VERSIONED_TABLES)


@live
def test_retention_and_legal_hold_fields_present(intake_db) -> None:
    with intake_db.connect() as conn:
        cur = conn.cursor()
        for table in intake.RETENTION_CLASS_TABLES:
            assert "retention_class" in _columns(cur, table), table
        for table in intake.LEGAL_HOLD_TABLES:
            cols = _columns(cur, table)
            assert "legal_hold" in cols, table
            assert cols["legal_hold"][0] == "boolean", table


@live
def test_authoritative_timestamps_present(intake_db) -> None:
    # Each core aggregate freezes an authoritative time. Spot-check representative
    # tables across the five schemas.
    expected = {
        "intake.intakes": ("submitted_at", "last_transition_at"),
        "intake.source_snapshots": ("captured_at", "observed_at", "stored_at"),
        "expansion.listings": ("created_at", "updated_at"),
        "identity.properties": ("created_at", "updated_at"),
        "workflow.jobs": ("created_at", "updated_at", "timeout_at"),
        "audit.audit_events": ("occurred_at", "retained_until"),
    }
    with intake_db.connect() as conn:
        cur = conn.cursor()
        for table, columns in expected.items():
            cols = _columns(cur, table)
            for column in columns:
                assert column in cols, f"{table}.{column} missing"
                assert cols[column][0].startswith("timestamp"), f"{table}.{column}"


@live
def test_tenant_qualified_unique_constraints_present(intake_db) -> None:
    with intake_db.connect() as conn:
        uniques = _constraint_names(conn.cursor(), "u")
    for name in intake.TENANT_QUALIFIED_UNIQUE_CONSTRAINTS:
        assert name in uniques, f"missing tenant-qualified unique {name}"
    assert len(intake.TENANT_QUALIFIED_UNIQUE_CONSTRAINTS) == 18


@live
def test_lineage_foreign_keys_present(intake_db) -> None:
    with intake_db.connect() as conn:
        fks = _constraint_names(conn.cursor(), "f")
    missing = [name for name in intake.LINEAGE_FOREIGN_KEYS if name not in fks]
    assert not missing, f"missing lineage FKs: {missing}"


@live
def test_current_pointer_foreign_keys_are_deferrable(intake_db) -> None:
    with intake_db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT conname, condeferrable, condeferred
            FROM pg_constraint
            WHERE contype = 'f' AND conname = ANY(%s)
            """,
            (list(intake.DEFERRABLE_FOREIGN_KEYS),),
        )
        rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    for name in intake.DEFERRABLE_FOREIGN_KEYS:
        assert name in rows, f"missing deferrable FK {name}"
        deferrable, deferred = rows[name]
        assert deferrable and deferred, f"{name} must be DEFERRABLE INITIALLY DEFERRED"


@live
def test_enum_check_constraints_carry_approved_values(intake_db) -> None:
    # The CHECK-based enums are load-bearing state machines; verify a representative
    # slice, including the PENDING_REVIEW value that patch 0003 adds to promotion.
    def constraint_def(cur, table: str, needle: str) -> str:
        schema, name = table.split(".", 1)
        cur.execute(
            """
            SELECT pg_get_constraintdef(co.oid)
            FROM pg_constraint co
            JOIN pg_class c ON c.oid = co.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE co.contype = 'c' AND n.nspname = %s AND c.relname = %s
              AND pg_get_constraintdef(co.oid) LIKE %s
            """,
            (schema, name, f"%{needle}%"),
        )
        row = cur.fetchone()
        return row[0] if row else ""

    with intake_db.connect() as conn:
        cur = conn.cursor()
        processing = constraint_def(cur, "intake.intakes", "processing_state")
        for state in (
            "SUBMITTED", "CHECKING_IDENTITY", "CHECKING_SOURCE_POLICY",
            "AWAITING_ASSISTED_ENTRY", "RETRIEVING", "PARSING", "MATCHING",
            "NEEDS_REVIEW", "READY", "QUARANTINED", "FAILED", "CANCELLED",
        ):
            assert state in processing, f"processing_state missing {state}"

        method = constraint_def(cur, "intake.intakes", "intake_method")
        for value in ("URL", "MANUAL", "CSV", "APPROVED_FEED", "OPERATOR_SNAPSHOT"):
            assert value in method

        retrieval = constraint_def(cur, "intake.source_registry", "retrieval_mode")
        for value in ("APPROVED_RETRIEVAL", "ASSISTED_ENTRY_ONLY", "AUTH_REQUIRED",
                      "SOURCE_BLOCKED", "POLICY_UNKNOWN"):
            assert value in retrieval

        promotion = constraint_def(cur, "expansion.promotion_decisions", "PENDING_REVIEW")
        assert "PENDING_REVIEW" in promotion


# --------------------------------------------------------------------------- #
# Row level security catalog state.
# --------------------------------------------------------------------------- #

@live
def test_force_rls_and_fail_closed_policy_on_every_tenant_table(intake_db) -> None:
    with intake_db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT n.nspname || '.' || c.relname AS rel,
                   c.relrowsecurity,
                   c.relforcerowsecurity,
                   p.polname,
                   pg_get_expr(p.polqual, p.polrelid) AS using_expr,
                   pg_get_expr(p.polwithcheck, p.polrelid) AS check_expr
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_policy p ON p.polrelid = c.oid AND p.polname = 'tenant_isolation'
            WHERE c.relkind = 'r' AND n.nspname = ANY(%s)
            """,
            (list(intake.SCHEMAS),),
        )
        state = {r[0]: r[1:] for r in cur.fetchall()}

    for table in intake.TENANT_TABLES:
        row_sec, force_sec, polname, using_expr, check_expr = state[table]
        assert row_sec, f"{table} RLS not enabled"
        assert force_sec, f"{table} RLS not FORCEd"
        assert polname == "tenant_isolation", f"{table} missing tenant_isolation policy"
        assert using_expr and "app.tenant_id" in using_expr, f"{table} USING not tenant scoped"
        assert check_expr and "app.tenant_id" in check_expr, f"{table} WITH CHECK not tenant scoped"

    # Global reference tables must NOT be under RLS (they are tenant-agnostic).
    for table in intake.NON_TENANT_TABLES:
        row_sec, force_sec, polname, *_ = state[table]
        assert not row_sec, f"{table} should not have RLS"
        assert polname is None, f"{table} should have no tenant_isolation policy"


@live
def test_schema_validator_script_passes(intake_db) -> None:
    # scripts/validate_assisted_listing_intake_schema.sql RAISEs on any RLS gap,
    # missing tenant-qualified FK, or missing lineage constraint; a clean run is
    # the pass signal.
    body = "\n".join(
        line for line in VALIDATOR_SQL.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("\\set")
    )
    with intake_db.connect() as conn:
        conn.execute(body)  # raises psycopg error if any DO block fails


# --------------------------------------------------------------------------- #
# Downgrade boundary and repeatable clean install (rollback behaviour).
# --------------------------------------------------------------------------- #

@live
def test_downgrade_boundary_then_reinstall(intake_db) -> None:
    intake_db.apply_downgrade()
    with intake_db.connect() as conn:
        cur = conn.cursor()
        assert _relations(cur) == set(), "downgrade left intake-context tables behind"
        cur.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s)",
            (list(intake.INTAKE_EXCLUSIVE_SCHEMAS),),
        )
        assert cur.fetchall() == [], "intake-exclusive schemas not dropped"

    # A clean install must replay against the downgraded database (rollback then
    # forward), proving the migration is not a one-shot dead end.
    intake_db.apply_migration()
    with intake_db.connect() as conn:
        assert _relations(conn.cursor()) == set(intake.ALL_TABLES)
