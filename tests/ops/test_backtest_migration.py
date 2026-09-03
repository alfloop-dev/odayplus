from pathlib import Path

MIGRATION = Path("infra/db/migrations/000018_learninghub_backtest_receipts.sql")


def test_backtest_receipt_migration_requires_replayable_lineage() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    for fragment in (
        "CREATE TABLE IF NOT EXISTS learning.backtest_receipts",
        "receipt_id                  VARCHAR(100) PRIMARY KEY",
        "model_name                  VARCHAR(255) NOT NULL",
        "model_version               VARCHAR(100) NOT NULL",
        "dataset_snapshot_id         VARCHAR(255) NOT NULL",
        "code_version                VARCHAR(100) NOT NULL",
        "decision_policy_version_id  VARCHAR(100) NOT NULL",
        "status                      VARCHAR(50) NOT NULL",
        "CONSTRAINT chk_backtest_receipt_model_version",
        "CONSTRAINT chk_backtest_receipt_snapshot",
        "CONSTRAINT chk_backtest_receipt_code_version",
        "CONSTRAINT chk_backtest_receipt_policy",
        "CONSTRAINT chk_backtest_receipt_status",
    ):
        assert fragment in sql


def test_backtest_receipt_is_indexed_by_model_version_and_snapshot() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "idx_backtest_receipts_model_version" in sql
    assert "model_name, model_version, created_at" in sql
    assert "idx_backtest_receipts_snapshot" in sql
    assert "dataset_snapshot_id" in sql
    assert "idx_backtest_receipts_policy" in sql
    assert "decision_policy_version_id" in sql
