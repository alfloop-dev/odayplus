"""Cross-engine contract tests for AVM quality-score nullability."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from shared.infrastructure.persistence.engine import SqliteEngine

POSTGRES_MIGRATION = Path("infra/db/migrations/000017_avm_quality_score_nullable.sql")
SQLITE_MIGRATION = Path("infra/db/migrations/000017_avm_quality_score_nullable_sqlite.sql")


def test_postgres_and_sqlite_migrations_share_nullable_legacy_contract() -> None:
    postgres_sql = POSTGRES_MIGRATION.read_text(encoding="utf-8")
    sqlite_sql = SQLITE_MIGRATION.read_text(encoding="utf-8")

    assert "ALTER COLUMN quality_score DROP NOT NULL" in postgres_sql
    assert "ALTER COLUMN quality_score DROP DEFAULT" in postgres_sql
    assert "quality_score_status" in postgres_sql
    assert "legacy_unknown" in postgres_sql

    assert "quality_score REAL," in sqlite_sql
    assert "quality_score_status TEXT NOT NULL DEFAULT 'legacy_unknown'" in sqlite_sql
    assert "INSERT INTO data_snapshots_nullable" in sqlite_sql
    assert "SELECT" in sqlite_sql
    assert "DROP TABLE data_snapshots" in sqlite_sql
    assert "quality_score" in sqlite_sql
    assert "legacy_unknown" in sqlite_sql
    # The migration deliberately copies historical 1.00 values instead of
    # rewriting them to NULL, because the old schema lost provenance.
    assert "quality_score,\n    created_by_run_id" in sqlite_sql


def test_sqlite_migration_preserves_legacy_values_and_accepts_null_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-quality.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """CREATE TABLE data_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            snapshot_type TEXT NOT NULL DEFAULT 'raw',
            source_id TEXT NOT NULL,
            snapshot_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            storage_uri TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            quality_score REAL NOT NULL DEFAULT 1.00,
            created_by_run_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    connection.execute(
        """INSERT INTO data_snapshots(
            snapshot_id, source_id, storage_uri, schema_version,
            created_by_run_id, quality_score
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        ("legacy-1", "source-1", "gs://legacy", "v1", "run-1", 1.0),
    )
    connection.commit()
    connection.close()

    engine = SqliteEngine(database_path)
    try:
        columns = {
            row["name"]: row for row in engine.query("PRAGMA table_info(data_snapshots)")
        }
        assert columns["quality_score"]["notnull"] == 0
        assert columns["quality_score"]["dflt_value"] is None

        legacy = engine.query_one(
            "SELECT quality_score, quality_score_status FROM data_snapshots WHERE snapshot_id = ?",
            ("legacy-1",),
        )
        assert legacy is not None
        assert legacy["quality_score"] == 1.0
        assert legacy["quality_score_status"] == "legacy_unknown"

        engine.execute(
            """INSERT INTO data_snapshots(
                snapshot_id, source_id, storage_uri, schema_version,
                created_by_run_id, quality_score, quality_score_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("unmeasured-1", "source-1", "gs://new", "v2", "run-2", None, "unmeasured"),
        )
        unmeasured = engine.query_one(
            "SELECT quality_score, quality_score_status FROM data_snapshots WHERE snapshot_id = ?",
            ("unmeasured-1",),
        )
        assert unmeasured is not None
        assert unmeasured["quality_score"] is None
        assert unmeasured["quality_score_status"] == "unmeasured"
    finally:
        engine.close()

    # SqliteEngine bootstraps idempotently on an already-migrated database and
    # retains both the legacy value and its provenance marker.
    reopened = SqliteEngine(database_path)
    try:
        legacy = reopened.query_one(
            "SELECT quality_score, quality_score_status FROM data_snapshots WHERE snapshot_id = ?",
            ("legacy-1",),
        )
        unmeasured = reopened.query_one(
            "SELECT quality_score, quality_score_status FROM data_snapshots WHERE snapshot_id = ?",
            ("unmeasured-1",),
        )
        assert legacy is not None and legacy["quality_score"] == 1.0
        assert legacy["quality_score_status"] == "legacy_unknown"
        assert unmeasured is not None and unmeasured["quality_score"] is None
        assert unmeasured["quality_score_status"] == "unmeasured"
    finally:
        reopened.close()
